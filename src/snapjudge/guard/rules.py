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


SEPARATORS = frozenset({";", "&&", "||", "|", "&", "\n"})
# Anything the shell rewrites before rm sees it: we cannot know the real path.
UNRESOLVABLE = re.compile(r"[$`]|^~[^/]")


def _commands(command: str) -> list[list[str]] | None:
    """Split a shell line into simple commands; None if it cannot be parsed."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    commands: list[list[str]] = [[]]
    for tok in tokens:
        if tok in SEPARATORS:
            commands.append([])
        else:
            commands[-1].append(tok)
    return [c for c in commands if c]


def _resolve(arg: str, cwd: Path | None) -> Path | None:
    """Resolve a path argument, or None when the shell would rewrite it."""
    if UNRESOLVABLE.search(arg) or cwd is None:
        return None
    target = Path(arg).expanduser()
    target = target if target.is_absolute() else cwd / target
    try:
        return target.resolve(strict=False)
    except OSError:
        return None


def _rm_targets_outside(command: str, workspace: Path) -> str | None:
    """Return the first recursive-rm target outside the workspace, or one whose
    location can't be established (a variable, or a relative path after a cd we
    couldn't follow). Tracks `cd` across the line."""
    commands = _commands(command)
    if commands is None:
        return None
    cwd: Path | None = workspace
    for argv in commands:
        prog = Path(argv[0]).name
        if prog in ("cd", "pushd"):
            dest = argv[1] if len(argv) > 1 else "~"
            cwd = _resolve(dest, cwd)
            continue
        if prog != "rm":
            continue
        args = argv[1:]
        short = "".join(
            a[1:] for a in args if a.startswith("-") and not a.startswith("--")
        )
        if not ("r" in short.lower() or "--recursive" in args):
            continue
        for arg in args:
            if arg.startswith("-"):
                continue
            resolved = _resolve(arg, cwd)
            if (
                resolved is None
                or resolved == workspace
                or not resolved.is_relative_to(workspace)
            ):
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


# Credentials that show up inside commands, not just files. Each pattern
# keeps its `keep` group (the label) and masks the value after it.
COMMAND_SECRETS = (
    re.compile(r"(?i)(?P<keep>authorization:\s*(bearer|basic|token)\s+)\S+"),
    re.compile(
        r"(?i)(?P<keep>(--)?(password|passwd|token|secret|api[-_]?key)[= ]\s*)[^\s'\"]+"
    ),
    re.compile(r"(?P<keep>\b[A-Z0-9_]*(TOKEN|SECRET|PASSWORD|API_KEY)=)\S+"),
    re.compile(r"(?P<keep>://[^:/\s@]+:)[^@\s]+(?=@)"),
)


def redact(text: str) -> str:
    """Mask credential-looking values so a command can be logged."""
    for _name, pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    for pattern in COMMAND_SECRETS:
        text = pattern.sub(lambda m: m.group("keep") + "[REDACTED]", text)
    return text
