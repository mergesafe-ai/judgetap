import io
import json

import pytest

from judgetap.guard import loop, prune


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    return tmp_path


def _records(tmp_path):
    path = tmp_path / "guard.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines()]


PROGRESS = "\n".join(f"Downloading {i}% [{'#' * 20}]" for i in range(100))
REPEAT = "\n".join("WARN: retrying connection" for _ in range(200))
LISTING = "\n".join(f"src/pkg/mod{i}.py:{i}: import os" for i in range(150))
VARIED = "\n".join(f"line {i} says something different {i * 7}" for i in range(200))
TRACE = "Traceback (most recent call last):\n" + REPEAT


@pytest.mark.parametrize(
    ("text", "tool", "expected"),
    [
        (PROGRESS, "Bash", "drop"),
        (REPEAT, "Bash", "summarize"),
        (LISTING, "Grep", "summarize"),
        (LISTING, "Bash", "summarize"),
        (VARIED, "Bash", "keep"),
        (TRACE, "Bash", "keep"),
        ("short\n" * 5, "Bash", "keep"),
    ],
)
def test_label(text, tool, expected):
    assert prune.label(text, tool) == expected


def test_failed_output_is_keep():
    assert prune.label(PROGRESS, "Bash", failed=True) == "keep"


def test_tokens_saved_is_chars_over_four():
    assert prune.tokens_saved(4000, "drop") == 1000
    assert prune.tokens_saved(4500, "summarize") == 1000
    assert prune.tokens_saved(4000, "keep") == 0


def _payload(text, tool="Bash"):
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "s1",
        "tool_name": tool,
        "tool_input": {"command": "pip install x"},
        "tool_response": {"stdout": text, "stderr": ""},
    }


def test_run_logs_a_shadow_note_and_leaves_output_alone(tmp_path, capsys):
    secret = "AKIAABCDEFGHIJKLMNOP "
    text = secret + PROGRESS
    out = io.StringIO()
    assert loop.run(io.StringIO(json.dumps(_payload(text))), out) == 0
    assert out.getvalue() == ""  # nothing sent back to the agent
    (r,) = _records(tmp_path)
    assert r["layer"] == "prune" and r["outcome"] == "note"
    assert r["label"] == "drop"
    assert r["output_chars"] == len(text) + 1  # stdout + "\n" + empty stderr
    assert r["est_tokens_saved"] == r["output_chars"] // 4
    assert len(r["subject"]) <= 200 and "AKIAABCDEFGHIJKLMNOP" not in r["subject"]
    assert PROGRESS[300:400] not in json.dumps(r)


def test_small_outputs_are_not_logged(tmp_path):
    prune.observe(_payload("ok\n" * 10))
    assert _records(tmp_path) == []


def test_threshold_is_configurable(tmp_path):
    (tmp_path / "guard.toml").write_text("prune_threshold = 10\n")
    assert prune.threshold() == 10
    prune.observe(_payload("x" * 50))
    assert _records(tmp_path)[0]["label"] == "keep"
    (tmp_path / "guard.toml").write_text("prune_threshold = 'big'\n")
    assert prune.threshold() == prune.DEFAULT_THRESHOLD


def test_errors_are_swallowed_and_noted(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(prune, "label", boom)
    prune.observe(_payload(PROGRESS))
    (r,) = _records(tmp_path)
    assert r["error"] == "RuntimeError" and r["est_tokens_saved"] == 0


def test_failure_events_are_not_pruned(tmp_path):
    p = _payload(PROGRESS)
    p["hook_event_name"] = "PostToolUseFailure"
    loop.run(io.StringIO(json.dumps(p)), io.StringIO())
    assert _records(tmp_path) == []


def test_stats_and_dashboard_show_shadow_total(tmp_path, capsys):
    from judgetap.cli import main
    from judgetap.dashboard.data import load

    rows = [
        {"outcome": "hold", "layer": "rules", "latency_ms": 1, "error": None},
        {
            "outcome": "note",
            "layer": "prune",
            "label": "drop",
            "est_tokens_saved": 1500,
        },
        {
            "outcome": "note",
            "layer": "prune",
            "label": "summarize",
            "est_tokens_saved": 500,
        },
        {"outcome": "note", "layer": "prune", "label": "keep", "est_tokens_saved": 0},
        {"outcome": "note", "layer": "prune", "est_tokens_saved": "bad"},
    ]
    (tmp_path / "guard.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    main(["guard", "stats"])
    out = capsys.readouterr().out
    assert "1 guarded calls" in out
    assert "est. tokens pruneable (shadow): 2,000 across 2 outputs" in out
    assert "estimated, if pruning were on" in out
    s = load(tmp_path)["summary"]
    assert s["total"] == 1 and s["est_tokens_pruneable"] == 2000
