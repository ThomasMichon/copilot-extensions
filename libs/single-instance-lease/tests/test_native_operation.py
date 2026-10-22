"""Native bootstrap custody must contend with Python's exact kernel protocol."""

from __future__ import annotations

import base64
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

import agent_procutil as pu
from single_instance_lease.operation import OperationLock, OperationLockedError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell custody")
NATIVE = Path(__file__).resolve().parents[1] / "src" / "single_instance_lease" / "operation.ps1"


def quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def command(script: str) -> list[str]:
    shell = shutil.which("powershell")
    assert shell, "native Windows qualification requires stock Windows PowerShell"
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def invoke(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        command(f"$ErrorActionPreference='Stop'; . {quoted(NATIVE)}; {script}"),
        capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
    )


def test_python_custody_excludes_native_without_rewriting_diagnostics(tmp_path):
    path = tmp_path / "operation.lock"
    path.write_text("diagnostics stay readable", encoding="ascii")
    with OperationLock(path):
        result = invoke(
            f"try {{ $l = Enter-NativeOperationLock {quoted(path)}; $l.Dispose(); exit 91 }}"
            " catch { [Console]::Out.WriteLine($_.Exception.Message); exit 7 }"
        )
        assert result.returncode == 7, result.stderr
        assert path.read_text() == "diagnostics stay readable"
    result = invoke(f"$l = Enter-NativeOperationLock {quoted(path)}; $l.AssertOwned(); $l.Dispose()")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("crash", [False, True])
def test_native_custody_excludes_python_and_releases_on_close_or_death(tmp_path, crash):
    path = tmp_path / "operation.lock"
    script = (
        f"$ErrorActionPreference='Stop'; . {quoted(NATIVE)}; "
        f"$l = Enter-NativeOperationLock {quoted(path)}; $l.AssertOwned(); "
        "[Console]::Out.WriteLine('owned'); [Console]::Out.Flush(); "
        "[void][Console]::In.ReadLine(); $l.AssertOwned(); $l.Dispose()"
    )
    child, job = pu.spawn_sync_in_kill_on_close_job(
        command(script), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **pu.no_window_kwargs(),
    )
    receipt = queue.Queue()
    reader = threading.Thread(target=lambda: receipt.put(child.stdout.readline()), daemon=True)
    try:
        reader.start()
        line = receipt.get(timeout=25)
        reader.join(timeout=1)
        assert line.strip() == "owned"
        with pytest.raises(OperationLockedError):
            OperationLock(path).acquire()
        if crash:
            child.kill()
            child.communicate(timeout=10)
            reader.join(timeout=1)
        else:
            _, error = child.communicate("\n", timeout=10)
            assert child.returncode == 0, error
        with OperationLock(path) as custody:
            custody.assert_owned()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
        if job is not None:
            job.close()


def test_native_local_handles_and_hardlinks_share_one_reservation(tmp_path):
    path = tmp_path / "operation.lock"
    path.touch()
    alias = tmp_path / "alias.lock"
    os.link(path, alias)
    result = invoke(
        f"$l = Enter-NativeOperationLock {quoted(path)}; "
        f"try {{ $other = Enter-NativeOperationLock {quoted(alias)}; $other.Dispose(); exit 91 }}"
        " catch { $l.AssertOwned() } finally { $l.Dispose() }"
    )
    assert result.returncode == 0, result.stderr
    with OperationLock(path):
        pass


@pytest.mark.parametrize("kind", [
    "relative", "drive-relative", "current-drive-rooted", "directory", "missing-parent",
])
def test_native_rejects_unowned_paths(tmp_path, kind):
    path = {
        "relative": Path("operation.lock"),
        "drive-relative": Path("C:operation.lock"),
        "current-drive-rooted": Path("\\operation.lock"),
        "directory": tmp_path,
        "missing-parent": tmp_path / "absent" / "operation.lock",
    }[kind]
    result = invoke(
        f"try {{ $l = Enter-NativeOperationLock {quoted(path)}; $l.Dispose(); exit 91 }}"
        " catch { exit 7 }"
    )
    assert result.returncode == 7, result.stderr
    if kind == "missing-parent":
        assert not path.parent.exists()
