"""Tests for the lease broker (docker mocked, paths redirected to tmp)."""

from __future__ import annotations

import json
import os
import stat
import threading
import time

import pytest

from agent_containers import lease as lease_mod
from agent_containers import private_state
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


def _seed_lease(
    *,
    container="myrepo-1",
    effort="previous-effort",
    pid=123,
    host=None,
    environment=None,
    heartbeat_at=None,
):
    now = time.time()
    lease_mod._write_leases(
        {
            container: lease_mod.Lease(
                container=container,
                effort=effort,
                pid=pid,
                host=host or lease_mod._this_host(),
                acquired_at=now - 60,
                heartbeat_at=heartbeat_at or now,
                environment=environment or lease_mod._this_environment(),
            )
        }
    )


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


def test_provider_lease_guard_blocks_release_until_registry_write_finishes(fleet):
    lease_mod.borrow(fleet, "effort-a", container="myrepo-1")
    started = threading.Event()
    result = []

    def release():
        started.set()
        result.append(lease_mod.release("myrepo-1"))

    with lease_mod.provider_lease_guard() as leases:
        assert [lease.container for lease in leases] == ["myrepo-1"]
        worker = threading.Thread(target=release)
        worker.start()
        assert started.wait(timeout=1)
        worker.join(timeout=0.05)
        assert worker.is_alive()

    worker.join(timeout=1)
    assert not worker.is_alive()
    assert result == [True]


def test_session_admission_binds_lease_and_blocks_release(fleet):
    lease = lease_mod.borrow(fleet, "effort-a", container="myrepo-1")
    assignment = {
        "kind": "lease",
        "effort": lease.effort,
        "acquired_at": lease.acquired_at,
    }

    with lease_mod.session_admission(
        "myrepo-1",
        expected_assignment=assignment,
    ):
        with pytest.raises(
            lease_mod.ProviderAdmissionError,
            match="Cannot release active provider session",
        ):
            lease_mod.release("myrepo-1")

    assert lease_mod.release("myrepo-1") is True


def test_session_admission_rejects_changed_lease_assignment(fleet):
    lease_mod.borrow(fleet, "effort-a", container="myrepo-1")

    with pytest.raises(
        lease_mod.ProviderAdmissionError,
        match="lease assignment changed",
    ):
        with lease_mod.session_admission(
            "myrepo-1",
            expected_assignment={
                "kind": "lease",
                "effort": "effort-b",
                "acquired_at": 1.0,
            },
        ):
            pass


def test_expired_lease_cannot_be_reassigned_during_session_admission(fleet):
    lease = lease_mod.borrow(fleet, "effort-a", container="myrepo-1")
    assignment = {
        "kind": "lease",
        "effort": lease.effort,
        "acquired_at": lease.acquired_at,
    }

    with lease_mod.session_admission(
        "myrepo-1",
        expected_assignment=assignment,
    ):
        with pytest.raises(
            lease_mod.ProviderAdmissionError,
            match="active provider session",
        ):
            lease_mod.borrow(
                fleet,
                "effort-b",
                container="myrepo-1",
                ttl=-1,
            )


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


def test_borrow_reclaims_definitively_dead_local_holder(fleet, monkeypatch):
    _seed_lease(pid=123)
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda pid: False if pid == 123 else True,
    )

    lease = lease_mod.borrow(fleet, "next-effort")

    assert lease.effort == "next-effort"
    assert lease.reclaim_reason == "dead-local-holder-pid"
    assert lease.reclaimed_from_effort == "previous-effort"
    assert lease.reclaimed_from_pid == 123
    assert lease.reclaimed_from_environment == lease_mod._this_environment()
    assert lease.reclaimed_at is not None
    stored = json.loads(lease_mod.LEASE_FILE.read_text(encoding="utf-8"))
    assert stored["myrepo-1"]["reclaim_reason"] == "dead-local-holder-pid"


def test_borrow_preserves_live_local_holder(fleet, monkeypatch):
    _seed_lease(pid=123)
    monkeypatch.setattr(lease_mod, "pid_alive", lambda _pid: True)

    with pytest.raises(RuntimeError, match="previous-effort"):
        lease_mod.borrow(fleet, "next-effort", container="myrepo-1")

    assert lease_mod.get_lease("myrepo-1").effort == "previous-effort"


def test_borrow_never_probes_or_reclaims_remote_holder(fleet, monkeypatch):
    _seed_lease(pid=123, host="remote-host")
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("must not inspect a remote PID")
        ),
    )

    with pytest.raises(RuntimeError, match="remote-host"):
        lease_mod.borrow(fleet, "next-effort", container="myrepo-1")

    assert lease_mod.get_lease("myrepo-1").effort == "previous-effort"


def test_borrow_never_probes_or_reclaims_cross_environment_holder(
    fleet,
    monkeypatch,
):
    other_environment = (
        "wsl" if lease_mod._this_environment() != "wsl" else "windows"
    )
    _seed_lease(pid=123, environment=other_environment)
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("must not inspect another environment's PID")
        ),
    )

    with pytest.raises(RuntimeError, match="previous-effort"):
        lease_mod.borrow(fleet, "next-effort", container="myrepo-1")

    assert lease_mod.get_lease("myrepo-1").environment == other_environment


def test_borrow_keeps_legacy_lease_without_environment_until_ttl(
    fleet,
    monkeypatch,
):
    now = time.time()
    lease_mod.LEASE_FILE.write_text(
        json.dumps(
            {
                "myrepo-1": {
                    "container": "myrepo-1",
                    "effort": "previous-effort",
                    "pid": 123,
                    "host": lease_mod._this_host(),
                    "acquired_at": now - 60,
                    "heartbeat_at": now,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda _pid: (_ for _ in ()).throw(
            AssertionError("must not inspect a legacy lease PID")
        ),
    )

    with pytest.raises(RuntimeError, match="previous-effort"):
        lease_mod.borrow(fleet, "next-effort", container="myrepo-1")

    lease = lease_mod.borrow(
        fleet,
        "next-effort",
        container="myrepo-1",
        ttl=-1,
    )
    assert lease.reclaim_reason is None


def test_borrow_keeps_unknown_local_liveness_until_ttl(fleet, monkeypatch):
    _seed_lease(pid=123)
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda _pid: (_ for _ in ()).throw(OSError("probe unavailable")),
    )

    with pytest.raises(RuntimeError, match="previous-effort"):
        lease_mod.borrow(fleet, "next-effort", container="myrepo-1")

    lease = lease_mod.borrow(
        fleet,
        "next-effort",
        container="myrepo-1",
        ttl=-1,
    )
    assert lease.reclaim_reason is None


def test_dead_local_holder_is_preserved_by_provider_deploy_hold(
    fleet,
    monkeypatch,
):
    _seed_lease(pid=123)
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda pid: False if pid == 123 else True,
    )

    with lease_mod.deploy_hold("myrepo-1", "recreate"):
        with pytest.raises(
            lease_mod.ProviderAdmissionError,
            match="provider recreate is in progress",
        ):
            lease_mod.borrow(
                fleet,
                "next-effort",
                container="myrepo-1",
            )

    assert lease_mod.get_lease("myrepo-1").effort == "previous-effort"


def test_dead_local_holder_is_preserved_by_active_session(
    fleet,
    monkeypatch,
):
    _seed_lease(pid=123)
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda pid: False if pid == 123 else True,
    )

    with lease_mod.session_admission("myrepo-1"):
        with pytest.raises(
            lease_mod.ProviderAdmissionError,
            match="active provider session",
        ):
            lease_mod.borrow(
                fleet,
                "next-effort",
                container="myrepo-1",
            )

    assert lease_mod.get_lease("myrepo-1").effort == "previous-effort"


def test_concurrent_borrowers_have_one_dead_holder_reclaim_winner(
    fleet,
    monkeypatch,
):
    _seed_lease(pid=123)
    monkeypatch.setattr(
        lease_mod,
        "pid_alive",
        lambda pid: False if pid == 123 else True,
    )
    barrier = threading.Barrier(3)
    successes = []
    conflicts = []

    def acquire(effort):
        barrier.wait()
        try:
            successes.append(
                lease_mod.borrow(
                    fleet,
                    effort,
                    container="myrepo-1",
                )
            )
        except RuntimeError as exc:
            conflicts.append(str(exc))

    workers = [
        threading.Thread(target=acquire, args=(effort,))
        for effort in ("effort-a", "effort-b")
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2)

    assert all(not worker.is_alive() for worker in workers)
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert successes[0].reclaim_reason == "dead-local-holder-pid"
    assert lease_mod.get_lease("myrepo-1").effort == successes[0].effort


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
        max_lifetime=60,
    ) as hold:
        lease_mod.mark_deploy_hold_uncertain("myrepo-1", hold.token)

    assert lease_mod.get_deploy_hold("myrepo-1") is not None
    records = json.loads(
        lease_mod._DEPLOY_HOLDS_FILE.read_text(encoding="utf-8")
    )
    records["myrepo-1"]["expires_at"] = time.time() - 1
    lease_mod._DEPLOY_HOLDS_FILE.write_text(
        json.dumps(records),
        encoding="utf-8",
    )
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


def test_coordination_json_writes_are_owner_only_under_open_umask(
    fleet,
    monkeypatch,
):
    now = time.time()
    fsync_calls = []
    monkeypatch.setattr(
        private_state,
        "fsync_directory",
        lambda path: fsync_calls.append(path),
    )
    hold = lease_mod.DeployHold(
        container="myrepo-1",
        operation="recreate",
        token="record-id",  # noqa: S106
        pid=123,
        host=lease_mod._this_host(),
        environment=lease_mod._this_environment(),
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now + 60,
    )
    previous = os.umask(0)
    try:
        lease_mod._write_records(
            lease_mod._DEPLOY_HOLDS_FILE,
            {"myrepo-1": hold},
        )
        lease_mod._write_leases(
            {
                "myrepo-1": lease_mod.Lease(
                    container="myrepo-1",
                    effort="example",
                    pid=123,
                    host=lease_mod._this_host(),
                    acquired_at=now,
                    heartbeat_at=now,
                )
            }
        )
    finally:
        os.umask(previous)

    if os.name != "nt":
        assert stat.S_IMODE(
            lease_mod._DEPLOY_HOLDS_FILE.stat().st_mode
        ) == 0o600
        assert stat.S_IMODE(lease_mod.LEASE_FILE.stat().st_mode) == 0o600
    assert fsync_calls == [
        lease_mod._DEPLOY_HOLDS_FILE.parent,
        lease_mod.LEASE_FILE.parent,
    ]
    assert not list(lease_mod._DEPLOY_HOLDS_FILE.parent.glob(".*.tmp"))


def test_deploy_hold_cleanup_corruption_preserves_original_exception(
    fleet,
    caplog,
):
    with pytest.raises(ValueError, match="original failure"):
        with lease_mod.deploy_hold("myrepo-1", "remove"):
            lease_mod._DEPLOY_HOLDS_FILE.write_text(
                "{corrupt",
                encoding="utf-8",
            )
            raise ValueError("original failure")

    assert "leaving it fail-closed" in caplog.text
    assert lease_mod._DEPLOY_HOLDS_FILE.exists()


def test_session_admission_cleanup_corruption_preserves_return_value(
    fleet,
    caplog,
):
    def operation():
        with lease_mod.session_admission("myrepo-1"):
            lease_mod._SESSION_ADMISSIONS_FILE.write_text(
                "{corrupt",
                encoding="utf-8",
            )
            return "completed"

    assert operation() == "completed"
    assert "leaving it fail-closed" in caplog.text
    assert lease_mod._SESSION_ADMISSIONS_FILE.exists()
