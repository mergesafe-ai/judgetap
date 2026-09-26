import asyncio
import json
import sys
import types

import pytest

import snapjudge as sj
from snapjudge.engines.llm import LLMEngine, LLMError


def reply(content):
    msg = types.SimpleNamespace(content=content)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


@pytest.fixture
def fake_litellm(monkeypatch):
    mod = types.ModuleType("litellm")
    mod.calls = []
    mod.content = json.dumps({"answers": {"q0": {"yes": 0.7, "no": 0.3}}})

    def completion(**kw):
        mod.calls.append(kw)
        return reply(mod.content)

    async def acompletion(**kw):
        return completion(**kw)

    mod.completion = completion
    mod.acompletion = acompletion
    mod.completion_cost = lambda completion_response: 0.0002
    monkeypatch.setitem(sys.modules, "litellm", mod)
    return mod


def test_answers_are_marked_uncalibrated(fake_litellm):
    d = sj.yesno("Risky?", {"cmd": "ls"}, engine=LLMEngine("openai/x"))
    assert (d.value, d.calibrated, d.engine) == ("yes", False, "llm:openai/x")
    assert d.cost_usd == pytest.approx(0.0002)
    call = fake_litellm.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert '"ls"' in call["messages"][1]["content"]


def test_async_path(fake_litellm):
    d = asyncio.run(sj.ayesno("q", engine=LLMEngine("openai/x")))
    assert d.value == "yes"


def test_unparseable_reply_is_llm_error(fake_litellm):
    fake_litellm.content = "sure! yes"
    with pytest.raises(LLMError):
        sj.yesno("q", engine=LLMEngine("openai/x"))


def test_out_of_set_reply_is_rejected_by_core(fake_litellm):
    fake_litellm.content = json.dumps({"answers": {"q0": {"maybe": 1.0}}})
    with pytest.raises(sj.InvalidAnswerError):
        sj.yesno("q", engine=LLMEngine("openai/x"))


def test_missing_litellm_explains_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "litellm", None)
    with pytest.raises(LLMError, match=r"snapjudge\[llm\]"):
        sj.yesno("q", engine=LLMEngine("openai/x"))
