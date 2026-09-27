import asyncio

import pytest

import judgetap as sj
from judgetap.testing import StaticEngine


def fixed(dist):
    return StaticEngine(lambda q, ctx: dist)


@pytest.fixture(autouse=True)
def _reset_default():
    yield
    sj.configure(None)


def test_yesno_returns_winning_side_and_p_yes():
    d = sj.yesno(
        "Irreversible?", {"cmd": "rm -rf /"}, engine=fixed({"yes": 0.9, "no": 0.1})
    )
    assert d.value == "yes"
    assert d.p == pytest.approx(0.9)
    assert d.p_yes == pytest.approx(0.9)
    assert d.engine == "static"


def test_p_yes_reads_yes_even_when_no_wins():
    d = sj.yesno("Off task?", engine=fixed({"yes": 0.2, "no": 0.8}))
    assert d.value == "no"
    assert d.p_yes == pytest.approx(0.2)


def test_choice_picks_highest_and_keeps_option_order():
    d = sj.choice(
        "Route",
        ["billing", "bug", "other"],
        engine=fixed({"other": 0.1, "bug": 0.7, "billing": 0.2}),
    )
    assert d.value == "bug"
    assert list(d.distribution) == ["billing", "bug", "other"]


def test_tie_goes_to_first_option():
    d = sj.choice("Pick", ["a", "b"], engine=fixed({"a": 0.5, "b": 0.5}))
    assert d.value == "a"


def test_score_exposes_level():
    d = sj.score(
        "Risk",
        ["low", "mid", "high"],
        engine=fixed({"low": 0.1, "mid": 0.2, "high": 0.7}),
    )
    assert (d.value, d.level) == ("high", 2)


def test_near_one_sum_is_renormalised():
    d = sj.yesno("q", engine=fixed({"yes": 0.6, "no": 0.3999}))
    assert sum(d.distribution.values()) == pytest.approx(1)


@pytest.mark.parametrize(
    "dist",
    [
        {"yes": 1.0},  # missing option
        {"yes": 0.5, "no": 0.5, "maybe": 0},  # extra option
        {"yes": 0.9, "no": 0.5},  # does not sum to 1
        {"yes": 1.2, "no": -0.2},  # out of range
        {"yes": float("nan"), "no": 0.5},  # not finite
    ],
)
def test_out_of_set_or_malformed_answers_are_rejected(dist):
    with pytest.raises(sj.InvalidAnswerError):
        sj.yesno("q", engine=fixed(dist))


def test_wrong_answer_count_is_rejected():
    class Short(StaticEngine):
        def decide(self, questions, context):
            return super().decide(questions, context)[:1]

    engine = Short(lambda q, ctx: {o: 1 / len(q.options) for o in q.options})
    with pytest.raises(sj.InvalidAnswerError):
        sj.batch([sj.Question.yesno("a"), sj.Question.yesno("b")], engine=engine)


def test_batch_sends_all_questions_in_one_call_with_shared_context():
    engine = StaticEngine(lambda q, ctx: {o: 1 / len(q.options) for o in q.options})
    qs = [sj.Question.yesno("a"), sj.Question.choice("b", ["x", "y", "z"])]
    out = sj.batch(qs, {"state": 1}, engine=engine)
    assert [d.question for d in out] == qs
    assert len(engine.calls) == 1
    assert engine.calls[0][1] == {"state": 1}


def test_empty_batch_needs_no_engine():
    assert sj.batch([]) == []


def test_configured_default_engine_is_used():
    sj.configure(fixed({"yes": 0.3, "no": 0.7}))
    assert sj.yesno("q").value == "no"


def test_no_engine_raises():
    with pytest.raises(sj.NoEngineError):
        sj.yesno("q")


def test_async_variants_match_sync():
    engine = fixed({"yes": 0.8, "no": 0.2})

    async def run():
        return await sj.ayesno("q", engine=engine), await sj.abatch(
            [sj.Question.yesno("q")], engine=engine
        )

    single, many = asyncio.run(run())
    assert single.value == many[0].value == "yes"


def test_falsey_explicit_engine_is_still_used():
    class Falsey(StaticEngine):
        def __bool__(self):
            return False

    sj.configure(fixed({"yes": 0.9, "no": 0.1}))
    engine = Falsey(lambda q, ctx: {"yes": 0.1, "no": 0.9}, name="falsey")
    assert sj.yesno("q", engine=engine).engine == "falsey"


def test_huge_integer_probability_is_invalid_answer_not_overflow():
    with pytest.raises(sj.InvalidAnswerError):
        sj.yesno("q", engine=fixed({"yes": 10**1000, "no": 0}))


def test_async_choice_and_score():
    engine = StaticEngine(
        lambda q, ctx: {o: (1.0 if i == 0 else 0.0) for i, o in enumerate(q.options)}
    )

    async def run():
        return (
            await sj.achoice("c", ["a", "b"], engine=engine),
            await sj.ascore("s", ["lo", "hi"], engine=engine),
        )

    c, s = asyncio.run(run())
    assert (c.value, s.value, s.level) == ("a", "lo", 0)
