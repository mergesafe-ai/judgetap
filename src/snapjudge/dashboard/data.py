"""Read the guard log and feedback file into what the page shows."""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_RECENT = 500


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a line cut off by a concurrent write
            if isinstance(record, dict):
                out.append(record)
    return out


def record_id(record: dict[str, Any]) -> str:
    """Stable id for a log line: its timestamp plus session."""
    return f"{record.get('ts', '')}|{record.get('session') or ''}"


def load(home: Path) -> dict[str, Any]:
    records = _read_jsonl(home / "guard.jsonl")
    false_alarms = {
        f["id"]
        for f in _read_jsonl(home / "feedback.jsonl")
        if f.get("verdict") == "false-alarm"
    }
    for r in records:
        r["id"] = record_id(r)
        r["false_alarm"] = r["id"] in false_alarms
    return {"summary": summarise(records), "recent": records[-MAX_RECENT:][::-1]}


def _rank(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(q * len(values)) - 1)]


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = Counter(r.get("outcome") for r in records)
    by_engine: dict[str, list[float]] = defaultdict(list)
    per_day: dict[str, Counter] = defaultdict(Counter)
    cost = 0.0
    for r in records:
        if r.get("layer") == "judge" and not r.get("error") and r.get("engine"):
            by_engine[r["engine"]].append(float(r.get("latency_ms") or 0))
        cost += r.get("cost_usd") or 0
        day = str(r.get("ts", ""))[:10]
        if day:
            per_day[day][r.get("outcome")] += 1
    holds = [r for r in records if r.get("outcome") == "hold"]
    total = len(records)
    return {
        "total": total,
        "outcomes": dict(outcomes),
        "holds_per_1000": round(1000 * outcomes["hold"] / total, 1) if total else None,
        "false_alarms": sum(1 for r in holds if r.get("false_alarm")),
        "cost_usd": round(cost, 6),
        "engines": {
            name: {"calls": len(v), "p50_ms": _rank(v, 0.5), "p95_ms": _rank(v, 0.95)}
            for name, v in sorted(by_engine.items())
        },
        "per_day": {day: dict(c) for day, c in sorted(per_day.items())[-30:]},
    }


def mark_false_alarm(home: Path, rid: str, known: set[str]) -> bool:
    """Record that a hold was a false alarm. Only ids present in the log."""
    if rid not in known:
        return False
    path = home / "feedback.jsonl"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    entry = {
        "id": rid,
        "verdict": "false-alarm",
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(entry) + "\n")
    return True
