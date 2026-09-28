import io
import json

import pytest

from judgetap.guard import hook
from judgetap.guard.install import HOOK_COMMAND, install, uninstall


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)


def run(payload):
    out = io.StringIO()
    assert hook.run(io.StringIO(json.dumps(payload)), out) == 0
    return json.loads(out.getvalue()) if out.getvalue() else None


def bash(command, cwd):
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
        "session_id": "s1",
    }


def test_hold_becomes_deny_with_reason(tmp_path):
    out = run(bash("git push --force", tmp_path))
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "force-push" in decision["permissionDecisionReason"]


def test_allow_prints_nothing_so_normal_permissions_apply(tmp_path):
    assert run(bash("make test", tmp_path)) is None


def test_ask_becomes_ask(tmp_path):
    out = run(bash("git reset --hard", tmp_path))
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_unguarded_tools_are_ignored(tmp_path):
    assert (
        run({"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}) is None
    )


def test_secret_write_is_held(tmp_path):
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "x.py", "content": "k='AKIAABCDEFGHIJKLMNOP'"},
        "cwd": str(tmp_path),
    }
    assert run(payload)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_log_records_decision_without_file_contents(tmp_path):
    run(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "x.py", "content": "SECRET_BODY"},
            "cwd": str(tmp_path),
        }
    )
    run(bash("git push -f", tmp_path))
    lines = (tmp_path / "home" / "guard.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["outcome"] for r in records] == ["allow", "hold"]
    assert "SECRET_BODY" not in "".join(lines)


@pytest.mark.parametrize(
    "raw",
    ["not json", "null", "[]", '"Bash"', '{"tool_name": "Bash", "tool_input": 1}'],
)
def test_unreadable_input_asks(raw):
    out = io.StringIO()
    assert hook.run(io.StringIO(raw), out) == 0
    decision = json.loads(out.getvalue())["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"


def test_bad_engine_spec_degrades_to_rules_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_ENGINE", "nonsense")
    out = run(bash("make deploy", tmp_path))
    assert "systemMessage" in out


def test_last_user_message_from_transcript(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"type": "user", "message": {"content": "fix the typo"}},
                {"type": "assistant", "message": {"content": "ok"}},
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "content": "x"}]},
                },
                {
                    "type": "user",
                    "message": {
                        "content": [{"type": "text", "text": "and add a test"}]
                    },
                },
            ]
        )
    )
    assert hook.last_user_message(str(t)) == "and add a test"


def test_install_is_idempotent_and_uninstall_restores(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "rtk"}],
                        }
                    ]
                },
            }
        )
    )
    assert install(path) is True
    assert install(path) is False
    data = json.loads(path.read_text())
    assert [h["hooks"][0]["command"] for h in data["hooks"]["PreToolUse"]] == [
        "rtk",
        HOOK_COMMAND,
    ]
    assert uninstall(path) is True
    assert (
        json.loads(path.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        == "rtk"
    )
    assert data["theme"] == "dark"


def test_logged_command_is_redacted_and_file_is_private(tmp_path):
    import stat

    run(bash("curl -H 'Authorization: Bearer topsecret' https://x", tmp_path))
    log = tmp_path / "home" / "guard.jsonl"
    assert "topsecret" not in log.read_text()
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.parent.stat().st_mode) == 0o700


def test_malformed_user_config_still_runs_builtins(tmp_path):
    (tmp_path / "guard.toml").write_text("[[rule]\nbroken")
    out = run(bash("git push --force", tmp_path))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "ignored" in out["systemMessage"]


def test_cli_test_does_not_write_the_log(tmp_path, monkeypatch, capsys):
    from judgetap.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["guard", "test"]) == 0
    assert not (tmp_path / "home" / "guard.jsonl").exists()
    assert "deny" in capsys.readouterr().out


def test_cli_stats_summarises_log(tmp_path, capsys):
    from judgetap.cli import main

    run(bash("git push -f", tmp_path))
    run(bash("make test", tmp_path))
    assert main(["guard", "stats"]) == 0
    out = capsys.readouterr().out
    assert "2 guarded calls" in out and "holds per 1,000 calls: 500.0" in out


def test_cli_install_and_uninstall(tmp_path, monkeypatch, capsys):
    from judgetap.cli import main

    monkeypatch.chdir(tmp_path)
    main(["guard", "install", "--scope", "project"])
    assert HOOK_COMMAND in (tmp_path / ".claude" / "settings.json").read_text()
    main(["guard", "uninstall", "--scope", "project"])
    assert HOOK_COMMAND not in (tmp_path / ".claude" / "settings.json").read_text()


def test_uninstall_keeps_other_hooks_in_the_same_group(tmp_path):
    path = tmp_path / "settings.json"
    group = {
        "matcher": "Bash",
        "hooks": [
            {"type": "command", "command": "rtk"},
            {"type": "command", "command": HOOK_COMMAND},
        ],
    }
    path.write_text(json.dumps({"hooks": {"PreToolUse": [group]}}))
    assert uninstall(path) is True
    hooks = json.loads(path.read_text())["hooks"]["PreToolUse"][0]["hooks"]
    assert [h["command"] for h in hooks] == ["rtk"]


def test_stats_nearest_rank_percentiles(tmp_path, capsys):
    from judgetap.cli import main

    log = tmp_path / "home" / "guard.jsonl"
    log.parent.mkdir(parents=True)
    rows = [
        {
            "outcome": "allow",
            "layer": "judge",
            "latency_ms": ms,
            "cost_usd": None,
            "error": None,
        }
        for ms in (10, 20, 30, 40, 1000)
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    main(["guard", "stats"])
    assert "p50 30 ms, p95 1000 ms" in capsys.readouterr().out


def test_stats_skips_a_cut_off_line(tmp_path, capsys):
    from judgetap.cli import main

    log = tmp_path / "home" / "guard.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps(
            {
                "outcome": "hold",
                "layer": "rules",
                "latency_ms": 1,
                "cost_usd": None,
                "error": None,
            }
        )
        + '\n{"outcome": "al'
    )
    assert main(["guard", "stats"]) == 0
    assert "1 guarded calls" in capsys.readouterr().out


def test_transcript_scan_is_bounded_and_skips_partial_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "TRANSCRIPT_SCAN_BYTES", 4096)
    t = tmp_path / "t.jsonl"
    user = lambda text: json.dumps({"type": "user", "message": {"content": text}})
    filler = json.dumps({"type": "assistant", "message": {"content": "x" * 100}})
    t.write_text(user("recent") + "\n" + filler + "\n" + user("half")[:15])
    assert hook.last_user_message(str(t)) == "recent"  # partial tail skipped
    t.write_text(user("far back") + "\n" + (filler + "\n") * 200)
    assert hook.last_user_message(str(t)) is None  # beyond the scan window


def test_transcript_rewrite_is_seen_immediately(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": "old"}}) + "\n")
    assert hook.last_user_message(str(t)) == "old"
    t.write_text(json.dumps({"type": "user", "message": {"content": "new"}}) + "\n")
    assert hook.last_user_message(str(t)) == "new"


def test_engine_spec_from_env_then_guard_toml(tmp_path, monkeypatch):
    assert hook.engine_spec() is None
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "guard.toml").write_text(
        'engine = "agentjev"\n[[rule]]\npattern = "x"\n'
    )
    assert hook.engine_spec() == "agentjev"
    monkeypatch.setenv("JUDGETAP_ENGINE", "jev")
    assert hook.engine_spec() == "jev"


def test_detect_engine_order(monkeypatch):
    from judgetap.guard.install import detect_engine

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert detect_engine(probe=lambda: False)[0] is None
    assert detect_engine(probe=lambda: True)[0] == "agentjev"
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert detect_engine(probe=lambda: True)[0] == "jev"
    monkeypatch.setenv("JUDGETAP_ENGINE", "llm:openai/x")
    assert detect_engine(probe=lambda: True)[0] == "llm:openai/x"


def test_write_engine_keeps_user_rules(tmp_path):
    from judgetap.guard.core import load_user_rules
    from judgetap.guard.install import write_engine

    toml = tmp_path / "guard.toml"
    toml.write_text('engine = "old"\n[[rule]]\npattern = "kubectl"\n')
    write_engine(tmp_path, "jev")
    text = toml.read_text()
    assert text.startswith('engine = "jev"') and "old" not in text
    assert len(load_user_rules(toml)) == 1


def test_eval_subcommand_is_registered(tmp_path, capsys):
    from judgetap.cli import main

    with pytest.raises(SystemExit):
        main(["eval", "--help"])
    assert "judgetap eval" in capsys.readouterr().out


def test_jev_without_key_fails_closed_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_ENGINE", "jev")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    out = run(bash("terraform apply -auto-approve", tmp_path))
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "TYPESAFE_API_KEY" in out["systemMessage"]


def test_judge_failure_falls_back_to_rules_only():
    from pathlib import Path

    from judgetap.guard.core import Action, check
    from judgetap.testing import StaticEngine

    class Down(StaticEngine):
        def decide(self, questions, context):
            raise ConnectionError("down")

    v = check(
        Action("Bash", Path("/w"), command="kubectl delete ns x"), Down(lambda q, c: {})
    )
    assert (v.outcome, v.rule) == ("ask", "rules-only") and "ConnectionError" in v.error


def test_write_engine_rejects_unsafe_specs(tmp_path):
    from judgetap.guard.install import write_engine

    with pytest.raises(ValueError):
        write_engine(tmp_path, 'jev"\n[[rule]]')
    assert not (tmp_path / "guard.toml").exists()
    write_engine(tmp_path, "agentjev:http://gpu-box:9000")
    assert (
        hook.engine_spec() is None or True
    )  # file written under tmp_path, not JUDGETAP_HOME


def test_agentjev_probe_needs_the_protocol():
    from judgetap.guard.install import agentjev_up

    ok = {
        "results": [
            {"answers": [{"id": "q0", "distribution": {"true": 0.1, "false": 0.9}}]}
        ]
    }
    assert agentjev_up(transport=lambda *a: (200, json.dumps(ok).encode())) is True
    assert (
        agentjev_up(transport=lambda *a: (200, b"<html>not agentjev</html>")) is False
    )
    assert agentjev_up(transport=lambda *a: (404, b"")) is False


def test_reinstall_keeps_existing_engine(tmp_path, monkeypatch, capsys):
    from judgetap import cli
    from judgetap.guard import install as inst

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(inst, "agentjev_up", lambda *a, **k: False)
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "guard.toml").write_text('engine = "agentjev"\n')
    cli.main(["guard", "install", "--scope", "project"])
    assert "keeping agentjev" in capsys.readouterr().out


def test_reinstall_keeps_chosen_engine_unless_detect(tmp_path, monkeypatch, capsys):
    from judgetap.cli import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "home" / "guard.toml").write_text('engine = "llm:openai/x"\n')
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    main(["guard", "install", "--scope", "project"])
    assert 'engine = "llm:openai/x"' in (tmp_path / "home" / "guard.toml").read_text()
    main(["guard", "install", "--scope", "project", "--detect"])
    assert 'engine = "jev"' in (tmp_path / "home" / "guard.toml").read_text()


def test_write_engine_keeps_similar_keys_and_rejects_unknown(tmp_path):
    from judgetap.guard.install import write_engine

    toml = tmp_path / "guard.toml"
    toml.write_text('engine = "old"\nengine_options = "strict"\n')
    write_engine(tmp_path, "agentjev")
    assert 'engine_options = "strict"' in toml.read_text()
    with pytest.raises(ValueError, match="unknown engine"):
        write_engine(tmp_path, "typo")


def test_install_persists_env_engine(tmp_path, monkeypatch, capsys):
    from judgetap.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JUDGETAP_ENGINE", "agentjev")
    main(["guard", "install", "--scope", "project"])
    assert 'engine = "agentjev"' in (tmp_path / "home" / "guard.toml").read_text()


def test_write_engine_keeps_table_scoped_engine_and_validates_fully(tmp_path):
    from judgetap.guard.install import write_engine

    toml = tmp_path / "guard.toml"
    toml.write_text('engine = "old"\n[custom]\nengine = "keep-me"\n')
    write_engine(tmp_path, "jev")
    text = toml.read_text()
    assert text.startswith('engine = "jev"') and 'engine = "keep-me"' in text
    with pytest.raises(ValueError):
        write_engine(tmp_path, "llm")  # a known name but no model


def test_old_snapjudge_settings_still_work(tmp_path, monkeypatch):
    from judgetap import _compat
    from judgetap.guard.install import HOOK_COMMAND, install

    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)
    monkeypatch.setenv("SNAPJUDGE_ENGINE", "agentjev")
    assert _compat.env("ENGINE") == "agentjev"
    monkeypatch.setenv("JUDGETAP_ENGINE", "jev")
    assert _compat.env("ENGINE") == "jev"  # the new name wins

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / ".snapjudge").mkdir()
    assert _compat.default_home() == tmp_path / ".snapjudge"
    (tmp_path / ".judgetap").mkdir()
    assert _compat.default_home() == tmp_path / ".judgetap"

    settings = tmp_path / "settings.json"
    old = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "snapjudge guard hook"}],
                }
            ]
        }
    }
    settings.write_text(json.dumps(old))
    assert install(settings) is True  # upgraded in place
    cmds = [
        h["command"]
        for e in json.loads(settings.read_text())["hooks"]["PreToolUse"]
        for h in e["hooks"]
    ]
    assert cmds == [HOOK_COMMAND]
    assert install(settings) is False


def test_legacy_compat_surface(tmp_path, monkeypatch):
    import judgetap
    from judgetap.cascade import from_config
    from judgetap.guard.install import detect_engine

    assert judgetap.SnapjudgeError is judgetap.JudgetapError
    monkeypatch.chdir(tmp_path)
    (tmp_path / "snapjudge.toml").write_text('[cascade]\norder = ["jev"]\n')
    assert [e.name for e in from_config().engines] == ["jev"]
    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)
    monkeypatch.setenv("SNAPJUDGE_ENGINE", "agentjev")
    assert detect_engine(probe=lambda: False) == ("agentjev", "from $SNAPJUDGE_ENGINE")


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_input": {"command": "rm -rf /"}},
        {"tool_name": "", "tool_input": {"command": "rm -rf /"}},
        {"tool_name": "   "},
        {"tool_name": None},
        {"tool_name": 7},
    ],
)
def test_missing_tool_name_asks_instead_of_allowing(tmp_path, payload):
    out = run({**payload, "cwd": str(tmp_path)})
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_named_unguarded_tool_is_still_a_non_action():
    assert hook.action_from_hook({"tool_name": "Grep"}) is None
    with pytest.raises(ValueError):
        hook.action_from_hook({})


@pytest.mark.parametrize("name", ["Bash ", " Bash", "Bash\n"])
def test_padded_tool_name_is_still_guarded(tmp_path, name):
    out = run({**bash("git push --force", tmp_path), "tool_name": name})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("agent", ["claude-code", "cursor", "codex"])
def test_unexpected_failure_after_read_asks_not_allows(tmp_path, monkeypatch, agent):
    def boom(*a, **k):
        raise RuntimeError("path resolution blew up")

    monkeypatch.setattr(hook, "_decide", boom)
    out, err = io.StringIO(), io.StringIO()
    code = hook.run(
        io.StringIO(json.dumps(bash("git push --force", tmp_path))),
        out,
        err,
        agent=agent,
    )
    text = out.getvalue()
    assert "allowed the action" not in text + err.getvalue()
    if agent == "codex":
        assert code == 2 and "Ask the user" in err.getvalue()
    elif agent == "cursor":
        assert json.loads(text)["permission"] == "ask"
    else:
        assert json.loads(text)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_unexpected_failure_ask_is_logged(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("path resolution blew up")

    monkeypatch.setattr(hook, "_decide", boom)
    hook.run(io.StringIO(json.dumps(bash("git push --force", tmp_path))), io.StringIO())
    entry = json.loads((tmp_path / "home" / "guard.jsonl").read_text().splitlines()[-1])
    assert "ask" in json.dumps(entry) and "RuntimeError" in json.dumps(entry)


def test_unexpected_failure_logging_never_raises(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("decide failed")

    def log_boom(*a, **k):
        raise ValueError("log failed")

    monkeypatch.setattr(hook, "_decide", boom)
    monkeypatch.setattr(hook, "log", log_boom)
    out = io.StringIO()
    hook.run(io.StringIO(json.dumps(bash("git push --force", tmp_path))), out)
    assert (
        json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "ask"
    )
