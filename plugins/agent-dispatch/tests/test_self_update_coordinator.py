"""Tests for the coordinator's live self-update settings/opt-in gate.

``_self_update_settings`` is the environment-var parsing seam for the
self-update loop wired into ``coordinator.create_app``'s lifespan (see
``test_self_update.py`` for the pure staleness-detection predicate itself).
"""

from __future__ import annotations

from agent_dispatch.coordinator import _self_update_settings


def test_self_update_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_SELF_UPDATE", raising=False)
    enabled, poll, k, cooldown = _self_update_settings()
    assert enabled is False


def test_self_update_opt_in_truthy_values(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE", value)
        enabled, *_ = _self_update_settings()
        assert enabled is True, value


def test_self_update_falsy_values_stay_disabled(monkeypatch):
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE", value)
        enabled, *_ = _self_update_settings()
        assert enabled is False, value


def test_self_update_defaults(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE", "1")
    monkeypatch.delenv("AGENT_DISPATCH_SELF_UPDATE_POLL_S", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_SELF_UPDATE_CONFIRMATIONS", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_SELF_UPDATE_COOLDOWN_S", raising=False)
    enabled, poll, k, cooldown = _self_update_settings()
    assert enabled is True
    assert poll == 60.0
    assert k == 3
    assert cooldown == 900.0


def test_self_update_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE", "1")
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE_POLL_S", "5")
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE_CONFIRMATIONS", "1")
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE_COOLDOWN_S", "10")
    enabled, poll, k, cooldown = _self_update_settings()
    assert enabled is True
    assert poll == 5.0
    assert k == 1
    assert cooldown == 10.0


def test_self_update_invalid_overrides_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE", "1")
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE_POLL_S", "not-a-number")
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE_CONFIRMATIONS", "not-a-number")
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE_COOLDOWN_S", "not-a-number")
    enabled, poll, k, cooldown = _self_update_settings()
    assert poll == 60.0
    assert k == 3
    assert cooldown == 900.0


def test_self_update_non_finite_overrides_fall_back_to_defaults(monkeypatch):
    # float("nan")/float("inf") parse successfully but are invalid here: nan
    # breaks the cooldown comparison and inf/nan both break asyncio.sleep().
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE", "1")
    for bad in ("nan", "inf", "-inf", "Infinity"):
        monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE_POLL_S", bad)
        monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE_COOLDOWN_S", bad)
        enabled, poll, k, cooldown = _self_update_settings()
        assert poll == 60.0, bad
        assert cooldown == 900.0, bad
