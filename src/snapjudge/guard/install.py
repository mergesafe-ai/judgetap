"""Add or remove the guard hook in Claude Code's settings.json."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

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
