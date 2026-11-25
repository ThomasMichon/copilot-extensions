"""Service ensure preserves exact native startup and uncertain outcomes."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from agent_remote_driver_app import service_lifecycle as lifecycle
from agent_remote_driver.service import DriverService
from agent_remote_driver.session_host.osutil import process_start_time


def _record(calls: list[Any], *values: Any, result: Any = None) -> Any:
    calls.append(values)
    return result


def test_service_worker_refuses_unknown_arguments_before_installed_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lifecycle, "selected_source", lambda *_: pytest.fail("unexpected effect"))
    with pytest.raises(ValueError, match="no arguments"):
        lifecycle.main(["--unknown"])


def test_service_callback_preserves_one_deadline_and_host_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_remote_driver_app as app
    from agent_remote_driver_app import connector

    calls: list[Any] = []
    binding = SimpleNamespace(root=Path("fixture-root"))
    monkeypatch.setattr(lifecycle, "selected_source", lambda deadline: (
        _record(calls, "qualify", deadline, result=binding)
    ))
    monkeypatch.setattr(lifecycle, "start", lambda selected, *, deadline: (
        _record(calls, "start", deadline, selected)
    ))
    monkeypatch.setattr(connector, "install", lambda selected, *, deadline: (
        _record(calls, "connector", deadline, selected)
    ))
    monkeypatch.setattr(connector, "publish_startup", lambda selected, marketplace: (
        _record(calls, "startup", selected, marketplace)
    ))
    assert app.main(["__source-start"]) == 0
    assert calls == [
        ("qualify", calls[0][1]), ("connector", calls[0][1], binding),
        ("startup", binding, binding.root / "connector-marketplace"),
        ("start", calls[0][1], binding),
    ]
    with pytest.raises(ValueError, match="no application arguments"):
        app.main(["__source-start", "--unknown"])
    def forwarded(arguments: list[str]) -> int:
        calls.append(arguments)
        return 17
    monkeypatch.setattr(app, "host_main", forwarded)
    original = ["--mode", "headed", "--", "copilot", "literal"]
    assert app.main(original) == 17 and calls[-1] == original


@pytest.mark.parametrize("arguments", [
    ["service"], ["service", "ensure", "extra"], ["service", "--unknown"],
    ["service", "stop"], ["service", "stop", "--instance-id", "invalid"],
    ["service", "stop", "--instance-id", "a" * 32, "extra"],
    ["service", "stop", "--instance", "a" * 32],
])
def test_service_commands_reject_unknown_input_before_any_installed_read(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    import agent_remote_driver_app as app

    monkeypatch.setattr(lifecycle, "selected_source", lambda *_: pytest.fail("unadmitted read"))
    monkeypatch.setattr(app, "host_main", lambda *_: pytest.fail("host fallback"))
    with pytest.raises(ValueError, match="exactly one"):
        app.main(arguments)


def test_service_command_support_requires_no_installation_or_daemon(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import agent_remote_driver_app as app

    monkeypatch.setattr(lifecycle, "selected_source", lambda *_: pytest.fail("support side effect"))
    assert app.main(["service", "--support-json"]) == 0
    assert json.loads(capsys.readouterr().out)["operations"] == ["ensure", "status", "stop"]


@pytest.mark.parametrize("operation", ["ensure", "status"])
def test_service_commands_use_selected_owner_without_exposing_private_capability(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], operation: str,
) -> None:
    import agent_remote_driver_app as app

    endpoint = _endpoint()
    calls: list[Any] = []
    monkeypatch.setattr(lifecycle, "selected_source", lambda deadline: (
        _record(calls, "qualify", deadline, result=binding)
    ))
    monkeypatch.setattr(lifecycle, "start", lambda selected, *, deadline: (
        _record(calls, "ensure", deadline, result=endpoint)
    ))
    monkeypatch.setattr(lifecycle, "_ready", lambda root, generation, deadline: (
        _record(calls, "status", deadline, result=endpoint)
    ))
    assert app.main(["service", operation]) == 0
    assert calls == [("qualify", calls[0][1]), (operation, calls[0][1])]
    raw = capsys.readouterr().out
    assert endpoint.nonce not in raw and "nonce" not in raw and "port" not in raw
    assert json.loads(raw) == {
        "contract": "copilot-driver.service.commands", "version": 1, "state": "running",
        "generation": binding.slot.name, "instance_id": endpoint.instance_id, "pid": endpoint.pid,
    }


@pytest.fixture
def binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    root = tmp_path / "installed"
    slot = root / "versions" / "generation-one"
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_bytes(b"# owned fixture bootstrap\n")
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    monkeypatch.setattr(lifecycle, "load_provider", lambda selected: SimpleNamespace(
        _resources=lambda _: (None, SimpleNamespace(_native_retained_path=str)),
    ))
    return SimpleNamespace(root=root, slot=slot, python=slot / ".venv" / "python")


def _endpoint(generation: str = "generation-one") -> lifecycle.ServiceEndpoint:
    return lifecycle.ServiceEndpoint(
        "original-instance", generation, 123, "original-birth", 1234, "private-" + "x" * 40,
    )


@pytest.mark.parametrize("fail_host", [False, True])
def test_installed_host_holds_its_own_canonical_runtime_reference(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, fail_host: bool,
) -> None:
    import agent_remote_driver_app as app

    prefix = binding.slot / ".venv"
    prefix.mkdir()
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.delenv(lifecycle.ROOT_ENV, raising=False)
    calls: list[Any] = []
    active = [False]

    @contextmanager
    def retain(root: Path, generation: str, **options: Any) -> Iterator[None]:
        calls.append((root, generation, options))
        active[0] = True
        try:
            yield
        finally:
            active[0] = False
            calls.append("released")

    runtime = SimpleNamespace(
        read_marker=lambda *_: {"payload_hash": "a" * 64}, retention_lease=retain,
    )
    monkeypatch.setattr(lifecycle, "load_provider", lambda *_: SimpleNamespace(
        _application=lambda *_: {
            "name": "agent-remote-driver", "module": "agent_remote_driver_app:main",
            "construction": {"payload_hash": "a" * 64},
        },
        _resources=lambda *_: (runtime, None),
    ))

    def host(arguments: list[str]) -> int:
        assert active[0] and arguments == ["--literal", "value"]
        if fail_host:
            raise RuntimeError("fixture Host failure")
        return 17

    monkeypatch.setattr(app, "host_main", host)
    if fail_host:
        with pytest.raises(RuntimeError, match="fixture Host failure"):
            app.main(["--literal", "value"])
    else:
        assert app.main(["--literal", "value"]) == 17
    assert not active[0]
    assert calls == [
        (binding.root, binding.slot.name, {"payload_hash": "a" * 64, "timeout": 0}), "released",
    ]


@pytest.mark.parametrize("state", ["staged", "malformed", "different-hash", "foreign-root"])
def test_host_retention_preserves_explicit_staging_but_refuses_unattributable_completion(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    prefix = binding.slot / ".venv"
    prefix.mkdir()
    marker = binding.slot / ".install-complete.json"
    if state == "malformed":
        marker.write_bytes(b"malformed")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    if state == "foreign-root":
        monkeypatch.setenv(lifecycle.ROOT_ENV, str(prefix))
    else:
        monkeypatch.delenv(lifecycle.ROOT_ENV, raising=False)
    runtime = SimpleNamespace(
        read_marker=lambda *_: {"payload_hash": "b" * 64} if state == "different-hash" else None,
        marker_path=lambda *_: marker,
        retention_lease=lambda *_a, **_kw: pytest.fail("unadmitted reference"),
    )
    monkeypatch.setattr(lifecycle, "load_provider", lambda *_: SimpleNamespace(
        _application=lambda *_: {
            "name": "agent-remote-driver", "module": "agent_remote_driver_app:main",
            "construction": {"payload_hash": "a" * 64},
        },
        _resources=lambda *_: (runtime, None),
    ))
    if state == "staged":
        with lifecycle.retain_host_runtime():
            assert not marker.exists()
    else:
        with pytest.raises((ValueError, RuntimeError)):
            with lifecycle.retain_host_runtime():
                pytest.fail("unattributable installed Host was admitted")


@pytest.mark.parametrize("configured", [None, "exact", "foreign"])
def test_installed_public_command_derives_root_only_from_its_numbered_runtime(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, configured: str | None,
) -> None:
    prefix = binding.slot / ".venv"
    prefix.mkdir()
    monkeypatch.setattr(lifecycle, "sys", SimpleNamespace(prefix=str(prefix)))
    if configured is None:
        monkeypatch.delenv(lifecycle.ROOT_ENV, raising=False)
    else:
        monkeypatch.setenv(
            lifecycle.ROOT_ENV, str(binding.root if configured == "exact" else prefix),
        )
    calls: list[Any] = []

    def qualify(slot: Path, **options: Any) -> SimpleNamespace:
        calls.append((slot, options))
        return binding

    monkeypatch.setattr(lifecycle, "load_provider", lambda *_: SimpleNamespace(
        qualify_installed_application=qualify,
    ))
    if configured == "foreign":
        with pytest.raises(ValueError, match="differs"):
            lifecycle.selected_source(123)
        assert not calls
    else:
        assert lifecycle.selected_source(123) is binding
        assert calls == [(binding.slot, {
            "component": "agent-remote-driver", "command": "agent-remote-driver",
            "entry_point": "agent_remote_driver_app:main", "deadline": 123,
        })]


def test_ensure_reuses_only_ready_selected_owner_without_spawning(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _endpoint()
    monkeypatch.setattr(lifecycle, "_ready", lambda *args: endpoint)
    monkeypatch.setattr(lifecycle, "spawn_daemon", lambda *args, **kwargs: pytest.fail("duplicate"))
    assert lifecycle.start(binding, deadline=time.monotonic() + 3) is endpoint


@pytest.fixture
def stop_owner(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> tuple[lifecycle.ServiceEndpoint, dict[str, str], list[tuple[object, ...]]]:
    endpoint = replace(_endpoint(), instance_id="a" * 32)
    record = {
        "service": lifecycle.SERVICE, "runtime_root": str(binding.slot),
        "instance_id": endpoint.instance_id, "pid": str(endpoint.pid),
        "birth": "canonical-native-birth", "state": "running",
        "process_identity_state": "matching",
    }
    calls: list[tuple[object, ...]] = []

    class Pin:
        def __init__(self, pid: int, birth: str) -> None:
            calls.append(("pin", pid, birth))

        def __enter__(self) -> Pin:
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append(("release",))

        def terminate(self, *, timeout: float) -> None:
            assert 0 < timeout <= 10
            calls.append(("terminate",))

        def exited(self) -> bool:
            calls.append(("exited",))
            return True

    monkeypatch.setattr(lifecycle, "PinnedProcess", Pin, raising=False)
    monkeypatch.setattr(lifecycle, "endpoints", lambda root, **kwargs: [endpoint])
    monkeypatch.setattr(lifecycle, "observe_instances", lambda **kwargs: [record])
    monkeypatch.setattr(lifecycle, "process_start_time", lambda pid: endpoint.birth)
    return endpoint, record, calls


def test_stop_pins_the_exact_registration_not_the_display_birth(
    binding: SimpleNamespace,
    stop_owner: tuple[lifecycle.ServiceEndpoint, dict[str, str], list[tuple[object, ...]]],
) -> None:
    endpoint, _record, calls = stop_owner
    assert lifecycle.stop(
        binding, endpoint.instance_id, deadline=time.monotonic() + 3,
    ) == endpoint
    assert calls == [
        ("pin", endpoint.pid, "canonical-native-birth"),
        ("terminate",), ("exited",), ("release",),
    ]


@pytest.mark.parametrize("change", ["instance", "runtime", "pid", "identity", "birth"])
def test_stop_refuses_changed_registration_before_any_native_signal(
    binding: SimpleNamespace,
    stop_owner: tuple[lifecycle.ServiceEndpoint, dict[str, str], list[tuple[object, ...]]],
    change: str,
) -> None:
    endpoint, record, calls = stop_owner
    record.update({
        "instance": {"instance_id": "b" * 32},
        "runtime": {"runtime_root": str(binding.root)},
        "pid": {"pid": "321"},
        "identity": {"process_identity_state": "unknown"},
        "birth": {"birth": ""},
    }[change])
    with pytest.raises(RuntimeError, match="registration"):
        lifecycle.stop(binding, endpoint.instance_id, deadline=time.monotonic() + 3)
    assert not calls


@pytest.mark.parametrize("change", ["replacement", "duplicates", "unresolved", "selected", "expired"])
def test_stop_never_follows_a_replacement_or_uncertain_owner(
    binding: SimpleNamespace,
    stop_owner: tuple[lifecycle.ServiceEndpoint, dict[str, str], list[tuple[object, ...]]],
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    endpoint, _record, calls = stop_owner
    if change == "replacement":
        monkeypatch.setattr(lifecycle, "endpoints", lambda *args, **kwargs: [
            replace(endpoint, instance_id="b" * 32),
        ])
    elif change == "duplicates":
        monkeypatch.setattr(lifecycle, "endpoints", lambda *args, **kwargs: [endpoint, endpoint])
    elif change == "unresolved":
        def unresolved(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("Driver service startup outcome is unresolved")
        monkeypatch.setattr(lifecycle, "endpoints", unresolved)
    elif change == "selected":
        (binding.root / "current-version").write_text("another-generation", encoding="utf-8")
    deadline = time.monotonic() + (-1 if change == "expired" else 3)
    with pytest.raises((RuntimeError, TimeoutError)):
        lifecycle.stop(binding, endpoint.instance_id, deadline=deadline)
    assert not calls


def test_stop_absence_is_idempotent_and_does_not_signal(
    binding: SimpleNamespace,
    stop_owner: tuple[lifecycle.ServiceEndpoint, dict[str, str], list[tuple[object, ...]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, _record, calls = stop_owner
    monkeypatch.setattr(lifecycle, "endpoints", lambda *args, **kwargs: [])
    assert lifecycle.stop(binding, endpoint.instance_id, deadline=time.monotonic() + 3) is None
    assert not calls


def test_public_stop_reports_only_the_original_exited_instance(
    binding: SimpleNamespace,
    stop_owner: tuple[lifecycle.ServiceEndpoint, dict[str, str], list[tuple[object, ...]]],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import agent_remote_driver_app as app

    endpoint, _record, _calls = stop_owner
    monkeypatch.setattr(lifecycle, "selected_source", lambda deadline: binding)
    assert app.main(["service", "stop", "--instance-id", endpoint.instance_id]) == 0
    raw = capsys.readouterr().out
    assert "nonce" not in raw and "port" not in raw and endpoint.nonce not in raw
    assert json.loads(raw) == {
        "contract": "copilot-driver.service.commands", "version": 1, "state": "stopped",
        "generation": binding.slot.name, "instance_id": endpoint.instance_id, "pid": endpoint.pid,
    }


def test_unresolved_existing_start_never_spawns_a_replacement(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unresolved(*args: Any) -> None:
        raise RuntimeError("Driver service startup outcome is unresolved")

    monkeypatch.setattr(lifecycle, "_ready", unresolved)
    monkeypatch.setattr(lifecycle, "spawn_daemon", lambda *args, **kwargs: pytest.fail("replay"))
    with pytest.raises(RuntimeError, match="unresolved"):
        lifecycle.start(binding, deadline=time.monotonic() + 3)


def test_service_expired_deadline_refuses_before_native_creation(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lifecycle, "_ready", lambda *args: None)
    monkeypatch.setattr(lifecycle, "spawn_daemon", lambda *args, **kwargs: pytest.fail("late spawn"))
    with pytest.raises(TimeoutError, match="before launch"):
        lifecycle.start(binding, deadline=time.monotonic() - 1)


@pytest.mark.parametrize("selection_changed", [False, True])
def test_ensure_pins_one_retained_application_and_preserves_uncertain_child(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, selection_changed: bool,
) -> None:
    endpoint = _endpoint()
    selections = iter([None, endpoint])
    monkeypatch.setattr(lifecycle, "_ready", lambda *args: next(selections))
    calls: list[Any] = []
    process = SimpleNamespace(pid=endpoint.pid, poll=lambda: None)

    def spawn(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((command, kwargs))
        if selection_changed:
            (binding.root / "current-version").write_text("generation-two", encoding="utf-8")
        return process

    monkeypatch.setattr(lifecycle, "spawn_daemon", spawn)
    if selection_changed:
        with pytest.raises(lifecycle.StartupUncertain) as failure:
            lifecycle.start(binding, deadline=time.monotonic() + 3)
        assert failure.value.process is process
    else:
        assert lifecycle.start(binding, deadline=time.monotonic() + 3) is endpoint
    assert len(calls) == 1
    command, options = calls[0]
    assert command[:4] == [str(binding.python), "-I", "-S", "-B"]
    assert command[4] == str(binding.slot / ".runtime-bootstrap" / "runtime_launcher.py")
    assert command[-2:] == ["-m", "agent_remote_driver_app.service_lifecycle"]
    assert options["instance"].runtime_root == binding.slot
    assert options["env_overrides"] == {lifecycle.ROOT_ENV: str(binding.root)}


@pytest.mark.parametrize("healthy", [False, True])
def test_same_generation_unready_owner_is_not_permission_to_launch_again(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, healthy: bool,
) -> None:
    endpoint = _endpoint()
    monkeypatch.setattr(lifecycle, "endpoints", lambda root, **kwargs: [endpoint])
    monkeypatch.setattr(lifecycle, "healthy", lambda *args, **kwargs: healthy)
    if healthy:
        assert lifecycle._ready(binding.root, binding.slot.name, time.monotonic() + 3) is endpoint
    else:
        with pytest.raises(RuntimeError, match="original owner"):
            lifecycle._ready(binding.root, binding.slot.name, time.monotonic() + 3)


@pytest.mark.parametrize("changed", ["none", "instance", "birth", "port", "location"])
@pytest.mark.parametrize("layout", ["original", "sealed-directory"])
def test_service_endpoint_observation_retains_native_identity_and_private_capability(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, changed: str, layout: str,
) -> None:
    if layout == "original":
        directory = binding.slot / "service" / "original-instance"
        directory.mkdir(parents=True)
        path = directory / "service.json"
    else:
        path = binding.slot / ".runtime-bootstrap" / "driver-service-original-instance.json"
    state = {
        "root": str(binding.root), "generation": binding.slot.name,
        "instance_id": "original-instance", "pid": 123, "birth": "original-birth",
        "port": 1234, "nonce": "private-" + "x" * 40,
    }
    if changed == "instance":
        state["instance_id"] = "replacement"
    elif changed == "birth":
        state["birth"] = "another-process"
    elif changed == "port":
        state["port"] = True
    path.write_text(json.dumps(state), encoding="utf-8")
    record = {
        "state": "running", "process_identity_state": "matching",
        "runtime_root": str(binding.slot), "pid": "123", "instance_id": "original-instance",
        lifecycle.LOCATOR: str(path if changed != "location" else binding.root / "other.json"),
    }
    monkeypatch.setattr(lifecycle, "observe_instances", lambda **kwargs: [record])
    monkeypatch.setattr(lifecycle, "process_start_time", lambda pid: "original-birth")
    if changed == "none":
        observed = lifecycle.endpoints(binding.root)
        assert observed == [_endpoint()]
        assert state["nonce"] not in repr(observed)
    else:
        with pytest.raises(ValueError):
            lifecycle.endpoints(binding.root)


def test_service_incoming_generation_is_not_vetoed_by_unresolved_predecessor(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    predecessor = {
        "state": "running", "process_identity_state": "matching",
        "runtime_root": str(binding.slot.parent / "old-generation"), "pid": "321",
        "instance_id": "unknown-original",
    }
    monkeypatch.setattr(lifecycle, "observe_instances", lambda **kwargs: [predecessor])
    assert lifecycle.endpoints(binding.root, generation=binding.slot.name) == []
    with pytest.raises(RuntimeError, match="unresolved"):
        lifecycle.endpoints(binding.root, generation="old-generation")


@pytest.mark.parametrize("state,identity", [
    ("reserved", "unknown"), ("bound", "matching"), ("bound", "unknown"),
    ("running", "unknown"), ("finished", "matching"),
])
def test_original_partial_startup_refuses_replacement_even_without_endpoint(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, state: str, identity: str,
) -> None:
    record = {
        "state": state, "process_identity_state": identity,
        "runtime_root": str(binding.slot), "pid": "123", "instance_id": "original-instance",
    }
    monkeypatch.setattr(lifecycle, "observe_instances", lambda **_kwargs: [record])
    monkeypatch.setattr(lifecycle, "spawn_daemon", lambda *_a, **_kw: pytest.fail("unknown replay"))
    with pytest.raises(RuntimeError, match="unresolved"):
        lifecycle.start(binding, deadline=time.monotonic() + 3)


@pytest.mark.parametrize("identity", ["dead", "reused"])
def test_confirmed_original_process_exit_permits_same_generation_recovery(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, identity: str,
) -> None:
    record = {
        "state": "running", "process_identity_state": identity,
        "runtime_root": str(binding.slot), "pid": "123", "instance_id": "original-instance",
    }
    monkeypatch.setattr(lifecycle, "observe_instances", lambda **_kwargs: [record])
    assert lifecycle._ready(binding.root, binding.slot.name, time.monotonic() + 3) is None


@pytest.mark.asyncio
async def test_service_health_consumer_uses_real_capability_header_and_native_birth() -> None:
    service = DriverService(
        "synthetic-private-" + "x" * 40, instance_id="actual-instance", generation="generation-one",
    )
    await service.serve()
    try:
        birth = process_start_time(os.getpid())
        assert birth is not None
        endpoint = lifecycle.ServiceEndpoint(
            "actual-instance", "generation-one", os.getpid(),
            birth, service.port, "synthetic-private-" + "x" * 40,
        )
        assert await asyncio.to_thread(lifecycle.healthy, endpoint, timeout=3)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_service_waits_for_positive_successor_before_draining_its_frontend(
    binding: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def register(path: Path) -> None:
        assert path == (
            binding.slot / ".runtime-bootstrap" / "driver-service-original-instance.json"
        )
        assert not (binding.slot / "service").exists()
        events.append("artifact")

    guard = SimpleNamespace(
        receipt=SimpleNamespace(instance_id="original-instance"),
        add_artifact=register,
        close=lambda: events.append("guard-close"),
    )
    monkeypatch.setattr(lifecycle, "admit_daemon_instance", lambda *args, **kwargs: guard)
    monkeypatch.setattr(lifecycle, "SingleInstance", lambda *args, **kwargs: SimpleNamespace(
        acquire=lambda: events.append("lease"), release=lambda: events.append("release"),
    ))
    monkeypatch.setattr(lifecycle, "process_start_time", lambda pid: "original-birth")
    monkeypatch.setattr(
        lifecycle, "publish_file_observation", lambda *args: events.append("publish"),
    )
    selected = iter([binding.slot.name, "generation-two", "generation-two"])
    monkeypatch.setattr(lifecycle, "_selected", lambda _: next(selected))
    attempts: list[Any] = []

    def ready(*args: Any) -> lifecycle.ServiceEndpoint:
        attempts.append(args)
        if len(attempts) == 1:
            raise RuntimeError("successor startup is unresolved")
        return _endpoint("generation-two")

    monkeypatch.setattr(lifecycle, "_ready", ready)

    class Service:
        port = 1234

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def serve(self) -> None:
            events.append("serve")

        async def drain(self) -> None:
            events.append("drain")

        async def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(lifecycle, "DriverService", Service)
    await lifecycle.serve(binding)
    assert len(attempts) == 2
    assert events == [
        "lease", "serve", "artifact", "publish", "drain", "close", "release", "guard-close",
    ]


@pytest.mark.parametrize("already_dead", [False, True])
@pytest.mark.parametrize("body_fails", [False, True])
def test_removal_keeps_start_custody_through_exact_artifact_settlement_and_withdrawal(
    binding: SimpleNamespace,
    stop_owner: tuple[lifecycle.ServiceEndpoint, dict[str, str], list[tuple[object, ...]]],
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, already_dead: bool, body_fails: bool,
) -> None:
    from single_instance_lease.operation import OperationLock, OperationLockedError

    endpoint, record, calls = stop_owner
    registry = tmp_path / "registry"
    record["protected"] = "0"
    if already_dead:
        record["process_identity_state"] = "dead"
    deadline = time.monotonic() + 3
    cleanup_calls: list[object] = []
    lock = binding.root / "driver-service-start.lock"

    def cleanup(receipt: Any, **options: Any) -> SimpleNamespace:
        assert receipt.instance_id == endpoint.instance_id and receipt.registry_root == registry
        assert options == {"terminate": False, "deadline": deadline, "runtime_root": binding.slot}
        with pytest.raises(OperationLockedError):
            with OperationLock(lock):
                pytest.fail("start custody escaped during artifact settlement")
        cleanup_calls.append(receipt)
        return SimpleNamespace(status="settled", reason="process-exit-observed")

    monkeypatch.setattr(lifecycle, "default_registry_root", lambda: registry, raising=False)
    monkeypatch.setattr(lifecycle, "cleanup_instance", cleanup, raising=False)

    def withdraw() -> None:
        with lifecycle.service_removal_custody(binding, endpoint.instance_id, deadline=deadline) as stopped:
            assert stopped == (None if already_dead else endpoint)
            assert len(cleanup_calls) == 1
            with pytest.raises(OperationLockedError):
                lifecycle.start(binding, deadline=deadline)
            if body_fails:
                raise RuntimeError("fixture withdrawal remains unresolved")

    if body_fails:
        with pytest.raises(RuntimeError, match="withdrawal remains unresolved"):
            withdraw()
    else:
        withdraw()
    assert calls == ([] if already_dead else [
        ("pin", endpoint.pid, "canonical-native-birth"),
        ("terminate",), ("exited",), ("release",),
    ])
    with OperationLock(lock):
        assert len(cleanup_calls) == 1


@pytest.mark.parametrize("status", ["held", "live", "missing"])
def test_unsettled_service_artifacts_block_withdrawal_without_cleanup_retry(
    binding: SimpleNamespace,
    stop_owner: tuple[lifecycle.ServiceEndpoint, dict[str, str], list[tuple[object, ...]]],
    monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    endpoint, record, calls = stop_owner
    record["process_identity_state"] = "dead"
    attempts: list[str] = []

    def cleanup(receipt: Any, **options: Any) -> SimpleNamespace:
        attempts.append(receipt.instance_id)
        return SimpleNamespace(status=status, reason="fixture-unconfirmed")

    monkeypatch.setattr(lifecycle, "cleanup_instance", cleanup, raising=False)
    with pytest.raises(RuntimeError, match="artifact settlement"):
        with lifecycle.service_removal_custody(
            binding, endpoint.instance_id, deadline=time.monotonic() + 3,
        ):
            pytest.fail("unsettled artifacts admitted publication withdrawal")
    assert attempts == [endpoint.instance_id] and not calls


@pytest.mark.parametrize("change", ["protected", "unknown", "reused", "runtime", "absent"])
def test_removal_refuses_unconfirmed_original_before_signal_or_artifact_effects(
    binding: SimpleNamespace,
    stop_owner: tuple[lifecycle.ServiceEndpoint, dict[str, str], list[tuple[object, ...]]],
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    endpoint, record, calls = stop_owner
    if change == "protected":
        record["protected"] = "1"
    elif change in {"unknown", "reused"}:
        record["process_identity_state"] = change
    elif change == "runtime":
        record["runtime_root"] = str(binding.root)
    else:
        monkeypatch.setattr(lifecycle, "observe_instances", lambda **options: [])
    monkeypatch.setattr(
        lifecycle, "cleanup_instance", lambda *_a, **_kw: pytest.fail("unadmitted cleanup"),
        raising=False,
    )
    with pytest.raises(RuntimeError, match="registration"):
        with lifecycle.service_removal_custody(
            binding, endpoint.instance_id, deadline=time.monotonic() + 3,
        ):
            pytest.fail("unconfirmed original admitted withdrawal")
    assert not calls
