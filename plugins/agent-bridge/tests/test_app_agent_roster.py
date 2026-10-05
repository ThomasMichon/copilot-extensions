"""Tests for :mod:`agent_bridge.app_agent_roster` (pivot-streaming-transport
Phase 3b): the daemon startup/readiness wiring around
:class:`agent_bridge.agent_registry_cache.AgentRosterCache`.

These tests isolate ``start_agent_roster_cache``'s own orchestration logic
(stubbing out :class:`AgentRosterCache`'s real timing/background-loop
machinery, which is already exhaustively covered by
``test_agent_roster_cache.py``) rather than re-exercising the full 30s
real-time warm-up wait end-to-end.
"""

from __future__ import annotations

import types

import pytest

from agent_bridge import app_agent_roster
from agent_bridge.agent_registry_cache import AgentRosterCache
from agent_bridge.agent_registry_resolver import AgentResolver


class _StubConfig:
    agent_roster_cache_interval = 60.0


def _make_app() -> types.SimpleNamespace:
    return types.SimpleNamespace(state=types.SimpleNamespace())


@pytest.mark.asyncio
async def test_start_agent_roster_cache_raises_when_discovery_never_completed(monkeypatch):
    """If `wait_until_warm()` times out AND discovery itself never
    completed a single clean pass (not merely some namespace having been
    force-failed -- there may be zero known namespaces at all, since
    discovery is what populates them), readiness must never be published
    for a roster that was never actually scanned. Raising here routes
    through `_initialize_readiness()`'s own existing exception-handling
    retry/backoff loop, the same recovery path any other startup failure
    already takes."""
    monkeypatch.setattr(AgentRosterCache, "start", lambda self: None)

    async def _fake_wait_until_warm(self, *, timeout=None):
        return False

    async def _fake_force_fail(self):
        return None

    monkeypatch.setattr(AgentRosterCache, "wait_until_warm", _fake_wait_until_warm)
    monkeypatch.setattr(
        AgentRosterCache, "force_fail_uninitialized_namespaces", _fake_force_fail,
    )
    # discovery_ever_ok is False by construction here -- start() is stubbed
    # to a no-op, so the cache's real background discovery loop never runs
    # and never has a chance to flip it.

    resolver = AgentResolver({}, {})
    app = _make_app()

    with pytest.raises(RuntimeError, match="discovery never completed"):
        await app_agent_roster.start_agent_roster_cache(app, resolver, _StubConfig())

    assert app.state.agent_roster_cache.discovery_ever_ok is False


@pytest.mark.asyncio
async def test_start_agent_roster_cache_does_not_raise_when_discovery_completed(monkeypatch):
    """Regression guard for the fix above: if discovery DID complete (even
    though some individual namespace still needed force-failing),
    readiness must still be allowed to publish -- the new check is
    specifically about discovery itself never having run, not about every
    namespace having succeeded (that distinction is `wait_until_warm`'s
    own, already-covered FAILED-counts-as-attempted contract)."""
    monkeypatch.setattr(AgentRosterCache, "start", lambda self: None)

    async def _fake_wait_until_warm(self, *, timeout=None):
        return False  # some namespace still needed force-failing

    async def _fake_force_fail(self):
        return None

    monkeypatch.setattr(AgentRosterCache, "wait_until_warm", _fake_wait_until_warm)
    monkeypatch.setattr(
        AgentRosterCache, "force_fail_uninitialized_namespaces", _fake_force_fail,
    )
    monkeypatch.setattr(
        AgentRosterCache, "discovery_ever_ok", property(lambda self: True),
    )

    resolver = AgentResolver({}, {})
    app = _make_app()

    await app_agent_roster.start_agent_roster_cache(app, resolver, _StubConfig())

    assert app.state.agent_roster_cache is not None


@pytest.mark.asyncio
async def test_start_agent_roster_cache_does_not_raise_when_fully_warmed(monkeypatch):
    """The ordinary happy path (no force-fail needed at all) must still
    work unchanged."""
    monkeypatch.setattr(AgentRosterCache, "start", lambda self: None)

    async def _fake_wait_until_warm(self, *, timeout=None):
        return True

    monkeypatch.setattr(AgentRosterCache, "wait_until_warm", _fake_wait_until_warm)

    resolver = AgentResolver({}, {})
    app = _make_app()

    await app_agent_roster.start_agent_roster_cache(app, resolver, _StubConfig())

    assert app.state.agent_roster_cache is not None
