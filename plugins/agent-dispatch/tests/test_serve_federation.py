"""Tests for wiring the federation runner into the coordinator ``serve`` lifecycle
(:func:`agent_dispatch.server._maybe_start_federation`).

The helper is unit-tested in isolation (no real uvicorn / no real threads): a fake
runner records ``start``/``stop`` so we assert the wiring without a live server.
"""

from __future__ import annotations

import agent_dispatch.federation_runner as fr
from agent_dispatch.server import _maybe_start_federation


class FakeRunner:
    def __init__(self) -> None:
        self.started_with: float | None = None
        self.stopped = False

    def start(self, *, interval: float) -> None:
        self.started_with = interval

    def stop(self, **_kwargs) -> None:
        self.stopped = True


def test_none_when_federation_disabled(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_FEDERATION_ROLE", raising=False)
    assert _maybe_start_federation() is None


def test_soft_fails_when_enabled_but_no_gateway(monkeypatch):
    # role set, but no shared/hosted URL -> runner_from_config raises -> swallowed.
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", "coordinator")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INSTANCE", "host-a")
    monkeypatch.delenv("AGENT_DISPATCH_SHARED_URL", raising=False)
    assert _maybe_start_federation() is None  # no exception escapes


def test_starts_runner_when_configured(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", "coordinator")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INSTANCE", "host-a")
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_INTERVAL", "7")
    fake = FakeRunner()
    kwargs = {}
    def factory(**options):
        kwargs.update(options)
        return fake
    monkeypatch.setattr(fr, "runner_from_config", factory)
    runner = _maybe_start_federation()
    assert runner is fake
    assert fake.started_with == 7.0
    from agent_dispatch import server
    assert kwargs == {"is_active": server._owns_active_route, "drain_gate": None}


def test_none_when_runner_from_config_returns_none(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_FEDERATION_ROLE", "peer")
    monkeypatch.setattr(fr, "runner_from_config", lambda **kwargs: None)
    assert _maybe_start_federation() is None
