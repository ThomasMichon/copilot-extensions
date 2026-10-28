"""Actor-free evidence and consent rules for the opt-in desktop qualification."""

from __future__ import annotations

import ast
import base64
from copy import deepcopy
import hashlib
import importlib.util
import io
import itertools
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs/agent-procutil/tests"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


observer = load("sdk_desktop_observer", "sdk_desktop_observer.py")
runner = load("unit_sdk_visibility", "sdk_visibility.py")


@pytest.fixture(autouse=True)
def deterministic_capture_clock(monkeypatch):
    ticks = itertools.count(100, 0.001)
    monkeypatch.setattr(observer, "time", SimpleNamespace(monotonic=lambda: next(ticks)))


class API:
    def __init__(self):
        self.current = 10
        self.windows = {10: (1, 2), 11: (3, 4)}

    def require_desktop(self):
        return {"available": True}

    def foreground(self):
        return self.current

    def window_identity(self, hwnd):
        return self.windows[hwnd]

    def raw_windows(self):
        return dict(self.windows)


def sealed(api=None):
    value = observer.DesktopObserver(api or API(), "unit")
    value.sample()
    value.hook_count = 2
    value.hooks_closed = True
    value.ended = observer.time.monotonic()
    value.sealed = True
    return value


def control_packet():
    def event(kind, hwnd, received):
        return {
            "kind": kind, "hwnd": hwnd, "received": received, "source_thread": 7,
            "generated_ms": int(received * 1000), "object_id": 0, "child_id": 0,
            "violation": kind in {"show", "foreground"}, "delay_ms": 0,
        }

    events = [
        event("show", 20, 1), event("foreground", 20, 2),
        event("foreground", 10, 3), event("destroy", 20, 4),
    ]
    return {
        "sealed": True, "hooks_closed": True, "hook_count": 2, "samples": 5,
        "maximum_gap": 0.02, "error": None, "callback_error": None,
        "cleanup_error": None, "overflow": False, "passed": False,
        "started": 0, "ended": 5, "raw_events": events,
    }, deepcopy(events)


def test_original_observer_limits_are_not_relaxed():
    value = sealed()
    packet = value.packet()
    assert packet["poll_interval"] == value.INTERVAL == 0.02
    assert value.MAX_GAP == 0.5
    assert packet["event_limit"] == value.EVENT_LIMIT == 4096


def test_empty_sealed_healthy_epoch_can_pass():
    value = sealed()
    assert value.packet()["passed"]
    snapshot = value.packet()
    snapshot["raw_events"].append({"kind": "forged"})
    assert value.packet()["raw_events"] == []


@pytest.mark.parametrize("hwnd", [0, 10, 999])
def test_all_raw_foreground_events_are_retained_without_name_filter(hwnd):
    value = observer.DesktopObserver(API(), "unit")
    value.event(3, hwnd, -4, 9, 123, 456, 0)
    assert value.events[0]["hwnd"] == hwnd and value.events[0]["violation"]
    value.hook_count, value.hooks_closed, value.samples, value.sealed = 2, True, 1, True
    assert not value.packet()["passed"]


@pytest.mark.parametrize("current", [0, 11])
def test_null_and_baseline_window_foreground_changes_are_not_ignored(current):
    api = API()
    value = observer.DesktopObserver(api, "unit")
    api.current = current
    value.sample()
    assert any(event["violation"] for event in value.events)


def test_unrelated_new_visible_window_is_not_filtered():
    api = API()
    value = observer.DesktopObserver(api, "unit")
    api.windows[99] = (100, 101)
    value.sample()
    assert any(event["kind"] == "sampled-visible" and event["hwnd"] == 99 for event in value.events)


def test_callback_delivery_over_original_limit_is_evidence_not_silence():
    value = observer.DesktopObserver(API(), "unit")
    value.event(0x8001, 20, 0, 0, 7, 1000, 501)
    assert any(event["kind"] == "delayed-event-delivery" for event in value.events)


def test_event_overflow_never_becomes_zero_change_proof():
    value = observer.DesktopObserver(API(), "unit")
    for _ in range(value.EVENT_LIMIT + 1):
        value.event(0x8000, 20, -4, 0, 7, 1000, 0)
    assert value.overflow and len(value.events) == value.EVENT_LIMIT
    assert not value.packet()["passed"]


def test_sealed_observation_rejects_late_mutations():
    value = sealed()
    with pytest.raises(observer.ObservationBlocked, match="sealed"):
        value.event(3, 10, 0, 0, 7, 1000, 0)
    with pytest.raises(observer.ObservationBlocked, match="completed"):
        value.sample()


def test_invalid_readiness_never_joins_the_pump_while_holding_its_capture_lock():
    value = observer.DesktopObserver(API(), "unit")
    value.hook_count = 2
    value.ready.set()
    value.record("foreground-null", hwnd=0)

    class Thread:
        def start(self):
            pass

        def join(self, timeout):
            assert timeout == 5
            assert not value.lock._is_owned()

        def is_alive(self):
            return False

    value.thread = Thread()
    with pytest.raises(observer.ObservationBlocked, match="unobservable"):
        value.__enter__()
    assert value.sealed


@pytest.mark.parametrize("gap,expected", [(0.5, True), (0.50001, False)])
def test_capture_gap_threshold_is_exact(gap, expected):
    value = sealed()
    value.max_gap = gap
    assert value.packet()["passed"] is expected


def test_calibration_requires_real_ordered_raw_witnesses():
    packet, witnesses = control_packet()
    observer.validate_calibration(packet, witnesses, 20, 7, 10)


@pytest.mark.parametrize("field,value", [
    ("sealed", False), ("hooks_closed", False), ("hook_count", 1),
    ("samples", 0), ("maximum_gap", 0.50001), ("overflow", True),
    ("error", "capture failed"), ("callback_error", "callback failed"),
    ("cleanup_error", "unhook failed"), ("passed", True),
])
def test_incomplete_or_relabelled_calibration_is_blocked(field, value):
    packet, witnesses = control_packet()
    packet[field] = value
    with pytest.raises(observer.ObservationBlocked):
        observer.validate_calibration(packet, witnesses, 20, 7, 10)


def test_sampled_or_api_restoration_cannot_replace_the_raw_event():
    packet, witnesses = control_packet()
    packet["raw_events"].pop(2)
    packet["sampled_restoration"] = True
    packet["set_foreground_returned_true"] = True
    with pytest.raises(observer.ObservationBlocked, match="absent"):
        observer.validate_calibration(packet, witnesses, 20, 7, 10)


def test_restoration_before_owned_activation_is_not_a_witness():
    packet, witnesses = control_packet()
    witnesses[2]["received"] = 1.5
    with pytest.raises(observer.ObservationBlocked, match="order"):
        observer.validate_calibration(packet, witnesses, 20, 7, 10)


@pytest.mark.parametrize("kind", ["coverage-gap", "delayed-event-delivery", "foreground-null"])
def test_unknown_or_delayed_control_intervals_cannot_qualify_calibration(kind):
    packet, witnesses = control_packet()
    packet["raw_events"].append({"kind": kind, "violation": True})
    with pytest.raises(observer.ObservationBlocked, match="unobservable"):
        observer.validate_calibration(packet, witnesses, 20, 7, 10)


@pytest.mark.parametrize("kind", ["foreground", "sampled-foreground", "sampled-visible"])
def test_unrelated_control_foreground_is_not_attributed_away(kind):
    packet, witnesses = control_packet()
    packet["raw_events"].append({"kind": kind, "hwnd": 999, "violation": True})
    with pytest.raises(observer.ObservationBlocked, match="unrelated"):
        observer.validate_calibration(packet, witnesses, 20, 7, 10)


def test_old_queued_restoration_is_not_new_raw_evidence():
    value = observer.DesktopObserver(API(), "unit")
    value.event(3, 10, 0, 0, 7, 99, 0)
    assert runner.raw_after(value, "foreground", 10, 0, 100) == []


@pytest.mark.parametrize("generated,tick,accepted", [
    (100, 100, False), (99, 100, False), (101, 100, True),
    (0, 0xFFFFFFFF, True), (10101, 100, False),
])
def test_raw_generation_requires_strictly_new_tick_even_if_delivery_is_later(
    generated, tick, accepted,
):
    event = {"received": 20, "generated_ms": generated}
    capture = SimpleNamespace(raw=lambda *args: [event])
    assert bool(runner.raw_after(capture, "foreground", 10, 10, tick)) is accepted


def test_private_command_waits_for_a_strictly_new_generation_before_api_action(monkeypatch):
    ticks = iter([100, 100, 101])
    pumps = []
    sleeps = []
    monkeypatch.setattr(runner, "time", SimpleNamespace(
        monotonic=time.monotonic, sleep=sleeps.append,
    ))
    api = SimpleNamespace(
        kernel=SimpleNamespace(GetTickCount64=lambda: next(ticks)),
        pump=lambda: pumps.append("pumped"),
    )
    runner.wait_generation_boundary(api, 100, time.monotonic() + 1)
    assert pumps == ["pumped", "pumped"]
    assert sleeps == [0.001, 0.001]


def test_generation_boundary_never_rearms_its_existing_deadline():
    api = SimpleNamespace(kernel=SimpleNamespace(GetTickCount64=lambda: 100))
    with pytest.raises(observer.ObservationBlocked, match="deadline"):
        runner.wait_generation_boundary(api, 100, time.monotonic() - 1)


def test_stale_generation_command_is_refused():
    api = SimpleNamespace(kernel=SimpleNamespace(GetTickCount64=lambda: 10101))
    with pytest.raises(observer.ObservationBlocked, match="stale"):
        runner.wait_generation_boundary(api, 100, time.monotonic() + 1)


@pytest.mark.parametrize("mode", ["probe", "canary"])
def test_no_native_binding_or_filesystem_effect_without_explicit_consent(monkeypatch, mode):
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native API reached without consent"))
    with pytest.raises(observer.ObservationBlocked, match="consent"):
        getattr(runner, mode)({}, False)


@pytest.mark.parametrize("deadline", [False, float("nan"), float("inf"), -1, 1e30])
def test_invalid_or_rearmed_deadlines_are_refused(deadline):
    with pytest.raises(observer.ObservationBlocked, match="deadline"):
        runner.validate_request("identity", {
            "nonce": "a" * 32, "deadline": deadline, "parent_pid": 1, "parent_birth": 2,
        })


def test_request_unknown_fields_are_not_ignored():
    with pytest.raises(observer.ObservationBlocked, match="fields"):
        runner.validate_request("identity", {
            "nonce": "a" * 32, "deadline": time.monotonic() + 10,
            "parent_pid": 1, "parent_birth": 2, "fallback": True,
        })


def test_known_unchanged_sources_are_verified_not_assumed(tmp_path):
    path = tmp_path / "source.py"
    path.write_bytes(b"fixed")
    manifest = [{"path": str(path), "bytes": 5, "sha256": hashlib.sha256(b"fixed").hexdigest()}]
    assert runner.verify_sources(manifest)[0]["bytes"] == 5
    path.write_bytes(b"other")
    with pytest.raises(observer.ObservationBlocked, match="changed"):
        runner.verify_sources(manifest)


def source_request(tmp_path):
    root = ROOT
    files = [
        {"path": str(root / name), "bytes": 0, "sha256": "b" * 64}
        for name in sys.modules["sdk_visibility_sources"].SDK_FILES
    ]
    return {
        "nonce": "a" * 32, "deadline": time.monotonic() + 10, "output": str(tmp_path / "output"),
        "python": "python", "sdk_root": str(root), "sources": files, "visible_controls": 1,
        "case_packets": case_packets(root),
    }


def case_packets(sdk):
    module = sys.modules["sdk_visibility_cases"]
    hosts = (r"C:\\Windows\\powershell.EXE", r"C:\\PowerShell\\pwsh.EXE")
    return [
        [
            str(sdk / module.NATIVE_TEST) + "::"
            + f"{module.FUNCTION}[{cycle}-{mode}-{composition}-{host}]"
            for mode, composition, host in itertools.product(
                ("completed", "abrupt", "caller-exit", "cancel"), ("path", "test-venv"), hosts,
            )
        ]
        for cycle in (0, 1)
    ]


def test_probe_manifest_requires_explicit_complete_sdk_closure_at_selected_root(tmp_path):
    request = source_request(tmp_path)
    assert runner.validate_request("probe", request) is request
    request["sources"].pop()
    with pytest.raises(observer.ObservationBlocked, match="SDK source closure"):
        runner.validate_request("probe", request)


def test_manifest_for_one_sdk_cannot_label_another_tree_verified(tmp_path):
    request = source_request(tmp_path)
    request["sdk_root"] = str(tmp_path / "another-sdk")
    with pytest.raises(observer.ObservationBlocked, match="another root"):
        runner.validate_request("probe", request)


@pytest.mark.parametrize("operation", ["probe", "worker"])
@pytest.mark.parametrize("foreign", [
    "sdk_visibility.py", "sdk_desktop_observer.py", "sdk_desktop_win32.py",
    "sdk_visibility_sources.py", "sdk_visibility_cases.py",
])
def test_running_runner_and_loaded_helpers_cannot_qualify_another_sdk(
    tmp_path, monkeypatch, operation, foreign,
):
    request = source_request(tmp_path)
    if operation == "worker":
        packets = request.pop("case_packets")
        request = {
            key: value for key, value in request.items() if key not in {"python", "visible_controls"}
        }
        request.update(parent_pid=1, parent_birth=2, nodes=packets[0], packet=0)
    origin = str(tmp_path / "older-sdk/libs/agent-procutil/tests" / foreign)
    if foreign == "sdk_visibility.py":
        monkeypatch.setattr(runner, "__file__", origin)
    else:
        monkeypatch.setitem(
            sys.modules, Path(foreign).stem, SimpleNamespace(__file__=origin),
        )
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding before root admission"))
    with pytest.raises(observer.ObservationBlocked, match="another SDK root"):
        runner.validate_request(operation, request)
    with pytest.raises(observer.ObservationBlocked, match="another SDK root"):
        if operation == "worker":
            runner.worker(request)
        else:
            runner.probe(request, True)


def test_manifest_cannot_mix_separately_located_runner_helpers_with_sdk_files(tmp_path, monkeypatch):
    request = source_request(tmp_path)
    binding = sys.modules["sdk_visibility_sources"]
    for item in request["sources"]:
        if Path(item["path"]).name in binding.RUNNER_FILES:
            item["path"] = str(tmp_path / "older-runner" / Path(item["path"]).name)
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding before root admission"))
    with pytest.raises(observer.ObservationBlocked, match="SDK source closure"):
        runner.validate_request("probe", request)
    with pytest.raises(observer.ObservationBlocked, match="SDK source closure"):
        runner.probe(request, True)


def test_ui_only_source_manifest_is_refused_before_any_native_or_visible_attempt(tmp_path, monkeypatch):
    request = source_request(tmp_path)
    request["sources"] = request["sources"][:5]
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding before SDK closure"))
    with pytest.raises(observer.ObservationBlocked, match="SDK source closure"):
        runner.probe(request, True)


def test_changed_bound_sdk_is_refused_before_native_binding(tmp_path, monkeypatch):
    request = source_request(tmp_path)
    monkeypatch.setattr(runner, "new_api", lambda: pytest.fail("native binding before source hashing"))
    with pytest.raises(observer.ObservationBlocked, match="changed"):
        runner.probe(request, True)


def test_worker_requires_same_sdk_source_manifest_as_parent(tmp_path):
    request = source_request(tmp_path)
    packets = request.pop("case_packets")
    request = {key: value for key, value in request.items() if key not in {"python", "visible_controls"}}
    request.update(parent_pid=1, parent_birth=2, nodes=packets[0], packet=0)
    assert runner.validate_request("worker", request) is request
    request.pop("sources")
    with pytest.raises(observer.ObservationBlocked, match="fields"):
        runner.validate_request("worker", request)


def test_required_source_closure_covers_current_package_and_every_sdk_fragment():
    binding = sys.modules["sdk_visibility_sources"]
    package = ROOT / "libs/agent-procutil/src/agent_procutil"
    assert set(binding.PACKAGE_FILES) == {path.name for path in package.glob("*.py")}
    syntax = ast.parse((ROOT / "libs/installation-context/installation_context.py").read_text())
    assignment = next(
        node for node in syntax.body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_FRAGMENT_FILES" for target in node.targets
        )
    )
    expected = {
        "libs/installation-context/" + name for name in ast.literal_eval(assignment.value)
    }
    assert expected <= set(binding.SDK_FILES)
    binding.require_sdk_sources(
        ROOT, [{"path": str(ROOT / name)} for name in binding.SDK_FILES],
        runner=SOURCE / "sdk_visibility.py",
    )


def test_worker_import_refuses_already_loaded_procutil_instead_of_relabelling_it(monkeypatch):
    binding = sys.modules["sdk_visibility_sources"]
    monkeypatch.setitem(sys.modules, "agent_procutil", SimpleNamespace(__file__="ambient"))
    with pytest.raises(observer.ObservationBlocked, match="already imported"):
        binding.bind_sdk_import(ROOT)


@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("reader_fails", [False, True])
def test_launcher_identity_failure_retires_exact_created_handle_before_constructor_returns(
    monkeypatch, tmp_path, cleanup_fails, reader_fails,
):
    calls = []

    class Handle:
        def __int__(self):
            return 42

        def Close(self):
            calls.append("handle-close")

    stderr = b"literal native precursor \xff"

    class ErrorStream(io.BytesIO):
        def read(self, size):
            data = super().read(size)
            if not data and reader_fails:
                raise OSError("injected reader error")
            return data

    def waited(timeout):
        calls.append("wait")
        process.returncode = 23

    process = SimpleNamespace(
        _handle=Handle(), pid=43, stdin=io.BytesIO(), stdout=io.BytesIO(b"partial stdout"),
        stderr=ErrorStream(stderr), wait=waited, returncode=None,
    )
    waits = iter([258, 0])
    api = SimpleNamespace(
        kernel=SimpleNamespace(
            WaitForSingleObject=lambda native, timeout: next(waits),
            TerminateProcess=lambda native, code: calls.append(("terminate-exact", native)) or not cleanup_fails,
        ),
        check=lambda value, stage: value or (_ for _ in ()).throw(
            observer.ObservationBlocked("injected termination failure")
        ),
        birth=lambda native: (_ for _ in ()).throw(observer.ObservationBlocked("injected birth")),
    )

    class Reader:
        def __init__(self, *, target, args, **kwargs):
            self.target, self.args, self.ident = target, args, None

        def start(self):
            self.ident = 1
            self.target(*self.args)

        def is_alive(self):
            return False

        def join(self, timeout):
            calls.append("reader-join")

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(runner.threading, "Thread", Reader)
    evidence = {}
    with pytest.raises(observer.ObservationBlocked, match="injected birth"):
        runner.Child(api, ["python"], {}, tmp_path, "launcher", evidence=evidence)
    assert calls == [("terminate-exact", 42), "reader-join", "reader-join", "wait", "handle-close"]
    assert evidence["launched"] and evidence["exit"] == 23
    assert evidence["stdout"] == "partial stdout"
    assert base64.b64decode(evidence["stderr_b64"]) == stderr
    assert evidence["stderr"] == r"literal native precursor \xff"
    assert evidence["cleanup_confirmed"] is not cleanup_fails
    assert evidence["readers_alive"] == [False, False]
    assert bool(evidence["reader_error"]) is reader_fails
    assert "injected birth" in evidence["error"]
    if cleanup_fails:
        assert "injected termination failure" in evidence["cleanup_error"]


def test_closed_child_before_ready_preserves_literal_error_without_waiting():
    child = runner.Child.__new__(runner.Child)
    child.reader_error = None
    child.lock = runner.threading.Lock()
    child.lines = runner.deque()
    child.process = SimpleNamespace(poll=lambda: 23)
    child.readers = []
    child.error = bytearray(b"WinAPI failure 5 before ready")
    with pytest.raises(observer.ObservationBlocked, match="exit 23.*WinAPI failure 5 before ready"):
        child.line()


@pytest.fixture
def probe_failure_fixture(monkeypatch, tmp_path):
    events = []
    active = []
    failure = {"stage": ""}
    identity = {"pid": 3, "birth": 4}
    original = {"hwnd": 10, "pid": 1, "thread": 2, "birth": 5}
    api = SimpleNamespace(current=10, visible=False)
    pin = SimpleNamespace(birth=5, gone=lambda: False, close=lambda: events.append("pin-close"))

    def restore(hwnd):
        assert active == ["calibration"]
        events.append("restore")
        api.current = hwnd
        return True

    api.user = SimpleNamespace(SetForegroundWindow=restore, IsWindow=lambda hwnd: api.visible)
    api.identity = lambda: {"pid": 9, "birth": 10}
    api.require_desktop = lambda: {}
    api.foreground = lambda: api.current
    api.window_identity = lambda hwnd: (1, 2) if hwnd == 10 else (3, 4)
    api.pin = lambda *args: pin
    api.check = lambda value, stage: value

    class Capture:
        def __init__(self, _api, name):
            self.name = name

        def __enter__(self):
            active.append(self.name)
            events.append(self.name + "-enter")
            return self

        def __exit__(self, *args):
            assert active == [self.name]
            events.append(self.name + "-seal")
            active.clear()

        def packet(self):
            return {"violations": [], "passed": True}

        def raw(self, kind, hwnd):
            return [{"kind": kind, "hwnd": hwnd, "received": time.monotonic(), "generated_ms": 101}]

    class FixtureChild:
        def __init__(self, _api, argv, request, directory, name, evidence=None):
            self.name = name
            self.action = ""
            self.process = SimpleNamespace(returncode=1 if failure["stage"] == name + "-exit" else 0)
            self.output = f"{name} partial stdout".encode()
            self.error = f"{name} literal native stderr".encode()
            self.evidence = evidence
            self.reader_error = None
            if evidence is not None:
                evidence.update(launched=True)
            if failure["stage"] == name + "-constructor":
                evidence.update(launched=False)
                raise observer.ObservationBlocked("injected launch before child creation")

        def admit(self, *args):
            if failure["stage"] == self.name + "-admit":
                raise observer.ObservationBlocked("injected admission")
            return {"identity": identity}

        def send(self, action):
            self.action = action
            if action == "show":
                api.current, api.visible = 20, True
            elif action == "restore":
                api.current = 10
            elif action == "close":
                api.visible = False
            if failure["stage"] == self.name + "-send":
                raise observer.ObservationBlocked("injected command")
            return time.monotonic(), 100

        def line(self):
            if failure["stage"] == "canary-response":
                raise observer.ObservationBlocked("injected visible response")
            if self.action == "show":
                return {"kind": "shown", "nonce": "a" * 32, "identity": identity,
                        "hwnd": 20, "thread": 4}
            if self.action == "restore":
                return {"kind": "restoring", "nonce": "a" * 32}
            return {"kind": "canary-result", "nonce": "a" * 32, "passed": True}

        def settled(self, *args):
            events.append(("settled", self.name, args[0], args[1].name))
            if failure["stage"] == self.name + "-settled":
                raise observer.ObservationBlocked("injected settlement")

        def close(self):
            expected = "calibration" if self.name == "canary" else "quiet-sdk"
            assert active == [expected]
            events.append(self.name + "-close")
            cleanup_fails = (
                failure["stage"] == self.name + "-cleanup"
                or failure.get("cleanup_stage") == self.name
            )
            if self.evidence is not None:
                self.evidence.update(
                    exit=self.process.returncode, stdout=self.output.decode(), stderr=self.error.decode(),
                    stdout_b64=base64.b64encode(self.output).decode(),
                    stderr_b64=base64.b64encode(self.error).decode(), reader_error=self.reader_error,
                    cleanup_confirmed=not cleanup_fails,
                    cleanup_errors=["injected child cleanup"] if cleanup_fails else [],
                )
            if cleanup_fails:
                raise observer.ObservationBlocked("injected child cleanup")

    monkeypatch.setattr(runner, "DesktopObserver", Capture)
    monkeypatch.setattr(runner, "Child", FixtureChild)
    monkeypatch.setattr(runner, "new_api", lambda: api)
    monkeypatch.setattr(runner, "require_sdk_sources", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "verify_sources", lambda *args: [])
    monkeypatch.setattr(runner, "validate_calibration", lambda *args: None)
    monkeypatch.setattr(runner, "verify_native_junit", lambda *args: {})
    monkeypatch.setattr(runner, "wait_for", lambda predicate, *args: predicate())
    # Avoid real sleeps without changing the observation module's deterministic clock.
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=time.monotonic, sleep=lambda _: None))
    request = {"nonce": "a" * 32, "deadline": time.monotonic() + 10,
               "output": str(tmp_path / "output"), "python": "python",
               "sdk_root": str(tmp_path / "sdk"), "sources": [], "visible_controls": 1,
               "case_packets": case_packets(tmp_path / "sdk")}
    return request, events, failure, original, pin, api


@pytest.mark.parametrize("stage", [
    f"worker-{packet:02}-{stage}" for packet in (1, 2) for stage in ("admit", "send", "settled", "exit")
])
def test_failed_sdk_worker_retires_inside_quiet_observer_before_seal(probe_failure_fixture, stage):
    request, events, failure, *_ = probe_failure_fixture
    failure["stage"] = stage
    with pytest.raises(observer.ObservationBlocked, match="injected|worker failed"):
        runner.probe(request, True)
    name = stage.rsplit("-", 1)[0]
    assert events.index(name + "-close") < events.index("quiet-sdk-seal") < events.index("pin-close")
    if name == "worker-01":
        assert "worker-02-close" not in events
    else:
        assert events.index("worker-01-close") < events.index("worker-02-close")
    report = json.loads((Path(request["output"]) / "visibility-result.json").read_text())
    assert len(report["workers"]) == (1 if name == "worker-01" else 2)
    record = report["workers"][-1]
    assert record["stderr"] == name + " literal native stderr"
    assert record["stdout"] == name + " partial stdout"
    assert base64.b64decode(record["stderr_b64"]) == record["stderr"].encode()
    assert record["cleanup_confirmed"] and not record["succeeded"]


def test_packet_two_primary_failure_survives_cleanup_failure_with_both_streams(probe_failure_fixture):
    request, events, failure, *_ = probe_failure_fixture
    failure.update(stage="worker-02-settled", cleanup_stage="worker-02")
    with pytest.raises(observer.ObservationBlocked, match="injected settlement"):
        runner.probe(request, True)
    report = json.loads((Path(request["output"]) / "visibility-result.json").read_text())
    assert len(report["workers"]) == 2
    first, second = report["workers"]
    assert first["succeeded"] and first["cleanup_confirmed"]
    assert "injected settlement" in report["error"]
    assert "injected settlement" in second["error"]
    assert "injected child cleanup" in second["cleanup_error"]
    assert second["stderr"] == "worker-02 literal native stderr"
    assert second["stdout"] == "worker-02 partial stdout"
    assert not second["cleanup_confirmed"] and not second["succeeded"]
    assert events.index("worker-02-close") < events.index("quiet-sdk-seal")


def test_packet_two_launch_failure_still_has_an_explicit_attempt_record(probe_failure_fixture):
    request, events, failure, *_ = probe_failure_fixture
    failure["stage"] = "worker-02-constructor"
    with pytest.raises(observer.ObservationBlocked, match="injected launch"):
        runner.probe(request, True)
    report = json.loads((Path(request["output"]) / "visibility-result.json").read_text())
    assert len(report["workers"]) == 2
    first, second = report["workers"]
    assert first["succeeded"] and first["cleanup_confirmed"]
    assert second["packet"] == 1 and not second["launched"] and not second["succeeded"]
    assert "injected launch" in second["error"]
    assert "worker-02-close" not in events
    assert events.index("worker-01-close") < events.index("quiet-sdk-seal")


def test_incomplete_control_cleanup_and_restoration_precede_calibration_seal(probe_failure_fixture):
    request, events, failure, *_ = probe_failure_fixture
    failure["stage"] = "canary-response"
    with pytest.raises(observer.ObservationBlocked, match="injected"):
        runner.probe(request, True)
    assert events.index("canary-close") < events.index("restore") < events.index("calibration-seal")
    assert "quiet-sdk-enter" not in events


def test_control_restoration_still_occurs_inside_capture_when_child_cleanup_fails(probe_failure_fixture):
    request, events, failure, *_ = probe_failure_fixture
    failure["stage"] = "canary-cleanup"
    # Force the error while the control still owns foreground, before its result.
    runner_fixture_child = runner.Child
    original_line = runner_fixture_child.line

    def failed_response(child):
        if child.action == "show":
            raise observer.ObservationBlocked("injected visible response")
        return original_line(child)

    runner_fixture_child.line = failed_response
    with pytest.raises(observer.ObservationBlocked, match="injected child cleanup"):
        runner.probe(request, True)
    assert events.index("canary-close") < events.index("restore") < events.index("calibration-seal")


def test_successful_children_close_inside_their_own_capture_epochs(probe_failure_fixture):
    request, events, *_ = probe_failure_fixture
    assert runner.probe(request, True) == 0
    assert events.index("canary-close") < events.index("calibration-seal")
    assert events.index("worker-01-close") < events.index("worker-02-close") < events.index("quiet-sdk-seal")
    assert events.count("calibration-enter") == events.count("quiet-sdk-enter") == 1
    report = json.loads((Path(request["output"]) / "visibility-result.json").read_text())
    assert [worker["packet"] for worker in report["workers"]] == [0, 1]
    assert [packet[-16:] for packet in report["pytest_packets"]] == request["case_packets"]
    waits = [event for event in events if isinstance(event, tuple) and event[1].startswith("worker")]
    assert len(waits) == 2 and waits[0][2] == waits[1][2]
    assert waits[0][3] == waits[1][3] == "quiet-sdk"


def test_native_scope_never_includes_the_synthetic_fixture_controls(tmp_path):
    sdk, output = tmp_path / "sdk", tmp_path / "output"
    packets = case_packets(sdk)
    for index, nodes in enumerate(packets):
        arguments = runner.native_arguments(sdk, output, nodes, index)
        assert arguments[-16:] == nodes
        assert arguments.count("-x") == 1 and "--noconftest" in arguments
        assert arguments[arguments.index("--rootdir") + 1] == str(sdk)
        assert "--junitxml=" + str(output / f"native-{index + 1:02}.xml") in arguments


@pytest.mark.parametrize("change", [
    "none", "failure", "error", "skipped", "missing", "duplicate", "other", "empty-classname",
])
def test_native_junit_requires_all_32_actual_case_results(tmp_path, change):
    sdk = tmp_path / "sdk"
    packets = case_packets(sdk)
    for index, nodes in enumerate(packets):
        suite = ET.Element("testsuite")
        for node in nodes:
            ET.SubElement(suite, "testcase", {
                "classname": "sdk.native.test_shell_caller_owned_native",
                "name": node.partition("::")[2],
            })
        ET.ElementTree(suite).write(tmp_path / f"native-{index + 1:02}.xml")
    path = tmp_path / "native-02.xml"
    suite = ET.parse(path).getroot()
    if change in {"failure", "error", "skipped"}:
        ET.SubElement(suite[0], change)
    elif change == "missing":
        suite.remove(suite[-1])
    elif change == "duplicate":
        suite[-1].set("name", suite[0].get("name"))
    elif change == "other":
        suite[0].set("name", "test_synthetic_publication_control")
    elif change == "empty-classname":
        suite[0].set("classname", "")
    ET.ElementTree(suite).write(path)
    if change == "none":
        assert runner.verify_native_junit(tmp_path, sdk, packets)["case_count"] == 32
    else:
        with pytest.raises(observer.ObservationBlocked, match="16-case"):
            runner.verify_native_junit(tmp_path, sdk, packets)


@pytest.mark.parametrize("change", [
    "one-packet", "oversize", "duplicate", "overlap", "wrong-function", "wrong-source",
    "missing-mode", "wrong-host",
])
def test_unbounded_or_incomplete_native_packet_matrices_are_refused(tmp_path, change):
    sdk = tmp_path / "sdk"
    packets = case_packets(sdk)
    if change == "one-packet":
        packets = [packets[0] + packets[1]]
    elif change == "oversize":
        packets[0].append(packets[1].pop())
    elif change == "duplicate":
        packets[0][-1] = packets[0][0]
    elif change == "overlap":
        packets[1] = packets[0][:]
    else:
        before, after = {
            "wrong-function": ("test_sdk_scope_retires_before_observer_cleanup", "test_other"),
            "wrong-source": ("test_shell_caller_owned_native.py", "other.py"),
            "missing-mode": ("-abrupt-", "-completed-"),
            "wrong-host": ("powershell.EXE", "another.EXE"),
        }[change]
        packets = [[node.replace(before, after) for node in packet] for packet in packets]
    with pytest.raises(observer.ObservationBlocked, match="native packet|native packets"):
        runner.validate_case_packets(sdk, packets)


def test_monolithic_case_function_is_not_a_bounded_literal_packet(tmp_path):
    module = sys.modules["sdk_visibility_cases"]
    sdk = tmp_path / "sdk"
    nodes = case_packets(sdk)[0]
    nodes[0] = str(sdk / module.NATIVE_TEST) + "::" + module.FUNCTION
    with pytest.raises(observer.ObservationBlocked, match="function cases"):
        runner.validate_case_packet(sdk, nodes)


@pytest.mark.parametrize("packet", [False, -1, 2, "0"])
def test_packet_ordinals_are_strict_before_any_command(tmp_path, packet):
    sdk = tmp_path / "sdk"
    with pytest.raises(observer.ObservationBlocked, match="ordinal"):
        runner.native_arguments(sdk, tmp_path, case_packets(sdk)[0], packet)
