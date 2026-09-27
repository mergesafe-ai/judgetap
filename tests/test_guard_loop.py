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
    """A documented Claude Code payload: failures on PostToolUseFailure with a
    top-level `error` ("Exit code N" first line for Bash), successes on
    PostToolUse with a tool_response."""
    base = {
        "session_id": session,
        "transcript_path": "/tmp/t.jsonl",
        "cwd": "/tmp",
        "permission_mode": "default",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "run"},
        "tool_use_id": "toolu_01ABC",
    }
    if fail:
        payload = {
            **base,
            "hook_event_name": "PostToolUseFailure",
            "error": f"Exit code {code}\n{err}",
            "is_interrupt": False,
            "duration_ms": 10,
        }
    else:
        payload = {
            **base,
            "hook_event_name": "PostToolUse",
            "tool_response": {"stdout": "ok", "stderr": "", "interrupted": False},
            "duration_ms": 10,
        }
    out = io.StringIO()
    assert loop.run(io.StringIO(json.dumps(payload)), out) == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


def test_third_identical_failure_adds_a_note():
    assert call("npm run build") is None
    assert call("npm run build") is None
    out = call("npm run build")
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUseFailure"
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


def test_concurrent_hooks_do_not_lose_actions(tmp_path, monkeypatch):
    import threading

    from judgetap.guard import loop

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    payload = {
        "session_id": "race",
        "tool_name": "Bash",
        "tool_input": {"command": "make build"},
        "tool_response": {"exit_code": 2, "stderr": "missing target"},
    }
    real_load = loop._load

    def slow_load(path):
        data = real_load(path)
        threading.Event().wait(0.02)  # widen the read-modify-write window
        return data

    monkeypatch.setattr(loop, "_load", slow_load)
    threads = [threading.Thread(target=loop.handle, args=(payload,)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(real_load(tmp_path / "sessions" / "race.json")) == 6


DOC_FAILURE = {
    # Verbatim from code.claude.com/docs/en/hooks#posttoolusefailure-input
    "session_id": "abc123",
    "transcript_path": "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
    "cwd": "/Users/...",
    "permission_mode": "default",
    "hook_event_name": "PostToolUseFailure",
    "tool_name": "Bash",
    "tool_input": {"command": "npm test", "description": "Run test suite"},
    "tool_use_id": "toolu_01ABC123...",
    "error": "Exit code 1\nError: Cannot find module 'express'",
    "is_interrupt": False,
    "duration_ms": 4187,
}


def test_documented_failure_payload_triggers_on_the_third_repeat():
    outs = [loop.handle(dict(DOC_FAILURE)) for _ in range(3)]
    assert outs[:2] == [None, None]
    assert outs[2]["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"


def test_interrupts_are_not_loop_signals():
    for _ in range(4):
        assert loop.handle({**DOC_FAILURE, "is_interrupt": True}) is None


def test_install_adds_post_tool_use_failure_and_upgrades_old_installs(tmp_path):
    path = tmp_path / "settings.json"
    old = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": POST_COMMAND}],
                }
            ]
        }
    }
    path.write_text(json.dumps(old))
    assert install(path) is True
    hooks = json.loads(path.read_text())["hooks"]
    assert POST_COMMAND in json.dumps(hooks["PostToolUseFailure"])
    assert install(path) is False
    assert uninstall(path) is True
    assert "PostToolUseFailure" not in json.loads(path.read_text()).get("hooks", {})


def test_prune_sessions_removes_old_state_once_an_hour(tmp_path):
    import os

    d = tmp_path / "sessions"
    d.mkdir()
    old, fresh = d / "a.json", d / "b.json"
    old.write_text("[]")
    fresh.write_text("[]")
    now = 10_000_000.0
    os.utime(old, (now - 8 * 86400, now - 8 * 86400))
    os.utime(fresh, (now - 60, now - 60))
    loop.prune_sessions(d, now=now)
    assert not old.exists() and fresh.exists()
    stale = d / "c.json"
    stale.write_text("[]")
    os.utime(stale, (now - 9 * 86400, now - 9 * 86400))
    loop.prune_sessions(d, now=now + 60)  # within the hour: no sweep
    assert stale.exists()
    loop.prune_sessions(d, now=now + 3700)
    assert not stale.exists()


def test_prune_keeps_locks_and_skips_busy_sessions(tmp_path):
    import fcntl
    import os
    import time

    from judgetap.guard.loop import SESSION_TTL_SECONDS, prune_sessions

    old = time.time() - SESSION_TTL_SECONDS - 100
    for name in (
        "idle.json",
        "idle.lock",
        "busy.json",
        "busy.lock",
        "idle.stop",
        "x." + "ab" * 16 + ".tmp",
    ):
        p = tmp_path / name
        p.write_text("{}")
        os.utime(p, (old, old))
    fd = os.open(tmp_path / "busy.lock", os.O_WRONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)  # another hook is working on "busy"
    try:
        prune_sessions(tmp_path)
    finally:
        os.close(fd)
    left = sorted(p.name for p in tmp_path.iterdir() if p.name != ".pruned")
    # x.lock: created to take the orphan temp file's session lock before deleting it.
    assert left == ["busy.json", "busy.lock", "idle.lock", "x.lock"]


def test_prune_leaves_a_temp_file_whose_session_is_locked(tmp_path):
    import fcntl
    import os
    import time

    from judgetap.guard.loop import SESSION_TTL_SECONDS, prune_sessions

    old = time.time() - SESSION_TTL_SECONDS - 100
    tmp = tmp_path / (
        "busy." + "0123abcd" * 4 + ".tmp"
    )  # <id>.<uuid hex>.tmp, as _save names it
    lock = tmp_path / "busy.lock"
    for p in (tmp, lock):
        p.write_text("x")
        os.utime(p, (old, old))
    fd = os.open(lock, os.O_WRONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        prune_sessions(tmp_path)
    finally:
        os.close(fd)
    assert tmp.exists()
    prune_sessions(tmp_path, now=time.time() + 7200)  # next sweep, lock free
    assert not tmp.exists()


def test_lock_path_matches_session_lock_for_dotted_ids(tmp_path):
    import uuid

    from judgetap.guard.loop import _lock_for

    d = tmp_path
    for data in ("a.b.json", "a.b.stop"):
        assert _lock_for(d / data) == (d / data).with_suffix(".lock") == d / "a.b.lock"
        tmp = (d / data).with_suffix(f".{uuid.uuid4().hex}.tmp")  # as _save writes it
        assert _lock_for(tmp) == d / "a.b.lock"
