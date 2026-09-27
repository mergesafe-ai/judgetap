import io
import sys
import types

import pytest

from judgetap import secrets


class FakeKeyring(types.ModuleType):
    def __init__(self, fail=False):
        super().__init__("keyring")
        self.store = {}
        self.fail = fail

    def get_password(self, service, name):
        if self.fail:
            raise OSError("backend down")
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        if self.fail:
            raise OSError("backend down")
        self.store[(service, name)] = value


@pytest.fixture
def kr(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return fake


def test_env_wins_over_keychain(kr, monkeypatch):
    kr.store[("judgetap", "TYPESAFE_API_KEY")] = "from-keychain"
    assert secrets.get_key("TYPESAFE_API_KEY") == "from-keychain"
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-env")
    assert secrets.get_key("TYPESAFE_API_KEY") == "from-env"
    assert secrets.key_source("TYPESAFE_API_KEY") == "env"


def test_missing_keyring_is_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert secrets.get_key("TYPESAFE_API_KEY") is None
    with pytest.raises(RuntimeError, match=r"judgetap\[keychain\]"):
        secrets.set_key("TYPESAFE_API_KEY", "x")


def test_broken_backend_is_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring(fail=True))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert secrets.get_key("TYPESAFE_API_KEY") is None
    with pytest.raises(RuntimeError, match="OSError"):
        secrets.set_key("TYPESAFE_API_KEY", "x")


def test_jev_engine_uses_keychain_key(kr):
    from judgetap.engines.jev import JevEngine

    kr.store[("judgetap", "TYPESAFE_API_KEY")] = "kc-key"
    assert JevEngine()._api_key == "kc-key"
    assert JevEngine(api_key="explicit")._api_key == "explicit"


def test_hook_accepts_keychain_key(kr, tmp_path, monkeypatch):
    from judgetap.guard import hook

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    monkeypatch.setenv("JUDGETAP_ENGINE", "jev")
    with pytest.raises(RuntimeError, match="keychain"):
        hook._engine()
    kr.store[("judgetap", "TYPESAFE_API_KEY")] = "kc-key"
    assert hook._engine().name == "jev"


def test_keys_set_and_status(kr, monkeypatch, capsys):
    from judgetap.cli import main

    monkeypatch.setattr("getpass.getpass", lambda prompt: "s3cret-value")
    assert main(["keys", "set", "TYPESAFE_API_KEY"]) == 0
    assert kr.store[("judgetap", "TYPESAFE_API_KEY")] == "s3cret-value"
    main(["keys", "status"])
    out = capsys.readouterr().out
    assert "TYPESAFE_API_KEY: keychain" in out and "s3cret-value" not in out


def test_install_non_tty_does_not_save_and_never_writes_key(
    kr, tmp_path, monkeypatch, capsys
):
    from judgetap.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-key-value")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    main(["guard", "install", "--scope", "project"])
    out = capsys.readouterr().out
    assert "no terminal to ask" in out and not kr.store
    assert "env-key-value" not in (tmp_path / "home" / "guard.toml").read_text()


def test_install_tty_offers_to_save(kr, monkeypatch, capsys):
    from judgetap.cli import _offer_keychain

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setenv("TYPESAFE_API_KEY", "env-key-value")
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    _offer_keychain(Tty())
    assert kr.store[("judgetap", "TYPESAFE_API_KEY")] == "env-key-value"
    assert "env-key-value" not in capsys.readouterr().out


def test_ipv6_loopback_spec_installs(tmp_path):
    from judgetap.guard.install import write_engine

    write_engine(tmp_path, "jev@http://[::1]:8000")
    assert 'engine = "jev@http://[::1]:8000"' in (tmp_path / "guard.toml").read_text()


def test_detect_engine_finds_a_keychain_only_key(kr, monkeypatch):
    from judgetap.guard.install import detect_engine

    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)
    kr.store[("judgetap", "TYPESAFE_API_KEY")] = "k"
    assert detect_engine(probe=lambda: False)[0] == "jev"


def test_hook_reads_the_keychain_once(kr, monkeypatch, tmp_path):
    from judgetap.guard import hook

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    monkeypatch.setenv("JUDGETAP_ENGINE", "jev")
    kr.store[("judgetap", "TYPESAFE_API_KEY")] = "k"
    reads = []
    real = kr.get_password
    kr.get_password = lambda service, name: reads.append(name) or real(service, name)
    assert hook._engine() is not None
    assert reads == ["TYPESAFE_API_KEY"]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("jev", True),
        ("typesafe:https://gw.example", True),
        ("jev@http://127.0.0.1:8000", False),
        ("agentjev", False),
    ],
)
def test_keychain_offer_covers_remote_typesafe(kr, monkeypatch, spec, expected):
    from judgetap.cli import _uses_typesafe_key

    monkeypatch.setenv("TYPESAFE_API_KEY", "k")  # the install-time case: key present
    from judgetap.engines import load

    assert _uses_typesafe_key(load(spec)) is expected


def test_old_keychain_service_is_read(kr):
    kr.store[("snapjudge", "TYPESAFE_API_KEY")] = "old"
    assert secrets.get_key("TYPESAFE_API_KEY") == "old"
    kr.store[("judgetap", "TYPESAFE_API_KEY")] = "new"
    assert secrets.get_key("TYPESAFE_API_KEY") == "new"
