"""Contained real-process qualification; never use an escaped host daemon."""

import os
import json
import re
from pathlib import Path
import subprocess
import sys
import time

import pytest

from agent_procutil import no_window_kwargs, spawn_daemon
from agent_procutil.daemon_instances import (
    DaemonInstanceSpec, DaemonLeasePolicy, cleanup_instance, inspect_instances,
    renew_instance, spawn_instance,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="requires the canonical process-tree containment owner",
)


def test_production_source_ready_and_nonexpiry(tmp_path):
    ready = tmp_path / "ready"
    code = """
import sys,time
from pathlib import Path
from agent_procutil.daemon_instances import admit_daemon_instance
with admit_daemon_instance('production-fixture'):
    Path(sys.argv[1]).write_text('ready')
    time.sleep(30)
"""
    process = spawn_instance(
        [sys.executable, "-B", "-c", code, str(ready)],
        instance=DaemonInstanceSpec(
            "production-fixture", tmp_path, Path(__file__).resolve(),
            registry_root=tmp_path / "registry",
        ),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    try:
        until = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < until:
            time.sleep(.01)
        assert ready.exists(), (process.poll(), process.stderr.read())
        rows = inspect_instances(tmp_path / "registry")
        assert rows[0]["purpose"] == "production"
        assert rows[0]["deadline"] == "0"
        time.sleep(1)
        assert process.poll() is None
        assert cleanup_instance(process.instance_receipt).status == "live"
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)
        assert cleanup_instance(process.instance_receipt).status == "settled"


def test_source_python_instance_expires_without_launcher_renewal(tmp_path):
    proof = tmp_path / "proof.txt"
    proof.write_text("retain")
    registry = tmp_path / "outside-runtime"
    runtime = tmp_path / "isolated-runtime"
    runtime.mkdir()
    process = spawn_daemon(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=runtime, log_path=runtime / "daemon.log", breakaway=False,
        instance=DaemonInstanceSpec(
            "source-fixture", runtime, Path(__file__).resolve(),
            purpose="unattended-test", registry_root=registry,
            lease=DaemonLeasePolicy(.7, 3),
        ),
    )
    try:
        receipt = process.instance_receipt
        records = inspect_instances(registry)
        assert records[0]["pid"] == str(process.pid)
        assert records[0]["source"] == str(Path(__file__).resolve())
        process.wait(timeout=8)
        assert cleanup_instance(receipt).status == "settled"
        assert proof.read_text() == "retain"
        assert (runtime / "daemon.log").exists(), "undeclared logs are not GC artifacts"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_owner_renewal_cannot_extend_absolute_lifetime(tmp_path):
    process = spawn_daemon(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path, log_path=tmp_path / "daemon.log", breakaway=False,
        instance=DaemonInstanceSpec(
            "hard-cap-fixture", tmp_path, Path(__file__).resolve(),
            purpose="unattended-test", registry_root=tmp_path / "registry",
            lease=DaemonLeasePolicy(.7, 2),
        ),
    )
    start = time.monotonic()
    try:
        while process.poll() is None and time.monotonic() - start < 5:
            try:
                renew_instance(process.instance_receipt)
            except ValueError:
                break
            time.sleep(.1)
        process.wait(timeout=5)
        assert time.monotonic() - start < 5
        assert cleanup_instance(process.instance_receipt).status == "settled"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="native pinned Windows observer")
def test_child_expiry_survives_abrupt_launcher_disappearance(tmp_path):
    launcher = """
import json, os, sys
from pathlib import Path
from agent_procutil import spawn_daemon
from agent_procutil.daemon_instances import DaemonInstanceSpec, DaemonLeasePolicy
root=Path(sys.argv[1])
p=spawn_daemon([sys.executable, '-c', 'import time; time.sleep(30)'],
 cwd=root,log_path=root/'child.log',breakaway=False,
 instance=DaemonInstanceSpec('owner-loss',root,Path(sys.argv[2]),
 purpose='unattended-test',registry_root=root/'registry',lease=DaemonLeasePolicy(2,4)))
(root/'receipt.json').write_text(json.dumps({'pid':p.pid,'id':p.instance_receipt.instance_id}))
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", launcher, str(tmp_path), str(Path(__file__).resolve())],
        capture_output=True, timeout=8, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    metadata = json.loads((tmp_path / "receipt.json").read_text())
    from agent_procutil.daemon_broker import WindowsDaemonProcess
    from agent_procutil.daemon_instances import DaemonInstanceReceipt
    from agent_procutil._daemon_instance_identity import identity_state
    records = inspect_instances(tmp_path / "registry")
    assert records[0]["owner_pid"] != str(os.getpid())
    observer = WindowsDaemonProcess(metadata["pid"], ["owner-loss-fixture"])
    try:
        assert identity_state(metadata["pid"], records[0]["birth"]) in ("matching", "dead")
        observer.wait(timeout=8)
    finally:
        if observer.poll() is None:
            observer.kill()
        observer.close()
    assert cleanup_instance(DaemonInstanceReceipt(metadata["id"], tmp_path / "registry")).status == "settled"


@pytest.mark.skipif(os.name != "nt", reason="stock Windows native binding")
def test_python_free_native_protocol_and_expiry(tmp_path):
    helper = Path(__file__).parents[1] / "src" / "agent_procutil" / "daemon_instances.ps1"
    registry = tmp_path / "native-registry"
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    body = (
        f". '{helper}'; "
        f"$guard=Enter-DaemonInstance -Service fixture -RuntimeRoot '{tmp_path}' "
        f"-Source '{helper}' -RegistryRoot '{registry}' -Purpose unattended-test "
        "-TtlSeconds 1 -MaxLifetimeSeconds 3; "
        f"[IO.File]::WriteAllText('{tmp_path / 'scopes.txt'}', $env:COPILOT_EXTENSIONS_OWNED_JOB_SCOPES); "
        "Start-Sleep -Seconds 30"
    )
    process = subprocess.Popen(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command", body],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    try:
        _, error = process.communicate(timeout=12)
        assert process.returncode == 1, error.decode(errors="replace")
        records = inspect_instances(registry)
        assert len(records) == 1
        assert records[0]["purpose"] == "unattended-test"
        assert records[0]["birth"].startswith("windows:")
        before = os.environ.get("COPILOT_EXTENSIONS_OWNED_JOB_SCOPES", "").split(";")
        if before == [""]:
            before = []
        scopes = (tmp_path / "scopes.txt").read_text().split(";")
        assert scopes[:-1] == before
        assert re.fullmatch(r"Local\\ce-owned-job-[0-9a-f]{32}", scopes[-1])
        assert scopes[-1] not in before
        from agent_procutil._daemon_instance_identity import path_identity
        assert (records[0]["runtime_device"], records[0]["runtime_inode"]) == path_identity(tmp_path)
        from agent_procutil.daemon_instances import DaemonInstanceReceipt
        result = cleanup_instance(DaemonInstanceReceipt(records[0]["instance_id"], registry))
        assert result.status == "settled", result
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="native Windows artifact settlement")
def test_native_reconcile_settles_only_exact_python_declared_artifact(tmp_path):
    registry = tmp_path / "registry"
    artifact = tmp_path / "daemon.pid"
    proof = tmp_path / "proof.txt"
    proof.write_text("retain")
    code = """
import sys, time
from pathlib import Path
from agent_procutil.daemon_instances import admit_daemon_instance
guard=admit_daemon_instance('artifact-fixture')
p=Path(sys.argv[1]);p.write_text('owned');guard.add_artifact(p)
time.sleep(.1)
"""
    process = spawn_daemon(
        [sys.executable, "-c", code, str(artifact)],
        cwd=tmp_path, log_path=tmp_path / "daemon.log", breakaway=False,
        instance=DaemonInstanceSpec(
            "artifact-fixture", tmp_path, Path(__file__).resolve(),
            purpose="unattended-test", registry_root=registry,
            lease=DaemonLeasePolicy(.7, 3),
        ),
    )
    try:
        process.wait(timeout=5)
        assert artifact.is_file()
        deadline = float(inspect_instances(registry)[0]["deadline"])
        time.sleep(max(0, deadline - time.time()) + .05)
        helper = Path(__file__).parents[1] / "src" / "agent_procutil" / "daemon_instances.ps1"
        shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        result = subprocess.run(
            [str(shell), "-NoProfile", "-NonInteractive", "-Command",
             f". '{helper}'; Invoke-DaemonInstanceReconcile -RegistryRoot '{registry}' | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=12, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "settled", result.stderr
        assert not artifact.exists()
        assert proof.read_text() == "retain"
        assert Path(__file__).is_file()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize("backend", ["python", "native"])
def test_finite_production_worker_closes_work_and_settles_only_control_record(tmp_path, backend):
    from agent_procutil.daemon_instances import spawn_finite_worker, reconcile_instances

    artifact = tmp_path / "production-result"
    code = """
from pathlib import Path
import sys,time
from agent_procutil.daemon_instances import admit_daemon_instance
with admit_daemon_instance('finite-fixture') as guard:
    path=Path(sys.argv[1]);path.write_text('retain-production-output')
    guard.add_artifact(path)
    time.sleep(.1)
"""
    process = spawn_finite_worker(
        [sys.executable, "-c", code, str(artifact)], breakaway=False,
        cwd=tmp_path, log_path=tmp_path / "finite.log",
        instance=DaemonInstanceSpec(
            "finite-fixture", tmp_path, Path(__file__).resolve(),
            registry_root=tmp_path / "registry",
        ),
    )
    try:
        process.wait(timeout=8)
        row = inspect_instances(tmp_path / "registry")[0]
        assert row["purpose"] == "production"
        assert row["lifecycle"] == "finite-worker"
        assert row["ttl"] == "0" and row["state"] == "finished"
        if backend == "python":
            result = reconcile_instances(registry_root=tmp_path / "registry")
            assert result[0].status == "settled"
        else:
            if os.name != "nt":
                pytest.skip("native PowerShell parity is Windows-only")
            helper = Path(__file__).parents[1] / "src" / "agent_procutil" / "daemon_instances.ps1"
            shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            result = subprocess.run(
                [str(shell), "-NoProfile", "-NonInteractive", "-Command",
                 f". '{helper}'; Invoke-DaemonInstanceReconcile -RegistryRoot '{tmp_path / 'registry'}' | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=12, **no_window_kwargs(),
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["reason"] == "finite-worker-exit-observed"
        assert artifact.read_text() == "retain-production-output"
        assert not process.instance_receipt.record_path.exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_automatic_provenance_names_actual_normalized_python_not_library(tmp_path):
    import agent_procutil as pu

    code = "import time;time.sleep(30)"
    process = pu.spawn_daemon(
        [sys.executable, "-c", code], breakaway=False,
        cwd=tmp_path, log_path=tmp_path / "auto.log",
    )
    try:
        row = inspect_instances(process.instance_receipt.registry_root)[0]
        executable = Path(pu.direct_python(sys.executable)[0]).resolve()
        assert row["source"] == str(executable)
        assert row["source"] != str(Path(pu.__file__).with_name("daemon.py"))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        cleanup_instance(process.instance_receipt, terminate=False)
