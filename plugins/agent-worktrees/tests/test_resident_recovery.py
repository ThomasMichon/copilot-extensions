"""Maintenance-aware discovery never replays mutations or repairs a host."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import resident_recovery as recovery, tracking_write as writes


@pytest.fixture
def private_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "runtime")
    root = tmp_path / ".copilot-extensions"
    root.mkdir()
    return root


def notice(root: Path, status: str = "active") -> None:
    (root / "maintenance.json").write_text(json.dumps({
        "version": 1, "status": status, "ownerSessionId": "fixture",
        "summary": "Fixture maintenance", "startedAt": "2026-11-05T00:00:00Z",
    }))


@pytest.mark.parametrize("kind", ["notice", "marker", "invalid", "oversized", "directory"])
def test_maintenance_holds_before_backoff_wake_or_fallback(private_recovery, monkeypatch, kind):
    root = private_recovery
    if kind == "notice":
        notice(root)
    elif kind == "marker":
        (root / "maintenance").touch()
    elif kind == "directory":
        (root / "maintenance.json").mkdir()
    else:
        (root / "maintenance.json").write_text("?" * (16385 if kind == "oversized" else 1))
    effects = []
    monkeypatch.setattr(writes.time, "sleep", lambda delay: effects.append("sleep"))
    with pytest.raises(recovery.ResidentRecoveryHeld):
        writes.write_with_boot(
            read_lock_data=lambda: None, ensure_monitor=lambda: effects.append("wake"),
            payload={"verb": "fixture"}, fallback=lambda: effects.append("fallback"),
        )
    assert effects == []
    assert recovery.wake_allowed() is False


@pytest.mark.parametrize("mode", ["arrives", "wake", "absent", "maintenance", "ambiguous", "no-wake"])
def test_bounded_recovery_sequence(private_recovery, monkeypatch, mode):
    clock, effects = [0.0], []
    ready = False
    endpoint = {
        "tracking_write_endpoint": "127.0.0.1:49152",
        "tracking_write_token": "fixture", "tracking_write_verbs": ["fixture"],
    }

    def sleep(delay):
        nonlocal ready
        effects.append("backoff")
        clock[0] += delay
        if mode == "maintenance":
            notice(private_recovery)
        if mode == "arrives":
            ready = True

    def wake():
        nonlocal ready
        effects.append("wake")
        ready = mode in {"wake", "ambiguous"}

    def send(*args, **kwargs):
        effects.append("send")
        if mode == "ambiguous":
            raise writes.AmbiguousWriteOutcome("possibly committed")
        return {"ok": True}

    monkeypatch.setattr(writes, "time", SimpleNamespace(time=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(writes, "_send_tracking_write_request", send)
    monkeypatch.setattr(writes.wcs_client, "release", lambda *args, **kwargs: None)
    options = dict(
        read_lock_data=lambda: endpoint if ready else None,
        ensure_monitor=None if mode == "no-wake" else wake,
        payload={"verb": "fixture"}, fallback=lambda: effects.append("fallback") or {"direct": True},
        boot_wait_s=.3,
    )
    if mode in {"maintenance", "ambiguous"}:
        with pytest.raises(
            recovery.ResidentRecoveryHeld if mode == "maintenance" else writes.AmbiguousWriteOutcome,
        ):
            writes.write_with_boot(**options)
        assert "fallback" not in effects
        assert effects.count("send") == (mode == "ambiguous")
    else:
        result = writes.write_with_boot(**options)
        assert result == ({"ok": True} if mode in {"arrives", "wake"} else {"direct": True})
        assert effects.count("wake") == (mode in {"wake", "absent"})
        assert effects.count("send") == (mode in {"arrives", "wake"})
        assert clock[0] <= .4
        if mode == "no-wake":
            assert effects == ["fallback"]


def test_complete_notice_and_removed_installation_marker_do_not_hold(private_recovery):
    notice(private_recovery, "complete")
    recovery.require_wake_allowed()
    (private_recovery / "maintenance.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-maintenance",
    }))
    recovery.require_wake_allowed()


def test_effort_dispatch_uses_normal_ensure(private_recovery, monkeypatch):
    from agent_worktrees import status_monitor_runtime as monitor, tracking_effort_binding_write as effort
    received = {}
    monkeypatch.setattr(monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(writes, "dispatch", lambda *args, **kwargs: received.update(kwargs) or {})
    effort.dispatch("effort_bind", {})
    assert received["ensure_monitor"] is monitor._ensure_status_monitor


def test_normal_ensure_does_not_spawn_during_maintenance(private_recovery, monkeypatch):
    from agent_worktrees import status_monitor_runtime as monitor
    notice(private_recovery)
    monkeypatch.setattr(monitor.locks, "read_lock", lambda path: None)
    monkeypatch.setattr(monitor, "_spawn_detached", lambda *args: pytest.fail("unexpected spawn"))
    assert monitor._ensure_status_monitor() is False


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("same_endpoint", [False, True])
def test_refused_connection_can_recover_only_before_any_send(
    private_recovery, monkeypatch, held, same_endpoint,
):
    current = {
        "tracking_write_endpoint": "127.0.0.1:49152", "tracking_write_token": "fixture",
        "tracking_write_verbs": ["fixture"],
    }
    calls = []

    def wake():
        calls.append("wake")
        if not same_endpoint:
            current["tracking_write_endpoint"] = "127.0.0.1:49153"

    def send(host, port, *args, **kwargs):
        calls.append(port)
        if len(calls) == 1:
            raise writes._PreSendFailure("connection refused before request")
        return {"ok": True}

    if held:
        notice(private_recovery)
    monkeypatch.setattr(writes.time, "sleep", lambda delay: None)
    monkeypatch.setattr(writes, "_send_tracking_write_request", send)
    monkeypatch.setattr(writes.wcs_client, "release", lambda *args, **kwargs: None)
    options = dict(
        read_lock_data=lambda: current, ensure_monitor=wake,
        payload={"verb": "fixture"}, fallback=lambda: pytest.fail("unexpected direct write"),
    )
    if held:
        with pytest.raises(recovery.ResidentRecoveryHeld):
            writes.write_with_boot(**options)
        assert calls == [49152]
    else:
        assert writes.write_with_boot(**options) == {"ok": True}
        assert calls == [49152, "wake", 49152 if same_endpoint else 49153]


def test_recovery_reaches_actual_canonical_capture(private_recovery, tmp_path, monkeypatch):
    from agent_worktrees import tracking
    from agent_worktrees import tracking_write
    from _native_receipt_fixtures import setup, publish, page

    owner, path, home = setup(tmp_path / "source", monkeypatch)
    server = None
    endpoint = None

    def wake():
        nonlocal server, endpoint
        server = tracking_write.start_server(owner.native_compute)
        server.start()
        endpoint = tracking_write.rendezvous_fields(server)
        return True

    try:
        result = writes.dispatch(
            "claim_add", {
                "yaml_path": str(path), "worktree_id": "native-one",
                "kind": "codespace", "ref": "fixture-child",
            },
            read_lock_data=lambda: endpoint, ensure_monitor=wake,
        )
        assert result["ok"]
        assert [claim.ref for claim in tracking.load_record(path).resources] == ["fixture-child"]
        publish(owner, path)
        history = page(owner)
        assert any(row["kind"] == "claim-added" and row["evidence_kind"] == "persisted-mutation"
                   for row in history["entries"])
    finally:
        if server is not None:
            server.close()
        owner.shutdown()
