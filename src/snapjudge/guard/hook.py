"""Claude Code PreToolUse hook: JSON in on stdin, a decision out on stdout.

Hold maps to permissionDecision "deny" (the agent reads the reason and
re-plans); ask maps to "ask". Allow prints nothing, so Claude Code's own
permission rules still apply -- the guard only ever tightens them.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from snapjudge.guard.core import Action, Verdict, check, load_user_rules, project_rules
from snapjudge.guard.rules import redact

TRANSCRIPT_SCAN_BYTES = 2 * 1024 * 1024
GUARDED_TOOLS = ("Bash", "Write", "Edit", "MultiEdit")


def home() -> Path:
    return Path(os.environ.get("SNAPJUDGE_HOME", Path.home() / ".snapjudge"))


def action_from_hook(payload: dict[str, Any]) -> Action | None:
    tool = payload.get("tool_name")
    if tool not in GUARDED_TOOLS:
        return None
    inp = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or os.getcwd())
    action = Action(tool=tool, cwd=cwd)
    if tool == "Bash":
        action.command = inp.get("command", "")
    else:
        action.path = inp.get("file_path")
        if tool == "Write":
            action.content = inp.get("content", "")
        elif tool == "Edit":
            action.content = inp.get("new_string", "")
        else:
            action.content = "\n".join(
                e.get("new_string", "") for e in inp.get("edits", [])
            )
    action.task = last_user_message(payload.get("transcript_path"))
    action.project_rules = project_rules(cwd)
    return action


def _lines_backward(path: str, limit: int) -> list[bytes]:
    """The file's complete lines within its last `limit` bytes, newest first.
    A line cut by the window's start is dropped; an unterminated last line is
    kept only if it is already a complete JSON record."""
    with open(path, "rb") as fh:
        end = fh.seek(0, os.SEEK_END)
        start = max(0, end - limit)
        fh.seek(start)
        window = fh.read()
    lines = window.split(b"\n")
    if start > 0:
        lines.pop(0)
    last = lines.pop() if lines else b""
    if last and _is_json(last):
        lines.append(last)
    return lines[::-1]


def _is_json(raw: bytes) -> bool:
    try:
        json.loads(raw)
    except ValueError:
        return False
    return True


def _user_text(raw: bytes) -> str | None:
    """The text of a plain user turn, or None for anything else."""
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str) and content.strip():
        return content[-2_000:]
    if isinstance(content, list):
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if any(t.strip() for t in texts):
            return "\n".join(texts)[-2_000:]
    return None


def last_user_message(transcript_path: str | None) -> str | None:
    """The newest plain-text user turn in a Claude Code JSONL transcript.

    Reads back from the end, at most TRANSCRIPT_SCAN_BYTES: a fixed cost per
    call however long the session, and no cache that could go stale. If the
    user's last turn is further back than that, the task is reported unknown.
    """
    if not transcript_path or not Path(transcript_path).is_file():
        return None
    lines = _lines_backward(transcript_path, TRANSCRIPT_SCAN_BYTES)
    return next(filter(None, map(_user_text, lines)), None)


def engine_spec() -> str | None:
    """$SNAPJUDGE_ENGINE, else `engine` in ~/.snapjudge/guard.toml.

    The file matters because agents often run hooks without the user's
    shell environment, so an exported variable may never reach the hook.
    """
    return os.environ.get("SNAPJUDGE_ENGINE") or saved_engine()


def saved_engine() -> str | None:
    """`engine` from ~/.snapjudge/guard.toml only, ignoring the environment."""
    path = home() / "guard.toml"
    if not path.is_file():
        return None
    with path.open("rb") as fh:
        value = tomllib.load(fh).get("engine")
    return value if isinstance(value, str) and value else None


def _engine():
    spec = engine_spec()
    if not spec:
        return None
    from snapjudge.engines import load

    engine = load(spec)
    if spec.partition(":")[0] == "jev" and not os.environ.get("TYPESAFE_API_KEY"):
        # Caught by the caller: the guard then runs rules only, failing closed.
        raise RuntimeError(
            "engine is jev but TYPESAFE_API_KEY isn't visible to the hook"
        )
    return engine


def log(action: Action, verdict: Verdict, session: str | None) -> None:
    """Append one line to the guard log. Never logs file contents."""
    path = home() / "guard.jsonl"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "session": session,
        "tool": action.tool,
        "subject": redact(action.command or action.path or "")[:500],
        "outcome": verdict.outcome,
        "layer": verdict.layer,
        "rule": verdict.rule,
        "reason": verdict.reason,
        "p": verdict.p,
        "engine": verdict.engine,
        "latency_ms": round(verdict.latency_ms, 1),
        "cost_usd": verdict.cost_usd,
        "error": verdict.error,
    }
    # Owner-only: commands are redacted, but the log is still private.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(record) + "\n")


AGENTS = ("claude-code", "cursor", "codex")


CODEX_SHELL_TOOLS = ("exec_command", "shell", "Bash")


def normalise(payload: dict[str, Any], agent: str) -> dict[str, Any]:
    """Map each agent's hook input onto Claude Code's PreToolUse shape."""
    if agent == "cursor":  # beforeShellExecution: {command, cwd, ...}
        return {
            "tool_name": "Bash",
            "tool_input": {"command": payload.get("command", "")},
            "cwd": payload.get("cwd"),
            "session_id": payload.get("conversation_id"),
            "transcript_path": payload.get("transcript_path"),
        }
    if agent == "codex" and payload.get("tool_name") in CODEX_SHELL_TOOLS:
        # Codex's shell tool is exec_command with tool_input.cmd (str or argv).
        inp = payload.get("tool_input") or {}
        cmd = inp.get("cmd", inp.get("command", ""))
        if isinstance(cmd, list):
            cmd = shlex.join(str(c) for c in cmd)
        return {**payload, "tool_name": "Bash", "tool_input": {"command": cmd}}
    return payload  # Claude Code's PreToolUse shape


def _reason(verdict: Verdict) -> str:
    reason = f"snapjudge guard: {verdict.reason}"
    if verdict.outcome == "hold":
        reason += ". If it is really needed, explain why and ask the user to run it."
    return reason


def respond(
    verdict: Verdict, agent: str = "claude-code"
) -> tuple[dict[str, Any] | None, int, str]:
    """(stdout JSON or None, exit code, stderr text) for this agent."""
    note = (
        f"snapjudge guard: {verdict.reason} ({verdict.error})" if verdict.error else ""
    )
    if agent == "cursor":
        # Cursor treats missing or invalid JSON on a permission hook as a
        # decision, so always answer; allow defers to Cursor's own settings.
        permission = {"hold": "deny", "ask": "ask"}.get(verdict.outcome, "allow")
        out: dict[str, Any] = {"permission": permission}
        if permission != "allow":
            out["user_message"] = out["agent_message"] = _reason(verdict)
        elif note:
            out["user_message"] = note
        return out, 0, ""
    if agent == "codex":
        # Codex has no "ask" from PreToolUse: both hold and ask deny, and the
        # reason tells the agent to get the user's go-ahead. Exit 2 + stderr
        # is the documented block.
        if verdict.outcome in ("hold", "ask"):
            reason = _reason(verdict)
            if verdict.outcome == "ask":
                reason += " Ask the user before running it."
            return None, 2, reason
        return {}, 0, note
    out = {}
    if verdict.outcome in ("hold", "ask"):
        out["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny" if verdict.outcome == "hold" else "ask",
            "permissionDecisionReason": _reason(verdict),
        }
    if note:
        out["systemMessage"] = note
    return out or None, 0, ""


def run(
    stdin=sys.stdin,
    stdout=sys.stdout,
    stderr=sys.stderr,
    *,
    record: bool = True,
    agent: str = "claude-code",
) -> int:
    """Hook entry point; returns the exit code. Failures never block: they
    allow the action and say so, since a crashing hook is ignored anyway."""
    start = time.perf_counter()
    code, err_text = 0, ""
    try:
        payload = normalise(json.load(stdin), agent)
        action = action_from_hook(payload)
        if action is None:
            out = {"permission": "allow"} if agent == "cursor" else None
        else:
            verdict = _decide(action, start)
            if record:
                try:
                    log(action, verdict, payload.get("session_id"))
                except OSError:
                    pass
            out, code, err_text = respond(verdict, agent)
    except Exception as err:  # noqa: BLE001 -- never break the agent, always say so
        message = f"snapjudge guard failed and allowed the action: {err}"
        out = (
            {"permission": "allow", "user_message": message}
            if agent == "cursor"
            else {"systemMessage": message}
        )
    if out is not None:
        json.dump(out, stdout)
    if err_text:
        print(err_text, file=stderr)
    return code


def _decide(action: Action, start: float) -> Verdict:
    try:
        engine = _engine()
    except Exception as err:  # noqa: BLE001 -- bad engine config degrades to rules only
        engine, engine_error = None, f"{type(err).__name__}: {err}"
    else:
        engine_error = None
    rules, config_error = [], None
    for path in (home() / "guard.toml", action.cwd / "guard.toml"):
        try:
            rules += load_user_rules(path)
        except Exception as err:  # noqa: BLE001 -- a bad config must not disable built-ins
            config_error = f"ignored {path}: {err}"
    verdict = check(action, engine, rules)
    problem = engine_error or config_error
    if problem and not verdict.error:
        verdict.error = problem
        verdict.reason = verdict.reason or "engine unavailable; rules only"
    verdict.latency_ms = verdict.latency_ms or (time.perf_counter() - start) * 1000
    return verdict
