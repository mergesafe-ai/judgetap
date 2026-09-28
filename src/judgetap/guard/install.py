"""Add or remove the guard hook in Claude Code's settings.json."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from judgetap.errors import JudgetapError

HOOK_COMMAND = "judgetap guard hook"
POST_COMMAND = "judgetap guard post"  # loop detection, Claude Code only
PRUNE_COMMAND = "judgetap guard prune --agent"  # shadow pruning, Cursor/Codex
STOP_COMMAND = "judgetap guard stop"  # opt-in task-done check, Claude Code only
MATCHER = "Bash|Write|Edit|MultiEdit"


AGENT_DIRS = {"claude-code": ".claude", "cursor": ".cursor", "codex": ".codex"}


def settings_path(scope: str, cwd: Path, agent: str = "claude-code") -> Path:
    base = Path.home() if scope == "user" else cwd
    name = "settings.json" if agent == "claude-code" else "hooks.json"
    return base / AGENT_DIRS[agent] / name


def detected_agents() -> list[str]:
    """Agents with a config directory in the user's home."""
    return [a for a, d in AGENT_DIRS.items() if (Path.home() / d).is_dir()]


def hook_command(agent: str) -> str:
    return HOOK_COMMAND if agent == "claude-code" else f"{HOOK_COMMAND} --agent {agent}"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text()
    return json.loads(text) if text.strip() else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    path.write_text(json.dumps(data, indent=2) + "\n")


OLD_HOOK_COMMAND = "snapjudge guard hook"  # written by installs before #38


def _ours(command: str | None) -> bool:
    """Exactly a command this installer writes (now or under the old name);
    anything edited is the user's."""
    return command in (
        POST_COMMAND,
        STOP_COMMAND,
        f"{PRUNE_COMMAND} cursor",
        f"{PRUNE_COMMAND} codex",
    ) or command in {
        base + suffix
        for base in (HOOK_COMMAND, OLD_HOOK_COMMAND)
        for suffix in ("", " --agent cursor", " --agent codex")
    }


def _upgrade_old(data: dict) -> bool:
    """Rewrite old-name hook commands to the new name in place."""
    changed = False
    for entries in data.get("hooks", {}).values():
        for entry in entries:
            for holder in [entry, *entry.get("hooks", [])]:
                cmd = holder.get("command")
                if (
                    isinstance(cmd, str)
                    and cmd.startswith(OLD_HOOK_COMMAND)
                    and _ours(cmd)
                ):
                    holder["command"] = HOOK_COMMAND + cmd[len(OLD_HOOK_COMMAND) :]
                    changed = True
    return changed


def _is_ours(entry: dict) -> bool:
    if _ours(entry.get("command")):  # Cursor's flat {"command": ...} entries
        return True
    return any(_ours(h.get("command")) for h in entry.get("hooks", []))


def _event(agent: str) -> str:
    return "beforeShellExecution" if agent == "cursor" else "PreToolUse"


def _events(agent: str, with_stop: bool = False) -> list[str]:
    if agent == "claude-code":
        # Loop detection needs both: failures arrive only on
        # PostToolUseFailure, successes (which end a streak) on PostToolUse.
        return [
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            *(["Stop"] if with_stop else []),
        ]
    # Shadow pruning (#112): Cursor's afterShellExecution carries the shell
    # output; Codex's PostToolUse carries every supported tool's output.
    return [_event(agent), _post_event(agent)]


def _post_event(agent: str) -> str:
    return "afterShellExecution" if agent == "cursor" else "PostToolUse"


def _entry(agent: str, event: str) -> dict:
    if agent != "claude-code" and event == _post_event(agent):
        command = f"{PRUNE_COMMAND} {agent}"
        if agent == "cursor":
            return {"command": command}
        return {"hooks": [{"type": "command", "command": command}]}  # every tool
    if agent == "cursor":
        return {"command": hook_command(agent)}
    if event == "Stop":  # Stop takes no matcher
        return {"hooks": [{"type": "command", "command": STOP_COMMAND}]}
    command = (
        POST_COMMAND
        if event in ("PostToolUse", "PostToolUseFailure")
        else hook_command(agent)
    )
    # Codex's PreToolUse fires for shell only today; the matcher says so. It
    # tolerates padding because normalise() strips the name before matching.
    matcher = r"^\s*(exec_command|shell|Bash)\s*$" if agent == "codex" else MATCHER
    return {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}


def install(path: Path, agent: str = "claude-code", *, with_stop: bool = False) -> bool:
    """Add any missing guard hooks. True if the file changed. `with_stop`
    adds the experimental task-done check (Claude Code only, off by default)."""
    data = _load(path)
    if agent == "cursor":
        data.setdefault("version", 1)
    changed = _upgrade_old(data)  # an install from before the rename
    for event in _events(agent, with_stop):
        entries = data.setdefault("hooks", {}).setdefault(event, [])
        if not any(_is_ours(e) for e in entries):
            entries.append(_entry(agent, event))
            changed = True
    if changed:
        _write(path, data)
    return changed


def uninstall(path: Path, agent: str = "claude-code") -> bool:
    data = _load(path)
    removed = False
    for event in _events(agent, with_stop=True):  # remove every hook we may have added
        entries = data.get("hooks", {}).get(event, [])
        if not any(_is_ours(e) for e in entries):
            continue
        removed = True
        kept = []
        for entry in entries:
            if "hooks" not in entry:  # Cursor's flat entries
                if not _ours(entry.get("command")):
                    kept.append(entry)
                continue
            # Remove only our hook; keep any others that share its matcher group.
            hooks = [h for h in entry.get("hooks", []) if not _ours(h.get("command"))]
            if hooks:
                kept.append({**entry, "hooks": hooks})
        data["hooks"][event] = kept
        if not kept:
            del data["hooks"][event]
    if not removed:
        return False
    if not data.get("hooks"):
        data.pop("hooks", None)
    _write(path, data)
    return True


def detect_engine(probe=None) -> tuple[str | None, str]:
    """Pick an engine the user already has, and say why.

    Order: an explicit $JUDGETAP_ENGINE, a TypeSafe key (Jev), a local
    AgentJev server on its default port. Nothing is downloaded and no key is
    stored: without one of these the guard runs rules only, which fail closed.
    """
    from judgetap._compat import env, env_source

    if env("ENGINE"):
        return env("ENGINE"), f"from {env_source('ENGINE')}"
    from judgetap.secrets import get_key

    if get_key("TYPESAFE_API_KEY"):  # env or OS keychain
        return "jev", "found TYPESAFE_API_KEY"
    probe = probe or agentjev_up
    if probe():
        return "agentjev", "AgentJev server answering on 127.0.0.1:8149"
    return None, "no engine found; rules only (they ask when unsure)"


def agentjev_up(
    url: str = "http://127.0.0.1:8149", timeout: float = 0.3, transport=None
) -> bool:
    """True only if the port speaks AgentJev's API, not just accepts a
    connection: a one-question request must come back as a decision."""
    from judgetap.engines.agentjev import AgentJevEngine
    from judgetap.types import Question

    kwargs = {"timeout": timeout} | ({"transport": transport} if transport else {})
    try:
        AgentJevEngine(url, **kwargs).decide(
            [Question.yesno("Is this a probe?")], "probe"
        )
    except Exception:  # noqa: BLE001 -- anything else on the port is "not AgentJev"
        return False
    return True


SPEC_PATTERN = re.compile(
    r"[A-Za-z0-9:_./@+\[\]?-]+"
)  # [ ] for IPv6 hosts, ? for llm options


KNOWN_ENGINES = frozenset(
    {"jev", "llm", "laya", "julia", "agentjev", "typesafe", "gliner"}
)


# Engines that load a model into the calling process. The guard hook is a new
# process per action, so these would reload the model on every guarded action.
IN_PROCESS_ENGINES = frozenset({"gliner"})


def in_process_error(spec: str) -> str | None:
    name = spec.partition(":")[0].partition("@")[0]
    if name in IN_PROCESS_ENGINES:
        return (
            f"{name} loads its model in-process, and the guard hook is a new process "
            "per action; use a server engine for the guard"
        )
    return None


def validate_engine(spec: str):
    """Check a spec fully and return the engine it builds (no network, no key
    needed). Raises ValueError for anything that can't be saved."""
    if not SPEC_PATTERN.fullmatch(spec):
        raise ValueError(f"not a valid engine spec: {spec!r}")
    if spec.partition(":")[0].partition("@")[0] not in KNOWN_ENGINES:
        raise ValueError(
            f"unknown engine {spec!r}; known: {', '.join(sorted(KNOWN_ENGINES))}"
        )
    if error := in_process_error(spec):
        raise ValueError(error)
    from judgetap.engines import load

    try:
        return load(spec)
    except JudgetapError as err:
        raise ValueError(str(err)) from err


def write_engine(home: Path, spec: str, *, engine=None) -> Path:
    """Record the engine in guard.toml, keeping any user rules already there.
    Pass `engine` when the caller already validated the spec, to build it once."""
    if engine is None:
        validate_engine(spec)
    path = home / "guard.toml"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    # Only the top-level `engine` key, before the first [table]: keys like
    # `engine_options` and an `engine` inside a table stay.
    first_table = next(
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("[")), len(lines)
    )
    lines = [
        ln
        for i, ln in enumerate(lines)
        if i >= first_table or not re.match(r"\s*engine\s*=", ln)
    ]
    path.write_text(f'engine = "{spec}"\n' + "\n".join(lines) + ("\n" if lines else ""))
    return path
