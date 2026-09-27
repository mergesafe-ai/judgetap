import io
import json

import pytest

from judgetap.guard import hook
from judgetap.guard.install import HOOK_COMMAND, install, settings_path, uninstall


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)


def run(payload, agent):
    out, err = io.StringIO(), io.StringIO()
    code = hook.run(io.StringIO(json.dumps(payload)), out, err, agent=agent)
    return (
        code,
        (json.loads(out.getvalue()) if out.getvalue() else None),
        err.getvalue(),
    )


def test_cursor_deny_ask_and_allow(tmp_path):
    code, out, _ = run({"command": "git push --force", "cwd": str(tmp_path)}, "cursor")
    assert (
        code == 0
        and out["permission"] == "deny"
        and "force-push" in out["agent_message"]
    )
    _, out, _ = run({"command": "git reset --hard", "cwd": str(tmp_path)}, "cursor")
    assert out["permission"] == "ask"
    _, out, _ = run({"command": "make test", "cwd": str(tmp_path)}, "cursor")
    assert out == {"permission": "allow"}


def test_cursor_always_answers_even_on_bad_input():
    out = io.StringIO()
    hook.run(io.StringIO("nope"), out, io.StringIO(), agent="cursor")
    assert json.loads(out.getvalue())["permission"] == "allow"


def test_codex_blocks_with_exit_2_and_stderr(tmp_path):
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git push -f"},
        "cwd": str(tmp_path),
    }
    code, out, err = run(payload, "codex")
    assert code == 2 and out is None and "force-push" in err
    code, _, err = run(
        {**payload, "tool_input": {"command": "git reset --hard"}}, "codex"
    )
    assert code == 2 and "Ask the user" in err
    code, out, _ = run({**payload, "tool_input": {"command": "make test"}}, "codex")
    assert (code, out) == (0, {})


def test_settings_paths(tmp_path):
    assert (
        settings_path("project", tmp_path, "cursor")
        == tmp_path / ".cursor" / "hooks.json"
    )
    assert (
        settings_path("project", tmp_path, "codex")
        == tmp_path / ".codex" / "hooks.json"
    )
    assert settings_path("project", tmp_path) == tmp_path / ".claude" / "settings.json"


def test_cursor_install_format_and_uninstall(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {"beforeShellExecution": [{"command": "./audit.sh"}]},
            }
        )
    )
    assert install(path, "cursor") is True and install(path, "cursor") is False
    entries = json.loads(path.read_text())["hooks"]["beforeShellExecution"]
    assert entries == [
        {"command": "./audit.sh"},
        {"command": f"{HOOK_COMMAND} --agent cursor"},
    ]
    assert uninstall(path, "cursor") is True
    assert json.loads(path.read_text())["hooks"]["beforeShellExecution"] == [
        {"command": "./audit.sh"}
    ]


def test_codex_install_uses_shell_matcher(tmp_path):
    path = tmp_path / "hooks.json"
    install(path, "codex")
    entry = json.loads(path.read_text())["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "^(exec_command|shell|Bash)$"
    assert entry["hooks"][0]["command"] == f"{HOOK_COMMAND} --agent codex"
    assert uninstall(path, "codex") is True


def test_cli_install_all_detects_agents(tmp_path, monkeypatch, capsys):
    from judgetap.cli import main

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".codex").mkdir()
    main(["guard", "install", "--for", "all"])
    out = capsys.readouterr().out
    assert (
        "cursor: installed" in out
        and "codex: installed" in out
        and "claude-code" not in out
    )
    assert "/hooks" in out


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "exec_command", "tool_input": {"cmd": "git push -f"}},
        {"tool_name": "exec_command", "tool_input": {"cmd": ["git", "push", "-f"]}},
        {"tool_name": "Bash", "tool_input": {"command": "git push -f"}},
    ],
)
def test_codex_exec_command_payloads_are_guarded(tmp_path, payload):
    code, _, err = run({**payload, "cwd": str(tmp_path)}, "codex")
    assert code == 2 and "force-push" in err


def test_uninstall_leaves_edited_hook_commands(tmp_path):
    path = tmp_path / "hooks.json"
    edited = f"{HOOK_COMMAND} --agent cursor && ./audit.sh"
    path.write_text(
        json.dumps(
            {"version": 1, "hooks": {"beforeShellExecution": [{"command": edited}]}}
        )
    )
    assert uninstall(path, "cursor") is False
    assert json.loads(path.read_text())["hooks"]["beforeShellExecution"] == [
        {"command": edited}
    ]
