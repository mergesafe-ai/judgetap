"""Laya, the open-weights (Apache-2.0) ModernBERT decision model, in process.

Laya takes Jev's question shapes (github.com/NandhaKishorM/laya), so the
request is built with the Jev adapter's helpers. Needs `pip install
'judgetap[laya]'`; the checkpoint downloads from Hugging Face on first use.

Laya depends on PyTorch, and on Linux pip picks the CUDA build by default
(several GB). On a CPU-only machine, install the CPU wheel first:

    pip install torch --index-url https://download.pytorch.org/whl/cpu
    pip install 'judgetap[laya]'
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from judgetap.engine import Context, RawAnswer
from judgetap.engines.jev import _distribution, _question_payload
from judgetap.errors import JudgetapError
from judgetap.types import Question

DEFAULT_MODEL = "typed-decisions"


class LayaError(JudgetapError):
    """Laya is missing, failed, or answered in an unexpected shape."""


def _state(context: Context) -> Any:
    if context is None:
        return ""
    if isinstance(context, Mapping):
        return json.loads(json.dumps(dict(context), default=str))
    return context


class LayaEngine:
    def __init__(self, model: str = DEFAULT_MODEL, *, router: Any = None) -> None:
        self.name = "laya"
        self.model = model
        self._router = router

    def _get_router(self) -> Any:
        if self._router is None:
            try:
                from laya import Router
            except ImportError as err:
                raise LayaError(
                    "the laya engine needs Laya: pip install 'judgetap[laya]'"
                ) from err
            self._router = Router()
        return self._router

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        ids = [f"q{i}" for i in range(len(questions))]
        payload = {i: _question_payload(q) for i, q in zip(ids, questions, strict=True)}
        result = self._get_router().predict(_state(context), payload, model=self.model)
        try:
            answers = result["answers"]
            # Local inference: no per-call price.
            return [
                RawAnswer(_distribution(q, answers[i]), cost_usd=0.0)
                for i, q in zip(ids, questions, strict=True)
            ]
        except (KeyError, TypeError, ValueError, IndexError) as err:
            raise LayaError(f"unexpected Laya result shape: {err!r}") from err

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        return await asyncio.to_thread(self.decide, questions, context)
