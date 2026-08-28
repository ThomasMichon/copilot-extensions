"""Tests for the lease broker (docker mocked, paths redirected to tmp)."""

from __future__ import annotations

import json
import os
import time

import pytest

from agent_containers import lease as lease_mod
from agent_containers.config import ContainersConfig
from agent_containers.lifecycle import DockerContainerInfo


@pytest.fixture
def fleet(monkeypatch, tmp_path):
    """Redirect lease state to tmp and stub docker discovery."""
    monkeypatch.setattr(lease_mod, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(lease_mod, "ensure_state_dir", lambda: None)
    monkeypatch.setattr(lease_mod, "_LOCK_FILE", tmp_path / "leases.lock")
    monkeypatch.setattr(
        lease_mod,
        "_DEPLOY_HOLDS_FILE",
        tmp_path / "deploy-holds.json",
    )
    monkeypatch.setattr(
        lease_mod,
        "_SESSION_ADMISSIONS_FILE",
        tmp_path / "session-admissions.json",
    )

    containers = [
        DockerContainerInfo("myrepo-1", "i1", "img", "running", "", fleet="myrepo"),
        DockerContainerInfo("myrepo-2", "i2", "img", "exited", "", fleet="myrepo"),
    ]
    monkeypatch.setattr(lease_mod, "list_containers", lambda config: containers)
    return ContainersConfig()


def test_borrow_picks_running_first(fleet):
    lease = lease_mod.borrow(fleet, "effort-a")
    assert lease.container == "myrepo-1"
    assert lease.effort == "effort-a"


def test_borrow_excludes_already_leased(fleet):
    lease_mod.borrow(fleet, "effort-a")  # takes myrepo-1
    lease = lease_mod.borrow(fleet, "effort-b")
    assert lease.container == "myrepo-2"


def test_borrow_all_leased_raises(fleet):
    lease_mod.borrow(fleet, "a")
    lease_mod.borrow(fleet, "b")
    with pytest.raises(RuntimeError, match="All fleet containers"):
        lease_mod.borrow(fleet, "c")


def test_borrow_specific_container(fleet):
    lease = lease_mod.borrow(fleet, "effort-a", container="myrepo-2")
    assert lease.container == "myrepo-2"


def test_borrow_specific_conflict_raises(fleet):
    lease_mod.borrow(fleet, "effort-a", container="myrepo-1")
    with pytest.raises(RuntimeError, match="leased by effort 'effort-a'"):
        lease_mod.borrow(fleet, "effort-b", container="myrepo-1")


def test_borrow_same_effort_idempotent(fleet):
    first = lease_mod.borrow(fleet, "effort-a", container="myrepo-1")
    second = lease_mod.borrow(fleet, "effort-a", container="myrepo-1")
    assert second.container == "myrepo-1"
    # acquired_at preserved across re-borrow
    assert second.acquired_at == first.acquired_at


def test_borrow_same_effort_without_specific_container_is_idempotent(fleet):
    first = lease_mod.borrow(fleet, "effort-a")
    second = lease_mod.borrow(fleet, "effort-a")
    assert second.container == first.container
    assert second.acquired_at == first.acquired_at


def test_release_by_container(fleet):
    lease_mod.borrow(fleet, "effort-a")
    assert lease_mod.release("myrepo-1") is True
    assert lease_mod.list_leases() == []


def test_release_by_effort(fleet):
    lease_mod.borrow(fleet, "effort-a")
    assert lease_mod.release("effort-a") is True
    assert lease_mod.get_lease("myrepo-1") is None


def test_release_missing_returns_false(fleet):
    assert lease_mod.release("nope") is False


def test_reclaim_after_ttl(fleet):
    lease_mod.borrow(fleet, "effort-a")  # leases myrepo-1
    # A negative TTL means any non-negative age is past expiry -- deterministic
    # regardless of clock resolution.
    assert lease_mod.list_leases(ttl=-1) == []


def test_lease_survives_within_ttl(fleet):
    lease_mod.borrow(fleet, "effort-a")
    # Default generous TTL -> lease persists across reads (and processes).
    leases = lease_mod.list_leases()
    assert len(leases) == 1
    assert leases[0].effort == "effort-a"


def test_concurrent_borrow_is_blocked_by_provider_deploy_hold(fleet):
    with lease_mod.deploy_hold("myrepo-1", "recreate"):
        with pytest.raises(RuntimeError, match="provider recreate is in progress"):
            lease_mod.borrow(
                fleet,
                "effort-a",
                container="myrepo-1",
            )


def test_idempotent_reborrow_is_also_blocked_by_provider_hold(fleet):
    lease_mod.borrow(fleet, "effort-a", container="myrepo-1")

    with lease_mod.deploy_hold("myrepo-1", "recreate"):
        with pytest.raises(
            lease_mod.ProviderAdmissionError,
            match="provider recreate is in progress",
        ):
            lease_mod.borrow(fleet, "effort-a")


def test_existing_session_is_visible_after_hold_and_new_session_is_blocked(fleet):
    with lease_mod.session_admission("myrepo-1"):
        admissions = lease_mod.active_session_admissions("myrepo-1")
        assert len(admissions) == 1
        with lease_mod.deploy_hold("myrepo-1", "remove"):
            assert len(lease_mod.active_session_admissions("myrepo-1")) == 1

    with lease_mod.deploy_hold("myrepo-1", "recreate"):
        with pytest.raises(RuntimeError, match="provider recreate is in progress"):
            with lease_mod.session_admission("myrepo-1"):
                pass


def test_unreadable_deploy_hold_state_fails_admission_closed(fleet):
    lease_mod._DEPLOY_HOLDS_FILE.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="refusing provider admission"):
        lease_mod.borrow(fleet, "effort-a", container="myrepo-1")


def test_process_liveness_reuses_windows_safe_ssh_manager_probe(
    fleet,
    monkeypatch,
):
    called = []
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda pid: called.append(pid) or True,
    )
    record = lease_mod.DeployHold(
        container="myrepo-1",
        operation="recreate",
        token="record-id",  # noqa: S106
        pid=123,
        host=lease_mod._this_host(),
        environment=lease_mod._this_environment(),
        acquired_at=time.time(),
        heartbeat_at=time.time(),
        expires_at=time.time() + lease_mod.DEPLOY_HOLD_TTL,
    )

    assert lease_mod._record_live(record, lease_mod.DEPLOY_HOLD_TTL) is True
    assert called == [123]


def test_cross_environment_hold_is_preserved_fail_closed(fleet, monkeypatch):
    monkeypatch.setattr(lease_mod, "_this_environment", lambda: "wsl")
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("must not inspect a Windows PID from WSL")
        ),
    )
    hold = lease_mod.DeployHold(
        container="myrepo-1",
        operation="recreate",
        token="record-id",  # noqa: S106
        pid=123,
        host=lease_mod._this_host(),
        environment="windows",
        acquired_at=time.time(),
        heartbeat_at=time.time(),
        expires_at=time.time() + lease_mod.DEPLOY_HOLD_TTL,
    )
    lease_mod._write_records(
        lease_mod._DEPLOY_HOLDS_FILE,
        {"myrepo-1": hold},
    )

    observed = lease_mod.get_deploy_hold("myrepo-1")

    assert observed is not None
    assert observed.environment == "windows"
    assert lease_mod._DEPLOY_HOLDS_FILE.exists()


def test_expired_cross_environment_hold_can_be_cleared_safely(
    fleet,
    monkeypatch,
):
    monkeypatch.setattr(lease_mod, "_this_environment", lambda: "wsl")
    hold = lease_mod.DeployHold(
        container="myrepo-1",
        operation="recreate",
        token="record-id",  # noqa: S106
        pid=123,
        host=lease_mod._this_host(),
        environment="windows",
        acquired_at=time.time() - 1000,
        heartbeat_at=time.time() - lease_mod.DEPLOY_HOLD_TTL - 1,
        expires_at=time.time() - 1,
    )
    lease_mod._write_records(
        lease_mod._DEPLOY_HOLDS_FILE,
        {"myrepo-1": hold},
    )

    cleared = lease_mod.clear_stale_provider_records("myrepo-1")

    assert cleared["deploy_holds"] == 1
    assert lease_mod.get_deploy_hold("myrepo-1") is None


def test_deploy_hold_heartbeats_and_verifies_token(fleet, monkeypatch):
    monkeypatch.setattr(lease_mod, "_RECORD_HEARTBEAT_INTERVAL", 0.01)
    with lease_mod.deploy_hold("myrepo-1", "recreate") as hold:
        initial_heartbeat = hold.heartbeat_at
        time.sleep(0.04)
        current = lease_mod.verify_deploy_hold("myrepo-1", hold.token)
        assert current.heartbeat_at > initial_heartbeat
        with pytest.raises(lease_mod.ProviderAdmissionError):
            lease_mod.verify_deploy_hold("myrepo-1", "wrong-token")


def test_deploy_hold_max_lifetime_expires_despite_heartbeat(fleet, monkeypatch):
    monkeypatch.setattr(lease_mod, "_RECORD_HEARTBEAT_INTERVAL", 0.005)
    with lease_mod.deploy_hold(
        "myrepo-1",
        "recreate",
        max_lifetime=0.02,
    ) as hold:
        time.sleep(0.04)
        with pytest.raises(lease_mod.ProviderAdmissionError):
            lease_mod.verify_deploy_hold("myrepo-1", hold.token)


def test_unconfirmed_action_hold_survives_owner_exit_until_expiry(fleet):
    with lease_mod.deploy_hold(
        "myrepo-1",
        "remove",
        max_lifetime=0.05,
    ) as hold:
        lease_mod.mark_deploy_hold_uncertain("myrepo-1", hold.token)

    assert lease_mod.get_deploy_hold("myrepo-1") is not None
    time.sleep(0.06)
    assert lease_mod.get_deploy_hold("myrepo-1") is None


def test_lease_lock_cleanup_never_unlinks_new_owner(fleet):
    with lease_mod._lease_lock():
        lease_mod._LOCK_FILE.write_text("replacement-owner", encoding="ascii")

    assert lease_mod._LOCK_FILE.read_text(encoding="ascii") == "replacement-owner"


def test_corrupt_hold_is_observable_but_admission_stays_closed(fleet):
    lease_mod._DEPLOY_HOLDS_FILE.write_text("{not-json", encoding="utf-8")

    status = lease_mod.deploy_hold_status("myrepo-1")

    assert status["state"] == "unknown"
    with pytest.raises(lease_mod.ProviderAdmissionError):
        lease_mod.get_deploy_hold("myrepo-1")


def test_malformed_hold_values_are_observable_unknown(fleet):
    lease_mod._DEPLOY_HOLDS_FILE.write_text(
        json.dumps(
            {
                "myrepo-1": {
                    "container": "myrepo-1",
                    "operation": "recreate",
                    "token": "record-id",
                    "pid": 123,
                    "host": lease_mod._this_host(),
                    "environment": lease_mod._this_environment(),
                    "acquired_at": time.time(),
                    "heartbeat_at": "not-a-number",
                    "expires_at": time.time() + lease_mod.DEPLOY_HOLD_TTL,
                }
            }
        ),
        encoding="utf-8",
    )

    assert lease_mod.deploy_hold_status("myrepo-1")["state"] == "unknown"


def test_safe_clear_refuses_fresh_corruption_then_clears_expired_file(fleet):
    lease_mod._DEPLOY_HOLDS_FILE.write_text("{not-json", encoding="utf-8")
    with pytest.raises(lease_mod.ProviderAdmissionError):
        lease_mod.clear_stale_provider_records()

    old = time.time() - lease_mod.DEPLOY_HOLD_TTL - 1
    os.utime(lease_mod._DEPLOY_HOLDS_FILE, (old, old))
    cleared = lease_mod.clear_stale_provider_records()

    assert cleared["deploy_holds"] == 1
    assert not lease_mod._DEPLOY_HOLDS_FILE.exists()
