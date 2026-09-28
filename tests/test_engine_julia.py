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
    with pytest.raises(JuliaError, match="pip install -e"):
        jt.yesno("q", engine=JuliaEngine())


def test_bad_shape_is_julia_error():
    with pytest.raises(JuliaError, match="shape"):
        jt.yesno("q", engine=JuliaEngine(runtime=FakeRuntime({"answers": {}})))


def test_async_path():
    d = asyncio.run(jt.abatch(QS, engine=JuliaEngine(runtime=FakeRuntime())))
    assert [x.value for x in d] == ["no", "billing", "high"]


def test_load_specs(monkeypatch, tmp_path):
    monkeypatch.delenv("JUDGETAP_JULIA_PATH", raising=False)
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    assert load("julia").path == str(tmp_path / "models" / "Julia-1")
    assert load("julia:/opt/Julia-1").path == "/opt/Julia-1"
    assert load("julia").name == "julia"


def test_install_accepts_julia_spec(tmp_path):
    from judgetap.guard.install import write_engine

    write_engine(tmp_path, "julia:/opt/Julia-1")
    assert 'engine = "julia:/opt/Julia-1"' in (tmp_path / "guard.toml").read_text()


def test_relative_paths_resolve_under_judgetap_home_not_cwd(tmp_path, monkeypatch):
    from judgetap.engines.julia import JuliaEngine

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    assert JuliaEngine().path == str(tmp_path / "home" / "models" / "Julia-1")
    assert JuliaEngine("/abs/Julia-1").path == "/abs/Julia-1"


def test_model_loads_once_per_process_even_concurrently(tmp_path, monkeypatch):
    import sys
    import threading
    import types

    from judgetap.engines import julia as jmod

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    monkeypatch.setattr(jmod, "_models", {})
    loads = []
    fake = types.ModuleType("julia")

    def load_model(path, **kw):
        loads.append(path)
        threading.Event().wait(0.02)
        return object()

    fake.load_model = load_model
    monkeypatch.setitem(sys.modules, "julia", fake)
    engines = [jmod.JuliaEngine() for _ in range(5)]
    threads = [threading.Thread(target=e._get_runtime) for e in engines]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(loads) == 1
    assert len({id(e._runtime) for e in engines}) == 1


@pytest.mark.parametrize("spec", ["../Julia-1", "a/../../x", "../../etc"])
def test_relative_paths_escaping_models_dir_are_rejected(spec, tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    with pytest.raises(ValueError, match="escapes"):
        JuliaEngine(spec)


def test_relative_path_inside_models_dir_with_dotdot_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    path = JuliaEngine("a/../Julia-1").path
    assert path == str(tmp_path / "home" / "models" / "a" / ".." / "Julia-1")


def test_model_cache_keeps_two_most_recently_used(fake_julia, tmp_path, monkeypatch):
    from judgetap.engines import julia as jmod

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    monkeypatch.setattr(jmod, "_models", {})
    jmod.JuliaEngine("A")._get_runtime()
    jmod.JuliaEngine("B")._get_runtime()
    jmod.JuliaEngine("A")._get_runtime()  # A is now most recent
    jmod.JuliaEngine("C")._get_runtime()  # evicts B
    models = tmp_path / "models"
    assert [k[0] for k in jmod._models] == [str(models / "A"), str(models / "C")]
    assert len(fake_julia.loads) == 3
    jmod.JuliaEngine("B")._get_runtime()  # reloads B, evicts A
    assert len(fake_julia.loads) == 4
    assert [k[0] for k in jmod._models] == [str(models / "C"), str(models / "B")]


def test_instance_hits_refresh_lru_and_do_not_pin_evicted(fake_julia, monkeypatch):
    from judgetap.engines import julia as jmod

    monkeypatch.setattr(jmod, "_models", {})
    a, b, c = (JuliaEngine(f"/m/{n}") for n in "abc")
    a._get_runtime()
    b._get_runtime()
    a._get_runtime()  # a hit on the same instance marks a most recently used
    c._get_runtime()  # evicts b, not a
    assert list(jmod._models) == [("/m/a", "cpu"), ("/m/c", "cpu")]
    assert b._runtime is None  # the evicted model is not kept alive by b
    b._get_runtime()  # so b reloads through the cache
    assert [p for p, _ in fake_julia.loads] == ["/m/a", "/m/b", "/m/c", "/m/b"]


def test_injected_runtime_bypasses_cache(monkeypatch):
    from judgetap.engines import julia as jmod

    monkeypatch.setattr(jmod, "_models", {})
    rt = FakeRuntime()
    assert JuliaEngine(runtime=rt)._get_runtime() is rt and jmod._models == {}
