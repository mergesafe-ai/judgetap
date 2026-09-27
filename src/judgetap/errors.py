"""Errors raised by judgetap."""


class JudgetapError(Exception):
    """Base class for every judgetap error."""


class InvalidQuestionError(JudgetapError, ValueError):
    """A question was malformed before it reached any engine."""


class InvalidAnswerError(JudgetapError):
    """An engine returned an answer outside the question's allowed values.

    An out-of-set answer is a failure, never a result: callers can rely on
    every returned Decision being one of the values they asked for.
    """


class NoEngineError(JudgetapError):
    """A decision was requested with no engine configured."""


# The old name, kept for one release after the rename (#38).
SnapjudgeError = JudgetapError
