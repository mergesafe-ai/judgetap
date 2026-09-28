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
    assert d.p == pytest.approx(0.7) and d.calibrated
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


def test_gliner_spec_can_be_saved(tmp_path):
    from judgetap.guard.install import write_engine

    write_engine(tmp_path, "gliner")
    assert 'engine = "gliner"' in (tmp_path / "guard.toml").read_text()
