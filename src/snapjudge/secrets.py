"""Engine keys: the environment first, then the OS keychain.

The keychain matters for the guard: agents often run hooks without the
user's shell environment, so an exported key may never reach the hook.
Keychain support is optional (`pip install 'snapjudge[keychain]'`); without
it, or when the backend fails, lookups quietly fall back to None.
"""

from __future__ import annotations

import os

SERVICE = "snapjudge"


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
        return keyring.get_password(SERVICE, name) or None
    except Exception:  # noqa: BLE001 -- a broken backend means "no key", never a crash
        return None


def set_key(name: str, value: str) -> None:
    """Save a key to the keychain. Raises RuntimeError if that isn't possible."""
    keyring = _keyring()
    if keyring is None:
        raise RuntimeError("keychain support needs: pip install 'snapjudge[keychain]'")
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
        return "keychain" if keyring.get_password(SERVICE, name) else None
    except Exception:  # noqa: BLE001
        return None
