import json

import pytest

import judgetap as sj
from judgetap.evaluate import evaluate, load_cases, main, to_json, to_markdown
from judgetap.testing import StaticEngine


def write_cases(tmp_path, rows):
    path = tmp_path / "cases.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


ROWS = [
    {
        "kind": "yesno",
        "question": "irreversible?",
        "context": {"cmd": "rm -rf /"},
        "label": "yes",
    },
    {
        "kind": "yesno",
        "question": "irreversible?",
        "context": {"cmd": "ls"},
        "label": "no",
    },
    {
        "kind": "choice",
        "question": "route",
        "options": ["a", "b"],
        "context": "x",
        "label": "b",
    },
    {
        "kind": "score",
        "question": "risk",
        "levels": ["lo", "hi"],
        "context": "y",
        "label": "hi",
    },
]


def test_load_cases_all_kinds(tmp_path):
    cases = load_cases(write_cases(tmp_path, ROWS))
    assert [c.question.kind for c in cases] == ["yesno", "yesno", "choice", "score"]


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "yesno", "question": "q", "label": "maybe"},
        {"kind": "vote", "question": "q", "label": "yes"},
        {"kind": "choice", "question": "q", "label": "a"},
    ],
)
def test_bad_case_names_the_line(tmp_path, row):
    with pytest.raises(sj.JudgetapError, match=r"cases.jsonl:2"):
        load_cases(write_cases(tmp_path, [ROWS[0], row]))


def test_perfect_confident_engine():
    def oracle(q, ctx):
        right = {
            "irreversible?": "yes" if "rm" in str(ctx) else "no",
            "route": "b",
            "risk": "hi",
        }[q.text]
        return {o: (1.0 if o == right else 0.0) for o in q.options}

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        cases = load_cases(write_cases(Path(d), ROWS))
    r = evaluate(cases, StaticEngine(oracle, name="oracle"))
    assert (r.accuracy, r.ece, r.errors, r.answered) == (1.0, 0.0, 0, 4)


def test_overconfident_wrong_engine_has_high_ece(tmp_path):
    cases = load_cases(write_cases(tmp_path, ROWS[:2]))
    always_yes = StaticEngine(lambda q, c: {"yes": 0.95, "no": 0.05}, name="yes-man")
    r = evaluate(cases, always_yes)
    assert r.accuracy == 0.5
    assert r.ece == pytest.approx(0.45)
    assert r.reliability == [
        {"bin": "0.9-1.0", "n": 2, "confidence": pytest.approx(0.95), "accuracy": 0.5}
    ]


def test_errors_are_counted_not_raised(tmp_path):
    class Flaky(StaticEngine):
        def decide(self, questions, context):
            if context == "x":
                raise TimeoutError
            return super().decide(questions, context)

    cases = load_cases(write_cases(tmp_path, ROWS))
    r = evaluate(
        cases, Flaky(lambda q, c: {o: 1 / len(q.options) for o in q.options}, name="f")
    )
    assert (r.errors, r.answered) == (1, 3)


def test_cost_per_1k_only_when_every_answer_is_priced(tmp_path):
    cases = load_cases(write_cases(tmp_path, ROWS[:2]))
    r = evaluate(cases, StaticEngine(lambda q, c: {"yes": 0.5, "no": 0.5}))
    assert r.usd_per_1k is None


def test_reports_render(tmp_path):
    cases = load_cases(write_cases(tmp_path, ROWS[:2]))
    r = evaluate(cases, StaticEngine(lambda q, c: {"yes": 0.95, "no": 0.05}, name="e"))
    md = to_markdown([r])
    assert "| e | 2/2 | 0 | 50.0% | 0.450 |" in md and "reliability" in md
    assert json.loads(to_json([r]))[0]["engine"] == "e"


def test_cli_runs_named_engines(tmp_path, monkeypatch, capsys):
    from judgetap import engines

    monkeypatch.setattr(
        engines,
        "load",
        lambda spec: StaticEngine(
            lambda q, c: {o: 1 / len(q.options) for o in q.options}, name=spec
        ),
    )
    assert main([str(write_cases(tmp_path, ROWS)), "--engines", "a,b"]) == 0
    out = capsys.readouterr().out
    assert "| a |" in out and "| b |" in out


def test_percentile_interpolates():
    from judgetap.evaluate import percentile

    assert percentile([10, 100], 0.5) == 55
    assert percentile([10, 20, 30, 40], 0.95) == pytest.approx(38.5)
    assert percentile([7], 0.95) == 7


def test_string_options_line_is_rejected(tmp_path):
    with pytest.raises(sj.JudgetapError, match="must be a JSON list"):
        load_cases(
            write_cases(
                tmp_path,
                [{"kind": "choice", "question": "q", "options": "ab", "label": "a"}],
            )
        )
