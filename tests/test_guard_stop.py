import json

import pytest

from judgetap.guard import stop
from judgetap.guard.install import STOP_COMMAND, install, uninstall
from judgetap.testing import StaticEngine


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)
    monkeypatch.delenv("SNAPJUDGE_ENGINE", raising=False)


def transcript(
    tmp_path, task="fix the tests", said="I fixed one of three; next I'll do the rest."
):
    t = tmp_path / "t.jsonl"
    rows = [
        {"type": "user", "message": {"content": task}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": said}]}},
    ]
    t.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(t)


def engine(p_done):
    return StaticEngine(lambda q, c: {"yes": p_done, "no": 1 - p_done}, name="fake")


def payload(tmp_path, **kw):
    return {"session_id": "s1", "transcript_path": transcript(tmp_path), **kw}


def test_stop_hook_active_short_circuits(tmp_path):
    e = engine(0.0)
    assert stop.handle(payload(tmp_path, stop_hook_active=True), engine=e) is None
    assert e.calls == []


def test_no_engine_is_a_no_op(tmp_path):
    assert stop.handle(payload(tmp_path)) is None
    assert not (tmp_path / "home" / "guard.jsonl").exists()


def test_confident_not_done_blocks(tmp_path):
    out = stop.handle(payload(tmp_path), engine=engine(0.05))
    assert out["decision"] == "block" and "doesn't look finished" in out["reason"]
    record = json.loads(
        (tmp_path / "home" / "guard.jsonl").read_text().splitlines()[-1]
    )
    assert record["layer"] == "stop" and record["outcome"] == "block"


@pytest.mark.parametrize("p", [0.16, 0.5, 0.95])
def test_above_threshold_lets_it_stop(tmp_path, p):
    assert stop.handle(payload(tmp_path), engine=engine(p)) is None


def test_threshold_from_guard_toml(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "guard.toml").write_text("stop_threshold = 0.4\n")
    assert stop.handle(payload(tmp_path), engine=engine(0.3))["decision"] == "block"
    (home / "guard.toml").write_text('stop_threshold = "high"\n')
    assert stop.threshold() == stop.DEFAULT_THRESHOLD


def test_at_most_two_blocks_per_session(tmp_path):
    outs = [stop.handle(payload(tmp_path), engine=engine(0.0)) for _ in range(4)]
    assert [o is not None for o in outs] == [True, True, False, False]
    other = stop.handle({**payload(tmp_path), "session_id": "s2"}, engine=engine(0.0))
    assert other is not None  # the cap is per session


def test_unsafe_session_id_is_ignored(tmp_path):
    assert (
        stop.handle({**payload(tmp_path), "session_id": "../x"}, engine=engine(0.0))
        is None
    )


def test_assistant_tail_is_bounded_and_since_last_user_turn(tmp_path):
    t = tmp_path / "t.jsonl"
    rows = [
        {"type": "assistant", "message": {"content": "before the task"}},
        {"type": "user", "message": {"content": "do it"}},
        {"type": "assistant", "message": {"content": "a" * 3000}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "b" * 3000}]},
        },
    ]
    t.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    text = stop.last_assistant_text(str(t), limit=4000)
    assert (
        len(text) == 4000
        and text.endswith("b" * 3000)
        and "before the task" not in text
    )


def test_misconfigured_engine_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_ENGINE", "nonsense")
    out = stop.handle(payload(tmp_path))
    assert "stop check skipped" in out["systemMessage"]


def test_install_with_stop_is_opt_in(tmp_path):
    path = tmp_path / "settings.json"
    install(path)
    assert "Stop" not in json.loads(path.read_text())["hooks"]
    assert install(path, with_stop=True) is True
    assert install(path, with_stop=True) is False
    stops = json.loads(path.read_text())["hooks"]["Stop"]
    assert stops == [{"hooks": [{"type": "command", "command": STOP_COMMAND}]}]
    assert uninstall(path) is True
    assert "hooks" not in json.loads(path.read_text())


def test_cli_install_with_stop(tmp_path, monkeypatch, capsys):
    from judgetap.cli import main

    monkeypatch.chdir(tmp_path)
    main(["guard", "install", "--scope", "project", "--with", "stop"])
    assert STOP_COMMAND in (tmp_path / ".claude" / "settings.json").read_text()


def test_stop_records_are_not_guarded_calls(tmp_path, capsys):
    from judgetap.cli import main
    from judgetap.dashboard.data import load

    home = tmp_path / "home"
    home.mkdir()
    rows = [
        {
            "outcome": "hold",
            "layer": "rules",
            "latency_ms": 1,
            "cost_usd": None,
            "error": None,
        },
        {
            "outcome": "block",
            "layer": "stop",
            "latency_ms": 0,
            "cost_usd": None,
            "error": None,
        },
    ]
    (home / "guard.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    main(["guard", "stats"])
    assert "1 guarded calls" in capsys.readouterr().out
    s = load(home)["summary"]
    assert s["total"] == 1 and s["stop_checks"] == 1


def test_task_and_reply_read_the_transcript_once(tmp_path, monkeypatch):
    import json

    from judgetap.guard import hook, stop

    t = tmp_path / "t.jsonl"
    t.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"type": "user", "message": {"content": "add a test"}},
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "Added it."}]},
                },
            ]
        )
        + "\n"
    )
    reads = []
    real = hook._lines_backward
    monkeypatch.setattr(hook, "_lines_backward", lambda *a: reads.append(1) or real(*a))
    assert stop.task_and_reply(str(t)) == ("add a test", "Added it.")
    assert len(reads) == 1


def test_failed_judge_logs_its_calls_and_lets_the_agent_stop(tmp_path, monkeypatch):
    import json

    import judgetap as jt
    from judgetap.dashboard.data import load
    from judgetap.guard import stop
    from judgetap.testing import StaticEngine

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    t = tmp_path / "t.jsonl"
    t.write_text(
        json.dumps({"type": "user", "message": {"content": "do x"}})
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "tried x"}]},
            }
        )
        + "\n"
    )
    low = StaticEngine(lambda q, c: {"yes": 0.5, "no": 0.5}, name="low")
    assert (
        stop.handle(
            {"session_id": "s9", "transcript_path": str(t)}, engine=jt.Cascade([low])
        )
        is None
    )
    assert load(tmp_path)["summary"]["engines"]["low"]["calls"] == 1


def test_stop_uses_last_assistant_message_when_the_transcript_lags(
    tmp_path, monkeypatch
):
    import json

    from judgetap.guard import stop
    from judgetap.testing import StaticEngine

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    t = tmp_path / "t.jsonl"
    t.write_text(
        json.dumps({"type": "user", "message": {"content": "Fix all 3 failing tests"}})
        + "\n"
    )
    seen = []

    def judge(q, ctx):
        seen.append(ctx["assistant_last_message"])
        return {"yes": 0.05, "no": 0.95}

    # The documented Stop input: the final reply comes in last_assistant_message.
    payload = {
        "session_id": "abc123",
        "transcript_path": str(t),
        "cwd": "/tmp",
        "permission_mode": "default",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "I fixed one test; two remain, stopping.",
        "background_tasks": [],
        "session_crons": [],
    }
    out = stop.handle(payload, engine=StaticEngine(judge, name="j"))
    assert seen == ["I fixed one test; two remain, stopping."]
    assert out["decision"] == "block"
