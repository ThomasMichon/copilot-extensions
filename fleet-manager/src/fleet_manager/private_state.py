"""Native owner-private state, shared by admission and standalone service hosts."""

import os
from pathlib import Path
import stat

from .planning import PlanError


def validate_private_file(path: Path) -> None:
    """Check one explicitly selected private file without repairing permissions."""
    if not path.is_absolute() or ".." in path.parts:
        raise PlanError("private input requires an absolute ordinary path")
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PlanError("private input cannot traverse indirection")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise PlanError("private input is not a regular file")
    if os.name == "nt":
        windows_private(path)
    elif info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PlanError("private input is not private to its owner")


def read_private_file(path: Path, maximum: int) -> bytes:
    """Validate custody of the actual opened file, not a replaceable path baseline."""
    from .artifacts import _content_stamp, _file_stat, _identity, _reader

    validate_private_file(path)
    try:
        with _reader(path) as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not 1 <= opened.st_size <= maximum:
                raise PlanError("private input is not a bounded regular file")
            if os.name == "nt":
                windows_private(path, descriptor=stream.fileno())
            elif opened.st_uid != os.getuid() or opened.st_mode & 0o077:
                raise PlanError("private input is not private to its owner")
            data = stream.read(maximum + 1)
            after, named = os.fstat(stream.fileno()), _file_stat(path)
            if (len(data) != opened.st_size or len(data) > maximum
                    or _content_stamp(after) != _content_stamp(opened)
                    or _identity(named) != _identity(opened)
                    or _content_stamp(named) != _content_stamp(opened)):
                raise PlanError("private input changed during admission")
            return data
    except OSError:
        raise PlanError("private input could not be read") from None


def _windows_identity():
    import ctypes as c
    from ctypes import wintypes as w

    security = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [w.LPVOID], w.LPVOID
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    security.OpenProcessToken.restype = w.BOOL
    security.GetTokenInformation.argtypes = [
        w.HANDLE, c.c_int, w.LPVOID, w.DWORD, c.POINTER(w.DWORD),
    ]
    security.GetTokenInformation.restype = w.BOOL
    security.ConvertSidToStringSidW.argtypes = [w.LPVOID, c.POINTER(w.LPWSTR)]
    security.ConvertSidToStringSidW.restype = w.BOOL

    def sid_text(sid):
        text = w.LPWSTR()
        if not security.ConvertSidToStringSidW(sid, c.byref(text)):
            raise c.WinError(c.get_last_error())
        try:
            return text.value
        finally:
            kernel.LocalFree(c.cast(text, w.LPVOID))

    token = w.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(token)):
        raise c.WinError(c.get_last_error())
    try:
        size = w.DWORD()
        security.GetTokenInformation(token, 1, None, 0, c.byref(size))
        if not size.value:
            raise c.WinError(c.get_last_error())
        data = c.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, data, size, c.byref(size)):
            raise c.WinError(c.get_last_error())
        user = sid_text(c.cast(data, c.POINTER(w.LPVOID))[0])
    finally:
        kernel.CloseHandle(token)
    return kernel, security, user, sid_text


def windows_private(path, *, privileged_owner: bool = False, descriptor: int | None = None):
    """Keep the admission ACL policy; recovery may select a privileged OS owner."""
    import ctypes as c
    from ctypes import wintypes as w

    kernel, security, user, sid_text = _windows_identity()
    security.GetNamedSecurityInfoW.argtypes = [
        w.LPWSTR, c.c_int, w.DWORD, c.POINTER(w.LPVOID), w.LPVOID,
        c.POINTER(w.LPVOID), w.LPVOID, c.POINTER(w.LPVOID),
    ]
    security.GetNamedSecurityInfoW.restype = w.DWORD
    security.GetAce.argtypes = [w.LPVOID, w.DWORD, c.POINTER(w.LPVOID)]
    security.GetAce.restype = w.BOOL
    owner, acl = (w.LPVOID() for _ in range(2))
    security_descriptor = w.LPVOID()
    if descriptor is None:
        error = security.GetNamedSecurityInfoW(
            str(path), 1, 5, c.byref(owner), None, c.byref(acl), None, c.byref(security_descriptor))
    else:
        import msvcrt

        security.GetSecurityInfo.argtypes = [
            w.HANDLE, c.c_int, w.DWORD, c.POINTER(w.LPVOID), w.LPVOID,
            c.POINTER(w.LPVOID), w.LPVOID, c.POINTER(w.LPVOID),
        ]
        security.GetSecurityInfo.restype = w.DWORD
        error = security.GetSecurityInfo(
            msvcrt.get_osfhandle(descriptor), 1, 5, c.byref(owner), None, c.byref(acl),
            None, c.byref(security_descriptor))
    if error:
        raise c.WinError(error)
    try:
        owners = {user, "S-1-5-18", "S-1-5-32-544"} if privileged_owner else {user}
        if not owner.value or sid_text(owner) not in owners or not acl.value:
            raise PlanError("state path is not private to its owner")
        allowed = {user, "S-1-3-4", "S-1-5-18", "S-1-5-32-544"}
        count = c.c_ushort.from_address(acl.value + 4).value
        for index in range(count):
            ace = w.LPVOID()
            if not security.GetAce(acl, index, c.byref(ace)):
                raise c.WinError(c.get_last_error())
            kind = c.c_ubyte.from_address(ace.value).value
            if kind == 1:
                continue
            if kind != 0 or sid_text(ace.value + 8) not in allowed:
                raise PlanError("state root has an unsupported or shared access grant")
    finally:
        kernel.LocalFree(security_descriptor)


def _windows_create(path: Path, *, directory: bool) -> int | None:
    import ctypes as c
    from ctypes import wintypes as w

    class Attributes(c.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]

    kernel, security, user, _ = _windows_identity()
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR, w.DWORD, c.POINTER(w.LPVOID), w.LPVOID,
    ]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, c.POINTER(Attributes)]
    kernel.CreateDirectoryW.restype = w.BOOL
    kernel.CreateFileW.argtypes = [
        w.LPCWSTR, w.DWORD, w.DWORD, c.POINTER(Attributes), w.DWORD, w.DWORD, w.HANDLE,
    ]
    kernel.CreateFileW.restype = w.HANDLE
    descriptor = w.LPVOID()
    sddl = f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, c.byref(descriptor), None,
    ):
        raise c.WinError(c.get_last_error())
    try:
        attributes = Attributes(c.sizeof(Attributes), descriptor, False)
        if directory:
            if not kernel.CreateDirectoryW(str(path), c.byref(attributes)):
                raise c.WinError(c.get_last_error())
            return None
        handle = kernel.CreateFileW(str(path), 0x40000000, 0, c.byref(attributes), 1, 0x80, None)
        if handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        import msvcrt

        try:
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except OSError:
            kernel.CloseHandle(handle)
            raise
    finally:
        kernel.LocalFree(descriptor)


def create_private_file(path: Path) -> int:
    """Create a new file with TokenUser ownership even for an elevated caller."""
    if os.name == "nt":
        descriptor = _windows_create(path, directory=False)
        assert descriptor is not None
        return descriptor
    return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)


def private_directory(path: Path) -> None:
    """Create privately in one operation, or validate without repairing ownership."""
    if not path.exists():
        if os.name == "nt":
            _windows_create(path, directory=True)
        else:
            path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise PlanError("state root is not an ordinary private directory")
    if os.name == "nt":
        windows_private(path)
    elif info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PlanError("state root is not private to its owner")
