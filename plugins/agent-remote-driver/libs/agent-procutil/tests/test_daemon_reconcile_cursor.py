"""Bounded progress metadata is not instance or artifact ownership."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from agent_procutil import _daemon_instance_store as store
from agent_procutil.daemon_instances import (
    DaemonInstanceReceipt, DaemonInstanceSpec, cancel_instance_reservation, reserve_instance,
)


@pytest.fixture
def inventory(tmp_path):
    root = tmp_path / "inventory"
    receipt = reserve_instance(DaemonInstanceSpec(
        "cursor-fixture", tmp_path, Path(__file__).resolve(), registry_root=root,
    ))
    template = store._read(receipt.record_path)
    cancel_instance_reservation(receipt, creation_refused=True)

    def populate(count):
        paths = []
        for index in range(count):
            entry = DaemonInstanceReceipt(f"{index + 1:032x}", root)
            row = dict(template, instance_id=entry.instance_id, protected="1")
            with store._locked(entry) as path:
                store._write(path, row)
            paths.append(entry.record_path)
        return paths
    return root, template, populate


def test_limit_progress_past_production_and_eof_wrap(inventory, monkeypatch):
    root, _, populate = inventory
    paths = populate(130)
    artifact = root.parent / "production-artifact"
    artifact.write_bytes(b"retained production artifact")
    receipt = DaemonInstanceReceipt(paths[0].stem, root)
    with store._locked(receipt) as path:
        row = store._read(path)
        device, inode = store.path_identity(artifact)
        row.update(artifact_count="1", artifact_0_path=str(artifact),
                   artifact_0_device=device, artifact_0_inode=inode)
        store._write(path, row)
    production = {path: path.read_bytes() for path in paths[:-1]}
    with store._locked(DaemonInstanceReceipt(paths[-1].stem, root)) as path:
        row = store._read(path)
        row.update(purpose="unattended-test", protected="0", pid="4321", birth="fixture",
                   deadline="1", hard_deadline="2", ttl="1")
        if sys.platform.startswith("linux"):
            row.update(store.lease_clock.fields(1, 2, 0))
        store._write(path, row)
    monkeypatch.setattr(store, "identity_state", lambda *_: "dead")
    assert store.reconcile(root, 128, 5) == []
    assert store._read_cursor(root) == paths[127].stem
    assert store.reconcile(root, 128, 5)[0].status == "settled"
    assert not paths[-1].exists()
    assert all(path.read_bytes() == data for path, data in production.items())
    assert artifact.read_bytes() == b"retained production artifact"
    assert store._read_cursor(root) < paths[127].stem, "second pass wraps at EOF"


def test_time_budget_resumes_past_every_visited_unknown_row(inventory, monkeypatch):
    root, _, populate = inventory
    paths = populate(5)
    paths[0].write_text("malformed owned record")
    now = [0.0]
    original = store._read
    visited = []

    def read(path):
        visited.append(path.stem)
        now[0] += .6
        return original(path)
    monkeypatch.setattr(store, "_read", read)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: now[0])
    assert store.reconcile(root, 128, 1)[0].status == "held"
    assert store._read_cursor(root) == paths[1].stem
    store.reconcile(root, 128, 1)
    assert visited == [path.stem for path in paths[:4]]
    assert store._read_cursor(root) == paths[3].stem


@pytest.mark.parametrize("contents", [b"", b"garbage\n", b"copilot-daemon-reconcile-v1\nUPPER\n"])
def test_bad_cursor_is_visible_and_untouched_even_empty(inventory, contents):
    root, _, _ = inventory
    marker = root / ".reconcile.cursor"
    marker.write_bytes(contents)
    with pytest.raises(ValueError):
        store.reconcile(root, 128, 5)
    assert marker.read_bytes() == contents


def test_foreign_cursor_is_not_repaired(inventory, monkeypatch):
    root, _, populate = inventory
    populate(1)
    marker = root / ".reconcile.cursor"
    marker.write_bytes(store._CURSOR_HEADER + b"\n")
    original = store._plain

    def plain(path, **kwargs):
        if path == marker:
            raise OSError("foreign cursor owner")
        return original(path, **kwargs)
    monkeypatch.setattr(store, "_plain", plain)
    with pytest.raises(OSError, match="foreign"):
        store.reconcile(root, 128, 5)
    assert marker.read_bytes() == store._CURSOR_HEADER + b"\n"


def test_symlink_cursor_is_not_followed(inventory):
    root, _, populate = inventory
    populate(1)
    target = root / "retained-proof"
    target.write_text("retain")
    try:
        (root / ".reconcile.cursor").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(OSError):
        store.reconcile(root, 128, 5)
    assert target.read_text() == "retain"


def test_empty_and_missing_inventory_create_no_progress_metadata(tmp_path):
    root = tmp_path / "absent"
    assert store.reconcile(root, 128, 5) == []
    assert not root.exists()
    root.mkdir()
    assert store.reconcile(root, 128, 5) == []
    assert list(root.iterdir()) == []


def test_concurrent_collectors_do_not_regress_cursor(inventory, monkeypatch):
    root, _, populate = inventory
    paths = populate(4)
    entered, release = threading.Event(), threading.Event()
    original = store._read

    def read(path):
        if path == paths[0]:
            entered.set()
            assert release.wait(3)
        return original(path)
    monkeypatch.setattr(store, "_read", read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(store.reconcile, root, 2, 5)
        assert entered.wait(3)
        second = pool.submit(store.reconcile, root, 2, 5)
        with pytest.raises(BlockingIOError):
            second.result(timeout=3)
        release.set()
        assert first.result(timeout=3) == []
    assert store._read_cursor(root) == paths[1].stem
    assert store.reconcile(root, 2, 5) == []
    assert store._read_cursor(root) == paths[3].stem


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="actual Windows binding parity requires canonical containment",
)
def test_python_powershell_share_cursor_and_unknown_row_progress(inventory):
    import agent_procutil
    from agent_procutil import no_window_kwargs

    root, _, populate = inventory
    paths = populate(4)
    paths[2].write_text("unknown owned record")
    helper = Path(agent_procutil.__file__).parent / "daemon_instances.ps1"
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    store.reconcile(root, 1, 5)
    assert store._read_cursor(root) == paths[0].stem
    result = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command",
         f"$ErrorActionPreference='Stop'; . '{helper}'; Invoke-DaemonInstanceReconcile -RegistryRoot '{root}' -Limit 1"],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert store._read_cursor(root) == paths[1].stem
    assert store.reconcile(root, 1, 5)[0].status == "held"
    assert store._read_cursor(root) == paths[2].stem
    result = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command",
         f"$ErrorActionPreference='Stop'; . '{helper}'; Invoke-DaemonInstanceReconcile -RegistryRoot '{root}' -Limit 1"],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert store._read_cursor(root) == paths[3].stem


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="actual Linux binding parity requires canonical containment",
)
def test_python_bash_share_bounded_progress_and_wrap(inventory):
    import agent_procutil

    root, _, populate = inventory
    paths = populate(130)
    paths[-1].write_text("unknown owned record")
    production = {path: path.read_bytes() for path in paths[:-1]}
    helper = Path(agent_procutil.__file__).parent / "daemon_instances.sh"

    def collect(limit):
        return subprocess.run(
            ["bash", "-c", 'source "$1"; reconcile_daemon_instances "$3" 5 "$2"',
             "cursor-test", str(helper), str(root), str(limit)],
            capture_output=True, text=True, timeout=10,
        )

    store.reconcile(root, 128, 5)
    assert store._read_cursor(root) == paths[127].stem
    result = collect(2)
    assert result.returncode == 0, result.stderr
    assert paths[-1].stem in result.stdout and "held" in result.stdout
    assert store._read_cursor(root) == paths[-1].stem
    result = collect(1)
    assert result.returncode == 0, result.stderr
    assert store._read_cursor(root) == paths[0].stem
    assert all(path.read_bytes() == data for path, data in production.items())
    assert store.reconcile(root, 1, 5) == []
    assert store._read_cursor(root) == paths[1].stem


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="actual Linux binding control holds require canonical containment",
)
@pytest.mark.parametrize("kind", ["malformed", "symlink", "unreadable", "locked"])
def test_bash_cursor_holds_unsafe_or_busy_control(inventory, kind):
    import fcntl
    import agent_procutil

    root, _, populate = inventory
    populate(1)
    marker = root / ".reconcile.cursor"
    proof = root / "proof"
    proof.write_text("retain")
    descriptor = None
    if kind == "symlink":
        marker.symlink_to(proof)
    elif kind == "malformed":
        marker.write_text("invalid")
    else:
        marker.write_bytes(store._CURSOR_HEADER + b"\n")
        if kind == "unreadable":
            if os.geteuid() == 0:
                pytest.skip("permission denial requires nonroot")
            marker.chmod(0)
        else:
            descriptor = (root / ".reconcile.lock").open("a+b")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    helper = Path(agent_procutil.__file__).parent / "daemon_instances.sh"
    try:
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; reconcile_daemon_instances 1 5 "$2"',
             "cursor-test", str(helper), str(root)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        assert result.stderr
        assert proof.read_text() == "retain"
        if kind == "symlink":
            assert marker.is_symlink()
        elif kind != "unreadable":
            expected = b"invalid" if kind == "malformed" else store._CURSOR_HEADER + b"\n"
            assert marker.read_bytes() == expected
    finally:
        if kind == "unreadable":
            marker.chmod(0o600)
        if descriptor is not None:
            descriptor.close()


@pytest.mark.skipif(
    (os.name != "nt" and sys.platform != "linux")
    or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="native collector progress requires canonical containment",
)
def test_native_binding_normal_calls_reach_late_row(inventory):
    import agent_procutil
    from agent_procutil import no_window_kwargs

    root, _, populate = inventory
    paths = populate(130)
    paths[-1].write_text("unknown owned record")
    production = {path: path.read_bytes() for path in paths[:-1]}
    package = Path(agent_procutil.__file__).parent
    if os.name == "nt":
        shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        command = [
            str(shell), "-NoProfile", "-NonInteractive", "-Command",
            f"$ErrorActionPreference='Stop'; . '{package / 'daemon_instances.ps1'}'; "
            f"Invoke-DaemonInstanceReconcile -RegistryRoot '{root}'",
        ]
    else:
        command = [
            "bash", "-c", 'source "$1"; reconcile_daemon_instances 128 5 "$2"',
            "cursor-test", str(package / "daemon_instances.sh"), str(root),
        ]
    previous = ""
    for _ in range(10):
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=12, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        current = store._read_cursor(root)
        assert current and current != previous, "normal calls must make bounded progress"
        if paths[-1].stem in result.stdout:
            assert "held" in result.stdout
            break
        previous = current
    else:
        pytest.fail("native bounded calls never reached the late unknown row")
    assert all(path.read_bytes() == data for path, data in production.items())


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
    reason="actual Windows collector contention requires canonical containment",
)
def test_powershell_collector_holds_python_custody(inventory):
    import agent_procutil
    from agent_procutil import no_window_kwargs

    root, _, populate = inventory
    populate(1)
    helper = Path(agent_procutil.__file__).parent / "daemon_instances.ps1"
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    with store._file_lock(root / ".reconcile.lock"):
        result = subprocess.run(
            [str(shell), "-NoProfile", "-NonInteractive", "-Command",
             f"$ErrorActionPreference='Stop'; . '{helper}'; Invoke-DaemonInstanceReconcile -RegistryRoot '{root}'"],
            capture_output=True, text=True, timeout=10, **no_window_kwargs(),
        )
    assert result.returncode != 0 and result.stderr
    assert not (root / ".reconcile.cursor").exists()
