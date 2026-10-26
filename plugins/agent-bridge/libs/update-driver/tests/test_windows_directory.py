"""Actual account access survives elevated queue creation on Windows."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

import pytest

import update_driver
from update_driver._directory import create_queue_directory

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="opt-in native Windows account/ACL qualification",
)


@contextmanager
def _ordinary_thread():
    kernel = c.WinDLL("kernel32", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    security.OpenProcessToken.restype = w.BOOL

    class SidAttributes(c.Structure):
        _fields_ = [("sid", w.LPVOID), ("attributes", w.DWORD)]

    security.CreateWellKnownSid.argtypes = [c.c_int, w.LPVOID, w.LPVOID, c.POINTER(w.DWORD)]
    security.CreateWellKnownSid.restype = w.BOOL
    security.CreateRestrictedToken.argtypes = [
        w.HANDLE, w.DWORD, w.DWORD, c.POINTER(SidAttributes), w.DWORD, w.LPVOID,
        w.DWORD, w.LPVOID, c.POINTER(w.HANDLE),
    ]
    security.CreateRestrictedToken.restype = w.BOOL
    security.ImpersonateLoggedOnUser.argtypes = [w.HANDLE]
    security.ImpersonateLoggedOnUser.restype = w.BOOL
    security.RevertToSelf.restype = w.BOOL
    security.GetTokenInformation.argtypes = [
        w.HANDLE, c.c_int, w.LPVOID, w.DWORD, c.POINTER(w.DWORD),
    ]
    security.GetTokenInformation.restype = w.BOOL
    security.ConvertSidToStringSidW.argtypes = [w.LPVOID, c.POINTER(w.LPWSTR)]
    security.ConvertSidToStringSidW.restype = w.BOOL
    kernel.LocalFree.argtypes = [w.LPVOID]
    kernel.LocalFree.restype = w.LPVOID
    token, restricted, text = w.HANDLE(), w.HANDLE(), w.LPWSTR()
    size = w.DWORD(68)
    admin = c.create_string_buffer(size.value)
    if not security.CreateWellKnownSid(26, None, admin, c.byref(size)):
        raise c.WinError(c.get_last_error())
    disabled = SidAttributes(c.cast(admin, w.LPVOID), 0)
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 10, c.byref(token)):
        raise c.WinError(c.get_last_error())
    try:
        needed = w.DWORD()
        security.GetTokenInformation(token, 1, None, 0, c.byref(needed))
        data = c.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 1, data, needed, c.byref(needed)):
            raise c.WinError(c.get_last_error())
        user = c.cast(data, c.POINTER(SidAttributes)).contents.sid
        if not security.ConvertSidToStringSidW(user, c.byref(text)):
            raise c.WinError(c.get_last_error())
        account = text.value
        if not security.CreateRestrictedToken(
            token, 1, 1, c.byref(disabled), 0, None, 0, None, c.byref(restricted),
        ):
            raise c.WinError(c.get_last_error())
        if not security.ImpersonateLoggedOnUser(restricted):
            raise c.WinError(c.get_last_error())
        try:
            assert not c.windll.shell32.IsUserAnAdmin()
            yield account
        finally:
            if not security.RevertToSelf():
                raise c.WinError(c.get_last_error())
    finally:
        if text:
            kernel.LocalFree(c.cast(text, w.LPVOID))
        if restricted:
            kernel.CloseHandle(restricted)
        kernel.CloseHandle(token)


def _sddl(path: Path) -> str:
    security = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    security.GetNamedSecurityInfoW.argtypes = [
        w.LPWSTR, c.c_int, w.DWORD, w.LPVOID, w.LPVOID, w.LPVOID, w.LPVOID,
        c.POINTER(w.LPVOID),
    ]
    security.GetNamedSecurityInfoW.restype = w.DWORD
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        w.LPVOID, w.DWORD, w.DWORD, c.POINTER(w.LPWSTR), w.LPVOID,
    ]
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = w.BOOL
    kernel.LocalFree.argtypes = [w.LPVOID]
    descriptor, text = w.LPVOID(), w.LPWSTR()
    code = security.GetNamedSecurityInfoW(str(path), 1, 5, None, None, None, None, c.byref(descriptor))
    if code:
        raise c.WinError(code)
    try:
        if not security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, 5, c.byref(text), None,
        ):
            raise c.WinError(c.get_last_error())
        return text.value
    finally:
        if text:
            kernel.LocalFree(c.cast(text, w.LPVOID))
        kernel.LocalFree(descriptor)


@pytest.mark.parametrize("create_elevated", [True, False], ids=["elevated-create", "ordinary-create"])
def test_current_account_can_read_and_update_its_private_queue(create_elevated: bool) -> None:
    if not c.windll.shell32.IsUserAnAdmin():
        pytest.skip("elevated producer token needed for the same-account regression")
    top = Path(tempfile.gettempdir()) / f"ce-update-queue-{uuid.uuid4().hex}"
    create_queue_directory(top, exist_ok=False)
    root = top / ".copilot-extensions" / "update-driver"
    runner = update_driver.TrustedRunner((sys.executable, "-c", "raise SystemExit(0)"), str(top))

    def enqueue(flag: str):
        return update_driver.enqueue(
            update_driver.RequestSpec(
                backend="manager", argv=(flag,), cwd=str(top), source="fixture",
            ),
            runner=runner, root=root,
        )

    try:
        if create_elevated:
            enqueue("--force")
        with _ordinary_thread() as account:
            if not create_elevated:
                enqueue("--force")
            assert len(update_driver.status(root=root)) == 1
            before = _sddl(root)
            assert before.startswith("O:" + account)
            assert "D:P" in before and f"(A;OICI;FA;;;{account})" in before
            enqueue("--no-anchor-sync")
            assert len(update_driver.status(root=root)) == 2
            create_queue_directory(root)
            assert _sddl(root) == before
    finally:
        shutil.rmtree(top)
