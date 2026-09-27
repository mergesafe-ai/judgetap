"""judgetap: fast typed decisions across Jev-style engines."""

from judgetap import engines
from judgetap.api import (
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
from judgetap.cascade import Cascade, CascadeExhaustedError
from judgetap.engine import Context, Engine, RawAnswer
from judgetap.errors import (
    InvalidAnswerError,
    InvalidQuestionError,
    JudgetapError,
    NoEngineError,
)
from judgetap.types import Decision, Question

__version__ = "0.0.1"  # x-release-please-version

__all__ = [
    "Cascade",
    "CascadeExhaustedError",
    "Context",
    "Decision",
    "Engine",
    "InvalidAnswerError",
    "InvalidQuestionError",
    "JudgetapError",
    "NoEngineError",
    "Question",
    "RawAnswer",
    "abatch",
    "achoice",
    "ascore",
    "ayesno",
    "batch",
    "choice",
    "configure",
    "engines",
    "score",
    "yesno",
]
