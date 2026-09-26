import pytest

from snapjudge import InvalidQuestionError, Question


@pytest.mark.parametrize(
    "build",
    [
        lambda: Question.choice("q", ["only"]),
        lambda: Question.choice("q", [str(i) for i in range(256)]),
        lambda: Question.choice("q", ["a", "a"]),
        lambda: Question.choice("q", ["a", " "]),
        lambda: Question.choice("  ", ["a", "b"]),
        lambda: Question.score("q", ["one"]),
        lambda: Question.score("q", [str(i) for i in range(11)]),
        lambda: Question("yesno", "q", ("no", "yes")),
    ],
)
def test_malformed_questions_fail_before_any_engine(build):
    with pytest.raises(InvalidQuestionError):
        build()


def test_limits_are_inclusive():
    Question.choice("q", [str(i) for i in range(255)])
    Question.score("q", ["lo", "hi"])
    Question.score("q", [str(i) for i in range(10)])


def test_level_and_p_yes_only_on_their_kinds():
    from snapjudge.types import Decision

    q = Question.choice("q", ["a", "b"])
    d = Decision(q, "a", 1.0, {"a": 1.0, "b": 0.0}, "e", 0.0)
    with pytest.raises(AttributeError):
        _ = d.p_yes
    with pytest.raises(AttributeError):
        _ = d.level


@pytest.mark.parametrize(
    "build",
    [
        lambda: Question("bogus", "q", ("a", "b")),
        lambda: Question.choice("route", [1, 2]),
        lambda: Question.choice(None, ["a", "b"]),
    ],
)
def test_wrong_kinds_and_types_raise_invalid_question(build):
    with pytest.raises(InvalidQuestionError):
        build()


@pytest.mark.parametrize("bad", [None, 3, "ab"])
def test_non_list_options_raise_invalid_question(bad):
    with pytest.raises(InvalidQuestionError):
        Question.choice("q", bad)
    with pytest.raises(InvalidQuestionError):
        Question.score("q", bad)


@pytest.mark.parametrize("bad", ["ab", {"low", "high"}, (o for o in "ab")])
def test_public_api_rejects_strings_sets_and_generators(bad):
    import snapjudge as sj
    from snapjudge.testing import StaticEngine

    engine = StaticEngine(lambda q, c: {o: 1 / len(q.options) for o in q.options})
    with pytest.raises(InvalidQuestionError):
        sj.choice("q", bad, engine=engine)
    with pytest.raises(InvalidQuestionError):
        sj.score("q", bad, engine=engine)
