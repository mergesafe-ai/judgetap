"""snapjudge: fast typed decisions across Jev-style engines."""

from snapjudge.api import (
    abatch,
    achoice,
    ascore,
    ayesno,
    batch,
    choice,
    configure,
    score,
    yesno,
)
from snapjudge.cascade import Cascade, CascadeExhaustedError
from snapjudge.engine import Context, Engine, RawAnswer
from snapjudge.errors import (
    InvalidAnswerError,
    InvalidQuestionError,
    NoEngineError,
    SnapjudgeError,
)
from snapjudge.types import Decision, Question

__version__ = "0.0.1"  # x-release-please-version

__all__ = [
    "Cascade",
    "CascadeExhaustedError",
    "Context",
    "Decision",
    "Engine",
    "InvalidAnswerError",
    "InvalidQuestionError",
    "NoEngineError",
    "Question",
    "RawAnswer",
    "SnapjudgeError",
    "abatch",
    "achoice",
    "ascore",
    "ayesno",
    "batch",
    "choice",
    "configure",
    "score",
    "yesno",
]
