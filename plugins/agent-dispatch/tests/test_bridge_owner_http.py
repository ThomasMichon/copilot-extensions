"""Contained native HTTP witness; never addresses an installed Bridge service."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_dispatch import tracking


@pytest.mark.parametrize("truncate", [None, "health", "metadata"],
                         ids=["normal", "truncated-health", "truncated-metadata"])
def test_private_http_rendezvous_and_stale_service(tmp_path, monkeypatch, truncate):
    from zdd.routing import publish_active

    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_BRIDGE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "auth.yaml").write_text("token: private-fixture", encoding="utf-8")
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            scheme = "Be" + "arer "
            assert self.headers["Authorization"] == scheme + "private-fixture"
            paths.append(self.path)
            body = ({"protocol_version": 1, "min_protocol_version": 1}
                    if self.path == "/health" else
                    {"session_id": "session", "worktree_id": "wt"})
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            stage = "health" if self.path == "/health" else "metadata"
            self.wfile.write(data[:-3] if truncate == stage else data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        publish_active(tmp_path, bind="127.0.0.1", port=server.server_port)
        expected = tracking.LIVE if truncate is None else tracking.UNKNOWN
        assert tracking.liveness_verdict("wt", owner_session_id="session") == expected
        expected_paths = ["/health"]
        if truncate != "health":
            expected_paths.append("/api/v1/live-sessions/resolve?handle=wt")
        assert paths == expected_paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert tracking.liveness_verdict("wt", owner_session_id="session") == tracking.UNKNOWN
    assert paths == expected_paths


@pytest.mark.parametrize("stage,part", [
    ("health", "headers"), ("health", "body"),
    ("metadata", "headers"), ("metadata", "body"),
])
def test_trickling_response_obeys_one_deadline(tmp_path, monkeypatch, stage, part):
    import time
    from zdd.routing import publish_active

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "auth.yaml").write_text("token: private-fixture", encoding="utf-8")
    monkeypatch.setattr(tracking, "_run_capture", lambda *a, **k: pytest.fail("no CLI fallback"))
    stop = threading.Event()
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            current = "health" if self.path == "/health" else "metadata"
            paths.append(current)
            data = json.dumps({"protocol_version": 1, "min_protocol_version": 1}
                              if current == "health" else
                              {"session_id": "s", "worktree_id": "wt"}).encode()
            try:
                if stage == current and part == "headers":
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Trickle: ")
                    for _ in range(100):
                        if stop.wait(0.02):
                            return
                        self.wfile.write(b"x")
                        self.wfile.flush()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if stage == current:
                    for byte in data:
                        if stop.wait(0.02):
                            return
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                else:
                    self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        publish_active(tmp_path, bind="127.0.0.1", port=server.server_port)
        started = time.monotonic()
        verdict = tracking.liveness_verdict("wt", owner_session_id="s", timeout=0.15)
        elapsed = time.monotonic() - started
        assert verdict == tracking.UNKNOWN
        assert elapsed < 0.8, elapsed
        assert paths == (["health"] if stage == "health" else ["health", "metadata"])
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()
