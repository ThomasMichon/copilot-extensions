"""Installed closure and native lifecycle qualification; no production enrollment."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import venv
from xml.etree import ElementTree as ET

import pytest

from agent_machines import health_enrollment as enrollment, health_jobs, health_run, health_slots
from agent_procutil import windowless_python
from agent_procutil.owned_process import run_caller_owned
from test_health_observe_consumer import QUALIFYING_BODY, listener, response

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("host-state"), pytest.mark.effect("process"),
    pytest.mark.effect("network"), pytest.mark.effect("filesystem"),
    pytest.mark.contract("agent_machines.health_windows_enrollment"),
    pytest.mark.skipif(
        sys.platform != "win32" or os.environ.get("AGENT_MACHINES_NATIVE_SCHEDULER") != "1",
        reason="explicit limited-user Windows scheduler fixture approval required",
    ),
]
REPO = Path(__file__).resolve().parents[4]
PLUGIN = REPO / "plugins" / "agent-machines"
NS = {"t": health_jobs.NS}


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _installed(root, version, *, collection_supported=True):
    """Use the ordinary completion/retention owner, with closed fixture inputs."""
    inputs = root.parent / ("build-input-" + version)
    helpers = inputs / "helpers"
    helpers.mkdir(parents=True)
    for source in (REPO / "libs" / "versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(source, helpers / source.name)
    shutil.copyfile(
        REPO / "libs" / "installer-engine" / "runtime_launcher.py", helpers / "runtime_launcher.py",
    )
    runtime = _module(helpers / "versioned_runtime.py", "_health_native_runtime_" + version.replace(".", "_"))
    slot = runtime.slot(root, version)
    environment = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    site = environment / "Lib" / "site-packages"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    modules = {
        "agent_machines": PLUGIN / "src" / "agent_machines",
        "agent_procutil": REPO / "libs" / "agent-procutil" / "src" / "agent_procutil",
    }
    for package in (
        "configuration_context", "configuration_layers", "dropin_registry",
        "plugin_activation", "plugin_resolve", "machine_transport", "remote_login_shell",
        "installer_readiness", "process_telemetry", "single_instance_lease", "yaml", "_yaml",
    ):
        spec = importlib.util.find_spec(package)
        assert spec is not None, package
        modules[package] = Path(spec.origin).parent if spec.submodule_search_locations else Path(spec.origin)
    for name, source in modules.items():
        if source.is_dir():
            shutil.copytree(source, inputs / name, ignore=ignore)
            shutil.copytree(inputs / name, site / name)
        else:
            shutil.copyfile(source, inputs / source.name)
            shutil.copyfile(inputs / source.name, site / source.name)
    for source in (REPO / "libs" / "installation-context").glob("*.py"):
        shutil.copyfile(source, inputs / source.name)
        shutil.copyfile(inputs / source.name, site / source.name)
    if not collection_supported:
        declaration = site / "agent_machines" / "health-collection-v1.json"
        value = json.loads(declaration.read_text(encoding="utf-8"))
        value["operations"].remove("installed-job")
        declaration.write_text(json.dumps(value), encoding="utf-8")
    runtime.retain_runtime_helpers(slot, complete=False)
    runtime.mark_complete(root, version, payload_hash="fixture-closed-input")
    runtime.activate(root, version, link_free=True)
    runtime = _module(
        slot / ".runtime-bootstrap" / "versioned_runtime.py", "_health_retained_runtime_" + version.replace(".", "_"),
    )
    launcher = _module(slot / ".runtime-bootstrap" / "runtime_launcher.py", "_health_native_launcher_" + version.replace(".", "_"))
    python = environment / "Scripts" / "python.exe"
    installed = enrollment.Installation(
        root, slot, python, windowless_python(python, require_windowless=True),
        launcher.runtime_identity(slot), runtime,
    )
    shutil.rmtree(inputs)
    assert not inputs.exists()
    # Exercise selection through the real installed CLI, not just this fixture's
    # accelerated-cadence adapter. This operation is strictly read-only.
    prefix = enrollment._peer_launch.retained_runtime_prefix(
        root, python, "agent_machines", runtime_identity=installed.identity,
    )
    if collection_supported:
        status = run_caller_owned(
            [*prefix, "health", "job", "status"], timeout=15,
            max_stdout_bytes=64 * 1024, max_stderr_bytes=4096, cwd=slot,
        )
        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["status"] == "observed"
    with pytest.raises(ValueError, match="gui-host-required"):
        enrollment.require_gui_host(python)
    return installed


class FixtureInstallation:
    """Change cadence only in this test-owned definition, never in the API."""
    def __init__(self, installed):
        self.installed = installed

    def __getattr__(self, name):
        return getattr(self.installed, name)

    def definition(self, desired, owner, *, enabled):
        root = ET.fromstring(self.installed.definition(desired, owner, enabled=enabled))
        trigger = root.find("t:Triggers/t:TimeTrigger", NS)
        start = datetime.now(timezone.utc) + timedelta(seconds=12)
        # Stable across converge calls, just as the production calendar boundary is.
        if not hasattr(self, "start"):
            self.start = start
        trigger.find("t:StartBoundary", NS).text = self.start.strftime("%Y-%m-%dT%H:%M:%SZ")
        trigger.find("t:Repetition/t:Interval", NS).text = "PT1M"
        ET.SubElement(trigger, f"{{{health_jobs.NS}}}EndBoundary").text = (
            self.start + timedelta(minutes=3)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        ET.SubElement(root.find("t:Settings", NS), f"{{{health_jobs.NS}}}DeleteExpiredTaskAfter").text = "PT1M"
        return ET.tostring(root, encoding="unicode")


def _store(tmp_path, port):
    store = tmp_path / "store"
    store.mkdir()
    (store / "policy.json").write_text(json.dumps({
        "schema": health_slots.POLICY_SCHEMA, "version": 2,
        "publisher": "fixture-publisher", "continuity": "fixture-continuity",
        "collection": {"bridge_port": port, "services": []},
    }), encoding="utf-8")
    (store / "collection.lock").write_bytes(b"\0")
    return store


def _until(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.2)
    raise AssertionError("Installed scheduler fixture deadline")


@pytest.mark.timeout(240)
def test_installed_periodic_closure_replacement_rollback_and_withdrawal(tmp_path):
    root = tmp_path / "runtime"
    first = FixtureInstallation(_installed(root, "1.0"))
    versions = {"1.0": first}
    current = [first]
    owner = enrollment.Enrollment(first, selector=lambda _root, version=None: versions[version] if version else current[0])
    cleanup = None
    try:
        with listener(response(QUALIFYING_BODY), count=4) as (port, requests):
            store = _store(tmp_path, port)
            sid = owner.backend.call("read")["account_sid"]
            desired = {
                "store": str(store), "policy_token": health_run.policy_preview(store)[2],
                "user_id": sid, "enabled": True,
            }
            preview = owner.configure(desired)
            result = owner.configure(desired, apply=True, expected=preview["preview_token"])
            assert result["status"] == "converged", result
            assert owner.converge()["status"] == "unchanged"

            def observed_twice():
                try:
                    records = [
                        json.loads(path.read_text(encoding="utf-8"))
                        for path in store.glob("*.json") if path.name != "policy.json"
                    ]
                except (FileNotFoundError, PermissionError):
                    return False
                return max(records, key=lambda row: row["hour"]) if (
                    len(requests) >= 2 and sum(row["revision"] for row in records) >= 2
                ) else False

            try:
                record = _until(observed_twice, 105)
            except AssertionError:
                raise AssertionError({
                    "native": owner.status(), "listener_requests": len(requests),
                    "records": [
                        json.loads(path.read_text(encoding="utf-8"))
                        for path in store.glob("*.json") if path.name != "policy.json"
                    ],
                }) from None
            target = store / f"{record['hour'] % 168:03d}.json"
            native = owner.backend.call("read")
            assert native["last_result"] == 0
            assert record["observation"]["domains"]["local_listener"]["state"] == "ok"
            refs = first.runtime._runtime_component("versioned_runtime_leases")
            assert refs.read_runtime_references(root)[enrollment.REFERENCE] == ["1.0"]
            # A different ready slot replaces both the GUI bootstrap and runtime action.
            second = FixtureInstallation(_installed(root, "2.0"))
            versions["2.0"] = second
            current[0] = second
            assert owner.converge()["status"] == "converged"
            assert owner.read()["confirmed"]["version"] == "2.0"
            assert refs.read_runtime_references(root)[enrollment.REFERENCE] == ["2.0"]
            # Requalify rollback through the same owner, not an old command fallback.
            first.runtime.activate(root, "1.0", link_free=True)
            current[0] = first
            assert owner.converge()["status"] == "converged"
            assert owner.read()["confirmed"]["version"] == "1.0"
            assert owner.disable()["status"] == "disabled"
            disabled = owner.backend.call("read")
            assert not disabled["enabled"] and not disabled["instances"]
            assert owner.converge(remove=True)["status"] == "removed"
            assert target.exists() and (store / "policy.json").exists()
    finally:
        cleanup = owner.converge(remove=True)
        (tmp_path / "enrollment-cleanup.json").write_text(json.dumps(cleanup), encoding="utf-8")
        assert not cleanup["configured"] and not cleanup["instances"], cleanup
        assert first.runtime._runtime_component("versioned_runtime_leases").read_runtime_references(root).get(
            enrollment.REFERENCE, [],
        ) == []
        # Native Stop can acknowledge an empty task-instance list before the
        # killed root finishes closing its retained consumer handles.
        leases = first.runtime._runtime_component("versioned_runtime_leases")
        for version in versions:
            with leases._kernel_lease(root, version, kind="retention", shared=False, timeout=10):
                pass


@pytest.mark.timeout(180)
def test_failed_installed_replacement_restores_known_good_and_runs_successor(tmp_path):
    root = tmp_path / "runtime"
    first = FixtureInstallation(_installed(root, "1.0"))
    versions = {"1.0": first}
    current = [first]
    owner = enrollment.Enrollment(first, selector=lambda _root, version=None: versions[version] if version else current[0])
    try:
        store = _store(tmp_path, None)
        desired = {
            "store": str(store), "policy_token": health_run.policy_preview(store)[2],
            "user_id": owner.backend.call("read")["account_sid"], "enabled": True,
        }
        prefix = enrollment._peer_launch.retained_runtime_prefix(
            root, first.python, "agent_machines", runtime_identity=first.identity,
        )
        actual_preview = run_caller_owned(
            [*prefix, "health", "job", "configure", "--store-dir", str(store),
             "--user-id", desired["user_id"], "--enabled"],
            timeout=15, max_stdout_bytes=64 * 1024, max_stderr_bytes=4096,
        )
        assert actual_preview.returncode == 0, actual_preview.stderr
        preview = owner.configure(desired)
        assert json.loads(actual_preview.stdout)["preview_token"] == preview["preview_token"]
        assert owner.configure(desired, apply=True, expected=preview["preview_token"])["status"] == "converged"
        original = enrollment.signature(owner.backend.call("read")["xml"])
        second = FixtureInstallation(_installed(root, "2.0"))
        versions["2.0"] = second
        current[0] = second
        call = owner.backend.call
        failed = []

        def fail_candidate_enable(operation, **fields):
            if operation == "enable" and not failed:
                failed.append(True)
                raise RuntimeError("fixture replacement fault")
            return call(operation, **fields)

        owner.backend.call = fail_candidate_enable
        result = owner.converge()
        assert result["status"] == "rolled-back" and result["rollback"] == "confirmed", result
        assert enrollment.signature(call("read")["xml"]) == original
        refs = first.runtime._runtime_component("versioned_runtime_leases")
        assert refs.read_runtime_references(root)[enrollment.REFERENCE] == ["1.0"]
        owner.backend.call = call
        switch_started = datetime.now(timezone.utc)
        assert owner.converge()["status"] == "converged"

        def successor_finished():
            native = call("read")
            last_run = datetime.fromisoformat(native["last_run"].replace("Z", "+00:00"))
            return native if (
                last_run >= switch_started and not native["instances"] and native["last_result"] == 0
            ) else False

        native = _until(successor_finished, 75)
        assert owner.read()["confirmed"]["version"] == "2.0"
        assert "versions\\2.0" in native["xml"]
        assert refs.read_runtime_references(root)[enrollment.REFERENCE] == ["2.0"]
        unsupported = FixtureInstallation(_installed(root, "0.5", collection_supported=False))
        versions["0.5"] = unsupported
        manager = enrollment._peer_launch.retained_runtime_prefix(
            root, second.python, "agent_machines", runtime_identity=second.identity,
        )
        before = call("read")["digest"]
        refused = run_caller_owned(
            [*manager, "health", "job", "converge"], timeout=20,
            max_stdout_bytes=64 * 1024, max_stderr_bytes=4096,
        )
        assert refused.returncode == 2 and "unsupported" in refused.stderr
        assert call("read")["digest"] == before
        for operation, status in (("disable", "disabled"), ("remove", "removed")):
            result = run_caller_owned(
                [*manager, "health", "job", operation], timeout=30,
                max_stdout_bytes=64 * 1024, max_stderr_bytes=4096,
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["status"] == status
    finally:
        cleanup = owner.converge(remove=True)
        (tmp_path / "replacement-cleanup.json").write_text(json.dumps(cleanup), encoding="utf-8")
        assert not cleanup["configured"] and not cleanup["instances"], cleanup
        leases = first.runtime._runtime_component("versioned_runtime_leases")
        for version in versions:
            with leases._kernel_lease(root, version, kind="retention", shared=False, timeout=10):
                pass


@pytest.mark.timeout(180)
def test_native_failed_withdrawal_stays_disabled_and_recovers_idempotently(tmp_path):
    root = tmp_path / "runtime"
    installed = FixtureInstallation(_installed(root, "1.0"))
    owner = enrollment.Enrollment(installed, selector=lambda *_args: installed)
    call = owner.backend.call
    try:
        store = _store(tmp_path, None)
        desired = {
            "store": str(store), "policy_token": health_run.policy_preview(store)[2],
            "user_id": call("read")["account_sid"], "enabled": True,
        }
        preview = owner.configure(desired)
        assert owner.configure(desired, apply=True, expected=preview["preview_token"])["status"] == "converged"
        def interrupt_disable(operation, **fields):
            if operation == "disable":
                raise RuntimeError("fixture pre-effect disable interruption")
            return call(operation, **fields)

        owner.backend.call = interrupt_disable
        assert owner.disable()["status"] == "unknown"
        assert call("read")["enabled"]
        owner.backend.call = call
        owner._policy = owner.qualifier = lambda *_args: pytest.fail("withdrawal required collection readiness")
        recovered = owner.converge()
        assert recovered["status"] == "disabled" and not recovered["enabled"] and not recovered["instances"]
        failed_remove = []

        def lose_remove_reply(operation, **fields):
            if operation == "remove" and not failed_remove:
                failed_remove.append(True)
                raise RuntimeError("fixture deletion fault")
            return call(operation, **fields)
        owner.backend.call = lose_remove_reply
        withdrawal = owner.converge(remove=True)
        assert withdrawal["status"] == "unknown" and not call("read")["enabled"]
        owner.backend.call = call
        manager = enrollment._peer_launch.retained_runtime_prefix(
            root, installed.python, "agent_machines", runtime_identity=installed.identity,
        )
        for operation in ("converge", "remove"):
            result = run_caller_owned(
                [*manager, "health", "job", operation], timeout=30,
                max_stdout_bytes=64 * 1024, max_stderr_bytes=4096,
            )
            assert result.returncode == 0, result.stderr
            row = json.loads(result.stdout)
            assert row["status"] == "removed" and not row["enabled"] and not row["configured"]
            if operation == "remove":
                assert row["changed"] is False
    finally:
        owner.backend.call = call
        cleanup = owner.converge(remove=True)
        (tmp_path / "withdrawal-cleanup.json").write_text(json.dumps(cleanup), encoding="utf-8")
        assert not cleanup["configured"] and not cleanup["instances"], cleanup
        leases = installed.runtime._runtime_component("versioned_runtime_leases")
        with leases._kernel_lease(root, "1.0", kind="retention", shared=False, timeout=10):
            pass
