"""Opt-in real-user-manager qualification; never enroll an existing installation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any, cast
import uuid

import pytest

from agent_machines import _peer_launch, health_enrollment as enrollment, health_run, health_slots
from agent_machines.health_systemd_native import NativePair
from agent_procutil.owned_process import run_caller_owned

_SPEC = importlib.util.spec_from_file_location(
    "_health_linux_native_fixture", Path(__file__).with_name("health_linux_fixture.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
fixture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fixture)
observe = fixture._module(
    Path(__file__).with_name("test_health_observe_consumer.py"), "_health_linux_listener",
)

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("host-state"), pytest.mark.effect("process"),
    pytest.mark.effect("network"), pytest.mark.effect("filesystem"),
    pytest.mark.contract("agent_machines.health_linux_installed"),
    pytest.mark.skipif(
        sys.platform != "linux" or os.environ.get("AGENT_MACHINES_NATIVE_SYSTEMD") != "1",
        reason="explicit isolated Linux user-unit fixture approval required",
    ),
]


@pytest.fixture
def native_scope(monkeypatch: pytest.MonkeyPatch) -> Path:
    config = os.environ.get("AGENT_MACHINES_NATIVE_SYSTEMD_CONFIG_HOME", "")
    if not config or not Path(config).is_absolute():
        pytest.skip("explicit existing user-manager config home required")
    runtime = Path("/run/user") / str(enrollment.local_uid())
    if not (runtime / "bus").exists() or not stat.S_ISSOCK((runtime / "bus").stat().st_mode):
        pytest.skip("already-running local user manager bus required")
    monkeypatch.setenv("XDG_CONFIG_HOME", config)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + str(runtime / "bus"))
    # Production readback must confirm this exact scope in the real UnitPath.
    # Do not fake a bus, redirect the manager's unit search path or start it.
    return Path(config) / "systemd" / "user"


@pytest.fixture
def native_root() -> Path:
    selected = os.environ.get("AGENT_MACHINES_NATIVE_SYSTEMD_ROOT", "")
    if not selected or not Path(selected).is_absolute() or not Path(selected).is_dir():
        pytest.skip("explicit persistent fixture evidence root required")
    root = Path(selected) / (
        "health-linux-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    )
    root.mkdir(mode=0o700)
    # Keep recovery state outside the runner's disposable sandbox, even on failure.
    return root


def _cli(installation: enrollment.Installation, *arguments: str) -> dict[str, Any]:
    prefix = _peer_launch.retained_runtime_prefix(
        installation.root, installation.python, "agent_machines",
        runtime_identity=installation.identity,
    )
    result = run_caller_owned(
        [*prefix, "health", *arguments], timeout=65,
        max_stdout_bytes=256 * 1024, max_stderr_bytes=4096, cwd=installation.slot,
    )
    assert result.returncode == 0, (result.returncode, result.stderr)
    return cast(dict[str, Any], json.loads(result.stdout))


def _store(directory: Path, port: int | None = None) -> Path:
    directory.mkdir()
    (directory / "policy.json").write_text(json.dumps({
        "schema": health_slots.POLICY_SCHEMA, "version": 2,
        "publisher": "fixture-publisher", "continuity": "fixture-continuity",
        "collection": {"bridge_port": port, "services": []},
    }), encoding="utf-8")
    (directory / "collection.lock").write_bytes(b"\0")
    return directory


def _cleanup(owner: enrollment.Enrollment, scope: Path, path: Path) -> None:
    result = owner.converge(remove=True)
    fixture.write_receipt(path, result)
    assert not result["configured"] and not result["instances"], result
    backend = cast(NativePair, owner.backend)
    for unit in backend.units:
        assert not (scope / unit).exists()
    assert not (scope / "timers.target.wants" / backend.units[1]).is_symlink()
    assert owner.leases.read_runtime_references(owner.root).get(enrollment.REFERENCE, []) == []


@pytest.mark.timeout(150)
def test_normal_installed_cli_disabled_enrollment_and_collection(
    native_root: Path, native_scope: Path,
) -> None:
    first = fixture.installed(native_root / "runtime", "1.0")
    owner = enrollment.Enrollment(first, selector=lambda *_args, **_kwargs: first)
    assert not owner.backend.call("read")["configured"]
    store = _store(native_root / "store")
    fixture.write_receipt(native_root / "fixture-owner.json", {
        "units": list(cast(NativePair, owner.backend).units),
        "owner": owner.owner, "runtime": str(first.root),
    })
    try:
        arguments = ("job", "configure", "--store-dir", str(store))
        preview = _cli(first, *arguments)
        result = _cli(first, *arguments, "--apply", "--expect", preview["preview_token"])
        assert result["status"] == "converged", result
        assert _cli(first, "job", "converge")["status"] == "unchanged"
        assert not _cli(first, "job", "status")["enabled"]
        with observe.listener(observe.response(observe.QUALIFYING_BODY)) as (port, requests):
            # Use a separate policy/store so the disabled enrollment stays exact.
            collection = _store(native_root / "collection", port)
            preview = _cli(first, "run", "--store-dir", str(collection))
            result = _cli(
                first, "run", "--store-dir", str(collection),
                "--apply", "--expect-policy", preview["policy_token"],
            )
            assert result["status"] == "completed", result
            assert len(requests) == 1
        assert _cli(first, "job", "remove")["status"] == "removed"
        assert (store / "policy.json").exists()
    finally:
        _cleanup(owner, native_scope, native_root / "cleanup.json")


@pytest.mark.timeout(240)
def test_finite_calendar_retained_action_replacement_and_rollback(
    native_root: Path, native_scope: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = native_root / "runtime"
    raw_first, raw_second = fixture.installed(root, "1.0"), fixture.installed(root, "2.0")
    raw_first.runtime.activate(root, "1.0", link_free=True)
    times, expires = fixture.calendar(datetime.now(timezone.utc))
    first, second = (
        fixture.FiniteInstallation(value, times, expires) for value in (raw_first, raw_second)
    )
    versions, current = {"1.0": first, "2.0": second}, [first]
    owner = enrollment.Enrollment(
        first, selector=lambda _root, version=None: versions[version] if version else current[0],
    )
    backend = cast(NativePair, owner.backend)
    assert not owner.backend.call("read")["configured"]
    fixture.write_receipt(native_root / "fixture-owner.json", first.receipt(owner.owner))
    try:
        with observe.listener(observe.response(observe.QUALIFYING_BODY), count=4) as (port, requests):
            store = _store(native_root / "store", port)
            desired = {
                "store": str(store), "policy_token": health_run.policy_preview(store)[2],
                "account_uid": enrollment.local_uid(), "enabled": True,
            }
            preview = owner.configure(desired)
            result = owner.configure(desired, apply=True, expected=preview["preview_token"])
            assert result["status"] == "converged", result
            assert owner.converge()["status"] == "unchanged"
            assert not (native_root / "build-input-1.0").exists()
            assert not (native_root / "build-input-2.0").exists()

            def observed_twice() -> dict[str, Any] | None:
                if len(requests) < 2:
                    return None
                native = owner.backend.call("read")
                return native if not native["instances"] and native["last_result"] is not None else None

            native = fixture.until(observed_twice, 95)
            assert native["last_result"] == {"result": "success", "code": "1", "status": "0"}
            assert datetime.fromisoformat(native["last_run"]) >= times[1]
            records = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in store.glob("*.json") if path.name != "policy.json"
            ]
            assert sum(value["revision"] for value in records) >= 2
            assert any(
                value["observation"]["domains"]["local_listener"]["state"] == "ok"
                for value in records if value.get("observation")
            )
            assert owner.leases.read_runtime_references(root)[enrollment.REFERENCE] == ["1.0"]
            original = owner.backend.call
            failed: list[bool] = []

            def fail_candidate_enable(operation: str, **fields: Any) -> dict[str, Any]:
                if operation == "enable" and not failed:
                    failed.append(True)
                    raise RuntimeError("fixture replacement fault")
                return original(operation, **fields)

            current[0] = second
            with monkeypatch.context() as fault:
                fault.setattr(backend, "call", fail_candidate_enable)
                result = owner.converge()
            assert result["status"] == "rolled-back" and result["rollback"] == "confirmed", result
            assert owner.leases.read_runtime_references(root)[enrollment.REFERENCE] == ["1.0"]
            assert owner.converge()["status"] == "converged"
            assert owner.read()["confirmed"]["version"] == "2.0"
            assert owner.leases.read_runtime_references(root)[enrollment.REFERENCE] == ["2.0"]
            assert datetime.now(timezone.utc) < expires
            backend._command("start", backend.units[0], timeout=60)
            successor = owner.backend.call("read")
            assert not successor["instances"]
            assert successor["last_result"] == {"result": "success", "code": "1", "status": "0"}
            assert len(requests) >= 3
            assert owner.disable()["status"] == "disabled"
            assert not owner.backend.call("read")["enabled"]
    finally:
        _cleanup(owner, native_scope, native_root / "cleanup.json")


@pytest.mark.timeout(150)
@pytest.mark.parametrize("operation", ["converge", "remove"])
def test_fresh_installed_cli_recovers_exit_between_unit_publications(
    native_root: Path, native_scope: Path, operation: str,
) -> None:
    first = fixture.installed(native_root / "runtime", "1.0")
    owner = enrollment.Enrollment(first, selector=lambda *_args, **_kwargs: first)
    backend = cast(NativePair, owner.backend)
    assert not backend.call("read")["configured"]
    store = _store(native_root / "store")
    fixture.write_receipt(native_root / "fixture-owner.json", {
        "units": list(backend.units), "owner": owner.owner, "runtime": str(first.root),
        "fault": "publisher-exit-after-first-unit", "recovery": operation,
    })
    preview = _cli(first, "job", "configure", "--store-dir", str(store))
    child = """
import os
from pathlib import Path
import runpy
import sys
from agent_machines import health_slots

scope, service, timer = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
original = health_slots._sync_directory
def exit_after_first_unit(path):
    original(path)
    if path == scope and (scope / service).is_file() and not (scope / timer).exists():
        os._exit(73)
health_slots._sync_directory = exit_after_first_unit
sys.argv = ["agent-machines", "health", "job", "configure", "--store-dir", sys.argv[4],
            "--apply", "--expect", sys.argv[5]]
runpy.run_module("agent_machines", run_name="__main__")
"""
    try:
        exited = run_caller_owned(
            [str(first.python), "-I", "-c", child, str(native_scope), *backend.units,
             str(store), preview["preview_token"]],
            timeout=65, max_stdout_bytes=64 * 1024, max_stderr_bytes=4096,
            cwd=first.slot,
        )
        assert exited.returncode == 73, (exited.returncode, exited.stderr)
        assert (native_scope / backend.units[0]).is_file()
        assert not (native_scope / backend.units[1]).exists()
        pending = owner.read()["pending"]
        assert pending is not None and not pending["native_unsettled"]
        observed = backend.call("read")
        assert observed["configured"] and not observed["enabled"] and not observed["instances"]
        recovered = _cli(first, "job", operation)
        assert recovered["status"] == ("removed" if operation == "remove" else "disabled")
        assert not recovered["enabled"] and not recovered["instances"] and not recovered["recovery_required"]
        assert owner.read()["pending"] is None
        references = owner.leases.read_runtime_references(first.root).get(enrollment.REFERENCE, [])
        if operation == "remove":
            assert not recovered["configured"] and references == []
        else:
            assert recovered["configured"] and references == ["1.0"]
            assert backend.call("read")["loaded"]
            assert all((native_scope / unit).is_file() for unit in backend.units)
        assert (store / "policy.json").exists()
        fixture.write_receipt(native_root / "publisher-recovery.json", {
            "child_exit": exited.returncode, "partial_pair_observed": True,
            "pending_native_unsettled": pending["native_unsettled"],
            "result": recovered,
        })
    finally:
        _cleanup(owner, native_scope, native_root / "cleanup.json")


@pytest.mark.timeout(150)
@pytest.mark.parametrize("failure", [
    "storage-unwritable", "startup-timeout",
    pytest.param("collection-child-timeout", id="collector_deadline"),
])
def test_native_failure_is_not_reported_as_success_and_remains_removable(
    native_root: Path, native_scope: Path, failure: str,
) -> None:
    installed = fixture.installed(native_root / "runtime", "1.0")
    times, expires = fixture.calendar(datetime.now(timezone.utc))
    marker = native_root / "pre-start-pid"
    first = (
        fixture.StartupDelayInstallation(installed, times, expires, marker)
        if failure == "startup-timeout" else fixture.FiniteInstallation(installed, times, expires)
    )
    if failure == "collection-child-timeout":
        fixture.stall_collection_child(installed, marker)
    owner = enrollment.Enrollment(first, selector=lambda *_args, **_kwargs: first)
    backend = cast(NativePair, owner.backend)
    assert not backend.call("read")["configured"]
    fixture.write_receipt(native_root / "fixture-owner.json", {
        **first.receipt(owner.owner), "failure": failure, "enabled": False,
    })
    with observe.listener(observe.response(observe.QUALIFYING_BODY), count=2) as (port, requests):
        store = _store(native_root / "store", port)
        mode = stat.S_IMODE(store.stat().st_mode)
        try:
            desired = {
                "store": str(store), "policy_token": health_run.policy_preview(store)[2],
                "account_uid": enrollment.local_uid(), "enabled": False,
            }
            preview = owner.configure(desired)
            result = owner.configure(desired, apply=True, expected=preview["preview_token"])
            assert result["status"] == "converged" and not result["enabled"], result
            before = {path.name: path.read_bytes() for path in store.iterdir() if path.is_file()}
            if failure == "storage-unwritable":
                store.chmod(0o500)
            with pytest.raises(health_slots.HealthError, match="operation-unconfirmed"):
                backend._command("start", backend.units[0], timeout=65)

            def settled_failure() -> dict[str, Any] | None:
                observed = backend.call("read")
                return observed if not observed["instances"] else None

            observed = fixture.until(settled_failure, 10)
            properties = backend._properties(backend.units[0])
            assert properties["Result"] == ("timeout" if failure == "startup-timeout" else "exit-code")
            assert not observed["enabled"]
            if failure == "startup-timeout":
                assert marker.is_file() and int(marker.read_text()) > 0
                assert observed["last_result"] is None and observed["last_run"] is None
                assert requests == []
            elif failure == "collection-child-timeout":
                assert marker.is_file() and int(marker.read_text()) > 0
                assert not (Path("/proc") / marker.read_text()).exists()
                assert observed["last_result"] == {"result": "exit-code", "code": "1", "status": "124"}
                assert requests == []
            else:
                assert observed["last_result"]["result"] == "exit-code"
                assert observed["last_result"]["status"] != "0"
                assert len(requests) == 1
            store.chmod(mode)
            assert {path.name: path.read_bytes() for path in store.iterdir() if path.is_file()} == before
            fixture.write_receipt(native_root / "native-failure.json", {
                "failure": failure, "manager_result": properties["Result"],
                "observation": observed, "store_unchanged": True,
            })
        finally:
            store.chmod(mode)
            _cleanup(owner, native_scope, native_root / "cleanup.json")


@pytest.mark.timeout(150)
def test_persistent_late_start_coalesces_missed_triggers_without_replay(
    native_root: Path, native_scope: Path,
) -> None:
    installed = fixture.installed(native_root / "runtime", "1.0")
    times, expires = fixture.calendar(datetime.now(timezone.utc))
    first = fixture.FiniteInstallation(installed, times, expires)
    owner = enrollment.Enrollment(first, selector=lambda *_args, **_kwargs: first)
    backend = cast(NativePair, owner.backend)
    assert not backend.call("read")["configured"]
    fixture.write_receipt(native_root / "fixture-owner.json", {
        **first.receipt(owner.owner), "scenario": "persistent-late-start",
    })
    try:
        with observe.listener(observe.response(observe.QUALIFYING_BODY), count=2) as (port, requests):
            store = _store(native_root / "store", port)
            desired = {
                "store": str(store), "policy_token": health_run.policy_preview(store)[2],
                "account_uid": enrollment.local_uid(), "enabled": True,
            }
            preview = owner.configure(desired)
            result = owner.configure(desired, apply=True, expected=preview["preview_token"])
            assert result["status"] == "converged" and result["enabled"], result
            assert datetime.now(timezone.utc) < times[0]
            backend._command("stop", backend.units[1], timeout=60)
            assert backend._properties(backend.units[1])["ActiveState"] == "inactive"

            def both_triggers_missed() -> dict[str, Any] | None:
                return {"missed": True} if datetime.now(timezone.utc) > times[1] + timedelta(seconds=1) else None

            fixture.until(both_triggers_missed, 60)
            assert requests == []
            resumed = datetime.now(timezone.utc)
            assert resumed < expires
            backend._command("start", backend.units[1], timeout=60)

            def collected_once() -> dict[str, Any] | None:
                if not requests:
                    return None
                observed = backend.call("read")
                return observed if not observed["instances"] and observed["last_result"] is not None else None

            observed = fixture.until(collected_once, 25)
            assert observed["last_result"] == {"result": "success", "code": "1", "status": "0"}
            assert datetime.fromisoformat(observed["last_run"]) >= resumed.replace(microsecond=0)
            time.sleep(5)
            assert len(requests) == 1
            records = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in store.glob("*.json") if path.name != "policy.json"
            ]
            assert len(records) == 1 and records[0]["revision"] == 1
            view = _cli(first, "inspect", "--store-dir", str(store), "--now", datetime.now(timezone.utc).isoformat())
            assert view["observed_hours"] == 1 and view["carrier_receipt"] == "not-observed"
            fixture.write_receipt(native_root / "late-start.json", {
                "missed_triggers": 2, "requests": len(requests), "observed_hours": view["observed_hours"],
                "resumed": resumed.isoformat(), "observation": observed,
            })
    finally:
        _cleanup(owner, native_scope, native_root / "cleanup.json")


@pytest.mark.timeout(150)
def test_second_native_start_during_actual_collection_does_not_duplicate(
    native_root: Path, native_scope: Path,
) -> None:
    installed = fixture.installed(native_root / "runtime", "1.0")
    times, expires = fixture.calendar(datetime.now(timezone.utc))
    delay = timedelta(seconds=40)
    times, expires = (times[0] + delay, times[1] + delay), expires + delay
    first = fixture.FiniteInstallation(installed, times, expires)
    owner = enrollment.Enrollment(first, selector=lambda *_args, **_kwargs: first)
    backend = cast(NativePair, owner.backend)
    assert not backend.call("read")["configured"]
    fixture.write_receipt(native_root / "fixture-owner.json", {
        **first.receipt(owner.owner), "scenario": "active-collection-overlap", "enabled": True,
    })
    overlap: list[dict[str, Any]] = []

    def request_overlapping_start() -> None:
        # The real observer is waiting for this response, not a pre-start child.
        before = backend._properties(backend.units[0])
        assert before["ActiveState"] == "activating"
        assert int(before["MainPID"]) > 0 and before["InvocationID"]
        backend._command("start", backend.units[0], "--no-block")
        after = backend._properties(backend.units[0])
        assert after["ActiveState"] == "activating"
        assert after["MainPID"] == before["MainPID"]
        assert after["InvocationID"] == before["InvocationID"]
        overlap.append({
            "pid": int(after["MainPID"]), "invocation": after["InvocationID"],
            "before": before["ActiveState"], "after": after["ActiveState"],
        })

    try:
        with observe.listener(
            observe.response(observe.QUALIFYING_BODY), count=2,
            before_response=request_overlapping_start,
        ) as (port, requests):
            store = _store(native_root / "store", port)
            desired = {
                "store": str(store), "policy_token": health_run.policy_preview(store)[2],
                "account_uid": enrollment.local_uid(), "enabled": True,
            }
            preview = owner.configure(desired)
            result = owner.configure(desired, apply=True, expected=preview["preview_token"])
            assert result["status"] == "converged" and result["enabled"], result
            assert datetime.now(timezone.utc) < times[0]
            backend._command("start", backend.units[0], timeout=60)
            time.sleep(3)
            observed = backend.call("read")
            assert len(requests) == len(overlap) == 1
            assert not observed["instances"] and observed["enabled"]
            assert observed["last_result"] == {"result": "success", "code": "1", "status": "0"}
            assert datetime.now(timezone.utc) < times[0]
            records = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in store.glob("*.json") if path.name != "policy.json"
            ]
            assert len(records) == 1 and records[0]["revision"] == 1
            assert records[0]["observation"]["domains"]["local_listener"]["state"] == "ok"
            fixture.write_receipt(native_root / "active-overlap.json", {
                "requests": len(requests), "revisions": records[0]["revision"],
                "overlap": overlap, "observation": observed,
            })
    finally:
        _cleanup(owner, native_scope, native_root / "cleanup.json")


@pytest.mark.timeout(150)
def test_unknown_native_client_settlement_fences_fresh_cli_until_owner_cleanup(
    native_root: Path, native_scope: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import owned_process

    first = fixture.installed(native_root / "runtime", "1.0")
    owner = enrollment.Enrollment(first, selector=lambda *_args, **_kwargs: first)
    backend = cast(NativePair, owner.backend)
    assert not backend.call("read")["configured"]
    store = _store(native_root / "store")
    fixture.write_receipt(native_root / "fixture-owner.json", {
        "units": list(backend.units), "owner": owner.owner, "runtime": str(first.root),
        "fault": "withheld-native-client-settlement-after-real-reload",
    })
    original = owned_process.run_caller_owned
    withheld: list[int] = []
    cleanup = owned_process.OwnedCleanupError(None, cleanup_retry=lambda _deadline: None)

    def unconfirmed(argv: list[str], **kwargs: Any) -> Any:
        if argv[-1] == "daemon-reload" and not withheld:
            # The real bounded client settles; only its acknowledgement is faulted.
            kwargs.pop("on_settled")
            completed = original(argv, **kwargs)
            assert completed.returncode == 0
            withheld.append(completed.returncode)
            raise cleanup
        return original(argv, **kwargs)

    try:
        desired = {
            "store": str(store), "policy_token": health_run.policy_preview(store)[2],
            "account_uid": enrollment.local_uid(), "enabled": False,
        }
        preview = owner.configure(desired)
        with monkeypatch.context() as fault:
            fault.setattr(owned_process, "run_caller_owned", unconfirmed)
            result = owner.configure(desired, apply=True, expected=preview["preview_token"])
        assert withheld == [0] and result["status"] == "unknown"
        assert owner.read()["pending"]["native_unsettled"]
        prefix = _peer_launch.retained_runtime_prefix(
            first.root, first.python, "agent_machines", runtime_identity=first.identity,
        )
        refused = run_caller_owned(
            [*prefix, "health", "job", "converge"], timeout=65,
            max_stdout_bytes=64 * 1024, max_stderr_bytes=4096, cwd=first.slot,
        )
        assert refused.returncode == 2
        assert "native-health-task-cleanup-unconfirmed" in refused.stdout + refused.stderr
        assert owner.read()["pending"]["native_unsettled"]
        with pytest.raises(ValueError, match="reference transaction is pending"):
            owner.leases.read_runtime_references(first.root)
        assert enrollment.settle_native_cleanup(owner)
        assert not owner.read()["pending"]["native_unsettled"]
        recovered = _cli(first, "job", "converge")
        assert recovered["status"] == "disabled" and not recovered["recovery_required"]
        assert owner.read()["pending"] is None
        assert owner.leases.read_runtime_references(first.root)[enrollment.REFERENCE] == ["1.0"]
        fixture.write_receipt(native_root / "client-settlement.json", {
            "fault": "withheld-acknowledgement", "native_client_exit": withheld[0],
            "fresh_cli_exit_while_unconfirmed": refused.returncode,
            "owner_cleanup_confirmed": True, "recovered": recovered,
        })
    finally:
        enrollment.settle_native_cleanup(owner)
        _cleanup(owner, native_scope, native_root / "cleanup.json")
