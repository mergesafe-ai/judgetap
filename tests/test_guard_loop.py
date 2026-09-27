import io
import json
import stat

import pytest

from judgetap.guard import loop
from judgetap.guard.install import POST_COMMAND, install, uninstall


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))


def call(
    command, *, fail=True, err="npm ERR! missing script: build", session="s1", code=1
):
    payload = {
        "session_id": session,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {
            "stdout": "",
            "stderr": err if fail else "",
            "exit_code": code if fail else 0,
        },
    }
    out = io.StringIO()
    assert loop.run(io.StringIO(json.dumps(payload)), out) == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


def test_third_identical_failure_adds_a_note():
    assert call("npm run build") is None
    assert call("npm run build") is None
    out = call("npm run build")
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUse"
    assert (
        "`npm run build` has now failed the same way 3 times"
        in ctx["additionalContext"]
    )
    assert "decision" not in out  # never blocks


def test_other_actions_in_between_still_count():
    for cmd in ["npm run build", "ls", "npm run build", "cat x", "npm run build"]:
        out = call(cmd, fail=cmd.startswith("npm"))
    assert out is not None


def test_different_errors_do_not_trigger():
    call("make", err="error A")
    call("make", err="error B")
    assert call("make", err="error C") is None


def test_numbers_in_errors_are_ignored():
    call("pytest", err="1 failed in 0.51s")
    call("pytest", err="1 failed in 0.49s")
    assert call("pytest", err="1 failed in 0.53s") is not None


def test_success_resets_the_streak():
    call("npm run build")
    call("npm run build")
    call("npm run build", fail=False)
    assert call("npm run build") is None


def test_outside_the_window_does_not_count():
    call("make")
    call("make")
    for i in range(8):
        call(f"echo {i}", fail=False)
    assert call("make") is None


@pytest.mark.parametrize("session", ["../../etc/x", "", None, "a" * 200])
def test_unsafe_session_ids_are_ignored(session, tmp_path):
    for _ in range(3):
        assert call("make", session=session) is None
    assert not (tmp_path / "home" / "sessions").exists() or not list(
        (tmp_path / "home" / "sessions").iterdir()
    )


def test_state_is_private_and_redacted(tmp_path):
    for _ in range(3):
        call("curl -u bob:hunter2 https://x")
    state = tmp_path / "home" / "sessions" / "s1.json"
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert "hunter2" not in state.read_text()
    log = (tmp_path / "home" / "guard.jsonl").read_text()
    assert (
        "hunter2" not in log and '"layer": "loop"' in log and '"outcome": "note"' in log
    )


def test_buffer_is_bounded(tmp_path):
    for i in range(40):
        call(f"echo {i}", fail=False)
    assert (
        len(json.loads((tmp_path / "home" / "sessions" / "s1.json").read_text()))
        == loop.BUFFER
    )


def test_bad_input_is_silent():
    out = io.StringIO()
    assert loop.run(io.StringIO("not json"), out) == 0
    assert out.getvalue() == ""


def test_install_adds_post_hook_and_uninstall_removes_both(tmp_path):
    path = tmp_path / "settings.json"
    assert install(path) is True
    hooks = json.loads(path.read_text())["hooks"]
    assert hooks["PostToolUse"][0]["hooks"][0]["command"] == POST_COMMAND
    assert install(path) is False
    # An install from before loop detection gets the new hook added.
    data = json.loads(path.read_text())
    del data["hooks"]["PostToolUse"]
    path.write_text(json.dumps(data))
    assert install(path) is True
    assert uninstall(path) is True
    assert "hooks" not in json.loads(path.read_text())


def test_other_agents_get_no_post_hook(tmp_path):
    path = tmp_path / "hooks.json"
    install(path, "codex")
    assert "PostToolUse" not in json.loads(path.read_text())["hooks"]


def test_exit_code_prefixed_string_is_a_failure():
    from judgetap.guard.loop import failure

    assert (
        failure({"tool_response": "Exit code 1\nnpm ERR! missing script: build"})
        is not None
    )
    assert failure({"tool_response": "Exit code 0\nok"}) is None


def test_empty_diagnostics_differ_by_exit_code():
    from judgetap.guard.loop import failure

    a = failure({"tool_response": {"exit_code": 1, "stderr": ""}})
    b = failure({"tool_response": {"exit_code": 2, "stderr": ""}})
    assert a and b and a != b


def test_long_commands_differing_late_do_not_collide(tmp_path, monkeypatch):
    from judgetap.guard import loop

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    base = "x" * 600
    for i in range(3):
        out = loop.handle(
            {
                "session_id": "s1",
                "tool_name": "Bash",
                "tool_input": {"command": base + str(i) * 3},
                "tool_response": {"exit_code": 1, "stderr": "boom"},
            }
        )
    assert out is None


def test_loop_notes_are_not_guarded_calls(tmp_path, monkeypatch, capsys):
    import json

    from judgetap.cli import main
    from judgetap.dashboard.data import load

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    rows = [
        {
            "outcome": "hold",
            "layer": "rules",
            "latency_ms": 1,
            "cost_usd": None,
            "error": None,
        },
        {
            "outcome": "note",
            "layer": "loop",
            "latency_ms": 0,
            "cost_usd": None,
            "error": None,
        },
    ]
    (tmp_path / "guard.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    main(["guard", "stats"])
    assert "1 guarded calls" in capsys.readouterr().out
    s = load(tmp_path)["summary"]
    assert s["total"] == 1 and s["loop_notes"] == 1
