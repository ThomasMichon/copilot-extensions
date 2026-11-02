"""Real process-worker command polling never dispatches desktop messages."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from tools.promotion_unit_tests.test_sdk_process_safety import process_bindings, runner


@pytest.mark.parametrize("scenario", [
    "immediate", "split", "empty", "generation-wait", "wrong-nonce",
    "stale", "closed", "pipelined", "oversized", "expired",
])
def test_real_process_command_retains_pipe_framing_correlation_and_deadlines(
    monkeypatch: pytest.MonkeyPatch, scenario: str,
) -> None:
    bindings = process_bindings()
    api = bindings.NativeProcessAPI()
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: 3))
    nonce = "1" * 32
    frame = (json.dumps({
        "nonce": "2" * 32 if scenario == "wrong-nonce" else nonce,
        "action": "run", "after_tick": 7,
    }) + "\n").encode()
    chunks = [frame[:20], frame[20:]] if scenario == "split" else [frame]
    if scenario == "pipelined":
        chunks = [frame + b"{}\n"]
    elif scenario == "oversized":
        chunks = [b"x" * 4096, b"x"]
    empty = [scenario == "empty"]

    def peek(handle: int, buffer: Any, length: int, read: Any, output: Any, remaining: Any) -> int:
        if scenario == "closed":
            return 0
        output._obj.value = 0 if empty[0] else len(chunks[0])
        empty[0] = False
        return 1

    def read(fd: int, length: int) -> bytes:
        assert fd == 0 and length <= 4096
        value = chunks.pop(0)
        assert len(value) <= length
        return value

    api.kernel.PeekNamedPipe.side_effect = peek
    api.kernel.GetTickCount64.side_effect = (
        [10008] if scenario == "stale" else [7, 8] if scenario == "generation-wait" else [8]
    )
    if scenario == "closed":
        bindings.ct.get_last_error = lambda: 109
    pump = Mock(wraps=api.pump)
    api.pump = pump
    monkeypatch.setattr(runner.os, "read", read)
    monkeypatch.setattr(runner.time, "monotonic", lambda: 100)
    sleep = Mock()
    monkeypatch.setattr(runner.time, "sleep", sleep)
    if scenario in {"wrong-nonce", "stale", "closed", "pipelined", "oversized", "expired"}:
        with pytest.raises(runner.ObservationBlocked):
            runner.command(api, nonce, 100 if scenario == "expired" else 101)
    else:
        assert runner.command(api, nonce, 101) == "run"
    assert pump.call_count == (
        0 if scenario == "expired" else 2 if scenario in {"split", "empty", "generation-wait", "oversized"}
        else 1
    )
    assert sleep.call_count == int(scenario in {"split", "empty", "generation-wait", "oversized"})
    assert not hasattr(api, "user") and bindings.loaded == ["kernel32", "advapi32"]


def test_real_process_worker_handshake_reaches_pytest_and_closes_parent_without_gui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = process_bindings()
    api = bindings.NativeProcessAPI()
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: 3))
    api.kernel.GetCurrentProcess.return_value = 1
    api.kernel.OpenProcess.return_value = 19
    api.kernel.WaitForSingleObject.return_value = 0x102
    api.kernel.CloseHandle.return_value = 1
    api.kernel.GetTickCount64.side_effect = [7, 8]

    def value(output: Any, selected: int) -> int:
        output._obj.value = selected
        return 1

    def birth(handle: int, first: Any, *rest: Any) -> int:
        first._obj.dwLowDateTime = 17 if handle == 19 else 23
        return 1

    api.kernel.IsProcessInJob.side_effect = lambda process, job, output: value(output, 1)
    api.advapi.OpenProcessToken.side_effect = lambda process, access, output: value(output, 6)
    api.advapi.GetTokenInformation.side_effect = lambda token, kind, output, size, written: value(output, 0)
    api.kernel.GetProcessTimes.side_effect = birth
    api.kernel.GetNamedPipeServerProcessId.side_effect = lambda handle, output: value(output, 11)
    frame = (json.dumps({"nonce": "1" * 32, "action": "run", "after_tick": 7}) + "\n").encode()
    api.kernel.PeekNamedPipe.side_effect = lambda handle, buffer, length, read, output, remaining: (
        value(output, len(frame))
    )
    pump = Mock(wraps=api.pump)
    api.pump = pump
    monkeypatch.setattr(runner.os, "read", lambda fd, length: frame)
    monkeypatch.setattr(runner.time, "monotonic", lambda: 100)
    monkeypatch.setattr(runner.time, "sleep", Mock())
    monkeypatch.setattr(runner, "require_sdk_sources", lambda *a, **k: None)
    monkeypatch.setattr(runner, "verify_sources", lambda *a: None)
    monkeypatch.setattr(runner, "validate_case_packet", lambda *a: None)
    monkeypatch.setattr(runner, "native_arguments", lambda *a: ["literal-node"])
    monkeypatch.setattr(runner, "bind_sdk_import", lambda *a: None)
    monkeypatch.setattr(runner, "new_process_api", lambda: api)
    monkeypatch.setattr(runner, "new_api", Mock(side_effect=AssertionError("desktop factory")))
    emitted: list[dict] = []
    monkeypatch.setattr(runner, "emit", emitted.append)
    execute = Mock(return_value=0)
    monkeypatch.setattr(pytest, "main", execute)
    request = {
        "sdk_root": "/fixture", "sources": [], "output": "/output", "nodes": ["literal-node"],
        "packet": 0, "nonce": "1" * 32, "deadline": 101, "parent_pid": 11, "parent_birth": 17,
    }
    assert runner.worker(request, process_only=True) == 0
    execute.assert_called_once_with(["literal-node"])
    assert emitted == [{"kind": "ready", "nonce": "1" * 32, "identity": {"pid": 7, "birth": 23}}]
    assert pump.call_count == 2
    assert [getattr(call.args[0], "value", call.args[0])
            for call in api.kernel.CloseHandle.call_args_list] == [6, 19]
    assert bindings.loaded == ["kernel32", "advapi32"] and not hasattr(api, "user")


def test_desktop_subclass_retains_actual_message_dispatch_instead_of_process_noop() -> None:
    source = Path(runner.__file__).with_name("sdk_desktop_win32.py")
    declared = next(node for node in ast.parse(source.read_bytes()).body
                    if isinstance(node, ast.ClassDef) and node.name == "WinAPI")
    pump = next(node for node in declared.body if isinstance(node, ast.FunctionDef) and node.name == "pump")
    owner = ast.ClassDef(
        name="DesktopAPI", bases=[], keywords=[], body=[pump], decorator_list=[], type_params=[],
    )
    namespace = {
        "wt": SimpleNamespace(MSG=lambda: SimpleNamespace(message=0)),
        "ct": SimpleNamespace(byref=lambda value: SimpleNamespace(_obj=value)),
        "ObservationBlocked": runner.ObservationBlocked,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[owner], type_ignores=[])),
                 str(source), "exec"), namespace)
    api = namespace["DesktopAPI"]()
    api.user = Mock()
    api.user.PeekMessageW.side_effect = [1, 0]
    assert api.pump() is None
    assert api.user.PeekMessageW.call_count == 2
    api.user.TranslateMessage.assert_called_once()
    api.user.DispatchMessageW.assert_called_once()
