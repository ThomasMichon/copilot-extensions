"""Kernel boot observations do not qualify recovery or native scope absence."""

import ctypes
from types import SimpleNamespace
from uuid import UUID

import pytest

from agent_remote_driver.session_host import boot_identity


@pytest.fixture
def windows_query(monkeypatch):
    observed = []
    identifier = UUID("12345678-9abc-4def-8123-456789abcdef")

    def query(information_class, buffer, size, returned):
        observed.append((information_class, size))
        assert query.argtypes == [
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        assert query.restype is ctypes.c_int32
        if query.error is not None:
            raise query.error
        ctypes.memmove(buffer, query.identifier.bytes_le, 16)
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32))[0] = query.length
        return query.status

    query.status, query.length, query.identifier, query.error = 0, 32, identifier, None
    monkeypatch.setattr(boot_identity, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name: SimpleNamespace(NtQuerySystemInformation=query)
        if name == "ntdll" else pytest.fail("unexpected native library"),
        raising=False,
    )
    return query, observed, identifier


def test_windows_host_observes_the_actual_boot_guid_without_generating_an_identity(windows_query):
    query, observed, identifier = windows_query
    assert boot_identity.windows_boot_id() == str(identifier)
    assert boot_identity.windows_boot_id() == str(identifier)
    changed = UUID("abcdef12-3456-4789-9abc-def012345678")
    query.identifier = changed
    assert boot_identity.windows_boot_id() == str(changed)
    assert observed == [(90, 32)] * 3


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
    windows_query, changes,
):
    query, observed, _ = windows_query
    for name, value in changes.items():
        setattr(query, name, value)
    assert boot_identity.windows_boot_id() == ""
    assert observed == [(90, 32)]


def test_missing_windows_query_does_not_fall_back_to_process_or_wall_time(windows_query, monkeypatch):
    _, observed, _ = windows_query
    monkeypatch.setattr(ctypes, "WinDLL", lambda _: SimpleNamespace())
    assert boot_identity.windows_boot_id() == ""
    assert observed == []


def test_other_platforms_do_not_query_the_windows_kernel(windows_query, monkeypatch):
    _, observed, _ = windows_query
    monkeypatch.setattr(boot_identity, "sys", SimpleNamespace(platform="unknown"))
    assert boot_identity.windows_boot_id() == ""
    assert observed == []
