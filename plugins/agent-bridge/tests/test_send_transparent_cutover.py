"""Phase 4 (agent-bridge-unified-zdd-cutover): ``send`` survives a mid-command
graceful cutover transparently, exercised at the real CLI call site.

``BridgeClient._request()`` already follows a retiring daemon's "draining" 503
(#3179) to the successor named by the routing table and retries within the
connect grace -- proven at the client-request level by
``TestDrainGrace`` in ``test_client_connect.py``. This test closes the one gap
Phase 4's own checklist calls out explicitly: validating that the *same*
transparency reaches all the way through ``agent-bridge send``'s real code
path (``_cmd_send`` -> ``resolve_live_session`` -> ``send_live_message``),
not just the underlying ``BridgeClient`` unit in isolation -- a live cutover
mid-``send`` must surface as a delivered message, never a traceback or a
hard CLI failure.
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.error

from agent_bridge import __main__ as m
from agent_bridge.client import BridgeClient


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _draining() -> urllib.error.HTTPError:
    detail = "agent-bridge is draining for a redeploy; retry shortly."
    return urllib.error.HTTPError(
        "http://127.0.0.1/api/v1/live-sessions/sess1/messages",
        503,
        "Service Unavailable",
        {},
        io.BytesIO(json.dumps({"detail": detail}).encode()),
    )


def test_send_cli_survives_drain_503_mid_delivery(monkeypatch, capsys):
    old_base = "http://127.0.0.1:57585"
    new_base = "http://127.0.0.1:47000"
    client = BridgeClient(
        old_base,
        "tok",
        connect_grace=2.0,
        reresolve=lambda: new_base,
    )
    seen: list[str] = []

    def by_port(req, timeout=None):
        seen.append(req.full_url)
        if req.full_url.endswith("/api/v1/live-sessions/resolve?handle=agent-x"):
            return _FakeResp({"session_id": "sess1", "status": "idle"})
        if req.full_url.startswith(old_base):
            # The retiring generation still answers -- it just refuses new
            # delivery while it waits for its successor to take over.
            raise _draining()
        return _FakeResp({"message_id": "m1", "replied": False})

    monkeypatch.setattr(
        "agent_bridge.client.urllib.request.urlopen", by_port
    )
    monkeypatch.setattr(m, "_get_client", lambda: client)
    monkeypatch.setattr(m, "_live_sender_label", lambda _args: "caller-A")
    monkeypatch.setattr(m, "_live_reply_to", lambda _args: None)
    monkeypatch.setattr(m, "_live_message_kind", lambda _args: "prompt")
    monkeypatch.setattr(m, "_live_message_delivery", lambda _args: "queue")

    args = argparse.Namespace(
        target="agent-x",
        prompt="hello",
        prompt_file=None,
        new=False,
        json=False,
        no_wait=True,
        reply_timeout=120.0,
        idempotency_key=None,
        expected_session_id=None,
    )

    m._cmd_send(args)

    out = capsys.readouterr().out
    assert "Delivered to live session sess1" in out
    # The retiring generation was tried first, then the routing table's
    # successor -- exactly the sequence `send` must follow to look like a
    # brief buffered pause, never a hard error, across a graceful cutover.
    assert seen == [
        f"{old_base}/api/v1/live-sessions/resolve?handle=agent-x",
        f"{old_base}/api/v1/live-sessions/sess1/messages",
        f"{new_base}/api/v1/live-sessions/sess1/messages",
    ]
    assert client._base == new_base
