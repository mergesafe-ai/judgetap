"""The rules layer: fixed patterns, microseconds, no model."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Outcome = Literal["allow", "hold", "ask"]

PROTECTED_BRANCHES = ("main", "master")


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    outcome: Outcome
    reason: str


def _r(name: str, regex: str, outcome: Outcome, reason: str) -> Rule:
    return Rule(name, re.compile(regex, re.IGNORECASE), outcome, reason)


COMMAND_RULES: tuple[Rule, ...] = (
    _r(
        "force-push",
        r"\bgit\s+push\b[^\n;&|]*\s(--force\b|--force-with-lease\b|-f\b|\+\S)",
        "hold",
        "force-push rewrites shared history",
    ),
    _r(
        "push-protected",
        r"\bgit\s+push\b[^\n;&|]*\s(\S+:)?(" + "|".join(PROTECTED_BRANCHES) + r")\b",
        "hold",
        "pushes straight to a protected branch",
    ),
    _r(
        "drop",
        r"\bdrop\s+(table|database|schema)\b",
        "hold",
        "drops a table, database or schema",
    ),
    _r(
        "delete-without-where",
        r"\bdelete\s+from\s+[\w.\"`]+\s*(;|$|\"|')",
        "hold",
        "DELETE without a WHERE clause removes every row",
    ),
    _r(
        "truncate",
        r"\btruncate\s+(table\s+)?[\w.\"`]+",
        "hold",
        "TRUNCATE removes every row",
    ),
    _r(
        "terraform-destroy",
        r"\bterraform\s+destroy\b",
        "hold",
        "terraform destroy tears down infrastructure",
    ),
    _r(
        "git-reset-hard",
        r"\bgit\s+reset\s+--hard\b",
        "ask",
        "git reset --hard discards uncommitted work",
    ),
    _r(
        "git-clean",
        r"\bgit\s+clean\s+-\w*f",
        "ask",
        "git clean -f deletes untracked files",
    ),
    _r(
        "pipe-to-shell",
        r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b",
        "ask",
        "pipes a download into a shell",
    ),
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "private-key",
        re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("openai-key", re.compile(r"\bsk-(proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}\b")),
)


def _rm_targets_outside(command: str, workspace: Path) -> str | None:
    """Return the first path a recursive rm would delete outside the workspace."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if Path(tok).name != "rm":
            continue
        args = tokens[i + 1 :]
        flags = "".join(
            a[1:] for a in args if a.startswith("-") and not a.startswith("--")
        )
        long_flags = {a for a in args if a.startswith("--")}
        if not ("r" in flags.lower() or "--recursive" in long_flags):
            continue
        for arg in args:
            if arg.startswith("-") or arg in {";", "&&", "||", "|"}:
                if arg in {";", "&&", "||", "|"}:
                    break
                continue
            target = Path(arg).expanduser()
            target = (workspace / target) if not target.is_absolute() else target
            try:
                resolved = target.resolve(strict=False)
            except OSError:
                return arg
            if resolved == workspace or not resolved.is_relative_to(workspace):
                return arg
    return None


def check_command(command: str, workspace: Path) -> tuple[Outcome, str, str] | None:
    """Return (outcome, rule, reason) for the first rule a shell command hits."""
    outside = _rm_targets_outside(command, workspace.resolve())
    if outside is not None:
        return (
            "hold",
            "rm-outside-workspace",
            f"recursive delete of {outside!r} outside the workspace",
        )
    for rule in COMMAND_RULES:
        if rule.pattern.search(command):
            return rule.outcome, rule.name, rule.reason
    return None


def check_content(content: str) -> tuple[Outcome, str, str] | None:
    """Return a hold if file content being written contains a secret."""
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            return (
                "hold",
                f"secret:{name}",
                f"writes what looks like a {name} into a file",
            )
    return None
