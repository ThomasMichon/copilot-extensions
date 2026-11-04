"""Installed Source survival after actual nested caller-Job closure, opt-in only."""

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

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows caller Jobs")


class _NativeProcess:
    def __init__(self, pid, birth):
        import ctypes
        from ctypes import wintypes
        from agent_procutil._daemon_instance_identity import _windows_birth

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.api.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self.handle = self.api.OpenProcess(0x00100000 | 0x1000, False, pid)
        assert self.handle, "owned native process is unavailable"
        if _windows_birth(self.handle) != birth:
            self.close()
            raise AssertionError("owned native birth identity changed")

    def running(self):
        result = self.api.WaitForSingleObject(self.handle, 0)
        assert result in (0, 0x102)
        return result == 0x102

    def in_job(self, job):
        import ctypes
        from ctypes import wintypes

        member = wintypes.BOOL()
        assert self.api.IsProcessInJob(self.handle, job.handle, ctypes.byref(member))
        return bool(member.value)

    def close(self):
        self.api.CloseHandle(self.handle)

    def exit_code(self):
        import ctypes
        from ctypes import wintypes

        assert not self.running()
        code = wintypes.DWORD()
        assert self.api.GetExitCodeProcess(self.handle, ctypes.byref(code))
        return code.value


@pytest.mark.parametrize("outer_allows_breakaway", [True, False], ids=["survival", "strict-outer"])
def test_generated_source_outlives_exact_caller_jobs(tmp_path, outer_allows_breakaway):
    source = os.environ.get("DISPATCH_QUALIFIED_SOURCE_SNAPSHOT")
    root_name = os.environ.get("DISPATCH_CALLER_JOB_NATIVE_ROOT")
    evidence_name = os.environ.get("DISPATCH_SOURCE_EVIDENCE_ROOT")
    if not source or not root_name or not evidence_name:
        pytest.skip("explicit admitted Source, private compact root and evidence destination required")
    from agent_procutil import direct_python, spawn_sync_in_kill_on_close_job
    from agent_procutil.daemon_instances import daemon_test_lifecycle
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.cutover_process import _identity_running
    from zdd import diagnostics, routing

    mode = "survival" if outer_allows_breakaway else "strict-outer"
    root = Path(root_name) / ("p" if outer_allows_breakaway else "n")
    evidence = Path(evidence_name)
    record = json.loads((Path(source) / "install-source.json").read_text(encoding="utf-8"))
    application = Path(source) / record["projects"]["agent-dispatch"]["application"]
    declaration = json.loads((application / "application.json").read_text(encoding="utf-8"))
    generation = declaration["construction"]["runtime_version"]
    slot = root / "versions" / generation
    shutil.copytree(application, slot)
    profile = root / "profile"
    profile.mkdir()
    (profile / "uv.toml").write_text("", encoding="utf-8")
    token, control = uuid.uuid4().hex, uuid.uuid4().hex
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("AGENT_DISPATCH") and key not in {
            "PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT", "COPILOT_AGENT_SESSION_ID",
            "UV_NO_CONFIG", "UV_CONFIG_FILE",
        }
    }
    environment.update({
        "HOME": str(profile), "USERPROFILE": str(profile),
        "AGENT_DISPATCH_INSTALL_DIR": str(root),
        "AGENT_DISPATCH_RUN_DIR": str(root / "run"),
        "AGENT_DISPATCH_ROUTING_DIR": str(root / "routes"),
        "AGENT_DISPATCH_DB": str(root / "data" / "tasks.db"),
        "AGENT_DISPATCH_HOST": "127.0.0.1",
        "AGENT_DISPATCH_TOKEN": token, "AGENT_DISPATCH_CONTROL_TOKEN": control,
        "AGENT_DISPATCH_GC_INTERVAL": "0", "AGENT_DISPATCH_SELF_UPDATE": "0",
        "UV_CONFIG_FILE": str(profile / "uv.toml"),
        "UV_PYTHON_INSTALL_DIR": str(root / "python"),
        "UV_CACHE_DIR": str(root / "cache" / "uv"),
        # This one explicit native fixture admits separately leased residents.
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "0",
    })
    host = shutil.which("pwsh")
    assert host, "admitted native fixture requires PowerShell 7"
    request = {
        "root": str(root),
        "command": [host, "-NoProfile", "-File", str(slot / "install.ps1")],
        "pending": str(evidence / f"caller-job-{mode}-pending.json"),
        "result": str(evidence / f"caller-job-{mode}-result.json"),
        "stdout": str(evidence / f"caller-job-{mode}.stdout.log"),
        "stderr": str(evidence / f"caller-job-{mode}.stderr.log"),
    }
    python, overrides = direct_python(sys.executable)
    environment.update(overrides)
    cleanup = []
    pins = []
    caller = job = endpoint = None
    proof = {"mode": mode, "source": record["source_commit"], "generation": generation}
    started = time.monotonic()
    try:
        with daemon_test_lifecycle(
            owner="dispatch-native-caller-jobs", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as overlay:
            environment.update(overlay)
            try:
                caller, job = spawn_sync_in_kill_on_close_job(
                    [python, str(Path(__file__).with_name("_source_caller_job_fixture.py")), json.dumps(request)],
                    cwd=root, env=environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    require_job=True, allow_breakaway=outer_allows_breakaway,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                stdout, stderr = caller.communicate(timeout=330)
                assert caller.returncode == 0, stdout + stderr
                result = json.loads(Path(request["result"]).read_text(encoding="utf-8"))
                proof.update(result)
                assert result["inner_job_closed"] is True
                bounded = _NativeProcess(result["bounded_pid"], result["bounded_birth"])
                pins.append(bounded)
                assert bounded.running() and bounded.in_job(job)
                if not outer_allows_breakaway:
                    assert result["installer_exit"] != 0, "strict outer Job unexpectedly admitted a resident"
                    error = Path(request["stderr"]).read_text(encoding="utf-8")
                    assert (
                        ("DaemonSpawnError" in error and "breakaway" in error)
                        or "native daemon launch could not escape launcher-owned cleanup Jobs" in error
                    ), (
                        "strict-outer control did not reach the native lifetime refusal"
                    )
                    assert "ApplicationStartupUncertain" in error
                    selected_marker = (root / "current-version").read_text(encoding="utf-8").strip()
                    last_good = root / "last-known-good"
                    observed_route = (routing.read_table(root / "routes") or {}).get("active")
                    proof["post_startup_failure"] = {
                        "selected": selected_marker,
                        "last_known_good": last_good.read_text(encoding="utf-8").strip() if last_good.exists() else None,
                        "active_route": observed_route,
                        "outcome": "unknown; retained without rollback or replay",
                    }
                    assert selected_marker == generation
                    assert not last_good.exists()
                    assert observed_route is None
                else:
                    assert result["installer_exit"] == 0, Path(request["stderr"]).read_text(encoding="utf-8")
                    endpoint = routing.Endpoint.from_dict(routing.read_table(root / "routes")["active"])
                    proof["resident"] = endpoint.to_dict()
                    assert endpoint.version == generation
                    assert diagnostics.process_start_time(endpoint.pid) == endpoint.process_start_time
                    assert _identity_running(endpoint.pid, endpoint.process_start_time) is True
                    resident = _NativeProcess(endpoint.pid, "windows:" + endpoint.process_start_time)
                    pins.append(resident)
                    assert resident.running() and not resident.in_job(job)
                job.close()
                job = None
                proof["outer_job_closed"] = True
                until = time.monotonic() + 10
                while bounded.running():
                    assert time.monotonic() < until, "bounded control survived its strict caller lifetime"
                    time.sleep(0.05)
                proof["bounded_exit_after_outer_close"] = True
                if endpoint is not None:
                    with DispatchClient(endpoint.base_url, token=token, control_token=control) as client:
                        for _ in range(2):
                            assert resident.running()
                            assert _identity_running(endpoint.pid, endpoint.process_start_time) is True
                            health = client.health()
                            assert health["slot"]["pid"] == endpoint.pid
                            assert health["slot"]["process_start_time"] == endpoint.process_start_time
                            assert health["slot"]["role"] == "active"
                            assert health["version"] == generation
                            proof.setdefault("post_caller_job_health", []).append({
                                "pid": health["slot"]["pid"],
                                "birth": health["slot"]["process_start_time"],
                                "role": health["slot"]["role"], "version": health["version"],
                            })
                            time.sleep(0.1)
                    proof["same_native_owner_survived_all_fixture_caller_jobs"] = True
            finally:
                if caller is not None and caller.poll() is None:
                    caller.kill()
                    caller.wait(timeout=5)
                if job is not None:
                    job.close()
    finally:
        proof["seconds"] = time.monotonic() - started
        proof["cleanup"] = [asdict(item) for item in cleanup]
        if endpoint is not None:
            proof["final_native_exit"] = _identity_running(endpoint.pid, endpoint.process_start_time) is False
            proof["retained_native_handle_signaled_exit"] = not resident.running()
        for pin in pins:
            pin.close()
        (evidence / f"caller-job-{mode}-proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    assert cleanup and all(item.status in {"settled", "missing"} for item in cleanup)
    if endpoint is not None:
        assert proof["final_native_exit"] is True


def test_bounded_control_is_admitted_and_dies_with_strict_outer_job(tmp_path):
    root_name = os.environ.get("DISPATCH_CALLER_JOB_NATIVE_ROOT")
    evidence_name = os.environ.get("DISPATCH_SOURCE_EVIDENCE_ROOT")
    if not root_name or not evidence_name:
        pytest.skip("explicit private native fixture and evidence roots required")
    from agent_procutil import direct_python, spawn_sync_in_kill_on_close_job
    from agent_procutil.daemon_instances import daemon_test_lifecycle

    root = Path(root_name) / "b"
    root.mkdir()
    evidence = Path(evidence_name)
    python, overrides = direct_python(sys.executable)
    request = {
        "root": str(root), "command": [python, "-I", "-S", "-c", "pass"],
        "pending": str(evidence / "caller-bounded-pending.json"),
        "result": str(evidence / "caller-bounded-result.json"),
        "stdout": str(evidence / "caller-bounded.stdout.log"),
        "stderr": str(evidence / "caller-bounded.stderr.log"),
    }
    cleanup = []
    caller = job = pin = None
    proof = {}
    try:
        with daemon_test_lifecycle(
            owner="dispatch-bounded-caller-control", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as overlay:
            environment = {
                **os.environ, **overrides, **overlay, "COPILOT_EXTENSIONS_TEST_CONTAINED": "0",
            }
            try:
                caller, job = spawn_sync_in_kill_on_close_job(
                    [python, str(Path(__file__).with_name("_source_caller_job_fixture.py")), json.dumps(request)],
                    cwd=root, env=environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    require_job=True, allow_breakaway=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                stdout, stderr = caller.communicate(timeout=30)
                assert caller.returncode == 0, stdout + stderr
                proof = json.loads(Path(request["result"]).read_text(encoding="utf-8"))
                proof["scenario"] = "bounded-control-no-application"
                assert proof["installer_exit"] == 0 and proof["inner_job_closed"]
                pin = _NativeProcess(proof["bounded_pid"], proof["bounded_birth"])
                assert pin.running() and pin.in_job(job)
                job.close()
                job = None
                proof["outer_job_closed"] = True
                until = time.monotonic() + 10
                while pin.running():
                    assert time.monotonic() < until, "bounded control escaped its caller"
                    time.sleep(0.05)
                proof["retained_handle_confirmed_exit"] = True
            finally:
                if caller is not None and caller.poll() is None:
                    caller.kill()
                    caller.wait(timeout=5)
                if job is not None:
                    job.close()
    finally:
        if pin is not None:
            pin.close()
        proof["cleanup"] = [asdict(item) for item in cleanup]
        (evidence / "caller-bounded-proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    assert cleanup and all(item.status in {"settled", "missing"} for item in cleanup)
