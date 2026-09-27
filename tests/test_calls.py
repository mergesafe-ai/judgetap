import asyncio
import json

import pytest

import judgetap as jt
from judgetap.cascade import CascadeExhaustedError
from judgetap.dashboard.data import load
from judgetap.testing import StaticEngine


def eng(name, p_yes):
    return StaticEngine(lambda q, c: {"yes": p_yes, "no": 1 - p_yes}, name=name)


@pytest.fixture
def clock(monkeypatch):
    """perf_counter advancing 10 ms per read: every call gets a distinct,
    deterministic latency without sleeping."""
    t = [0.0]

    def tick():
        t[0] += 0.01
        return t[0]

    monkeypatch.setattr("time.perf_counter", tick)


def test_plain_batch_is_one_call_for_all_questions():
    out = jt.batch(
        [jt.Question.yesno("a"), jt.Question.yesno("b")], engine=eng("e", 0.9)
    )
    assert [(c.engine, c.ok, c.questions) for c in out[0].calls] == [("e", True, 2)]
    assert out[0].calls is out[1].calls


def test_escalating_cascade_reports_each_hop_with_its_own_latency(clock):
    d = jt.yesno("q", engine=jt.Cascade([eng("cheap", 0.5), eng("strong", 0.9)]))
    assert [c.engine for c in d.calls] == ["cheap", "strong"]
    assert all(c.latency_ms > 0 for c in d.calls)


def test_nested_cascade_lists_inner_providers_not_cascades():
    inner = jt.Cascade([eng("a", 0.5), eng("b", 0.6)])
    d = jt.yesno("q", engine=jt.Cascade([inner, eng("c", 0.9)]))
    assert [c.engine for c in d.calls] == ["a", "b", "c"]


def test_exhausted_cascade_carries_calls_on_the_error():
    with pytest.raises(CascadeExhaustedError) as info:
        jt.yesno("q", engine=jt.Cascade([eng("a", 0.5), eng("b", 0.6)]))
    assert [c.engine for c in info.value.calls] == ["a", "b"]


def test_fallback_callback_is_not_a_call_but_an_engine_named_fallback_is():
    via_callback = jt.yesno(
        "q",
        engine=jt.Cascade(
            [eng("a", 0.5)], on_exhausted=lambda q, a: {"yes": 1.0, "no": 0.0}
        ),
    )
    assert [c.engine for c in via_callback.calls] == ["a"]
    real = jt.yesno("q", engine=jt.Cascade([eng("a", 0.5), eng("fallback", 0.9)]))
    assert [c.engine for c in real.calls] == ["a", "fallback"]


def test_failed_engine_call_is_recorded_not_ok():
    class Boom(StaticEngine):
        def decide(self, questions, context):
            raise OSError("down")

    d = jt.yesno(
        "q", engine=jt.Cascade([Boom(lambda q, c: {}, name="boom"), eng("ok", 0.9)])
    )
    assert [(c.engine, c.ok) for c in d.calls] == [("boom", False), ("ok", True)]
    with pytest.raises(OSError) as info:
        jt.yesno("q", engine=Boom(lambda q, c: {}, name="boom"))
    assert [(c.engine, c.ok) for c in info.value.calls] == [("boom", False)]


def test_async_path_reports_calls():
    d = asyncio.run(
        jt.ayesno("q", engine=jt.Cascade([eng("cheap", 0.5), eng("strong", 0.9)]))
    )
    assert [c.engine for c in d.calls] == ["cheap", "strong"]


def test_library_batch_calls_logged_once_and_counted_once(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    monkeypatch.setenv("JUDGETAP_LOG", "1")
    qs = [jt.Question.yesno("easy"), jt.Question.yesno("hard")]
    cheap = StaticEngine(
        lambda q, c: (
            {"yes": 0.99, "no": 0.01} if q.text == "easy" else {"yes": 0.5, "no": 0.5}
        ),
        name="cheap",
    )
    jt.batch(qs, engine=jt.Cascade([cheap, eng("strong", 0.9)]))
    records = [
        json.loads(line) for line in (tmp_path / "guard.jsonl").read_text().splitlines()
    ]
    assert [len(r["calls"]) for r in records] == [2, 0] and records[0][
        "batch"
    ] == records[1]["batch"]
    engines = load(tmp_path)["summary"]["engines"]
    assert engines["cheap"]["calls"] == 1 and engines["strong"]["calls"] == 1
    assert (
        engines["cheap"]["p50_ms"] is not None
        and engines["strong"]["p50_ms"] is not None
    )


def test_guard_logs_its_judge_calls_including_exhausted(tmp_path, monkeypatch):
    from judgetap.guard.core import Action, check
    from judgetap.guard.hook import log

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    action = Action(tool="Bash", cwd=tmp_path, command="make deploy")
    ok = check(action, jt.Cascade([eng("cheap", 0.5), eng("strong", 0.05)]))
    failed = check(action, jt.Cascade([eng("x", 0.5), eng("y", 0.6)]))
    assert [c.engine for c in ok.calls] == ["cheap", "strong"]
    assert failed.error and [c.engine for c in failed.calls] == ["x", "y"]
    log(action, ok, "s")
    log(action, failed, "s")
    engines = load(tmp_path)["summary"]["engines"]
    assert {n: e["calls"] for n, e in engines.items()} == {
        "cheap": 1,
        "strong": 1,
        "x": 1,
        "y": 1,
    }


def test_records_from_before_calls_still_count(tmp_path):
    old = {
        "id": "o",
        "outcome": "allow",
        "layer": "judge",
        "engine": "jev",
        "latency_ms": 200,
    }
    (tmp_path / "guard.jsonl").write_text(json.dumps(old) + "\n")
    engines = load(tmp_path)["summary"]["engines"]
    assert engines["jev"] == {"calls": 1, "p50_ms": 200.0, "p95_ms": 200.0}


def test_malformed_hop_is_not_ok():
    import judgetap as jt
    from judgetap.testing import StaticEngine

    bad = StaticEngine(lambda q, c: {"maybe": 1.0}, name="bad")
    good = StaticEngine(lambda q, c: {"yes": 0.9, "no": 0.1}, name="good")
    d = jt.yesno("q", engine=jt.Cascade([bad, good]))
    by = {c.engine: c.ok for c in d.calls}
    assert by == {"bad": False, "good": True}


def test_raising_callback_keeps_provider_calls():
    import pytest as _pytest

    import judgetap as jt
    from judgetap.testing import StaticEngine

    def boom(question, attempts):
        raise RuntimeError("human unavailable")

    low = StaticEngine(lambda q, c: {"yes": 0.5, "no": 0.5}, name="low")
    with _pytest.raises(RuntimeError) as info:
        jt.yesno("q", engine=jt.Cascade([low], on_exhausted=boom))
    assert [c.engine for c in info.value.calls] == ["low"]


def test_stop_records_carry_calls(tmp_path, monkeypatch):
    import json

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
                "message": {"content": [{"type": "text", "text": "done x"}]},
            }
        )
        + "\n"
    )
    engine = StaticEngine(lambda q, c: {"yes": 0.9, "no": 0.1}, name="judge")
    stop.handle({"session_id": "s1", "transcript_path": str(t)}, engine=engine)
    assert load(tmp_path)["summary"]["engines"]["judge"]["calls"] == 1


def test_old_stop_records_are_counted_without_latency(tmp_path):
    import json

    from judgetap.dashboard.data import load

    rows = [
        {
            "id": "a",
            "layer": "stop",
            "outcome": "allow",
            "engine": "judge",
            "latency_ms": 0.0,
        },
        {
            "id": "b",
            "layer": "stop",
            "outcome": "skip",
            "engine": "judge",
            "latency_ms": 0.0,
        },
    ]
    (tmp_path / "guard.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    e = load(tmp_path)["summary"]["engines"]["judge"]
    assert e["calls"] == 1 and e["p50_ms"] is None
