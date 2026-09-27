"""Loop detection: a Claude Code PostToolUse hook, no model involved.

After each tool call the hook records a normalized action (the command or
file path) and a short hash of its error, in a small per-session ring
buffer. When the same action keeps failing the same way, it adds a note to
the agent's context asking it to stop and re-plan. It never blocks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from judgetap.guard.rules import redact

BUFFER = 20  # actions kept per session
WINDOW = 8  # recent actions scanned for repeats
REPEATS = 3  # identical failures that count as a loop
SESSION_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _home() -> Path:
    from judgetap.guard.hook import home

    return home()


def normalize(payload: dict[str, Any]) -> str | None:
    """The action's identity: tool plus command (whitespace-collapsed) or path."""
    tool = payload.get("tool_name")
    inp = payload.get("tool_input") or {}
    if tool == "Bash":
        target = " ".join(str(inp.get("command", "")).split())
    elif tool in ("Write", "Edit", "MultiEdit"):
        target = str(inp.get("file_path", ""))
    else:
        return None
    return f"{tool}:{target}" if target else None


EXIT_PREFIX = re.compile(r"^\s*exit code[: ]\s*(-?\d+)", re.IGNORECASE)


def failure(payload: dict[str, Any]) -> str | None:
    """A short hash of the error, or None when the call succeeded.

    Claude Code sends failures on PostToolUseFailure with the error as a
    top-level `error` string (for Bash, first line "Exit code N"); successes
    arrive on PostToolUse. The tool_response checks cover other agents and
    older shapes."""
    resp = payload.get("tool_response")
    error = payload.get("error")
    failure_event = payload.get("hook_event_name") == "PostToolUseFailure"
    text, failed, code = "", bool(error) or failure_event, None
    if isinstance(error, str):
        m = EXIT_PREFIX.match(error)
        if m:
            code = int(m.group(1))
    if isinstance(resp, dict):
        code = resp.get("exit_code", resp.get("exitCode", resp.get("returncode")))
        if isinstance(code, int) and code != 0:
            failed = True
        if resp.get("is_error") or resp.get("isError") or resp.get("interrupted"):
            failed = True
        text = str(resp.get("stderr") or resp.get("error") or "")
    elif isinstance(resp, str):
        # Claude Code renders a failed Bash call as "Exit code N\n<output>".
        m = EXIT_PREFIX.match(resp)
        if m and m.group(1) != "0":
            failed, text, code = True, resp, int(m.group(1))
        elif resp.lower().startswith(("error", "fatal")):
            failed, text = True, resp
    if error:
        text = str(error)
    if not failed:
        return None
    # Numbers vary between retries (pids, timings, line numbers): ignore them.
    stable = re.sub(r"\d+", "#", text.strip())[:2000]
    if not stable:
        # No diagnostic text: distinguish failures by their exit status.
        stable = f"exit:{code}"
    return hashlib.sha256(stable.encode()).hexdigest()[:12]


SESSION_TTL_SECONDS = 7 * 24 * 3600
PRUNE_EVERY_SECONDS = 3600


def prune_sessions(directory: Path, now: float | None = None) -> None:
    """Delete session state (json, stop, tmp) untouched for SESSION_TTL_SECONDS.

    Lock files are never deleted: unlinking a lock someone holds would let a
    second hook lock a fresh inode and break mutual exclusion. They are empty,
    so keeping them costs an inode, not space. A session's data is only
    removed while holding its lock without waiting; a busy session is skipped.
    Runs at most once per PRUNE_EVERY_SECONDS and never raises."""
    try:
        now = time.time() if now is None else now
        marker = directory / ".pruned"
        if marker.exists() and now - marker.stat().st_mtime < PRUNE_EVERY_SECONDS:
            return
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        marker.touch()
        os.utime(marker, (now, now))
        for f in directory.iterdir():
            if f.name == ".pruned" or f.suffix not in (".json", ".stop", ".tmp"):
                continue
            try:
                if now - f.stat().st_mtime <= SESSION_TTL_SECONDS:
                    continue
                _unlink_if_unlocked(f)
            except OSError:
                continue
    except OSError:
        return


TMP_NAME = re.compile(r"^(?P<stem>.+)\.[0-9a-f]{32}\.tmp$")


def _lock_for(f: Path) -> Path:
    """The lock _session_lock takes for this file. State files are
    `<id>.json` / `<id>.stop` and lock `<id>.lock` (with_suffix, so a
    session id may itself contain dots); their temp files are
    `<id>.<uuid>.tmp` (with_suffix replaces .json/.stop), so the stem before
    the uuid is the session id."""
    m = TMP_NAME.match(f.name)
    if m:
        return f.with_name(m.group("stem") + ".lock")
    return f.with_suffix(".lock")


def _unlink_if_unlocked(f: Path) -> None:
    """Remove f only while holding its session lock (non-blocking)."""
    lock = _lock_for(f)
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass  # no flock (Windows): best effort, as for the hooks themselves
        except OSError:
            return  # a hook holds it: this session is in use, keep its data
        f.unlink(missing_ok=True)
    finally:
        os.close(fd)


def _state_path(session: str | None) -> Path | None:
    if not session or not SESSION_ID.fullmatch(session):
        return None
    return _home() / "sessions" / f"{session}.json"


@contextmanager
def _session_lock(path: Path):
    """An exclusive lock on <session>.lock (POSIX flock). Where flock isn't
    available (Windows), updates run unlocked: a missed note, never a crash."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path.with_suffix(".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        os.close(fd)  # closing releases the lock


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save(path: Path, actions: list[dict[str, Any]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(actions[-BUFFER:], fh)
    os.replace(tmp, path)  # atomic: a concurrent hook never reads half a file


def detect(actions: list[dict[str, Any]]) -> int:
    """How many times the latest action has failed the same way within the
    last WINDOW actions, if that is at least REPEATS; else 0. A success of
    that action, or a different error from it, ends the count."""
    if not actions or not actions[-1].get("err"):
        return 0
    last, count = actions[-1], 0
    for a in reversed(actions[-WINDOW:]):
        if a.get("key", a["act"]) != last.get("key", last["act"]):
            continue  # other actions in between don't break a loop
        if a.get("err") != last["err"]:
            break
        count += 1
    return count if count >= REPEATS else 0


def _log(session: str | None, act: str, count: int) -> None:
    path = _home() / "guard.jsonl"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "session": session,
        "tool": act.split(":", 1)[0],
        "subject": redact(act.split(":", 1)[1])[:500],
        "outcome": "note",
        "layer": "loop",
        "rule": "loop",
        "reason": f"{count} identical failures",
        "p": {},
        "engine": None,
        "latency_ms": 0.0,
        "cost_usd": None,
        "error": None,
    }
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def handle(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Record this call; return the hook output when it completes a loop."""
    act = normalize(payload)
    path = _state_path(payload.get("session_id"))
    if act is None or path is None:
        return None
    if payload.get("is_interrupt"):
        return None  # an abort, not an error the tool reported: not a loop signal
    prune_sessions(path.parent)
    # Only a hash and the (redacted) action are stored: never file contents.
    # Hooks for one session can finish together: serialise the
    # read-append-write so no action is lost.
    with _session_lock(path):
        actions = _load(path)
        # Identity is a hash of the full action (so long commands that differ
        # late don't collide); the redacted, truncated text is only for display.
        actions.append(
            {
                "key": hashlib.sha256(act.encode()).hexdigest()[:16],
                "act": redact(act)[:500],
                "err": failure(payload),
            }
        )
        _save(path, actions)
    count = detect(actions)
    if not count:
        return None
    _log(payload.get("session_id"), actions[-1]["act"], count)
    shown = actions[-1]["act"].split(":", 1)[1][:200]
    event = payload.get("hook_event_name")
    if event not in ("PostToolUse", "PostToolUseFailure"):
        event = "PostToolUseFailure"
    return {
        "hookSpecificOutput": {
            # Answer on the event that fired (PostToolUseFailure for failures).
            "hookEventName": event,
            "additionalContext": (
                f"judgetap: `{shown}` has now failed the same way {count} times; "
                "stop and re-plan (read the error, try a different approach)."
            ),
        }
    }


def run(stdin=sys.stdin, stdout=sys.stdout) -> int:
    """Hook entry point. Always exits 0 and stays silent on any error."""
    try:
        out = handle(json.load(stdin))
    except Exception:  # noqa: BLE001 -- loop detection must never disturb the agent
        return 0
    if out:
        json.dump(out, stdout)
    return 0
