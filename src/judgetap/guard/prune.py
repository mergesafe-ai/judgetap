"""Shadow-mode output pruning (v0.5, #27/#29): observe only.

On Claude Code PostToolUse, a large tool output is labelled keep, summarize
or drop by a few rules (no model), and the guard log records the tokens a
summarize or drop *would* have saved. The output itself is never modified,
suppressed or replaced, and never logged (a redacted excerpt at most). Real
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

from judgetap.guard.rules import redact

DEFAULT_THRESHOLD = 2000  # chars; smaller outputs aren't considered
SUMMARY_CHARS = 500  # what a summary is assumed to keep
EXCERPT_CHARS = 200
CHARS_PER_TOKEN = 4

ERROR = re.compile(
    r"Traceback \(most recent call last\)|\b(error|exception|fatal|panic)\b[:\]]",
    re.IGNORECASE,
)
PROGRESS = re.compile(r"\d{1,3}(\.\d+)?\s?%|[#=█▇▆▅▄▃▂▁-]{10,}|\r")
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
    progress = sum(1 for ln in lines if PROGRESS.search(ln)) / len(lines)
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
        verdict = label(text, record["tool"], failed)
        record.update(
            label=verdict,
            output_chars=len(text),
            est_tokens_saved=tokens_saved(len(text), verdict),
            subject=redact(text[:EXCERPT_CHARS])[:EXCERPT_CHARS],
            reason=f"shadow: {verdict} (estimated, pruning is off)",
        )
    except Exception as exc:  # noqa: BLE001 -- shadow mode must never disturb the agent
        record.update(label=None, est_tokens_saved=0, error=type(exc).__name__)
    try:
        _log(record)
    except Exception:  # noqa: BLE001, S110
        pass
