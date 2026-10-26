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
