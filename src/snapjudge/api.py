"""Public decision API: choice, score, yesno and batch, sync and async."""

from __future__ import annotations

import math
import time
from collections.abc import Sequence

from snapjudge.engine import Context, Engine, RawAnswer
from snapjudge.errors import InvalidAnswerError, NoEngineError
from snapjudge.types import Decision, Question

# Probabilities within this distance of summing to 1 are renormalised;
# anything further off is an engine bug and is rejected.
SUM_TOLERANCE = 1e-3

_default_engine: Engine | None = None


def configure(engine: Engine | None) -> None:
    """Set the process-wide default engine; None clears it.

    A call's own `engine=` argument always takes precedence. Engine adapters
    and file/env configuration arrive with #2; until then an engine is any
    object implementing `snapjudge.Engine`.
    """
    global _default_engine
    _default_engine = engine


def _resolve(engine: Engine | None) -> Engine:
    chosen = engine if engine is not None else _default_engine
    if chosen is None:
        raise NoEngineError(
            "no engine configured: call snapjudge.configure(engine) "
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
            )
        )
    return out


def batch(
    questions: Sequence[Question],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> list[Decision]:
    """Answer several questions over one context, in one engine call."""
    if not questions:
        return []
    chosen = _resolve(engine)
    start = time.perf_counter()
    answers = chosen.decide(questions, context)
    latency_ms = (time.perf_counter() - start) * 1000
    return _decisions(questions, answers, chosen, latency_ms)


async def abatch(
    questions: Sequence[Question],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> list[Decision]:
    if not questions:
        return []
    chosen = _resolve(engine)
    start = time.perf_counter()
    answers = await chosen.adecide(questions, context)
    latency_ms = (time.perf_counter() - start) * 1000
    return _decisions(questions, answers, chosen, latency_ms)


def choice(
    text: str,
    options: Sequence[str],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> Decision:
    return batch([Question.choice(text, tuple(options))], context, engine=engine)[0]


def score(
    text: str,
    levels: Sequence[str],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> Decision:
    return batch([Question.score(text, tuple(levels))], context, engine=engine)[0]


def yesno(
    text: str, context: Context = None, *, engine: Engine | None = None
) -> Decision:
    return batch([Question.yesno(text)], context, engine=engine)[0]


async def achoice(
    text: str,
    options: Sequence[str],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> Decision:
    return (
        await abatch([Question.choice(text, tuple(options))], context, engine=engine)
    )[0]


async def ascore(
    text: str,
    levels: Sequence[str],
    context: Context = None,
    *,
    engine: Engine | None = None,
) -> Decision:
    return (
        await abatch([Question.score(text, tuple(levels))], context, engine=engine)
    )[0]


async def ayesno(
    text: str, context: Context = None, *, engine: Engine | None = None
) -> Decision:
    return (await abatch([Question.yesno(text)], context, engine=engine))[0]
