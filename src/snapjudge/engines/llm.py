"""Any chat model through LiteLLM, asked for a JSON distribution.

LLMs rarely expose calibrated probabilities for a label set, so answers
from this engine are marked `calibrated=False`: the numbers are the model's
own estimate, useful for ranking and escalation, not as true frequencies.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from snapjudge.engine import Context, RawAnswer, plain_context
from snapjudge.errors import SnapjudgeError
from snapjudge.types import Question

SYSTEM = (
    "You answer typed questions about the given state. For each question, "
    "return a probability for every allowed option; probabilities for one "
    "question sum to 1. Reply with JSON only: "
    '{"answers": {"<question id>": {"<option>": <probability>, ...}, ...}}'
)


class LLMError(SnapjudgeError):
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


class LLMEngine:
    def __init__(self, model: str, **completion_kwargs: Any) -> None:
        self.name = f"llm:{model}"
        self.model = model
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

    def decide(
        self, questions: Sequence[Question], context: Context
    ) -> Sequence[RawAnswer]:
        litellm = _import_litellm()
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
            "the llm engine needs LiteLLM: pip install 'snapjudge[llm]'"
        ) from err
    return litellm
