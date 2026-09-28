"""Any chat model through LiteLLM.

Two modes:
- JSON (default): the model is asked for a JSON distribution. Those numbers
  are its own estimate, so answers are marked `calibrated=False`.
- logprobs (`llm:<model>?logprobs`): each question is one call that lists the
  options as letters and reads the letter's token probabilities from the
  server's `top_logprobs`. The distribution comes from the model itself, so
  answers are `calibrated=True`. This costs one call per question instead of
  one per batch, but each call generates a single token. If the provider
  rejects logprobs, the engine falls back to JSON mode for good.
"""

from __future__ import annotations

import json
import math
import string
from collections.abc import Sequence
from typing import Any

from judgetap.engine import Context, RawAnswer, plain_context
from judgetap.errors import JudgetapError
from judgetap.types import Question

SYSTEM = (
    "You answer typed questions about the given state. For each question, "
    "return a probability for every allowed option; probabilities for one "
    "question sum to 1. Reply with JSON only: "
    '{"answers": {"<question id>": {"<option>": <probability>, ...}, ...}}'
)


class LLMError(JudgetapError):
    """The model call failed or its reply could not be parsed."""


def _prompt(questions: Sequence[Question], context: Context) -> str:
    state = plain_context(context)
    state = state if isinstance(state, str) else json.dumps(state)
    lines = [f"State:\n{state}\n", "Questions:"]
    for i, q in enumerate(questions):
        order = " (ordered lowest to highest)" if q.kind == "score" else ""
        lines.append(
            f"- q{i}: {q.text}\n  options{order}: {json.dumps(list(q.options))}"
        )
    return "\n".join(lines)


LETTERS = string.ascii_uppercase
LOGPROBS_SYSTEM = (
    "You answer one question about the given state by choosing one option. "
    "Reply with the option's letter only, nothing else."
)


def _letter_prompt(question: Question, context: Context) -> str:
    state = plain_context(context)
    state = state if isinstance(state, str) else json.dumps(state)
    order = " (ordered lowest to highest)" if question.kind == "score" else ""
    lines = [f"State:\n{state}\n", f"Question: {question.text}", f"Options{order}:"]
    lines += [f"{LETTERS[i]}. {option}" for i, option in enumerate(question.options)]
    lines.append("Answer with one letter.")
    return "\n".join(lines)


def _field(obj: Any, name: str) -> Any:
    """Read a field from an OpenAI-shaped object or a plain dict."""
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _letter_distribution(
    question: Question, response: Any, model: str
) -> dict[str, float]:
    """Option -> probability from the first token's top_logprobs, matching
    letters case-insensitively and ignoring surrounding whitespace."""
    try:
        first = _field(_field(response.choices[0], "logprobs"), "content")[0]
        tops = _field(first, "top_logprobs") or []
    except (TypeError, IndexError, AttributeError) as err:
        raise LLMError(f"{model} returned no logprobs: {err!r}") from err
    letters = {LETTERS[i]: option for i, option in enumerate(question.options)}
    mass = dict.fromkeys(question.options, 0.0)
    for top in tops:
        token = str(_field(top, "token") or "").strip().upper()
        logprob = _field(top, "logprob")
        if token in letters and isinstance(logprob, int | float):
            mass[letters[token]] += math.exp(logprob)
    total = sum(mass.values())
    if total <= 0:
        raise LLMError(f"{model} put no probability on any option letter")
    return {option: p / total for option, p in mass.items()}


def _rejects_logprobs(litellm: Any, err: Exception) -> bool:
    """True when the provider refused the logprobs parameters themselves."""
    kinds = tuple(
        k
        for k in (getattr(litellm, "UnsupportedParamsError", None),)
        if isinstance(k, type)
    )
    return (kinds and isinstance(err, kinds)) or "logprob" in str(err).lower()


class LLMEngine:
    def __init__(
        self, model: str, *, logprobs: bool = False, **completion_kwargs: Any
    ) -> None:
        self.name = f"llm:{model}"
        self.model = model
        self.logprobs = logprobs
        self._kwargs = completion_kwargs

    def _messages(self, questions: Sequence[Question], context: Context) -> list[dict]:
        return [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _prompt(questions, context)},
        ]

    def _parse(self, questions: Sequence[Question], response: Any) -> list[RawAnswer]:
        try:
            content = response.choices[0].message.content
            answers = json.loads(content)["answers"]
            try:
                from litellm import completion_cost

                cost = completion_cost(completion_response=response) / len(questions)
            except Exception:  # noqa: BLE001 -- LiteLLM raises assorted errors for unpriced models
                cost = None
            return [
                RawAnswer(dict(answers[f"q{i}"]), cost_usd=cost, calibrated=False)
                for i in range(len(questions))
            ]
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as err:
            raise LLMError(f"could not parse {self.model} reply: {err!r}") from err

    def _letter_call(self, question: Question, context: Context) -> dict[str, Any]:
        if len(question.options) > len(LETTERS):
            raise LLMError(f"logprobs mode handles up to {len(LETTERS)} options")
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": LOGPROBS_SYSTEM},
                {"role": "user", "content": _letter_prompt(question, context)},
            ],
            "max_tokens": 1,
            "logprobs": True,
            "top_logprobs": 20,
            **self._kwargs,
        }

    def _letter_answer(self, question: Question, response: Any) -> RawAnswer:
        try:
            from litellm import completion_cost

            cost = completion_cost(completion_response=response)
        except Exception:  # noqa: BLE001 -- LiteLLM raises assorted errors for unpriced models
            cost = None
        return RawAnswer(
            _letter_distribution(question, response, self.model),
            cost_usd=cost,
            calibrated=True,
        )

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        litellm = _import_litellm()
        if self.logprobs:
            try:
                return [
                    self._letter_answer(
                        q, litellm.completion(**self._letter_call(q, context))
                    )
                    for q in questions
                ]
            except Exception as err:
                if not _rejects_logprobs(litellm, err):
                    raise
                self.logprobs = False  # this provider can't: JSON mode from now on
        response = litellm.completion(
            model=self.model,
            messages=self._messages(questions, context),
            response_format={"type": "json_object"},
            **self._kwargs,
        )
        return self._parse(questions, response)

    async def adecide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        litellm = _import_litellm()
        if self.logprobs:
            try:
                answers = []
                for q in questions:
                    response = await litellm.acompletion(
                        **self._letter_call(q, context)
                    )
                    answers.append(self._letter_answer(q, response))
                return answers
            except Exception as err:
                if not _rejects_logprobs(litellm, err):
                    raise
                self.logprobs = False
        response = await litellm.acompletion(
            model=self.model,
            messages=self._messages(questions, context),
            response_format={"type": "json_object"},
            **self._kwargs,
        )
        return self._parse(questions, response)


def _import_litellm():
    try:
        import litellm
    except ImportError as err:
        raise LLMError(
            "the llm engine needs LiteLLM: pip install 'judgetap[llm]'"
        ) from err
    return litellm
