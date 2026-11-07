from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_manager import daemon_health


def test_candidates_are_scoped_to_requested_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "cell-a"
    root.mkdir()
    monkeypatch.setattr(
        daemon_health.mux_daemon_cutover,
        "_iter_mux_daemon_pids",
        lambda: {101, 202},
    )
    monkeypatch.setattr(
        daemon_health.mux_daemon_cutover,
        "_pid_matches_root",
        lambda pid, *, root: pid == 101,
    )
    monkeypatch.setattr(
        daemon_health.diagnostics,
        "process_start_time",
        lambda pid: f"start-{pid}",
    )

    candidates = daemon_health._candidates(root)

    assert [candidate.pid for candidate in candidates] == [101]
    assert candidates[0].start_time == "start-101"


@pytest.mark.parametrize("credential", [None, "", "retained-fixture-control"])
def test_diagnostic_probe_only_reads_existing_credentials(tmp_path, monkeypatch, credential):
    cutover = daemon_health.mux_daemon_cutover
    root = tmp_path / "manager"
    directory = cutover.routing_dir(root)
    directory.mkdir(parents=True)
    record = directory / "cutover.json"
    record.write_text(json.dumps({
        "state": "draining", "old": {"bind": "127.0.0.1", "port": 1},
        "updated_at": "2000-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    original = record.read_bytes()
    token = cutover.control_token_path(root)
    if credential is not None:
        token.write_text(credential, encoding="utf-8")
    observed = []
    monkeypatch.setattr(cutover, "_iter_mux_daemon_pids", lambda: set())
    monkeypatch.setattr(
        cutover.ControlClient, "health",
        lambda client: observed.append(client.token) or {"status": "ready"},
    )
    report = daemon_health.doctor_report(root=root, apply=False)
    assert record.read_bytes() == original
    if credential:
        assert observed == [credential] and token.read_text(encoding="utf-8") == credential
        assert report["counts"].get("control_credential_unavailable", 0) == 0
    else:
        assert not observed
        if credential is None:
            assert not token.exists()
        else:
            assert token.read_text(encoding="utf-8") == credential
        assert report["counts"]["control_credential_unavailable"] == 1
        finding = next(f for f in report["findings"] if f["kind"] == "control_credential_unavailable")
        assert not finding["repairable"] and "unconfirmed" in finding["summary"]


def test_generation_health_probe_never_creates_a_missing_credential(tmp_path, monkeypatch):
    cutover = daemon_health.mux_daemon_cutover
    monkeypatch.setattr(cutover.ControlClient, "health", lambda client: {"status": "ready"})
    assert not cutover._health_check("127.0.0.1", 1, root=tmp_path)
    assert not cutover.control_token_path(tmp_path).exists()


def test_inactive_diagnostics_do_not_require_control_credentials(tmp_path, monkeypatch):
    cutover = daemon_health.mux_daemon_cutover
    monkeypatch.setattr(cutover, "_iter_mux_daemon_pids", lambda: set())
    monkeypatch.setattr(cutover.ControlClient, "health", lambda client: pytest.fail("inactive probe"))
    report = daemon_health.doctor_report(root=tmp_path, apply=False)
    assert report["counts"].get("control_credential_unavailable", 0) == 0
    assert not cutover.control_token_path(tmp_path).exists()
