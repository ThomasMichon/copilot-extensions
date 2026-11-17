"""Opt-in native Windows access controls on newly owned synthetic directories."""

import os
from pathlib import Path
import sys
from typing import Any, Callable

import pytest

from agent_worktrees import artifact_output, tracking
from agent_worktrees.artifact_root import _windows_error, admit_root
from test_artifact_output import Destination, destination as destination, produce
from test_resource_producer import owner as owner

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.skipif(os.name != "nt", reason="native Windows access-control entries"),
]


def _access(path: Path, entry: str) -> None:
    import ctypes
    from ctypes import wintypes
    from agent_procutil._daemon_instance_owner import _apis, _current_sids

    native_apis: Callable[[], tuple[Any, Any]] = _apis
    advapi, kernel = native_apis()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    user = _current_sids(os.getpid())[0]
    descriptor = ctypes.c_void_p()
    sddl = f"O:{user}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{user})" + entry
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None,
    ):
        raise OSError("synthetic access-control descriptor conversion failed")
    try:
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise OSError("synthetic directory access-control installation failed")
    finally:
        kernel.LocalFree(descriptor)


def test_native_api_failure_uses_saved_ctypes_error(tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    native = getattr(ctypes, "WinDLL")("kernel32", use_last_error=False)
    saved = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    native.SetLastError.argtypes = [wintypes.DWORD]
    native.SetLastError.restype = None
    native.GetLastError.argtypes = []
    native.GetLastError.restype = wintypes.DWORD
    saved.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    saved.GetFileAttributesW.restype = wintypes.DWORD
    native.SetLastError(0)
    assert saved.GetFileAttributesW(str(tmp_path / "missing-file")) == 0xFFFFFFFF
    get_last_error: Callable[[], int] = getattr(ctypes, "get_last_error")
    assert get_last_error() == 2 and native.GetLastError() == 0
    assert getattr(_windows_error(), "winerror") == 2
    assert getattr(_windows_error(5), "winerror") == 5


@pytest.mark.parametrize(("entry", "accepted"), [
    ("(A;OICI;FR;;;WD)", True),
    ("(A;OICIIO;FA;;;CO)", True),
    ("(A;OICI;FW;;;WD)", False),
])
def test_native_output_accepts_reads_not_other_principal_writes(
    destination: Destination, entry: str, accepted: bool,
) -> None:
    path, selection, _ = destination
    root = Path(selection["root"])
    _access(root, entry)
    if accepted:
        assert produce(destination)["phase"] == "delivered"
        with pytest.raises(ValueError, match="another principal"):
            with admit_root(root, forbidden=()):
                pytest.fail("ordinary output permissions weakened private scratch admission")
    else:
        with pytest.raises(ValueError, match="unsafe access"):
            produce(destination)
        assert tracking.load_record(path).resources == [] and not Path(selection["destination"]).exists()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Windows creation-time stat attributes")
@pytest.mark.parametrize("field", ["st_ctime_ns", "st_mtime_ns"])
def test_native_cross_probe_timestamp_meanings_preserve_real_change_checks(
    destination: Destination, monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    path, selection, _ = destination
    root = Path(selection["root"])
    original = Path.lstat
    def observed(selected: Path) -> os.stat_result:
        info = original(selected)
        if selected.is_relative_to(root) and selected.name in {artifact_output.MARKER, "report.txt"}:
            attributes = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            attributes[field] += 100
            return os.stat_result(tuple(info), attributes)
        return info
    monkeypatch.setattr(Path, "lstat", observed)
    if field == "st_ctime_ns":
        assert produce(destination)["phase"] == "delivered"
    else:
        with pytest.raises(ValueError, match="changed during verification"):
            produce(destination)
        assert tracking.load_record(path).resources[0].is_unsettled
