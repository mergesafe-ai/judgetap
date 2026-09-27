import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from snapjudge.dashboard.data import load, record_id
from snapjudge.dashboard.server import make_handler

ROWS = [
    {
        "ts": "2026-09-26T10:00:00.000+00:00",
        "session": "s",
        "tool": "Bash",
        "subject": "git push -f",
        "outcome": "hold",
        "layer": "rules",
        "rule": "force-push",
        "reason": "force",
        "latency_ms": 1,
        "cost_usd": None,
        "error": None,
    },
    {
        "ts": "2026-09-26T10:01:00.000+00:00",
        "session": "s",
        "tool": "Bash",
        "subject": "make test",
        "outcome": "allow",
        "layer": "judge",
        "engine": "jev",
        "latency_ms": 200,
        "cost_usd": 0.0001,
        "error": None,
    },
    {
        "ts": "2026-09-27T09:00:00.000+00:00",
        "session": "s",
        "tool": "Bash",
        "subject": "make deploy",
        "outcome": "ask",
        "layer": "judge",
        "engine": "jev",
        "latency_ms": 400,
        "cost_usd": 0.0001,
        "error": None,
    },
]


@pytest.fixture
def home(tmp_path):
    (tmp_path / "guard.jsonl").write_text(
        "\n".join(json.dumps(r) for r in ROWS) + '\n{"cut'
    )
    return tmp_path


def test_summary(home):
    s = load(home)["summary"]
    assert s["total"] == 3 and s["outcomes"] == {"hold": 1, "allow": 1, "ask": 1}
    assert s["holds_per_1000"] == pytest.approx(333.3)
    assert s["engines"]["jev"] == {"calls": 2, "p50_ms": 200.0, "p95_ms": 400.0}
    assert set(s["per_day"]) == {"2026-09-26", "2026-09-27"}
    assert s["cost_usd"] == pytest.approx(0.0002)


def test_recent_is_newest_first(home):
    assert [r["subject"] for r in load(home)["recent"]] == [
        "make deploy",
        "make test",
        "git push -f",
    ]


@pytest.fixture
def server(home):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(home, "tok", port))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", port
    srv.shutdown()
    srv.server_close()


def call(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def test_page_and_data(server):
    base, _ = server
    status, body = call(base + "/")
    assert status == 200 and b"snapjudge dashboard" in body and b'"tok"' in body
    status, body = call(base + "/api/data")
    assert status == 200 and json.loads(body)["summary"]["total"] == 3


def test_foreign_host_is_refused(server):
    base, _ = server
    assert call(base + "/api/data", headers={"Host": "evil.example:80"})[0] == 403


def test_false_alarm_needs_token_and_a_real_hold(server, home):
    base, _ = server
    hold_id = record_id(ROWS[0])
    body = json.dumps({"id": hold_id}).encode()
    assert call(base + "/api/false-alarm", "POST", body)[0] == 403
    headers = {"X-Snapjudge-Token": "tok", "Content-Type": "application/json"}
    allow_id = json.dumps({"id": record_id(ROWS[1])}).encode()
    assert call(base + "/api/false-alarm", "POST", allow_id, headers)[0] == 404
    assert call(base + "/api/false-alarm", "POST", body, headers)[0] == 200
    s = load(home)
    assert s["summary"]["false_alarms"] == 1
    assert (home / "feedback.jsonl").stat().st_mode & 0o777 == 0o600


def test_oversized_body_is_rejected(server):
    base, _ = server
    headers = {"X-Snapjudge-Token": "tok"}
    assert call(base + "/api/false-alarm", "POST", b"x" * 5000, headers)[0] == 413


def test_empty_home(tmp_path):
    data = load(tmp_path)
    assert data["summary"]["total"] == 0 and data["recent"] == []
