"""Bundled engine adapters, addressed by a short spec string.

load("jev")                       -> TypeSafe Jev (TYPESAFE_API_KEY)
load("jev:jev-1.13.0")            -> Jev pinned to a model version
load("llm:openai/gpt-4o-mini")    -> any LiteLLM model (pip install judgetap[llm])
"""

from __future__ import annotations

from judgetap._compat import env
from judgetap.engine import Engine
from judgetap.errors import JudgetapError

ENV_VAR = "JUDGETAP_ENGINE"  # SNAPJUDGE_ENGINE still read for one release


def load(spec: str | None = None) -> Engine:
    """Build an engine from a spec string, or from $JUDGETAP_ENGINE."""
    spec = spec or env("ENGINE")
    if not spec:
        raise JudgetapError(f"no engine spec given and ${ENV_VAR} is not set")
    if spec.startswith("jev@"):
        from judgetap.engines.jev import JevEngine

        return JevEngine(base_url=spec[len("jev@") :])
    name, _, arg = spec.partition(":")
    if name == "typesafe":
        if not arg:
            raise JudgetapError(
                "typesafe engine needs a URL, e.g. typesafe:http://127.0.0.1:8000"
            )
        from judgetap.engines.jev import JevEngine

        return JevEngine(base_url=arg)
    if name == "jev":
        from judgetap.engines.jev import JevEngine

        return JevEngine(model=arg or "jev-latest")
    if name == "llm":
        if not arg:
            raise JudgetapError("llm engine needs a model, e.g. llm:openai/gpt-4o-mini")
        from judgetap.engines.llm import LLMEngine

        return LLMEngine(model=arg)
    if name == "laya":
        from judgetap.engines.laya import DEFAULT_MODEL, LayaEngine

        return LayaEngine(model=arg or DEFAULT_MODEL)
    if name == "agentjev":
        from judgetap.engines.agentjev import DEFAULT_URL, AgentJevEngine

        return AgentJevEngine(url=arg or DEFAULT_URL)
    raise JudgetapError(
        f"unknown engine {name!r} in spec {spec!r}; known: jev, jev@<url>, typesafe:<url>, llm, laya, agentjev"
    )


__all__ = ["ENV_VAR", "load"]
