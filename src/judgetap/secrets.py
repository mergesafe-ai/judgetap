"""Engine keys: the environment first, then the OS keychain.

The keychain matters for the guard: agents often run hooks without the
user's shell environment, so an exported key may never reach the hook.
Keychain support is optional (`pip install 'judgetap[keychain]'`); without
it, or when the backend fails, lookups quietly fall back to None.
"""

from __future__ import annotations

import os

# Keys an engine reads through get_key. Saving any other name to the keychain
# would store something nothing reads.
KNOWN_KEYS = ("TYPESAFE_API_KEY",)
SERVICE = "judgetap"
OLD_SERVICE = "snapjudge"  # keys saved before the rename (#38)


def _keyring():
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def get_key(name: str) -> str | None:
    """$name if set, else the keychain entry for it, else None."""
    value = os.environ.get(name)
    if value:
        return value
    keyring = _keyring()
    if keyring is None:
        return None
    try:
        value = keyring.get_password(SERVICE, name)
        if value:
            return value
        value = keyring.get_password(OLD_SERVICE, name)
        if value:
            # Copy a pre-rename key to the new service once, so later lookups
            # take one keychain read. The old entry is left for the user.
            try:
                keyring.set_password(SERVICE, name, value)
            except Exception:  # noqa: BLE001, S110 -- migration is best effort
                pass
        return value or None
    except Exception:  # noqa: BLE001 -- a broken backend means "no key", never a crash
        return None


def set_key(name: str, value: str) -> None:
    """Save a key to the keychain. Raises RuntimeError if that isn't possible."""
    keyring = _keyring()
    if keyring is None:
        raise RuntimeError("keychain support needs: pip install 'judgetap[keychain]'")
    try:
        keyring.set_password(SERVICE, name, value)
    except Exception as err:
        raise RuntimeError(
            f"couldn't save to the keychain: {type(err).__name__}"
        ) from err


def key_source(name: str) -> str | None:
    """Where a key would come from: 'env', 'keychain', or None. Never the value."""
    if os.environ.get(name):
        return "env"
    keyring = _keyring()
    if keyring is None:
        return None
    try:
        found = keyring.get_password(SERVICE, name) or keyring.get_password(
            OLD_SERVICE, name
        )
        return "keychain" if found else None
    except Exception:  # noqa: BLE001
        return None
