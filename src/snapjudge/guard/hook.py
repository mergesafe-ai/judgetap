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


def last_user_message(transcript_path: str | None) -> str | None:
    """The newest plain-text user turn in a Claude Code JSONL transcript."""
    if not transcript_path or not Path(transcript_path).is_file():
        return None
    last = None
    with open(transcript_path, errors="replace") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "user":
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str) and content.strip():
                last = content
            elif isinstance(content, list):
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                if any(t.strip() for t in texts):
                    last = "\n".join(texts)
    return last[-2_000:] if last else None


def _engine():
    spec = os.environ.get("SNAPJUDGE_ENGINE")
    if not spec:
        return None
    from snapjudge.engines import load

    return load(spec)


def log(action: Action, verdict: Verdict, session: str | None) -> None:
    """Append one line to the guard log. Never logs file contents."""
    path = home() / "guard.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "session": session,
        "tool": action.tool,
        "subject": (action.command or action.path or "")[:500],
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
    with path.open("a") as fh:
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


def run(stdin=sys.stdin, stdout=sys.stdout) -> int:
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
        rules = []
        for path in (home() / "guard.toml", action.cwd / "guard.toml"):
            rules += load_user_rules(path)
        verdict = check(action, engine, rules)
        if engine_error and not verdict.error:
            verdict.error = engine_error
            verdict.reason = verdict.reason or "engine unavailable; rules only"
        verdict.latency_ms = verdict.latency_ms or (time.perf_counter() - start) * 1000
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
