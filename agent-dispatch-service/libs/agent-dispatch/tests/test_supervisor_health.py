"""Unit tests for :mod:`agent_dispatch.supervisor_health`."""

from __future__ import annotations

import time

from agent_dispatch.supervisor_health import (
    supervisor_stall_action,
    supervisor_stall_seconds,
)


def test_supervisor_stall_seconds_none_when_idle_and_healthy():
    now = time.time()
    assert supervisor_stall_seconds(
        {"cycle_started_at": now - 5, "updated_at": now}
    ) is None


def test_supervisor_stall_seconds_none_without_enough_information():
    assert supervisor_stall_seconds(None) is None
    assert supervisor_stall_seconds({}) is None
    assert supervisor_stall_seconds({"updated_at": time.time()}) is None


def test_supervisor_stall_seconds_none_below_the_threshold():
    now = time.time()
    # A cycle in flight for 10s with no finish yet is completely normal.
    assert supervisor_stall_seconds(
        {"cycle_started_at": now - 10, "updated_at": now - 40}
    ) is None


def test_supervisor_stall_seconds_flags_a_long_unfinished_cycle():
    now = time.time()
    started = now - 400
    result = supervisor_stall_seconds(
        {"cycle_started_at": started, "updated_at": started - 30}
    )
    assert result is not None
    assert result >= 400


def test_supervisor_stall_action_names_the_installer_restart_path():
    action = supervisor_stall_action(215.0)
    assert "215" in action
    assert "install.ps1 update" in action
    assert "manual process kill" in action
