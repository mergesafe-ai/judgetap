import io
import json

import pytest

from snapjudge.guard import hook
from snapjudge.guard.install import HOOK_COMMAND, install, uninstall


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPJUDGE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SNAPJUDGE_ENGINE", raising=False)


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


def test_garbage_input_never_blocks():
    out = io.StringIO()
    assert hook.run(io.StringIO("not json"), out) == 0
    assert "failed and allowed" in json.loads(out.getvalue())["systemMessage"]


def test_bad_engine_spec_degrades_to_rules_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPJUDGE_ENGINE", "nonsense")
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


def test_transcript_read_from_the_end(tmp_path, monkeypatch):
    t = tmp_path / "t.jsonl"
    old = json.dumps({"type": "user", "message": {"content": "old"}})
    new = json.dumps({"type": "user", "message": {"content": "newest ask"}})
    t.write_text("\n".join([old] * 5000 + [new, json.dumps({"type": "assistant"})]))
    seen = []
    real = json.loads
    monkeypatch.setattr(hook.json, "loads", lambda s: seen.append(1) or real(s))
    assert hook.last_user_message(str(t)) == "newest ask"
    assert len(seen) <= 3


def test_malformed_user_config_still_runs_builtins(tmp_path):
    (tmp_path / "guard.toml").write_text("[[rule]\nbroken")
    out = run(bash("git push --force", tmp_path))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "ignored" in out["systemMessage"]


def test_cli_test_does_not_write_the_log(tmp_path, monkeypatch, capsys):
    from snapjudge.cli import main

    monkeypatch.chdir(tmp_path)
    assert main(["guard", "test"]) == 0
    assert not (tmp_path / "home" / "guard.jsonl").exists()
    assert "deny" in capsys.readouterr().out


def test_cli_stats_summarises_log(tmp_path, capsys):
    from snapjudge.cli import main

    run(bash("git push -f", tmp_path))
    run(bash("make test", tmp_path))
    assert main(["guard", "stats"]) == 0
    out = capsys.readouterr().out
    assert "2 guarded calls" in out and "holds per 1,000 calls: 500.0" in out


def test_cli_install_and_uninstall(tmp_path, monkeypatch, capsys):
    from snapjudge.cli import main

    monkeypatch.chdir(tmp_path)
    main(["guard", "install", "--scope", "project"])
    assert HOOK_COMMAND in (tmp_path / ".claude" / "settings.json").read_text()
    main(["guard", "uninstall", "--scope", "project"])
    assert HOOK_COMMAND not in (tmp_path / ".claude" / "settings.json").read_text()


def test_transcript_cache_reads_only_new_bytes(tmp_path, monkeypatch):
    t = tmp_path / "t.jsonl"
    user = lambda text: json.dumps({"type": "user", "message": {"content": text}})
    tool = json.dumps({"type": "assistant", "message": {"content": "x" * 50}})
    t.write_text(user("first task") + "\n" + tool + "\n")
    assert hook.last_user_message(str(t), "s1") == "first task"

    parsed = []
    real = hook._user_text
    monkeypatch.setattr(hook, "_user_text", lambda raw: parsed.append(raw) or real(raw))
    with t.open("a") as fh:
        fh.write(tool + "\n" + tool + "\n")
    assert hook.last_user_message(str(t), "s1") == "first task"
    assert len(parsed) == 2  # only the two new lines
    with t.open("a") as fh:
        fh.write(
            user("second task") + "\n" + '{"type": "user", "mess'
        )  # half-written line
    assert hook.last_user_message(str(t), "s1") == "second task"


def test_transcript_cache_invalidated_by_truncation(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        json.dumps({"type": "user", "message": {"content": "long " * 100}}) + "\n"
    )
    hook.last_user_message(str(t), "s2")
    t.write_text(json.dumps({"type": "user", "message": {"content": "new"}}) + "\n")
    assert hook.last_user_message(str(t), "s2") == "new"


def test_unsafe_session_ids_are_not_cached(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n")
    assert hook.last_user_message(str(t), "../../etc/evil") == "x"
    assert not (tmp_path / "etc").exists()


def test_partial_user_record_on_first_read_is_picked_up_later(tmp_path):
    t = tmp_path / "t.jsonl"
    done = json.dumps({"type": "user", "message": {"content": "old task"}})
    partial = json.dumps({"type": "user", "message": {"content": "new task"}})
    t.write_text(done + "\n" + partial[:20])
    assert hook.last_user_message(str(t), "s3") == "old task"
    with t.open("a") as fh:
        fh.write(partial[20:] + "\n")
    assert hook.last_user_message(str(t), "s3") == "new task"


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
    from snapjudge.cli import main

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


def test_replaced_transcript_invalidates_cache(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"content": "a"}}) + "\n")
    hook.last_user_message(str(t), "s4")
    replacement = tmp_path / "new.jsonl"
    replacement.write_text(
        json.dumps({"type": "user", "message": {"content": "b" * 200}}) + "\n"
    )
    replacement.replace(t)  # same path, larger, new inode
    assert hook.last_user_message(str(t), "s4") == "b" * 200


def test_in_place_rewrite_invalidates_cache(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        json.dumps({"type": "user", "message": {"content": "old " * 50}}) + "\n"
    )
    hook.last_user_message(str(t), "s5")
    with t.open("r+") as fh:  # same inode, same or larger size, different bytes
        fh.seek(0)
        fh.write(
            json.dumps({"type": "user", "message": {"content": "new " * 60}}) + "\n"
        )
    assert hook.last_user_message(str(t), "s5") == ("new " * 60)


def test_stats_skips_a_cut_off_line(tmp_path, capsys):
    from snapjudge.cli import main

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
