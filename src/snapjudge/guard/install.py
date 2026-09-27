"""Add or remove the guard hook in Claude Code's settings.json."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from snapjudge.errors import SnapjudgeError

HOOK_COMMAND = "snapjudge guard hook"
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


def _ours(command: str | None) -> bool:
    """Exactly a command this installer writes; anything edited is the user's."""
    return command in {
        HOOK_COMMAND,
        *(f"{HOOK_COMMAND} --agent {a}" for a in ("cursor", "codex")),
    }


def _is_ours(entry: dict) -> bool:
    if _ours(entry.get("command")):  # Cursor's flat {"command": ...} entries
        return True
    return any(_ours(h.get("command")) for h in entry.get("hooks", []))


def _event(agent: str) -> str:
    return "beforeShellExecution" if agent == "cursor" else "PreToolUse"


def install(path: Path, agent: str = "claude-code") -> bool:
    """Return True if the hook was added, False if it was already there."""
    data = _load(path)
    if agent == "cursor":
        data.setdefault("version", 1)
    entries = data.setdefault("hooks", {}).setdefault(_event(agent), [])
    if any(_is_ours(e) for e in entries):
        return False
    command = hook_command(agent)
    if agent == "cursor":
        entries.append({"command": command})
    else:
        # Codex's PreToolUse fires for shell only today; the matcher says so.
        matcher = "^(exec_command|shell|Bash)$" if agent == "codex" else MATCHER
        entries.append(
            {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}
        )
    _write(path, data)
    return True


def uninstall(path: Path, agent: str = "claude-code") -> bool:
    data = _load(path)
    event = _event(agent)
    pre = data.get("hooks", {}).get(event, [])
    if not any(_is_ours(e) for e in pre):
        return False
    kept = []
    for entry in pre:
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
    if not data["hooks"]:
        del data["hooks"]
    _write(path, data)
    return True


def detect_engine(probe=None) -> tuple[str | None, str]:
    """Pick an engine the user already has, and say why.

    Order: an explicit $SNAPJUDGE_ENGINE, a TypeSafe key (Jev), a local
    AgentJev server on its default port. Nothing is downloaded and no key is
    stored: without one of these the guard runs rules only, which fail closed.
    """
    if os.environ.get("SNAPJUDGE_ENGINE"):
        return os.environ["SNAPJUDGE_ENGINE"], "from $SNAPJUDGE_ENGINE"
    from snapjudge.secrets import get_key

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
    from snapjudge.engines.agentjev import AgentJevEngine
    from snapjudge.types import Question

    kwargs = {"timeout": timeout} | ({"transport": transport} if transport else {})
    try:
        AgentJevEngine(url, **kwargs).decide(
            [Question.yesno("Is this a probe?")], "probe"
        )
    except Exception:  # noqa: BLE001 -- anything else on the port is "not AgentJev"
        return False
    return True


SPEC_PATTERN = re.compile(r"[A-Za-z0-9:_./@+\[\]-]+")  # [ ] for IPv6 hosts


KNOWN_ENGINES = frozenset({"jev", "llm", "laya", "agentjev", "typesafe"})


def write_engine(home: Path, spec: str) -> Path:
    """Record the engine in guard.toml, keeping any user rules already there."""
    if not SPEC_PATTERN.fullmatch(spec):
        raise ValueError(f"not a valid engine spec: {spec!r}")
    if spec.partition(":")[0].partition("@")[0] not in KNOWN_ENGINES:
        raise ValueError(
            f"unknown engine {spec!r}; known: {', '.join(sorted(KNOWN_ENGINES))}"
        )
    from snapjudge.engines import load  # builds the engine: no network, no key needed

    try:
        load(spec)
    except SnapjudgeError as err:
        raise ValueError(str(err)) from err
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
