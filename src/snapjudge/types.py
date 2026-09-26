"""Questions and the Decision returned for each."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from snapjudge.errors import InvalidQuestionError

Kind = Literal["choice", "score", "yesno"]

MAX_CHOICE_OPTIONS = 255
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10
YES, NO = "yes", "no"


@dataclass(frozen=True)
class Question:
    """One typed question. Build it with `choice`, `score` or `yesno`."""

    kind: Kind
    text: str
    options: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in ("choice", "score", "yesno"):
            raise InvalidQuestionError(
                f"unknown question kind {self.kind!r}; use choice, score or yesno"
            )
        if not isinstance(self.text, str):
            raise InvalidQuestionError(
                f"question text must be a string, got {self.text!r}"
            )
        if not all(isinstance(o, str) for o in self.options):
            raise InvalidQuestionError(f"options must be strings, got {self.options!r}")
        if not self.text.strip():
            raise InvalidQuestionError("question text is empty")
        if len(set(self.options)) != len(self.options):
            raise InvalidQuestionError(f"duplicate options in {self.options!r}")
        if any(not o.strip() for o in self.options):
            raise InvalidQuestionError("options must be non-empty strings")
        if self.kind == "choice" and not 2 <= len(self.options) <= MAX_CHOICE_OPTIONS:
            raise InvalidQuestionError(
                f"choice needs 2-{MAX_CHOICE_OPTIONS} options, got {len(self.options)}"
            )
        if self.kind == "score" and not (
            MIN_SCORE_LEVELS <= len(self.options) <= MAX_SCORE_LEVELS
        ):
            raise InvalidQuestionError(
                f"score needs {MIN_SCORE_LEVELS}-{MAX_SCORE_LEVELS} levels, "
                f"got {len(self.options)}"
            )
        if self.kind == "yesno" and self.options != (YES, NO):
            raise InvalidQuestionError("yesno options are fixed to ('yes', 'no')")

    @classmethod
    def choice(cls, text: str, options: list[str] | tuple[str, ...]) -> Question:
        return cls("choice", text, tuple(options))

    @classmethod
    def score(cls, text: str, levels: list[str] | tuple[str, ...]) -> Question:
        """Levels are ordered from lowest to highest."""
        return cls("score", text, tuple(levels))

    @classmethod
    def yesno(cls, text: str) -> Question:
        return cls("yesno", text, (YES, NO))


@dataclass(frozen=True)
class Decision:
    """An engine's answer to one question.

    `value` is always one of the question's options. `p` is the probability
    of `value` after snapjudge renormalises the engine's distribution to sum
    to exactly 1; for a yes/no question use `p_yes` to read the probability
    of "yes" regardless of which side won.
    """

    question: Question
    value: str
    p: float
    distribution: dict[str, float]
    engine: str
    latency_ms: float
    cost_usd: float | None = None
    escalated: bool = False
    calibrated: bool = True
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def p_yes(self) -> float:
        if self.question.kind != "yesno":
            raise AttributeError("p_yes is only defined for yes/no questions")
        return self.distribution[YES]

    @property
    def level(self) -> int:
        """Zero-based position of `value` on a score question's scale."""
        if self.question.kind != "score":
            raise AttributeError("level is only defined for score questions")
        return self.question.options.index(self.value)
