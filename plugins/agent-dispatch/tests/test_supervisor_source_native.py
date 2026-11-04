"""Opt-in enabled-master and standalone Source consumer qualification."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from tests.test_source_caller_job_native import _NativeProcess

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native installed Windows lifecycle")


@pytest.mark.parametrize("component", ["agent-dispatch", "dispatch-service"])
def test_enabled_source_master_independent_handoff_and_standalone(tmp_path, component):
    variables = {
        "agent-dispatch": "DISPATCH_C_PLUGIN_SNAPSHOT",
        "dispatch-service": "DISPATCH_C_SERVICE_SNAPSHOT",
    }
    selected = os.environ.get(variables[component])
    root_name = os.environ.get("DISPATCH_C_NATIVE_ROOT")
    evidence_name = os.environ.get("DISPATCH_SOURCE_EVIDENCE_ROOT")
    if not selected or not root_name or not evidence_name:
        pytest.skip("explicit admitted C profiles and private native/evidence roots required")
    from agent_procutil import direct_python, spawn_sync_in_kill_on_close_job
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.single_instance import is_locked, lock_path_for, read_holder_pid
    from agent_dispatch.supervisor_source import read_prepared
    from zdd import routing

    snapshot, evidence = Path(selected), Path(evidence_name)
    record = json.loads((snapshot / "install-source.json").read_text(encoding="utf-8"))
    key = "agent-dispatch" if component == "agent-dispatch" else "standalone:dispatch-service"
    application = snapshot / record["projects"][key]["application"]
    declaration = json.loads((application / "application.json").read_text(encoding="utf-8"))
    generation = declaration["construction"]["runtime_version"]
    root = Path(root_name) / ("u" if component == "agent-dispatch" else "v")
    slot = root / "versions" / generation
    shutil.copytree(application, slot)
    profile = root / "profile"
    profile.mkdir()
    (profile / "uv.toml").write_text("", encoding="utf-8")
    (root / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_MODE=serve\nAGENT_DISPATCH_SUPERVISE_INTERVAL=2\n",
        encoding="utf-8",
    )
    token, control = uuid.uuid4().hex, uuid.uuid4().hex
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith("AGENT_DISPATCH") and name not in {
            "PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT", "COPILOT_AGENT_SESSION_ID",
            "UV_NO_CONFIG", "UV_CONFIG_FILE", "__PYVENV_LAUNCHER__",
        }
    }
    environment.update({
        "HOME": str(profile), "USERPROFILE": str(profile),
        "AGENT_DISPATCH_INSTALL_DIR": str(root), "AGENT_DISPATCH_RUN_DIR": str(root / "control"),
        "AGENT_DISPATCH_ROUTING_DIR": str(root / "routes"),
        "AGENT_DISPATCH_DB": str(root / "data" / "tasks.db"),
        "AGENT_DISPATCH_HOST": "127.0.0.1",
        "AGENT_DISPATCH_TOKEN": token, "AGENT_DISPATCH_CONTROL_TOKEN": control,
        "AGENT_DISPATCH_GC_INTERVAL": "0", "AGENT_DISPATCH_SELF_UPDATE": "0",
        "AGENT_DISPATCH_SELF_RETIRE_POLL_S": "1", "AGENT_DISPATCH_SELF_RETIRE_CONFIRMATIONS": "1",
        "AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE": "1",
        "AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_POLL_S": "1",
        "UV_CONFIG_FILE": str(profile / "uv.toml"),
        "UV_PYTHON_INSTALL_DIR": str(root / "python"), "UV_CACHE_DIR": str(root / "cache" / "uv"),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "0",
    })
    host = shutil.which("pwsh")
    assert host
    phases, pins, cleanup = [], [], []
    proof = {"component": component, "source": record["source_commit"], "generation": generation}
    caller = caller_job = incoming_install = None
    hidden = []

    def endpoint():
        return routing.Endpoint.from_dict(routing.read_table(root / "routes")["active"])

    def prepared(version):
        for path in (root / "run" / "supervisor-candidates").glob("*.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("runtime_version") == version:
                ready = read_prepared(root, version, document["scope"], instance_id=path.stem)
                if ready is not None:
                    return ready
        return None

    def status(ready):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for path in (root / "control").glob("*.status.json"):
                document = json.loads(path.read_text(encoding="utf-8"))
                if (
                    document.get("generation") == ready["generation"]
                    and document.get("runtime_version") == ready["runtime_version"]
                    and document.get("cycle_id", 0) > 0
                    and document.get("updated_at", 0) >= document.get("cycle_started_at", float("inf"))
                    and document.get("progress", {}).get("phase") == "complete"
                ):
                    return document
            time.sleep(0.02)
        raise AssertionError("selected supervisor did not complete its native cycle")

    def invoke(phase, command, arguments=()):
        started = time.monotonic()
        completed = subprocess.run(
            [host, "-NoProfile", "-File", str(command), *arguments],
            cwd=profile, env=environment, capture_output=True, text=True,
            timeout=300, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        elapsed = time.monotonic() - started
        phases.append({"phase": phase, "seconds": elapsed, "exit": completed.returncode})
        (evidence / f"{component}-{phase}.stderr.log").write_text(completed.stderr, encoding="utf-8")
        (evidence / f"{component}-{phase}.stdout.log").write_text(completed.stdout, encoding="utf-8")
        assert completed.returncode == 0, completed.stderr[-16000:]
        assert elapsed <= 300
        return completed

    try:
        with daemon_test_lifecycle(
            owner="dispatch-enabled-master-native", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as overlay:
            environment.update(overlay)
            python, overrides = direct_python(sys.executable)
            environment.update(overrides)
            request = {
                "root": str(root), "command": [host, "-NoProfile", "-File", str(slot / "install.ps1")],
                "pending": str(evidence / f"{component}-caller-pending.json"),
                "result": str(evidence / f"{component}-caller-result.json"),
                "stdout": str(evidence / f"{component}-install.stdout.log"),
                "stderr": str(evidence / f"{component}-install.stderr.log"),
            }
            started = time.monotonic()
            caller, caller_job = spawn_sync_in_kill_on_close_job(
                [python, str(Path(__file__).with_name("_source_caller_job_fixture.py")), json.dumps(request)],
                cwd=root, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                require_job=True, allow_breakaway=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            out, error = caller.communicate(timeout=330)
            assert caller.returncode == 0, out + error
            result = json.loads(Path(request["result"]).read_text(encoding="utf-8"))
            phases.append({"phase": "enabled-install", "seconds": time.monotonic() - started, "exit": result["installer_exit"]})
            assert result["installer_exit"] == 0, Path(request["stderr"]).read_text(encoding="utf-8")[-16000:]
            ready = prepared(generation)
            assert ready
            master = _NativeProcess(ready["generation"]["pid"], ready["birth"])
            pins.append(master)
            current = endpoint()
            coordinator = _NativeProcess(current.pid, "windows:" + current.process_start_time)
            pins.append(coordinator)
            bounded = _NativeProcess(result["bounded_pid"], result["bounded_birth"])
            pins.append(bounded)
            assert master.running() and coordinator.running()
            assert not master.in_job(caller_job) and not coordinator.in_job(caller_job)
            assert bounded.in_job(caller_job)
            caller_job.close()
            caller_job = None
            until = time.monotonic() + 10
            while bounded.running():
                assert time.monotonic() < until
                time.sleep(0.05)
            assert master.running() and coordinator.running()
            proof["initial_master_completed_cycle"] = status(ready)
            proof["initial_master"] = ready
            proof["both_services_survived_all_caller_jobs"] = result["inner_job_closed"]
            with DispatchClient(current.base_url, token=token, control_token=control) as api:
                held = api.create("enabled supervisor held fixture", repo="example.com/acme/widget")
                assert api.claim(worker_id="fixture-held", repo="example.com/acme/widget", lease_seconds=3600)["id"] == held["id"]
                queued = api.create("enabled supervisor reserved fixture", repo="example.com/acme/widget")
                reservation = api.reserve_spawn(queued["id"])
                before = api.get(held["id"])
            data = root / "data" / "unfinished.json"
            data.write_text('{"unfinished":true}', encoding="utf-8")
            repair = slot.with_name(generation + ".repair." + uuid.uuid4().hex)
            shutil.copytree(application, repair)
            started = time.monotonic()
            incoming_install = subprocess.Popen(
                [host, "-NoProfile", "-File", str(repair / "install.ps1")], cwd=profile,
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            overlap = None
            while incoming_install.poll() is None:
                candidate = prepared(repair.name)
                if candidate is not None and master.running():
                    lock = lock_path_for(root / "control", candidate["scope"])
                    if is_locked(lock) and read_holder_pid(lock) == ready["generation"]["pid"]:
                        overlap = candidate
                        break
                assert time.monotonic() - started < 300
                time.sleep(0.02)
            out, error = incoming_install.communicate(timeout=max(0.1, 300 - (time.monotonic() - started)))
            phases.append({"phase": "enabled-handoff", "seconds": time.monotonic() - started, "exit": incoming_install.returncode})
            (evidence / f"{component}-handoff.stderr.log").write_text(error, encoding="utf-8")
            (evidence / f"{component}-handoff.stdout.log").write_text(out, encoding="utf-8")
            assert incoming_install.returncode == 0, error[-16000:]
            assert overlap, "incoming application preparation was not independent of old ownership"
            replacement = prepared(repair.name)
            assert replacement and replacement["generation"]["pid"] != ready["generation"]["pid"]
            successor = _NativeProcess(replacement["generation"]["pid"], replacement["birth"])
            pins.append(successor)
            assert successor.running()
            proof["replacement_master_completed_cycle"] = status(replacement)
            until = time.monotonic() + 30
            while master.running():
                assert time.monotonic() < until
                time.sleep(0.05)
            assert master.exit_code() == 42
            proof["independent_preparation_while_old_singleton_held"] = overlap
            proof["replacement_master"] = replacement
            proof["outgoing_master_native_exit"] = 42
            current = endpoint()
            new_coordinator = _NativeProcess(current.pid, "windows:" + current.process_start_time)
            pins.append(new_coordinator)
            with DispatchClient(current.base_url, token=token, control_token=control) as api:
                assert api.get(held["id"]) == before
                retained = api.reserve_spawn(queued["id"])
                assert retained["reserved"] is False
                assert retained["reservation"] == reservation["reservation"]
            assert data.read_text(encoding="utf-8") == '{"unfinished":true}'
            proof["held_work_preserved"] = True
            if component == "agent-dispatch":
                from agent_procutil._daemon_instance_identity import stop_identity

                stale = root / "run" / "supervisor-candidates" / (replacement["instance_id"] + ".json")
                original_record = stale.read_bytes()
                assert stop_identity(replacement["generation"]["pid"], replacement["birth"])
                assert not successor.running()
                assert stale.read_bytes() == original_record
                invoke("stale-record-restart", repair / "install.ps1")
                restarted = prepared(repair.name)
                assert restarted and restarted["instance_id"] != replacement["instance_id"]
                restart_pin = _NativeProcess(restarted["generation"]["pid"], restarted["birth"])
                pins.append(restart_pin)
                assert restart_pin.running()
                proof["restart_master_completed_cycle"] = status(restarted)
                assert stale.read_bytes() == original_record
                with DispatchClient(current.base_url, token=token, control_token=control) as api:
                    assert api.get(held["id"]) == before
                    assert api.reserve_spawn(queued["id"])["reservation"] == reservation["reservation"]
                proof["stale_record_restart"] = {
                    "confirmed_exited": replacement, "native_exit": successor.exit_code(),
                    "record_retained_unchanged": True, "ready_replacement": restarted,
                }
            if component == "dispatch-service":
                for original in (snapshot / "source", Path(os.environ["DISPATCH_C_BUILD_ROOT"]) / "source"):
                    destination = original.with_name(original.name + ".hidden-" + uuid.uuid4().hex)
                    original.rename(destination)
                    hidden.append((original, destination))
                global_entry = profile / ".local" / "bin" / "agent-dispatch-service.ps1"
                version = invoke("without-source-version", global_entry, ["--version"])
                assert repair.name in "".join(version.stdout.split())
                health = invoke("without-source-health", global_entry, ["health"])
                assert json.loads(health.stdout)["slot"]["pid"] == current.pid
                assert successor.running()
                proof["standalone_runtime_without_plugin_or_source_staging"] = True
    finally:
        if incoming_install is not None and incoming_install.poll() is None:
            incoming_install.kill()
            incoming_install.wait(timeout=5)
        if caller is not None and caller.poll() is None:
            caller.kill()
            caller.wait(timeout=5)
        if caller_job is not None:
            caller_job.close()
        for original, destination in reversed(hidden):
            destination.rename(original)
        proof["phases"], proof["cleanup"] = phases, [asdict(item) for item in cleanup]
        proof["all_retained_native_handles_exited"] = all(not pin.running() for pin in pins)
        for pin in pins:
            pin.close()
        (evidence / f"{component}-enabled-native-proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    assert cleanup and all(item.status in {"settled", "missing"} for item in cleanup)
    assert proof["all_retained_native_handles_exited"]
