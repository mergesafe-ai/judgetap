import json

import pytest

import snapjudge as sj
from snapjudge.engines import load
from snapjudge.engines.agentjev import AgentJevEngine, AgentJevError
from snapjudge.engines.laya import LayaEngine, LayaError

QS = [
    sj.Question.yesno("Tests passing?"),
    sj.Question.choice("Next?", ["debug", "submit"]),
    sj.Question.score("Risk?", ["low", "mid", "high"]),
]


class FakeRouter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def predict(self, state, questions, model):
        self.calls.append((state, questions, model))
        return self.result


LAYA_RESULT = {
    "answers": {
        "q0": {"type": "noul", "noul": 0.2},
        "q1": {
            "type": "choice",
            "choice": "debug",
            "probabilities": {"debug": 0.9, "submit": 0.1},
        },
        "q2": {
            "type": "score",
            "score": 0.3,
            "probabilities": {"0": 0.7, "1": 0.3, "2": 0.0},
        },
    },
    "routing": {"model": "typed-decisions"},
}


def test_laya_uses_jev_shapes_and_is_free():
    router = FakeRouter(LAYA_RESULT)
    out = sj.batch(QS, {"log": "1 failed"}, engine=LayaEngine(router=router))
    assert [d.value for d in out] == ["no", "debug", "low"]
    assert all(d.cost_usd == 0.0 for d in out)
    state, questions, model = router.calls[0]
    assert state == {"log": "1 failed"} and model == "typed-decisions"
    assert questions["q0"] == {"type": "noul", "instructions": "Tests passing?"}


def test_laya_bad_shape():
    with pytest.raises(LayaError, match="shape"):
        sj.yesno("q", engine=LayaEngine(router=FakeRouter({"answers": {}})))


def test_laya_missing_package_names_the_extra(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "laya", None)
    with pytest.raises(LayaError, match=r"snapjudge\[laya\]"):
        sj.yesno("q", engine=LayaEngine())


def agentjev_response(score_keys):
    return {
        "api_version": "agentjev.decision.v1",
        "results": [
            {
                "id": "0",
                "answers": [
                    {
                        "id": "q0",
                        "type": "boolean",
                        "distribution": {"true": 0.08, "false": 0.92},
                    },
                    {
                        "id": "q1",
                        "type": "choice",
                        "distribution": {"debug": 0.87, "submit": 0.13},
                    },
                    {
                        "id": "q2",
                        "type": "score",
                        "distribution": dict(
                            zip(score_keys, [0.1, 0.2, 0.7], strict=True)
                        ),
                    },
                ],
            }
        ],
        "usage": {"generated_tokens": 0},
    }


@pytest.mark.parametrize("score_keys", [["0", "1", "2"], ["low", "mid", "high"]])
def test_agentjev_maps_answers(score_keys):
    sent = []

    def transport(url, headers, body, timeout):
        sent.append((url, json.loads(body)))
        return 200, json.dumps(agentjev_response(score_keys)).encode()

    out = sj.batch(
        QS, "23 passed, 1 failed", engine=AgentJevEngine(transport=transport)
    )
    assert [d.value for d in out] == ["no", "debug", "high"]
    url, body = sent[0]
    assert url == "http://127.0.0.1:8149/api/evaluate"
    assert body["questions"][0] == {
        "id": "q0",
        "type": "boolean",
        "question": "Tests passing?",
    }
    assert body["questions"][2]["levels"] == ["low", "mid", "high"]


def test_agentjev_unreachable_server():
    def transport(*a):
        raise ConnectionRefusedError("refused")

    with pytest.raises(AgentJevError, match="unreachable"):
        sj.yesno("q", engine=AgentJevEngine(transport=transport))


def test_agentjev_http_error():
    with pytest.raises(AgentJevError, match="500"):
        sj.yesno("q", engine=AgentJevEngine(transport=lambda *a: (500, b"boom")))


def test_load_local_specs():
    assert load("laya").name == "laya"
    assert load("agentjev").url == "http://127.0.0.1:8149"
    assert load("agentjev:http://gpu-box:9000/").url == "http://gpu-box:9000"


def test_agentjev_none_context_is_empty_state():
    sent = []

    def transport(url, headers, body, timeout):
        sent.append(json.loads(body))
        return 200, json.dumps(agentjev_response(["0", "1", "2"])).encode()

    sj.batch(QS, engine=AgentJevEngine(transport=transport))
    assert sent[0]["state"] == ""


def test_agentjev_numeric_level_names_are_not_indices():
    qs = [sj.Question.score("Rate", ["1", "2", "3"])]
    body = {
        "results": [
            {"answers": [{"id": "q0", "distribution": {"1": 0.1, "2": 0.2, "3": 0.7}}]}
        ]
    }
    d = sj.batch(
        qs, engine=AgentJevEngine(transport=lambda *a: (200, json.dumps(body).encode()))
    )[0]
    assert d.value == "3"


def test_local_engines_async():
    import asyncio

    laya = LayaEngine(router=FakeRouter(LAYA_RESULT))
    aj = AgentJevEngine(
        transport=lambda *a: (
            200,
            json.dumps(agentjev_response(["0", "1", "2"])).encode(),
        )
    )

    async def run():
        return await sj.abatch(QS, engine=laya), await sj.abatch(QS, engine=aj)

    a, b = asyncio.run(run())
    assert [d.value for d in a] == ["no", "debug", "low"]
    assert [d.value for d in b] == ["no", "debug", "high"]
