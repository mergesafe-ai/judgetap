"""TypeSafe Jev over its HTTP API (docs.typesafe.ai/api), stdlib only."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from snapjudge.engine import Context, RawAnswer, plain_context
from snapjudge.errors import SnapjudgeError
from snapjudge.types import YES, Question

API_URL = "https://api.typesafe.ai/v1/systemone"
# Published list price: $0.042 per million input tokens, output unmetered.
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000
RETRY_STATUSES = frozenset({429, 529})

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


class JevError(SnapjudgeError):
    """Jev rejected or failed a request."""


def _urllib_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def _question_payload(q: Question) -> dict[str, Any]:
    if q.kind == "yesno":
        return {"type": "noul", "instructions": q.text}
    if q.kind == "choice":
        # Jev wants option -> description; the option name is the description.
        return {
            "type": "choice",
            "instructions": q.text,
            "criteria": {o: o for o in q.options},
        }
    return {"type": "score", "instructions": q.text, "criteria": list(q.options)}


def _distribution(q: Question, answer: dict[str, Any]) -> dict[str, float]:
    if q.kind == "yesno":
        p = answer["noul"]
        return {YES: p, "no": 1 - p}
    probs = answer["probabilities"]
    if q.kind == "choice":
        return dict(probs)
    # Score probabilities are keyed by level index as a string.
    return {q.options[int(i)]: p for i, p in probs.items()}


class JevEngine:
    def __init__(
        self,
        model: str = "jev-latest",
        api_key: str | None = None,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        transport: Transport = _urllib_transport,
    ) -> None:
        self.name = "jev"
        self.model = model
        self._api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport = transport

    def __repr__(self) -> str:  # never print the key
        return f"JevEngine(model={self.model!r})"

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        if not self._api_key:
            raise JevError("no Jev API key: set TYPESAFE_API_KEY or pass api_key=")
        ids = [f"q{i}" for i in range(len(questions))]
        body = json.dumps(
            {
                "model": self.model,
                "state": plain_context(context),
                "questions": {
                    i: _question_payload(q) for i, q in zip(ids, questions, strict=True)
                },
            }
        ).encode()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        data = self._post(headers, body)
        try:
            answers = data["answers"]
            tokens = data.get("usage", {}).get("input_tokens")
            cost = tokens * USD_PER_INPUT_TOKEN / len(questions) if tokens else None
            return [
                RawAnswer(_distribution(q, answers[i]), cost_usd=cost)
                for i, q in zip(ids, questions, strict=True)
            ]
        except (KeyError, TypeError, ValueError, IndexError) as err:
            raise JevError(f"unexpected Jev response shape: {err!r}") from err

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        return await asyncio.to_thread(self.decide, questions, context)

    def _post(self, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            status, raw = self._transport(API_URL, headers, body, self._timeout)
            if status == 200:
                return json.loads(raw)
            if status in RETRY_STATUSES and attempt < self._max_retries:
                time.sleep(min(0.25 * 2**attempt, 4.0))
                continue
            detail = raw[:300].decode(errors="replace")
            raise JevError(f"Jev returned HTTP {status}: {detail}")
        raise AssertionError("unreachable")
