"""Source-removal qualification still requires a real successful rename."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading

import pytest


def test_missing_source_is_not_retried(area, rename_source, monkeypatch):
    original = Path.rename
    attempts = []

    def observed(path, target):
        attempts.append(1)
        return original(path, target)

    monkeypatch.setattr(Path, "rename", observed)
    with pytest.raises(FileNotFoundError):
        rename_source(area / "missing", area / "removed-source")
    assert len(attempts) == 1 and not (area / "removed-source").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows directory sharing")
@pytest.mark.parametrize("release", [False, True])
def test_source_removal_waits_only_for_bounded_native_sharing(
    area, rename_source, monkeypatch, release,
):
    source, destination = area / "inputs", area / "removed-source"
    source.mkdir()
    (source / "source.py").write_text("pass\n")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(source), 0x80000000, 3, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    handles, guard = [handle], threading.Lock()
    failed = threading.Event()
    attempts = []
    original = Path.rename

    def close():
        with guard:
            if handles:
                assert kernel.CloseHandle(handles.pop())

    def observed(path, target):
        attempts.append(1)
        try:
            return original(path, target)
        except OSError:
            failed.set()
            raise

    def release_after_actual_refusal():
        failed.wait(10)
        close()

    monkeypatch.setattr(Path, "rename", observed)
    thread = threading.Thread(target=release_after_actual_refusal) if release else None
    if thread is not None:
        thread.start()
    try:
        if release:
            assert rename_source(source, destination) == destination
            assert 2 <= len(attempts) <= 5 and failed.is_set()
            assert not source.exists() and (destination / "source.py").read_text() == "pass\n"
        else:
            with pytest.raises(OSError) as error:
                rename_source(source, destination)
            assert error.value.winerror in (5, 32, 33) and len(attempts) == 5
            assert not destination.exists() and (source / "source.py").read_text() == "pass\n"
    finally:
        close()
        failed.set()
        if thread is not None:
            thread.join(10)
            assert not thread.is_alive()
