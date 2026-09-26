import asyncio

import pytest

import snapjudge as sj
from snapjudge.testing import StaticEngine


def eng(name, p_yes):
    return StaticEngine(lambda q, ctx: {"yes": p_yes, "no": 1 - p_yes}, name=name)


class Boom(StaticEngine):
    def __init__(self, name="boom"):
        super().__init__(lambda q, ctx: {}, name=name)

    def decide(self, questions, context):
        self.calls.append((tuple(questions), context))
        raise OSError("network down")


def test_confident_first_engine_answers_alone():
    cheap, strong = eng("cheap", 0.95), eng("strong", 0.99)
    d = sj.yesno("q", engine=sj.Cascade([cheap, strong]))
    assert (d.engine, d.escalated, d.meta["hops"]) == ("cheap", False, ["cheap"])
    assert strong.calls == []


def test_low_confidence_escalates():
    d = sj.yesno("q", engine=sj.Cascade([eng("cheap", 0.6), eng("strong", 0.9)]))
    assert (d.engine, d.escalated, d.meta["hops"]) == (
        "strong",
        True,
        ["cheap", "strong"],
    )


def test_only_unresolved_questions_escalate_in_one_batch():
    cheap = StaticEngine(
        lambda q, ctx: (
            {"yes": 0.99, "no": 0.01} if q.text == "easy" else {"yes": 0.5, "no": 0.5}
        ),
        name="cheap",
    )
    strong = eng("strong", 0.9)
    qs = [
        sj.Question.yesno("easy"),
        sj.Question.yesno("hard"),
        sj.Question.yesno("hard2"),
    ]
    out = sj.batch(qs, engine=sj.Cascade([cheap, strong]))
    assert [d.engine for d in out] == ["cheap", "strong", "strong"]
    assert [q.text for q in strong.calls[0][0]] == ["hard", "hard2"]


def test_engine_error_falls_through():
    boom = Boom()
    d = sj.yesno("q", engine=sj.Cascade([boom, eng("strong", 0.9)]))
    assert d.engine == "strong" and d.meta["hops"] == ["boom", "strong"]


def test_malformed_answer_falls_through():
    bad = StaticEngine(lambda q, ctx: {"maybe": 1.0}, name="bad")
    d = sj.yesno("q", engine=sj.Cascade([bad, eng("strong", 0.9)]))
    assert d.engine == "strong"


def test_exhausted_raises_with_every_attempt_named():
    with pytest.raises(sj.CascadeExhaustedError, match=r"a: p=0\.60.*b: network down"):
        sj.yesno("q", engine=sj.Cascade([eng("a", 0.6), Boom("b")]))


def test_return_last_uses_last_valid_answer():
    c = sj.Cascade([eng("a", 0.6), eng("b", 0.7), Boom()], on_exhausted="return_last")
    d = sj.yesno("q", engine=c)
    assert (d.engine, d.p) == ("b", pytest.approx(0.7))


def test_return_last_with_no_valid_answer_still_raises():
    with pytest.raises(sj.CascadeExhaustedError):
        sj.yesno("q", engine=sj.Cascade([Boom()], on_exhausted="return_last"))


def test_fallback_callback_sees_attempts():
    seen = []

    def human(question, attempts):
        seen.extend(a.engine for a in attempts)
        return {"yes": 0.0, "no": 1.0}

    d = sj.yesno("q", engine=sj.Cascade([eng("a", 0.5)], on_exhausted=human))
    assert (d.value, d.engine, seen) == ("no", "fallback", ["a"])
    assert d.meta["hops"] == ["a", "fallback"]


def test_invalid_fallback_answer_is_rejected_by_core():
    c = sj.Cascade([eng("a", 0.5)], on_exhausted=lambda q, a: {"maybe": 1.0})
    with pytest.raises(sj.InvalidAnswerError):
        sj.yesno("q", engine=c)


def test_async_cascade():
    d = asyncio.run(sj.ayesno("q", engine=sj.Cascade([eng("a", 0.5), eng("b", 0.85)])))
    assert d.engine == "b"


@pytest.mark.parametrize(
    "kwargs", [{"engines": []}, {"engines": [eng("a", 1)], "escalate_below": 1.5}]
)
def test_bad_cascade_config(kwargs):
    with pytest.raises(sj.SnapjudgeError):
        sj.Cascade(**kwargs)
