"""Shadow-mode output pruning (v0.5, #27/#29): observe only.

On Claude Code PostToolUse, a large tool output is labelled keep, summarize
or drop by a few rules (no model), and the guard log records the tokens a
summarize or drop *would* have saved. The output itself is never modified,
suppressed or replaced, and none of it is logged: only its size and label. Real
pruning waits for the labelled set in #89.

Tokens are estimated as characters / 4, not counted with a tokenizer.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
import uuid
from datetime import UTC, datetime
from typing import Any

DEFAULT_THRESHOLD = 2000  # chars; smaller outputs aren't considered
SUMMARY_CHARS = 500  # what a summary is assumed to keep
SCAN_CHARS = 200_000  # a huge output is labelled on its head and tail only
CHARS_PER_TOKEN = 4

ERROR = re.compile(
    r"Traceback \(most recent call last\)"
    r"|\b(error|exception|fatal|panic|failed|failure|denied)\b"
    r"|\bE\d{3,}\b|\bERR!",
    re.IGNORECASE,
)
# A progress line carries at least two of the three progress signals: a
# percentage, a counter ("12/340") and a bar (any run of 8+ symbol
# characters, whatever it is drawn with), or a carriage return. One signal
# alone isn't progress: a bar alone is a diff stat ("| 12 ++++----") or a
# dotted leader, a percentage alone is a coverage report. Every pattern is a
# single character-class run, so matching is linear in the line length.
BAR = re.compile(r"[^\w\s]{8,}")
PERCENT = re.compile(r"\d\s?%")
COUNTER = re.compile(r"\d/\d")


def _is_progress(line: str) -> bool:
    if "\r" in line:
        return True
    signals = (PERCENT.search(line), COUNTER.search(line), BAR.search(line))
    return sum(1 for m in signals if m) >= 2


PATHLIKE = re.compile(r"^\s*[\w./@~-]+\.\w+(:\d+)?(:|$)")


def _home():
    from judgetap.guard.hook import home

    return home()


def threshold() -> int:
    """`prune_threshold` (chars) from ~/.judgetap/guard.toml, else the default."""
    try:
        with (_home() / "guard.toml").open("rb") as fh:
            value = tomllib.load(fh).get("prune_threshold", DEFAULT_THRESHOLD)
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_THRESHOLD
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return DEFAULT_THRESHOLD
    return value


def output_text(payload: dict[str, Any]) -> str:
    """The tool output as text: a string response, or a dict's stdout/stderr
    (or content/output), else its JSON."""
    resp = payload.get("tool_response")
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        parts = [
            resp[k]
            for k in ("stdout", "stderr", "output", "content")
            if isinstance(resp.get(k), str)
        ]
        if parts:
            return "\n".join(parts)
    if resp is None:
        return ""
    return json.dumps(resp, default=str)


def label(text: str, tool: str | None = None, failed: bool = False) -> str:
    """keep, summarize or drop. Conservative: anything not clearly noise is keep."""
    if failed or ERROR.search(text):
        return "keep"  # errors are what the agent needs to read
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 20:
        return "keep"
    progress = sum(1 for ln in lines if _is_progress(ln)) / len(lines)
    if progress >= 0.5 or text.count("\r") >= 20:
        return "drop"
    if len(set(lines)) / len(lines) < 0.3:
        return "summarize"  # long repetitive log
    if len(lines) >= 100 and (
        tool in ("Grep", "Glob")
        or sum(1 for ln in lines if PATHLIKE.match(ln)) / len(lines) >= 0.8
    ):
        return "summarize"  # very long search listing
    return "keep"


def tokens_saved(chars: int, verdict: str) -> int:
    """Estimated (chars / 4) tokens a verdict would save if pruning were on."""
    if verdict == "drop":
        return chars // CHARS_PER_TOKEN
    if verdict == "summarize":
        return max(0, chars - SUMMARY_CHARS) // CHARS_PER_TOKEN
    return 0


def _log(record: dict[str, Any]) -> None:
    path = _home() / "guard.jsonl"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def observe(payload: dict[str, Any], failed: bool = False) -> None:
    """Label a large output and log the estimate. Never raises; an error is
    noted in the log (best effort) and otherwise ignored."""
    record: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "session": payload.get("session_id")
        if isinstance(payload.get("session_id"), str)
        else None,
        "tool": payload.get("tool_name")
        if isinstance(payload.get("tool_name"), str)
        else None,
        "outcome": "note",
        "layer": "prune",
        "rule": "prune-shadow",
        "p": {},
        "engine": None,
        "latency_ms": 0.0,
        "cost_usd": None,
        "error": None,
    }
    try:
        text = output_text(payload)
        if len(text) <= threshold():
            return
        # Label a bounded sample, the head and the tail, so a huge output
        # doesn't balloon the hook and an error at the end still counts.
        half = SCAN_CHARS // 2
        sample = text if len(text) <= SCAN_CHARS else text[:half] + "\n" + text[-half:]
        # Errors are searched for in the whole output (a regex scan allocates
        # nothing); only the line-level rules use the sample.
        verdict = label(sample, record["tool"], failed or bool(ERROR.search(text)))
        record.update(
            label=verdict,
            output_chars=len(text),
            est_tokens_saved=tokens_saved(len(text), verdict),
            reason=f"shadow: {verdict} (estimated, pruning is off)",
        )
    except Exception as exc:  # noqa: BLE001 -- shadow mode must never disturb the agent
        record.update(label=None, est_tokens_saved=0, error=type(exc).__name__)
    try:
        _log(record)
    except Exception:  # noqa: BLE001, S110
        pass
