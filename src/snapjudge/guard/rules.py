"""The rules layer: fixed patterns, microseconds, no model."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
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
    ("github-fine-grained-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("openai-key", re.compile(r"\bsk-(proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}\b")),
)


SEPARATORS = frozenset({";", "&&", "||", "|", "&", "\n", "(", ")", "((", "))", "()"})
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
# A command name the shell builds at run time.
UNRESOLVABLE_NAME = re.compile(r"[$`]")


def _commands(command: str, *, unwrap: bool = True) -> list[list[str]] | None:
    """Split a shell line into simple commands; None if it cannot be parsed."""
    # Newlines separate commands; shlex would otherwise treat them as spaces.
    # Backslash-newline is a line continuation, not a command break.
    command = command.replace("\\\n", " ").replace("\n", " ; ")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
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
    return [_unwrap(c) if unwrap else c for c in commands if c]


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
            rest = argv[1:]
            if rest and rest[0] == "--":
                rest = rest[1:]
            # `cd -` is $OLDPWD, and any other option (-P, -L, -e) makes the
            # destination parsing ours, not the shell's: treat both as unknown.
            if rest and rest[0].startswith("-"):
                cwd = None
            else:
                cwd = _resolve(rest[0] if rest else "~", cwd)
            continue
        if UNRESOLVABLE_NAME.search(argv[0]):
            # `$(which rm) -rf /`, `$CMD x`: we can't know what runs.
            return argv[0]
        # An rm anywhere in the words, not just first: `then rm ...`,
        # `find -exec rm ...`, `echo rm ...`. Over-holding beats a missed delete.
        for j, word in enumerate(argv):
            if Path(word).name != "rm":
                continue
            if "xargs" in map(_name, argv[:j]):
                # Targets arrive on stdin: nothing to resolve.
                if _recursive(argv[j + 1 :]):
                    return "<paths from xargs>"
                continue
            hit = _rm_outside(argv[j + 1 :], cwd, workspace)
            if hit is not None:
                return hit
        if prog == "find" and ("-delete" in argv or "rm" in map(_name, argv)):
            for root in _find_roots(argv[1:]) or ["."]:
                resolved = _resolve(root, cwd)
                if resolved is None or not (
                    resolved == workspace or resolved.is_relative_to(workspace)
                ):
                    return root
    return None


def _name(word: str) -> str:
    return Path(word).name


def _recursive(args: list[str]) -> bool:
    short = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
    return "r" in short.lower() or "--recursive" in args


def _find_roots(args: list[str]) -> list[str]:
    """find's starting points: the words after its leading options (-H, -L,
    -P, -D debugopts, -Olevel) and before its first expression token."""
    rest = list(args)
    while rest:
        if rest[0] in ("-H", "-L", "-P") or re.fullmatch(r"-O\d*", rest[0]):
            rest.pop(0)
        elif rest[0] == "-D":
            del rest[:2]
        else:
            break
    roots = []
    for arg in rest:
        if arg.startswith(("-", "(", "!")):
            break
        roots.append(arg)
    return roots


def _rm_outside(args: list[str], cwd: Path | None, workspace: Path) -> str | None:
    if not _recursive(args):
        return None
    targets = [a for a in args if not a.startswith("-")]
    if not targets:
        return "<paths from stdin>"  # `... | xargs rm -rf`: nothing to resolve
    for arg in targets:
        resolved = _resolve(arg, cwd)
        if (
            resolved is None
            or resolved == workspace
            or not resolved.is_relative_to(workspace)
        ):
            return arg
    return None
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


def _git_dirs(cwd: Path) -> tuple[Path, Path] | None:
    """(git dir, common dir) for the repo containing cwd; worktree aware."""
    for directory in (cwd, *cwd.parents):
        git = directory / ".git"
        try:
            if git.is_file():  # worktree or submodule: "gitdir: <path>"
                text = git.read_text(errors="replace").strip()
                if not text.startswith("gitdir:"):
                    return None
                git = (directory / text.split(":", 1)[1].strip()).resolve()
            if git.is_dir():
                common = git
                if (git / "commondir").is_file():
                    common = (git / (git / "commondir").read_text().strip()).resolve()
                return git, common
        except OSError:
            return None
    return None


def upstream_branch(cwd: Path, branch: str) -> str | None:
    """The branch `branch` tracks (branch.<name>.merge in .git/config)."""
    dirs = _git_dirs(cwd)
    if dirs is None:
        return None
    try:
        config = (dirs[1] / "config").read_text(errors="replace")
    except OSError:
        return None
    section = re.search(
        r'^\[branch\s+"' + re.escape(branch) + r'"\](.*?)(?=^\[|\Z)',
        config,
        re.MULTILINE | re.DOTALL,
    )
    if not section:
        return None
    merge = re.search(r"^\s*merge\s*=\s*(\S+)", section.group(1), re.MULTILINE)
    return merge.group(1).removeprefix("refs/heads/") if merge else None


def _custom_push_config(cwd: Path) -> bool:
    """True when .git/config sets anything that changes where a plain push
    goes beyond current/upstream: remote push refspecs, pushRemote, or a
    push.default other than simple/current/upstream."""
    dirs = _git_dirs(cwd)
    if dirs is None:
        return False
    try:
        config = (dirs[1] / "config").read_text(errors="replace")
    except OSError:
        return True  # can't read it: assume the worst
    if re.search(r"^\s*(push|pushremote)\s*=", config, re.MULTILINE | re.IGNORECASE):
        return True
    default = re.search(r"^\[push\](.*?)(?=^\[|\Z)", config, re.MULTILINE | re.DOTALL)
    if default:
        mode = re.search(
            r"^\s*default\s*=\s*(\S+)", default.group(1), re.MULTILINE | re.IGNORECASE
        )
        if mode and mode.group(1).lower() not in (
            "simple",
            "current",
            "upstream",
            "tracking",
        ):
            return True
    return False


def current_branch(cwd: Path) -> str | None:
    """The checked-out branch, read from .git/HEAD: no subprocess, so the
    rules layer stays in microseconds. None if detached or not a repo."""
    for directory in (cwd, *cwd.parents):
        git = directory / ".git"
        if git.is_file():  # worktree or submodule: "gitdir: <path>"
            try:
                text = git.read_text(errors="replace").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            git = (directory / text.split(":", 1)[1].strip()).resolve()
        if git.is_dir():
            try:
                head = (git / "HEAD").read_text(errors="replace").strip()
            except OSError:
                return None
            prefix = "ref: refs/heads/"
            return head[len(prefix) :] if head.startswith(prefix) else None
    return None


PUSH_VALUE_FLAGS = frozenset(
    {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
)


def _push_rule(
    argv: list[str], cwd: Path | None, branch_of: Callable[[Path], str | None]
) -> tuple[Outcome, str, str] | None:
    """Judge one `git push` by its destination refs, not by words in it."""
    args = argv[2:]
    for arg in args:
        forced = (
            arg.startswith("--force")
            or (arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:])
            or (not arg.startswith("-") and arg.startswith("+"))
        )
        if forced:
            return "hold", "force-push", "force-push rewrites shared history"
    if {"--all", "--mirror"} & set(args):
        return "hold", "push-protected", "pushes every branch, protected ones included"
    positional, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg in PUSH_VALUE_FLAGS:
            skip = True
        elif not arg.startswith("-"):
            positional.append(arg)
    # The remote is the first positional, unless --repo already named it.
    has_repo = any(a == "--repo" or a.startswith("--repo=") for a in args)
    refspecs = positional if has_repo else positional[1:]
    if not refspecs:
        refspecs = ["HEAD"]  # plain `git push [remote]` pushes the current branch
    for spec in refspecs:
        dest = spec.lstrip("+").split(":")[-1]
        dest = dest.removeprefix("refs/heads/")
        if UNRESOLVABLE_NAME.search(dest) or not dest:
            return (
                "ask",
                "push-implicit",
                f"push destination {spec!r} can't be resolved",
            )
        if dest == "HEAD":
            branch = branch_of(cwd) if cwd is not None else None
            if branch is None:
                return "ask", "push-implicit", "push destination can't be determined"
            # With push.default=upstream a plain push goes to the tracked
            # branch, which may be named differently: check both.
            if _custom_push_config(cwd):
                return (
                    "ask",
                    "push-implicit",
                    "repo config rewrites where a plain push goes",
                )
            upstream = upstream_branch(cwd, branch) if spec == "HEAD" else None
            protected = [b for b in (branch, upstream) if b in PROTECTED_BRANCHES]
            if protected:
                return (
                    "hold",
                    "push-protected",
                    f"plain push from {branch!r} can reach protected branch {protected[0]!r}",
                )
            continue
        if "*" in dest:
            pattern = re.escape(dest).replace(r"\*", ".*")
            if any(re.fullmatch(pattern, b) for b in PROTECTED_BRANCHES):
                return (
                    "hold",
                    "push-protected",
                    f"wildcard refspec {spec!r} covers protected branches",
                )
        if dest in PROTECTED_BRANCHES:
            return (
                "hold",
                "push-protected",
                f"pushes straight to protected branch {dest!r}",
            )
    return None


# Words that can destroy or publish something.
DESTRUCTIVE = re.compile(
    r"(?<![\w-])(rm|rmdir|find|xargs|dd|mkfs\S*|shred|truncate|mv|git|terraform|kubectl|"
    r"drop|delete|psql|mysql|sqlite3|aws|gcloud|az|chmod|chown)(?![\w-])",
    re.IGNORECASE,
)
# Shell the rules can't see through: expansions, substitutions, heredocs,
# eval, and wrappers that rewrite the command or its directory.
OPAQUE = re.compile(
    r"[$`]|<\(|>\(|<<|\beval\b|\benv\b[^;&|]*\s(-S|--split-string|-C|--chdir)\b"
)


def check_command(
    command: str,
    workspace: Path,
    branch_of: Callable[[Path], str | None] = current_branch,
) -> tuple[Outcome, str, str] | None:
    """Return (outcome, rule, reason) for the strongest rule a command hits:
    any hold beats any ask, so a cautious rule can't mask a dangerous one."""
    # `git -C dir push` pushes dir's branch: remember it before normalising.
    git_dir = re.search(r"\bgit\s+(?:\S+\s+)*?-C\s+(\S+)", command)
    push_cwd: Path | None = workspace
    if git_dir:
        resolved = _resolve(git_dir.group(1), workspace)
        push_cwd = resolved
    command = GIT_GLOBALS.sub("git ", command)
    hits = [
        h
        for inner in nested_commands(command)
        if (h := check_command(inner, workspace, branch_of))
    ]
    outside = _rm_targets_outside(command, workspace.resolve())
    if outside is not None:
        hits.append(
            (
                "hold",
                "rm-outside-workspace",
                (
                    f"recursive delete of {outside!r}, which is outside the workspace "
                    "or can't be resolved before the shell runs"
                ),
            )
        )
    hits += [
        (r.outcome, r.name, r.reason)
        for r in COMMAND_RULES
        if r.pattern.search(command)
    ]
    for argv in _commands(command) or []:
        if [Path(argv[0]).name, *argv[1:2]] == ["git", "push"] and (
            h := _push_rule(argv, push_cwd, branch_of)
        ):
            hits.append(h)
    if OPAQUE.search(command) and DESTRUCTIVE.search(command):
        hits.append(
            (
                "ask",
                "opaque-destructive",
                (
                    "a destructive command uses shell the rules can't analyse "
                    "(expansion, substitution, heredoc, eval or env -S/--chdir)"
                ),
            )
        )
    if not hits:
        return None
    return next((h for h in hits if h[0] == "hold"), hits[0])


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
    # JSON fields: {"password": "v"}, {"api_key":"v"}.
    re.compile(
        r"(?i)(?P<keep>\"[\w-]*(password|passwd|token|secret|api[-_]?key|auth)[\w-]*\"\s*:\s*)"
        r"\"[^\"]*\""
    ),
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


# Programs whose effect the rules can't bound: without an engine to judge
# them, they ask. `git` is allowed for the subcommands below only.
RISKY_PROGRAMS = frozenset(
    {
        "dd",
        "mkfs",
        "shred",
        "truncate",
        "terraform",
        "kubectl",
        "helm",
        "psql",
        "mysql",
        "sqlite3",
        "mongo",
        "redis-cli",
        "aws",
        "gcloud",
        "az",
        "xargs",
        "eval",
        "sh",
        "bash",
        "zsh",
        "env",
        "sudo",
        "doas",
    }
)
SAFE_GIT = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "add",
        "commit",
        "fetch",
        "pull",
        "switch",
        "stash",
        "branch",
        "tag",
        "merge",
        "rebase",
        "rev-parse",
        "remote",
        "blame",
        "worktree",
        "init",
        "clone",
        "push",
        "reset",
        "clean",
        "grep",
        "ls-files",
        "describe",
        "cherry-pick",
    }
)
# Arguments that turn an otherwise routine git subcommand destructive:
# branch -D, tag -d, stash drop/clear, switch --discard-changes, worktree remove.
DESTRUCTIVE_GIT_ARGS = frozenset(
    {
        "-D",
        "-d",
        "--delete",
        "-M",
        "-f",
        "--force",
        "--discard-changes",
        "drop",
        "clear",
        "remove",
        "prune",
        "--prune",
        "-x",
        "--hard",
    }
)


def rules_only_check(command: str) -> tuple[Outcome, str, str] | None:
    """With no engine, fail closed: ask for what the rules can't vouch for.

    Called only after check_command found nothing. Programs with unbounded
    effects ask; so do git subcommands outside a known set, and any
    destructive command whose shell the rules can't see through.
    """
    if OPAQUE.search(command) and DESTRUCTIVE.search(command):
        return "ask", "rules-only", "no engine to judge an opaque destructive command"
    command = GIT_GLOBALS.sub("git ", command)
    # Wrappers stay visible here: `sudo make deploy` asks because of sudo.
    for argv in _commands(command, unwrap=False) or []:
        progs = [Path(w).name for w in argv]
        risky = next(
            (p for p in progs if p in RISKY_PROGRAMS or p.startswith("mkfs")), None
        )
        if risky and progs.index(risky) <= _first_command_index(argv):
            return "ask", "rules-only", f"no engine configured to judge {risky!r}"
        if "git" in progs:
            i = progs.index("git")
            sub = next((a for a in argv[i + 1 :] if not a.startswith("-")), None)
            args = set(argv[i + 1 :])
            if sub not in SAFE_GIT or args & DESTRUCTIVE_GIT_ARGS:
                return "ask", "rules-only", f"no engine configured to judge 'git {sub}'"
    return None


def _first_command_index(argv: list[str]) -> int:
    """Index of the command a wrapper chain finally runs."""
    return len(argv) - len(_unwrap(argv))
