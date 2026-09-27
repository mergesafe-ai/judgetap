"""Public decision API: choice, score, yesno and batch, sync and async."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Sequence

from judgetap import decision_log
from judgetap.engine import Call, Context, Engine, RawAnswer
from judgetap.errors import InvalidAnswerError, NoEngineError
from judgetap.types import Decision, Question

# Probabilities within this distance of summing to 1 are renormalised;
# anything further off is an engine bug and is rejected.
SUM_TOLERANCE = 1e-3

_default_engine: Engine | None = None
_log = False


def configure(engine: Engine | None, *, log: bool = False) -> None:
    """Set the process-wide default engine; None clears it.

    A call's own `engine=` argument always takes precedence. With `log=True`
    (or JUDGETAP_LOG=1) each decision is appended to the local decision log
    that `judgetap dashboard` reads; the context is never logged.
    """
    global _default_engine, _log
    _default_engine = engine
    _log = log


def _finish(decisions: list[Decision], log: bool) -> list[Decision]:
    if log and decision_log.enabled(_log):
        decision_log.record(decisions)
    return decisions


async def _afinish(decisions: list[Decision], log: bool) -> list[Decision]:
    # File writes stay off the event loop.
    if log and decision_log.enabled(_log):
        try:
            await asyncio.to_thread(decision_log.record, decisions)
        except RuntimeError:
            pass  # executor gone (shutdown): logging must never fail a decision
    return decisions


def _resolve(engine: Engine | None) -> Engine:
    chosen = engine if engine is not None else _default_engine
    if chosen is None:
        raise NoEngineError(
            "no engine configured: call judgetap.configure(engine) "
            "or pass engine= to the call"
        )
    return chosen


def validate_answer(
    question: Question, raw: RawAnswer, engine: str
) -> dict[str, float]:
    dist = dict(raw.distribution)
    if set(dist) != set(question.options):
        raise InvalidAnswerError(
            f"{engine} answered with {sorted(dist)!r}, "
            f"expected exactly {list(question.options)!r}"
        )
    for option, p in dist.items():
        # Range first: math.isfinite overflows on huge ints.
        if not (isinstance(p, int | float) and 0 <= p <= 1 and math.isfinite(p)):
            raise InvalidAnswerError(f"{engine} gave {option!r} probability {p!r}")
    total = sum(dist.values())
    if abs(total - 1) > SUM_TOLERANCE:
        raise InvalidAnswerError(f"{engine} probabilities sum to {total}, not 1")
    # Keep the question's option order so ties resolve predictably.
    return {o: dist[o] / total for o in question.options}


def _calls(answers: Sequence[RawAnswer], engine: Engine, latency_ms: float, n: int):
    """The engine calls behind a batch: what a composite engine reported, or
    the one call the core just timed."""
    for raw in answers:
        if getattr(raw, "calls", ()):
            return tuple(raw.calls)
    return (Call(engine.name, latency_ms, True, n),)


def _tag_failure(err: Exception, engine: Engine, latency_ms: float, n: int) -> None:
    """Attach the failed call to the exception unless an inner engine did."""
    if not getattr(err, "calls", None):
        try:
            err.calls = (Call(engine.name, latency_ms, False, n),)
        except AttributeError:
            pass  # exceptions without a __dict__: no call record


def _checked(
    questions: Sequence[Question],
    answers: Sequence[RawAnswer],
    engine: Engine,
    latency_ms: float,
) -> list[Decision]:
    """_decisions, but an answer that fails validation after the engine
    returned still carries the calls that were made (marked not ok)."""
    try:
        return _decisions(questions, answers, engine, latency_ms)
    except Exception as err:
        try:
            reported = next(
                (tuple(r.calls) for r in answers if getattr(r, "calls", ())), ()
            )
        except TypeError:
            reported = ()
        if reported and not getattr(err, "calls", None):
            try:
                err.calls = reported  # a composite's own calls, as it reported them
            except AttributeError:
                pass
        else:
            _tag_failure(err, engine, latency_ms, len(questions))
        raise


def _decisions(
    questions: Sequence[Question],
    answers: Sequence[RawAnswer],
    engine: Engine,
    latency_ms: float,
) -> list[Decision]:
    if len(answers) != len(questions):
        raise InvalidAnswerError(
            f"{engine.name} returned {len(answers)} answers "
            f"for {len(questions)} questions"
        )
    calls = _calls(answers, engine, latency_ms, len(questions))
    out = []
    for question, raw in zip(questions, answers, strict=True):
        dist = validate_answer(question, raw, engine.name)
        value = max(dist, key=dist.__getitem__)  # first option wins a tie
        out.append(
            Decision(
                question=question,
                value=value,
                p=dist[value],
                distribution=dist,
                engine=raw.engine or engine.name,
                latency_ms=latency_ms,
                cost_usd=raw.cost_usd,
                escalated=len(raw.hops) > 1,
                calibrated=raw.calibrated,
                meta={"hops": list(raw.hops)} if raw.hops else {},
                calls=calls,
            )
        )
    return out


def batch(
    questions: Sequence[Question],
    context: Context = None,
    *,
    engine: Engine | None = None,
    log: bool = True,
) -> list[Decision]:
    """Answer several questions over one context, in one engine call.

    `log=False` keeps the call out of the decision log (the guard uses it
    for its own judge questions, which it logs as one guard record)."""
    if not questions:
        return []
    chosen = _resolve(engine)
    start = time.perf_counter()
    try:
        answers = chosen.decide(questions, context)
    except Exception as err:
        _tag_failure(err, chosen, (time.perf_counter() - start) * 1000, len(questions))
        raise
    latency_ms = (time.perf_counter() - start) * 1000
    return _finish(_checked(questions, answers, chosen, latency_ms), log)


async def abatch(
    questions: Sequence[Question],
    context: Context = None,
    *,
    engine: Engine | None = None,
    log: bool = True,
) -> list[Decision]:
    if not questions:
        return []
    chosen = _resolve(engine)
    start = time.perf_counter()
    try:
        answers = await chosen.adecide(questions, context)
    except Exception as err:
        _tag_failure(err, chosen, (time.perf_counter() - start) * 1000, len(questions))
        raise
    latency_ms = (time.perf_counter() - start) * 1000
    return await _afinish(_checked(questions, answers, chosen, latency_ms), log)


def choice(
    text: str,
    options: list[str] | tuple[str, ...],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> Decision:
    return batch([Question.choice(text, options)], context, engine=engine)[0]


def score(
    text: str,
    levels: list[str] | tuple[str, ...],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> Decision:
    return batch([Question.score(text, levels)], context, engine=engine)[0]


def yesno(
    text: str, context: Context = None, *, engine: Engine | None = None
) -> Decision:
    return batch([Question.yesno(text)], context, engine=engine)[0]


async def achoice(
    text: str,
    options: list[str] | tuple[str, ...],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> Decision:
    return (await abatch([Question.choice(text, options)], context, engine=engine))[0]


async def ascore(
    text: str,
    levels: list[str] | tuple[str, ...],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> Decision:
    return (await abatch([Question.score(text, levels)], context, engine=engine))[0]


async def ayesno(
    text: str, context: Context = None, *, engine: Engine | None = None
) -> Decision:
    return (await abatch([Question.yesno(text)], context, engine=engine))[0]
