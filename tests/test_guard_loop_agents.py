import io
import json

import pytest

from judgetap.guard import loop
from judgetap.guard.install import POST_COMMAND, install, uninstall


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))


def run(payload, agent):
    out = io.StringIO()
    assert loop.run(io.StringIO(json.dumps(payload)), out, agent=agent) == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


def cursor_failure(cmd="npm run build", err="Command failed: missing script"):
    return {
        "hook_event_name": "postToolUseFailure",
        "conversation_id": "c1",
        "tool_name": "Shell",
        "tool_input": {"command": cmd},
        "error_message": err,
        "failure_type": "error",
        "is_interrupt": False,
    }


def test_cursor_third_identical_failure_returns_additional_context():
    assert run(cursor_failure(), "cursor") is None
    assert run(cursor_failure(), "cursor") is None
    out = run(cursor_failure(), "cursor")
    assert "re-plan" in out["additional_context"]
    assert set(out) == {"additional_context"}


def test_cursor_post_tool_use_nonzero_exit_counts_and_success_resets():
    def post(code):
        return {
            "hook_event_name": "postToolUse",
            "conversation_id": "c2",
            "tool_name": "Shell",
            "tool_input": {"command": "make"},
            "tool_output": json.dumps({"exitCode": code, "stdout": "boom"}),
        }

    run(post(2), "cursor")
    run(post(2), "cursor")
    run(post(0), "cursor")  # success ends the streak
    assert run(post(2), "cursor") is None
    run(post(2), "cursor")
    assert "additional_context" in run(post(2), "cursor")


def test_cursor_denials_and_interrupts_are_not_loops():
    for _ in range(4):
        denied = cursor_failure() | {"failure_type": "permission_denied"}
        assert run(denied, "cursor") is None
        assert run(cursor_failure() | {"is_interrupt": True}, "cursor") is None


def codex_bash(resp, cmd=("npm", "test")):
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "x1",
        "tool_name": "Bash",
        "tool_input": {"command": list(cmd)},
        "tool_response": resp,
    }


def test_codex_nonzero_exit_loops_with_additional_context():
    resp = "Exit code: 1\nWall time: 0.4 seconds\nOutput:\nFAIL src/a.test.ts"
    assert run(codex_bash(resp), "codex") is None
    assert run(codex_bash(resp), "codex") is None
    out = run(codex_bash(resp), "codex")
    assert "re-plan" in out["hookSpecificOutput"]["additionalContext"]
    ok = "Exit code: 0\nWall time: 0.1 seconds\nOutput:\nok"
    for _ in range(4):
        assert run(codex_bash(ok), "codex") is None


def test_codex_apply_patch_maps_to_file_path():
    patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** End Patch"
    p = loop.normalise_post(
        {"tool_name": "apply_patch", "tool_input": {"command": patch}}, "codex"
    )
    assert p["tool_name"] == "Edit" and p["tool_input"]["file_path"] == "src/a.py"


def test_bad_payloads_never_raise():
    for agent in ("cursor", "codex"):
        assert run([1, 2], agent) is None
        assert run({"tool_input": "nope", "tool_name": 3}, agent) is None


@pytest.mark.parametrize(
    ("agent", "events"),
    [
        ("cursor", ["postToolUse", "postToolUseFailure"]),
        ("codex", ["PostToolUse"]),
    ],
)
def test_install_adds_post_hooks_and_uninstall_removes_them(tmp_path, agent, events):
    path = tmp_path / "hooks.json"
    assert install(path, agent) is True and install(path, agent) is False
    hooks = json.loads(path.read_text())["hooks"]
    for event in events:
        entry = hooks[event][0]
        cmd = entry.get("command") or entry["hooks"][0]["command"]
        assert cmd == f"{POST_COMMAND} --agent {agent}"
    assert uninstall(path, agent) is True
    assert "hooks" not in json.loads(path.read_text())
