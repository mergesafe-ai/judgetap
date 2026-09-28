"""Ask a cheap engine first; escalate low-confidence answers to the next.

A Cascade is itself an Engine, so everything that takes an engine takes a
cascade. Each engine is asked only the questions still unresolved, in one
batch. An engine that errors or returns a malformed answer counts as not
answering, and the question moves on to the next engine.
"""

from __future__ import annotations

import asyncio
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from judgetap.api import validate_answer
from judgetap.engine import Call, Context, Engine, RawAnswer
from judgetap.errors import JudgetapError
from judgetap.types import Question

CONFIG_FILE = "judgetap.toml"
OLD_CONFIG_FILE = "snapjudge.toml"


class CascadeExhaustedError(JudgetapError):
    """No engine answered a question with enough confidence.

    `calls` lists the engine calls the cascade made before giving up."""

    def __init__(self, message: str, calls: tuple[Call, ...] = ()) -> None:
        super().__init__(message)
        self.calls = calls


@dataclass
class Attempt:
    engine: str
    answer: RawAnswer | None = None  # validated; None when the engine failed
    p: float | None = None
    error: Exception | None = None
    # For a nested cascade that gave up on this question: what it spent and
    # which engines it consulted, so the outer cascade keeps both.
    cost_usd: float | None = None
    hops: tuple[str, ...] = ()
    # False when the nested cascade paid for an answer whose price is unknown:
    # cost_usd is then None because the total is unknown, not because it is 0.
    cost_known: bool = True


@dataclass
class QuestionFailed:
    """A nested cascade's answer slot for a question it couldn't resolve.

    Only exchanged between cascades (via `decide_partial`): the rest of the
    batch keeps its answers instead of the whole batch failing."""

    error: Exception
    cost_usd: float | None = None
    hops: tuple[str, ...] = ()
    calls: tuple[Call, ...] = ()
    cost_known: bool = True  # see Attempt.cost_known


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
        return _raise_first_failure(self.decide_partial(questions, context))

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        return _raise_first_failure(await self.adecide_partial(questions, context))

    def decide_partial(
        self, questions: Sequence[Question], context: Context
    ) -> list[RawAnswer | QuestionFailed]:
        """Like decide, but a question no engine resolved comes back as a
        QuestionFailed instead of failing the whole batch."""
        attempts: list[list[Attempt]] = [[] for _ in questions]
        calls: list[Call] = []
        for engine in self.engines:
            pending = self._pending(questions, attempts)
            if not pending:
                break
            asked = [questions[i] for i in pending]
            start = time.perf_counter()
            try:
                if isinstance(engine, Cascade):
                    answers = engine.decide_partial(asked, context)
                else:
                    answers = engine.decide(asked, context)
            except Exception as err:  # noqa: BLE001 -- any engine failure falls through
                answers = err
            calls.extend(_calls_of(engine, answers, start, asked))
            self._record(engine, questions, pending, answers, attempts)
        return self._results(questions, attempts, tuple(calls))

    async def adecide_partial(
        self, questions: Sequence[Question], context: Context
    ) -> list[RawAnswer | QuestionFailed]:
        attempts: list[list[Attempt]] = [[] for _ in questions]
        calls: list[Call] = []
        for engine in self.engines:
            pending = self._pending(questions, attempts)
            if not pending:
                break
            asked = [questions[i] for i in pending]
            start = time.perf_counter()
            try:
                if isinstance(engine, Cascade):
                    answers = await engine.adecide_partial(asked, context)
                else:
                    answers = await engine.adecide(asked, context)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 -- any engine failure falls through
                answers = err
            calls.extend(_calls_of(engine, answers, start, asked))
            self._record(engine, questions, pending, answers, attempts)
        return self._results(questions, attempts, tuple(calls))

    def _results(
        self, questions, attempts, calls: tuple[Call, ...]
    ) -> list[RawAnswer | QuestionFailed]:
        """Finish every question; every answer, failure slot, or raised error
        carries the calls this batch made."""
        results: list[RawAnswer | QuestionFailed] = []
        for q, a in zip(questions, attempts, strict=True):
            try:
                results.append(self._finish(q, a))
            except CascadeExhaustedError as err:
                results.append(
                    QuestionFailed(
                        err,
                        cost_usd=_spent(a),
                        hops=_hops(a),
                        calls=calls,
                        cost_known=_cost_known(a),
                    )
                )
            except Exception as err:
                # An on_exhausted callback that raised or returned garbage:
                # the providers' calls travel with the error.
                _attach_calls(err, calls)
                raise
        out: list[RawAnswer | QuestionFailed] = []
        for r in results:
            if isinstance(r, QuestionFailed):
                _attach_calls(r.error, calls)
                out.append(r)
            else:
                out.append(replace(r, calls=calls))
        return out

    def _pending(self, questions, attempts) -> list[int]:
        return [i for i in range(len(questions)) if not self._confident(attempts[i])]

    def _confident(self, attempts: Sequence[Attempt], *, final: bool = False) -> bool:
        """Whether the latest answer stops the cascade. An uncalibrated p
        (calibrated=False) never stops it while another engine is left to
        ask; once none is (final), it is judged on p like any other."""
        last = attempts[-1] if attempts else None
        return (
            last is not None
            and last.p is not None
            and last.p >= self.escalate_below
            and (final or last.answer is None or last.answer.calibrated)
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
            if isinstance(raw, QuestionFailed):
                # A nested cascade gave up on this one question only.
                attempts[i].append(
                    Attempt(
                        engine.name,
                        error=raw.error,
                        cost_usd=raw.cost_usd,
                        hops=raw.hops,
                        cost_known=raw.cost_known,
                    )
                )
                continue
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
        spent = _spent(attempts)
        if self._confident(attempts, final=True):
            return _as_result(attempts[-1], hops, spent)
        if callable(self.on_exhausted):
            raw = RawAnswer(
                dict(self.on_exhausted(question, attempts)),
                cost_usd=spent,
                engine="fallback",
                hops=(*hops, "fallback"),
            )
            # Checked here so a bad callback result fails with the calls made.
            validate_answer(question, raw, "on_exhausted callback")
            return raw
        if self.on_exhausted == "return_last" and answered:
            return _as_result(answered[-1], hops, spent)
        errors = "; ".join(f"{a.engine}: {a.error or f'p={a.p:.2f}'}" for a in attempts)
        raise CascadeExhaustedError(
            f"no engine reached p>={self.escalate_below} for {question.text!r} ({errors})"
        )


def _calls_of(
    engine: Engine, answers, start: float, asked: list[Question]
) -> tuple[Call, ...]:
    """The calls one hop made: a nested composite's own calls when it reports
    them (answers or its exhaustion error), else the one call timed here."""
    inner = getattr(answers, "calls", None) if isinstance(answers, Exception) else None
    if inner is None and not isinstance(answers, Exception):
        try:
            inner = next((a.calls for a in answers if getattr(a, "calls", ())), None)
        except TypeError:
            inner = None  # a malformed, non-iterable result: timed below
    if inner:
        return tuple(inner)
    n = len(asked)
    elapsed = (time.perf_counter() - start) * 1000
    # ok means usable: the right number of answers and every one valid for
    # its question, the same check _record applies before using them.
    try:
        ok = not isinstance(answers, Exception) and len(answers) == n
        if ok:
            for q, raw in zip(asked, answers, strict=True):
                validate_answer(q, raw, engine.name)
    except Exception:  # noqa: BLE001 -- any malformed result is an unusable call
        ok = False
    return (Call(engine.name, elapsed, ok, n),)


def _hops(attempts: Sequence[Attempt]) -> tuple[str, ...]:
    """Every engine consulted, expanding a nested cascade's own path."""
    hops: list[str] = []
    for a in attempts:
        inner = a.answer.hops if a.answer is not None else a.hops
        hops.extend(inner or (a.engine,))
    return tuple(hops)


def _cost_known(attempts: Sequence[Attempt]) -> bool:
    """False if anything paid for on this question has an unknown price."""
    if any(not a.cost_known for a in attempts):
        return False
    return all(a.answer.cost_usd is not None for a in attempts if a.answer is not None)


def _spent(attempts: Sequence[Attempt]) -> float | None:
    """Everything paid for on a question, not just the winner (including what
    a nested cascade spent before giving up); None if any part is unknown."""
    if not _cost_known(attempts):
        return None  # part of the spend is unknown, including inside a nested cascade
    costs = [a.answer.cost_usd for a in attempts if a.answer is not None]
    # A nested cascade's spend on a question it gave up on; when it paid for
    # nothing (every engine errored) there is nothing to add.
    costs += [
        a.cost_usd for a in attempts if a.answer is None and a.cost_usd is not None
    ]
    return sum(costs) if costs and None not in costs else None


def _attach_calls(err: Exception, calls: tuple[Call, ...]) -> None:
    try:
        err.calls = calls
    except AttributeError:
        pass  # an exception type that refuses attributes


def _raise_first_failure(results: list[RawAnswer | QuestionFailed]) -> list[RawAnswer]:
    """A top-level cascade keeps its contract: any unresolved question raises."""
    for r in results:
        if isinstance(r, QuestionFailed):
            raise r.error
    return results


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

    if (
        path is None
        and not Path(CONFIG_FILE).exists()
        and Path(OLD_CONFIG_FILE).exists()
    ):
        path = OLD_CONFIG_FILE  # the pre-rename name, read for one release
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
