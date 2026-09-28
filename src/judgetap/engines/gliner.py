"""GLiNER2.5-Decide (Fastino, Apache-2.0) in process, through `gliner2`.

A 340M DeBERTa encoder that picks labels for several questions ("heads") in
one forward pass, on CPU or GPU (huggingface.co/fastino/GLiNER2.5-Decide).
Needs `pip install 'judgetap[gliner]'`; the checkpoint downloads from
Hugging Face on first use.

What comes back depends on the gliner2 code path. Only a full `probabilities`
map covering every option is marked `calibrated`; anything less is
`calibrated=False`, so a cascade escalates it to its next engine whatever
its p:
- a full `probabilities` map per head (the classifier path): renormalised
  over the options, calibrated;
- a map missing some options: renormalised over the ones present, uncalibrated;
- `{"label", "confidence"}` (the extractor path): the winner gets its score
  and the other labels split the rest evenly, uncalibrated (gliner2 doesn't
  report them, and the score isn't a calibrated probability);
- a bare label (older versions): p=1.0, uncalibrated.

Loaded models are cached per process (by model id, at most
MAX_CACHED_MODELS). The guard refuses this engine: its hook is a new process
per action, so the model would load on every guarded action.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from judgetap.engine import Context, RawAnswer, plain_context
from judgetap.errors import JudgetapError
from judgetap.types import NO, YES, Question

DEFAULT_MODEL = "fastino/GLiNER2.5-Decide"
MAX_CACHED_MODELS = 2
_models: dict[str, Any] = {}
_models_lock = threading.Lock()


class GlinerError(JudgetapError):
    """gliner2 is missing, failed to load or classify, or answered in an
    unexpected shape. The underlying exception is chained."""


def _task(q: Question) -> dict[str, Any]:
    """One classification head: its labels, with the question as the prompt."""
    labels = [YES, NO] if q.kind == "yesno" else list(q.options)
    return {"labels": labels, "prompt": q.text}


def _text(context: Context) -> str:
    state = plain_context(context)
    return state if isinstance(state, str) else json.dumps(state)


def _distribution(q: Question, answer: Any) -> tuple[dict[str, float], bool]:
    """(distribution over the question's options, calibrated)."""
    options = list(q.options)
    if isinstance(answer, Mapping) and isinstance(answer.get("probabilities"), Mapping):
        probs = {str(k): float(v) for k, v in answer["probabilities"].items()}
        total = sum(probs.get(o, 0.0) for o in options)
        if total > 0:
            complete = all(o in probs for o in options)
            return {o: probs.get(o, 0.0) / total for o in options}, complete
    if isinstance(answer, Mapping) and "label" in answer:
        label, p = str(answer["label"]), float(answer.get("confidence", 1.0))
        rest = (1.0 - p) / (len(options) - 1) if len(options) > 1 else 0.0
        return {o: (p if o == label else rest) for o in options}, False
    if isinstance(answer, Mapping) and "value" in answer:
        answer = answer["value"]
    label = str(answer)
    return {o: (1.0 if o == label else 0.0) for o in options}, False


class GlinerEngine:
    def __init__(self, model: str = DEFAULT_MODEL, *, extractor: Any = None) -> None:
        self.name = "gliner"
        self.model = model
        self._extractor = extractor

    def _get(self) -> Any:
        if self._extractor is not None:
            return self._extractor
        # One load per process and model, even with concurrent first calls.
        with _models_lock:
            if self.model not in _models:
                try:
                    from gliner2 import AutoExtractor
                except ImportError as err:
                    raise GlinerError(
                        "the gliner engine needs gliner2: pip install 'judgetap[gliner]'"
                    ) from err
                try:
                    _models[self.model] = AutoExtractor.from_pretrained(self.model)
                except Exception as err:
                    raise GlinerError(f"could not load {self.model}: {err}") from err
                while len(_models) > MAX_CACHED_MODELS:
                    del _models[next(iter(_models))]  # oldest load first
            else:
                _models[self.model] = _models.pop(self.model)  # most recently used
            self._extractor = _models[self.model]
        return self._extractor

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        ids = [f"q{i}" for i in range(len(questions))]
        tasks = {i: _task(q) for i, q in zip(ids, questions, strict=True)}
        extractor, text = self._get(), _text(context)
        try:
            try:
                result = extractor.classify_text(text, tasks, include_confidence=True)
            except TypeError:
                result = extractor.classify_text(text, tasks)  # no confidence support
        except Exception as err:
            raise GlinerError(f"gliner2 classification failed: {err}") from err
        try:
            answers = []
            for i, q in zip(ids, questions, strict=True):
                dist, calibrated = _distribution(q, result[i])
                answers.append(RawAnswer(dist, cost_usd=0.0, calibrated=calibrated))
            return answers
        except (KeyError, TypeError, ValueError) as err:
            raise GlinerError(f"unexpected gliner2 result shape: {err!r}") from err

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        return await asyncio.to_thread(self.decide, questions, context)
