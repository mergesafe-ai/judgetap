"""Read the guard log and feedback file into what the page shows."""

from __future__ import annotations

import json
import math
import os
import threading
from collections import Counter, OrderedDict, defaultdict, deque
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

MAX_RECENT = 500


DAYS = 30
MAX_LATENCIES = 10_000  # per engine, newest kept: bounded memory


def _iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """(line number, record) for each parseable line, streamed."""
    if not path.is_file():
        return
    with path.open() as fh:
        for n, line in enumerate(fh, 1):
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a line cut off by a concurrent write
            if isinstance(record, dict):
                yield n, record


def record_id(record: dict[str, Any], line: int = 0) -> str:
    """The id the guard wrote, or for older lines without one, timestamp,
    session and line number, which is unique within the log."""
    if record.get("id"):
        return str(record["id"])
    return f"{record.get('ts', '')}|{record.get('session') or ''}|{line}"


_cache: dict[tuple, dict[str, Any]] = {}
_cache_lock = threading.Lock()  # handlers run on ThreadingHTTPServer threads


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def load(home: Path, today: date | None = None) -> dict[str, Any]:
    """Summary and recent records. Cached until the log or feedback file
    changes (or the day rolls over), so the page's 10 s polling doesn't
    rescan an idle log."""
    today = today or datetime.now(UTC).date()
    key = (
        str(home),
        today,
        _stamp(home / "guard.jsonl"),
        _stamp(home / "feedback.jsonl"),
    )
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            _cache.clear()  # only the latest state is worth keeping
            hit = _cache[key] = _load(home, today)
        return hit


def _load(home: Path, today: date) -> dict[str, Any]:
    false_alarms = {
        f["id"]
        for _, f in _iter_jsonl(home / "feedback.jsonl")
        if f.get("verdict") == "false-alarm" and isinstance(f.get("id"), str)
    }
    window = [(today - timedelta(days=i)).isoformat() for i in range(DAYS - 1, -1, -1)]
    per_day: dict[str, Counter] = {d: Counter() for d in window}
    outcomes: Counter = Counter()
    by_engine: dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_LATENCIES))
    calls: Counter = Counter()
    # A batch's records are written together, so remembering the last few
    # (call, engine) pairs is enough to count each once: memory stays bounded.
    seen_calls: OrderedDict[tuple[str, str], None] = (
        OrderedDict()
    )  # true totals; the latency deques are capped
    recent: deque = deque(maxlen=MAX_RECENT)
    cost, total, false_holds, library = 0.0, 0, 0, 0
    for n, r in _iter_jsonl(home / "guard.jsonl"):
        r["id"] = record_id(r, n)
        r["source"] = r.get("source") or "guard"
        r["false_alarm"] = r["id"] in false_alarms
        cost += r.get("cost_usd") or 0
        recent.append(r)
        if r["source"] == "library":
            # Library outcomes are answers ("billing", "yes"), not guard
            # verdicts: counted apart, kept out of hold/ask/allow and the chart.
            library += 1
            # A batch is one engine call: count it (and its latency) once.
            # Every engine a cascade consulted made a call; the end-to-end
            # latency is attributed to the engine that answered.
            for hop in r.get("hops") or ([r["engine"]] if r.get("engine") else []):
                key = (r.get("call") or r["id"], hop)
                if key in seen_calls:
                    continue
                seen_calls[key] = None
                if len(seen_calls) > 256:
                    seen_calls.popitem(last=False)
                calls[hop] += 1
                if hop == r.get("engine"):
                    by_engine[hop].append(float(r.get("latency_ms") or 0))
            continue
        total += 1
        outcomes[r.get("outcome")] += 1
        if r.get("outcome") == "hold" and r["false_alarm"]:
            false_holds += 1
        if r.get("layer") == "judge" and not r.get("error") and r.get("engine"):
            by_engine[r["engine"]].append(float(r.get("latency_ms") or 0))
            calls[r["engine"]] += 1
        day = str(r.get("ts", ""))[:10]
        if day in per_day:
            per_day[day][r.get("outcome")] += 1
    summary = {
        "total": total,
        "library": library,
        "outcomes": dict(outcomes),
        "holds_per_1000": round(1000 * outcomes["hold"] / total, 1) if total else None,
        "false_alarms": false_holds,
        "cost_usd": round(cost, 6),
        "engines": {
            name: {
                "calls": calls[name],
                "p50_ms": _rank(by_engine.get(name, ()), 0.5),
                "p95_ms": _rank(by_engine.get(name, ()), 0.95),
            }
            for name in sorted(set(calls) | set(by_engine))
        },
        "per_day": {d: dict(c) for d, c in per_day.items()},
    }
    return {"summary": summary, "recent": list(recent)[::-1]}


def _rank(values: Iterable[float], q: float) -> float | None:
    values = sorted(values)
    if not values:
        return None
    return values[max(0, math.ceil(q * len(values)) - 1)]


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
