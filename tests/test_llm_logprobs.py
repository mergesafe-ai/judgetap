import asyncio
import math
import sys
import time
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


def test_distribution_from_letter_logprobs(fake):
    lit = fake(
        lambda m, kw: lp_response([("A", 0.7), (" b", 0.2), ("C", 0.05), ("Z", 0.05)])
    )
    d = jt.choice(
        "route?",
        ["billing", "tech", "sales"],
        engine=LLMEngine("openai/x", logprobs=True),
    )
    assert (
        d.value == "billing" and not d.calibrated
    )  # token probabilities, not calibrated
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
        LLMEngine("m", logprobs=True)._check_options([q])


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


def test_option_limit_is_local_and_keeps_logprobs_enabled(fake):
    lit = fake(lambda m, kw: lp_response([("A", 0.9), ("B", 0.1)]))
    engine = LLMEngine("openai/x", logprobs=True)
    too_many = [str(i) for i in range(27)]
    with pytest.raises(LLMError, match="up to 26 options"):
        jt.choice("q", too_many, engine=engine)
    assert engine.logprobs is True and lit.calls == []  # no request, no fallback
    d = jt.choice("q", ["a", "b"], engine=engine)
    assert d.value == "a" and lit.calls[-1].get("logprobs")


def test_bad_request_mentioning_logprobs_falls_back(fake):
    def handler(m, kw):
        if kw.get("logprobs"):
            raise m.BadRequestError("this model does not support logprobs")
        return json_response('{"answers": {"q0": {"yes": 0.8, "no": 0.2}}}')

    lit = fake(handler)

    class BadRequestError(Exception):
        pass

    lit.BadRequestError = BadRequestError
    engine = LLMEngine("openai/x", logprobs=True)
    assert jt.yesno("q", engine=engine).value == "yes" and engine.logprobs is False


def test_batch_questions_run_concurrently_in_order(fake):
    import threading

    lock, state = threading.Lock(), {"now": 0, "peak": 0}
    gate = threading.Barrier(3, timeout=2)

    def handler(m, kw):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        gate.wait()  # only passes if all three requests are in flight together
        with lock:
            state["now"] -= 1
        letter = "A" if "first" in kw["messages"][1]["content"] else "B"
        return lp_response([(letter, 1.0)])

    fake(handler)
    qs = [
        jt.Question.yesno("first"),
        jt.Question.yesno("second"),
        jt.Question.yesno("third"),
    ]
    out = jt.batch(qs, engine=LLMEngine("openai/x", logprobs=True))
    assert state["peak"] == 3
    assert [d.value for d in out] == ["yes", "no", "no"]


def test_async_batch_runs_concurrently(fake):
    lit = fake(lambda m, kw: lp_response([("A", 1.0)]))
    active = {"now": 0, "peak": 0}

    async def acompletion(**kw):
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        await asyncio.sleep(0.01)
        active["now"] -= 1
        return lp_response([("A", 1.0)])

    lit.acompletion = acompletion
    qs = [jt.Question.yesno(str(i)) for i in range(5)]
    asyncio.run(jt.abatch(qs, engine=LLMEngine("openai/x", logprobs=True)))
    assert active["peak"] == 5


def test_plain_dict_response_is_read(fake):
    def handler(m, kw):
        return {
            "choices": [
                {
                    "logprobs": {
                        "content": [{"top_logprobs": [{"token": "B", "logprob": 0.0}]}]
                    }
                }
            ]
        }

    fake(handler)
    d = jt.choice("q", ["a", "b"], engine=LLMEngine("openai/x", logprobs=True))
    assert d.value == "b"


def test_each_request_is_reported_as_a_call(fake):
    fake(lambda m, kw: lp_response([("A", 1.0)]))
    qs = [jt.Question.yesno(str(i)) for i in range(3)]
    out = jt.batch(qs, engine=LLMEngine("openai/x", logprobs=True))
    assert len(out[0].calls) == 3
    assert all(c.engine == "llm:openai/x" and c.questions == 1 for c in out[0].calls)


@pytest.mark.parametrize("spec", ["llm:?logprobs", "llm: ?logprobs", "llm: "])
def test_empty_model_spec_is_rejected(spec):
    with pytest.raises(jt.JudgetapError):
        load(spec)


def test_unsupported_param_not_about_logprobs_is_raised(fake):
    def handler(m, kw):
        raise m.UnsupportedParamsError("temperature is not supported")

    fake(handler)
    engine = LLMEngine("openai/x", logprobs=True)
    with pytest.raises(Exception, match="temperature"):
        jt.yesno("q", engine=engine)
    assert engine.logprobs is True


def test_fallback_keeps_the_rejected_calls_with_the_json_call(fake):
    def handler(m, kw):
        if kw.get("logprobs"):
            raise m.UnsupportedParamsError("logprobs is not supported")
        return json_response(
            '{"answers": {"q0": {"yes": 0.8, "no": 0.2}, "q1": {"yes": 0.1, "no": 0.9}}}'
        )

    fake(handler)
    out = jt.batch(
        [jt.Question.yesno("a"), jt.Question.yesno("b")],
        engine=LLMEngine("openai/x", logprobs=True),
    )
    calls = out[0].calls
    assert [c.ok for c in calls][-1] is True and calls[-1].questions == 2
    assert calls[:-1] and all(not c.ok and c.questions == 1 for c in calls[:-1])


def test_failed_requests_are_reported_on_the_error(fake):
    def handler(m, kw):
        if "b" in kw["messages"][1]["content"].split("Question: ")[1][:2]:
            raise RuntimeError("rate limited")
        return lp_response([("A", 1.0)])

    fake(handler)
    with pytest.raises(RuntimeError) as info:
        jt.batch(
            [jt.Question.yesno("a"), jt.Question.yesno("b")],
            engine=LLMEngine("openai/x", logprobs=True),
        )
    calls = info.value.calls
    assert len(calls) == 2 and sorted(c.ok for c in calls) == [False, True]


def test_first_failure_cancels_requests_not_yet_sent(fake, monkeypatch):
    import judgetap.engines.llm as llm_mod

    monkeypatch.setattr(llm_mod, "MAX_CONCURRENT", 1)

    def handler(m, kw):
        raise RuntimeError("down")

    lit = fake(handler)
    with pytest.raises(RuntimeError):
        jt.batch(
            [jt.Question.yesno(str(i)) for i in range(5)],
            engine=LLMEngine("openai/x", logprobs=True),
        )
    assert len(lit.calls) < 5


def test_async_first_failure_cancels_the_rest(fake):
    started = []

    class Slow(FakeLiteLLM):
        async def acompletion(self, **kw):
            started.append(kw)
            if len(started) == 1:
                raise RuntimeError("down")
            await asyncio.sleep(10)

    mod = Slow(lambda m, kw: None)
    sys.modules["litellm"] = mod
    try:
        engine = LLMEngine("openai/x", logprobs=True)
        qs = [jt.Question.yesno(str(i)) for i in range(3)]

        async def go():
            return await asyncio.wait_for(engine.adecide(qs, "s"), timeout=5)

        with pytest.raises(RuntimeError) as info:
            asyncio.run(go())
        assert len(info.value.calls) == 3
        assert not any(c.ok for c in info.value.calls)
    finally:
        del sys.modules["litellm"]


def test_later_failure_is_seen_while_an_earlier_question_is_slow(fake, monkeypatch):
    import threading

    import judgetap.engines.llm as llm_mod

    monkeypatch.setattr(llm_mod, "MAX_CONCURRENT", 2)
    lock, first = threading.Lock(), []

    def handler(m, kw):
        with lock:
            slow = not first
            first.append(True)
        if slow:
            time.sleep(1.0)  # the early question is slow...
            return lp_response([("A", 0.9), ("B", 0.1)])
        raise RuntimeError("down")  # ...while a later one fails at once

    lit = fake(handler)
    with pytest.raises(RuntimeError):
        jt.batch(
            [jt.Question.yesno(str(i)) for i in range(8)],
            engine=LLMEngine("openai/x", logprobs=True),
        )
    # Waiting in question order would let the second worker drain all 8.
    assert len(lit.calls) <= 3


def test_async_provider_rejecting_logprobs_falls_back_to_json(fake):
    def handler(m, kw):
        if kw.get("logprobs"):
            raise m.UnsupportedParamsError("logprobs is not supported")
        return json_response('{"answers": {"q0": {"yes": 0.8, "no": 0.2}}}')

    lit = fake(handler)
    engine = LLMEngine("openai/x", logprobs=True)
    d = asyncio.run(jt.ayesno("q", engine=engine))
    assert d.value == "yes" and not d.calibrated and engine.logprobs is False
    assert [c.ok for c in d.calls] == [False, True]  # rejected call kept
    asyncio.run(jt.ayesno("q", engine=engine))
    assert sum(1 for c in lit.calls if c.get("logprobs")) == 1


def test_async_other_errors_are_not_swallowed(fake):
    fake(lambda m, kw: (_ for _ in ()).throw(RuntimeError("rate limited")))
    engine = LLMEngine("openai/x", logprobs=True)
    with pytest.raises(RuntimeError):
        asyncio.run(jt.ayesno("q", engine=engine))
    assert engine.logprobs is True


def test_sync_stops_submitting_after_a_failure(fake, monkeypatch):
    import judgetap.engines.llm as llm_mod

    monkeypatch.setattr(llm_mod, "MAX_CONCURRENT", 2)

    def handler(m, kw):
        raise RuntimeError("down")

    lit = fake(handler)
    with pytest.raises(RuntimeError):
        jt.batch(
            [jt.Question.yesno(str(i)) for i in range(10)],
            engine=LLMEngine("openai/x", logprobs=True),
        )
    assert len(lit.calls) <= 2


def test_a_finished_request_frees_its_slot_while_another_is_slow(fake, monkeypatch):
    import threading

    import judgetap.engines.llm as llm_mod

    monkeypatch.setattr(llm_mod, "MAX_CONCURRENT", 2)
    lock, seen, rest_done = threading.Lock(), [], threading.Event()

    def handler(m, kw):
        with lock:
            n = len(seen)
            seen.append(n)
        if n == 0:
            # The slow question finishes only once the others got through,
            # which they can only do if each finished call frees a slot.
            assert rest_done.wait(5)
        elif n == 5:
            rest_done.set()
        return lp_response([("A", 0.9), ("B", 0.1)])

    fake(handler)
    ds = jt.batch(
        [jt.Question.yesno(str(i)) for i in range(6)],
        engine=LLMEngine("openai/x", logprobs=True),
    )
    assert len(ds) == 6 and rest_done.is_set()
