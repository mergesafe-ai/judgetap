import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from judgetap.dashboard.data import load
from judgetap.dashboard.server import make_handler

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
    from datetime import date

    s = load(home, today=date(2026, 9, 27))["summary"]
    assert s["total"] == 3 and s["outcomes"] == {"hold": 1, "allow": 1, "ask": 1}
    assert s["holds_per_1000"] == pytest.approx(333.3)
    assert s["engines"]["jev"] == {"calls": 2, "p50_ms": 200.0, "p95_ms": 400.0}
    assert s["per_day"]["2026-09-26"] == {"hold": 1, "allow": 1}
    assert s["per_day"]["2026-09-27"] == {"ask": 1}
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
    assert status == 200 and b"judgetap dashboard" in body and b'"tok"' in body
    status, body = call(base + "/api/data")
    assert status == 200 and json.loads(body)["summary"]["total"] == 3


def test_foreign_host_is_refused(server):
    base, _ = server
    assert call(base + "/api/data", headers={"Host": "evil.example:80"})[0] == 403


def test_false_alarm_needs_token_and_a_real_hold(server, home):
    base, _ = server
    by_subject = {r["subject"]: r["id"] for r in load(home)["recent"]}
    hold_id = by_subject["git push -f"]
    body = json.dumps({"id": hold_id}).encode()
    assert call(base + "/api/false-alarm", "POST", body)[0] == 403
    headers = {"X-Judgetap-Token": "tok", "Content-Type": "application/json"}
    allow_id = json.dumps({"id": by_subject["make test"]}).encode()
    assert call(base + "/api/false-alarm", "POST", allow_id, headers)[0] == 404
    assert call(base + "/api/false-alarm", "POST", body, headers)[0] == 200
    s = load(home)
    assert s["summary"]["false_alarms"] == 1
    assert (home / "feedback.jsonl").stat().st_mode & 0o777 == 0o600


def test_oversized_body_is_rejected(server):
    base, _ = server
    headers = {"X-Judgetap-Token": "tok"}
    assert call(base + "/api/false-alarm", "POST", b"x" * 5000, headers)[0] == 413


def test_empty_home(tmp_path):
    data = load(tmp_path)
    assert data["summary"]["total"] == 0 and data["recent"] == []


def test_per_day_is_a_30_day_calendar_window(tmp_path):
    from datetime import date

    rows = [
        {"ts": "2026-06-01T10:00:00+00:00", "outcome": "hold"},
        {"ts": "2026-09-20T10:00:00+00:00", "outcome": "allow"},
    ]
    (tmp_path / "guard.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    per_day = load(tmp_path, today=date(2026, 9, 27))["summary"]["per_day"]
    assert len(per_day) == 30
    days = list(per_day)
    assert (days[0], days[-1]) == ("2026-08-29", "2026-09-27")
    assert "2026-06-01" not in per_day
    assert per_day["2026-09-20"] == {"allow": 1} and per_day["2026-09-21"] == {}


def test_identical_ts_and_session_get_distinct_ids(tmp_path):
    from judgetap.dashboard.data import mark_false_alarm

    row = {"ts": "2026-09-26T10:00:00+00:00", "session": "s", "outcome": "hold"}
    (tmp_path / "guard.jsonl").write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n"
    )
    recent = load(tmp_path)["recent"]
    ids = [r["id"] for r in recent]
    assert len(set(ids)) == 2
    assert mark_false_alarm(tmp_path, ids[0], set(ids))
    marked = [r["false_alarm"] for r in load(tmp_path)["recent"]]
    assert sorted(marked) == [False, True]


def test_logged_records_carry_a_unique_id(tmp_path, monkeypatch):
    import io

    from judgetap.guard import hook

    monkeypatch.setenv("JUDGETAP_HOME", str(tmp_path))
    monkeypatch.delenv("JUDGETAP_ENGINE", raising=False)
    for _ in range(2):
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push -f"},
            "cwd": str(tmp_path),
            "session_id": "s",
        }
        hook.run(io.StringIO(json.dumps(payload)), io.StringIO())
    ids = [
        json.loads(line)["id"]
        for line in (tmp_path / "guard.jsonl").read_text().splitlines()
    ]
    assert len(ids) == 2 and ids[0] != ids[1]


def test_non_string_id_is_400(server):
    base, _ = server
    headers = {"X-Judgetap-Token": "tok", "Content-Type": "application/json"}
    assert call(base + "/api/false-alarm", "POST", b'{"id": []}', headers)[0] == 400


def test_page_has_filter_labels_and_status(server):
    base, _ = server
    body = call(base + "/")[1].decode()
    assert body.count("<label>") == 4 and 'role="status"' in body


def test_engine_calls_are_counted_past_the_latency_cap(tmp_path, monkeypatch):
    from judgetap.dashboard import data

    monkeypatch.setattr(data, "MAX_LATENCIES", 3)
    rows = [
        {
            "ts": "2026-09-26T10:00:00+00:00",
            "outcome": "allow",
            "layer": "judge",
            "engine": "jev",
            "latency_ms": i,
        }
        for i in range(7)
    ]
    (tmp_path / "guard.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert data.load(tmp_path)["summary"]["engines"]["jev"]["calls"] == 7


def test_load_is_cached_until_the_log_changes(tmp_path, monkeypatch):
    from judgetap.dashboard import data

    log = tmp_path / "guard.jsonl"
    log.write_text(json.dumps(ROWS[0]) + "\n")
    calls = []
    real = data._load
    monkeypatch.setattr(data, "_load", lambda h, t: calls.append(1) or real(h, t))
    data.load(tmp_path)
    data.load(tmp_path)
    assert len(calls) == 1
    with log.open("a") as fh:
        fh.write(json.dumps(ROWS[1]) + "\n")
    assert data.load(tmp_path)["summary"]["total"] == 2 and len(calls) == 2


def test_bad_content_length_is_400(server):
    base, _ = server
    headers = {"X-Judgetap-Token": "tok", "Content-Length": "abc"}
    req = urllib.request.Request(
        base + "/api/false-alarm", data=b"", method="POST", headers=headers
    )
    req.remove_header("Content-length")
    req.add_unredirected_header("Content-Length", "abc")
    try:
        urllib.request.urlopen(req)
        status = 200
    except urllib.error.HTTPError as err:
        status = err.code
    assert status == 400


def test_page_has_empty_chart_message(server):
    base, _ = server
    assert b"No guarded calls in the last 30 days" in call(base + "/")[1]


def test_library_records_are_counted_apart(tmp_path):
    from judgetap.dashboard.data import load

    rows = [
        {
            "id": "g1",
            "ts": "2026-09-26T10:00:00+00:00",
            "outcome": "hold",
            "layer": "rules",
        },
        {
            "id": "l1",
            "ts": "2026-09-26T10:00:01+00:00",
            "source": "library",
            "outcome": "hold",
            "layer": "library",
            "engine": "jev",
            "latency_ms": 100,
        },
    ]
    (tmp_path / "guard.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    s = load(tmp_path, today=__import__("datetime").date(2026, 9, 26))
    assert s["summary"]["total"] == 1 and s["summary"]["library"] == 1
    assert s["summary"]["outcomes"] == {"hold": 1}
    assert s["summary"]["per_day"]["2026-09-26"] == {"hold": 1}
    assert (
        "jev" not in s["summary"]["engines"]
    )  # library calls stay out of the guard engine table
    assert {r["source"] for r in s["recent"]} == {"guard", "library"}


def test_library_hold_cannot_be_marked_false_alarm(tmp_path):
    rows = [
        {
            "id": "l1",
            "ts": "2026-09-26T10:00:01+00:00",
            "source": "library",
            "outcome": "hold",
            "layer": "library",
        }
    ]
    (tmp_path / "guard.jsonl").write_text(json.dumps(rows[0]) + "\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(tmp_path, "tok", port))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, _ = call(
            f"http://127.0.0.1:{port}/api/false-alarm",
            "POST",
            b'{"id": "l1"}',
            {"X-Judgetap-Token": "tok", "Content-Type": "application/json"},
        )
        assert status == 404
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("length", ["²", None])
def test_content_length_must_be_ascii_digits_and_present(server, length):
    import http.client

    _, port = server
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.putrequest("POST", "/api/false-alarm", skip_accept_encoding=True)
    conn.putheader("X-Judgetap-Token", "tok")
    if length is not None:
        conn.putheader("Content-Length", length.encode("utf-8").decode("latin-1"))
    conn.endheaders()
    assert conn.getresponse().status == 400
    conn.close()


def test_cache_is_safe_under_concurrent_loads(tmp_path):
    from judgetap.dashboard import data

    log = tmp_path / "guard.jsonl"
    log.write_text(json.dumps(ROWS[0]) + "\n")
    errors = []

    def reader():
        try:
            for _ in range(200):
                data.load(tmp_path)
        except Exception as err:  # noqa: BLE001
            errors.append(err)

    def writer():
        for i in range(50):
            with log.open("a") as fh:
                fh.write(json.dumps({**ROWS[1], "id": f"w{i}"}) + "\n")

    threads = [threading.Thread(target=reader) for _ in range(4)] + [
        threading.Thread(target=writer)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_outcome_filter_is_built_from_records(server):
    base, _ = server
    page = call(base + "/")[1]
    assert (
        b"function outcomeOptions" in page
        and b'<select id="f-outcome"><option value="">All</option></select>' in page
    )


def test_filters_offer_library_layer_and_source_aware_outcomes(server):
    base, _ = server
    page = call(base + "/")[1]
    assert b"<option>library</option></select>" in page
    assert b'src === "library" ? [] : ["hold", "ask", "allow"]' in page
