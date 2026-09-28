import asyncio
import math
import sys
import types

import pytest

import judgetap as jt
from judgetap.engines import load
from judgetap.engines.llm import LLMEngine, LLMError


def lp_response(tops):
    """An OpenAI-shaped chat completion whose first token has these top_logprobs."""
    content = [
        types.SimpleNamespace(
            top_logprobs=[
                types.SimpleNamespace(token=t, logprob=math.log(p)) for t, p in tops
            ]
        )
    ]
    choice = types.SimpleNamespace(
        logprobs=types.SimpleNamespace(content=content),
        message=types.SimpleNamespace(content="A"),
    )
    return types.SimpleNamespace(choices=[choice])


def json_response(text):
    msg = types.SimpleNamespace(content=text)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg, logprobs=None)]
    )


class FakeLiteLLM(types.ModuleType):
    def __init__(self, handler):
        super().__init__("litellm")
        self.handler = handler
        self.calls = []

        class UnsupportedParamsError(Exception):
            pass

        self.UnsupportedParamsError = UnsupportedParamsError

    def completion(self, **kw):
        self.calls.append(kw)
        return self.handler(self, kw)

    async def acompletion(self, **kw):
        return self.completion(**kw)

    def completion_cost(self, completion_response):
        raise ValueError("unpriced")


@pytest.fixture
def fake(monkeypatch):
    def install(handler):
        mod = FakeLiteLLM(handler)
        monkeypatch.setitem(sys.modules, "litellm", mod)
        return mod

    return install


def test_distribution_from_letter_logprobs_is_calibrated(fake):
    lit = fake(
        lambda m, kw: lp_response([("A", 0.7), (" b", 0.2), ("C", 0.05), ("Z", 0.05)])
    )
    d = jt.choice(
        "route?",
        ["billing", "tech", "sales"],
        engine=LLMEngine("openai/x", logprobs=True),
    )
    assert d.value == "billing" and d.calibrated
    assert d.distribution["billing"] == pytest.approx(0.7 / 0.95)
    assert d.distribution["tech"] == pytest.approx(0.2 / 0.95)
    call = lit.calls[0]
    assert (
        call["max_tokens"] == 1
        and call["logprobs"] is True
        and call["top_logprobs"] == 20
    )
    assert "A. billing" in call["messages"][1]["content"]


def test_missing_letters_get_zero(fake):
    fake(lambda m, kw: lp_response([("B", 0.9), ("x", 0.1)]))
    d = jt.yesno("q", engine=LLMEngine("openai/x", logprobs=True))
    assert d.distribution == {"yes": 0.0, "no": 1.0}


def test_no_option_letter_is_an_error_so_a_cascade_falls_through(fake):
    fake(lambda m, kw: lp_response([("x", 0.9), ("y", 0.1)]))
    with pytest.raises(LLMError, match="no probability"):
        jt.yesno("q", engine=LLMEngine("openai/x", logprobs=True))


def test_one_call_per_question(fake):
    lit = fake(lambda m, kw: lp_response([("A", 1.0)]))
    jt.batch(
        [jt.Question.yesno("a"), jt.Question.yesno("b")],
        engine=LLMEngine("openai/x", logprobs=True),
    )
    assert len(lit.calls) == 2


def test_provider_rejecting_logprobs_falls_back_to_json_for_good(fake):
    def handler(m, kw):
        if kw.get("logprobs"):
            raise m.UnsupportedParamsError("logprobs is not supported")
        return json_response('{"answers": {"q0": {"yes": 0.8, "no": 0.2}}}')

    lit = fake(handler)
    engine = LLMEngine("openai/x", logprobs=True)
    d = jt.yesno("q", engine=engine)
    assert d.value == "yes" and not d.calibrated and engine.logprobs is False
    jt.yesno("q", engine=engine)
    assert sum(1 for c in lit.calls if c.get("logprobs")) == 1  # not retried


def test_other_errors_are_not_swallowed(fake):
    def handler(m, kw):
        raise RuntimeError("rate limited")

    fake(handler)
    with pytest.raises(RuntimeError):
        jt.yesno("q", engine=LLMEngine("openai/x", logprobs=True))


def test_async_logprobs(fake):
    fake(lambda m, kw: lp_response([("A", 0.6), ("B", 0.4)]))
    d = asyncio.run(jt.ayesno("q", engine=LLMEngine("openai/x", logprobs=True)))
    assert d.value == "yes" and d.p == pytest.approx(0.6)


def test_dict_shaped_logprobs(fake):
    choice = types.SimpleNamespace(
        logprobs={"content": [{"top_logprobs": [{"token": "b", "logprob": 0.0}]}]}
    )
    resp = types.SimpleNamespace(choices=[choice])
    fake(lambda m, kw: resp)
    assert jt.yesno("q", engine=LLMEngine("openai/x", logprobs=True)).value == "no"


def test_too_many_options_for_letters():
    q = jt.Question.choice("q", [str(i) for i in range(27)])
    with pytest.raises(LLMError, match="up to 26"):
        LLMEngine("m", logprobs=True)._letter_call(q, None)


def test_spec_parsing():
    assert load("llm:openai/qwen?logprobs").logprobs is True
    e = load("llm:openai/qwen")
    assert e.logprobs is False and e.model == "openai/qwen"
    with pytest.raises(jt.JudgetapError):
        load("llm:openai/qwen?bogus")


def test_spec_can_be_saved_to_guard_toml(tmp_path):
    from judgetap.guard.install import write_engine

    write_engine(tmp_path, "llm:openai/qwen?logprobs")
    assert (
        'engine = "llm:openai/qwen?logprobs"' in (tmp_path / "guard.toml").read_text()
    )
