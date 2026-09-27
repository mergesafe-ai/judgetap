import asyncio
from dataclasses import replace

import pytest

import judgetap as jt
from judgetap import InvalidAnswerError
from judgetap.cascade import Cascade, CascadeExhaustedError
from judgetap.testing import StaticEngine


class Costly(StaticEngine):
    def __init__(self, script, name, cost):
        super().__init__(script, name)
        self.cost = cost

    def decide(self, questions, context):
        return [
            replace(r, cost_usd=self.cost) for r in super().decide(questions, context)
        ]


def yes(p):
    return lambda q, c: {"yes": p, "no": 1 - p}


def nested():
    inner = Cascade(
        [
            Costly(
                lambda q, c: (
                    {"yes": 0.9, "no": 0.1}
                    if "one" in q.text
                    else {"yes": 0.5, "no": 0.5}
                ),
                "cheap",
                0.01,
            )
        ],
        name="inner",
    )
    big = Costly(yes(0.95), "big", 0.10)
    return Cascade([inner, big]), big


def test_nested_exhaustion_keeps_answered_questions_and_cost():
    outer, big = nested()
    qs = [jt.Question.yesno("one"), jt.Question.yesno("two")]
    ds = jt.batch(qs, engine=outer, log=False)
    # "one" was answered by cheap inside inner: not re-asked, cost kept.
    assert ds[0].engine == "cheap" and ds[0].cost_usd == pytest.approx(0.01)
    assert [q.text for q in big.calls[0][0]] == ["two"]
    # "two": inner paid 0.01 before giving up, big paid 0.10.
    assert ds[1].engine == "big" and ds[1].cost_usd == pytest.approx(0.11)
    assert ds[1].meta["hops"] == ["cheap", "big"]
    assert {c.engine for c in ds[0].calls} == {"cheap", "big"}


def test_nested_exhaustion_async_matches_sync():
    outer, _ = nested()
    qs = [jt.Question.yesno("one"), jt.Question.yesno("two")]
    ds = asyncio.run(jt.abatch(qs, engine=outer, log=False))
    assert [d.engine for d in ds] == ["cheap", "big"]
    assert ds[1].cost_usd == pytest.approx(0.11)


def test_top_level_cascade_still_raises_with_calls():
    c = Cascade([StaticEngine(yes(0.5), "a")])
    with pytest.raises(CascadeExhaustedError) as info:
        jt.yesno("q", engine=c)
    assert [x.engine for x in info.value.calls] == ["a"]


@pytest.mark.parametrize("run_async", [False, True])
def test_invalid_answer_from_plain_engine_carries_its_call(run_async):
    bad = StaticEngine(lambda q, c: {"maybe": 1.0}, "bad")
    with pytest.raises(InvalidAnswerError) as info:
        if run_async:
            asyncio.run(jt.ayesno("x", engine=bad))
        else:
            jt.yesno("x", engine=bad)
    (call,) = info.value.calls
    assert (call.engine, call.ok) == ("bad", False)


def test_count_mismatch_carries_the_call():
    class Short(StaticEngine):
        def decide(self, questions, context):
            return super().decide(questions, context)[:1]

    e = Short(yes(0.9), "short")
    with pytest.raises(InvalidAnswerError) as info:
        jt.batch([jt.Question.yesno("a"), jt.Question.yesno("b")], engine=e, log=False)
    assert [c.engine for c in info.value.calls] == ["short"]


@pytest.mark.parametrize("run_async", [False, True])
def test_invalid_callback_output_keeps_the_cascades_calls(run_async):
    c = Cascade(
        [StaticEngine(yes(0.6), "a"), StaticEngine(yes(0.6), "b")],
        on_exhausted=lambda q, a: {"maybe": 1},
    )
    with pytest.raises(InvalidAnswerError) as info:
        if run_async:
            asyncio.run(jt.ayesno("x", engine=c))
        else:
            jt.yesno("x", engine=c)
    assert [x.engine for x in info.value.calls] == ["a", "b"]


def test_nested_unknown_cost_makes_the_total_unknown():
    from dataclasses import replace

    import judgetap as jt
    from judgetap.testing import StaticEngine

    class Priced(StaticEngine):
        def __init__(self, f, name, cost):
            super().__init__(f, name=name)
            self.cost = cost

        def decide(self, qs, ctx):
            return [replace(r, cost_usd=self.cost) for r in super().decide(qs, ctx)]

    unpriced = StaticEngine(
        lambda q, c: {"yes": 0.5, "no": 0.5}, name="unpriced"
    )  # cost None
    inner = jt.Cascade([unpriced], name="inner")
    outer = jt.Cascade(
        [inner, Priced(lambda q, c: {"yes": 0.95, "no": 0.05}, "big", 0.10)]
    )
    d = jt.yesno("q", engine=outer)
    assert d.cost_usd is None
