"""The contract every engine adapter implements."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from snapjudge.types import Question

Context = str | Mapping[str, Any] | None


def plain_context(context: Context) -> str | dict[str, Any]:
    """Context as something json.dumps accepts: str, or a plain dict whose
    nested values fall back to str()."""
    if context is None:
        return ""
    if isinstance(context, str):
        return context
    return json.loads(json.dumps(dict(context), default=str))


@dataclass(frozen=True)
class RawAnswer:
    """What an engine returns for one question, before validation.

    `distribution` maps each option to a probability. snapjudge checks it
    against the question, so adapters pass through what the engine said
    rather than repairing it.
    """

    distribution: Mapping[str, float]
    cost_usd: float | None = None
    calibrated: bool = True
    # Set by composite engines (the cascade): which engine actually answered,
    # and every engine consulted, in order.
    engine: str | None = None
    hops: tuple[str, ...] = ()


@runtime_checkable
class Engine(Protocol):
    """An engine answers a batch of questions over one shared context.

    Engines that answer a batch in one pass (Jev) should do so; others may
    loop. Return one RawAnswer per question, in order.
    """

    name: str

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]: ...

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]: ...
