"""snapjudge guard: a pre-action check for coding agents.

Two layers. Rules decide the obvious cases with no model. Everything the
rules leave open goes to a snapjudge engine, which is asked whether the
action is hard to undo, off-task, or against the project's own rules.
"""

from snapjudge.guard.core import Action, Verdict, check

__all__ = ["Action", "Verdict", "check"]
