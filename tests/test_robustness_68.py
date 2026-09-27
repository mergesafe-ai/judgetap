import json
import math
import threading
import urllib.request

import pytest

from judgetap.dashboard.data import load


def write(tmp_path, rows):
    (tmp_path / "guard.jsonl").write_text("\n".join(rows) + "\n")


@pytest.mark.parametrize(
    "line",
    [
        '{"outcome":"allow","cost_usd":"0.1"}',
        '{"outcome":["hold"]}',
        '{"outcome":"allow","cost_usd":NaN}',
        '{"outcome":"allow","layer":"judge","calls":[{"engine":"e","latency_ms":NaN}]}',
        '{"outcome":"allow","layer":{"x":1},"engine":7,"source":[1],"p":"nope","calls":"x"}',
        '{"outcome":"allow","latency_ms":Infinity,"layer":"judge","engine":"e"}',
    ],
)
def test_odd_log_values_never_break_load(tmp_path, line):
    write(tmp_path, [json.dumps({"outcome": "hold", "layer": "rules"}), line])
    data = load(tmp_path)
    json.dumps(data, allow_nan=False)  # strict JSON: no NaN/Infinity left
    assert data["summary"]["outcomes"].get("hold") == 1
    assert all(isinstance(k, str) or k is None for k in data["summary"]["outcomes"])


def test_nan_latency_is_not_sampled(tmp_path):
    write(
        tmp_path,
        [
            '{"outcome":"allow","layer":"judge","calls":[{"engine":"e","latency_ms":NaN},{"engine":"e","latency_ms":5}]}'
        ],
    )
    e = load(tmp_path)["summary"]["engines"]["e"]
    assert e["calls"] == 2 and e["p50_ms"] == 5 and not math.isnan(e["p50_ms"])


def test_api_data_answers_with_a_bad_line(tmp_path):
    from judgetap.dashboard.server import serve

    write(tmp_path, ['{"outcome":"allow","cost_usd":"0.1"}', '{"outcome":["hold"]}'])
    srv = serve(tmp_path, 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/data") as res:
            assert res.status == 200 and json.loads(res.read())["summary"]["total"] == 2
    finally:
        srv.shutdown()
        srv.server_close()


def test_port_zero_uses_the_bound_port(tmp_path):
    from judgetap.dashboard.server import serve

    srv = serve(tmp_path, 0)
    port = srv.server_address[1]
    assert port != 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as res:
            assert res.status == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_keys_set_rejects_names_no_engine_reads(monkeypatch, capsys):
    from judgetap.cli import main

    asked = []
    monkeypatch.setattr("getpass.getpass", lambda prompt: asked.append(prompt) or "v")
    assert main(["keys", "set", "OPENAI_API_KEY"]) == 2
    assert not asked and "isn't read by any engine" in capsys.readouterr().out
    assert main(["keys", "status", "OPENAI_API_KEY"]) == 2


def test_empty_new_env_wins_over_old(monkeypatch):
    from judgetap._compat import env, env_source

    monkeypatch.setenv("JUDGETAP_ENGINE", "")
    monkeypatch.setenv("SNAPJUDGE_ENGINE", "llm")
    assert env("ENGINE") is None and env_source("ENGINE") is None
    monkeypatch.delenv("JUDGETAP_ENGINE")
    assert env("ENGINE") == "llm" and env_source("ENGINE") == "$SNAPJUDGE_ENGINE"


def test_nan_in_unknown_fields_is_scrubbed(tmp_path):
    write(tmp_path, ['{"outcome":"allow","extra":{"x":[NaN, -Infinity]}}'])
    json.dumps(load(tmp_path), allow_nan=False)
