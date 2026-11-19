"""Opt-in real-user-manager qualification; never enroll an existing installation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
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


def _until(
    predicate: Callable[[], dict[str, Any] | None], seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.5)
    raise AssertionError("bounded native Linux fixture deadline")


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

            native = _until(observed_twice, 95)
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
