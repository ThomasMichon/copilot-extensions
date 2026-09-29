"""Phase 4 (agent-bridge-unified-zdd-cutover): ``send`` survives a mid-command
graceful cutover transparently, exercised at the real CLI call site.

Correction (post-review): an earlier version of this test simulated the
retiring generation refusing ``send``'s delivery with a 503 "draining"
response. That response is real (#3179) but is only ever emitted by the
*session-creation* route (``POST /api/v1/sessions``, when the daemon is
mid-drain and refuses brand-new work) -- ``post_live_message`` (the route
``send`` actually hits when the target already has a live session, the
common case this test exercises) has no draining gate at all, since
delivering into an *already-registered* live session is cheap local-DB work,
not new agent work. That prior test therefore validated a scenario the real
endpoint can never produce.

The real risk window for ``send`` mid-cutover is different: once the
retiring generation's HTTP listener actually closes (post-shutdown, after
the drain grace has elapsed), the *next* delivery attempt against the
remembered port sees a plain connection refusal (a clean ``ECONNREFUSED``,
never a "connection reset" -- nothing was ever sent to the dead process, so
retrying is unambiguously safe even for this non-idempotent POST).
``BridgeClient._request()`` already follows exactly this case to the
routing table's successor and retries (proven generically at the
``BridgeClient`` unit level by ``TestReresolveOnRejection`` in
``test_client_connect.py``); this test closes the same gap as before, this
time against the real endpoint and the real CLI ``send`` code path.
"""

from __future__ import annotations

import argparse
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


def test_send_cli_survives_connection_refused_mid_delivery(monkeypatch, capsys):
    """A clean ECONNREFUSED against the retired generation's port (post-#3179
    shutdown, not mid-drain) is followed to the successor and retried, even
    for `send`'s non-idempotent delivery POST -- and the CLI prints a normal
    delivery confirmation, never a traceback or hard failure."""
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
            # The old generation has fully shut down -- its port is closed,
            # not merely refusing new work while alive.
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
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
    # The retired generation's dead port was tried first, then the routing
    # table's successor -- exactly the sequence `send` must follow to look
    # like a brief buffered pause, never a hard error, across a cutover.
    assert seen == [
        f"{old_base}/api/v1/live-sessions/resolve?handle=agent-x",
        f"{old_base}/api/v1/live-sessions/sess1/messages",
        f"{new_base}/api/v1/live-sessions/sess1/messages",
    ]
    assert client._base == new_base
