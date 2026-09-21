"""Regression coverage for ``rendezvous.connect_probe``'s wildcard handling."""

from __future__ import annotations

import socket

from agent_index.rendezvous import Endpoint, connect_probe


def test_connect_probe_normalizes_wildcard_bind_to_loopback():
    # A wildcard/unspecified bind (0.0.0.0/::) is not a dialable
    # *destination* -- connecting to it directly fails on most OSes
    # regardless of whether anything is listening, so an endpoint
    # advertised on 0.0.0.0 would otherwise be misclassified as stale
    # (review follow-up on ThomasMichon/copilot-extensions#3066).
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert connect_probe(Endpoint("tcp", f"0.0.0.0:{port}"), timeout=0.5) is True
    finally:
        srv.close()
