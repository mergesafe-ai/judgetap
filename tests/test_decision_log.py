import json
import stat

import pytest

import snapjudge as sj
from snapjudge.testing import StaticEngine


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SNAPJUDGE_LOG", raising=False)
    yield
    sj.configure(None)


def engine():
    return StaticEngine(lambda q, c: {"yes": 0.9, "no": 0.1}, name="fake")


def lines(tmp_path):
    path = tmp_path / "home" / "guard.jsonl"
    return (
        [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    )


def test_off_by_default(tmp_path):
    sj.configure(engine())
    sj.yesno("q")
    assert lines(tmp_path) == []


def test_configure_log_records_decision_without_context(tmp_path):
    sj.configure(engine(), log=True)
    sj.yesno("Refund this? token=abc123secret", {"customer": "SECRET-CONTEXT"})
    [rec] = lines(tmp_path)
    assert (
        rec["source"] == "library"
        and rec["outcome"] == "yes"
        and rec["engine"] == "fake"
    )
    raw = (tmp_path / "home" / "guard.jsonl").read_text()
    assert "SECRET-CONTEXT" not in raw and "abc123secret" not in raw
    assert stat.S_IMODE((tmp_path / "home" / "guard.jsonl").stat().st_mode) == 0o600


def test_env_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPJUDGE_LOG", "1")
    sj.batch([sj.Question.yesno("a"), sj.Question.yesno("b")], engine=engine())
    assert [r["subject"] for r in lines(tmp_path)] == ["a", "b"]


def test_async_path_logs(tmp_path):
    import asyncio

    sj.configure(engine(), log=True)
    asyncio.run(sj.ayesno("q"))
    assert len(lines(tmp_path)) == 1


def test_logging_failure_never_raises(tmp_path, monkeypatch):
    (tmp_path / "home").write_text("not a directory")
    sj.configure(engine(), log=True)
    assert sj.yesno("q").value == "yes"


def test_guard_judge_batches_are_not_library_decisions(tmp_path, monkeypatch):
    import snapjudge as sj
    from snapjudge.guard.core import Action, check
    from snapjudge.testing import StaticEngine

    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path))
    monkeypatch.setenv("SNAPJUDGE_LOG", "1")
    engine = StaticEngine(lambda q, c: {"yes": 0.1, "no": 0.9})
    check(Action(tool="Bash", cwd=tmp_path, command="make deploy"), engine)
    log = tmp_path / "guard.jsonl"
    assert not log.exists() or "library" not in log.read_text()
    sj.yesno("q", engine=engine)
    assert log.read_text().count('"source": "library"') == 1


def test_async_logging_runs_off_the_loop(tmp_path, monkeypatch):
    import asyncio
    import threading

    import snapjudge as sj
    from snapjudge import decision_log
    from snapjudge.testing import StaticEngine

    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path))
    monkeypatch.setenv("SNAPJUDGE_LOG", "1")
    threads = []
    real = decision_log.record
    monkeypatch.setattr(
        decision_log,
        "record",
        lambda d: threads.append(threading.current_thread()) or real(d),
    )

    async def run():
        await sj.ayesno("q", engine=StaticEngine(lambda q, c: {"yes": 0.9, "no": 0.1}))
        return threading.current_thread()

    loop_thread = asyncio.run(run())
    assert threads and threads[0] is not loop_thread


def test_guard_stats_ignore_library_records(tmp_path, monkeypatch, capsys):
    import json

    from snapjudge.cli import main

    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path))
    rows = [
        {
            "outcome": "hold",
            "layer": "rules",
            "latency_ms": 1,
            "cost_usd": None,
            "error": None,
        },
        {
            "source": "library",
            "outcome": "yes",
            "layer": "library",
            "latency_ms": 5,
            "cost_usd": 0.0,
            "error": None,
        },
    ]
    (tmp_path / "guard.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    main(["guard", "stats"])
    assert "1 guarded calls" in capsys.readouterr().out


def test_batch_counts_as_one_engine_call(tmp_path, monkeypatch):
    import snapjudge as sj
    from snapjudge.dashboard.data import load
    from snapjudge.testing import StaticEngine

    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path))
    monkeypatch.setenv("SNAPJUDGE_LOG", "1")
    engine = StaticEngine(lambda q, c: {"yes": 0.9, "no": 0.1}, name="e")
    sj.batch([sj.Question.yesno("a"), sj.Question.yesno("b")], engine=engine)
    summary = load(tmp_path)["summary"]
    assert summary["engines"]["e"]["calls"] == 1


def test_async_scheduling_failure_does_not_fail_the_decision(tmp_path, monkeypatch):
    import asyncio

    import snapjudge as sj
    from snapjudge.testing import StaticEngine

    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path))
    monkeypatch.setenv("SNAPJUDGE_LOG", "1")

    async def boom(*a, **k):
        raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(asyncio, "to_thread", boom)
    d = asyncio.run(
        sj.ayesno("q", engine=StaticEngine(lambda q, c: {"yes": 0.9, "no": 0.1}))
    )
    assert d.value == "yes"


def test_cascade_batch_counts_each_engine(tmp_path, monkeypatch):
    import snapjudge as sj
    from snapjudge.dashboard.data import load
    from snapjudge.testing import StaticEngine

    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path))
    monkeypatch.setenv("SNAPJUDGE_LOG", "1")
    cheap = StaticEngine(
        lambda q, c: (
            {"yes": 0.99, "no": 0.01} if q.text == "easy" else {"yes": 0.5, "no": 0.5}
        ),
        name="cheap",
    )
    strong = StaticEngine(lambda q, c: {"yes": 0.9, "no": 0.1}, name="strong")
    sj.batch(
        [sj.Question.yesno("easy"), sj.Question.yesno("hard")],
        engine=sj.Cascade([cheap, strong]),
    )
    engines = load(tmp_path)["summary"]["engines"]
    assert engines["cheap"]["calls"] == 1 and engines["strong"]["calls"] == 1


def test_escalated_cascade_counts_every_hop(tmp_path, monkeypatch):
    import snapjudge as sj
    from snapjudge.dashboard.data import load
    from snapjudge.testing import StaticEngine

    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path))
    monkeypatch.setenv("SNAPJUDGE_LOG", "1")
    cheap = StaticEngine(lambda q, c: {"yes": 0.5, "no": 0.5}, name="cheap")
    strong = StaticEngine(lambda q, c: {"yes": 0.9, "no": 0.1}, name="strong")
    sj.yesno("hard", engine=sj.Cascade([cheap, strong]))
    engines = load(tmp_path)["summary"]["engines"]
    assert engines["cheap"]["calls"] == 1 and engines["strong"]["calls"] == 1
    assert (
        engines["cheap"]["p50_ms"] is None and engines["strong"]["p50_ms"] is not None
    )
