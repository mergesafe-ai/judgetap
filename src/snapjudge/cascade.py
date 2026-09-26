"""Ask a cheap engine first; escalate low-confidence answers to the next.

A Cascade is itself an Engine, so everything that takes an engine takes a
cascade. Each engine is asked only the questions still unresolved, in one
batch. An engine that errors or returns a malformed answer counts as not
answering, and the question moves on to the next engine.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from snapjudge.api import validate_answer
from snapjudge.engine import Context, Engine, RawAnswer
from snapjudge.errors import SnapjudgeError
from snapjudge.types import Question


class CascadeExhaustedError(SnapjudgeError):
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
            raise SnapjudgeError("a cascade needs at least one engine")
        if not 0 <= self.escalate_below <= 1:
            raise SnapjudgeError("escalate_below must be between 0 and 1")

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
        if isinstance(answers, Exception) or len(answers) != len(pending):
            err = (
                answers
                if isinstance(answers, Exception)
                else SnapjudgeError(
                    f"{engine.name} returned {len(answers)} answers for {len(pending)} questions"
                )
            )
            for i in pending:
                attempts[i].append(Attempt(engine.name, error=err))
            return
        for i, raw in zip(pending, answers, strict=True):
            try:
                dist = validate_answer(questions[i], raw, engine.name)
            except SnapjudgeError as err:
                attempts[i].append(Attempt(engine.name, error=err))
                continue
            clean = replace(raw, distribution=dist)
            attempts[i].append(Attempt(engine.name, answer=clean, p=max(dist.values())))

    def _finish(self, question: Question, attempts: Sequence[Attempt]) -> RawAnswer:
        hops = tuple(a.engine for a in attempts)
        answered = [a for a in attempts if a.answer is not None]
        if self._confident(attempts):
            return replace(attempts[-1].answer, engine=attempts[-1].engine, hops=hops)
        if callable(self.on_exhausted):
            dist = self.on_exhausted(question, attempts)
            return RawAnswer(dict(dist), engine="fallback", hops=(*hops, "fallback"))
        if self.on_exhausted == "return_last" and answered:
            last = answered[-1]
            return replace(last.answer, engine=last.engine, hops=hops)
        errors = "; ".join(f"{a.engine}: {a.error or f'p={a.p:.2f}'}" for a in attempts)
        raise CascadeExhaustedError(
            f"no engine reached p>={self.escalate_below} for {question.text!r} ({errors})"
        )
