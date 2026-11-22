"""Tests for the supersession self-retire decision."""

from __future__ import annotations

import os
import sys
from typing import Protocol, cast

import pytest

from single_instance_lease import is_superseded, pid_alive


class _NativeFunction(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, *_a: object, **_k: object) -> None: ...


def _table(
    pid: int, generation: int, *, bind: str | int = "127.0.0.1",
    port: int = 9281, key: str = "active",
) -> dict[str, dict[str, object]]:
    return {key: {"pid": pid, "generation": generation, "bind": bind, "port": port}}


def _yes(_host: str, _port: int) -> bool:
    return True


def _no(_host: str, _port: int) -> bool:
    return False


def test_superseded_by_live_newer_generation() -> None:
    table = _table(pid=222, generation=8)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is True


def test_not_superseded_when_successor_not_listening() -> None:
    table = _table(pid=222, generation=8)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_no) is False


def test_own_pid_active_is_never_superseded() -> None:
    table = _table(pid=111, generation=9)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is False


def test_equal_generation_is_not_supersession() -> None:
    table = _table(pid=222, generation=7)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is False


def test_older_generation_is_not_supersession() -> None:
    table = _table(pid=222, generation=5)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is False


def test_no_table_stays_alive() -> None:
    assert is_superseded(None, my_pid=1, my_generation=0, is_listening=_yes) is False
    assert is_superseded({}, my_pid=1, my_generation=0, is_listening=_yes) is False


def test_missing_or_broken_active_stays_alive() -> None:
    assert is_superseded({"active": None}, 1, 0, is_listening=_yes) is False
    assert is_superseded({"active": {"generation": 9}}, 1, 0, is_listening=_yes) is False
    assert is_superseded({"active": "x"}, 1, 0, is_listening=_yes) is False


# -- pid_alive: fail-open contract -------------------------------------------
#
# Only a definitive "no such process" answer may read as dead. An access-denied
# answer proves the pid exists (you cannot be denied access to a process that
# is not there), so it must read alive -- as must any error the platform check
# cannot interpret. Getting this wrong lets a live-but-protected/other-user
# successor read as "dead", which a reaper built on this predicate could act on.


def test_pid_alive_self_and_dead() -> None:
    assert pid_alive(os.getpid()) is True
    assert pid_alive(999_999_999) is False
    assert pid_alive(0) is False
    assert pid_alive(-1) is False
    assert pid_alive(None) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only liveness path")
def test_pid_alive_access_denied_reads_alive_on_windows() -> None:
    # pid 4 is Windows' "System" process: always running, but
    # PROCESS_QUERY_LIMITED_INFORMATION is typically refused for it from an
    # unprivileged token -- OpenProcess fails, yet the process is definitely
    # alive. This is the exact scenario the fail-open contract exists for.
    assert pid_alive(4) is True


def test_pid_alive_permission_denied_reads_alive_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX-only liveness path")

    def _raise(*_a: object, **_k: object) -> None:
        raise PermissionError()

    monkeypatch.setattr(os, "kill", _raise)
    assert pid_alive(4242) is True


def test_pid_alive_no_such_process_reads_dead_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX-only liveness path")

    def _raise(*_a: object, **_k: object) -> None:
        raise ProcessLookupError()

    monkeypatch.setattr(os, "kill", _raise)
    assert pid_alive(4242) is False


def test_pid_alive_unknown_oserror_reads_alive_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX-only liveness path")

    def _raise(*_a: object, **_k: object) -> None:
        raise OSError("nope")

    monkeypatch.setattr(os, "kill", _raise)
    assert pid_alive(4242) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only liveness path")
def test_pid_alive_ctypes_argument_error_reads_alive_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    # The declared ``argtypes`` make ctypes validate ``pid`` against a DWORD
    # before the Win32 call runs; a non-numeric/malformed recorded pid raises
    # ``ctypes.ArgumentError`` there instead of reaching OpenProcess. A
    # reaper/self-retire check iterating over untrusted table data must never
    # crash on this -- it's exactly the same "can't tell" case as any other
    # platform-probe failure, so it must read alive too.
    import ctypes

    class _RaisingDLL:
        def __getattr__(self, _name: str) -> _NativeFunction:
            def _raise(*_a: object, **_k: object) -> None:
                raise ctypes.ArgumentError("bad argument")

            return cast(_NativeFunction, _raise)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: _RaisingDLL())
    assert pid_alive(os.getpid()) is True



def test_bad_generation_value_stays_alive() -> None:
    table = {"active": {"pid": 2, "generation": "NaN", "port": 1, "bind": "127.0.0.1"}}
    assert is_superseded(table, my_pid=1, my_generation=0, is_listening=_yes) is False


def test_wildcard_bind_maps_to_loopback() -> None:
    seen: dict[str, str] = {}

    def probe(host: str, port: int) -> bool:
        seen["host"] = host
        return True

    table = _table(pid=222, generation=8, bind="0.0.0.0")
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=probe) is True
    assert seen["host"] == "127.0.0.1"


def test_ipv6_wildcard_bind_maps_to_loopback() -> None:
    seen: dict[str, str] = {}

    def probe(host: str, port: int) -> bool:
        seen["host"] = host
        return True

    table = _table(pid=222, generation=8, bind="::")
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=probe) is True
    assert seen["host"] == "::1"


def test_missing_bind_stays_alive_without_probing() -> None:
    # A malformed active record (no ``bind`` field) must never default to a
    # loopback probe target -- that could coincidentally find something else
    # listening on the recorded port and produce a false-positive
    # supersession (a live daemon incorrectly self-retiring).
    called: list[tuple[str, int]] = []

    def probe(host: str, port: int) -> bool:
        called.append((host, port))
        return True

    table = {"active": {"pid": 222, "generation": 8, "port": 9281}}
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=probe) is False
    assert called == []


def test_empty_string_bind_stays_alive_without_probing() -> None:
    called: list[tuple[str, int]] = []

    def probe(host: str, port: int) -> bool:
        called.append((host, port))
        return True

    table = _table(pid=222, generation=8, bind="")
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=probe) is False
    assert called == []


def test_non_string_bind_stays_alive_without_probing() -> None:
    called: list[tuple[str, int]] = []

    def probe(host: str, port: int) -> bool:
        called.append((host, port))
        return True

    table = _table(pid=222, generation=8, bind=123)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=probe) is False
    assert called == []


def test_zero_port_stays_alive() -> None:
    table = _table(pid=222, generation=8, port=0)
    assert is_superseded(table, my_pid=111, my_generation=7, is_listening=_yes) is False
