"""Kernel boot observations do not qualify recovery or native scope absence."""

import ctypes
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from agent_remote_driver.session_host import boot_identity


class _Query:
    def __init__(self) -> None:
        self.observed: list[tuple[int, int]] = []
        self.identifier = UUID("12345678-9abc-4def-8123-456789abcdef")
        self.status = 0
        self.length = 32
        self.error: Exception | None = None
        self.argtypes: list[object] = []
        self.restype: object | None = None

    def __call__(self, information_class: int, buffer: Any, size: int, returned: Any) -> int:
        self.observed.append((information_class, size))
        assert self.argtypes == [
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        assert self.restype is ctypes.c_int32
        if self.error is not None:
            raise self.error
        ctypes.memmove(buffer, self.identifier.bytes_le, 16)
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32))[0] = self.length
        return self.status


@pytest.fixture
def windows_query(monkeypatch: pytest.MonkeyPatch) -> _Query:
    query = _Query()
    monkeypatch.setattr(boot_identity, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name: SimpleNamespace(NtQuerySystemInformation=query)
        if name == "ntdll" else pytest.fail("unexpected native library"),
        raising=False,
    )
    return query


def test_windows_host_observes_the_actual_boot_guid_without_generating_an_identity(
    windows_query: _Query,
) -> None:
    query = windows_query
    assert boot_identity.windows_boot_id() == str(query.identifier)
    assert boot_identity.windows_boot_id() == str(query.identifier)
    changed = UUID("abcdef12-3456-4789-9abc-def012345678")
    query.identifier = changed
    assert boot_identity.windows_boot_id() == str(changed)
    assert query.observed == [(90, 32)] * 3


@pytest.mark.parametrize("changes", [
    {"status": -1073741822},
    {"status": -1073741820},
    {"status": 1},
    {"length": 0},
    {"length": 16},
    {"length": 24},
    {"length": 40},
    {"identifier": UUID(int=0)},
    {"error": OSError("native query unavailable")},
    {"error": AttributeError("native query unsupported")},
])
def test_unconfirmed_windows_boot_is_unavailable_without_retry_or_fabrication(
    windows_query: _Query, changes: dict[str, object],
) -> None:
    query = windows_query
    for name, value in changes.items():
        setattr(query, name, value)
    assert boot_identity.windows_boot_id() == ""
    assert query.observed == [(90, 32)]


def test_missing_windows_query_does_not_fall_back_to_process_or_wall_time(
    windows_query: _Query, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ctypes, "WinDLL", lambda _: SimpleNamespace())
    assert boot_identity.windows_boot_id() == ""
    assert windows_query.observed == []


def test_other_platforms_do_not_query_the_windows_kernel(
    windows_query: _Query, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(boot_identity, "sys", SimpleNamespace(platform="unknown"))
    assert boot_identity.windows_boot_id() == ""
    assert windows_query.observed == []
