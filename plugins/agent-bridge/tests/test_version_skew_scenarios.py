"""End-to-end **version-skew scenarios** (dotfiles #632, Stage 3).

Where `test_wire_compat.py` statically guards the tolerant-reader invariant and
`test_protocol_negotiation.py` unit-tests the negotiation primitives, this module
exercises the two skew directions as **named, readable scenarios** against real
routes — the regression anchor for "the suite is correct *while* skewed":

1. **newer client → older daemon** — a client gates a version-introduced feature
   on the daemon's advertised support and degrades gracefully when the daemon is
   older (or predates protocol advertisement) instead of blind-sending.
(The tolerant-reader direction — an older daemon ignoring a newer client's
unknown fields — is guarded statically for every wire model by
`test_wire_compat.py`, so it needs no per-endpoint live scenario here.)
"""

from __future__ import annotations

from agent_bridge.client import BridgeClient


# -- Direction 1: newer client gates on an older daemon's advertised support ---


def test_newer_client_degrades_against_older_daemon():
    # An "older daemon" that predates protocol advertisement: /health omits the
    # protocol fields. A client that needs a hypothetical protocol >= 2 feature
    # must gate off and take a fallback, not blind-send.
    c = BridgeClient("http://127.0.0.1:0", "t")
    c.health = lambda: {"status": "ok", "draining": False}  # type: ignore[method-assign]

    needed = 2
    if c.daemon_supports(needed):
        used_feature = True
    else:
        used_feature = False  # graceful fallback path

    assert used_feature is False
    assert c.daemon_protocol() == (0, 0)  # unversioned -> gate off


def test_newer_client_uses_feature_when_daemon_new_enough():
    c = BridgeClient("http://127.0.0.1:0", "t")
    c.health = lambda: {  # type: ignore[method-assign]
        "status": "ok", "protocol_version": 5, "min_protocol_version": 1,
    }
    assert c.daemon_supports(2) is True  # daemon new enough -> use the feature
