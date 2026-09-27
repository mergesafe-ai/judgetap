import asyncio

import pytest

import judgetap as sj
from judgetap.testing import StaticEngine


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
    with pytest.raises(sj.JudgetapError):
        sj.Cascade(**kwargs)


def priced(name, p_yes, cost):
    class Priced(StaticEngine):
        def decide(self, questions, context):
            return [
                sj.RawAnswer({"yes": p_yes, "no": 1 - p_yes}, cost_usd=cost)
                for _ in questions
            ]

    return Priced(lambda q, c: {}, name=name)


@pytest.mark.parametrize(
    ("on_exhausted", "engines", "cost"),
    [
        ("raise", [priced("a", 0.5, 0.01), priced("b", 0.9, 0.02)], 0.03),
        ("return_last", [priced("a", 0.5, 0.01), priced("b", 0.6, 0.02)], 0.03),
        (
            lambda q, a: {"yes": 1.0, "no": 0.0},
            [priced("a", 0.5, 0.01), priced("b", 0.6, 0.02)],
            0.03,
        ),
    ],
)
def test_cost_sums_every_engine_paid(on_exhausted, engines, cost):
    d = sj.yesno("q", engine=sj.Cascade(engines, on_exhausted=on_exhausted))
    assert d.cost_usd == pytest.approx(cost)


def test_cost_is_none_when_no_engine_reports_it():
    assert (
        sj.yesno("q", engine=sj.Cascade([eng("a", 0.5), eng("b", 0.9)])).cost_usd
        is None
    )


@pytest.mark.parametrize("bad", [None, [sj.RawAnswer(None)], [object()]])
def test_malformed_results_fall_through(bad):
    class Bad(StaticEngine):
        def decide(self, questions, context):
            return bad

    d = sj.yesno(
        "q", engine=sj.Cascade([Bad(lambda q, c: {}, name="bad"), eng("good", 0.9)])
    )
    assert d.engine == "good" and d.meta["hops"] == ["bad", "good"]


def test_nested_cascade_keeps_inner_engine_and_hops():
    inner = sj.Cascade([eng("cheap", 0.5), eng("strong", 0.9)])
    d = sj.yesno("q", engine=sj.Cascade([inner]))
    assert (d.engine, d.escalated, d.meta["hops"]) == (
        "strong",
        True,
        ["cheap", "strong"],
    )


def test_cost_is_unknown_when_any_answering_engine_is_unpriced():
    d = sj.yesno(
        "q", engine=sj.Cascade([eng("free-unknown", 0.5), priced("b", 0.9, 0.02)])
    )
    assert d.cost_usd is None


def test_from_config_builds_cascade(tmp_path):
    from judgetap.cascade import from_config

    cfg = tmp_path / "judgetap.toml"
    cfg.write_text(
        '[cascade]\norder = ["jev", "llm:openai/x"]\nescalate_below = 0.7\non_exhausted = "return_last"\n'
    )
    c = from_config(cfg)
    assert [e.name for e in c.engines] == ["jev", "llm:openai/x"]
    assert (c.escalate_below, c.on_exhausted) == (0.7, "return_last")


@pytest.mark.parametrize(
    "body",
    [
        "",
        "[cascade]\norder = []\n",
        '[cascade]\norder = ["jev"]\non_exhausted = "shrug"\n',
    ],
)
def test_from_config_rejects_bad_files(tmp_path, body):
    from judgetap.cascade import from_config

    cfg = tmp_path / "judgetap.toml"
    cfg.write_text(body)
    with pytest.raises(sj.JudgetapError):
        from_config(cfg)


@pytest.mark.parametrize(
    "body",
    [
        '[cascade]\norder = ["jev"]\nescalate_below = "high"\n',
        '[cascade]\norder = "jev"\n',
        '[cascade]\norder = ["jev"]\nescalate_below = true\n',
    ],
)
def test_from_config_type_errors_name_the_setting(tmp_path, body):
    from judgetap.cascade import from_config

    cfg = tmp_path / "judgetap.toml"
    cfg.write_text(body)
    with pytest.raises(sj.JudgetapError, match="cascade\\."):
        from_config(cfg)


def test_from_config_scalar_cascade_is_a_config_error(tmp_path):
    from judgetap.cascade import from_config

    cfg = tmp_path / "judgetap.toml"
    cfg.write_text('cascade = "jev"\n')
    with pytest.raises(sj.JudgetapError):
        from_config(cfg)
