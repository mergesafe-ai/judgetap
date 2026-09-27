"""AgentJev-0.6B through its local server (github.com/malevrigns/agent-jev).

Start the server with `python -m jev_service.server ... --port 8149`; this
adapter posts to its `/api/evaluate` endpoint. Stdlib only.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from judgetap.engine import Context, RawAnswer
from judgetap.engines.jev import Transport, _urllib_transport
from judgetap.errors import JudgetapError
from judgetap.types import NO, YES, Question

DEFAULT_URL = "http://127.0.0.1:8149"


class AgentJevError(JudgetapError):
    """The AgentJev server is unreachable, failed, or answered unexpectedly."""


def _question(qid: str, q: Question) -> dict[str, Any]:
    if q.kind == "yesno":
        return {"id": qid, "type": "boolean", "question": q.text}
    if q.kind == "choice":
        return {
            "id": qid,
            "type": "choice",
            "question": q.text,
            "options": {o: o for o in q.options},
        }
    return {"id": qid, "type": "score", "question": q.text, "levels": list(q.options)}


def _distribution(q: Question, answer: Mapping[str, Any]) -> dict[str, float]:
    dist = answer["distribution"]
    if q.kind == "yesno":
        return {YES: dist["true"], NO: dist["false"]}
    if q.kind == "score" and set(map(str, dist)) != set(q.options):
        # Not keyed by level name, so it must be by level index. Level names
        # are checked first because levels may themselves be numerals.
        keys = [str(k) for k in dist]
        if all(k.isdigit() and int(k) < len(q.options) for k in keys):
            return {
                q.options[int(k)]: p for k, p in zip(keys, dist.values(), strict=True)
            }
    return dict(dist)


class AgentJevEngine:
    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        timeout: float = 5.0,
        transport: Transport = _urllib_transport,
    ) -> None:
        self.name = "agentjev"
        self.url = url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        ids = [f"q{i}" for i in range(len(questions))]
        if context is None:
            state = ""  # same as Jev and Laya: no context is an empty state
        elif isinstance(context, str):
            state = context
        else:
            state = json.dumps(
                dict(context) if isinstance(context, Mapping) else context, default=str
            )
        body = json.dumps(
            {
                "state": state,
                "questions": [
                    _question(i, q) for i, q in zip(ids, questions, strict=True)
                ],
            }
        ).encode()
        try:
            status, raw = self._transport(
                f"{self.url}/api/evaluate",
                {"Content-Type": "application/json"},
                body,
                self._timeout,
            )
        except OSError as err:
            raise AgentJevError(
                f"AgentJev server at {self.url} is unreachable: {err}"
            ) from err
        if status != 200:
            raise AgentJevError(
                f"AgentJev returned HTTP {status}: {raw[:300].decode(errors='replace')}"
            )
        try:
            answers = {a["id"]: a for a in json.loads(raw)["results"][0]["answers"]}
            return [
                RawAnswer(_distribution(q, answers[i]), cost_usd=0.0)
                for i, q in zip(ids, questions, strict=True)
            ]
        except (KeyError, TypeError, ValueError, IndexError) as err:
            raise AgentJevError(f"unexpected AgentJev response shape: {err!r}") from err

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        return await asyncio.to_thread(self.decide, questions, context)
