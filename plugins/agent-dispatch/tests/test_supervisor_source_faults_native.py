"""Native source regression: retained Windows process objects are not live."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import time
import uuid
from types import SimpleNamespace

import pytest

from tests.test_source_caller_job_native import _NativeProcess

pytestmark = pytest.mark.skipif(os.name != "nt", reason="retained Windows native process identity")


def test_exited_coordinator_with_retained_native_birth_is_confirmed_dead(tmp_path):
    from agent_procutil.daemon_instances import DaemonInstanceSpec, daemon_test_lifecycle, spawn_instance
    from agent_procutil._daemon_instance_identity import birth_identity, stop_identity
    from agent_dispatch import source_start
    from zdd.diagnostics import process_start_time

    cleanup, pin, process = [], None, None
    proof = {}
    try:
        with daemon_test_lifecycle(
            owner="dispatch-exited-route-native", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as overlay:
            process = spawn_instance(
                [sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
                instance=DaemonInstanceSpec(
                    "dispatch-exited-route-control", tmp_path, Path(__file__),
                    purpose="unattended-test", lifecycle="finite-worker",
                ),
                cwd=tmp_path, env={**os.environ, **overlay},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            birth = birth_identity(process.pid)
            token = process_start_time(process.pid)
            assert token
            pin = _NativeProcess(process.pid, birth)
            endpoint = SimpleNamespace(pid=process.pid, process_start_time=token)
            assert source_start._route_owner_state(endpoint) == "matching"
            assert stop_identity(process.pid, birth)
            assert not pin.running()
            assert process_start_time(process.pid) == token
            proof["native_handle_signalled_while_birth_still_queryable"] = True
            proof["observed_state"] = source_start._route_owner_state(endpoint)
            assert proof["observed_state"] == "dead"
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if pin is not None:
            pin.close()
        proof["cleanup"] = [asdict(item) for item in cleanup]
        evidence = os.environ.get("DISPATCH_SOURCE_EVIDENCE_ROOT")
        if evidence:
            (Path(evidence) / "retained-process-birth-proof.json").write_text(
                json.dumps(proof, indent=2), encoding="utf-8",
            )
    assert cleanup and all(item.status in {"settled", "missing"} for item in cleanup)


def test_installed_supervisor_failure_owners_with_real_native_candidates(tmp_path):
    snapshot_name = os.environ.get("DISPATCH_C_PLUGIN_SNAPSHOT")
    root_name = os.environ.get("DISPATCH_C_NATIVE_ROOT")
    evidence_name = os.environ.get("DISPATCH_SOURCE_EVIDENCE_ROOT")
    if not snapshot_name or not root_name or not evidence_name:
        pytest.skip("explicit admitted profile, private native root and evidence destination required")
    from agent_procutil.daemon_instances import DaemonInstanceSpec, daemon_test_lifecycle, spawn_instance
    from agent_procutil._daemon_instance_identity import birth_identity
    from agent_dispatch.client import DispatchClient
    from zdd import routing

    snapshot, evidence = Path(snapshot_name), Path(evidence_name)
    record = json.loads((snapshot / "install-source.json").read_text(encoding="utf-8"))
    application = snapshot / record["projects"]["agent-dispatch"]["application"]
    definition = json.loads((application / "application.json").read_text(encoding="utf-8"))
    generation = definition["construction"]["runtime_version"]
    root = Path(root_name) / "x"
    base = root / "versions" / generation
    shutil.copytree(application, base)
    profile = root / "profile"
    profile.mkdir()
    (profile / "uv.toml").write_text("", encoding="utf-8")
    (root / "service.env").write_text("AGENT_DISPATCH_ENV=native-faults\n", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("AGENT_DISPATCH") and key not in {
            "PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT", "COPILOT_AGENT_SESSION_ID",
            "UV_NO_CONFIG", "UV_CONFIG_FILE", "__PYVENV_LAUNCHER__",
        }
    }
    token, control = uuid.uuid4().hex, uuid.uuid4().hex
    environment.update({
        "HOME": str(profile), "USERPROFILE": str(profile),
        "AGENT_DISPATCH_INSTALL_DIR": str(root), "AGENT_DISPATCH_RUN_DIR": str(root / "control"),
        "AGENT_DISPATCH_ROUTING_DIR": str(root / "routes"),
        "AGENT_DISPATCH_DB": str(root / "data" / "tasks.db"),
        "AGENT_DISPATCH_HOST": "127.0.0.1",
        "AGENT_DISPATCH_TOKEN": token, "AGENT_DISPATCH_CONTROL_TOKEN": control,
        "AGENT_DISPATCH_GC_INTERVAL": "0", "AGENT_DISPATCH_SELF_UPDATE": "0",
        "AGENT_DISPATCH_SELF_RETIRE_POLL_S": "1", "AGENT_DISPATCH_SELF_RETIRE_CONFIRMATIONS": "1",
        "UV_CONFIG_FILE": str(profile / "uv.toml"),
        "UV_PYTHON_INSTALL_DIR": str(root / "python"), "UV_CACHE_DIR": str(root / "cache" / "uv"),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "0",
    })
    pins, cleanup = [], []
    proof = {"source": record["source_commit"], "phases": []}
    driver = None

    def install(slot):
        started = time.monotonic()
        completed = subprocess.run(
            [shutil.which("pwsh"), "-NoProfile", "-File", str(slot / "install.ps1")],
            cwd=profile, env=environment, capture_output=True, text=True,
            timeout=300, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        proof["phases"].append({"generation": slot.name, "seconds": time.monotonic() - started,
                                "exit": completed.returncode})
        (evidence / f"failure-install-{len(proof['phases'])}.stderr.log").write_text(
            completed.stderr, encoding="utf-8",
        )
        assert completed.returncode == 0, completed.stderr[-16000:]
        endpoint = routing.Endpoint.from_dict(routing.read_table(root / "routes")["active"])
        pin = _NativeProcess(endpoint.pid, "windows:" + endpoint.process_start_time)
        pins.append(pin)
        return endpoint, pin

    try:
        with daemon_test_lifecycle(
            owner="dispatch-installed-failure-native", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as overlay:
            environment.update(overlay)
            _old, old_pin = install(base)
            repair = base.with_name(generation + ".repair." + uuid.uuid4().hex)
            shutil.copytree(application, repair)
            current, _pin = install(repair)
            until = time.monotonic() + 30
            while old_pin.running():
                assert time.monotonic() < until
                time.sleep(0.05)
            assert old_pin.exit_code() == 0
            with DispatchClient(current.base_url, token=token, control_token=control) as client:
                task = client.create("native owner held fixture", repo="example.com/acme/native-faults")
                assert client.claim(worker_id="native-fixture-owner", repo="example.com/acme/native-faults",
                                    lease_seconds=3600)["id"] == task["id"]
                reserved = client.create("native owner reserved fixture", repo="example.com/acme/native-faults")
                reservation = client.reserve_spawn(reserved["id"])
                request = {
                    "root": str(root), "version": repair.name, "original_version": generation,
                    "held_id": task["id"], "held_before": client.get(task["id"]),
                    "reserved_id": reserved["id"], "reservation": reservation["reservation"],
                    "output": str(evidence / "installed-failure-controller.json"),
                    "remaining_only": os.environ.get("DISPATCH_NATIVE_REMAINING_ONLY") == "1",
                }
            fixture = Path(__file__).with_name("_source_failure_fixture.py")
            started = time.monotonic()
            driver = spawn_instance(
                [str(base / ".venv" / "Scripts" / "python.exe"), "-I", "-B", str(fixture), json.dumps(request)],
                instance=DaemonInstanceSpec(
                    "dispatch-installed-failure-controller", root, fixture,
                    purpose="unattended-test", lifecycle="finite-worker",
                ),
                cwd=profile, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            pins.append(_NativeProcess(driver.pid, birth_identity(driver.pid)))
            out, error = driver.communicate(timeout=240)
            (evidence / "installed-failure-controller.stdout.log").write_text(out, encoding="utf-8")
            (evidence / "installed-failure-controller.stderr.log").write_text(error, encoding="utf-8")
            proof["controller_seconds"] = time.monotonic() - started
            assert driver.returncode == 0, error[-20000:]
            detail = json.loads(Path(request["output"]).read_text(encoding="utf-8"))
            assert detail["completed"] and detail["held_work_preserved"]
            assert detail["selected_after"] == repair.name
            proof["controller"] = detail
    finally:
        if driver is not None and driver.poll() is None:
            driver.kill()
            driver.wait(timeout=5)
        proof["cleanup"] = [asdict(item) for item in cleanup]
        proof["all_retained_native_handles_exited"] = all(not pin.running() for pin in pins)
        for pin in pins:
            pin.close()
        (evidence / "installed-failure-native-proof.json").write_text(
            json.dumps(proof, indent=2), encoding="utf-8",
        )
    assert cleanup and all(item.status in {"settled", "missing"} for item in cleanup)
    assert proof["all_retained_native_handles_exited"]
