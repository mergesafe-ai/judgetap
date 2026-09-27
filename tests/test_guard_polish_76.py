"""Fixes for the P2s left on #71 (issue #76)."""

import io
import json

import pytest

from judgetap.guard import hook


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)
    monkeypatch.delenv("SNAPJUDGE_ENGINE", raising=False)


def run(payload, agent="claude-code"):
    out, err = io.StringIO(), io.StringIO()
    code = hook.run(io.StringIO(json.dumps(payload)), out, err, agent=agent)
    return (
        code,
        (json.loads(out.getvalue()) if out.getvalue() else None),
        err.getvalue(),
    )


# P2 1, 2 and 5: missing or malformed commands from Cursor and Codex ask.
def test_cursor_missing_command_asks(tmp_path):
    _, out, _ = run({"cwd": str(tmp_path)}, agent="cursor")
    assert out["permission"] == "ask"


@pytest.mark.parametrize(
    "tool_input",
    [{}, [], "ls", {"cmd": 5}, {"cmd": []}],
)
def test_codex_malformed_tool_input_asks(tmp_path, tool_input):
    code, _, err = run(
        {"tool_name": "exec_command", "tool_input": tool_input, "cwd": str(tmp_path)},
        agent="codex",
    )
    assert code == 2 and "couldn't be read" in err


# P2 7: a non-string or blank Bash command isn't readable.
@pytest.mark.parametrize("command", [[], {"a": 1}, 5, "", "   "])
def test_non_string_command_asks(tmp_path, command):
    _, out, _ = run(
        {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path)}
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


# P2 8: a write without a readable destination asks.
@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_write_without_destination_asks(tmp_path, tool):
    _, out, _ = run(
        {
            "tool_name": tool,
            "tool_input": {"content": "ordinary text"},
            "cwd": str(tmp_path),
        }
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "destination" in out["hookSpecificOutput"]["permissionDecisionReason"]


# P2 3: example and backup files aren't the active configuration.
@pytest.mark.parametrize(
    ("command", "asks"),
    [
        ("cat guard.toml.example", False),
        ("cp .claude/settings.json.backup /tmp/x", False),
        ("cat guard.toml", True),
        ("echo x >> .claude/settings.local.json", True),
    ],
)
def test_mention_needs_the_whole_config_name(tmp_path, command, asks):
    from judgetap.guard.rules import check_command_writes

    assert (check_command_writes(command, tmp_path) is not None) is asks


# P2 4: the repo config is parsed once per action.
def test_repo_config_parsed_once(tmp_path, monkeypatch):
    import tomllib

    (tmp_path / "guard.toml").write_text('[[rule]]\npattern = "x"\noutcome = "allow"\n')
    calls = []
    real = tomllib.load
    monkeypatch.setattr(tomllib, "load", lambda fh: calls.append(1) or real(fh))
    run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "cwd": str(tmp_path),
            "session_id": "s1",
        }
    )
    assert len(calls) == 1  # ~/.judgetap/guard.toml doesn't exist here


# P2 6: the ignored-allow warning survives an engine error and a Codex deny.
def test_repo_warning_not_lost_behind_engine_error(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_ENGINE", "nonsense")
    (tmp_path / "guard.toml").write_text('[[rule]]\npattern = "x"\noutcome = "allow"\n')
    _, out, _ = run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "make x"},
            "cwd": str(tmp_path),
            "session_id": "s2",
        }
    )
    msg = out["systemMessage"]
    assert "ignored 1 allow rule" in msg and "nonsense" in msg


def test_repo_warning_shown_on_codex_deny(tmp_path):
    (tmp_path / "guard.toml").write_text('[[rule]]\npattern = "x"\noutcome = "allow"\n')
    code, _, err = run(
        {
            "tool_name": "exec_command",
            "tool_input": {"cmd": "git push --force"},
            "cwd": str(tmp_path),
            "session_id": "s3",
        },
        agent="codex",
    )
    assert code == 2 and "ignored 1 allow rule" in err


# P2 9: README describes the guard-config check.
def test_readme_mentions_guard_config_check():
    from pathlib import Path

    readme = (Path(__file__).parent.parent / "README.md").read_text()
    assert "writes to the guard's own configuration" in readme
