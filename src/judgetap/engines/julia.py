"""Julia-1 (Supersonic Labs, Apache-2.0): a 144M-parameter decision model
that runs in process on CPU (~33 ms a decision on an M4).

Julia takes Jev's question shapes (huggingface.co/SupersonicLabs/Julia-1), so
the request is built with the Jev adapter's helpers. Its runtime ships in the
model repo rather than on PyPI:

    python -c "from huggingface_hub import snapshot_download; \\
        import os; snapshot_download('SupersonicLabs/Julia-1', \\
        local_dir=os.path.expanduser('~/.judgetap/models/Julia-1'))"
    pip install -e ~/.judgetap/models/Julia-1

Spec `julia` loads the checkpoint at $JUDGETAP_JULIA_PATH (default
`~/.judgetap/models/Julia-1`); `julia:<path>` names it. A relative path is
taken from ~/.judgetap/models, never the working directory, and may not
escape it (`../x` is rejected): the guard runs inside untrusted repos, which
must not be able to supply the model that judges them. $JUDGETAP_JULIA_DEVICE picks the device (default `cpu`).

Loaded models are cached per process (path, device), at most
MAX_CACHED_MODELS of them (least recently used evicted). Every call goes
through that cache, so a model is loaded again only after it was evicted. The guard hook is a new process per action, so an
in-process model still loads on every guarded action there: for the guard,
prefer a server engine (AgentJev, or a TypeSafe-compatible URL).
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from judgetap.engine import Context, RawAnswer, plain_context
from judgetap.engines.jev import _distribution, _question_payload
from judgetap.errors import JudgetapError
from judgetap.types import NO, YES, Question

DEFAULT_PATH = "Julia-1"
MAX_CACHED_MODELS = 2
_models: dict[tuple[str, str], Any] = {}  # insertion order = LRU order
_models_lock = threading.Lock()


def _models_dir() -> Path:
    from judgetap.guard.hook import home

    return home() / "models"


def resolve_path(path: str) -> str:
    """Absolute paths as given; anything else under ~/.judgetap/models.

    A relative path that resolves outside the models directory (``../x``,
    or a symlink out of it) is rejected.
    """
    p = Path(path).expanduser()
    if p.is_absolute():
        return str(p)
    base = _models_dir()
    if not (base / p).resolve().is_relative_to(base.resolve()):
        raise ValueError(
            f"julia model path {path!r} escapes {base}; "
            "use an absolute path for a model outside it"
        )
    return str(base / p)


# Julia's native limits: 2-20 options per question.
MIN_OPTIONS, MAX_OPTIONS = 2, 20


class JuliaError(JudgetapError):
    """Julia is missing, failed, or can't take this question."""


def _answer_distribution(q: Question, answer: Mapping[str, Any]) -> dict[str, float]:
    if q.kind == "yesno":
        # Julia reports false/true probabilities; `noul` is P(true).
        probs = answer.get("probabilities")
        if isinstance(probs, Mapping) and "true" in probs and "false" in probs:
            return {YES: probs["true"], NO: probs["false"]}
    return _distribution(q, dict(answer))


class JuliaEngine:
    def __init__(
        self,
        path: str | None = None,
        *,
        device: str | None = None,
        runtime: Any = None,
    ) -> None:
        self.name = "julia"
        self.path = resolve_path(
            path or os.environ.get("JUDGETAP_JULIA_PATH") or DEFAULT_PATH
        )
        self.device = device or os.environ.get("JUDGETAP_JULIA_DEVICE") or "cpu"
        self._runtime = runtime  # an injected model; otherwise the shared cache

    def _get_runtime(self) -> Any:
        if self._runtime is not None:
            return self._runtime
        key = (self.path, self.device)
        # Concurrent first calls load once; the model is not pinned on the
        # instance, so each call refreshes the LRU and eviction frees it.
        with _models_lock:
            if key in _models:
                _models[key] = _models.pop(key)  # mark most recently used
            else:
                try:
                    from julia import load_model
                except ImportError as err:
                    raise JuliaError(
                        "the julia engine needs the Julia-1 runtime: download "
                        "SupersonicLabs/Julia-1 from Hugging Face into "
                        "~/.judgetap/models/Julia-1 and `pip install -e` it"
                    ) from err
                _models[key] = load_model(
                    self.path,
                    device=self.device,
                    strict_encoding=True,
                    max_length=8192,
                    head_length=512,
                )
                while len(_models) > MAX_CACHED_MODELS:
                    del _models[next(iter(_models))]
            return _models[key]

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        for q in questions:
            if not MIN_OPTIONS <= len(q.options) <= MAX_OPTIONS:
                # Raised before loading, so a cascade falls through cheaply.
                raise JuliaError(
                    f"Julia takes {MIN_OPTIONS}-{MAX_OPTIONS} options per question; "
                    f"{q.text!r} has {len(q.options)}"
                )
        ids = [f"q{i}" for i in range(len(questions))]
        payload = {i: _question_payload(q) for i, q in zip(ids, questions, strict=True)}
        result = self._get_runtime().predict(
            state=plain_context(context), questions=payload
        )
        try:
            answers = result["answers"]
            # Local inference: no per-call price.
            return [
                RawAnswer(_answer_distribution(q, answers[i]), cost_usd=0.0)
                for i, q in zip(ids, questions, strict=True)
            ]
        except (KeyError, TypeError, ValueError, IndexError) as err:
            raise JuliaError(f"unexpected Julia result shape: {err!r}") from err

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        return await asyncio.to_thread(self.decide, questions, context)
