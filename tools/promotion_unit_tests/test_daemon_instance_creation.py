"""Actor-free private creation contracts, including Windows default-owner skew."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs/agent-procutil/src"))
from agent_procutil import _daemon_instance_owner as owner
from agent_procutil import _daemon_instance_store as store


class Function:
    def __init__(self, call: Any) -> None:
        self.call = call
        self.argtypes = self.restype = None

    def __call__(self, *args: Any) -> Any:
        return self.call(*args)


class Scalar:
    def __init__(self, value: Any = None) -> None:
        self.value = value


class Pointer(Scalar):
    def __init__(self, value: Any = None) -> None:
        super().__init__((1 << 64) - 1 if value == -1 else value)


class Structure:
    def __init__(self, *values: Any) -> None:
        for (name, _kind), value in zip(self._fields_, values, strict=True):
            setattr(self, name, value.value if isinstance(value, Scalar) else value)


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    events: list[Any] = []
    state = {"error": 5, "fail": ""}
    ctypes = SimpleNamespace(
        Structure=Structure, c_uint32=Scalar, c_int32=Scalar, c_int=int, c_void_p=Pointer,
        POINTER=lambda kind: kind, sizeof=lambda kind: 24,
        byref=lambda value: SimpleNamespace(_obj=value),
        get_last_error=lambda: state["error"],
        WinError=lambda code: OSError(code, "native security refused"),
        wintypes=SimpleNamespace(LPCWSTR=str, LPWSTR=str, DWORD=Scalar, BOOL=int, HANDLE=Pointer),
    )
    monkeypatch.setitem(sys.modules, "ctypes", ctypes)
    monkeypatch.setattr(owner, "_attributes_type", owner._attributes_type.__wrapped__)

    def security(sddl: str, revision: int, pointer: Any, length: Any) -> int:
        assert revision == 1 and length is None
        events.append(("security", sddl))
        if state["fail"] == "security":
            return 0
        pointer._obj.value = 1234
        return 1

    def attributes(pointer: Any) -> None:
        value = pointer._obj
        assert value.length == ctypes.sizeof(owner._attributes_type())
        assert value.descriptor == 1234 and value.inherit_handle == 0

    def directory(path: str, pointer: Any) -> int:
        attributes(pointer)
        events.append(("directory", path))
        return int(state["fail"] != "native")

    def file(path: str, access: int, sharing: int, pointer: Any,
             disposition: int, flags: int, template: Any) -> int:
        attributes(pointer)
        assert sharing == 3 and flags == 0x00200080 and template is None
        events.append(("file", access, disposition))
        return ctypes.c_void_p(-1).value if state["fail"] == "native" else 77

    def release(pointer: Any) -> int | None:
        assert pointer.value == 1234
        events.append(("free-security",))
        return 1 if state["fail"] == "free-security" else None

    def close(handle: int) -> int:
        assert handle == 77
        events.append(("close-handle",))
        return int(state["fail"] != "close-handle")

    def descriptor(handle: int, flags: int) -> int:
        assert handle == 77 and flags in (0, 8)
        events.append(("descriptor", flags))
        if state["fail"] in {"descriptor", "close-handle"}:
            raise OSError("descriptor conversion failed")
        return 12

    def inheritance(fd: int, inherit: bool) -> None:
        assert fd == 12 and inherit is False
        events.append(("noninherit",))
        if state["fail"] == "noninherit":
            raise OSError("descriptor inheritance failed")

    advapi = SimpleNamespace(
        ConvertStringSecurityDescriptorToSecurityDescriptorW=Function(security),
    )
    kernel = SimpleNamespace(
        CreateDirectoryW=Function(directory), CreateFileW=Function(file),
        LocalFree=Function(release), CloseHandle=Function(close),
    )
    operating_system = SimpleNamespace(
        name="nt", getpid=lambda: 7,
        O_RDONLY=0, O_WRONLY=1, O_RDWR=2, O_APPEND=8, O_CREAT=0x100,
        O_TRUNC=0x200, O_EXCL=0x400, O_BINARY=0x8000, O_NOINHERIT=0x80,
        set_inheritable=inheritance, close=lambda fd: events.append(("close-descriptor", fd)),
    )
    monkeypatch.setattr(owner, "os", operating_system)
    monkeypatch.setattr(owner, "_apis", lambda: (advapi, kernel))
    monkeypatch.setattr(owner, "_current_sids", lambda pid: ("current-user", "launcher-default-owner"))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=descriptor))
    return SimpleNamespace(events=events, state=state, os=operating_system, advapi=advapi, kernel=kernel)


def test_windows_directory_owner_is_user_at_creation_not_launcher_default(native: SimpleNamespace) -> None:
    path = Path("C:/example/private")
    owner.mkdir_private(path)
    assert native.events == [
        ("security", "O:current-userD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;current-user)"),
        ("directory", str(path)), ("free-security",),
    ]
    assert all("launcher-default-owner" not in str(event) for event in native.events)


@pytest.mark.parametrize("failure", ["security", "native", "free-security"])
def test_windows_directory_creation_failure_stays_explicit(
    native: SimpleNamespace, failure: str,
) -> None:
    native.state["fail"] = failure
    with pytest.raises(OSError):
        owner.mkdir_private(Path("C:/example/private"))
    if failure == "security":
        assert len(native.events) == 1
    else:
        assert native.events[-1] == ("free-security",)


def test_existing_windows_directory_is_not_reowned(
    native: SimpleNamespace, tmp_path: Path,
) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    native.state.update(fail="native", error=183)
    owner.mkdir_private(existing, exist_ok=True)
    assert [event[0] for event in native.events] == ["security", "directory", "free-security"]
    with pytest.raises(FileExistsError):
        owner.mkdir_private(existing)


@pytest.mark.parametrize("mode,create,exclusive,truncated,access,disposition", [
    (1, True, True, False, 0x40000000, 1),
    (2, True, False, False, 0xC0000000, 4),
    (1, True, False, True, 0x40000000, 2),
    (2, False, False, True, 0xC0000000, 5),
    (0, False, False, False, 0x80000000, 3),
])
def test_windows_private_file_preserves_open_semantics_and_user_owner(
    native: SimpleNamespace, mode: int, create: bool, exclusive: bool,
    truncated: bool, access: int, disposition: int,
) -> None:
    flags = mode | (0x100 if create else 0) | (0x400 if exclusive else 0) | (0x200 if truncated else 0)
    assert owner.open_private(Path("C:/example/private/entry"), flags) == 12
    assert native.events[0] == (
        "security", "O:current-userD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;current-user)",
    )
    assert native.events[1:3] == [("file", access, disposition), ("free-security",)]
    assert native.events[-1] == ("noninherit",)
    assert not any(event[0].startswith("close-") for event in native.events)


@pytest.mark.parametrize("failure", [
    "security", "native", "free-security", "descriptor", "noninherit", "close-handle",
])
def test_windows_private_file_failure_releases_only_owned_resources(
    native: SimpleNamespace, failure: str,
) -> None:
    native.state["fail"] = failure
    with pytest.raises(OSError):
        owner.open_private(Path("C:/example/private/entry"), 0x101)
    closed = [event[0] for event in native.events if event[0].startswith("close-")]
    expected = (
        [] if failure in {"security", "native"}
        else ["close-descriptor"] if failure == "noninherit"
        else ["close-handle"]
    )
    assert closed == expected


def test_windows_private_file_collision_and_mode_refuse_without_adoption(native: SimpleNamespace) -> None:
    native.state.update(fail="native", error=80)
    with pytest.raises(FileExistsError):
        owner.open_private(Path("C:/example/private/entry"), 0x501)
    before = list(native.events)
    with pytest.raises(ValueError, match="0600"):
        owner.open_private(Path("C:/example/private/entry"), 0x501, 0o644)
    assert native.events == before


@pytest.mark.parametrize("path_owner,accepted", [
    ("current-user", True), ("launcher-default-owner", True), ("arbitrary-other-owner", False),
])
def test_existing_windows_owner_admission_is_not_broadened(
    native: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, path_owner: str, accepted: bool,
) -> None:
    def information(path: str, kind: int, fields: int, owner_pointer: Any, *args: Any) -> int:
        owner_pointer._obj.value = 54
        args[-1]._obj.value = 1234
        return 0

    native.advapi.GetNamedSecurityInfoW = Function(information)
    monkeypatch.setattr(owner, "_sid_text", lambda pointer: path_owner)
    if accepted:
        owner.assert_path_owner(Path("C:/example/existing"))
    else:
        with pytest.raises(OSError, match="foreign owner"):
            owner.assert_path_owner(Path("C:/example/existing"))
    assert native.events == [("free-security",)]


def test_private_store_directory_lock_and_record_use_shared_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner, "os", SimpleNamespace(name="posix", open=os.open))
    if os.name == "nt":
        # Native ownership is a separate lane; this unit checks portable I/O wiring.
        monkeypatch.setattr(store, "assert_path_owner", lambda path: None)
    calls = []
    mkdir, opened = store.mkdir_private, store.open_private

    def directory(path: Path, *, exist_ok: bool) -> None:
        calls.append(("directory", path))
        mkdir(path, exist_ok=exist_ok)

    def file(path: Path, flags: int, mode: int = 0o600) -> int:
        calls.append(("file", path, flags, mode))
        return opened(path, flags, mode)

    monkeypatch.setattr(store, "mkdir_private", directory)
    monkeypatch.setattr(store, "open_private", file)
    root = store._root(tmp_path / "private" / "inventory", create=True)
    identity = "a" * 32
    record = root / (identity + ".instance")
    with store._file_lock(record.with_suffix(".lock")):
        store._write(record, {"schema": "1", "instance_id": identity})
    store._write_cursor(root, identity)
    assert store._read(record)["instance_id"] == identity
    assert store._read_cursor(root) == identity
    assert [call[0] for call in calls] == ["directory", "directory", "file", "file", "file"]
    if os.name != "nt":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert calls[-1][2] == os.O_WRONLY | os.O_CREAT | os.O_EXCL


def test_existing_runtime_anchor_keeps_borrowed_readonly_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = []
    monkeypatch.setattr(store, "_plain", lambda path, **kwargs: observed.append(kwargs))
    assert store._runtime_anchor(tmp_path) == tmp_path
    assert observed == [{"directory": True, "owned": False}]


def test_portable_private_creation_keeps_native_bindings_lazy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(owner, "os", SimpleNamespace(name="posix", open=os.open))
    path = tmp_path / "portable"
    owner.mkdir_private(path)
    descriptor = owner.open_private(path / "entry", os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    os.close(descriptor)
    assert path.is_dir() and (path / "entry").is_file()
    assert "ctypes" not in vars(owner)
