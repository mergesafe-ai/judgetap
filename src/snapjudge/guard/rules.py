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
        r"\bgit\s+push\b[^\n;&|]*\s(\S+:)?(refs/heads/)?("
        + "|".join(PROTECTED_BRANCHES)
        + r")\b",
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
        # No WHERE before the statement ends: comments and trailing text don't hide it.
        r"\bdelete\s+from\s+[\w.\"`]+(?![^;]*\bwhere\b)",
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
# Prefixes that run the rest of the line as a command.
WRAPPERS = frozenset(
    {
        "sudo",
        "doas",
        "env",
        "nice",
        "nohup",
        "time",
        "command",
        "exec",
        "xargs",
        "stdbuf",
        "timeout",
    }
)
# Shell text that runs as a command inside another: $(...), `...`, sh -c '...'.
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
SHELL_STRING = re.compile(
    r"\b(?:ba|z|da)?sh\s+-\w*c\s+(['\"])(.*?)\1|\beval\s+(['\"])(.*?)\3", re.DOTALL
)
# Anything the shell rewrites before rm sees it: we cannot know the real path.
UNRESOLVABLE = re.compile(r"[$`]|^~[^/]")


def _commands(command: str) -> list[list[str]] | None:
    """Split a shell line into simple commands; None if it cannot be parsed."""
    # Newlines separate commands; shlex would otherwise treat them as spaces.
    command = command.replace("\n", " ; ")
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
    return [_unwrap(c) for c in commands if c]


# Wrapper flags that consume the next argument (`sudo -u root rm ...`).
WRAPPER_ARG_FLAGS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "nice": {"-n", "--adjustment"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {
        "-I",
        "-n",
        "-P",
        "-L",
        "-s",
        "-d",
        "-E",
        "-a",
        "--max-args",
        "--max-procs",
    },
    "stdbuf": {"-i", "-o", "-e"},
}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")


def _unwrap(argv: list[str]) -> list[str]:
    """Drop sudo/env/... prefixes, their flags (and flag values), VAR=value
    assignments and a timeout duration, leaving the command that really runs."""
    i = 0
    while i < len(argv):
        name = Path(argv[i]).name
        if ASSIGNMENT.fullmatch(argv[i]):
            i += 1
            continue
        if name not in WRAPPERS:
            break
        takes_arg = WRAPPER_ARG_FLAGS.get(name, set())
        i += 1
        while i < len(argv):
            arg = argv[i]
            if arg == "--":
                i += 1
                break
            if arg in takes_arg:
                i += 2
            elif arg.startswith("-") or ASSIGNMENT.fullmatch(arg):
                i += 1
            elif name == "timeout" and re.fullmatch(r"\d+(\.\d+)?[smhd]?", arg):
                i += 1  # the duration
                break
            else:
                break
    return argv[i:] or argv


# git options that come before the subcommand: `git -C dir push --force`.
GIT_GLOBALS = re.compile(
    r"\bgit((?:\s+(?:-C\s+\S+|-c\s+\S+|--git-dir(?:=|\s+)\S+|--work-tree(?:=|\s+)\S+"
    r"|--namespace(?:=|\s+)\S+|--no-pager|-P|--bare|--no-replace-objects|--literal-pathspecs))+)\s"
)


def nested_commands(command: str) -> list[str]:
    """Command text that the shell runs inside this one."""
    inner = [a or b for a, b in SUBSTITUTION.findall(command)]
    inner += [m.group(2) or m.group(4) for m in SHELL_STRING.finditer(command)]
    return [c for c in inner if c and c.strip()]


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
            # `cd -` goes to $OLDPWD, which we can't see.
            cwd = None if dest == "-" else _resolve(dest, cwd)
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
    command = GIT_GLOBALS.sub("git ", command)
    for inner in nested_commands(command):
        hit = check_command(inner, workspace)
        if hit:
            return hit
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
    # Header-style credentials: X-API-Key: v, X-Auth-Token: v, Cookie: v.
    re.compile(
        r"(?i)(?P<keep>\b[\w-]*(api[-_]?key|token|secret|password|auth|cookie)[\w-]*\s*:\s*)"
        r"(?!(bearer|basic|token)\s)[^\s'\"]+"
    ),
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
