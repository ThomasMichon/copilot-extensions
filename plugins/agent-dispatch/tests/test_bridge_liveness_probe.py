"""Tests for the Phase 10 item 4 HOT/WARM/COLD local-body liveness probe."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_dispatch import bridge_liveness_probe
from agent_dispatch.bridge_state_machine import Liveness


def _patch(monkeypatch, *, prefix=("agent-bridge",), run=None):
    monkeypatch.setattr(bridge_liveness_probe, "_agent_bridge_launch_prefix", lambda: list(prefix))
    if run is not None:
        monkeypatch.setattr(bridge_liveness_probe.subprocess, "run", run)


@pytest.mark.parametrize("status", ["running", "RUNNING", " Running "])
def test_running_status_resolves_hot(monkeypatch, status):
    _patch(
        monkeypatch,
        run=lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"status": status}), stderr=""
        ),
    )
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.HOT


@pytest.mark.parametrize("status", ["idle", "created", "starting"])
def test_alive_but_idle_statuses_resolve_warm(monkeypatch, status):
    _patch(
        monkeypatch,
        run=lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"status": status}), stderr=""
        ),
    )
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.WARM


@pytest.mark.parametrize("status", ["stopping", "stopped", "failed", "ended"])
def test_terminal_statuses_resolve_cold(monkeypatch, status):
    _patch(
        monkeypatch,
        run=lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"status": status}), stderr=""
        ),
    )
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.COLD


def test_unrecognized_status_resolves_hot_not_guessed(monkeypatch):
    _patch(
        monkeypatch,
        run=lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"status": "something-new"}), stderr=""
        ),
    )
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.HOT


def test_not_found_exit_resolves_cold(monkeypatch):
    _patch(
        monkeypatch,
        run=lambda *a, **k: SimpleNamespace(
            returncode=1, stdout="", stderr="[FAIL] Session sid not found"
        ),
    )
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.COLD


def test_other_nonzero_exit_resolves_hot_not_cold(monkeypatch):
    _patch(
        monkeypatch,
        run=lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="connection refused"),
    )
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.HOT


def test_empty_stdout_resolves_hot(monkeypatch):
    _patch(monkeypatch, run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.HOT


def test_unparseable_json_resolves_hot(monkeypatch):
    _patch(
        monkeypatch,
        run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="not json", stderr=""),
    )
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.HOT


def test_non_dict_json_resolves_hot(monkeypatch):
    _patch(
        monkeypatch,
        run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="[1, 2, 3]", stderr=""),
    )
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.HOT


def test_timeout_resolves_hot(monkeypatch):
    import subprocess as real_subprocess

    def raise_timeout(*a, **k):
        raise real_subprocess.TimeoutExpired(cmd="agent-bridge", timeout=8.0)

    _patch(monkeypatch, run=raise_timeout)
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.HOT


def test_os_error_resolves_hot(monkeypatch):
    def raise_os_error(*a, **k):
        raise OSError("no such file")

    _patch(monkeypatch, run=raise_os_error)
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.HOT


def test_no_launch_prefix_resolves_hot(monkeypatch):
    monkeypatch.setattr(bridge_liveness_probe, "_agent_bridge_launch_prefix", lambda: None)
    assert bridge_liveness_probe.local_body_liveness_probe("sid") == Liveness.HOT


def test_empty_session_id_resolves_hot(monkeypatch):
    _patch(monkeypatch, run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="{}", stderr=""))
    assert bridge_liveness_probe.local_body_liveness_probe("") == Liveness.HOT


def test_never_raises_across_every_failure_path(monkeypatch):
    # Sanity check across all the failure branches above -- none should
    # propagate an exception out of the probe.
    def boom(*a, **k):
        raise RuntimeError("should not propagate")

    _patch(monkeypatch, prefix=("agent-bridge",))
    monkeypatch.setattr(bridge_liveness_probe, "_agent_bridge_launch_prefix", lambda: None)
    result = bridge_liveness_probe.local_body_liveness_probe("sid")
    assert result == Liveness.HOT
