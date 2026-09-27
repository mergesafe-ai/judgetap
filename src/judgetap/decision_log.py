"""Opt-in log of library decisions, in the guard log's format.

Written to the same file the guard uses, so `judgetap dashboard` shows
both. Only the question text (redacted) and the answer are recorded, never
the context passed with it.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from judgetap._compat import env
from judgetap.types import Decision

ENV_VAR = "JUDGETAP_LOG"  # SNAPJUDGE_LOG still read for one release


def enabled(configured: bool) -> bool:
    return configured or (env("LOG") or "").lower() in ("1", "true", "yes")


def record(decisions: Sequence[Decision]) -> None:
    """Append one line per decision. Never raises: logging must not break
    the caller's decision."""
    try:
        from judgetap.guard.hook import home
        from judgetap.guard.rules import redact

        path = home() / "guard.jsonl"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        ts = datetime.now(UTC).isoformat(timespec="milliseconds")
        call_id = uuid.uuid4().hex
        # The batch's engine calls are written once, on its first record;
        # every record carries the batch id so readers count them once.
        calls = [c.as_dict() for c in (decisions[0].calls if decisions else ())]
        lines = [
            json.dumps(
                {
                    "id": uuid.uuid4().hex,
                    "ts": ts,
                    "source": "library",
                    "tool": "library",
                    "subject": redact(d.question.text)[:500],
                    # Answer labels are caller-defined text: redact them too.
                    "outcome": redact(d.value)[:200],
                    "layer": "library",
                    "engine": d.engine,
                    "p": round(d.p, 4),
                    "latency_ms": round(d.latency_ms, 1),
                    "cost_usd": d.cost_usd,
                    "error": None,
                    "batch": call_id,
                    "calls": calls if i == 0 else [],
                }
            )
            for i, d in enumerate(decisions)
        ]
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write("".join(line + "\n" for line in lines))
    except Exception:  # noqa: BLE001, S110 -- logging is best effort by design
        pass
