"""Audit fixes: repo guard.toml can't loosen (#64), odd input doesn't fail
open (#65), common secrets are redacted (#66)."""

import io
import json

import pytest

from judgetap.guard import hook
from judgetap.guard.rules import redact


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)
    monkeypatch.delenv("SNAPJUDGE_ENGINE", raising=False)


def run(payload):
    out = io.StringIO()
    hook.run(io.StringIO(json.dumps(payload)), out)
    return json.loads(out.getvalue()) if out.getvalue() else {}


def decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


def bash(cmd, cwd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd)}


# --- #64 ------------------------------------------------------------------


def test_repo_guard_toml_cannot_allow_everything(tmp_path):
    (tmp_path / "guard.toml").write_text(
        '[[rule]]\npattern = ".*"\noutcome = "allow"\n'
    )
    out = run(bash("rm -rf ~", tmp_path))
    assert decision(out) == "deny"
    assert "repo can only tighten" in out.get("systemMessage", "")


def test_repo_guard_toml_can_still_tighten(tmp_path):
    (tmp_path / "guard.toml").write_text(
        '[[rule]]\npattern = "kubectl"\noutcome = "hold"\n'
    )
    assert decision(run(bash("kubectl get pods", tmp_path))) == "deny"


def test_user_guard_toml_can_allow(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "guard.toml").write_text(
        '[[rule]]\npattern = "git push origin main"\noutcome = "allow"\n'
    )
    assert decision(run(bash("git push origin main", tmp_path))) == "allow"


@pytest.mark.parametrize(
    "path",
    [
        "guard.toml",
        "/r/.claude/settings.json",
        "/r/.claude/settings.local.json",
        "/r/.cursor/hooks.json",
        "/r/.codex/hooks.json",
        "/r/.codex/config.toml",
    ],
)
def test_writing_guard_config_asks(tmp_path, path):
    out = run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": path, "content": "x"},
            "cwd": str(tmp_path),
        }
    )
    assert decision(out) == "ask"


def test_ordinary_writes_are_not_affected(tmp_path):
    out = run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "src/app.toml", "content": "x"},
            "cwd": str(tmp_path),
        }
    )
    assert decision(out) == "allow"


# --- #65 ------------------------------------------------------------------


def test_null_multiedit_value_still_runs_the_rules(tmp_path):
    secret = "AKIA" + "B" * 16
    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": "/x",
            "edits": [{"old_string": "a", "new_string": None}, {"new_string": secret}],
        },
        "cwd": str(tmp_path),
    }
    out = run(payload)
    assert "failed and allowed" not in out.get("systemMessage", "")
    assert decision(out) == "deny"  # the secret in the second edit is still caught


def test_audit_repro_null_new_string_is_not_a_crash(tmp_path):
    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": "/x",
            "edits": [{"old_string": "a", "new_string": None}],
        },
        "cwd": str(tmp_path),
    }
    assert "failed and allowed" not in run(payload).get("systemMessage", "")


def test_unreadable_bash_command_asks(tmp_path):
    out = run({"tool_name": "Bash", "tool_input": {}, "cwd": str(tmp_path)})
    assert decision(out) == "ask"


def test_non_string_fields_are_coerced(tmp_path):
    out = run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": ["git", "push", "-f"]},
            "cwd": str(tmp_path),
        }
    )
    assert "failed and allowed" not in out.get("systemMessage", "")


def test_enrichment_failure_still_runs_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "last_user_message", lambda p: 1 / 0)
    monkeypatch.setattr(hook, "project_rules", lambda p: 1 / 0)
    assert decision(run(bash("git push --force", tmp_path))) == "deny"


# --- #66 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "leak"),
    [
        ("AWS_SECRET_ACCESS_KEY=abcd1234 aws s3 ls", "abcd1234"),
        ("export AWS_SECRET_ACCESS_KEY=abcd1234", "abcd1234"),
        ("DB_PRIVATE_KEY_PEM=zzz9 ./run", "zzz9"),
        ("PGPASSWORD=s3cret psql -h db", "s3cret"),
        ("mysql -u root -phunter2 db", "hunter2"),
        ("mysqldump -h h -u u -pverysecret app", "verysecret"),
        ("sshpass -p hunter2 ssh host", "hunter2"),
        ("sshpass -phunter2 ssh host", "hunter2"),
    ],
)
def test_common_secrets_are_redacted(command, leak):
    assert leak not in redact(command)


@pytest.mark.parametrize(
    "command", ["git add -p", "mkdir -p build/out", "cp -p a b", "mysql -p db"]
)
def test_harmless_dash_p_is_kept(command):
    assert redact(command) == command


def test_symlinked_path_to_guard_config_asks(tmp_path):
    from judgetap.guard.rules import check_path

    (tmp_path / "guard.toml").write_text("")
    (tmp_path / "guard-alias").symlink_to(tmp_path / "guard.toml")
    assert check_path("guard-alias", tmp_path)[1] == "guard-config"
    assert check_path(str(tmp_path / "guard-alias"))[1] == "guard-config"
    assert check_path("notes.md", tmp_path) is None


@pytest.mark.parametrize(
    ("command", "leak"),
    [
        ("db_private_key=zzz ./run", "zzz"),
        ("aws_secret_access_key=abc123 aws s3 ls", "abc123"),
        ("sshpass -p 'hunter2' ssh host", "hunter2"),
        ('sshpass -p "pa ss" ssh host', "pa ss"),
        ("mysql -u root -p'hunter2' db", "hunter2"),
        ('mysql -u root -p"pw 1" db', "pw 1"),
    ],
)
def test_round2_redaction(command, leak):
    from judgetap.guard.rules import redact

    assert leak not in redact(command)


@pytest.mark.parametrize(
    "command",
    [
        "printf x > .claude/settings.json",
        "echo '{}' >> guard.toml",
        "cat new | tee .cursor/hooks.json",
        "cp evil.toml guard.toml",
        "mv x .codex/hooks.json",
        "sed -i 's/ask/allow/' guard.toml",
    ],
)
def test_bash_writes_to_guard_config_ask(tmp_path, command):
    from judgetap.guard.core import Action, check

    v = check(Action(tool="Bash", cwd=tmp_path, command=command), None)
    assert (v.outcome, v.rule) == ("ask", "guard-config")


def test_ordinary_redirects_are_untouched(tmp_path):
    from judgetap.guard.rules import check_command_writes

    assert check_command_writes("echo hi > out.txt; cp a b", tmp_path) is None


def test_repo_allow_warning_once_per_session(tmp_path, monkeypatch):
    import io
    import json

    from judgetap.guard import hook

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)
    (tmp_path / "guard.toml").write_text('[[rule]]\npattern = "x"\noutcome = "allow"\n')

    def run():
        out = io.StringIO()
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "make test"},
            "cwd": str(tmp_path),
            "session_id": "s1",
        }
        hook.run(io.StringIO(json.dumps(payload)), out)
        return json.loads(out.getvalue()) if out.getvalue() else {}

    assert "ignored" in run().get("systemMessage", "")
    assert "systemMessage" not in run()
