import json

import pytest

import snapjudge as sj
from snapjudge.engines import load
from snapjudge.engines.jev import JevEngine, JevError

# Shapes from docs.typesafe.ai/api.
RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "q0": {"type": "noul", "noul": 0.95},
        "q1": {
            "type": "choice",
            "choice": "billing",
            "probabilities": {"billing": 0.88, "technical": 0.12, "sales": 0.0},
            "confidence": 0.81,
        },
        "q2": {
            "type": "score",
            "score": 1.05,
            "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
            "probabilities": {"0": 0.0, "1": 0.95, "2": 0.05},
            "confidence": 0.92,
        },
    },
    "usage": {"input_tokens": 300, "output_tokens": 20},
}

QUESTIONS = [
    sj.Question.yesno("Urgent?"),
    sj.Question.choice("Team?", ["billing", "technical", "sales"]),
    sj.Question.score("Mood?", ["Calm", "Frustrated", "Very angry"]),
]


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, headers, body, timeout):
        self.requests.append((url, headers, json.loads(body)))
        return self.responses.pop(0)


def ok(payload=RESPONSE):
    return 200, json.dumps(payload).encode()


def test_maps_all_three_kinds_and_cost(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    t = Recorder(ok())
    out = sj.batch(
        QUESTIONS, {"msg": "charged twice"}, engine=JevEngine(api_key="k", transport=t)
    )
    assert [d.value for d in out] == ["yes", "billing", "Frustrated"]
    assert out[0].p_yes == pytest.approx(0.95)
    assert out[2].level == 1
    assert out[0].cost_usd == pytest.approx(300 * 0.042e-6 / 3)
    _, headers, body = t.requests[0]
    assert headers["Authorization"] == "Bearer k"
    assert body["state"] == {"msg": "charged twice"}
    assert body["questions"]["q0"] == {"type": "noul", "instructions": "Urgent?"}
    assert body["questions"]["q2"]["criteria"] == ["Calm", "Frustrated", "Very angry"]


def test_retries_429_and_529_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    t = Recorder((429, b"slow down"), (529, b"busy"), ok())
    out = sj.batch(QUESTIONS, engine=JevEngine(api_key="k", transport=t))
    assert len(out) == 3 and len(t.requests) == 3


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    t = Recorder(*[(529, b"busy")] * 3)
    with pytest.raises(JevError, match="529"):
        sj.batch(QUESTIONS, engine=JevEngine(api_key="k", transport=t, max_retries=2))


def test_auth_error_is_not_retried():
    t = Recorder((401, b"bad key"))
    with pytest.raises(JevError, match="401"):
        sj.yesno("q", engine=JevEngine(api_key="k", transport=t))
    assert len(t.requests) == 1


def test_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    t = Recorder()
    with pytest.raises(JevError, match="TYPESAFE_API_KEY"):
        sj.yesno("q", engine=JevEngine(transport=t))
    assert t.requests == []


def test_unexpected_shape_is_a_jev_error():
    t = Recorder(ok({"answers": {}}))
    with pytest.raises(JevError, match="shape"):
        sj.yesno("q", engine=JevEngine(api_key="k", transport=t))


def test_repr_never_shows_key():
    assert "secret" not in repr(JevEngine(api_key="secret"))


def test_load_specs(monkeypatch):
    assert load("jev").model == "jev-latest"
    assert load("jev:jev-1.13.0").model == "jev-1.13.0"
    assert load("llm:openai/gpt-4o-mini").name == "llm:openai/gpt-4o-mini"
    monkeypatch.setenv("SNAPJUDGE_ENGINE", "jev")
    assert load().name == "jev"
    with pytest.raises(sj.SnapjudgeError):
        load("nope")
    with pytest.raises(sj.SnapjudgeError):
        load("llm")
