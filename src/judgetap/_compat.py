"""Reading settings left by the previous name, snapjudge.

The project was renamed from snapjudge (#38). For one release, anything a
user set up under the old name keeps working: SNAPJUDGE_* env vars, the
~/.snapjudge directory, old keychain entries and old hook commands.
"""

from __future__ import annotations

import os
from pathlib import Path

OLD_PREFIX, NEW_PREFIX = "SNAPJUDGE_", "JUDGETAP_"


def env(name: str) -> str | None:
    """$JUDGETAP_<name>, else the old $SNAPJUDGE_<name>. The new name wins
    whenever it is set, even to an empty string (which reads as unset)."""
    if NEW_PREFIX + name in os.environ:
        return os.environ[NEW_PREFIX + name] or None
    return os.environ.get(OLD_PREFIX + name) or None


def default_home() -> Path:
    """~/.judgetap, or an existing ~/.snapjudge when the new one isn't there yet."""
    new, old = Path.home() / ".judgetap", Path.home() / ".snapjudge"
    return old if not new.exists() and old.exists() else new


def env_source(name: str) -> str | None:
    """Which variable `env(name)` read: '$JUDGETAP_<name>' or '$SNAPJUDGE_<name>'."""
    if NEW_PREFIX + name in os.environ:  # set, even empty: the old name is ignored
        return f"${NEW_PREFIX}{name}" if os.environ[NEW_PREFIX + name] else None
    return f"${OLD_PREFIX}{name}" if os.environ.get(OLD_PREFIX + name) else None
