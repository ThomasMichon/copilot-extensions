"""Pure ACL admission and native same-account, non-administrator reuse."""

from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "versioned_runtime_leases.py"
spec = importlib.util.spec_from_file_location("windows_lease_access", SOURCE)
leases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leases)


@contextmanager
def non_administrator():
    import ctypes as c
    from ctypes import wintypes as w

    kernel, security = c.WinDLL("kernel32", use_last_error=True), c.WinDLL("advapi32", use_last_error=True)

    class SidAttributes(c.Structure):
        _fields_ = [("sid", w.LPVOID), ("attributes", w.DWORD)]

    signatures = (
        (kernel, "GetCurrentProcess", [], w.HANDLE),
        (kernel, "CloseHandle", [w.HANDLE], w.BOOL),
        (security, "CreateWellKnownSid", [c.c_int, w.LPVOID, w.LPVOID, c.POINTER(w.DWORD)], w.BOOL),
        (security, "OpenProcessToken", [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL),
        (security, "CreateRestrictedToken",
         [w.HANDLE, w.DWORD, w.DWORD, c.POINTER(SidAttributes), w.DWORD, w.LPVOID,
          w.DWORD, w.LPVOID, c.POINTER(w.HANDLE)], w.BOOL),
        (security, "ImpersonateLoggedOnUser", [w.HANDLE], w.BOOL),
        (security, "RevertToSelf", [], w.BOOL),
    )
    for dll, name, arguments, result in signatures:
        getattr(dll, name).argtypes, getattr(dll, name).restype = arguments, result
    token, limited, size = w.HANDLE(), w.HANDLE(), w.DWORD(68)
    sid = c.create_string_buffer(size.value)
    assert security.CreateWellKnownSid(26, None, sid, c.byref(size))
    disabled = SidAttributes(c.cast(sid, w.LPVOID), 0)
    assert security.OpenProcessToken(kernel.GetCurrentProcess(), 10, c.byref(token))
    try:
        assert security.CreateRestrictedToken(
            token, 1, 1, c.byref(disabled), 0, None, 0, None, c.byref(limited),
        )
        assert security.ImpersonateLoggedOnUser(limited)
        try:
            assert not c.windll.shell32.IsUserAnAdmin()
            yield
        finally:
            assert security.RevertToSelf()
    finally:
        if limited:
            kernel.CloseHandle(limited)
        kernel.CloseHandle(token)


def elevated_windows():
    if os.name != "nt":
        return False
    import ctypes

    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def legacy_permissions(path, *, directory, sddl=None):
    import ctypes as c
    from ctypes import wintypes as w

    security, kernel = c.WinDLL("advapi32", use_last_error=True), c.WinDLL("kernel32", use_last_error=True)
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [w.LPCWSTR, w.DWORD, c.POINTER(w.LPVOID), w.LPVOID]
    convert.restype = w.BOOL
    security.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, w.LPVOID]
    security.SetFileSecurityW.restype = w.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [w.LPVOID], w.LPVOID
    descriptor = w.LPVOID()
    assert convert(
        sddl or (
            "O:BAD:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
            if directory else "O:BA"
        ),
        1, c.byref(descriptor), None,
    )
    try:
        assert security.SetFileSecurityW(str(path), 0x80000005 if directory else 1, descriptor)
    finally:
        kernel.LocalFree(descriptor)


def legacy_file(path, data):
    import ctypes as c
    from ctypes import wintypes as w
    import msvcrt

    class Attributes(c.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]

    security, kernel = c.WinDLL("advapi32", use_last_error=True), c.WinDLL("kernel32", use_last_error=True)
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes, convert.restype = [w.LPCWSTR, w.DWORD, c.POINTER(w.LPVOID), w.LPVOID], w.BOOL
    kernel.CreateFileW.argtypes = [
        w.LPCWSTR, w.DWORD, w.DWORD, c.POINTER(Attributes), w.DWORD, w.DWORD, w.HANDLE,
    ]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [w.LPVOID], w.LPVOID
    descriptor = w.LPVOID()
    assert convert("O:BA", 1, c.byref(descriptor), None)
    try:
        attributes = Attributes(c.sizeof(Attributes), descriptor, False)
        handle = kernel.CreateFileW(str(path), 0xC0000000, 3, c.byref(attributes), 1, 0x80, None)
        assert handle != c.c_void_p(-1).value
        descriptor_fd = msvcrt.open_osfhandle(handle, os.O_RDWR)
        try:
            os.write(descriptor_fd, data)
        finally:
            os.close(descriptor_fd)
    finally:
        kernel.LocalFree(descriptor)


@pytest.mark.skipif(not elevated_windows(), reason="requires native elevated Windows token")
@pytest.mark.parametrize("kind", ["retention", "construction", "publication"])
def test_elevated_creation_and_legacy_repair_preserve_non_admin_access(tmp_path, kind):
    import hashlib

    root = tmp_path / "user-root"
    os.close(leases._windows_private_lease_file(tmp_path, root, "root-fixture"))
    version = "selectors" if kind == "publication" else "1.2.3"
    directory = root / f".{kind}-leases"
    directory.mkdir()
    legacy_permissions(directory, directory=True)
    path = directory / (hashlib.sha256(version.encode()).hexdigest() + ".lock")
    legacy_file(path, b"retained")
    before = path.stat().st_ino
    with non_administrator():
        with pytest.raises(PermissionError):
            path.open("r+b")
    for _ in range(2):
        with leases._kernel_lease(root, version, kind=kind, shared=kind == "retention"):
            assert path.stat().st_ino == before
    audit = list(root.glob(".lease-access-repair-*.json"))
    assert len(audit) == 1
    assert json.loads(audit[0].read_text())["entries"][-1]["path"] == directory.name
    with non_administrator():
        for _ in range(2):
            with leases._kernel_lease(root, version, kind=kind, shared=kind == "retention"):
                assert path.stat().st_ino == before
            assert path.read_bytes() == b"retained"
        with leases._kernel_lease(root, "next", kind=kind, shared=False):
            pass


@pytest.mark.skipif(not elevated_windows(), reason="requires native elevated Windows token")
def test_unknown_legacy_directory_entry_refuses_before_permission_changes(tmp_path):
    root = tmp_path / "user-root"
    os.close(leases._windows_private_lease_file(tmp_path, root, "root-fixture"))
    directory = root / ".retention-leases"
    directory.mkdir()
    legacy_permissions(directory, directory=True)
    foreign = directory / "unowned-file"
    legacy_file(foreign, b"preserve")
    with pytest.raises(PermissionError, match="unowned entry"):
        with leases._kernel_lease(root, "1.0", kind="retention", shared=True):
            pass
    assert foreign.read_bytes() == b"preserve"
    assert not list(root.glob(".lease-access-repair-*.json"))
    with non_administrator():
        with pytest.raises(PermissionError):
            foreign.open("r+b")


@pytest.mark.skipif(not elevated_windows(), reason="requires native elevated Windows token")
@pytest.mark.parametrize("kind", ["retention", "construction", "publication"])
def test_private_admin_owned_parent_retains_account_authority(tmp_path, kind):
    import hashlib

    root = tmp_path / "admin-owned-parent"
    os.close(leases._windows_private_lease_file(tmp_path, root, "root-fixture"))
    legacy_permissions(root, directory=False)  # Change only owner; keep the account's full grant.
    directory = root / f".{kind}-leases"
    directory.mkdir()
    legacy_permissions(directory, directory=True)
    key = "selectors"
    path = directory / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
    legacy_file(path, b"retained")
    before = path.stat().st_ino
    with leases._kernel_lease(root, key, kind=kind, shared=False):
        assert path.stat().st_ino == before
    with non_administrator():
        with leases._kernel_lease(root, key, kind=kind, shared=False):
            assert path.stat().st_ino == before
        assert path.read_bytes() == b"retained"


@pytest.mark.skipif(not elevated_windows(), reason="requires native elevated Windows token")
@pytest.mark.parametrize("public", [False, True], ids=["no-account-grant", "public-root"])
def test_admin_owned_parent_without_private_account_authority_refuses(tmp_path, public):
    root = tmp_path / "untrusted-parent"
    os.close(leases._windows_private_lease_file(tmp_path, root, "root-fixture"))
    if public:
        legacy_permissions(
            root, directory=True,
            sddl="O:BAD:P(A;OICI;FA;;;WD)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
        )
    else:
        legacy_permissions(root, directory=True)
    directory = root / ".publication-leases"
    directory.mkdir()
    legacy_permissions(directory, directory=True)
    with pytest.raises(PermissionError, match="public or foreign root"):
        with leases._kernel_lease(root, "selectors", kind="publication", shared=False):
            pass
    assert not list(root.glob(".lease-access-repair-*.json"))


@pytest.mark.skipif(not elevated_windows(), reason="requires native elevated Windows token")
def test_permission_repair_does_not_release_an_existing_kernel_owner(tmp_path, monkeypatch):
    import hashlib

    root = tmp_path / "user-root"
    os.close(leases._windows_private_lease_file(tmp_path, root, "root-fixture"))
    directory = root / ".retention-leases"
    directory.mkdir()
    legacy_permissions(directory, directory=True)
    path = directory / (hashlib.sha256(b"1.0").hexdigest() + ".lock")
    legacy_file(path, b"retained")
    before = path.stat().st_ino
    owner = leases._kernel_lease(root, "1.0", kind="retention", shared=True)
    with monkeypatch.context() as old_entry:
        old_entry.setattr(
            leases, "_windows_private_lease_file",
            lambda _root, folder, name: os.open(folder / name, os.O_RDWR),
        )
        owner.__enter__()  # Existing kernel custody predates the permissions repair.
    try:
        with pytest.raises(leases.RetentionTimeout):
            with leases._kernel_lease(
                root, "1.0", kind="retention", shared=False, timeout=0,
            ):
                pass
        assert path.stat().st_ino == before
        assert path.read_bytes() == b"retained"
    finally:
        owner.__exit__(None, None, None)
    with non_administrator():
        with leases._kernel_lease(root, "1.0", kind="retention", shared=False, timeout=0):
            assert path.stat().st_ino == before


@pytest.mark.skipif(not elevated_windows(), reason="requires native elevated Windows token")
def test_caught_partial_permission_repair_resumes_without_replacing_files(tmp_path, monkeypatch):
    import ctypes as c
    from ctypes import wintypes as w
    import hashlib

    root = tmp_path / "user-root"
    os.close(leases._windows_private_lease_file(tmp_path, root, "root-fixture"))
    directory = root / ".retention-leases"
    directory.mkdir()
    legacy_permissions(directory, directory=True)
    path = directory / (hashlib.sha256(b"1.0").hexdigest() + ".lock")
    legacy_file(path, b"retained")
    before = path.stat().st_ino
    original = c.WinDLL
    calls = []

    def library(name, **kwargs):
        native = original(name, **kwargs)
        if name != "advapi32":
            return native
        setter = native.SetFileSecurityW
        setter.argtypes, setter.restype = [w.LPCWSTR, w.DWORD, w.LPVOID], w.BOOL

        def fail_second(*args):
            calls.append(str(args[0]))
            if len(calls) == 2:
                c.set_last_error(5)
                return False
            return setter(*args)

        native.SetFileSecurityW = fail_second
        return native

    with monkeypatch.context() as failure:
        failure.setattr(c, "WinDLL", library)
        with pytest.raises(OSError):
            with leases._kernel_lease(root, "1.0", kind="retention", shared=True):
                pass
    assert len(calls) == 2
    assert path.stat().st_ino == before
    with leases._kernel_lease(root, "1.0", kind="retention", shared=True):
        assert path.stat().st_ino == before
    assert len(list(root.glob(".lease-access-repair-*.json"))) == 2
    with non_administrator():
        with leases._kernel_lease(root, "1.0", kind="retention", shared=True):
            assert path.read_bytes() == b"retained"


@pytest.mark.skipif(not elevated_windows(), reason="requires native elevated Windows token")
def test_overlapping_permission_repairs_admit_the_exact_completed_winner(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import ctypes as c
    from ctypes import wintypes as w
    import hashlib
    import threading

    root = tmp_path / "user-root"
    os.close(leases._windows_private_lease_file(tmp_path, root, "root-fixture"))
    directory = root / ".retention-leases"
    directory.mkdir()
    legacy_permissions(directory, directory=True)
    path = directory / (hashlib.sha256(b"1.0").hexdigest() + ".lock")
    legacy_file(path, b"retained")
    before = path.stat().st_ino
    original, local = c.WinDLL, threading.local()
    both_snapshotted, first_done = threading.Barrier(2), threading.Event()

    def library(name, **kwargs):
        native = original(name, **kwargs)
        if name != "advapi32":
            return native
        setter = native.SetFileSecurityW
        setter.argtypes, setter.restype = [w.LPCWSTR, w.DWORD, w.LPVOID], w.BOOL

        def overlap(target, *args):
            if str(target) == str(path):
                both_snapshotted.wait(timeout=5)
                if local.second:
                    assert first_done.wait(timeout=5)
            result = setter(target, *args)
            if str(target) == str(directory) and not local.second:
                first_done.set()
            return result

        native.SetFileSecurityW = overlap
        return native

    def acquire(second):
        local.second = second
        with leases._kernel_lease(root, "1.0", kind="retention", shared=True):
            assert path.stat().st_ino == before

    with monkeypatch.context() as race:
        race.setattr(c, "WinDLL", library)
        with ThreadPoolExecutor(max_workers=2) as callers:
            first, second = callers.submit(acquire, False), callers.submit(acquire, True)
            first.result(timeout=10)
            second.result(timeout=10)
    with non_administrator():
        with leases._kernel_lease(root, "1.0", kind="retention", shared=True):
            assert path.read_bytes() == b"retained"
