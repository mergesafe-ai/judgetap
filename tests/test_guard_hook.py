import io
import json

import pytest

from snapjudge.guard import hook
from snapjudge.guard.install import HOOK_COMMAND, install, uninstall


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SNAPJUDGE_ENGINE", raising=False)


def run(payload):
    out = io.StringIO()
    assert hook.run(io.StringIO(json.dumps(payload)), out) == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


def bash(command, cwd):
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
        "session_id": "s1",
    }


def test_hold_becomes_deny_with_reason(tmp_path):
    out = run(bash("git push --force", tmp_path))
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "force-push" in decision["permissionDecisionReason"]


def test_allow_prints_nothing_so_normal_permissions_apply(tmp_path):
    assert run(bash("make test", tmp_path)) is None


def test_ask_becomes_ask(tmp_path):
    out = run(bash("git reset --hard", tmp_path))
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_unguarded_tools_are_ignored(tmp_path):
    assert (
        run({"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}) is None
    )


def test_secret_write_is_held(tmp_path):
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "x.py", "content": "k='AKIAABCDEFGHIJKLMNOP'"},
        "cwd": str(tmp_path),
    }
    assert run(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_log_records_decision_without_file_contents(tmp_path):
    run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "x.py", "content": "SECRET_BODY"},
            "cwd": str(tmp_path),
        }
    )
    run(bash("git push -f", tmp_path))
    lines = (tmp_path / "home" / "guard.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["outcome"] for r in records] == ["allow", "hold"]
    assert "SECRET_BODY" not in "".join(lines)


def test_garbage_input_never_blocks():
    out = io.StringIO()
    assert hook.run(io.StringIO("not json"), out) == 0
    assert "failed and allowed" in json.loads(out.getvalue())["systemMessage"]


def test_bad_engine_spec_degrades_to_rules_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPJUDGE_ENGINE", "nonsense")
    out = run(bash("make deploy", tmp_path))
    assert "systemMessage" in out


def test_last_user_message_from_transcript(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"type": "user", "message": {"content": "fix the typo"}},
                {"type": "assistant", "message": {"content": "ok"}},
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "content": "x"}]},
                },
                {
                    "type": "user",
                    "message": {
                        "content": [{"type": "text", "text": "and add a test"}]
                    },
                },
            ]
        )
    )
    assert hook.last_user_message(str(t)) == "and add a test"


def test_install_is_idempotent_and_uninstall_restores(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "rtk"}],
                        }
                    ]
                },
            }
        )
    )
    assert install(path) is True
    assert install(path) is False
    data = json.loads(path.read_text())
    assert [h["hooks"][0]["command"] for h in data["hooks"]["PreToolUse"]] == [
        "rtk",
        HOOK_COMMAND,
    ]
    assert uninstall(path) is True
    assert (
        json.loads(path.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        == "rtk"
    )
    assert data["theme"] == "dark"
