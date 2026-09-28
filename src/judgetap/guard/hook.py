"""Claude Code PreToolUse hook: JSON in on stdin, a decision out on stdout.

Hold maps to permissionDecision "deny" (the agent reads the reason and
re-plans); ask maps to "ask". Allow prints nothing, so Claude Code's own
permission rules still apply -- the guard only ever tightens them.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from judgetap._compat import default_home, env
from judgetap.guard.core import (
    Action,
    Verdict,
    check,
    load_rules_counting_allows,
    project_rules,
)
from judgetap.guard.rules import redact

TRANSCRIPT_SCAN_BYTES = 2 * 1024 * 1024
GUARDED_TOOLS = ("Bash", "Write", "Edit", "MultiEdit")


def home() -> Path:
    configured = env("HOME")
    return Path(configured) if configured else default_home()


def _text(value: Any) -> str:
    """Hook fields as text, whatever type arrived (None -> "")."""
    return "" if value is None else value if isinstance(value, str) else str(value)


def action_from_hook(payload: dict[str, Any]) -> Action | None:
    """The action to check. Parsing never raises on odd field types; if the
    extras (task, project rules) can't be read, the rules still run.

    None means a named tool judgetap doesn't guard (Read, Grep, ...): a true
    non-action, allowed. A missing or empty tool name is not that -- the
    hook can't tell what is about to run -- so it raises, and the caller
    fails closed (asks)."""
    raw = payload.get("tool_name")
    # Match on the stripped name: "Bash " is still Bash, not an unguarded tool.
    tool = raw.strip() if isinstance(raw, str) else ""
    if not tool:
        raise ValueError(f"hook input has no tool name: {raw!r}")
    if tool not in GUARDED_TOOLS:
        return None
    inp = payload.get("tool_input")
    inp = inp if isinstance(inp, dict) else {}
    cwd = Path(_text(payload.get("cwd")) or os.getcwd())
    action = Action(tool=tool, cwd=cwd)
    if tool == "Bash":
        command = inp.get("command")
        # Only a nonempty string can be inspected; anything else asks.
        readable = isinstance(command, str) and bool(command.strip())
        action.command = command if readable else None
        action.unreadable = not readable
    else:
        path = inp.get("file_path")
        action.path = path if isinstance(path, str) and path.strip() else None
        # A write whose destination can't be read can't be checked: ask.
        action.unreadable = action.path is None
        if tool == "Write":
            action.content = _text(inp.get("content"))
        elif tool == "Edit":
            action.content = _text(inp.get("new_string"))
        else:
            edits = inp.get("edits")
            edits = edits if isinstance(edits, list) else []
            action.content = "\n".join(
                _text(e.get("new_string")) if isinstance(e, dict) else _text(e)
                for e in edits
            )
    try:
        action.task = last_user_message(_text(payload.get("transcript_path")) or None)
    except Exception:  # noqa: BLE001 -- enrichment only: the rules still run
        action.task = None
    try:
        action.project_rules = project_rules(cwd)
    except Exception:  # noqa: BLE001 -- enrichment only
        action.project_rules = None
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
    """$JUDGETAP_ENGINE, else `engine` in ~/.judgetap/guard.toml.

    The file matters because agents often run hooks without the user's
    shell environment, so an exported variable may never reach the hook.
    """
    return env("ENGINE") or saved_engine()


def saved_engine() -> str | None:
    """`engine` from ~/.judgetap/guard.toml only, ignoring the environment."""
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
    from judgetap.engines import load
    from judgetap.guard.install import in_process_error

    if error := in_process_error(spec):
        # Caught by the caller: rules only, failing closed.
        raise RuntimeError(error)
    engine = load(spec)
    # The engine looked the key up once when built; reuse that, don't hit the
    # keychain a second time on every guarded action.
    if getattr(engine, "needs_key", False):
        # Caught by the caller: the guard then runs rules only, failing closed.
        raise RuntimeError(
            "engine is jev but TYPESAFE_API_KEY isn't in the hook's env or the keychain"
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
        "calls": [c.as_dict() for c in verdict.calls],
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
            # Missing stays None, so the guard asks instead of checking "".
            "tool_input": {"command": payload.get("command")},
            "cwd": payload.get("cwd"),
            "session_id": payload.get("conversation_id"),
            "transcript_path": payload.get("transcript_path"),
        }
    tool = payload.get("tool_name")
    if agent == "codex" and isinstance(tool, str) and tool.strip() in CODEX_SHELL_TOOLS:
        # Codex's shell tool is exec_command with tool_input.cmd (str or argv).
        inp = payload.get("tool_input")
        inp = inp if isinstance(inp, dict) else {}
        cmd = inp.get("cmd", inp.get("command"))
        if isinstance(cmd, list) and cmd:
            cmd = shlex.join(str(c) for c in cmd)
        elif not isinstance(cmd, str):
            cmd = None  # missing or not a command: unreadable, so the guard asks
        return {**payload, "tool_name": "Bash", "tool_input": {"command": cmd}}
    return payload  # Claude Code's PreToolUse shape


def _reason(verdict: Verdict) -> str:
    reason = f"judgetap guard: {verdict.reason}"
    if verdict.outcome == "hold":
        reason += ". If it is really needed, explain why and ask the user to run it."
    return reason


def respond(
    verdict: Verdict, agent: str = "claude-code"
) -> tuple[dict[str, Any] | None, int, str]:
    """(stdout JSON or None, exit code, stderr text) for this agent."""
    note = (
        f"judgetap guard: {verdict.reason} ({verdict.error})" if verdict.error else ""
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
            if note:
                reason += f" ({verdict.error})"  # warnings aren't lost on a deny
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
    """Hook entry point; returns the exit code. Input the guard can't read
    asks the user (it never saw the action, so it can't vouch for it), and so
    does any later unexpected failure: the check didn't finish, so the guard
    can't vouch for the action either."""
    start = time.perf_counter()
    code, err_text = 0, ""
    action = payload = None
    try:
        try:
            payload = json.load(stdin)
            if not isinstance(payload, dict):
                raise TypeError(f"expected a JSON object, got {type(payload).__name__}")
            payload = normalise(payload, agent)
            action = action_from_hook(payload)
        except Exception as err:  # noqa: BLE001 -- unreadable input fails closed
            verdict = Verdict(
                "ask",
                "none",
                "couldn't read the hook input",
                error=f"{type(err).__name__}: {err}",
            )
            out, code, err_text = respond(verdict, agent)
            action = payload = None
        if payload is None:
            pass
        elif action is None:
            out = {"permission": "allow"} if agent == "cursor" else None
        else:
            verdict = _decide(action, start, payload.get("session_id"))
            if record:
                try:
                    log(action, verdict, payload.get("session_id"))
                except OSError:
                    pass
            out, code, err_text = respond(verdict, agent)
    except Exception as err:  # noqa: BLE001 -- never break the agent, fail closed
        verdict = Verdict(
            "ask",
            "none",
            "judgetap guard failed before finishing its check",
            error=f"{type(err).__name__}: {err}",
        )
        if record and action is not None:
            try:  # best effort: the ask must reach the agent whatever happens
                log(action, verdict, (payload or {}).get("session_id"))
            except Exception:  # noqa: BLE001, S110
                pass
        out, code, err_text = respond(verdict, agent)
    if out is not None:
        json.dump(out, stdout)
    if err_text:
        print(err_text, file=stderr)
    return code


def _warn_once(session: str | None, key: str) -> bool:
    """True the first time `key` is warned about in this session."""
    if not isinstance(session, str) or not re.fullmatch(
        r"[A-Za-z0-9._-]{1,128}", session
    ):
        return True  # no usable session id: warn rather than stay silent
    marker = home() / "sessions" / f"{session}.warned"
    try:
        seen = set(marker.read_text().splitlines()) if marker.exists() else set()
        if key in seen:
            return False
        marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(marker, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(key + "\n")
    except OSError:
        return True
    return True


def _decide(action: Action, start: float, session: str | None = None) -> Verdict:
    if getattr(action, "unreadable", False):
        # A command or destination that can't be read: fail closed.
        what = "command" if action.tool == "Bash" else "destination file"
        return Verdict(
            "ask",
            "rules",
            f"the {what} couldn't be read from the hook input",
            rule="unreadable",
        )
    try:
        engine = _engine()
    except Exception as err:  # noqa: BLE001 -- bad engine config degrades to rules only
        engine, engine_error = None, f"{type(err).__name__}: {err}"
    else:
        engine_error = None
    rules, config_error = [], None
    for path, trusted in (
        (home() / "guard.toml", True),
        (action.cwd / "guard.toml", False),
    ):
        try:
            loaded, n = load_rules_counting_allows(path, trusted=trusted)
            rules += loaded
            if n and _warn_once(session, f"repo-allow:{path}"):
                config_error = f"ignored {n} allow rule(s) in {path}: a repo can only tighten the guard"
        except Exception as err:  # noqa: BLE001 -- a bad config must not disable built-ins
            config_error = f"ignored {path}: {err}"
    verdict = check(action, engine, rules)
    # Keep every problem: an engine error must not hide a config warning that
    # _warn_once has already marked as shown for this session.
    problems = [p for p in (verdict.error, engine_error, config_error) if p]
    if problems:
        verdict.error = "; ".join(problems)
        verdict.reason = verdict.reason or "engine unavailable; rules only"
    verdict.latency_ms = verdict.latency_ms or (time.perf_counter() - start) * 1000
    return verdict
