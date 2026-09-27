"""Read the guard log and feedback file into what the page shows."""

from __future__ import annotations

import json
import math
import os
import threading
from collections import Counter, defaultdict, deque
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
    calls: Counter = Counter()  # true totals; the latency deques are capped
    recent: deque = deque(maxlen=MAX_RECENT)
    cost, total, false_holds, library, loops, stops = 0.0, 0, 0, 0, 0, 0
    for n, raw in _iter_jsonl(home / "guard.jsonl"):
        try:
            r = _clean(raw)
            r["id"] = record_id(r, n)
        except Exception:  # noqa: BLE001, S112 -- one unreadable record never breaks the page
            continue
        r["source"] = r.get("source") or "guard"
        r["false_alarm"] = r["id"] in false_alarms
        cost += r.get("cost_usd") or 0
        recent.append(r)
        _count_calls(r, calls, by_engine)
        if r.get("layer") == "stop":
            stops += 1  # a task-done check, not a guarded call
            continue
        if r.get("layer") == "loop":
            loops += 1  # a note after repeated failures, not a guarded call
            continue
        if r["source"] == "library":
            # Library outcomes are answers ("billing", "yes"), not guard
            # verdicts: counted apart, kept out of hold/ask/allow and the chart.
            library += 1
            continue
        total += 1
        outcomes[r.get("outcome")] += 1
        if r.get("outcome") == "hold" and r["false_alarm"]:
            false_holds += 1
        day = str(r.get("ts", ""))[:10]
        if day in per_day:
            per_day[day][r.get("outcome")] += 1
    summary = {
        "total": total,
        "library": library,
        "loop_notes": loops,
        "stop_checks": stops,
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


STR_FIELDS = (
    "id",
    "ts",
    "session",
    "source",
    "tool",
    "subject",
    "outcome",
    "layer",
    "rule",
    "reason",
    "engine",
    "error",
    "call",
    "batch",
)
NUM_FIELDS = ("cost_usd", "latency_ms")


def _finite(value: Any) -> float | None:
    """A finite int/float as float; anything else (str, bool, NaN, inf) is None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _scrub(value: Any) -> Any:
    """NaN/Infinity anywhere (even in fields the page doesn't use) become None."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _clean(r: dict[str, Any]) -> dict[str, Any]:
    """The log is ours but may be edited, truncated or written by an older
    version: coerce every field the page uses to its expected type, so no
    value can crash the summary or make the JSON invalid (NaN)."""
    out = _scrub(dict(r))
    for key in STR_FIELDS:
        if key in out and not isinstance(out[key], str):
            out[key] = None
    for key in NUM_FIELDS:
        if key in out:
            out[key] = _finite(out[key])
    if not isinstance(out.get("p"), dict):
        out["p"] = {}
    else:
        out["p"] = {
            k: v
            for k, v in out["p"].items()
            if isinstance(k, str) and _finite(v) is not None
        }
    if "calls" in out:
        # Calls are echoed back in "recent" too: keep only well-formed ones
        # with a finite (or absent) latency.
        calls = out["calls"] if isinstance(out["calls"], list) else []
        out["calls"] = [
            {**c, "latency_ms": _finite(c.get("latency_ms"))}
            for c in calls
            if isinstance(c, dict) and isinstance(c.get("engine"), str)
        ]
    return out


def _count_calls(r: dict[str, Any], calls: Counter, by_engine) -> None:
    """Engine metrics come from the calls each record reports, each with its
    own latency: nothing is inferred. A batch writes its calls on one record
    only. Records from before calls were logged fall back: a successful guard
    judgement counts one call (with its latency) of its answering engine, and
    a Stop check that asked its engine counts one call (no latency recorded)."""
    reported = r.get("calls")
    if isinstance(reported, list):
        for c in reported:
            if isinstance(c, dict) and isinstance(c.get("engine"), str):
                calls[c["engine"]] += 1
                latency = _finite(c.get("latency_ms"))
                if latency is not None:
                    by_engine[c["engine"]].append(latency)
        return
    if not r.get("engine") or r.get("error"):
        return
    if r.get("layer") == "judge":
        calls[r["engine"]] += 1
        by_engine[r["engine"]].append(float(r.get("latency_ms") or 0))
    elif r.get("layer") == "stop" and r.get("outcome") in ("allow", "block"):
        # A Stop check from before calls were logged: it asked its engine
        # once, but recorded no latency, so only the call is counted.
        calls[r["engine"]] += 1


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
