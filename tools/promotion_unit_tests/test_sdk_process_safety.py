"""Actor-free process qualification keeps every desktop capability absent."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from tools.promotion_unit_tests.test_sdk_functional import functional_fixture as functional_fixture
from tools.promotion_unit_tests.test_sdk_functional import runner


def process_bindings() -> SimpleNamespace:
    source = Path(runner.__file__).with_name("sdk_native_win32.py")
    tree = ast.parse(source.read_bytes())
    tree.body = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        or isinstance(node, ast.ImportFrom) and node.module == "__future__"
    ]

    class Number:
        def __init__(self) -> None:
            self.value = 0
            self.dwHighDateTime = self.dwLowDateTime = 0

    libraries: dict[str, Any] = {}
    loaded: list[str] = []

    def library(name: str, *, use_last_error: bool) -> Any:
        assert name in {"kernel32", "advapi32"} and use_last_error is True
        loaded.append(name)
        value = Mock()
        libraries[name] = value
        return value

    namespace = {
        "ct": SimpleNamespace(
            WinDLL=library, POINTER=lambda value: value, c_ulonglong=Number, c_int=Number,
            byref=lambda value: SimpleNamespace(_obj=value), sizeof=lambda value: 4,
            get_last_error=lambda: 5,
        ),
        "wt": SimpleNamespace(
            HANDLE=Number, DWORD=Number, BOOL=Number, UINT=Number,
            ULONG=Number, FILETIME=Number, LPVOID=Number,
        ),
        "os": SimpleNamespace(name="nt", getpid=lambda: 7),
        "ObservationBlocked": runner.ObservationBlocked,
    }
    exec(compile(tree, str(source), "exec"), namespace)
    return SimpleNamespace(**namespace, libraries=libraries, loaded=loaded)


def test_process_binding_constructs_only_kernel_and_token_libraries() -> None:
    bindings = process_bindings()
    api = bindings.NativeProcessAPI()
    assert bindings.loaded == ["kernel32", "advapi32"]
    assert not hasattr(api, "user")
    assert not hasattr(api, "require_console") and not hasattr(api, "require_desktop")
    assert not hasattr(api, "foreground") and not hasattr(api, "object_name")
    assert api.kernel.GetNamedPipeServerProcessId.argtypes
    assert api.kernel.IsProcessInJob.argtypes


def test_actual_process_factory_cannot_construct_a_desktop_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = process_bindings()
    monkeypatch.setitem(sys.modules, "sdk_native_win32", SimpleNamespace(
        NativeProcessAPI=bindings.NativeProcessAPI,
        __file__=str(Path(runner.__file__).with_name("sdk_native_win32.py")),
    ))
    monkeypatch.setitem(sys.modules, "sdk_desktop_win32", SimpleNamespace(
        WinAPI=Mock(side_effect=AssertionError("desktop API constructed")),
    ))
    api = runner.new_process_api()
    assert type(api) is bindings.NativeProcessAPI
    assert bindings.loaded == ["kernel32", "advapi32"] and not hasattr(api, "user")


@pytest.mark.parametrize("contained,elevated", [(False, False), (True, True), (True, False)])
def test_process_admission_requires_job_and_unelevated_token_only(
    contained: bool, elevated: bool,
) -> None:
    bindings = process_bindings()
    api = bindings.NativeProcessAPI()
    calls: list[str] = []

    def value(output: Any, selected: int | bool) -> int:
        output._obj.value = selected
        return 1

    api.kernel.GetCurrentProcess.return_value = 1
    api.kernel.IsProcessInJob.side_effect = lambda process, job, output: (
        calls.append("job") or value(output, contained)
    )
    api.advapi.OpenProcessToken.side_effect = lambda process, access, output: (
        calls.append("token") or value(output, 2)
    )
    api.advapi.GetTokenInformation.side_effect = lambda token, kind, output, size, written: (
        calls.append("elevation") or value(output, elevated)
    )
    api.kernel.CloseHandle.side_effect = lambda token: calls.append("close") or 1
    if contained and not elevated:
        assert api.require_process_owner() == {"inherited_job_membership": True, "elevated": False}
    else:
        with pytest.raises(runner.ObservationBlocked, match="outer Job owner|unelevated"):
            api.require_process_owner()
    assert calls == (["job", "token", "elevation", "close"] if contained else ["job"])
    assert bindings.loaded == ["kernel32", "advapi32"]


@pytest.mark.parametrize("failure", ["token", "elevation", "close"])
def test_token_inspection_failures_remain_explicit_with_exact_handle_cleanup(failure: str) -> None:
    bindings = process_bindings()
    api = bindings.NativeProcessAPI()
    api.kernel.GetCurrentProcess.return_value = 1

    def opened(process: int, access: int, output: Any) -> int:
        output._obj.value = 2
        return int(failure != "token")

    api.advapi.OpenProcessToken.side_effect = opened
    api.advapi.GetTokenInformation.return_value = int(failure != "elevation")
    api.kernel.CloseHandle.return_value = int(failure != "close")
    with pytest.raises(runner.ObservationBlocked, match="WinError"):
        api.elevated()
    assert api.kernel.CloseHandle.call_count == (0 if failure == "token" else 1)


@pytest.mark.parametrize("server,live", [(7, True), (8, True), (7, False)])
def test_process_pipe_retains_original_parent_birth_and_closes_refused_handle(
    monkeypatch: pytest.MonkeyPatch, server: int, live: bool,
) -> None:
    bindings = process_bindings()
    api = bindings.NativeProcessAPI()
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda descriptor: 3))
    api.kernel.OpenProcess.return_value = 9
    api.kernel.CloseHandle.return_value = 1
    api.kernel.WaitForSingleObject.return_value = 0x102 if live else 0

    def birth(handle: int, first: Any, *rest: Any) -> int:
        first._obj.dwLowDateTime = 11
        return 1

    def pipe(handle: int, output: Any) -> int:
        output._obj.value = server
        return 1

    api.kernel.GetProcessTimes.side_effect = birth
    api.kernel.GetNamedPipeServerProcessId.side_effect = pipe
    request = {"parent_pid": 7, "parent_birth": 11}
    if server == 7 and live:
        pin = api.require_parent_pipe(request)
        assert pin.pid == 7 and pin.birth == 11 and pin.owned is False
        with pytest.raises(runner.ObservationBlocked, match="foreign"):
            pin.terminate_owned()
        api.kernel.TerminateProcess.assert_not_called()
        pin.close()
    else:
        with pytest.raises(runner.ObservationBlocked, match="original live caller"):
            api.require_parent_pipe(request)
    api.kernel.CloseHandle.assert_called_once_with(9)


@pytest.fixture
def process_fixture(
    functional_fixture: tuple, monkeypatch: pytest.MonkeyPatch,
) -> tuple:
    request, events, fault = functional_fixture
    fault["mode"] = "process-safety-worker"

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("process safety constructed a desktop API")

    api = SimpleNamespace(
        identity=lambda: {"pid": 7, "birth": 8},
        require_process_owner=lambda: events.append("process-owner") or {
            "inherited_job_membership": True, "elevated": False,
        },
    )
    monkeypatch.setattr(runner, "new_api", forbidden)
    monkeypatch.setattr(runner, "new_process_api", lambda: api)
    return request, events, fault


def test_process_packet_reports_no_desktop_observation_or_qualification(process_fixture: tuple) -> None:
    request, events, _ = process_fixture
    assert runner.process_safety(request) == 0
    report = json.loads((Path(request["output"]) / "process-safety-result.json").read_bytes())
    assert report["kind"] == "sdk-process-safety" and report["passed"] is True
    assert report["desktop_observed"] is False and report["desktop_qualified"] is False
    assert report["visible_controls_attempted"] == 0
    assert events[0] == "process-owner" and events.count("worker-04-close") == 1
    assert report["source_before"] == report["source_after"] == request["sources"]
    assert report["native_junit"] == {"case_count": 44}
    assert report["process_owner"] == {"inherited_job_membership": True, "elevated": False}
    assert not (Path(request["output"]) / "functional-result.json").exists()
    assert not (Path(request["output"]) / "visibility-result.json").exists()


@pytest.mark.parametrize("admission", ["refused", "expired"])
def test_process_owner_admission_never_launches_workers_after_refusal_or_deadline(
    process_fixture: tuple, monkeypatch: pytest.MonkeyPatch, admission: str,
) -> None:
    request, events, _ = process_fixture
    now = [1.0]
    request["deadline"] = 100
    monkeypatch.setattr(runner.time, "monotonic", lambda: now[0])

    def admit() -> dict[str, bool]:
        events.append("process-owner")
        if admission == "refused":
            raise runner.ObservationBlocked("original owner refused")
        now[0] = 100
        return {"inherited_job_membership": True, "elevated": False}

    monkeypatch.setattr(runner, "new_process_api", lambda: SimpleNamespace(
        require_process_owner=admit,
    ))
    with pytest.raises(runner.ObservationBlocked, match="refused|expired"):
        runner.process_safety(request)
    report = json.loads((Path(request["output"]) / "process-safety-result.json").read_bytes())
    assert events == ["process-owner"] and "workers" not in report
    assert report["passed"] is False and report["desktop_observed"] is False
    assert report["desktop_qualified"] is False


@pytest.mark.parametrize("stage", ["launch", "admit", "settled", "exit", "cleanup"])
def test_process_packet_failure_retains_evidence_and_never_launches_next_packet(
    process_fixture: tuple, stage: str,
) -> None:
    request, events, fault = process_fixture
    fault["at"] = "worker-01-" + stage
    with pytest.raises(runner.ObservationBlocked):
        runner.process_safety(request)
    report = json.loads((Path(request["output"]) / "process-safety-result.json").read_bytes())
    assert report["passed"] is False and report["desktop_observed"] is False
    assert report["desktop_qualified"] is False and report["visible_controls_attempted"] == 0
    assert len(report["workers"]) == 1 and not any("worker-02" in event for event in events)


@pytest.mark.parametrize("field", ["visible_controls", "original", "desktop_observed"])
def test_process_request_refuses_ui_fields_before_native_binding_or_output(
    process_fixture: tuple, field: str,
) -> None:
    request, events, _ = process_fixture
    request[field] = True
    with pytest.raises(runner.ObservationBlocked, match="fields"):
        runner.process_safety(request)
    assert events == [] and not Path(request["output"]).exists()


@pytest.mark.parametrize("expired", ["none", "hash", "owner", "pipe"])
def test_process_worker_admits_only_original_process_owner_and_pipe_within_deadline(
    monkeypatch: pytest.MonkeyPatch, expired: str,
) -> None:
    events: list[str] = []
    now = [1.0]

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("process worker reached a console or desktop API")

    def phase(name: str) -> None:
        events.append(name)
        if name == expired:
            now[0] = 100.0

    parent = SimpleNamespace(gone=lambda: False, close=lambda: events.append("closed"))
    api = SimpleNamespace(
        require_process_owner=lambda: phase("owner"),
        require_console=forbidden, require_desktop=forbidden,
        require_parent_pipe=lambda request: phase("pipe") or parent,
        identity=lambda: {"pid": 7, "birth": 8},
    )
    monkeypatch.setattr(runner.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runner, "require_sdk_sources", lambda *a, **k: events.append("sources"))
    monkeypatch.setattr(runner, "verify_sources", lambda *a: phase("hash"))
    monkeypatch.setattr(runner, "validate_case_packet", lambda *a: None)
    monkeypatch.setattr(runner, "native_arguments", lambda *a: ["literal-node"])
    monkeypatch.setattr(runner, "bind_sdk_import", lambda *a: events.append("import"))
    monkeypatch.setattr(runner, "new_api", forbidden)
    monkeypatch.setattr(runner, "new_process_api", lambda: events.append("api") or api)
    monkeypatch.setattr(runner, "emit", lambda value: events.append("ready"))
    monkeypatch.setattr(runner, "command", lambda *a: "run")
    monkeypatch.setattr(pytest, "main", lambda *a: events.append("pytest") or 0)
    request = {
        "sdk_root": "/fixture", "sources": [], "output": "/output", "nodes": ["literal-node"],
        "packet": 0, "nonce": "1" * 32, "deadline": 100,
    }
    if expired == "none":
        assert runner.worker(request, process_only=True) == 0
        assert events == ["sources", "hash", "import", "api", "owner", "pipe",
                          "ready", "pytest", "hash", "closed"]
    else:
        with pytest.raises(runner.ObservationBlocked, match="expired"):
            runner.worker(request, process_only=True)
        assert "ready" not in events and "pytest" not in events
        assert ("closed" in events) == (expired == "pipe")
        assert ("api" in events) == (expired != "hash")


def test_process_worker_refuses_ambiguous_admission_before_any_source_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "require_sdk_sources", Mock(side_effect=AssertionError("source read")))
    with pytest.raises(runner.ObservationBlocked, match="overlap"):
        runner.worker({}, functional_only=True, process_only=True)


@pytest.mark.parametrize("observer", [None, object()])
def test_process_packet_cannot_borrow_console_or_desktop_admission(observer: Any) -> None:
    with pytest.raises(runner.ObservationBlocked, match="borrow"):
        runner.run_workers(
            {}, Path("/fixture"), None, 100, 1, {}, observer,
            functional_only=observer is None, process_only=True,
        )


@pytest.mark.parametrize("mode", ["process-safety", "process-safety-worker"])
@pytest.mark.parametrize("visible", [False, True])
def test_process_cli_dispatch_is_explicit_and_refuses_visible_permission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, visible: bool,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    parser = Mock()
    parser.parse_args.return_value = SimpleNamespace(
        mode=mode, request=request_path, allow_visible_control_once=visible,
    )
    parser.error.side_effect = ValueError
    monkeypatch.setattr(runner.argparse, "ArgumentParser", lambda **kwargs: parser)
    monkeypatch.setattr(runner, "validate_request", lambda selected, value: value)
    safety, worker = Mock(return_value=0), Mock(return_value=0)
    monkeypatch.setattr(runner, "process_safety", safety)
    monkeypatch.setattr(runner, "worker", worker)
    if visible:
        with pytest.raises(ValueError):
            runner.main()
        safety.assert_not_called()
        worker.assert_not_called()
    else:
        assert runner.main() == 0
        if mode == "process-safety":
            safety.assert_called_once_with({})
            worker.assert_not_called()
        else:
            worker.assert_called_once_with({}, process_only=True)
            safety.assert_not_called()
