"""Actor-free functional qualification cannot activate a desktop control."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.promotion_unit_tests.test_sdk_desktop_observer import runner, source_request


def console_bindings():
    source = Path(runner.__file__).with_name("sdk_desktop_win32.py")
    declared = next(node for node in ast.parse(source.read_bytes()).body
                    if isinstance(node, ast.ClassDef) and node.name == "WinAPI")
    methods = {"check", "console_facts", "facts", "require_console", "require_desktop"}
    selected = [node for node in declared.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    assert {node.name for node in selected} == methods
    owner = ast.copy_location(ast.ClassDef(
        name="WinAPI", bases=[], keywords=[], body=selected, decorator_list=[], type_params=[],
    ), declared)

    class NumberType(type):
        def __mul__(cls, count):
            return lambda: [cls() for _ in range(count)]

    class Number(metaclass=NumberType):
        def __init__(self):
            self.value = 0

    # Execute the real method bodies with Python value doubles, never loading a native library.
    namespace = {
        "ct": SimpleNamespace(byref=lambda value: SimpleNamespace(_obj=value),
                              sizeof=lambda value: 4, get_last_error=lambda: 0),
        "wt": SimpleNamespace(BOOL=Number, HANDLE=Number, DWORD=Number),
        "os": os, "ObservationBlocked": runner.ObservationBlocked,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[owner], type_ignores=[])),
                 str(source), "exec"), namespace)
    return SimpleNamespace(WinAPI=namespace["WinAPI"])


@pytest.fixture
def functional_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple:
    request = source_request(tmp_path)
    request.pop("visible_controls")
    events: list[str] = []
    fault: dict[str, str] = {"at": ""}

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("functional qualification reached desktop observation or activation")

    class Child:
        def __init__(
            self, api: Any, argv: list[str], child_request: dict, directory: Path,
            name: str, evidence: dict,
        ) -> None:
            assert argv[-1] == "functional-worker"
            self.name, self.evidence = name, evidence
            self.process = SimpleNamespace(returncode=1 if fault["at"] == name + "-exit" else 0)
            evidence["launched"] = True
            events.append(name + "-launch")
            if fault["at"] == name + "-launch":
                evidence["launched"] = False
                raise runner.ObservationBlocked("literal launch failure")

        def admit(self, deadline: float, observer: Any = None) -> None:
            assert observer is None
            events.append(self.name + "-admit")
            if fault["at"] == self.name + "-admit":
                raise runner.ObservationBlocked("literal admission failure")

        def send(self, command: str) -> None:
            assert command == "run"
            events.append(self.name + "-run")

        def settled(self, deadline: float, observer: Any = None) -> None:
            assert observer is None
            events.append(self.name + "-settled")
            if fault["at"] == self.name + "-settled":
                raise runner.ObservationBlocked("literal settlement failure")

        def close(self) -> None:
            events.append(self.name + "-close")
            self.evidence.update(
                exit=self.process.returncode, cleanup_confirmed=True, cleanup_errors=[],
                stdout="retained stdout", stderr="retained stderr", readers_alive=[False, False],
            )
            if fault["at"] == self.name + "-cleanup" or fault.get("cleanup") == self.name:
                self.evidence["cleanup_confirmed"] = False
                raise runner.ObservationBlocked("literal cleanup failure")

    monkeypatch.setattr(runner, "Child", Child)
    monkeypatch.setattr(runner, "new_api", lambda: SimpleNamespace(identity=lambda: {"pid": 7, "birth": 8}))
    monkeypatch.setattr(runner, "DesktopObserver", forbidden)
    monkeypatch.setattr(runner, "activate_foreground", forbidden)
    monkeypatch.setattr(runner, "canary", forbidden)
    monkeypatch.setattr(runner, "require_sdk_sources", lambda *a, **k: None)
    monkeypatch.setattr(runner, "verify_sources", lambda values: list(values))
    monkeypatch.setattr(runner, "verify_native_junit", lambda *a: {"case_count": 44})
    return request, events, fault


def test_functional_native_packet_never_claims_desktop_qualification(functional_fixture: tuple) -> None:
    request, events, _ = functional_fixture
    assert runner.functional(request) == 0
    report = json.loads((Path(request["output"]) / "functional-result.json").read_bytes())
    assert report["kind"] == "sdk-functional"
    assert report["passed"] is True and report["desktop_qualified"] is False
    assert report["visible_controls_attempted"] == 0
    assert report["source_before"] == report["source_after"] == request["sources"]
    assert report["native_junit"] == {"case_count": 44}
    assert "quiet" not in report and "canary" not in report and "calibration" not in report
    assert not (Path(request["output"]) / "visibility-result.json").exists()
    assert len(report["workers"]) == 4
    assert events.count("worker-04-close") == 1


def test_functional_worker_requires_console_and_private_pipe_without_foreground(monkeypatch):
    events = []

    def forbidden(*args, **kwargs):
        pytest.fail("functional worker queried or controlled the foreground")

    api = SimpleNamespace(
        require_console=lambda: events.append("console"),
        require_desktop=forbidden,
        require_parent_pipe=lambda request: events.append("pipe") or SimpleNamespace(
            gone=lambda: False, close=lambda: events.append("closed"),
        ),
        identity=lambda: {"pid": 7, "birth": 8},
    )
    monkeypatch.setattr(runner, "require_sdk_sources", lambda *args, **kwargs: events.append("sources"))
    monkeypatch.setattr(runner, "verify_sources", lambda *args: events.append("hash"))
    monkeypatch.setattr(runner, "validate_case_packet", lambda *args: None)
    monkeypatch.setattr(runner, "native_arguments", lambda *args: ["literal-node"])
    monkeypatch.setattr(runner, "bind_sdk_import", lambda *args: events.append("import"))
    monkeypatch.setattr(runner, "new_api", lambda: api)
    monkeypatch.setattr(runner, "emit", lambda value: events.append("ready"))
    monkeypatch.setattr(runner, "command", lambda *args: "run")
    monkeypatch.setattr(pytest, "main", lambda args: events.append("pytest") or 0)
    request = {
        "sdk_root": "/fixture", "sources": [], "output": "/output",
        "nodes": ["literal-node"], "packet": 0, "nonce": "1" * 32, "deadline": 180,
    }
    assert runner.worker(request, functional_only=True) == 0
    assert events == ["sources", "hash", "import", "console", "pipe", "ready", "pytest", "hash", "closed"]


@pytest.mark.parametrize("functional_only", [False, True])
def test_worker_precondition_failure_never_falls_back_or_opens_pipe(monkeypatch, functional_only):
    events = []

    def refuse():
        events.append("console" if functional_only else "desktop")
        raise runner.ObservationBlocked("explicit precondition refusal")

    def forbidden(*args, **kwargs):
        pytest.fail("refused worker opened its pipe or used another precondition")

    api = SimpleNamespace(
        require_console=refuse if functional_only else forbidden,
        require_desktop=forbidden if functional_only else refuse,
        require_parent_pipe=forbidden,
    )
    monkeypatch.setattr(runner, "require_sdk_sources", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "verify_sources", lambda *args: None)
    monkeypatch.setattr(runner, "validate_case_packet", lambda *args: None)
    monkeypatch.setattr(runner, "native_arguments", lambda *args: [])
    monkeypatch.setattr(runner, "bind_sdk_import", lambda *args: None)
    monkeypatch.setattr(runner, "new_api", lambda: api)
    request = {"sdk_root": "/fixture", "sources": [], "output": "/output", "nodes": [], "packet": 0}
    with pytest.raises(runner.ObservationBlocked, match="explicit precondition"):
        runner.worker(request, functional_only=functional_only)
    assert events == ["console" if functional_only else "desktop"]


@pytest.mark.parametrize("contained", [False, True])
def test_functional_console_admission_never_reads_foreground(monkeypatch, contained):
    bindings = console_bindings()
    api = bindings.WinAPI.__new__(bindings.WinAPI)
    events = []

    def in_job(process, job, output):
        output._obj.value = contained
        events.append("job")
        return 1

    def forbidden(*args, **kwargs):
        pytest.fail("console admission reached a foreground or desktop qualification query")

    facts = {
        "elevated": False, "ssh": False, "remote_session": False, "remote_control": False,
        "station": "WinSta0", "station_visible": True, "thread_desktop": "Default",
        "input_desktop": "Default", "session": 1, "console_session": 1,
    }
    api.kernel = SimpleNamespace(GetCurrentProcess=lambda: 1, IsProcessInJob=in_job)
    api.console_facts = lambda: events.append("console-facts") or dict(facts)
    api.facts = api.foreground = api.window_identity = api.require_desktop = forbidden
    if contained:
        result = api.require_console()
        assert result == {**facts, "inherited_job_membership": True}
        assert events == ["job", "console-facts"]
    else:
        with pytest.raises(runner.ObservationBlocked, match="outer Job owner"):
            api.require_console()
        assert events == ["job"]


def test_actual_console_facts_retain_native_prerequisites_and_close_handles_without_foreground(monkeypatch):
    bindings = console_bindings()
    api = bindings.WinAPI.__new__(bindings.WinAPI)
    closed = []

    def value(output, selected):
        output._obj.value = selected
        return 1

    def user_object(station, information, flags, size, written):
        assert station == 3 and information == 1
        flags[2] = 1
        return 1

    def forbidden(*args, **kwargs):
        pytest.fail("local-console facts read foreground/window state")

    api.kernel = SimpleNamespace(
        GetCurrentProcess=lambda: 1, GetCurrentThreadId=lambda: 6,
        IsProcessInJob=lambda process, job, output: value(output, True),
        CloseHandle=lambda token: closed.append(token.value) or 1,
        WTSGetActiveConsoleSessionId=lambda: 1,
    )
    api.advapi = SimpleNamespace(
        OpenProcessToken=lambda process, access, output: value(output, 2),
        GetTokenInformation=lambda token, information, output, size, written: value(output, 0),
    )
    api.user = SimpleNamespace(
        GetProcessWindowStation=lambda: 3, GetUserObjectInformationW=user_object,
        OpenInputDesktop=lambda flags, inherited, access: 4,
        GetThreadDesktop=lambda thread: 5,
        CloseDesktop=lambda handle: closed.append(handle) or 1,
        GetSystemMetrics=lambda metric: 0,
        GetForegroundWindow=forbidden, GetShellWindow=forbidden,
        IsWindowVisible=forbidden, GetWindowThreadProcessId=forbidden,
    )
    api.object_name = lambda handle: {3: "WinSta0", 4: "Default", 5: "Default"}[handle]
    api.session = lambda pid: 1 if pid == os.getpid() else forbidden()
    api.foreground = api.window_identity = forbidden
    for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    facts = api.require_console()
    assert facts == {
        "elevated": False, "ssh": False, "remote_session": False, "remote_control": False,
        "station": "WinSta0", "station_visible": True, "thread_desktop": "Default",
        "input_desktop": "Default", "session": 1, "console_session": 1,
        "inherited_job_membership": True,
    }
    assert closed == [2, 4]


@pytest.mark.parametrize("visible,same_session", [(False, True), (True, False), (True, True)])
def test_strict_desktop_admission_keeps_foreground_visibility_and_session_requirements(visible, same_session):
    bindings = console_bindings()
    api = bindings.WinAPI.__new__(bindings.WinAPI)

    def in_job(process, job, output):
        output._obj.value = True
        return 1

    facts = {
        "elevated": False, "ssh": False, "remote_session": False, "remote_control": False,
        "station": "WinSta0", "station_visible": True, "thread_desktop": "Default",
        "input_desktop": "Default", "session": 1, "console_session": 1,
        "foreground_visible": visible, "same_session": same_session,
    }
    api.kernel = SimpleNamespace(GetCurrentProcess=lambda: 1, IsProcessInJob=in_job)
    api.facts = lambda: dict(facts)
    if visible and same_session:
        assert api.require_desktop() == {**facts, "inherited_job_membership": True}
    else:
        with pytest.raises(runner.ObservationBlocked, match="normal local console"):
            api.require_desktop()


@pytest.mark.parametrize("visible_flag", [False, True])
def test_functional_worker_dispatch_is_explicit_and_refuses_visible_permission(tmp_path, monkeypatch, visible_flag):
    source = source_request(tmp_path)
    request = {key: source[key] for key in ("nonce", "deadline", "sdk_root", "output", "sources")}
    request.update(parent_pid=7, parent_birth=8, nodes=source["case_packets"][0], packet=0)
    path = tmp_path / "functional-worker.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    calls = []
    monkeypatch.setattr(runner, "worker", lambda value, **options: calls.append((value, options)) or 0)
    arguments = ["sdk_visibility.py", "functional-worker"]
    if visible_flag:
        arguments.append("--allow-visible-control-once")
    monkeypatch.setattr(runner.sys, "argv", [*arguments, str(path)])
    if visible_flag:
        with pytest.raises(SystemExit) as failure:
            runner.main()
        assert failure.value.code == 2 and calls == []
    else:
        assert runner.main() == 0
        assert calls == [(request, {"functional_only": True})]


@pytest.mark.parametrize("stage", ["launch", "admit", "settled", "exit", "cleanup"])
def test_functional_failure_preserves_evidence_and_stops_before_next_packet(
    functional_fixture: tuple, stage: str,
) -> None:
    request, events, fault = functional_fixture
    fault["at"] = "worker-02-" + stage
    with pytest.raises(runner.ObservationBlocked):
        runner.functional(request)
    report = json.loads((Path(request["output"]) / "functional-result.json").read_bytes())
    assert report["passed"] is False and report["desktop_qualified"] is False
    assert report["visible_controls_attempted"] == 0 and report["error"]
    assert len(report["workers"]) == 2
    assert "worker-03-launch" not in events
    if stage != "launch":
        assert events.count("worker-02-close") == 1
        assert report["workers"][1]["stderr"] == "retained stderr"


def test_functional_mode_refuses_visible_control_fields_before_native_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = source_request(tmp_path)
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding before request refusal"))
    with pytest.raises(runner.ObservationBlocked, match="fields"):
        runner.validate_request("functional", request)
    request.pop("visible_controls")
    assert runner.validate_request("functional", request) is request


def test_functional_mode_hashes_source_before_native_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = source_request(tmp_path)
    request.pop("visible_controls")
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding before source hashing"))
    with pytest.raises(runner.ObservationBlocked, match="qualification source changed"):
        runner.functional(request)
    assert not Path(request["output"]).exists()


def test_functional_primary_error_survives_secondary_cleanup(functional_fixture: tuple) -> None:
    request, _, fault = functional_fixture
    fault.update(at="worker-02-settled", cleanup="worker-02")
    with pytest.raises(runner.ObservationBlocked, match="literal settlement failure"):
        runner.functional(request)
    report = json.loads((Path(request["output"]) / "functional-result.json").read_bytes())
    assert "literal settlement failure" in report["error"]
    assert "literal cleanup failure" in report["workers"][1]["cleanup_error"]
    assert report["workers"][1]["cleanup_confirmed"] is False
    assert report["passed"] is False


def test_functional_primary_error_survives_expired_final_clock(
    functional_fixture: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, _, _ = functional_fixture
    request["deadline"] = 110
    clock = {"now": 100}
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    def fail(*args: Any, **kwargs: Any) -> None:
        clock["now"] = 111
        raise runner.ObservationBlocked("original worker failure")

    monkeypatch.setattr(runner, "run_workers", fail)
    with pytest.raises(runner.ObservationBlocked, match="original worker failure"):
        runner.functional(request)
    report = json.loads((Path(request["output"]) / "functional-result.json").read_bytes())
    assert "original worker failure" in report["error"]
    assert report["deadline_error"] == "immutable functional deadline exceeded"
    assert report["passed"] is False and report["desktop_qualified"] is False


def test_functional_request_expired_by_hashing_cannot_reach_native_binding(
    functional_fixture: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, events, _ = functional_fixture
    clock = {"now": 100}
    request["deadline"] = 110
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    def hashing(values: list[dict]) -> list[dict]:
        clock["now"] = 111
        return values

    monkeypatch.setattr(runner, "verify_sources", hashing)
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding after expired hashing"))
    with pytest.raises(runner.ObservationBlocked, match="deadline"):
        runner.functional(request)
    assert events == []
    assert not Path(request["output"]).exists()


def test_functional_request_expired_by_parent_identity_cannot_launch(
    functional_fixture: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, events, _ = functional_fixture
    clock = {"now": 100}
    request["deadline"] = 110
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    def identity() -> dict[str, int]:
        clock["now"] = 111
        return {"pid": 7, "birth": 8}

    monkeypatch.setattr(runner, "new_api", lambda: SimpleNamespace(identity=identity))
    with pytest.raises(runner.ObservationBlocked, match="deadline"):
        runner.functional(request)
    assert events == []


def test_functional_request_expired_between_packets_cannot_launch_next(
    functional_fixture: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, events, _ = functional_fixture
    clock = {"now": 100}
    request["deadline"] = 110
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    original_close = runner.Child.close

    def close(child: Any) -> None:
        original_close(child)
        if child.name == "worker-01":
            clock["now"] = 111

    monkeypatch.setattr(runner.Child, "close", close)
    with pytest.raises(runner.ObservationBlocked, match="deadline"):
        runner.functional(request)
    assert "worker-01-close" in events and "worker-02-launch" not in events


def test_owned_child_request_expired_during_request_write_cannot_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100}
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    def write(*args: Any) -> None:
        clock["now"] = 111

    monkeypatch.setattr(runner, "write_new", write)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: pytest.fail("expired child spawn"))
    monkeypatch.setattr(runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    evidence: dict[str, Any] = {}
    with pytest.raises(runner.ObservationBlocked, match="deadline"):
        runner.Child(SimpleNamespace(), ["python"], {"deadline": 110}, tmp_path, "late", evidence=evidence)
    assert evidence["launched"] is False
