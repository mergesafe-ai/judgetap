"""Opt-in log of library decisions, in the guard log's format.

Written to the same file the guard uses, so `snapjudge dashboard` shows
both. Only the question text (redacted) and the answer are recorded, never
the context passed with it.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from snapjudge.types import Decision

ENV_VAR = "SNAPJUDGE_LOG"


def enabled(configured: bool) -> bool:
    return configured or os.environ.get(ENV_VAR, "").lower() in ("1", "true", "yes")


def record(decisions: Sequence[Decision]) -> None:
    """Append one line per decision. Never raises: logging must not break
    the caller's decision."""
    try:
        from snapjudge.guard.hook import home
        from snapjudge.guard.rules import redact

        path = home() / "guard.jsonl"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        ts = datetime.now(UTC).isoformat(timespec="milliseconds")
        call = uuid.uuid4().hex  # one engine call answered the whole batch
        lines = [
            json.dumps(
                {
                    "id": uuid.uuid4().hex,
                    "call": call,
                    "ts": ts,
                    "source": "library",
                    "tool": "library",
                    "subject": redact(d.question.text)[:500],
                    "outcome": d.value,
                    "layer": "library",
                    "engine": d.engine,
                    "p": round(d.p, 4),
                    "latency_ms": round(d.latency_ms, 1),
                    "cost_usd": d.cost_usd,
                    "error": None,
                }
            )
            for d in decisions
        ]
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write("".join(line + "\n" for line in lines))
    except Exception:  # noqa: BLE001, S110 -- logging is best effort by design
        pass
