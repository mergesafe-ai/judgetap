"""A scripted engine for tests, yours and ours."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from judgetap.engine import Context, RawAnswer
from judgetap.types import Question

Script = Callable[[Question, Context], Mapping[str, float]]


class StaticEngine:
    """Answers every question from a function, with no model behind it.

    >>> engine = StaticEngine(lambda q, ctx: {o: 1 / len(q.options) for o in q.options})
    """

    def __init__(self, script: Script, name: str = "static") -> None:
        self.name = name
        self._script = script
        self.calls: list[tuple[tuple[Question, ...], Context]] = []

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        self.calls.append((tuple(questions), context))
        return [RawAnswer(dict(self._script(q, context))) for q in questions]

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        return self.decide(questions, context)
