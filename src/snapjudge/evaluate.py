"""Calibration check: run labelled cases through engines and compare them.

A case file is JSONL, one case per line:

    {"kind": "yesno", "question": "Irreversible?", "context": {...}, "label": "yes"}
    {"kind": "choice", "question": "Route", "options": ["a", "b"], "context": "...", "label": "b"}

For each engine the report gives accuracy, expected calibration error (ECE),
a reliability table, p50/p95 latency and cost per 1,000 decisions.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from snapjudge.api import batch
from snapjudge.engine import Engine
from snapjudge.errors import SnapjudgeError
from snapjudge.types import Question

BINS = 10


@dataclass(frozen=True)
class Case:
    question: Question
    context: Any
    label: str


@dataclass
class EngineReport:
    engine: str
    cases: int = 0
    answered: int = 0
    errors: int = 0
    accuracy: float | None = None
    ece: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    usd_per_1k: float | None = None
    calibrated: bool = True
    # One row per confidence bin with at least one answer.
    reliability: list[dict[str, float]] = field(default_factory=list)


def load_cases(path: str | Path) -> list[Case]:
    cases = []
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                kind = raw["kind"]
                for key in ("options", "levels"):
                    if key in raw and not isinstance(raw[key], list):
                        raise ValueError(f"{key} must be a JSON list")
                if kind == "yesno":
                    q = Question.yesno(raw["question"])
                elif kind == "choice":
                    q = Question.choice(raw["question"], raw["options"])
                elif kind == "score":
                    q = Question.score(raw["question"], raw["levels"])
                else:
                    raise ValueError(f"unknown kind {kind!r}")
                label = raw["label"]
                if label not in q.options:
                    raise ValueError(
                        f"label {label!r} is not one of {list(q.options)!r}"
                    )
                cases.append(Case(q, raw.get("context"), label))
            except (KeyError, ValueError, TypeError, SnapjudgeError) as err:
                raise SnapjudgeError(f"{path}:{n}: {err}") from err
    if not cases:
        raise SnapjudgeError(f"{path} has no cases")
    return cases


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def evaluate(cases: Sequence[Case], engine: Engine) -> EngineReport:
    """Each case is one call, so latency is per decision and errors are counted
    per case rather than failing the run."""
    report = EngineReport(engine=engine.name, cases=len(cases))
    hits: list[tuple[float, bool]] = []
    latencies: list[float] = []
    costs: list[float | None] = []
    for case in cases:
        try:
            decision = batch([case.question], case.context, engine=engine)[0]
        except Exception:  # noqa: BLE001 -- an engine failure is a data point, not a crash
            report.errors += 1
            continue
        hits.append((decision.p, decision.value == case.label))
        latencies.append(decision.latency_ms)
        costs.append(decision.cost_usd)
        report.calibrated = report.calibrated and decision.calibrated
    report.answered = len(hits)
    if not hits:
        return report
    report.accuracy = sum(ok for _, ok in hits) / len(hits)
    report.ece, report.reliability = _calibration(hits)
    latencies.sort()
    report.p50_ms, report.p95_ms = (
        percentile(latencies, 0.5),
        percentile(latencies, 0.95),
    )
    if None not in costs:
        report.usd_per_1k = 1000 * sum(costs) / len(costs)
    return report


def _calibration(
    hits: Iterable[tuple[float, bool]],
) -> tuple[float, list[dict[str, float]]]:
    """Expected calibration error over equal-width confidence bins."""
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(BINS)]
    total = 0
    for p, ok in hits:
        bins[min(BINS - 1, math.floor(p * BINS))].append((p, ok))
        total += 1
    ece, rows = 0.0, []
    for i, members in enumerate(bins):
        if not members:
            continue
        conf = sum(p for p, _ in members) / len(members)
        acc = sum(ok for _, ok in members) / len(members)
        ece += len(members) / total * abs(conf - acc)
        rows.append(
            {
                "bin": f"{i / BINS:.1f}-{(i + 1) / BINS:.1f}",
                "n": len(members),
                "confidence": conf,
                "accuracy": acc,
            }
        )
    return ece, rows


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def to_markdown(reports: Sequence[EngineReport]) -> str:
    lines = [
        "| engine | answered | errors | accuracy | ECE | p50 ms | p95 ms | $ / 1k |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        ece = _fmt(r.ece, ".3f") + ("" if r.calibrated else " (self-reported)")
        lines.append(
            f"| {r.engine} | {r.answered}/{r.cases} | {r.errors} | {_fmt(r.accuracy, '.1%')} "
            f"| {ece} | {_fmt(r.p50_ms, '.0f')} | {_fmt(r.p95_ms, '.0f')} "
            f"| {_fmt(r.usd_per_1k, '.4f')} |"
        )
    for r in reports:
        if not r.reliability:
            continue
        lines += [
            "",
            f"**{r.engine}** reliability",
            "",
            "| confidence bin | n | mean confidence | accuracy |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {row['bin']} | {row['n']} | {row['confidence']:.2f} | {row['accuracy']:.2f} |"
            for row in r.reliability
        ]
    return "\n".join(lines) + "\n"


def to_json(reports: Sequence[EngineReport]) -> str:
    return json.dumps([asdict(r) for r in reports], indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from snapjudge.engines import load

    parser = argparse.ArgumentParser(
        prog="snapjudge eval", description=__doc__.split("\n")[0]
    )
    parser.add_argument("cases", help="JSONL file of labelled cases")
    parser.add_argument(
        "--engines", required=True, help="comma-separated specs, e.g. jev,laya"
    )
    parser.add_argument("--json", action="store_true", help="JSON instead of Markdown")
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    reports = [evaluate(cases, load(spec)) for spec in args.engines.split(",")]
    print(to_json(reports) if args.json else to_markdown(reports), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
