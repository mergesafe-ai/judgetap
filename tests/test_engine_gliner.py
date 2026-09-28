import asyncio
import sys
import types

import pytest

import judgetap as jt
from judgetap.engines import load
from judgetap.engines.gliner import GlinerEngine, GlinerError

QS = [
    jt.Question.yesno("Did the agent finish?"),
    jt.Question.choice("Which team?", ["billing", "shipping", "access"]),
    jt.Question.score("How urgent?", ["low", "mid", "high"]),
]


class Fake:
    def __init__(self, result, confidence_kw=True):
        self.result = result
        self.confidence_kw = confidence_kw
        self.calls = []

    def classify_text(self, text, tasks, **kw):
        if kw and not self.confidence_kw:
            raise TypeError("unexpected keyword argument 'include_confidence'")
        self.calls.append((text, tasks, kw))
        return self.result


def test_one_call_with_all_heads_and_prompts():
    fake = Fake(
        {
            "q0": {"label": "no", "confidence": 0.8},
            "q1": {"label": "billing", "confidence": 0.9},
            "q2": {"label": "high", "confidence": 0.6},
        }
    )
    out = jt.batch(QS, {"msg": "charged twice"}, engine=GlinerEngine(extractor=fake))
    assert [d.value for d in out] == ["no", "billing", "high"]
    assert len(fake.calls) == 1
    text, tasks, kw = fake.calls[0]
    assert '"charged twice"' in text and kw == {"include_confidence": True}
    assert tasks["q0"] == {"labels": ["yes", "no"], "prompt": "Did the agent finish?"}
    assert tasks["q2"]["labels"] == ["low", "mid", "high"]
    assert all(d.cost_usd == 0.0 for d in out)


def test_winner_confidence_is_exact_and_rest_split():
    fake = Fake({"q0": {"label": "billing", "confidence": 0.7}})
    d = jt.choice(
        "Which team?",
        ["billing", "shipping", "access"],
        engine=GlinerEngine(extractor=fake),
    )
    assert d.p == pytest.approx(0.7) and not d.calibrated
    assert d.distribution["shipping"] == pytest.approx(0.15)


def test_full_probabilities_are_used_when_present():
    fake = Fake(
        {
            "q0": {
                "value": "yes",
                "confidence": 0.6,
                "probabilities": {"yes": 0.6, "no": 0.4},
            }
        }
    )
    d = jt.yesno("q", engine=GlinerEngine(extractor=fake))
    assert d.distribution == pytest.approx({"yes": 0.6, "no": 0.4}) and d.calibrated


def test_bare_labels_without_confidence_support():
    fake = Fake({"q0": "shipping"}, confidence_kw=False)
    d = jt.choice(
        "Which team?", ["billing", "shipping"], engine=GlinerEngine(extractor=fake)
    )
    assert (d.value, d.p, d.calibrated) == ("shipping", 1.0, False)


def test_unexpected_shape_is_gliner_error():
    with pytest.raises(GlinerError):
        jt.yesno("q", engine=GlinerEngine(extractor=Fake({})))


def test_missing_package_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "gliner2", None)
    with pytest.raises(GlinerError, match=r"judgetap\[gliner\]"):
        jt.yesno("q", engine=GlinerEngine())


def test_model_loaded_once(monkeypatch):
    loads = []
    mod = types.ModuleType("gliner2")

    class AutoExtractor:
        @staticmethod
        def from_pretrained(name):
            loads.append(name)
            return Fake({"q0": {"label": "yes", "confidence": 0.9}})

    mod.AutoExtractor = AutoExtractor
    monkeypatch.setitem(sys.modules, "gliner2", mod)
    engine = load("gliner")
    jt.yesno("a", engine=engine)
    asyncio.run(jt.ayesno("b", engine=engine))
    assert loads == ["fastino/GLiNER2.5-Decide"]
    assert (
        load("gliner:fastino/GLiNER2.5-multi-Decide").model
        == "fastino/GLiNER2.5-multi-Decide"
    )


def test_guard_refuses_gliner(tmp_path, monkeypatch):
    from judgetap.guard import hook
    from judgetap.guard.install import write_engine

    with pytest.raises(ValueError, match="in-process"):
        write_engine(tmp_path, "gliner:fastino/GLiNER2.5-multi-Decide")
    assert not (tmp_path / "guard.toml").exists()
    # A hand-edited guard.toml fails closed to rules only, without loading.
    monkeypatch.setattr(hook, "engine_spec", lambda: "gliner")
    with pytest.raises(RuntimeError, match="in-process"):
        hook._engine()


def test_partial_probability_map_is_uncalibrated():
    fake = Fake({"q0": {"probabilities": {"billing": 0.6, "shipping": 0.2}}})
    (d,) = jt.batch(
        [jt.Question.choice("topic?", ["billing", "shipping", "other"])],
        "x",
        engine=GlinerEngine(extractor=fake),
        log=False,
    )
    assert d.value == "billing" and not d.calibrated


def test_model_cache_is_bounded(monkeypatch):
    from judgetap.engines import gliner as gmod

    loads = []
    mod = types.ModuleType("gliner2")

    class AutoExtractor:
        @staticmethod
        def from_pretrained(name):
            loads.append(name)
            return Fake({"q0": {"probabilities": {"yes": 0.9, "no": 0.1}}})

    mod.AutoExtractor = AutoExtractor
    monkeypatch.setitem(sys.modules, "gliner2", mod)
    monkeypatch.setattr(gmod, "_models", {})
    for name in ["a", "b", "a", "c", "a", "b"]:
        GlinerEngine(name)._get()
    assert list(gmod._models) == ["a", "b"] and loads == ["a", "b", "c", "b"]


def test_load_and_classify_failures_are_gliner_errors(monkeypatch):
    import sys
    import types

    from judgetap.engines import gliner as gmod

    monkeypatch.setattr(gmod, "_models", {})
    fake = types.ModuleType("gliner2")

    class Boom:
        @staticmethod
        def from_pretrained(model):
            raise OSError("network down")

    fake.AutoExtractor = Boom
    monkeypatch.setitem(sys.modules, "gliner2", fake)
    with pytest.raises(gmod.GlinerError, match="could not load") as info:
        gmod.GlinerEngine()._get()
    assert isinstance(info.value.__cause__, OSError)

    class Bad:
        def classify_text(self, text, tasks, **kw):
            raise RuntimeError("cuda oom")

    with pytest.raises(gmod.GlinerError, match="classification failed"):
        gmod.GlinerEngine(extractor=Bad()).decide([jt.Question.yesno("q")], "x")


def test_model_loads_once_per_process_even_concurrently(monkeypatch):
    import sys
    import threading
    import types

    from judgetap.engines import gliner as gmod

    monkeypatch.setattr(gmod, "_models", {})
    loads = []
    fake = types.ModuleType("gliner2")

    class Auto:
        @staticmethod
        def from_pretrained(model):
            loads.append(model)
            threading.Event().wait(0.02)
            return object()

    fake.AutoExtractor = Auto
    monkeypatch.setitem(sys.modules, "gliner2", fake)
    engines = [gmod.GlinerEngine() for _ in range(5)]
    threads = [threading.Thread(target=e._get) for e in engines]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(loads) == 1 and len({id(e._extractor) for e in engines}) == 1
