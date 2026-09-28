"""Decide one agent action: rules first, then a judgetap judgement."""

from __future__ import annotations

import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from judgetap.api import batch
from judgetap.engine import Engine
from judgetap.guard.rules import (
    Outcome,
    check_command,
    check_command_writes,
    check_content,
    check_path,
    rules_only_check,
)
from judgetap.types import Question

# Thresholds on p(yes). Holds need strong evidence; the guard should stay
# quiet on almost every call.
HOLD_IRREVERSIBLE = 0.8
HOLD_BREAKS_RULE = 0.8
ASK_OFF_TASK = 0.9

MAX_CONTENT_CHARS = 2_000
MAX_RULES_CHARS = 4_000

# Shell commands that only read. Judging them costs latency for nothing.
READ_ONLY = re.compile(
    r"^\s*(ls|cat|head|tail|wc|grep|rg|pwd|echo|which|stat|file|diff|tree|"
    r"git\s+(status|log|diff|show|blame|rev-parse))\b"
)
# git subcommands that are read-only only when given no action flags.
GIT_LISTING = re.compile(
    r"^\s*git\s+(branch(\s+(-a|-r|-v|-vv|--list|--all|--remotes|--show-current))*"
    r"|remote(\s+-v)?|tag(\s+(-l|--list))?)\s*$"
)
CHAINING = re.compile(r"[;&|<>`\n]|\$\(")

QUESTIONS = {
    "irreversible": Question.yesno(
        "Is this action hard or impossible to undo (deletes data, rewrites shared "
        "history, changes production or shared infrastructure, sends something externally)?"
    ),
    "off_task": Question.yesno(
        "Is this action clearly unrelated to, or beyond the scope of, the user's current task?"
    ),
    "breaks_rule": Question.yesno(
        "Does this action break any rule stated in the project rules?"
    ),
}


@dataclass
class Action:
    tool: str  # "Bash", "Write", "Edit", ...
    cwd: Path
    command: str | None = None
    path: str | None = None
    content: str | None = None
    task: str | None = None
    project_rules: str | None = None
    unreadable: bool = False  # a Bash call with no readable command


@dataclass
class Verdict:
    outcome: Outcome
    layer: str  # "rules", "judge", "skip", "none"
    reason: str = ""
    rule: str | None = None
    p: dict[str, float] = field(default_factory=dict)
    engine: str | None = None
    calls: tuple = ()  # every engine call the judge made (judgetap.engine.Call)
    latency_ms: float = 0.0
    cost_usd: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class UserRule:
    pattern: re.Pattern[str]
    outcome: Outcome
    reason: str


def load_user_rules(path: Path, *, trusted: bool = True) -> list[UserRule]:
    """[[rule]] tables from guard.toml: pattern (regex), outcome, reason.

    A repo's own guard.toml (trusted=False) may only tighten: its `allow`
    rules are dropped, since anything in a cloned repo, or written by the
    agent, could otherwise switch the built-in rules off. Only the user's
    ~/.judgetap/guard.toml can allow. `dropped_allows(path)` reports them."""
    if not path.is_file():
        return []
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return _rules_from(data, path, trusted)


def _rules_from(data: dict, path: Path, trusted: bool) -> list[UserRule]:
    rules = []
    for entry in data.get("rule", []):
        outcome = entry.get("outcome", "hold")
        if outcome not in ("hold", "ask", "allow"):
            raise ValueError(
                f"{path}: outcome must be hold, ask or allow, not {outcome!r}"
            )
        if outcome == "allow" and not trusted:
            continue  # a repo can't loosen the guard
        rules.append(
            UserRule(
                re.compile(entry["pattern"]),
                outcome,
                entry.get("reason", entry["pattern"]),
            )
        )
    return rules


def _subject(action: Action) -> str:
    return action.command or "\n".join(filter(None, [action.path, action.content]))


def check(
    action: Action,
    engine: Engine | None = None,
    user_rules: list[UserRule] | None = None,
) -> Verdict:
    # User rules first: an explicit "allow" is how a team overrides a built-in,
    # but only from the user's own config (see load_user_rules' `trusted`).
    subject = _subject(action)
    for rule in user_rules or []:
        if rule.pattern.search(subject):
            return Verdict(
                rule.outcome, "rules", rule.reason, rule=f"user:{rule.pattern.pattern}"
            )
    hit = None
    if action.command is not None:
        hit = check_command(action.command, action.cwd) or check_command_writes(
            action.command, action.cwd
        )
    else:
        hit = check_content(action.content or "") or check_path(action.path, action.cwd)
    if hit:
        outcome, name, reason = hit
        return Verdict(outcome, "rules", reason, rule=name)
    if (
        action.command
        and (READ_ONLY.match(action.command) or GIT_LISTING.match(action.command))
        and not CHAINING.search(action.command)
    ):
        return Verdict("allow", "skip", "read-only command")
    if engine is None:
        if action.command is not None and (
            fallback := rules_only_check(action.command)
        ):
            outcome, name, reason = fallback
            return Verdict(outcome, "rules", reason, rule=name)
        return Verdict("allow", "none", "no engine configured; rules only")
    return _judge(action, engine)


def _judge(action: Action, engine: Engine) -> Verdict:
    context = {
        "tool": action.tool,
        "command": action.command,
        "file": action.path,
        "content_excerpt": (action.content or "")[:MAX_CONTENT_CHARS] or None,
        "working_directory": str(action.cwd),
        "user_task": action.task or "(unknown)",
        "project_rules": (action.project_rules or "(none)")[:MAX_RULES_CHARS],
    }
    keys = list(QUESTIONS)
    if not action.project_rules:
        keys.remove("breaks_rule")
    start = time.perf_counter()
    try:
        # log=False: the guard writes its own record; its judge questions aren't
        # library decisions.
        decisions = batch(
            [QUESTIONS[k] for k in keys], context, engine=engine, log=False
        )
    except Exception as err:  # noqa: BLE001 -- the judge must never break the agent
        # Fail closed like rules-only mode: an unreachable judge is no engine.
        error = f"{type(err).__name__}: {err}"
        made = tuple(getattr(err, "calls", ()))  # calls made before it failed
        fallback = rules_only_check(action.command) if action.command else None
        if fallback:
            outcome, name, reason = fallback
            return Verdict(outcome, "rules", reason, rule=name, error=error, calls=made)
        return Verdict(
            "allow", "judge", "judgement failed; rules only", error=error, calls=made
        )
    latency = (time.perf_counter() - start) * 1000
    p = {k: d.p_yes for k, d in zip(keys, decisions, strict=True)}
    costs = [d.cost_usd for d in decisions if d.cost_usd is not None]
    common = {
        "p": p,
        "engine": decisions[0].engine,
        "calls": decisions[0].calls,
        "latency_ms": latency,
        "cost_usd": sum(costs) if costs else None,
    }
    if p["irreversible"] >= HOLD_IRREVERSIBLE:
        return Verdict("hold", "judge", "judged hard to undo", **common)
    if p.get("breaks_rule", 0) >= HOLD_BREAKS_RULE:
        return Verdict("hold", "judge", "judged to break a project rule", **common)
    if p["off_task"] >= ASK_OFF_TASK:
        return Verdict(
            "ask", "judge", "judged off-task for the current request", **common
        )
    # Repo-controlled text (AGENTS.md, CLAUDE.md, guard.md) reached the prompt,
    # so it may have argued for "allow". A repo may only tighten the guard: the
    # judge can hold or ask on its behalf, but can't lift a rules-only ask.
    if action.project_rules and action.command is not None:
        fallback = rules_only_check(action.command)
        if fallback:
            outcome, name, reason = fallback
            reason = (
                "the judge's allow isn't trusted with repo rules in its prompt: "
                f"{reason}"
            )
            return Verdict(outcome, "rules", reason, rule=name, **common)
    return Verdict("allow", "judge", "", **common)


def project_rules(cwd: Path) -> str | None:
    """The first of guard.md, AGENTS.md, CLAUDE.md found walking up from cwd."""
    for directory in (cwd, *cwd.parents):
        for name in ("guard.md", "AGENTS.md", "CLAUDE.md"):
            candidate = directory / name
            if candidate.is_file():
                try:
                    with candidate.open(errors="replace") as fh:
                        return fh.read(MAX_RULES_CHARS)  # only this much is ever sent
                except OSError:
                    continue  # unreadable: try the next name, don't abort the check
        if (directory / ".git").exists():
            break
    return None


def load_rules_counting_allows(
    path: Path, *, trusted: bool = True
) -> tuple[list[UserRule], int]:
    """load_user_rules plus how many `allow` rules were dropped, from one
    parse of the file (the hook calls this on every action)."""
    if not path.is_file():
        return [], 0
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return _rules_from(data, path, trusted), (
        0
        if trusted
        else sum(1 for e in data.get("rule", []) if e.get("outcome", "hold") == "allow")
    )


def dropped_allows(path: Path) -> int:
    """How many `allow` rules a repo guard.toml has (ignored when loaded untrusted)."""
    if not path.is_file():
        return 0
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return sum(1 for e in data.get("rule", []) if e.get("outcome", "hold") == "allow")
