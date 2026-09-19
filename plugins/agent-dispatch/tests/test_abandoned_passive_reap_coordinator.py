"""Tests for the coordinator's abandoned-passive-reap settings/opt-in gate.

``_abandoned_passive_reap_settings`` is the environment-var parsing seam for
the periodic sweep wired into ``coordinator.create_app``'s lifespan that
retires a ``spawn_passive`` daemon a cutover never got to promote
(aperture-labs #5195). See ``test_reap.py`` for the pure
``reap_abandoned_passive_backstop``/``is_live_coordinator_pid`` primitives it
composes, and ``libs/zdd/tests/test_breadcrumb.py`` for the library-level
``reap_abandoned_passive`` this ultimately calls.
"""

from __future__ import annotations

from agent_dispatch.coordinator import _abandoned_passive_reap_settings


def test_abandoned_passive_reap_enabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", raising=False)
    enabled, _poll, _grace = _abandoned_passive_reap_settings()
    assert enabled is True


def test_abandoned_passive_reap_falsy_values_disable(monkeypatch):
    for value in ("0", "false", "no", "off"):
        monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", value)
        enabled, *_ = _abandoned_passive_reap_settings()
        assert enabled is False, value


def test_abandoned_passive_reap_defaults(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP_POLL_S", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP_GRACE_S", raising=False)
    enabled, poll, grace = _abandoned_passive_reap_settings()
    assert enabled is True
    assert poll == 120.0
    assert grace == 600.0


def test_abandoned_passive_reap_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP_POLL_S", "30")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP_GRACE_S", "60")
    _enabled, poll, grace = _abandoned_passive_reap_settings()
    assert poll == 30.0
    assert grace == 60.0


def test_abandoned_passive_reap_invalid_overrides_fall_back_to_defaults(
    monkeypatch,
):
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP_POLL_S", "not-a-number")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP_GRACE_S", "not-a-number")
    _enabled, poll, grace = _abandoned_passive_reap_settings()
    assert poll == 120.0
    assert grace == 600.0
