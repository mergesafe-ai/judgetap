"""The local HTTP server: one page, GET /api/data, POST /api/false-alarm."""

from __future__ import annotations

import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

from judgetap.dashboard.data import load, mark_false_alarm

MAX_BODY = 4096


def make_handler(home: Path, token: str, port: int) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    page = files("judgetap.dashboard").joinpath("page.html").read_text()
    page = page.replace("__TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        server_version = "judgetap"

        def log_message(self, *args) -> None:  # keep the terminal quiet
            pass

        def _host_ok(self) -> bool:
            # Rejects DNS rebinding: another site's name pointed at 127.0.0.1.
            return self.headers.get("Host") in allowed_hosts

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: object) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json")

        def do_GET(self) -> None:
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            if self.path == "/":
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if self.path == "/api/data":
                return self._json(200, load(home))
            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            # The token is only in the page this server rendered, so another
            # site can't forge the request.
            if not secrets.compare_digest(
                self.headers.get("X-Judgetap-Token", ""), token
            ):
                return self._json(403, {"error": "bad token"})
            if self.path != "/api/false-alarm":
                return self._json(404, {"error": "not found"})
            raw_length = self.headers.get("Content-Length")
            # ASCII digits only: str.isdigit() also accepts "²", which int() rejects.
            if raw_length is None or not re.fullmatch(r"[0-9]+", raw_length):
                return self._json(400, {"error": "missing or bad content-length"})
            length = int(raw_length)
            if length > MAX_BODY:
                return self._json(413, {"error": "too large"})
            try:
                rid = json.loads(self.rfile.read(length) or b"{}").get("id", "")
            except (ValueError, AttributeError):
                return self._json(400, {"error": "bad json"})
            if not isinstance(rid, str):
                return self._json(400, {"error": "id must be a string"})
            known = {
                r["id"]
                for r in load(home)["recent"]
                if r.get("source") == "guard" and r.get("outcome") == "hold"
            }
            if not mark_false_alarm(home, rid, known):
                return self._json(404, {"error": "no such hold"})
            return self._json(200, {"ok": True})

    return Handler


def serve(home: Path, port: int = 8765) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(home, token, port))
    return server
