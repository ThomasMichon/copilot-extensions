"""Tests for coordinator configuration resolution."""

from __future__ import annotations

from agent_dispatch.config import DEFAULT_SWEEP_INTERVAL, load_config


def test_sweep_interval_default(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_SWEEP_INTERVAL", raising=False)
    assert load_config().sweep_interval == DEFAULT_SWEEP_INTERVAL


def test_sweep_interval_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SWEEP_INTERVAL", "5")
    assert load_config().sweep_interval == 5.0


def test_sweep_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SWEEP_INTERVAL", "0")
    assert load_config().sweep_interval == 0.0
