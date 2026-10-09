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


def test_strict_resume_fails_closed_against_older_daemon():
    """``strict=True`` is the one gated capability that must NOT degrade:
    unlike an additive field a client can safely omit, an older daemon's
    ``/resume`` handler ignores an unrecognized ``strict`` query parameter
    entirely and falls through to its own fresh-session fallback -- silently
    defeating the exact identity-preserving guarantee the caller asked for.
    ``resume_worktree(strict=True)`` must raise rather than silently send a
    non-strict request in that case."""
    from agent_bridge.client import BridgeClientError

    c = BridgeClient("http://127.0.0.1:0", "t")
    c.health = lambda: {"status": "ok", "protocol_version": 24, "min_protocol_version": 1}  # type: ignore[method-assign]
    c._request = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("must not send a request when the daemon can't honor strict")
    )

    try:
        c.resume_worktree("wt-1", strict=True)
    except BridgeClientError as exc:
        assert exc.status == 426
    else:
        raise AssertionError("expected BridgeClientError(426, ...)")


def test_strict_resume_proceeds_against_new_enough_daemon():
    c = BridgeClient("http://127.0.0.1:0", "t")
    c.health = lambda: {"status": "ok", "protocol_version": 27, "min_protocol_version": 1}  # type: ignore[method-assign]
    sent = {}

    def fake_request(method, path, *, params=None, request_timeout=None):
        sent["params"] = params
        return {"session_id": "sid-1"}

    c._request = fake_request  # type: ignore[method-assign]
    result = c.resume_worktree("wt-1", strict=True)
    assert result["session_id"] == "sid-1"
    assert sent["params"] == {"strict": "true"}
