"""Public eval suites, converted to judgetap's case format on demand.

Nothing here is vendored: the first ``judgetap eval --suite <name>`` pages the
split out of the Hugging Face datasets-server JSON API (plain HTTPS, stdlib
only), converts each row to a ``choice`` case and caches the JSONL under
``~/.judgetap/suites`` (override with ``JUDGETAP_SUITES_DIR``). Later runs read
the cache. ``--limit N`` takes a deterministic sample: a fixed seed, so the same
N cases every run and on every machine.
"""

from __future__ import annotations

import json
import os
import random
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from judgetap.errors import JudgetapError

ROWS_API = "https://datasets-server.huggingface.co/rows"
PAGE = 100  # the rows API's maximum page size
SEED = 88
TIMEOUT = 30


@dataclass(frozen=True)
class Suite:
    name: str
    dataset: str  # Hugging Face dataset id
    split: str
    question: str
    licence: str
    source: str  # human-readable page for the dataset
    config: str = "default"
    text_field: str = "text"
    label_field: str = "label"


REGISTRY: dict[str, Suite] = {
    s.name: s
    for s in (
        Suite(
            name="ag_news",
            dataset="fancyzhx/ag_news",
            split="test",
            question="Which topic is this news article about?",
            licence="unknown; AG's corpus is provided for non-commercial research use",
            source="https://huggingface.co/datasets/fancyzhx/ag_news",
        ),
        Suite(
            name="banking77",
            dataset="legacy-datasets/banking77",
            split="test",
            question="Which intent does this banking customer message express?",
            licence="CC-BY-4.0",
            source="https://huggingface.co/datasets/legacy-datasets/banking77",
        ),
    )
}


def cache_dir() -> Path:
    override = os.environ.get("JUDGETAP_SUITES_DIR")
    return Path(override) if override else Path.home() / ".judgetap" / "suites"


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "judgetap-eval"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def _page_url(suite: Suite, offset: int) -> str:
    query = urllib.parse.urlencode(
        {
            "dataset": suite.dataset,
            "config": suite.config,
            "split": suite.split,
            "offset": offset,
            "length": PAGE,
        }
    )
    return f"{ROWS_API}?{query}"


def _label_names(features: list[dict[str, Any]], field: str) -> list[str]:
    for feature in features:
        if feature.get("name") == field:
            names = feature.get("type", {}).get("names")
            if isinstance(names, list) and names:
                return [str(n) for n in names]
    raise JudgetapError(f"no class-label names for {field!r} in the dataset")


def download(suite: Suite) -> list[dict[str, Any]]:
    """Every row of the split, as judgetap case dicts."""
    cases: list[dict[str, Any]] = []
    names: list[str] | None = None
    offset, total = 0, None
    while total is None or offset < total:
        try:
            page = _get_json(_page_url(suite, offset))
        except (OSError, ValueError) as err:
            raise JudgetapError(f"downloading suite {suite.name!r}: {err}") from err
        if names is None:
            names = _label_names(page.get("features", []), suite.label_field)
        total = int(page.get("num_rows_total", 0))
        rows = page.get("rows", [])
        if not rows:
            break
        for item in rows:
            row = item["row"]
            cases.append(
                {
                    "kind": "choice",
                    "question": suite.question,
                    "options": names,
                    "context": row[suite.text_field],
                    "label": names[int(row[suite.label_field])],
                }
            )
        offset += len(rows)
    if not cases:
        raise JudgetapError(f"suite {suite.name!r} downloaded no rows")
    return cases


def suite_path(name: str) -> Path:
    """The cached JSONL for ``name``, downloading it the first time."""
    suite = REGISTRY.get(name)
    if suite is None:
        raise JudgetapError(
            f"unknown suite {name!r}; available: {', '.join(sorted(REGISTRY))}"
        )
    path = cache_dir() / f"{suite.name}-{suite.split}.jsonl"
    if path.exists():
        return path
    cases = download(suite)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.part")
    tmp.write_text("".join(json.dumps(c) + "\n" for c in cases))
    tmp.replace(path)  # a half-written download never looks cached
    return path


def sample(items: list[Any], limit: int | None) -> list[Any]:
    """A fixed-seed sample of ``limit`` items, kept in their original order."""
    if limit is None or limit >= len(items):
        return items
    if limit < 1:
        raise JudgetapError("--limit must be at least 1")
    picked = sorted(random.Random(SEED).sample(range(len(items)), limit))
    return [items[i] for i in picked]
