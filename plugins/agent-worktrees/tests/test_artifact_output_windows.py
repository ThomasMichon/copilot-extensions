"""Opt-in native Windows access controls on newly owned synthetic directories."""

import os
from pathlib import Path
from typing import Any, Callable

import pytest

from agent_worktrees import tracking
from agent_worktrees.artifact_root import admit_root
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
