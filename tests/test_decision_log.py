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
