from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_supervisor_source import VERSION, _incumbent
from tests.test_source_supervisor_startup import _sources


def test_owned_production_candidate_is_cancelled_before_cleanup_on_selection_flap(tmp_path, monkeypatch):
    from agent_procutil import _daemon_instance_identity, daemon_instances

    daemon, _ = _incumbent(tmp_path, monkeypatch)
    receipt = SimpleNamespace(registry_root=tmp_path / "registry", instance_id="a" * 32)
    child = SimpleNamespace(pid=501, instance_receipt=receipt, poll=lambda: None)
    daemon._source_handoff = {
        "root": tmp_path, "version": VERSION, "target": Path("candidate"),
        "phase": "withdrawing", "child": child, "retargeting": True,
    }
    stopped = []
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: (
        SimpleNamespace(status="settled" if stopped else "live")
    ))
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda _: [{
        "instance_id": receipt.instance_id, "runtime_root": str(tmp_path),
        "pid": "501", "birth": "owned-birth", "state": "running",
    }])
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda *_: "dead" if stopped else "matching")
    monkeypatch.setattr(_daemon_instance_identity, "stop_identity", lambda pid, birth, **_kwargs: (
        stopped.append((pid, birth)) or True
    ))
    assert daemon._maybe_self_update() is False
    assert stopped == [(501, "owned-birth")]
    assert daemon._source_handoff is None
    assert not daemon._lock.released


@pytest.mark.parametrize("owned", [True, False])
def test_candidate_exit_after_shutdown_restores_admission_with_unchanged_selection(tmp_path, monkeypatch, owned):
    from agent_procutil import _daemon_instance_identity, daemon_instances

    daemon, _ = _incumbent(tmp_path, monkeypatch)
    daemon._pool_metadata_closed.set()
    daemon._source_handoff = {
        "root": tmp_path, "version": VERSION, "target": Path("candidate"),
        "phase": "withdrawing",
        "prepared": {"generation": {"pid": 501}, "birth": "owned-birth"},
    }
    if owned:
        daemon._source_handoff["child"] = SimpleNamespace(instance_receipt=object(), poll=lambda: 1)
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: SimpleNamespace(status="settled"))
    monkeypatch.setattr(_daemon_instance_identity, "identity_state", lambda *_: "dead")
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is None
    assert not daemon._pool_metadata_closed.is_set()
    assert not daemon._lock.released


def test_confirmed_nonstart_retries_after_cooldown_without_unknown_hold(tmp_path, monkeypatch):
    from agent_procutil import DaemonSpawnError

    daemon, source = _incumbent(tmp_path, monkeypatch)
    clock = [100.0]
    daemon.clock = lambda: clock[0]
    daemon.self_update_cooldown = 10
    daemon.self_update_poll_interval = 1
    calls = []

    def refused(*_args, **_kwargs):
        calls.append("attempt")
        raise DaemonSpawnError("known nonstart", process_created=False, cleanup_confirmed=True)

    monkeypatch.setattr(source, "spawn_candidate", refused)
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff is None
    clock[0] = 101
    assert daemon._maybe_self_update() is False
    assert calls == ["attempt"]
    clock[0] = 111
    assert daemon._maybe_self_update() is False
    assert calls == ["attempt", "attempt"]


def test_nonprofile_source_uses_supported_ordinary_handoff(tmp_path, monkeypatch):
    daemon, source = _incumbent(tmp_path, monkeypatch)
    flat = VERSION.split(".profile.")[0]
    (tmp_path / "current-version").write_text(flat, encoding="utf-8")
    monkeypatch.setattr(source, "advance_handoff", lambda *_: pytest.fail("flat Source entered app receiver"))
    monkeypatch.setattr(daemon, "shutdown", lambda: True)
    spawned = []
    daemon._self_update_spawn = lambda *_: spawned.append(True)
    assert daemon._maybe_self_update() is True
    assert spawned == [True]


def test_profiled_application_requires_nested_target_layout(tmp_path):
    from agent_dispatch import supervisor_source

    slot = tmp_path / "versions" / VERSION
    assert supervisor_source.is_profiled_application(tmp_path, VERSION, slot / ".venv" / "Scripts" / "python.exe")
    assert not supervisor_source.is_profiled_application(tmp_path, VERSION, slot / "Scripts" / "python.exe")


@pytest.mark.parametrize("attested", [False, True])
def test_locked_master_settings_must_match_captured_legacy_source_mode(tmp_path, monkeypatch, attested):
    from agent_dispatch import companion, config, single_instance, source_start, supervisor_source, supervisor_status_io

    _sources(monkeypatch, declarations=[object()])
    (tmp_path / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_LABELS=fixture\n", encoding="utf-8",
    )
    plan = source_start.supervisor_plan(tmp_path)
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path / "control")
    monkeypatch.setattr(single_instance, "is_locked", lambda _: True)
    monkeypatch.setattr(single_instance, "read_holder_pid", lambda _: 41)
    monkeypatch.setattr(companion, "process_start_token", lambda _: "birth")
    monkeypatch.setattr(supervisor_status_io, "read_status", lambda _: ({
        "generation": {"pid": 41, "start_token": "birth"}, "runtime_version": source_start.__version__,
        "cycle_id": 1, "updated_at": 2, "cycle_started_at": 1,
    }, None))
    settings = supervisor_source.effective_settings(plan.args)
    settings["legacy_env"] = attested
    monkeypatch.setattr(supervisor_source, "read_prepared", lambda *_args, **_kwargs: {
        "generation": {"pid": 41}, "settings": settings,
    })
    if attested:
        assert source_start.ensure_supervisor(tmp_path, plan=plan) == {"status": "ready", "pid": 41}
    else:
        with pytest.raises(RuntimeError, match="settings"):
            source_start.ensure_supervisor(tmp_path, plan=plan)


def test_nonstart_with_unsettled_reservation_retains_cleanup_identity(tmp_path, monkeypatch):
    from agent_procutil import DaemonSpawnError, daemon_instances

    daemon, source = _incumbent(tmp_path, monkeypatch)
    error = DaemonSpawnError("reservation unconfirmed", process_created=False, cleanup_confirmed=True)
    error.instance_receipt = object()
    monkeypatch.setattr(daemon_instances, "cleanup_instance", lambda *_args, **_kwargs: SimpleNamespace(status="held"))

    def refused(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(source, "spawn_candidate", refused)
    assert daemon._maybe_self_update() is False
    assert daemon._source_handoff["spawn_error"] is error
    assert daemon._source_handoff["phase"] == "unconfirmed"


@pytest.mark.parametrize("wrong_type", [False, True])
def test_service_environment_read_failure_precedes_service_mutation(tmp_path, monkeypatch, wrong_type):
    from agent_dispatch import coordinator_cli, source_start
    from agent_dispatch.registrar import RegistrarError

    service = tmp_path / "service.env"
    if wrong_type:
        service.mkdir()
    else:
        service.write_text("AGENT_DISPATCH_ENV=production\n", encoding="utf-8")
        original = Path.read_text

        def denied(path, *args, **kwargs):
            if path == service:
                raise PermissionError("fixture service environment denied")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda *_args, **_kwargs: pytest.fail("unvalidated service mutation"))
    with pytest.raises((OSError, RegistrarError)):
        source_start.command(argparse.Namespace())


def test_source_start_captures_service_environment_once_before_both_services(tmp_path, monkeypatch):
    from agent_dispatch import coordinator_cli, source_start

    _sources(monkeypatch, declarations=[object()])
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_DISPATCH_ENV", raising=False)
    (tmp_path / "supervisor.env").write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\n", encoding="utf-8")
    service = tmp_path / "service.env"
    service.write_text("AGENT_DISPATCH_ENV=production\n", encoding="utf-8")
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    calls = []

    def coordinator(_args, **kwargs):
        calls.append(os.environ.get("AGENT_DISPATCH_ENV"))
        service.write_text("AGENT_DISPATCH_ENV=changed-after-capture\n", encoding="utf-8")
        assert kwargs["source_environment"]["AGENT_DISPATCH_ENV"] == "production"
        return 0

    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", coordinator)
    monkeypatch.setattr(source_start, "ensure_supervisor", lambda _root, *, plan: calls.append(plan.env))
    assert source_start.command(argparse.Namespace(json=True)) == 0
    assert calls == ["production", "production"]
    assert "AGENT_DISPATCH_ENV" not in os.environ


@pytest.mark.parametrize("state", ["dead", "reused", "unknown"])
def test_dead_same_generation_route_only_withdraws_confirmed_stale_owner(tmp_path, monkeypatch, state):
    from agent_dispatch import client, source_start
    from agent_dispatch.config import Config
    from zdd import cutover, diagnostics, routing

    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    endpoint = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=43111, pid=os.getpid(), version=source_start.__version__,
        process_start_time=diagnostics.process_start_time(os.getpid()),
    )
    monkeypatch.setattr(source_start, "_route_owner_state", lambda _: state, raising=False)

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            raise RuntimeError("unavailable coordinator")

    monkeypatch.setattr(client, "DispatchClient", Client)

    class Orchestrator:
        def __init__(self, *_args, **kwargs):
            assert kwargs["expected_old"] is None
            assert kwargs["refuse_old"](None) is None

        def run(self, **_kwargs):
            return SimpleNamespace(to_dict=lambda: {"ok": True})

    monkeypatch.setattr(cutover, "CutoverOrchestrator", Orchestrator)
    run = lambda: source_start.run_coordinator(
        Config(db_path=str(tmp_path / "tasks.db")), tmp_path,
        token=None, spawn_passive=lambda _: pytest.fail("unexpected direct launch"),
        pick_free_port=lambda: 43112,
    )
    if state == "unknown":
        with pytest.raises(RuntimeError):
            run()
        assert routing.read_table(tmp_path)["active"] == endpoint.to_dict()
    else:
        assert run()["ok"]
        assert routing.read_table(tmp_path).get("active") is None


@pytest.mark.parametrize("observation", ["matching", "reused", "dead", "unknown", "missing-pid", "unavailable-sdk"])
def test_route_owner_observation_distinguishes_unavailable_native_identity(monkeypatch, observation):
    from agent_dispatch import companion, source_start
    from agent_procutil import _daemon_instance_identity
    from zdd import diagnostics

    def native_birth(_pid):
        if observation == "dead":
            raise ProcessLookupError("confirmed native exit")
        if observation == "unavailable-sdk":
            raise PermissionError("native liveness unavailable")
        return "native-birth"

    monkeypatch.setattr(_daemon_instance_identity, "birth_identity", native_birth)
    token = {"matching": "birth", "reused": "other"}.get(observation)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda _: token)
    monkeypatch.setattr(companion, "_process_exists", lambda _: observation == "unknown")
    endpoint = SimpleNamespace(pid=None if observation == "missing-pid" else 41, process_start_time="birth")
    expected = "unknown" if observation in {"missing-pid", "unavailable-sdk"} else observation
    assert source_start._route_owner_state(endpoint) == expected


def test_confirmed_dead_route_does_not_overwrite_previous_generation_custody(tmp_path, monkeypatch):
    from agent_dispatch import source_start
    from agent_dispatch.config import Config
    from zdd import diagnostics, routing

    for port in (43111, 43112):
        routing.publish_active(
            tmp_path, bind="127.0.0.1", port=port, pid=os.getpid(), version=source_start.__version__,
            process_start_time=diagnostics.process_start_time(os.getpid()), demote_existing=True,
        )
    before = routing.read_table(tmp_path)
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    monkeypatch.setattr(source_start, "_route_owner_state", lambda _: "dead")
    with pytest.raises(RuntimeError, match="previous cutover"):
        source_start.run_coordinator(
            Config(db_path=str(tmp_path / "tasks.db")), tmp_path, token=None,
            spawn_passive=lambda _: pytest.fail("unsettled previous custody"),
            pick_free_port=lambda: 43113,
        )
    assert routing.read_table(tmp_path) == before
