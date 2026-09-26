"""Claude Code PreToolUse hook: JSON in on stdin, a decision out on stdout.

Hold maps to permissionDecision "deny" (the agent reads the reason and
re-plans); ask maps to "ask". Allow prints nothing, so Claude Code's own
permission rules still apply -- the guard only ever tightens them.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from snapjudge.guard.core import Action, Verdict, check, load_user_rules, project_rules
from snapjudge.guard.rules import redact

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


def _lines_backward(path: str, chunk: int = 65_536):
    """Yield a file's lines newest first, reading from the end in chunks."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos, tail = fh.tell(), b""
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            block = fh.read(step) + tail
            lines = block.split(b"\n")
            tail = lines.pop(0)  # may be cut mid-line; finish it next round
            yield from reversed(lines)
        yield tail


def last_user_message(transcript_path: str | None) -> str | None:
    """The newest plain-text user turn in a Claude Code JSONL transcript.

    Reads from the end, so the cost is the distance to the last user turn,
    not the length of the session.
    """
    if not transcript_path or not Path(transcript_path).is_file():
        return None
    for raw in _lines_backward(transcript_path):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return content[-2_000:]
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            if any(t.strip() for t in texts):
                return "\n".join(texts)[-2_000:]
    return None


def _engine():
    spec = os.environ.get("SNAPJUDGE_ENGINE")
    if not spec:
        return None
    from snapjudge.engines import load

    return load(spec)


def log(action: Action, verdict: Verdict, session: str | None) -> None:
    """Append one line to the guard log. Never logs file contents."""
    path = home() / "guard.jsonl"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {
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


def respond(verdict: Verdict) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    if verdict.outcome in ("hold", "ask"):
        reason = f"snapjudge guard: {verdict.reason}"
        if verdict.outcome == "hold":
            reason += (
                ". If it is really needed, explain why and ask the user to run it."
            )
        out["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny" if verdict.outcome == "hold" else "ask",
            "permissionDecisionReason": reason,
        }
    if verdict.error:
        out["systemMessage"] = f"snapjudge guard: {verdict.reason} ({verdict.error})"
    return out or None


def run(stdin=sys.stdin, stdout=sys.stdout, *, record: bool = True) -> int:
    """Hook entry point. Always exits 0: a crashing hook is ignored by Claude
    Code, so failures are reported through systemMessage instead."""
    start = time.perf_counter()
    try:
        payload = json.load(stdin)
        action = action_from_hook(payload)
        if action is None:
            return 0
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
        if record:
            try:
                log(action, verdict, payload.get("session_id"))
            except OSError:
                pass
        out = respond(verdict)
    except Exception as err:  # noqa: BLE001 -- never break the agent, always say so
        out = {"systemMessage": f"snapjudge guard failed and allowed the action: {err}"}
    if out:
        json.dump(out, stdout)
    return 0
