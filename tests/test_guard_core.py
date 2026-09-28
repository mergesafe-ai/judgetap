from pathlib import Path

import pytest

from judgetap.guard.core import Action, check, load_user_rules, project_rules
from judgetap.testing import StaticEngine


def judge(**p):
    """An engine answering each guard question with the given p(yes)."""

    def answer(q, ctx):
        text = q.text.lower()
        key = (
            "irreversible"
            if "undo" in text
            else "off_task"
            if "unrelated" in text
            else "breaks_rule"
        )
        py = p.get(key, 0.05)
        return {"yes": py, "no": 1 - py}

    return StaticEngine(answer, name="fake")


def act(command=None, **kw):
    return Action(
        tool="Bash" if command else "Write", cwd=Path("/w"), command=command, **kw
    )


def test_rules_decide_before_the_engine():
    engine = judge()
    v = check(act("git push --force"), engine)
    assert (v.outcome, v.layer, v.rule) == ("hold", "rules", "force-push")
    assert engine.calls == []


def test_read_only_commands_skip_the_judge():
    engine = judge()
    assert check(act("git status"), engine).layer == "skip"
    assert engine.calls == []


def test_chained_read_only_command_is_still_judged():
    engine = judge()
    check(act("cat x > y"), engine)
    assert len(engine.calls) == 1


def test_no_engine_means_rules_only():
    v = check(act("make deploy"), None)
    assert (v.outcome, v.layer) == ("allow", "none")


@pytest.mark.parametrize(
    ("p", "outcome"),
    [
        ({"irreversible": 0.9}, "hold"),
        ({"breaks_rule": 0.85}, "hold"),
        ({"off_task": 0.95}, "ask"),
        ({"off_task": 0.85}, "allow"),
        ({}, "allow"),
    ],
)
def test_judge_thresholds(p, outcome):
    v = check(act("make deploy", project_rules="never deploy on fridays"), judge(**p))
    assert v.outcome == outcome and v.layer == "judge"
    assert v.engine == "fake"


def test_breaks_rule_not_asked_without_project_rules():
    engine = judge(breaks_rule=0.99)
    v = check(act("make deploy"), engine)
    assert v.outcome == "allow"
    assert len(engine.calls[0][0]) == 2


def test_engine_failure_allows_and_reports():
    class Broken(StaticEngine):
        def decide(self, questions, context):
            raise TimeoutError("slow")

    v = check(act("make deploy"), Broken(lambda q, c: {}, name="b"))
    assert v.outcome == "allow" and "TimeoutError" in v.error


def test_user_rules_can_hold_and_override(tmp_path):
    cfg = tmp_path / "guard.toml"
    cfg.write_text(
        '[[rule]]\npattern = "kubectl delete"\noutcome = "hold"\nreason = "no kubectl deletes"\n'
        '[[rule]]\npattern = "git push origin main"\noutcome = "allow"\n'
    )
    rules = load_user_rules(cfg)
    assert (
        check(act("kubectl delete pod x"), None, rules).reason == "no kubectl deletes"
    )
    assert check(act("git push origin main"), None, rules).outcome == "allow"


def test_bad_user_rule_outcome_is_rejected(tmp_path):
    cfg = tmp_path / "guard.toml"
    cfg.write_text('[[rule]]\npattern = "x"\noutcome = "block"\n')
    with pytest.raises(ValueError):
        load_user_rules(cfg)


def test_project_rules_found_walking_up_to_repo_root(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("use worktrees")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    assert project_rules(sub) == "use worktrees"


@pytest.mark.parametrize(
    "command",
    [
        "git branch -D feature",
        "git remote remove origin",
        "git tag -d v1",
        "find . -delete",
    ],
)
def test_mutating_listing_commands_are_judged(command):
    engine = judge()
    v = check(act(command), engine)
    assert v.layer == "judge" and len(engine.calls) == 1


@pytest.mark.parametrize(
    "command", ["git branch", "git branch -a", "git remote -v", "git tag -l"]
)
def test_listing_commands_skip_the_judge(command):
    assert check(act(command), judge()).layer == "skip"


def test_multiline_read_only_prefix_is_still_judged():
    engine = judge()
    v = check(act("git status\nmake deploy"), engine)
    assert v.layer == "judge"


def test_process_substitution_is_judged():
    engine = judge()
    assert check(act("cat <(make deploy)"), engine).layer == "judge"


def test_rules_only_asks_for_unbounded_programs():
    v = check(act("terraform apply -auto-approve"), None)
    assert (v.outcome, v.rule) == ("ask", "rules-only")


def test_engine_configured_judges_instead_of_asking():
    engine = judge()
    v = check(act("terraform apply -auto-approve"), engine)
    assert v.layer == "judge" and v.outcome == "allow"


def test_unreadable_project_rules_are_skipped(tmp_path):
    (tmp_path / ".git").mkdir()
    rules = tmp_path / "guard.md"
    rules.write_text("x")
    rules.chmod(0)
    (tmp_path / "AGENTS.md").write_text("use worktrees")
    try:
        assert project_rules(tmp_path) == "use worktrees"
    finally:
        rules.chmod(0o600)
