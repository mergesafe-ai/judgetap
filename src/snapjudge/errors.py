"""Errors raised by snapjudge."""


class SnapjudgeError(Exception):
    """Base class for every snapjudge error."""


class InvalidQuestionError(SnapjudgeError, ValueError):
    """A question was malformed before it reached any engine."""


class InvalidAnswerError(SnapjudgeError):
    """An engine returned an answer outside the question's allowed values.

    An out-of-set answer is a failure, never a result: callers can rely on
    every returned Decision being one of the values they asked for.
    """


class NoEngineError(SnapjudgeError):
    """A decision was requested with no engine configured."""
