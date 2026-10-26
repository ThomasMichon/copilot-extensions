"""Actual prebuilt native Linux protocol; explicit source build precedes tests."""

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from agent_procutil import _daemon_instance_store as store
from agent_procutil.daemon_instances import (
    DaemonInstanceSpec, DaemonLeasePolicy, cleanup_instance, inspect_instances,
    reserve_instance, renew_instance,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="requires canonical contained Linux native qualification",
)


@pytest.fixture
def native_helper():
    from agent_procutil._daemon_instance_native import native_helper_path

    try:
        helper = native_helper_path()
    except OSError as error:
        if os.environ.get("PROCUTIL_REQUIRE_NATIVE") == "1":
            pytest.fail(str(error))
        pytest.skip("explicit source build or platform wheel required")
    subprocess.run([str(helper), "check"], check=True, timeout=5)
    return helper


def start_supervised(native_helper, tmp_path, *, code, ttl=.8, cap=3):
    receipt = reserve_instance(DaemonInstanceSpec(
        "native-linux-fixture", tmp_path, Path(__file__).resolve(),
        purpose="unattended-test", registry_root=tmp_path / "registry",
        lease=DaemonLeasePolicy(ttl, cap),
    ))
    process = subprocess.Popen(
        [str(native_helper), "supervise", str(receipt.record_path), "--",
         sys.executable, "-c", code, str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        record = inspect_instances(receipt.registry_root)[0]
        if record.get("state") == "running":
            return process, receipt, record
        if process.poll() is not None:
            pytest.fail(f"native admission exited {process.returncode}: {process.stderr.read()!r}")
        time.sleep(.01)
    process.kill()
    process.communicate(timeout=5)
    pytest.fail("native admission deadline")


def test_native_expiry_reaps_actual_worker_and_grandchild(native_helper, tmp_path):
    code = """
import subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
(Path(sys.argv[1])/'grandchild').write_text(str(child.pid))
time.sleep(30)
"""
    process, receipt, record = start_supervised(native_helper, tmp_path, code=code, ttl=1)
    descriptors = []
    try:
        deadline = time.monotonic() + .8
        while not (tmp_path / "grandchild").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (tmp_path / "grandchild").exists()
        grandchild = int((tmp_path / "grandchild").read_text())
        descriptors = [os.pidfd_open(int(record["pid"])), os.pidfd_open(grandchild)]
        process.communicate(timeout=6)
        from agent_procutil._caller_owned import _pidfd_exited
        assert all(_pidfd_exited(fd) for fd in descriptors)
        assert cleanup_instance(receipt).status == "settled"
        assert (tmp_path / "grandchild").exists(), "undeclared proof is preserved"
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=6)
        for descriptor in descriptors:
            os.close(descriptor)


def test_native_renewal_obeys_hard_cap(native_helper, tmp_path):
    process, receipt, record = start_supervised(
        native_helper, tmp_path, code="import time;time.sleep(30)", ttl=.8, cap=2,
    )
    start = time.monotonic()
    try:
        while process.poll() is None and time.monotonic()-start < 5:
            try:
                renew_instance(receipt)
            except ValueError:
                break
            time.sleep(.1)
        process.communicate(timeout=6)
        assert time.monotonic()-start < 5
        assert float(record["hard_deadline"])-float(record["created"]) == pytest.approx(2)
        assert cleanup_instance(receipt).status == "settled"
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=6)


def test_native_cleanup_removes_only_declared_artifact(native_helper, tmp_path):
    code = """
import time
time.sleep(30)
"""
    process, receipt, record = start_supervised(native_helper, tmp_path, code=code, ttl=1, cap=3)
    artifact = tmp_path / "daemon.pid"
    proof = tmp_path / "proof.txt"
    artifact.write_text("owned")
    proof.write_text("retain")
    from agent_procutil._daemon_instance_identity import path_identity
    device, inode = path_identity(artifact)
    try:
        with store._locked(receipt) as path:
            row = store._read(path)
            row.update(
                artifact_count="1", artifact_0_path=str(artifact),
                artifact_0_device=device, artifact_0_inode=inode,
            )
            store._write(path, row)
        process.communicate(timeout=6)
        result = subprocess.run(
            [str(native_helper), "cleanup", str(receipt.record_path), "3"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("settled\t")
        assert not artifact.exists()
        assert proof.read_text() == "retain"
        assert Path(__file__).exists()
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=6)


def test_automatic_source_follows_real_child_path_not_parent_path(tmp_path, monkeypatch):
    from agent_procutil import spawn_daemon

    parent_bin, child_bin, working = (tmp_path / name for name in ("parent", "child", "work"))
    for directory in (parent_bin, child_bin, working):
        directory.mkdir()
    marker = tmp_path / "executed"
    for directory, label in ((parent_bin, "parent"), (child_bin, "child")):
        target = directory / "service-fixture"
        target.write_text(f"#!/bin/sh\nprintf '{label}' > '{marker}'\nexec /bin/sleep 30\n")
        target.chmod(0o755)
    monkeypatch.setenv("PATH", str(parent_bin))
    process = spawn_daemon(
        ["service-fixture"], cwd=working, log_path=tmp_path / "path.log",
        env={"PATH": str(child_bin)}, breakaway=False,
    )
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.read_text() == "child"
        row = inspect_instances(process.instance_receipt.registry_root)[0]
        assert row["source"] == str((child_bin / "service-fixture").resolve())
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        cleanup_instance(process.instance_receipt, terminate=False)


def test_native_finite_production_cleanup_preserves_artifacts(native_helper, tmp_path):
    from agent_procutil.daemon_instances import spawn_finite_worker

    artifact = tmp_path / "production-output"
    code = "from pathlib import Path;import sys;Path(sys.argv[1]).write_text('retain')"
    process = spawn_finite_worker(
        [sys.executable, "-c", code, str(artifact)],
        instance=DaemonInstanceSpec(
            "finite-native", tmp_path, Path(__file__).resolve(),
            registry_root=tmp_path / "registry",
        ),
        cwd=tmp_path, log_path=tmp_path / "finite.log", breakaway=False,
    )
    try:
        process.wait(timeout=8)
        row = inspect_instances(process.instance_receipt.registry_root)[0]
        assert row["state"] == "finished" and row["deadline"] == "0"
        result = subprocess.run(
            [str(native_helper), "cleanup", str(process.instance_receipt.record_path), "3"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0, result.stderr
        assert "finite-worker-exit-observed" in result.stdout
        assert artifact.read_text() == "retain"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: 0)() == 0,
    reason="permission refusal requires an unprivileged user",
)
@pytest.mark.parametrize("hidden_ancestor", [False, True])
@pytest.mark.parametrize("backend", ["bash", "python-inspect", "python-reconcile"])
def test_native_inventory_permission_failure_is_not_reported_empty(tmp_path, hidden_ancestor, backend):
    import agent_procutil

    helper = Path(agent_procutil.__file__).parent / "daemon_instances.sh"
    ancestor = tmp_path / "parent"
    registry = ancestor / "inventory"
    registry.mkdir(parents=True)
    canary = registry / ("a" * 32 + ".instance")
    canary.write_text("retained-canary")
    protected = ancestor if hidden_ancestor else registry
    protected.chmod(0)
    try:
        if backend == "bash":
            result = subprocess.run(
                ["bash", "-c", '. "$1"; reconcile_daemon_instances 128 5 "$2"',
                 "permission-probe", str(helper), str(registry)],
                capture_output=True, text=True, timeout=5,
            )
            assert result.returncode != 0
            assert "inventory" in result.stderr and "unreadable" in result.stderr
        else:
            from agent_procutil.daemon_instances import reconcile_instances
            with pytest.raises(PermissionError):
                if backend == "python-inspect":
                    inspect_instances(registry)
                else:
                    reconcile_instances(registry_root=registry, limit=128, max_seconds=5)
    finally:
        protected.chmod(0o700)
    assert canary.read_text() == "retained-canary"


@pytest.mark.parametrize("kind", ["relative", "non-normalized", "broken-root", "broken-parent"])
def test_native_inventory_unsafe_absence_is_not_reported_empty(tmp_path, kind):
    import agent_procutil

    helper = Path(agent_procutil.__file__).parent / "daemon_instances.sh"
    if kind == "relative":
        root = "relative-missing"
    elif kind == "non-normalized":
        root = str(tmp_path) + "/unused/../missing"
    elif kind == "broken-root":
        link = tmp_path / "broken"
        link.symlink_to(tmp_path / "missing")
        root = str(link)
    else:
        link = tmp_path / "broken"
        link.symlink_to(tmp_path / "missing", target_is_directory=True)
        root = str(link / "inventory")
    result = subprocess.run(
        ["bash", "-c", '. "$1"; reconcile_daemon_instances 128 5 "$2"',
         "unsafe-probe", str(helper), root],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode != 0
    assert "path unsafe" in result.stderr


@pytest.mark.parametrize("operation", ["inspect", "reconcile"])
def test_python_dangling_inventory_root_is_not_missing(tmp_path, operation):
    from agent_procutil.daemon_instances import reconcile_instances

    root = tmp_path / "dangling-inventory"
    root.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    with pytest.raises(OSError):
        if operation == "inspect":
            inspect_instances(root)
        else:
            reconcile_instances(registry_root=root, limit=128, max_seconds=5)
