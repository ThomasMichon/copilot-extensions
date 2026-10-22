"""Publication authority requires the original live management process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_command_binding import _snapshot, binding_fixture as binding_fixture
from test_installer_publication import _cell

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]


@contextmanager
def _live_management_owner(
    fixture: dict,
) -> Iterator[tuple[Path, dict, subprocess.Popen[str]]]:
    primitive = fixture["primitive"]
    root, arguments = _cell(fixture, {})
    program = """
import importlib.util,json,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("managed_owner_fixture",sys.argv[1])
helper=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=helper
spec.loader.exec_module(helper)
original=helper._current_environment
helper._current_environment=lambda **kw: original(**{**kw,"os_profile":Path(sys.argv[2])})
with helper.runtime_publication_lease(**json.loads(sys.argv[3])) as patch:
    print(json.dumps(patch),flush=True)
    sys.stdin.readline()
    os._exit(0)
"""
    child = subprocess.Popen(
        [
            sys.executable, "-I", "-B", "-c", program, primitive.__file__,
            str(fixture["home"]), json.dumps(arguments, default=str),
        ],
        env={**os.environ, **fixture["environment"]},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        assert child.stdout is not None and child.stderr is not None
        line = child.stdout.readline()
        assert line, child.stderr.read()
        patch = json.loads(line)
        yield root, {**arguments, "environment": {**arguments["environment"], **patch}}, child
    finally:
        if child.stdin is not None:
            child.stdin.close()
        child.wait(timeout=5)
        if child.stdout is not None:
            child.stdout.close()
        if child.stderr is not None:
            child.stderr.close()


def test_current_management_process_has_native_birth_identity(binding_fixture: dict) -> None:
    identity = binding_fixture["primitive"]._runtime_publication_process_identity(os.getpid())
    assert isinstance(identity, str) and identity


@pytest.mark.skipif(os.name != "nt", reason="Native Windows DWORD process identifier")
def test_windows_process_identifier_cannot_alias_another_native_pid(binding_fixture: dict) -> None:
    primitive = binding_fixture["primitive"]
    assert primitive._runtime_publication_process_identity(os.getpid() + (1 << 32)) is None


@pytest.mark.parametrize("identity", [None, "different-native-birth"])
def test_borrower_refuses_unknown_or_reused_management_process(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch, identity: str | None,
) -> None:
    fixture, primitive = binding_fixture, binding_fixture["primitive"]
    _, arguments = _cell(fixture, {})
    with primitive.runtime_publication_lease(**arguments) as patch:
        borrowed = {**arguments, "environment": {**arguments["environment"], **patch}}
        with monkeypatch.context() as changed:
            changed.setattr(primitive, "_runtime_publication_process_identity", lambda _pid: identity)
            before = _snapshot(fixture["home"])
            with pytest.raises(primitive.InstallationContextError):
                primitive.borrow_runtime_publication(**borrowed)
            assert _snapshot(fixture["home"]) == before


def test_missing_native_identity_refuses_before_management_lock_writes(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, primitive = binding_fixture, binding_fixture["primitive"]
    _, arguments = _cell(fixture, {})
    monkeypatch.setattr(primitive, "_runtime_publication_process_identity", lambda _pid: None)
    before = _snapshot(fixture["home"])
    with pytest.raises(primitive.InstallationContextError, match="process identity is unavailable"):
        with primitive.runtime_publication_lease(**arguments):
            pytest.fail("Unproved management process acquired publication custody")
    assert _snapshot(fixture["home"]) == before


@pytest.mark.parametrize("state,live", [("S", True), ("Z", False), ("X", False), ("?", False)])
def test_linux_identity_requires_nonterminal_native_state(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch, state: str, live: bool,
) -> None:
    primitive = binding_fixture["primitive"]
    boot = "12345678-1234-1234-1234-123456789abc"
    fields = [state, *(["0"] * 18), "12345"]
    record = "123 (manager ) name) " + " ".join(fields)
    monkeypatch.setattr(primitive, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(primitive, "Path", lambda path: SimpleNamespace(
        read_text=lambda **_kwargs: boot if str(path).endswith("boot_id") else record,
    ))
    assert primitive._runtime_publication_process_identity(123) == (
        f"linux:{boot}:12345" if live else None
    )


@pytest.mark.parametrize("opened,wait,times,expected", [
    (False, 258, True, None), (True, 0, True, None),
    (True, 0xFFFFFFFF, True, None), (True, 258, False, None),
    (True, 258, True, "windows:12345"),
])
def test_windows_identity_requires_live_handle_and_native_birth(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
    opened: bool, wait: int, times: bool, expected: str | None,
) -> None:
    import ctypes

    primitive = binding_fixture["primitive"]
    closed = []

    def open_process(access: int, inherit: bool, pid: int) -> int:
        assert access == 0x00100000 | 0x1000 and inherit is False and pid == 123
        return 456 if opened else 0

    def get_times(handle: int, creation: Any, *_rest: Any) -> bool:
        assert handle == 456
        creation._obj.dwLowDateTime = 12345
        return times

    kernel = SimpleNamespace(
        OpenProcess=open_process, WaitForSingleObject=lambda _handle, _timeout: wait,
        GetProcessTimes=get_times, CloseHandle=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr(primitive, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(primitive, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel, raising=False)
    assert primitive._runtime_publication_process_identity(123) == expected
    assert closed == ([456] if opened else [])


@pytest.mark.skipif(sys.platform != "linux", reason="Linux unreaped process evidence")
@pytest.mark.timeout(20)
def test_exited_unreaped_manager_cannot_authorize_publication(binding_fixture: dict) -> None:
    fixture, primitive = binding_fixture, binding_fixture["primitive"]
    with _live_management_owner(fixture) as (_, borrowed, child):
        primitive.borrow_runtime_publication(**borrowed)
        assert child.stdin is not None
        child.stdin.write("exit\n")
        child.stdin.flush()
        exited = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        assert exited.si_pid == child.pid and exited.si_status == 0
        os.kill(child.pid, 0)
        assert primitive._pid_is_live(child.pid), "the conservative existence probe is unchanged"
        before = _snapshot(fixture["home"])
        with pytest.raises(primitive.InstallationContextError):
            primitive.borrow_runtime_publication(**borrowed)
        assert _snapshot(fixture["home"]) == before
