"""Tests for startup-tolerance state classification + the patient waiter."""

from __future__ import annotations

from unittest.mock import patch

from agent_codespaces.lifecycle import (
    CodespaceInfo,
    WaitOutcome,
    classify_state,
    wait_for_available,
    wait_for_codespace,
)


def _cs(name: str, state: str) -> CodespaceInfo:
    return CodespaceInfo(
        name=name, display_name=name, repository="org/repo",
        branch="main", state=state, machine="m",
    )


def test_classify_state_buckets():
    assert classify_state("Available") == "available"
    assert classify_state("Failed") == "failed"
    assert classify_state("Unavailable") == "failed"
    assert classify_state("Deleted") == "failed"
    # Transient / resting states are all "pending" -- keep waiting.
    assert classify_state("Provisioning") == "pending"
    assert classify_state("Starting") == "pending"
    assert classify_state("Shutdown") == "pending"
    assert classify_state("Queued") == "pending"
    assert classify_state("Unknown") == "pending"


def test_wait_returns_available_immediately():
    with patch(
        "agent_codespaces.lifecycle.list_codespaces",
        return_value=[_cs("cs-one", "Available")],
    ):
        outcome, state = wait_for_codespace("cs-one", timeout=5, interval=1)
    assert outcome == WaitOutcome.AVAILABLE
    assert state == "Available"


def test_wait_fails_fast_on_terminal_state():
    # A genuinely-dead state returns FAILED without burning the whole timeout.
    with patch(
        "agent_codespaces.lifecycle.list_codespaces",
        return_value=[_cs("cs-one", "Failed")],
    ):
        outcome, state = wait_for_codespace("cs-one", timeout=999, interval=1)
    assert outcome == WaitOutcome.FAILED
    assert state == "Failed"


def test_wait_times_out_while_pending():
    # Perpetually provisioning -> TIMEOUT (never mistaken for dead).
    with patch(
        "agent_codespaces.lifecycle.list_codespaces",
        return_value=[_cs("cs-one", "Provisioning")],
    ):
        outcome, state = wait_for_codespace("cs-one", timeout=0.2, interval=0.05)
    assert outcome == WaitOutcome.TIMEOUT
    assert state == "Provisioning"


def test_wait_tolerates_list_errors():
    with patch(
        "agent_codespaces.lifecycle.list_codespaces",
        side_effect=RuntimeError("gh hiccup"),
    ):
        outcome, _ = wait_for_codespace("cs-one", timeout=0.2, interval=0.05)
    assert outcome == WaitOutcome.TIMEOUT


def test_wait_for_available_shim_true():
    with patch(
        "agent_codespaces.lifecycle.list_codespaces",
        return_value=[_cs("cs-one", "Available")],
    ):
        assert wait_for_available("cs-one", timeout=5, interval=1) is True


def test_wait_for_available_shim_false_on_failed():
    with patch(
        "agent_codespaces.lifecycle.list_codespaces",
        return_value=[_cs("cs-one", "Failed")],
    ):
        assert wait_for_available("cs-one", timeout=999, interval=1) is False
