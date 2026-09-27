"""Task-done check: an opt-in, experimental Claude Code Stop hook.

When the agent tries to stop, ask the engine whether the user's task is
actually finished. Only a confident "not done" blocks the stop, at most
twice per session, so the check can never trap the agent. Off by default:
it needs a labelled eval set before it can be trusted on by default.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from judgetap.api import batch
from judgetap.guard.loop import SESSION_ID, _session_lock
from judgetap.types import Question

DEFAULT_THRESHOLD = 0.15  # block only when p(done) is at or below this
MAX_BLOCKS = 2  # per session
ASSISTANT_TAIL_CHARS = 4_000

QUESTION = Question.yesno(
    "Has the assistant fully completed the user's task, with nothing left it said it would do?"
)


def _hook():
    from judgetap.guard import hook

    return hook


def threshold() -> float:
    """`stop_threshold` from ~/.judgetap/guard.toml, else the default."""
    path = _hook().home() / "guard.toml"
    try:
        with path.open("rb") as fh:
            value = tomllib.load(fh).get("stop_threshold", DEFAULT_THRESHOLD)
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_THRESHOLD
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 <= value <= 1
    ):
        return DEFAULT_THRESHOLD
    return float(value)


def _assistant_text(raw: bytes) -> str | None:
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        texts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t.strip())
        return joined or None
    return None


def task_and_reply(
    transcript_path: str | None, limit: int = ASSISTANT_TAIL_CHARS
) -> tuple[str | None, str | None]:
    """(the user's latest task, what the assistant said since), from one
    read of the bounded transcript window. The reply is newest-last and at
    most `limit` characters."""
    if not transcript_path or not Path(transcript_path).is_file():
        return None, None
    hook = _hook()
    parts: list[str] = []
    size, task = 0, None
    for raw in hook._lines_backward(transcript_path, hook.TRANSCRIPT_SCAN_BYTES):
        task = hook._user_text(raw)
        if task:
            break  # only what the assistant said since the user's last turn
        if size >= limit:
            continue  # enough reply text: keep scanning only for the task
        text = _assistant_text(raw)
        if text:
            parts.append(text)
            size += len(text)
    reply = "\n".join(reversed(parts))[-limit:] if parts else None
    return task, reply


def last_assistant_text(
    transcript_path: str | None, limit: int = ASSISTANT_TAIL_CHARS
) -> str | None:
    """What the assistant said since the user's last turn (see task_and_reply)."""
    return task_and_reply(transcript_path, limit)[1]


def _count_path(session: str) -> Path:
    return _hook().home() / "sessions" / f"{session}.stop"


def _take_block(session: str) -> bool:
    """Count one block for this session; False once the cap is reached."""
    path = _count_path(session)
    with _session_lock(path):
        try:
            count = int(path.read_text().strip() or 0)
        except (OSError, ValueError):
            count = 0
        if count >= MAX_BLOCKS:
            return False
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(str(count + 1))
        os.replace(tmp, path)
    return True


def _log(
    session: str | None,
    outcome: str,
    reason: str,
    p: float | None,
    engine: str | None,
    calls=(),
) -> None:
    path = _hook().home() / "guard.jsonl"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "session": session,
        "tool": "Stop",
        "subject": "task-done check",
        "outcome": outcome,
        "layer": "stop",
        "rule": "stop",
        "reason": reason,
        "p": {"done": round(p, 4)} if p is not None else {},
        "engine": engine,
        "latency_ms": 0.0,
        "cost_usd": None,
        "error": None,
        # The engine calls behind this check, so the dashboard counts them.
        "calls": [c.as_dict() for c in calls],
    }
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def handle(payload: dict[str, Any], engine=None) -> dict[str, Any] | None:
    """The hook's JSON output, or None to let the agent stop."""
    if payload.get("stop_hook_active"):
        return None  # already continuing because of a block: never loop
    session = payload.get("session_id")
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        return None
    if engine is None:
        try:
            engine = _hook()._engine()
        except Exception as err:  # noqa: BLE001 -- misconfiguration: say so, let it stop
            return {"systemMessage": f"judgetap stop check skipped: {err}"}
    if engine is None:
        return None  # needs a judge: no rules-only behaviour here
    transcript = payload.get("transcript_path")
    task, said = task_and_reply(transcript)
    if not task or not said:
        _log(
            session,
            "skip",
            "no task or assistant text in the transcript",
            None,
            getattr(engine, "name", None),
        )
        return None
    decision = batch(
        [QUESTION],
        {"user_task": task, "assistant_last_message": said},
        engine=engine,
        log=False,
    )[0]
    p_done = decision.p_yes
    if p_done > threshold():
        _log(session, "allow", "looks done", p_done, decision.engine, decision.calls)
        return None
    if not _take_block(session):
        _log(
            session,
            "allow",
            f"not-done but the {MAX_BLOCKS}-block cap is reached",
            p_done,
            decision.engine,
            decision.calls,
        )
        return None
    _log(
        session, "block", "judged not finished", p_done, decision.engine, decision.calls
    )
    return {
        "decision": "block",
        "reason": (
            f"judgetap: the task doesn't look finished (p(done)={p_done:.2f}). "
            "Continue, or say explicitly what's left and why you're stopping."
        ),
    }


def run(stdin=sys.stdin, stdout=sys.stdout) -> int:
    """Hook entry point. Always exits 0; any failure lets the agent stop."""
    try:
        out = handle(json.load(stdin))
    except Exception:  # noqa: BLE001 -- the check must never disturb the agent
        return 0
    if out:
        json.dump(out, stdout)
    return 0
