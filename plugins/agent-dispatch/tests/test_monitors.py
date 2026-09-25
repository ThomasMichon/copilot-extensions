"""Pure, fake-clock tests for the monitor vocabulary (no DB, no subprocess).

These mirror ``test_task_state_machine.py``'s posture: structural/behavioral
checks against declared data, entirely independent of any live coordinator.
"""

from __future__ import annotations

import pytest

from agent_dispatch.monitors import (
    DEFAULT_COOLDOWN_SECONDS,
    CooldownMonitor,
    MonitorKind,
    default_monitor,
    is_resolved,
)


def test_default_monitor_is_a_cooldown():
    monitor = default_monitor(now=1000.0)
    assert isinstance(monitor, CooldownMonitor)
    assert monitor.kind is MonitorKind.COOLDOWN


def test_default_monitor_uses_the_default_duration():
    monitor = default_monitor(now=1000.0)
    assert monitor.not_before == 1000.0 + DEFAULT_COOLDOWN_SECONDS


def test_default_monitor_honors_an_explicit_duration():
    monitor = default_monitor(now=1000.0, cooldown_seconds=30.0)
    assert monitor.not_before == 1030.0


def test_default_monitor_rejects_a_negative_duration():
    with pytest.raises(ValueError):
        default_monitor(now=1000.0, cooldown_seconds=-1.0)


def test_zero_cooldown_is_allowed_and_resolves_immediately():
    monitor = default_monitor(now=1000.0, cooldown_seconds=0.0)
    assert is_resolved(monitor, now=1000.0)


def test_not_resolved_before_the_deadline():
    monitor = default_monitor(now=1000.0, cooldown_seconds=60.0)
    assert not is_resolved(monitor, now=1059.0)


def test_resolved_exactly_at_the_deadline():
    monitor = default_monitor(now=1000.0, cooldown_seconds=60.0)
    assert is_resolved(monitor, now=1060.0)


def test_resolved_after_the_deadline():
    monitor = default_monitor(now=1000.0, cooldown_seconds=60.0)
    assert is_resolved(monitor, now=1200.0)


def test_cooldown_monitor_rejects_a_foreign_kind():
    with pytest.raises(ValueError):
        CooldownMonitor(kind=None, not_before=0.0)  # type: ignore[arg-type]


def test_is_resolved_rejects_an_unknown_monitor_type():
    with pytest.raises(TypeError):
        is_resolved(object(), now=0.0)  # type: ignore[arg-type]
