"""Bundled engine adapters, addressed by a short spec string.

load("jev")                       -> TypeSafe Jev (TYPESAFE_API_KEY)
load("jev:jev-1.13.0")            -> Jev pinned to a model version
load("llm:openai/gpt-4o-mini")    -> any LiteLLM model (pip install snapjudge[llm])
"""

from __future__ import annotations

import os

from snapjudge.engine import Engine
from snapjudge.errors import SnapjudgeError

ENV_VAR = "SNAPJUDGE_ENGINE"


def load(spec: str | None = None) -> Engine:
    """Build an engine from a spec string, or from $SNAPJUDGE_ENGINE."""
    spec = spec or os.environ.get(ENV_VAR)
    if not spec:
        raise SnapjudgeError(f"no engine spec given and ${ENV_VAR} is not set")
    name, _, arg = spec.partition(":")
    if name == "jev":
        from snapjudge.engines.jev import JevEngine

        return JevEngine(model=arg or "jev-latest")
    if name == "llm":
        if not arg:
            raise SnapjudgeError(
                "llm engine needs a model, e.g. llm:openai/gpt-4o-mini"
            )
        from snapjudge.engines.llm import LLMEngine

        return LLMEngine(model=arg)
    if name == "laya":
        from snapjudge.engines.laya import DEFAULT_MODEL, LayaEngine

        return LayaEngine(model=arg or DEFAULT_MODEL)
    if name == "agentjev":
        from snapjudge.engines.agentjev import DEFAULT_URL, AgentJevEngine

        return AgentJevEngine(url=arg or DEFAULT_URL)
    raise SnapjudgeError(
        f"unknown engine {name!r} in spec {spec!r}; known: jev, llm, laya, agentjev"
    )


__all__ = ["ENV_VAR", "load"]
