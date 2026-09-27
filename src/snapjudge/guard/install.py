"""Add or remove the guard hook in Claude Code's settings.json."""

from __future__ import annotations

import json
import os
import re
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
    if not any(_is_ours(e) for e in pre):
        return False
    kept = []
    for entry in pre:
        # Remove only our hook; keep any others that share its matcher group.
        hooks = [h for h in entry.get("hooks", []) if h.get("command") != HOOK_COMMAND]
        if hooks:
            kept.append({**entry, "hooks": hooks})
    data["hooks"]["PreToolUse"] = kept
    if not kept:
        del data["hooks"]["PreToolUse"]
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
    if os.environ.get("TYPESAFE_API_KEY"):
        return "jev", "found TYPESAFE_API_KEY"
    probe = probe or agentjev_up
    if probe():
        return "agentjev", "AgentJev server answering on 127.0.0.1:8149"
    return None, "no engine found; rules only (they ask when unsure)"


def agentjev_up(
    url: str = "http://127.0.0.1:8149", timeout: float = 0.5, transport=None
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


SPEC_PATTERN = re.compile(r"[A-Za-z0-9:_./@+-]+")


def write_engine(home: Path, spec: str) -> Path:
    """Record the engine in guard.toml, keeping any user rules already there."""
    if not SPEC_PATTERN.fullmatch(spec):
        raise ValueError(f"not a valid engine spec: {spec!r}")
    path = home / "guard.toml"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [ln for ln in lines if not ln.strip().startswith("engine")]
    path.write_text(f'engine = "{spec}"\n' + "\n".join(lines) + ("\n" if lines else ""))
    return path
