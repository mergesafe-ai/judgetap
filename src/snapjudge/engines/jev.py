"""TypeSafe Jev over its HTTP API (docs.typesafe.ai/api), stdlib only."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from snapjudge.engine import Context, RawAnswer, plain_context
from snapjudge.errors import SnapjudgeError
from snapjudge.secrets import get_key
from snapjudge.types import YES, Question

DEFAULT_BASE_URL = "https://api.typesafe.ai"
API_PATH = "/v1/systemone"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
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
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise JevError("base_url must be an http(s) URL with a host")
        if parsed.username or parsed.password:
            # Credentials in the URL would end up in names, reprs and logs.
            raise JevError("base_url must not contain credentials; use api_key")
        try:
            _ = parsed.port  # raises on a malformed port
        except ValueError as err:
            raise JevError(f"base_url has an invalid port: {err}") from err
        # Any TypeSafe-compatible server on this machine: no key, no price.
        self.local = parsed.hostname in LOCAL_HOSTS
        if parsed.scheme == "http" and not self.local:
            raise JevError(
                "remote base_url must use https: the API key would travel in cleartext"
            )
        self.base_url = base_url.rstrip("/")
        self.name = (
            "jev" if self.base_url == DEFAULT_BASE_URL else f"typesafe@{parsed.netloc}"
        )
        self.model = model
        self._api_key = api_key or get_key("TYPESAFE_API_KEY")
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport = transport

    @property
    def needs_key(self) -> bool:
        """True when a remote endpoint has no key to authenticate with."""
        return not self.local and not self._api_key

    def __repr__(self) -> str:  # never print the key
        return f"JevEngine(model={self.model!r}, base_url={self.base_url!r})"

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        if not self._api_key and not self.local:
            raise JevError(
                "no Jev API key: set TYPESAFE_API_KEY, save it with `snapjudge keys set "
                "TYPESAFE_API_KEY`, or pass api_key="
            )
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
        headers = {"Content-Type": "application/json"}
        if self._api_key and not self.local:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = self._post(headers, body)
        try:
            answers = data["answers"]
            tokens = data.get("usage", {}).get("input_tokens")
            if self.local:
                cost = 0.0
            else:
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
            status, raw = self._transport(
                self.base_url + API_PATH, headers, body, self._timeout
            )
            if status == 200:
                return json.loads(raw)
            if status in RETRY_STATUSES and attempt < self._max_retries:
                time.sleep(min(0.25 * 2**attempt, 4.0))
                continue
            detail = raw[:300].decode(errors="replace")
            raise JevError(f"Jev returned HTTP {status}: {detail}")
        raise AssertionError("unreachable")
