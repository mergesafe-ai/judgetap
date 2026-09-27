"""Ask a cheap engine first; escalate low-confidence answers to the next.

A Cascade is itself an Engine, so everything that takes an engine takes a
cascade. Each engine is asked only the questions still unresolved, in one
batch. An engine that errors or returns a malformed answer counts as not
answering, and the question moves on to the next engine.
"""

from __future__ import annotations

import asyncio
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from judgetap.api import validate_answer
from judgetap.engine import Context, Engine, RawAnswer
from judgetap.errors import JudgetapError
from judgetap.types import Question

CONFIG_FILE = "judgetap.toml"


class CascadeExhaustedError(JudgetapError):
    """No engine answered a question with enough confidence."""


@dataclass
class Attempt:
    engine: str
    answer: RawAnswer | None = None  # validated; None when the engine failed
    p: float | None = None
    error: Exception | None = None


Fallback = Callable[[Question, Sequence[Attempt]], Mapping[str, float]]
OnExhausted = Literal["raise", "return_last"] | Fallback


@dataclass
class Cascade:
    engines: Sequence[Engine]
    escalate_below: float = 0.8
    on_exhausted: OnExhausted = "raise"
    name: str = field(default="cascade")

    def __post_init__(self) -> None:
        if not self.engines:
            raise JudgetapError("a cascade needs at least one engine")
        if not 0 <= self.escalate_below <= 1:
            raise JudgetapError("escalate_below must be between 0 and 1")

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        attempts: list[list[Attempt]] = [[] for _ in questions]
        for engine in self.engines:
            pending = self._pending(questions, attempts)
            if not pending:
                break
            try:
                answers = engine.decide([questions[i] for i in pending], context)
            except Exception as err:  # noqa: BLE001 -- any engine failure falls through
                answers = err
            self._record(engine, questions, pending, answers, attempts)
        return [self._finish(q, a) for q, a in zip(questions, attempts, strict=True)]

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        attempts: list[list[Attempt]] = [[] for _ in questions]
        for engine in self.engines:
            pending = self._pending(questions, attempts)
            if not pending:
                break
            try:
                answers = await engine.adecide([questions[i] for i in pending], context)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 -- any engine failure falls through
                answers = err
            self._record(engine, questions, pending, answers, attempts)
        return [self._finish(q, a) for q, a in zip(questions, attempts, strict=True)]

    def _pending(self, questions, attempts) -> list[int]:
        return [i for i in range(len(questions)) if not self._confident(attempts[i])]

    def _confident(self, attempts: Sequence[Attempt]) -> bool:
        return (
            bool(attempts)
            and attempts[-1].p is not None
            and (attempts[-1].p >= self.escalate_below)
        )

    def _record(self, engine, questions, pending, answers, attempts) -> None:
        try:
            if isinstance(answers, Exception):
                raise answers
            answers = list(answers)
            if len(answers) != len(pending):
                raise JudgetapError(
                    f"{engine.name} returned {len(answers)} answers for {len(pending)} questions"
                )
        except Exception as err:  # noqa: BLE001 -- a malformed batch counts as no answer
            for i in pending:
                attempts[i].append(Attempt(engine.name, error=err))
            return
        for i, raw in zip(pending, answers, strict=True):
            try:
                dist = validate_answer(questions[i], raw, engine.name)
            except Exception as err:  # noqa: BLE001 -- including TypeError from a bad RawAnswer
                attempts[i].append(Attempt(engine.name, error=err))
                continue
            clean = replace(raw, distribution=dist)
            attempts[i].append(Attempt(engine.name, answer=clean, p=max(dist.values())))

    def _finish(self, question: Question, attempts: Sequence[Attempt]) -> RawAnswer:
        hops = _hops(attempts)
        answered = [a for a in attempts if a.answer is not None]
        costs = [a.answer.cost_usd for a in answered]
        # Everything paid for, not just the winner; unknown if any part is unknown.
        spent = sum(costs) if costs and None not in costs else None
        if self._confident(attempts):
            return _as_result(attempts[-1], hops, spent)
        if callable(self.on_exhausted):
            dist = self.on_exhausted(question, attempts)
            return RawAnswer(
                dict(dist), cost_usd=spent, engine="fallback", hops=(*hops, "fallback")
            )
        if self.on_exhausted == "return_last" and answered:
            return _as_result(answered[-1], hops, spent)
        errors = "; ".join(f"{a.engine}: {a.error or f'p={a.p:.2f}'}" for a in attempts)
        raise CascadeExhaustedError(
            f"no engine reached p>={self.escalate_below} for {question.text!r} ({errors})"
        )


def _hops(attempts: Sequence[Attempt]) -> tuple[str, ...]:
    """Every engine consulted, expanding a nested cascade's own path."""
    hops: list[str] = []
    for a in attempts:
        inner = a.answer.hops if a.answer is not None else ()
        hops.extend(inner or (a.engine,))
    return tuple(hops)


def _as_result(
    attempt: Attempt, hops: tuple[str, ...], spent: float | None
) -> RawAnswer:
    # A nested cascade already names the engine that really answered.
    engine = attempt.answer.engine or attempt.engine
    return replace(attempt.answer, engine=engine, hops=hops, cost_usd=spent)


def from_config(path: str | Path | None = None) -> Cascade:
    """Build a cascade from the [cascade] table of judgetap.toml.

    [cascade]
    order = ["jev", "llm:gemini/gemini-2.0-flash-lite"]
    escalate_below = 0.8
    on_exhausted = "raise"        # or "return_last"

    A callback for on_exhausted can't be written in TOML; set it in code
    with `Cascade(..., on_exhausted=fn)` or `replace(from_config(), on_exhausted=fn)`.
    """
    from judgetap.engines import load

    path = Path(path or CONFIG_FILE)
    with path.open("rb") as fh:
        table = tomllib.load(fh).get("cascade")
    if not isinstance(table, dict) or not table.get("order"):
        raise JudgetapError(f"{path} has no [cascade] table with an order list")
    on_exhausted = table.get("on_exhausted", "raise")
    if on_exhausted not in ("raise", "return_last"):
        raise JudgetapError(
            "on_exhausted in a config file must be 'raise' or 'return_last'"
        )
    order, threshold = table["order"], table.get("escalate_below", 0.8)
    if not isinstance(order, list) or not all(isinstance(s, str) for s in order):
        raise JudgetapError(f"{path}: cascade.order must be a list of engine specs")
    if isinstance(threshold, bool) or not isinstance(threshold, int | float):
        raise JudgetapError(
            f"{path}: cascade.escalate_below must be a number, got {threshold!r}"
        )
    return Cascade(
        engines=[load(spec) for spec in order],
        escalate_below=float(threshold),
        on_exhausted=on_exhausted,
    )
