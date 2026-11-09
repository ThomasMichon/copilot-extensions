from __future__ import annotations

from concurrent.futures import Future
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_supervisor_source import VERSION, _incumbent


@pytest.mark.parametrize("checkpoint", ["withdrawing", "pre-release"])
@pytest.mark.parametrize("failure", ["missing", "unreadable"])
@pytest.mark.parametrize("custody", ["settled", "held"])
def test_unreadable_withdrawal_profile_uses_owned_settlement_before_admission_rollback(
    tmp_path, monkeypatch, checkpoint, failure, custody,
):
    from agent_procutil import daemon_instances
    from agent_dispatch.supervisor_profile import MasterProfile

    daemon, source = _incumbent(tmp_path, monkeypatch)
    profile = tmp_path / "supervisor.env"
    profile.write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\n")
    daemon._master_profile = MasterProfile(profile, hashlib.sha256(profile.read_bytes()).hexdigest())
    receipt = SimpleNamespace(instance_id="a" * 32, registry_root=tmp_path / "registry")
    child = SimpleNamespace(instance_receipt=receipt, poll=lambda: 1 if custody == "settled" else None)
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": checkpoint if checkpoint == "withdrawing" else "preparing", "child": child,
    }
    daemon._source_handoff = state
    unreadable = [False]
    original_read = Path.read_bytes

    def read(path):
        if path == profile and unreadable[0]:
            raise PermissionError("fixture profile read refused")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)

    def withdraw():
        daemon._pool_metadata_closed.set()
        if failure == "missing":
            profile.unlink()
        else:
            unreadable[0] = True
        return True

    if checkpoint == "withdrawing":
        withdraw()
    monkeypatch.setattr(daemon, "shutdown", withdraw)
    monkeypatch.setattr(source, "read_prepared", lambda *_args, **_kwargs: {"instance_id": "d" * 32})
    monkeypatch.setattr(source, "spawn_candidate", lambda *_args, **_kwargs: pytest.fail("unconfirmed effect replay"))
    cleaned = []

    def cleanup(actual, *, terminate):
        assert actual is receipt and terminate is False
        cleaned.append(actual)
        return SimpleNamespace(status=custody)

    monkeypatch.setattr(daemon_instances, "cleanup_instance", cleanup)
    assert daemon._maybe_self_update() is False
    assert cleaned == [receipt]
    assert not daemon._lock.released
    if custody == "settled":
        assert daemon._source_handoff is None
        assert not daemon._pool_metadata_closed.is_set()
    else:
        assert daemon._source_handoff is state
        assert daemon._pool_metadata_closed.is_set()
        assert daemon._maybe_self_update() is False
        assert cleaned == [receipt, receipt]
        assert daemon._source_handoff is state


@pytest.mark.parametrize("checkpoint", ["early", "pre-release"])
@pytest.mark.parametrize("control", ["action", "governance"])
def test_update_hold_restores_original_admission_at_both_checks(tmp_path, monkeypatch, checkpoint, control):
    from agent_dispatch import action_controls, supervisor_retirement
    from agent_procutil import daemon_instances

    daemon, source = _incumbent(tmp_path, monkeypatch)
    child = SimpleNamespace(instance_receipt=object(), poll=lambda: 1)
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": "withdrawing", "child": child,
    }
    daemon._source_handoff = state
    if checkpoint == "early":
        daemon._pool_metadata_closed.set()
    else:
        state["phase"] = "preparing"
    reads = []

    def inhibited(*_args, **_kwargs):
        reads.append(True)
        return control == "action" and (checkpoint == "early" or len(reads) > 1)

    def governance(stage):
        if control == "governance" and (
            checkpoint == "early" or stage == "source-pre-release"
        ):
            return {"status": "blocked"}
        return None

    def shutdown():
        daemon._pool_metadata_closed.set()
        return True

    monkeypatch.setattr(action_controls, "action_inhibited", inhibited)
    monkeypatch.setattr(daemon, "_recheck_governance", governance)
    monkeypatch.setattr(source, "read_prepared", lambda *_args, **_kwargs: {"instance_id": "d" * 32})
    monkeypatch.setattr(daemon, "shutdown", shutdown)
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: SimpleNamespace(status="settled"))
    monkeypatch.setattr(supervisor_retirement, "_retire_owned_workers", lambda *_: True)
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is None
    assert not daemon._pool_metadata_closed.is_set()
    assert not daemon._lock.released


def test_update_hold_waits_for_local_work_without_losing_same_owner(tmp_path, monkeypatch):
    from agent_dispatch import action_controls
    from agent_procutil import daemon_instances

    daemon, _ = _incumbent(tmp_path, monkeypatch)
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": "withdrawing", "child": SimpleNamespace(instance_receipt=object(), poll=lambda: 1),
    }
    daemon._source_handoff = state
    daemon._pool_metadata_closed.set()
    pending = Future()
    daemon._probe_inflight.add(pending)
    monkeypatch.setattr(action_controls, "action_inhibited", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: SimpleNamespace(status="settled"))
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is state and daemon._pool_metadata_closed.is_set()
    pending.set_result(None)
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is None and not daemon._pool_metadata_closed.is_set()
    assert not daemon._lock.released


def test_update_hold_restores_supervision_without_terminating_a_discovered_peer(tmp_path, monkeypatch):
    from agent_dispatch import action_controls
    from agent_procutil import _daemon_instance_identity, daemon_instances

    daemon, _ = _incumbent(tmp_path, monkeypatch)
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": "withdrawing", "prepared": {"generation": {"pid": 901}, "birth": "peer"},
    }
    daemon._source_handoff = state
    daemon._pool_metadata_closed.set()
    monkeypatch.setattr(action_controls, "action_inhibited", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda *_: "matching")
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: pytest.fail("foreign cleanup"))
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is state and state["phase"] == "preparing"
    assert not daemon._pool_metadata_closed.is_set() and not daemon._lock.released


@pytest.mark.parametrize("target", ["candidate", "supervisor", "candidate-after-supervisor"])
@pytest.mark.parametrize("refusal", ["reused", "unknown", "child-pid", "foreign-root"])
def test_owned_cancellation_refuses_unverified_targets_without_stopping_anyone(tmp_path, monkeypatch, target, refusal):
    from agent_procutil import _daemon_instance_identity, daemon_instances

    daemon, source = _incumbent(tmp_path, monkeypatch)
    receipt = SimpleNamespace(registry_root=tmp_path / "registry", instance_id="a" * 32)
    child = SimpleNamespace(pid=501, instance_receipt=receipt, poll=lambda: None)
    row = {
        "instance_id": receipt.instance_id, "runtime_root": str(tmp_path),
        "state": "running", "pid": "501", "birth": "candidate-birth",
    }
    if target != "candidate":
        row.update(supervisor_pid="502", supervisor_birth="supervisor-birth")
    if refusal == "child-pid":
        row["pid"] = "999"
    if refusal == "foreign-root":
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        row["runtime_root"] = str(foreign)
    unsafe_pid = 502 if target == "supervisor" else 501
    stops = []
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: SimpleNamespace(status="live"))
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: [row])
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda pid, _birth: (
        refusal if pid == unsafe_pid and refusal in {"reused", "unknown"} else "matching"
    ))
    monkeypatch.setattr(_daemon_instance_identity, "stop_identity", lambda *args, **kwargs: stops.append(args) or True)
    assert source._cancel_owned_candidate(child, tmp_path) is False
    assert stops == []
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": "withdrawing", "child": child, "retargeting": True,
    }
    daemon._source_handoff = state
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is state and not daemon._lock.released
    assert stops == []


@pytest.mark.parametrize("changed", [False, True])
def test_unknown_launch_receipt_recovers_only_after_native_exit_and_sdk_settlement(tmp_path, monkeypatch, changed):
    from agent_procutil import DaemonSpawnError, _daemon_instance_identity, daemon_instances

    daemon, source = _incumbent(tmp_path, monkeypatch)
    receipt = SimpleNamespace(registry_root=tmp_path / "registry", instance_id="a" * 32)
    error = DaemonSpawnError("lost reply", cleanup_confirmed=False, process_created=True,
                             process_identity=(501, "birth"))
    error.instance_receipt = receipt
    state = {
        "root": tmp_path, "version": VERSION, "target": Path("incoming"),
        "phase": "unconfirmed", "child": None, "spawn_error": error,
    }
    daemon._source_handoff = state
    daemon.self_update_cooldown = 10
    daemon.self_update_poll_interval = 0
    clock = [100.0]
    daemon.clock = lambda: clock[0]
    row = {
        "instance_id": receipt.instance_id, "runtime_root": str(tmp_path),
        "state": "running", "pid": "501", "birth": "birth",
    }
    observation = ["matching"]
    calls = []
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: [row])
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda *_: observation[0])
    monkeypatch.setattr(_daemon_instance_identity, "stop_identity", lambda *_args, **_kwargs: pytest.fail("unknown receipt termination"))

    def cleanup(original, *, terminate):
        assert original is receipt and terminate is False
        calls.append("settled")
        return SimpleNamespace(status="settled")

    monkeypatch.setattr(daemon_instances, "cleanup_instance", cleanup)
    spawned = []
    monkeypatch.setattr(source, "spawn_candidate", lambda *_args, **_kwargs: (
        spawned.append(True) or SimpleNamespace(instance_receipt=object(), poll=lambda: None)
    ))
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is state and not calls
    if changed:
        (tmp_path / "current-version").write_text(VERSION.replace("a" * 40, "f" * 40), encoding="utf-8")
    observation[0] = "dead"
    row["state"] = "finished"
    assert daemon._maybe_self_update() is False
    assert calls == ["settled"] and daemon._source_handoff is None
    assert daemon._self_update_last_spawn == 100
    assert daemon._maybe_self_update() is False and not spawned
    clock[0] = 111
    assert daemon._maybe_self_update() is False and spawned == [True]
    assert not daemon._lock.released


@pytest.mark.parametrize("reason", ["unknown", "reused", "pending", "foreign-root", "sdk-held", "sdk-missing", "identity-mismatch"])
def test_unknown_launch_receipt_keeps_unconfirmed_or_foreign_effects_held(tmp_path, monkeypatch, reason):
    from agent_procutil import DaemonSpawnError, _daemon_instance_identity, daemon_instances

    daemon, _ = _incumbent(tmp_path, monkeypatch)
    receipt = SimpleNamespace(registry_root=tmp_path / "registry", instance_id="a" * 32)
    error = DaemonSpawnError("lost reply", cleanup_confirmed=False, process_created=True,
                             process_identity=(501, "birth"))
    error.instance_receipt = receipt
    state = {"root": tmp_path, "version": VERSION, "target": Path("incoming"),
             "phase": "unconfirmed", "child": None, "spawn_error": error}
    daemon._source_handoff = state
    row = {"instance_id": receipt.instance_id, "runtime_root": str(tmp_path),
           "state": "reserved" if reason == "pending" else "finished", "pid": "501", "birth": "birth"}
    if reason == "foreign-root":
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        row["runtime_root"] = str(foreign)
    if reason == "identity-mismatch":
        row["birth"] = "other"
    calls = []
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: [row])
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda *_: (
        reason if reason in {"unknown", "reused"} else "dead"
    ))
    monkeypatch.setattr(_daemon_instance_identity, "stop_identity", lambda *_args, **_kwargs: pytest.fail("unverified stop"))
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: (
        calls.append(True) or SimpleNamespace(status="missing" if reason == "sdk-missing" else "held")
    ))
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is state and not daemon._lock.released
    assert bool(calls) is (reason in {"sdk-held", "sdk-missing"})


@pytest.mark.parametrize("cached,status,cleared", [
    (False, "settled", False), (True, "missing", False), (True, "settled", True),
])
def test_missing_launch_record_requires_prior_binding_and_sdk_terminal_proof(
    tmp_path, monkeypatch, cached, status, cleared,
):
    from agent_procutil import DaemonSpawnError, _daemon_instance_identity, daemon_instances

    daemon, _ = _incumbent(tmp_path, monkeypatch)
    error = DaemonSpawnError("lost reply", cleanup_confirmed=False, process_created=True,
                             process_identity=(501, "birth"))
    error.instance_receipt = SimpleNamespace(registry_root=tmp_path / "registry", instance_id="a" * 32)
    state = {"root": tmp_path, "version": VERSION, "target": Path("incoming"),
             "phase": "unconfirmed", "child": None, "spawn_error": error}
    if cached:
        state["spawn_binding"] = (501, "birth")
    daemon._source_handoff = state
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: [])
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda *_: "dead")
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: SimpleNamespace(status=status))
    assert daemon._maybe_self_update() is False
    assert (daemon._source_handoff is None) is cleared
    assert not daemon._lock.released
