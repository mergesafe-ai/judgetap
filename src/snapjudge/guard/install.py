"""Add or remove the guard hook in Claude Code's settings.json."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

HOOK_COMMAND = "snapjudge guard hook"
MATCHER = "Bash|Write|Edit|MultiEdit"


def settings_path(scope: str, cwd: Path) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    return cwd / ".claude" / "settings.json"


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


def _is_ours(entry: dict) -> bool:
    return any(h.get("command") == HOOK_COMMAND for h in entry.get("hooks", []))


def install(path: Path) -> bool:
    """Return True if the hook was added, False if it was already there."""
    data = _load(path)
    pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    if any(_is_ours(e) for e in pre):
        return False
    pre.append(
        {"matcher": MATCHER, "hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    )
    _write(path, data)
    return True


def uninstall(path: Path) -> bool:
    data = _load(path)
    pre = data.get("hooks", {}).get("PreToolUse", [])
    kept = [e for e in pre if not _is_ours(e)]
    if len(kept) == len(pre):
        return False
    data["hooks"]["PreToolUse"] = kept
    if not kept:
        del data["hooks"]["PreToolUse"]
    if not data["hooks"]:
        del data["hooks"]
    _write(path, data)
    return True
