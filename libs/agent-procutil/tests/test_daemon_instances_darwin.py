"""Native Darwin reconciliation, using only private fixture inventories."""

import ctypes
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_procutil import _daemon_instance_identity as identity
from agent_procutil import _daemon_instance_store as store
from agent_procutil.daemon_instances import DaemonInstanceSpec, DaemonLeasePolicy


@pytest.mark.parametrize("error,expected", [
    (errno.ESRCH, "dead"), (errno.EPERM, "unknown"), (0, "unknown"),
])
def test_darwin_libproc_only_explicit_absence_is_dead(monkeypatch, error, expected):
    class Query:
        def __call__(self, *_args):
            ctypes.set_errno(error)
            return 0

    monkeypatch.setattr(identity, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(identity.sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_kw: SimpleNamespace(proc_pidinfo=Query()))
    assert identity.identity_state(123, "darwin:1:2") == expected


@pytest.fixture
def native_helpers(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("actual native macOS reconciliation")
    package = Path(store.__file__).parent
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    for name in ("daemon_instances.sh", "daemon_instance_runtime.pyz"):
        shutil.copyfile(package / name, helpers / name)
        (helpers / name).chmod(0o644)
    return helpers


def _reconcile(helpers, registry, *, shell="/bin/bash"):
    environment = dict(os.environ, COPILOT_EXTENSIONS_DAEMON_PYTHON=sys.executable)
    return subprocess.run(
        [shell, "-c", '. "$1"; reconcile_daemon_instances 128 5 "$2"',
         "fixture-reconcile", str(helpers / "daemon_instances.sh"), str(registry)],
        env=environment, capture_output=True, text=True, timeout=15,
    )


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("shell", list(dict.fromkeys(filter(None, ("/bin/bash", shutil.which("bash"))))))
def test_missing_and_empty_inventory_are_warning_free_and_unchanged(
    native_helpers, tmp_path, exists, shell,
):
    registry = tmp_path / "registry"
    if exists:
        registry.mkdir()
    result = _reconcile(native_helpers, registry, shell=shell)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert registry.exists() == exists
    if exists:
        assert list(registry.iterdir()) == []


@pytest.mark.parametrize("lifecycle", ["resident", "finite-worker"])
def test_native_live_and_exited_records_preserve_production_boundary(
    native_helpers, tmp_path, lifecycle,
):
    registry = tmp_path / "registry"
    receipt = store.reserve(DaemonInstanceSpec(
        "fixture", tmp_path, Path(__file__).resolve(),
        registry_root=registry, lifecycle=lifecycle,
    ))
    proof = tmp_path / "undeclared-proof"
    proof.write_text("retain", encoding="utf-8")
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        birth = identity.birth_identity(child.pid)
        assert birth.startswith("darwin:")
        store.bind_process(receipt, child.pid, observed_birth=birth)
        before = receipt.record_path.read_bytes()
        result = _reconcile(native_helpers, registry)
        assert result.returncode == 0 and not result.stderr, result.stderr
        assert receipt.record_path.read_bytes() == before
        assert child.poll() is None
    finally:
        child.communicate(timeout=5)
    assert identity.identity_state(child.pid, birth) == "dead"
    result = _reconcile(native_helpers, registry)
    assert result.returncode == 0 and not result.stderr, result.stderr
    if lifecycle == "finite-worker":
        assert "finite-worker-exit-observed" in result.stdout
        assert not receipt.record_path.exists()
    else:
        assert receipt.record_path.read_bytes() == before
    assert receipt.record_path.with_suffix(".lock").is_file()
    assert proof.read_text(encoding="utf-8") == "retain"


@pytest.mark.parametrize("condition,reason", [
    ("reused", "process-reused"), ("protected", "protected-work"),
])
def test_uncertain_or_protected_native_record_is_held_without_signaling(
    native_helpers, tmp_path, condition, reason,
):
    registry = tmp_path / "registry"
    receipt = store.reserve(DaemonInstanceSpec(
        "fixture", tmp_path, Path(__file__).resolve(),
        registry_root=registry, lifecycle="finite-worker",
    ))
    store.bind_process(receipt, os.getpid())
    with store._locked(receipt) as path:
        record = store._read(path)
        if condition == "reused":
            record["birth"] = "darwin:0:0"
        else:
            record["protected"] = "1"
        store._write(path, record)
    before = receipt.record_path.read_bytes()
    result = _reconcile(native_helpers, registry)
    assert result.returncode == 1
    assert reason in result.stderr and "held" in result.stdout
    assert receipt.record_path.read_bytes() == before


@pytest.mark.parametrize("condition", ["linked", "writable"])
def test_untrusted_archive_refuses_before_inventory_access(native_helpers, tmp_path, condition):
    archive = native_helpers / "daemon_instance_runtime.pyz"
    if condition == "linked":
        retained = archive.with_suffix(".retained")
        archive.rename(retained)
        archive.symlink_to(retained)
    else:
        archive.chmod(0o666)
    registry = tmp_path / "registry"
    result = _reconcile(native_helpers, registry)
    assert result.returncode == 1 and "custody unavailable" in result.stderr
    assert not registry.exists()


def test_invalid_inventory_control_reports_real_error(native_helpers, tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    cursor = registry / ".reconcile.cursor"
    cursor.write_bytes(b"invalid")
    result = _reconcile(native_helpers, registry)
    assert result.returncode == 1 and "invalid inventory cursor" in result.stderr
    assert cursor.read_bytes() == b"invalid"


def test_expired_live_darwin_process_is_not_signaled(native_helpers, tmp_path):
    registry = tmp_path / "registry"
    receipt = store.reserve(DaemonInstanceSpec(
        "fixture", tmp_path, Path(__file__).resolve(), purpose="unattended-test",
        registry_root=registry, lease=DaemonLeasePolicy(.01, .01),
    ))
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        store.bind_process(receipt, child.pid)
        time.sleep(.02)
        result = _reconcile(native_helpers, registry)
        assert result.returncode == 1 and "stop-unconfirmed" in result.stderr
        assert child.poll() is None
        assert receipt.record_path.is_file()
    finally:
        child.communicate(timeout=5)


def test_actual_installer_adapter_has_no_darwin_warning(native_helpers, tmp_path):
    wrapper = Path(__file__).resolve().parents[3] / "libs/installer-engine/installer-instance-reconcile.sh"
    registry = tmp_path / "absent"
    result = subprocess.run(
        ["/bin/bash", "-c", '. "$1"; reconcile_installer_instances "$2" "$3"',
         "fixture-installer", str(wrapper), str(native_helpers), str(registry)],
        env=dict(os.environ, COPILOT_EXTENSIONS_DAEMON_PYTHON=sys.executable),
        capture_output=True, text=True, timeout=15,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not registry.exists()
