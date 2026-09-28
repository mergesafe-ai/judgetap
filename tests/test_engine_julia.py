import asyncio
import sys
import types

import pytest

import judgetap as jt
from judgetap.engines import load
from judgetap.engines.julia import JuliaEngine, JuliaError

QS = [
    jt.Question.yesno("Tests passing?"),
    jt.Question.choice("Team?", ["billing", "shipping"]),
    jt.Question.score("Risk?", ["low", "mid", "high"]),
]

RESULT = {
    "answers": {
        "q0": {
            "type": "noul",
            "noul": 0.2,
            "probabilities": {"false": 0.8, "true": 0.2},
        },
        "q1": {
            "type": "choice",
            "choice": "billing",
            "probabilities": {"billing": 0.9, "shipping": 0.1},
        },
        "q2": {
            "type": "score",
            "score": 1.7,
            "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
        },
    }
}


class FakeRuntime:
    def __init__(self, result=RESULT):
        self.result = result
        self.calls = []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        return self.result


@pytest.fixture
def fake_julia(monkeypatch):
    mod = types.ModuleType("julia")
    mod.loads = []

    def load_model(path, **kw):
        mod.loads.append((path, kw))
        return FakeRuntime()

    mod.load_model = load_model
    monkeypatch.setitem(sys.modules, "julia", mod)
    return mod


def test_maps_all_three_kinds_and_is_free():
    rt = FakeRuntime()
    out = jt.batch(QS, {"log": "1 failed"}, engine=JuliaEngine(runtime=rt))
    assert [d.value for d in out] == ["no", "billing", "high"]
    assert out[0].p_yes == pytest.approx(0.2)
    assert all(d.cost_usd == 0.0 for d in out)
    state, questions = rt.calls[0]
    assert state == {"log": "1 failed"}
    assert questions["q0"] == {"type": "noul", "instructions": "Tests passing?"}
    assert questions["q1"]["criteria"] == {"billing": "billing", "shipping": "shipping"}
    assert questions["q2"]["criteria"] == ["low", "mid", "high"]


def test_noul_falls_back_to_noul_field():
    rt = FakeRuntime({"answers": {"q0": {"type": "noul", "noul": 0.7}}})
    assert jt.yesno("q", engine=JuliaEngine(runtime=rt)).p_yes == pytest.approx(0.7)


def test_loads_once_with_env_path_and_device(fake_julia, monkeypatch):
    monkeypatch.setenv("JUDGETAP_JULIA_PATH", "/models/Julia-1")
    monkeypatch.setenv("JUDGETAP_JULIA_DEVICE", "cuda")
    engine = JuliaEngine()
    engine._runtime = None
    jt.yesno("a", engine=engine)
    jt.yesno("b", engine=engine)
    assert len(fake_julia.loads) == 1
    path, kw = fake_julia.loads[0]
    assert (
        path == "/models/Julia-1"
        and kw["device"] == "cuda"
        and kw["strict_encoding"] is True
    )


def test_too_many_options_raise_before_loading(fake_julia):
    engine = JuliaEngine()
    with pytest.raises(JuliaError, match="2-20 options"):
        jt.choice("pick", [f"o{i}" for i in range(21)], engine=engine)
    assert fake_julia.loads == []


def test_missing_runtime_explains_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "julia", None)
    with pytest.raises(JuliaError, match="pip install -e ./Julia-1"):
        jt.yesno("q", engine=JuliaEngine())


def test_bad_shape_is_julia_error():
    with pytest.raises(JuliaError, match="shape"):
        jt.yesno("q", engine=JuliaEngine(runtime=FakeRuntime({"answers": {}})))


def test_async_path():
    d = asyncio.run(jt.abatch(QS, engine=JuliaEngine(runtime=FakeRuntime())))
    assert [x.value for x in d] == ["no", "billing", "high"]


def test_load_specs(monkeypatch):
    monkeypatch.delenv("JUDGETAP_JULIA_PATH", raising=False)
    assert load("julia").path == "Julia-1"
    assert load("julia:/opt/Julia-1").path == "/opt/Julia-1"
    assert load("julia").name == "julia"


def test_install_accepts_julia_spec(tmp_path):
    from judgetap.guard.install import write_engine

    write_engine(tmp_path, "julia:/opt/Julia-1")
    assert 'engine = "julia:/opt/Julia-1"' in (tmp_path / "guard.toml").read_text()
