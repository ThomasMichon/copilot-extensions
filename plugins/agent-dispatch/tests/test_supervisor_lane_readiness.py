from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest


@pytest.fixture
def native(monkeypatch, tmp_path):
    from agent_dispatch import companion, config, self_update, supervisor_lane_status as lane
    from agent_procutil import _daemon_instance_identity as identity, daemon_instances

    root = tmp_path
    monkeypatch.setattr(config, "run_dir", lambda: root / "run")
    monkeypatch.setattr(lane, "require_selected_runtime", lambda _: None)
    monkeypatch.setattr(self_update, "read_current_version", lambda _: lane.__version__)
    monkeypatch.setattr(identity, "birth_identity", lambda _: "fixture-birth")
    monkeypatch.setattr(identity, "identity_state", lambda *_: "matching")
    monkeypatch.setattr(companion, "process_start_token", lambda _: "fixture-start")
    receipt = SimpleNamespace(instance_id="a" * 32, registry_root=root / "registry")
    records = []
    calls = []

    def artifact(path):
        device, inode = identity.path_identity(path)
        calls.append(path)
        records[:] = [{
            "instance_id": receipt.instance_id, "pid": str(os.getpid()),
            "birth": "fixture-birth", "state": "running", "runtime_root": str(root),
            "artifact_count": "1", "artifact_0_path": str(path),
            "artifact_0_device": device, "artifact_0_inode": inode,
        }]

    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: records)
    guard = SimpleNamespace(receipt=receipt, add_artifact=artifact)
    profile = root / "supervisor.env"
    profile.write_text("AGENT_DISPATCH_SUPERVISE_LABELS=fixture\n")
    arguments = ["supervise", "--all-repos", "--label", "fixture"]
    return lane, root, profile, arguments, guard, calls, records


def test_native_life_or_started_cycle_is_not_completed_readiness(native):
    lane, root, profile, argv, guard, calls, _ = native
    with lane.LaneStatus(root, profile, argv, guard) as status:
        assert lane.observation(root, profile, argv)["status"] == "starting"
        status.start()
        assert lane.observation(root, profile, argv)["status"] == "starting"
        status.complete()
        assert lane.observation(root, profile, argv)["status"] == "ready"
        for _ in range(40):
            status.start()
            status.complete()
        assert len(calls) == 1, "native artifact custody must not grow each cycle"


def test_repeated_clean_incarnations_reclaim_only_their_own_status(native):
    lane, root, profile, argv, guard, _, _ = native
    for number in range(130):
        guard.receipt.instance_id = f"{number:032x}"
        with lane.LaneStatus(root, profile, argv, guard) as status:
            status.start()
            status.complete()
            assert lane.observation(root, profile, argv)["status"] == "ready"
        assert not status.path.exists()
    assert not list((root / "run").glob("lane-*.status.json"))


def test_clean_exit_refuses_replaced_status_artifact_but_releases_lease(native):
    lane, _, profile, argv, guard, _, _ = native
    from agent_dispatch.single_instance import is_locked

    status = lane.LaneStatus(profile.parent, profile, argv, guard)
    status.__enter__()
    status.start()
    status.path.rename(status.path.with_suffix(".retained"))
    status.path.write_text("foreign replacement")
    with pytest.raises(OSError):
        status.__exit__()
    assert status.path.read_text() == "foreign replacement"
    assert not is_locked(status.lock.lock_path)


def test_dead_crash_records_are_pruned_before_profile_and_installation_bounds(native, monkeypatch):
    lane, root, profile, argv, guard, _, records = native
    from agent_dispatch import supervisor_processes
    from agent_procutil import _daemon_instance_identity as identity
    from agent_dispatch.supervisor_status_io import runtime_status_path

    monkeypatch.setattr(identity, "identity_state", lambda pid, _: "matching" if pid == os.getpid() else "dead")
    with lane.LaneStatus(root, profile, argv, guard) as current:
        current.start()
        current.complete()
        original = json.loads(current.path.read_text())
        foreign = None
        for number in range(270):
            payload = json.loads(json.dumps(original))
            incarnation = f"{number:032x}"
            other = root / "other.env" if number >= 135 else profile
            path = runtime_status_path(
                lane.profile_scope(other) + ":instance:" + incarnation, directory=root / "run",
            )
            payload["generation"]["pid"] = os.getpid() + 100000 + number
            payload["lane_readiness"].update(instance_id=incarnation, profile=str(other))
            path.write_text(json.dumps(payload))
            device, inode = identity.path_identity(path)
            records.append({
                "instance_id": incarnation, "state": "running", "runtime_root": str(root),
                "pid": str(payload["generation"]["pid"]), "birth": "fixture-birth",
                "artifact_count": "1", "artifact_0_path": str(path),
                "artifact_0_device": device, "artifact_0_inode": inode,
            })
            if number == 0:
                # OS death permits filtering, not deleting a foreign owner's artifact.
                records[-1]["runtime_root"] = str(root.parent)
                foreign = path
        assert lane.observation(root, profile, argv)["status"] == "ready"
        monkeypatch.setattr(lane, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(supervisor_processes, "iter_windows_processes", lambda: [SimpleNamespace(pid=os.getpid())])
        monkeypatch.setattr(supervisor_processes, "_is_supervisor_runtime", lambda *_: True)
        lane.require_observed_incumbents(root)
        remaining = set((root / "run").glob("lane-*.status.json"))
        assert {current.path, foreign} <= remaining
        assert len(remaining) < 270
        monkeypatch.setattr(supervisor_processes, "iter_windows_processes", lambda: [
            SimpleNamespace(pid=os.getpid()), SimpleNamespace(pid=os.getpid() + 1),
        ])
        with pytest.raises(RuntimeError, match="no duplicate was started"):
            lane.require_observed_incumbents(root)


@pytest.mark.parametrize("change", ["settings", "version", "profile-generation", "birth", "custody", "nonfinite", "no-cycle"])
def test_readiness_binds_profile_settings_cycle_and_native_generation(native, change, monkeypatch):
    lane, root, profile, argv, guard, _, records = native
    with lane.LaneStatus(root, profile, argv, guard) as status:
        status.start()
        status.complete()
        payload = json.loads(status.path.read_text())
        if change == "settings":
            payload["lane_readiness"]["settings_identity"] = "other"
        elif change == "version":
            payload["runtime_version"] = "old-runtime"
        elif change == "profile-generation":
            profile.write_text("AGENT_DISPATCH_SUPERVISE_LABELS=changed\n")
        elif change == "birth":
            from agent_procutil import _daemon_instance_identity as identity
            monkeypatch.setattr(identity, "identity_state", lambda *_: "unknown")
        elif change == "custody":
            records[0]["pid"] = "123"
        elif change == "nonfinite":
            payload["updated_at"] = float("nan")
        else:
            payload["cycle_id"] = 0
        status.path.write_text(json.dumps(payload))
        if change in {"birth", "custody"}:
            with pytest.raises(RuntimeError, match="native"):
                lane.observation(root, profile, argv)
        else:
            assert lane.observation(root, profile, argv)["status"] != "ready"


def test_supersession_leaves_classic_loop_at_boundary_without_worker_retirement(native, monkeypatch):
    lane, root, profile, argv, guard, _, _ = native
    from agent_dispatch import self_update
    monkeypatch.setattr(self_update, "stale_target", lambda *_: root / "incoming-python")
    with lane.LaneStatus(root, profile, argv, guard) as status:
        with pytest.raises(lane.LaneSuperseded):
            status.start()
        assert not status.path.exists()


def test_failed_classic_cycle_never_publishes_completion(monkeypatch):
    from agent_dispatch import diagnostic_logs
    from agent_dispatch.supervisor import Supervisor

    monkeypatch.setattr(diagnostic_logs, "maintain_file_logging", lambda: None)
    supervisor = Supervisor.__new__(Supervisor)
    supervisor.event_wake = None
    supervisor._recheck_governance = lambda _: None
    supervisor.poll_once = lambda: (_ for _ in ()).throw(RuntimeError("fixture failed cycle"))
    supervisor.wait_for_turn_end = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    starts, completions = [], []
    supervisor.serve(
        interval=0, on_cycle_start=lambda: starts.append(1),
        on_cycle=lambda _: completions.append(1),
    )
    assert starts == [1]
    assert completions == []


def test_unavailable_cycle_observation_never_clears_a_hold_to_spawn(native, monkeypatch):
    lane, root, profile, argv, _, _, _ = native
    from agent_dispatch import action_controls, procutil

    monkeypatch.setattr(action_controls, "action_decision", lambda *_, **__: (
        SimpleNamespace(inhibited=False, error=None)
    ))
    monkeypatch.setattr(lane, "observation", lambda *_, **__: (
        (_ for _ in ()).throw(RuntimeError("classic lane status is unavailable: status-json-invalid"))
    ))
    monkeypatch.setattr(procutil, "spawn_dispatch_daemon", lambda *_, **__: (
        pytest.fail("unavailable observation is not permission to spawn")
    ))
    with pytest.raises(RuntimeError, match="readiness remains unconfirmed"):
        lane.ensure(root, profile, argv, timeout=0)


@pytest.mark.parametrize("identity", [None, "fixture-settings"])
def test_optional_readiness_extension_preserves_existing_master_status(native, identity):
    from agent_dispatch import supervise_cli

    supervise_cli._write_supervisor_cycle_start(
        "master-fixture", 1, 1.0, authority_target="local",
        readiness_identity=identity,
    )
    supervise_cli._write_supervisor_runtime_status(
        "master-fixture", SimpleNamespace(completed_at=2.0, running=[], backing_off=[]),
    )
    payload, error = supervise_cli._read_supervisor_runtime_status("master-fixture")
    assert error is None
    assert payload["cycle_id"] == 1
    assert payload["cycle_started_at"] == 1.0
    assert payload["updated_at"] == 2.0
    assert payload["generation"]["start_token"] == "fixture-start"
    assert payload["owner"]["authority_target"] == "local"
    from agent_dispatch.supervisor_health import supervisor_progress_observation
    observation = supervisor_progress_observation(
        payload, token_source=lambda _: "fixture-start", now=2.1,
    )
    assert observation["state"] == "progressing"
    assert observation["recovery_permitted"] is False
    if identity is None:
        assert "lane_readiness" not in payload
    else:
        assert payload["lane_readiness"]["settings_identity"] == identity


def test_downlevel_master_status_is_not_claimed_as_new_readiness(native, monkeypatch):
    lane, root, _, _, _, _, records = native
    from agent_dispatch import config, supervisor_registration
    from agent_dispatch.single_instance import SingleInstance, lock_path_for
    from agent_dispatch.supervisor_status_io import runtime_status_path
    from agent_procutil import daemon_instances

    monkeypatch.setattr(supervisor_registration, "registration_scope", lambda _: ("fixture", "default"))
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda *args: records)
    records[:] = [{
        "state": "running", "pid": str(os.getpid()), "birth": "fixture-birth",
        "runtime_root": str(root),
    }]
    scope = "supervisor:fixture:default"
    lock = SingleInstance(lock_path_for(config.run_dir(), scope))
    assert lock.acquire()
    try:
        runtime_status_path(scope).write_text(json.dumps({
            "runtime_version": lane.__version__,
            "generation": {"pid": os.getpid(), "start_token": "fixture-start"},
            "cycle_id": 1, "cycle_started_at": 1.0, "updated_at": 2.0,
        }))
        with pytest.raises(RuntimeError, match="support is unavailable"):
            lane.master_observation(root, ["supervise", "serve"])
    finally:
        lock.release()


def test_unmarked_master_restart_does_not_inherit_predecessor_settings(native, monkeypatch):
    lane, root, _, _, _, _, records = native
    from agent_dispatch import config, supervise_cli, supervisor_registration
    from agent_dispatch.single_instance import SingleInstance, lock_path_for
    from agent_dispatch.supervisor_status_io import runtime_status_path
    from agent_procutil import daemon_instances

    monkeypatch.setattr(supervisor_registration, "registration_scope", lambda _: ("fixture", "default"))
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda *args: records)
    records[:] = [{
        "state": "running", "pid": str(os.getpid()), "birth": "fixture-birth",
        "runtime_root": str(root),
    }]
    scope = "supervisor:fixture:default"
    lock = SingleInstance(lock_path_for(config.run_dir(), scope))
    assert lock.acquire()
    try:
        path = runtime_status_path(scope)
        path.write_text(json.dumps({
            "generation": {"pid": os.getpid() + 100000, "start_token": "predecessor"},
            "lane_readiness": {"capability": lane.CAPABILITY, "settings_identity": "old-settings"},
            "cycle_id": 99, "cycle_started_at": 1.0, "updated_at": 2.0,
        }))
        supervise_cli._write_supervisor_cycle_start(scope, 1, 3.0)
        supervise_cli._write_supervisor_runtime_status(
            scope, SimpleNamespace(completed_at=4.0, running=[], backing_off=[]),
        )
        current = json.loads(path.read_text())
        assert current["generation"]["pid"] == os.getpid()
        assert "lane_readiness" not in current
        with pytest.raises(RuntimeError, match="support is unavailable"):
            lane.master_observation(root, ["supervise", "serve", "--interval", "17"])
    finally:
        lock.release()


def test_corrupt_predecessor_does_not_poison_valid_current_discovery(native, monkeypatch):
    lane, root, profile, argv, guard, _, _ = native
    from agent_dispatch import supervisor_processes

    with lane.LaneStatus(root, profile, argv, guard) as status:
        status.start()
        status.complete()
        status.path.with_name(status.path.name.replace("a" * 32, "b" * 32)).write_text("{")
        assert lane.observation(root, profile, argv)["status"] == "ready"
        monkeypatch.setattr(lane, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(supervisor_processes, "iter_windows_processes", lambda: [
            SimpleNamespace(pid=os.getpid()),
        ])
        monkeypatch.setattr(supervisor_processes, "_is_supervisor_runtime", lambda *_: True)
        lane.require_observed_incumbents(root)
        monkeypatch.setattr(supervisor_processes, "iter_windows_processes", lambda: [
            SimpleNamespace(pid=os.getpid()), SimpleNamespace(pid=os.getpid() + 100000),
        ])
        with pytest.raises(RuntimeError, match="no supported profile readiness"):
            lane.require_observed_incumbents(root)


def test_corrupt_current_record_keeps_held_lease_unknown_not_absent(native, monkeypatch):
    lane, root, profile, argv, guard, _, _ = native
    from agent_dispatch import supervisor_processes

    with lane.LaneStatus(root, profile, argv, guard) as status:
        status.start()
        historical = json.loads(status.path.read_text())
        historical["generation"] = {"pid": os.getpid() + 100000, "start_token": "old"}
        status.path.with_name(status.path.name.replace("a" * 32, "b" * 32)).write_text(json.dumps(historical))
        status.path.write_text("{")
        result = lane.observation(root, profile, argv)
        assert result["status"] == "starting"
        assert not result.get("native_observed")
        monkeypatch.setattr(lane, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(supervisor_processes, "iter_windows_processes", lambda: [
            SimpleNamespace(pid=os.getpid()),
        ])
        monkeypatch.setattr(supervisor_processes, "_is_supervisor_runtime", lambda *_: True)
        with pytest.raises(RuntimeError, match="no supported profile readiness"):
            lane.require_observed_incumbents(root)


@pytest.mark.parametrize("ready", [False, True])
def test_nonwindows_classic_refuses_unprovable_absence_but_reuses_observed_owner(native, monkeypatch, ready):
    lane, root, profile, argv, _, _, _ = native
    from agent_dispatch import action_controls, procutil

    monkeypatch.setattr(lane, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(action_controls, "action_decision", lambda *_, **__: (
        SimpleNamespace(inhibited=False, error=None)
    ))
    monkeypatch.setattr(lane, "observation", lambda *_: (
        {"status": "ready", "pid": 123} if ready else None
    ))
    monkeypatch.setattr(procutil, "spawn_dispatch_daemon", lambda *_, **__: (
        pytest.fail("unobserved POSIX classic absence must not start a duplicate")
    ))
    if ready:
        assert lane.ensure(root, profile, argv, timeout=0)["pid"] == 123
    else:
        with pytest.raises(RuntimeError, match="discovery is unavailable"):
            lane.ensure(root, profile, argv, timeout=0)


def test_unobserved_classic_incumbent_prevents_duplicate_without_retirement(native, monkeypatch):
    lane, root, _, _, _, _, _ = native
    from agent_dispatch import supervisor_processes

    monkeypatch.setattr(lane, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(supervisor_processes, "iter_windows_processes", lambda: [SimpleNamespace(pid=123)])
    monkeypatch.setattr(supervisor_processes, "_is_supervisor_runtime", lambda *_: True)
    with pytest.raises(RuntimeError, match="no duplicate was started"):
        lane.require_observed_incumbents(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows startup policy")
@pytest.mark.parametrize("automatic,allowed,expected", [(True, True, False), (False, True, True), (False, False, False)])
def test_automatic_inhibition_is_separate_from_explicit_start_and_disabled_intent(
    native, monkeypatch, automatic, allowed, expected,
):
    from agent_dispatch import __main__ as cli, action_controls, config, installation_policy

    alive = []
    monkeypatch.setattr(config, "has_live_local_coordinator", lambda: bool(alive))
    monkeypatch.setattr(action_controls, "action_decision", lambda *_, **options: (
        SimpleNamespace(inhibited=options["automatic"], error=None)
    ))
    monkeypatch.setattr(installation_policy, "windows_policy", lambda *_, **__: {
        "start_coordinator": allowed,
    })
    monkeypatch.setattr(cli, "_spawn_coordinator_process", lambda: alive.append(True))
    assert cli._lazy_start_coordinator(timeout=1, automatic=automatic) is expected
    assert bool(alive) is expected
