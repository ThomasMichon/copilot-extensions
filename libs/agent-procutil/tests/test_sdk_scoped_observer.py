"""Synthetic USER32 and held-object controls with portable pump threads.

These are not admitted promotion units and never acquire a native desktop.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import replace
import hashlib
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from tools.promotion_unit_tests.test_sdk_desktop_observer import load

scoped = load("sdk_scoped_observer", "sdk_scoped_observer.py")
desktop = load("sdk_desktop_win32", "sdk_desktop_win32.py")
sources = sys.modules["sdk_visibility_sources"]
launch = load("sdk_scoped_spawn_controls", "test_windows_observation_spawn.py").launch


@pytest.fixture
def enrollment(tmp_path, monkeypatch):
    root = tmp_path / "source"
    consumer = sources.SCOPED_CONSUMER.partition("::")[0]
    rows = []
    for name in dict.fromkeys((*sources.SCOPED_FILES, consumer)):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (
            ("def " + sources.SCOPED_CONSUMER.partition("::")[2] + "():\n    return None\n").encode()
            if name == consumer else b"synthetic immutable source"
        )
        path.write_bytes(data)
        rows.append({
            "path": str(path), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    for filename in (*sources.RUNNER_FILES, "sdk_scoped_observer.py"):
        name = filename.removesuffix(".py")
        if name in sys.modules:
            monkeypatch.setattr(
                sys.modules[name], "__file__", str(root / "libs/agent-procutil/tests" / filename),
            )
    case = str(root / consumer) + "::" + sources.SCOPED_CONSUMER.partition("::")[2]
    return sources.enroll_scoped_consumer(root, case, rows, time.monotonic() + 10), rows


class API:
    def __init__(self):
        self.filters = {}
        self.queue = deque()
        self.calls = []
        self.exited = False
        self.birth = 700
        self.windows = {}
        self.visible = set()
        self.pump_error = False
        self.close_error = False
        self.pin = SimpleNamespace(
            pid=603, birth=700, gone=lambda: self.exited,
            close=lambda: self.calls.append("close-process-pin"),
        )
        self.kernel = SimpleNamespace(
            GetTickCount64=lambda: 100, GetThreadId=lambda _: 604,
            GetProcessIdOfThread=lambda _: 603,
            WaitForSingleObject=lambda handle, timeout: 0 if self.exited else 0x102,
            CloseHandle=lambda handle: self.calls.append(("close", handle)) or True,
        )
        self.user = SimpleNamespace(
            PeekMessageW=lambda *args: 0, SetWinEventHook=self.hook,
            UnhookWinEvent=self.unhook,
            EnumThreadWindows=self.enumerate_thread,
            IsWindowVisible=lambda hwnd: hwnd in self.visible,
            EnumWindows=lambda *args: pytest.fail("desktop enumeration"),
            GetForegroundWindow=lambda: pytest.fail("global foreground capture"),
        )

    def check(self, value, operation):
        if not value:
            raise scoped.ObservationBlocked(operation)

    def require_console(self):
        self.calls.append("console-prerequisites")

    def hook(self, first, last, module, callback, pid, thread, flags):
        assert pid == 603 and thread == 0 and flags == 0
        handle = len(self.filters) + 1
        self.filters[handle] = (first, last, callback, pid)
        self.calls.append(("subscribe", pid, thread))
        return handle

    def unhook(self, handle):
        self.calls.append(("unhook", handle))
        if self.close_error:
            return False
        self.filters.pop(handle)
        return True

    def enumerate_thread(self, thread, callback, unused):
        self.calls.append(("thread-windows", thread))
        for hwnd, identity in self.windows.items():
            if identity[1] == thread:
                if not callback(hwnd, unused):
                    return False
        return True

    def pin_handle(self, handle, pid):
        assert (handle, pid) == (601, 603)
        if self.exited:
            raise scoped.ObservationBlocked("root exited before pin")
        self.calls.append(("pin-held", handle, pid))
        return self.pin

    def duplicate(self, handle):
        assert handle == 602
        return 802

    def window_identity(self, hwnd):
        return self.windows[hwnd]

    thread_windows = desktop.WinAPI.thread_windows
    scoped_event_hooks = desktop.WinAPI.scoped_event_hooks

    def pump(self):
        if self.pump_error:
            raise scoped.ObservationBlocked("pump failed")
        while self.queue:
            pid, event, hwnd, thread = self.queue.popleft()
            for handle, (first, last, callback, subscribed_pid) in tuple(self.filters.items()):
                # The native filter, not ScopedObserver.event, determines acquisition.
                if pid == subscribed_pid and first <= event <= last:
                    self.calls.append(("acquired", hwnd))
                    callback(handle, event, hwnd, 0, 0, thread, 100)


@pytest.fixture
def root(enrollment, monkeypatch):
    import agent_procutil
    from agent_procutil import _windows_caller_owned as native

    value, _ = enrollment
    package = value.root / "libs/agent-procutil/src/agent_procutil"
    monkeypatch.setattr(agent_procutil, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(native, "__file__", str(package / "_windows_caller_owned.py"))
    api = API()
    accounting = {"total": 1}
    monkeypatch.setattr(native, "_api", lambda: None)
    monkeypatch.setattr(native, "_job_total", lambda *args: accounting["total"])
    monkeypatch.setattr(native, "_job_pids", lambda *args, **kwargs: set() if api.exited else {603})
    info = native._ProcessInfo()
    info.hProcess, info.hThread, info.dwProcessId, info.dwThreadId = 601, 602, 603, 604
    process = native._Process([], info, (None, None, None))
    process._observation_pending = True
    job = SimpleNamespace(handle=501)
    observer = scoped.ScopedObserver(api, value)
    context_owner = native._pre_resume_context
    token = context_owner.set((
        process, job, 601, 602, 501, value.deadline - 1, value.deadline,
    ))
    try:
        yield observer, api, process, job, accounting
    finally:
        try:
            observer.close()
        except scoped.ScopedCleanupError as error:
            assert error.scoped_observer is observer
            assert not observer.packet()["passed"]
            assert observer._cleanup_error is error
        context_owner.reset(token)


def admit(root):
    observer, _, process, job, _ = root
    observer.admit(process, job, observer.enrollment.deadline - 1, observer.enrollment.deadline)


def wait_for(predicate):
    end = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < end, "synthetic callback was not delivered"
        time.sleep(.001)


def test_unrelated_windows_and_focus_are_neither_acquired_nor_recorded(root):
    observer, api, _, _, _ = root
    api.windows[99] = (999, 888)
    api.visible.add(99)
    with observer:
        admit(root)
        api.queue.extend([(999, 0x8002, 99, 888), (999, 3, 99, 888)])
        wait_for(lambda: not api.queue)
        api.exited = True
        packet = observer.finish()
    assert packet["passed"] and packet["qualified"] and packet["raw_events"] == []
    assert packet["consumer_qualified"] is False
    assert packet["schema"] != "whole-desktop"
    assert not any(row[0] == "acquired" for row in api.calls if isinstance(row, tuple))
    assert api.filters == {} and packet["pins_closed"]
    closed = api.calls.index("close-process-pin")
    assert all(index < closed for index, row in enumerate(api.calls)
               if isinstance(row, tuple) and row[0] == "unhook")


@pytest.mark.parametrize("event", [0x8002, 3])
def test_owned_visibility_and_foreground_are_violations_in_raw_scoped_evidence(root, event):
    observer, api, _, _, _ = root
    admit(root)
    api.windows[20] = (603, 604)
    api.visible.add(20)
    api.queue.append((603, event, 20, 604))
    wait_for(lambda: bool(observer.events))
    api.exited = True
    packet = observer.finish()
    assert packet["qualified"] and not packet["passed"]
    assert packet["identity"]["birth"] == 700
    assert packet["raw_events"][0]["hwnd"] == 20
    assert packet["raw_events"][0]["violation"]


@pytest.mark.parametrize("fault", [
    "extra-process", "early-exit", "pump", "close", "callback", "source-changed", "tail-budget",
])
def test_gaps_and_unsettled_or_unadmitted_coverage_never_become_zero(root, fault):
    observer, api, _, _, accounting = root
    if fault == "early-exit":
        api.exited = True
        with pytest.raises(scoped.ObservationBlocked):
            admit(root)
    else:
        admit(root)
        if fault == "extra-process":
            accounting["total"] = 2
        elif fault == "pump":
            api.pump_error = True
            wait_for(lambda: bool(observer.errors))
        elif fault == "close":
            api.close_error = True
        elif fault == "source-changed":
            from pathlib import Path
            Path(observer.enrollment.sources[0][0]).write_bytes(b"changed during observation")
        elif fault == "tail-budget":
            observer.execution_deadline = time.monotonic() + .1
        else:
            observer.event(0x8002, 21, 0, 0, 604, 100, 0)
        api.exited = True
        expected = scoped.ScopedCleanupError if fault == "close" else scoped.ObservationBlocked
        message = "unconfirmed" if fault == "close" else "unqualified"
        with pytest.raises(expected, match=message):
            observer.finish()
    assert not observer.packet()["passed"]
    if fault == "close":
        assert not observer.pins_closed and observer.hooks.handles
        assert "close-process-pin" not in api.calls


@pytest.mark.parametrize("fault", [
    "wrong-pid", "no-owner-barrier", "reused-observer", "expired", "no-owner-context",
])
def test_forged_and_reused_launch_bindings_refuse_before_subscription(root, fault, monkeypatch):
    observer, api, process, job, _ = root
    if fault == "wrong-pid":
        process.pid = 999
    elif fault == "no-owner-barrier":
        process._observation_pending = False
    elif fault == "reused-observer":
        observer.close()
    elif fault == "no-owner-context":
        from agent_procutil import _windows_caller_owned as native
        monkeypatch.setattr(native, "_pre_resume_context", ContextVar("absent-admission", default=None))
    end = time.monotonic() - 1 if fault == "expired" else observer.enrollment.deadline - 1
    with pytest.raises((scoped.ObservationBlocked, AssertionError)):
        observer.admit(process, job, end, observer.enrollment.deadline)
    assert not api.filters


@pytest.mark.parametrize("fault", [
    "extra-case", "missing-source", "changed-source", "rebound", "expired", "loaded-helper",
    "former-mcp-case", "undeclared-case",
])
def test_source_owner_rejects_case_or_source_rebinding_before_native_effect(
    enrollment, fault, monkeypatch,
):
    value, rows = enrollment
    if fault == "extra-case":
        value = replace(value, case=value.case + "::extra")
    elif fault == "missing-source":
        value = replace(value, sources=value.sources[:-1])
    elif fault == "changed-source":
        from pathlib import Path
        Path(rows[0]["path"]).write_bytes(b"changed")
    elif fault == "rebound":
        value = replace(value, root=value.root.parent)
    elif fault == "loaded-helper":
        monkeypatch.setattr(sys.modules["sdk_native_win32"], "__file__", "foreign.py")
    elif fault == "former-mcp-case":
        value = replace(value, case=str(
            value.root / "plugins/agent-mcp/tests/test_installer_native_capture_ps1.py",
        ) + "::test_generated_cmd_uses_marker_owner_and_preserves_stdin[complete]")
    elif fault == "undeclared-case":
        from pathlib import Path
        case_file = value.root / sources.SCOPED_CONSUMER.partition("::")[0]
        data = b"def different_case():\n    return None\n"
        case_file.write_bytes(data)
        value = replace(value, sources=tuple(
            (name, len(data), hashlib.sha256(data).hexdigest()) if Path(name) == case_file
            else (name, length, digest) for name, length, digest in value.sources
        ))
    else:
        value = replace(value, deadline=time.monotonic() - 1)
    with pytest.raises(scoped.ObservationBlocked):
        scoped.ScopedObserver(API(), value)


def test_shared_terminal_host_cannot_expand_root_scope(root):
    observer, api, _, _, _ = root
    with pytest.raises(scoped.ObservationBlocked, match="unsupported"):
        observer.require_host(pid=900, thread=901, hwnd=902)
    assert not api.filters and not observer.events


def test_event_overflow_and_delayed_delivery_are_unqualified(root):
    observer, api, _, _, _ = root
    admit(root)
    api.windows[20] = (603, 604)
    observer.EVENT_LIMIT = 1
    observer.event(0x8000, 20, 0, 0, 604, 100, 0)
    observer.event(0x8002, 20, 0, 0, 604, 100, 0)
    observer.event(3, 20, 0, 0, 604, 100, 501)
    api.exited = True
    with pytest.raises(scoped.ObservationBlocked, match="unqualified"):
        observer.finish()
    assert observer.packet()["overflow"] and len(observer.events) == 1


@pytest.mark.parametrize("mutation", ["old-schema", "epoch", "identity", "cleanup", "scope", "consumer"])
def test_packet_verifier_cannot_relabel_global_or_rebound_evidence(root, mutation):
    observer, api, _, _, _ = root
    admit(root)
    api.exited = True
    original = observer.finish()
    packet = deepcopy(original)
    if mutation == "old-schema":
        packet = {"sealed": True, "passed": True, "raw_events": []}
    elif mutation == "epoch":
        packet["epoch"] = "0" * 32
    elif mutation == "identity":
        packet["identity"]["birth"] += 1
    elif mutation == "cleanup":
        packet["hooks_closed"] = False
    elif mutation == "scope":
        packet["scope"] = "whole-desktop"
    else:
        packet["consumer_qualified"] = True
    with pytest.raises(scoped.ObservationBlocked):
        sources.verify_scoped_root_packet(packet, observer.enrollment, original["identity"])


def test_message_pump_startup_gap_cannot_qualify_zero(root, monkeypatch):
    observer, api, _, _, _ = root
    observer.pin = api.pin
    observer.initial_thread = 604
    observer.thread_pin = 802
    ticks = iter([100, 100.6, 100.7])
    monkeypatch.setattr(scoped, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    observer._run()
    assert observer.max_gap > .5 and observer.errors
    assert not observer.packet()["passed"]


@pytest.mark.parametrize("fault", ["wrong-pid", "exited", "birth-query"])
def test_held_process_pin_uses_duplicate_object_and_retains_native_failures(fault):
    from tools.promotion_unit_tests.test_sdk_process_safety import process_bindings

    bindings = process_bindings()
    api = bindings.NativeProcessAPI()
    api.kernel.GetCurrentProcess.return_value = 1
    api.kernel.DuplicateHandle.side_effect = (
        lambda source, handle, target, out, *rest: setattr(out._obj, "value", 71) or True
    )
    api.kernel.GetProcessId.return_value = 8 if fault == "wrong-pid" else 7
    api.kernel.WaitForSingleObject.return_value = 0 if fault == "exited" else 258
    api.kernel.GetProcessTimes.return_value = fault != "birth-query"
    api.kernel.CloseHandle.return_value = True
    with pytest.raises(scoped.ObservationBlocked):
        api.pin_handle(41, 7)
    api.kernel.OpenProcess.assert_not_called()
    assert api.kernel.DuplicateHandle.call_args.args[1] == 41
    api.kernel.CloseHandle.assert_called_once_with(71)


@pytest.mark.parametrize("pid", [0, -1, True, 1 << 32, (1 << 32) + 603])
def test_root_mode_refuses_unbounded_or_truncated_native_process_filter(pid):
    api = API()
    pin = SimpleNamespace(pid=pid, gone=lambda: False)
    hooks = desktop.ScopedEventHooks(api, lambda *args: None, lambda error: None, pin)
    with pytest.raises(scoped.ObservationBlocked):
        hooks.start()
    assert not api.filters


def test_proven_precreation_thread_refusal_releases_pins_on_outer_context_cleanup(root, monkeypatch):
    observer, api, _, _, _ = root
    original_thread = threading.Thread
    monkeypatch.setattr(
        scoped.threading, "Thread", lambda **kwargs: original_thread.__new__(original_thread),
    )
    with pytest.raises(RuntimeError, match="__init__.*not called"):
        with observer:
            admit(root)
    assert observer.thread is None
    assert ("close", 802) in api.calls and "close-process-pin" in api.calls
    assert observer.pins_closed and not api.filters


def test_delayed_callback_alone_makes_the_scope_unqualified(root):
    observer, api, _, _, _ = root
    admit(root)
    api.windows[20] = (603, 604)
    observer.event(3, 20, 0, 0, 604, 100, 501)
    api.exited = True
    with pytest.raises(scoped.ObservationBlocked, match="unqualified"):
        observer.finish()
    assert observer.errors and not observer.events


def test_native_duplicate_close_failure_retains_the_exact_object_on_admission_error():
    from tools.promotion_unit_tests.test_sdk_process_safety import process_bindings

    bindings = process_bindings()
    api = bindings.NativeProcessAPI()
    api.kernel.GetCurrentProcess.return_value = 1
    api.kernel.DuplicateHandle.side_effect = (
        lambda source, handle, target, out, *rest: setattr(out._obj, "value", 71) or True
    )
    api.kernel.GetProcessId.return_value = 8
    api.kernel.CloseHandle.return_value = False
    with pytest.raises(scoped.ObservationBlocked) as failure:
        api.pin_handle(41, 7)
    assert failure.value.process_pin.handle == 71
    assert failure.value.admission_error is not failure.value
    api.kernel.OpenProcess.assert_not_called()
    api.kernel.CloseHandle.assert_called_once_with(71)


def test_root_exit_retains_subscription_until_queued_delivery_tail_is_observed(root):
    observer, api, _, _, _ = root
    admit(root)
    api.windows[20] = (603, 604)
    api.visible.add(20)
    original = api.pump
    delivered = []

    def pump():
        if (observer.root_exit_observed is not None and not delivered
                and time.monotonic() >= observer.root_exit_observed + .05):
            delivered.append(True)
            api.queue.append((603, 0x8002, 20, 604))
        original()

    api.pump = pump
    api.exited = True
    packet = observer.finish()
    assert delivered and packet["qualified"] and not packet["passed"]
    assert packet["delivery_tail"] >= .5 and packet["raw_events"][0]["hwnd"] == 20


@pytest.mark.parametrize("old_provider", ["no-formal-seam", "no-owner-boundary"])
def test_older_owner_cannot_silently_ignore_observation_in_generic_kwargs(root, monkeypatch, old_provider):
    from agent_procutil import _windows_caller_owned as native

    observer, api, _, _, _ = root
    if old_provider == "no-formal-seam":
        monkeypatch.setattr(native, "spawn", lambda *args, **kwargs: pytest.fail("unobserved launch"))
    else:
        monkeypatch.setattr(native, "require_pre_resume_admission", None)
    with pytest.raises(scoped.ObservationBlocked, match="lacks scoped"):
        scoped.ScopedObserver(api, observer.enrollment)
    assert not api.calls and not api.filters


@pytest.mark.parametrize("owner_cleanup_fails", [False, True])
@pytest.mark.parametrize("observer_cleanup", ["confirmed", "hook", "pin"])
def test_observer_context_settles_after_admitted_native_spawn_failure(
    root, launch, monkeypatch, owner_cleanup_fails, observer_cleanup,
):
    from agent_procutil import _windows_caller_owned as native
    from agent_procutil.owned_process import OwnedCleanupError

    observer, api, _, _, _ = root
    launch.clock[0] = time.monotonic()
    original = OSError("injected original ResumeThread failure")
    root_cleanup = OSError("injected original Job settlement failure")
    if owner_cleanup_fails:
        launch.kernel.cancel_cleanup = root_cleanup

    def resume(handle):
        assert handle == 602 and observer.barrier is not None
        assert observer.thread.is_alive()
        raise original

    monkeypatch.setattr(launch.kernel, "ResumeThread", resume)
    if observer_cleanup == "hook":
        api.close_error = True
    elif observer_cleanup == "pin":
        pin_type = sys.modules["sdk_native_win32"].HeldProcessPin
        pin = pin_type.__new__(pin_type)
        pin.api, pin.pid, pin.birth, pin.owned, pin.handle = api, 603, 700, False, 901
        api.pin = pin
        api.kernel.WaitForSingleObject = lambda handle, timeout: 258
        api.kernel.CloseHandle = lambda handle: api.calls.append(("close", handle)) or handle != 901

    body_error = None
    with pytest.raises((OSError, OwnedCleanupError)) as caught:
        with observer:
            try:
                native.spawn(
                    ["fixture.exe"], before_resume=observer.admit,
                    command_deadline=observer.enrollment.deadline - 1,
                    cleanup_deadline=observer.enrollment.deadline,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
            except BaseException as error:
                body_error = error
                raise
    error = caught.value
    assert error is body_error
    assert error.scoped_observer is observer
    assert error.scoped_observer_evidence == observer.packet()
    assert not observer.thread.is_alive() and not observer.packet()["passed"]
    assert observer.process is not None and observer.job is launch.jobs[0]
    if owner_cleanup_fails:
        assert isinstance(error, OwnedCleanupError)
        assert error.process is observer.process and error.job is observer.job
        assert error.operation_error is original and error.cleanup_error is root_cleanup
        assert error.__cause__ is original
        assert not observer.job.closed
    else:
        assert error is original and observer.job.closed
        assert observer.process._handle is None
    if observer_cleanup == "confirmed":
        assert observer.pins_closed and observer.hooks_closed
        assert not hasattr(error, "scoped_observer_cleanup_error")
    else:
        cleanup = error.scoped_observer_cleanup_error
        assert isinstance(cleanup, scoped.ScopedCleanupError)
        assert cleanup.scoped_observer is observer
        assert not cleanup.scoped_observer_evidence["pins_closed"]
        if observer_cleanup == "hook":
            assert observer.hooks.handles and not observer.hooks_closed
            assert "close-process-pin" not in api.calls
        else:
            assert observer.pin.handle == 901 and observer.hooks_closed
            assert api.calls.count(("close", 901)) == 1
        calls = list(api.calls)
        with pytest.raises(scoped.ScopedCleanupError) as repeated:
            observer.close()
        assert repeated.value is cleanup and api.calls == calls


def test_explicit_close_without_finish_never_qualifies_and_is_not_replayed_on_context_exit(root):
    observer, api, _, _, _ = root
    with observer:
        admit(root)
        observer.close()
        packet = observer.packet()
        calls = list(api.calls)
    assert api.calls == calls
    assert packet["pins_closed"] and packet["hooks_closed"]
    assert not packet["root_and_job_settled"] and not packet["qualified"] and not packet["passed"]


def test_normal_context_exit_surfaces_unconfirmed_cleanup_with_retained_custody(root):
    observer, api, _, _, _ = root
    with pytest.raises(scoped.ScopedCleanupError) as caught:
        with observer:
            admit(root)
            api.close_error = True
    assert caught.value.scoped_observer is observer
    assert observer.hooks.handles and observer.pin is api.pin
    assert not caught.value.scoped_observer_evidence["passed"]


def test_admission_retained_pin_failure_is_not_replayed_by_context_cleanup(root):
    observer, api, _, _, _ = root
    original = scoped.ObservationBlocked("admission retained an unconfirmed pin close")
    api.pin.handle = 901
    original.process_pin = api.pin

    def failed_pin(handle, pid):
        assert (handle, pid) == (601, 603)
        api.calls.append(("close", 901))
        raise original

    api.pin_handle = failed_pin
    with pytest.raises(scoped.ObservationBlocked) as caught:
        with observer:
            admit(root)
    assert caught.value is original
    assert original.scoped_observer is observer and observer.pin.handle == 901
    assert isinstance(original.scoped_observer_cleanup_error, scoped.ScopedCleanupError)
    assert api.calls.count(("close", 901)) == 1
    assert "close-process-pin" not in api.calls and not api.filters


@pytest.mark.parametrize("selection", ["absent", "unbound"])
def test_sdk_native_case_requires_driver_enrollment_before_native_binding(selection):
    module = load("sdk_scoped_native_case_controls", "test_caller_owned_native.py")
    config = SimpleNamespace()
    if selection == "unbound":
        config._agent_procutil_scoped_enrollment = object()
    expected = pytest.skip.Exception if selection == "absent" else scoped.ObservationBlocked
    with pytest.raises(expected):
        module.sdk_scoped_enrollment.__wrapped__(SimpleNamespace(config=config))


def test_owned_thread_enumeration_false_without_callbacks_is_a_valid_empty_result():
    api = API()
    api.user.EnumThreadWindows = lambda *args: False
    assert api.thread_windows(604, 603) == []


def test_owned_thread_enumeration_does_not_hide_callback_or_partial_failure():
    api = API()
    api.windows[20] = (999, 604)
    with pytest.raises(scoped.ObservationBlocked, match="identity changed"):
        api.thread_windows(604, 603)
    api.windows[20] = (603, 604)

    def incomplete(thread, callback, unused):
        assert callback(20, unused)
        return False

    api.user.EnumThreadWindows = incomplete
    with pytest.raises(scoped.ObservationBlocked):
        api.thread_windows(604, 603)
    failure = OSError("native enumeration failed")

    def native_failure(*args):
        raise failure

    api.user.EnumThreadWindows = native_failure
    with pytest.raises(OSError) as caught:
        api.thread_windows(604, 603)
    assert caught.value is failure


@pytest.mark.parametrize("change", ["exited", "rebound"])
def test_empty_enumeration_cannot_hide_a_changed_held_thread(root, change):
    observer, api, _, _, _ = root

    def empty(*args):
        if change == "exited":
            api.exited = True
        else:
            api.kernel.GetProcessIdOfThread = lambda handle: 999
        return False

    api.user.EnumThreadWindows = empty
    with pytest.raises(scoped.ObservationBlocked, match="barrier failed"):
        with observer:
            admit(root)
    assert any("thread exited or changed" in error for error in observer.errors)
    assert not observer.packet()["passed"]


def test_stalled_admission_retires_owned_root_before_blocking_observer_teardown(root, launch, monkeypatch):
    from agent_procutil import _windows_caller_owned as native

    observer, api, _, _, _ = root
    release = threading.Event()
    order = []
    monkeypatch.setattr(native, "time", time)
    api.pump = lambda: release.wait(1)
    terminate = native.JobHandle.terminate_and_wait

    def retire(job, timeout):
        order.append("root-retirement")
        assert not observer._close_attempted
        release.set()
        return terminate(job, timeout)

    monkeypatch.setattr(native.JobHandle, "terminate_and_wait", retire)
    start = time.monotonic()
    try:
        with pytest.raises(scoped.ObservationBlocked, match="barrier timed out"):
            with observer:
                native.spawn(
                    ["fixture.exe"], before_resume=observer.admit,
                    command_deadline=start + .08, cleanup_deadline=start + .35,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
        assert order == ["root-retirement"]
        assert observer.job.closed and observer.process._handle is None
        assert observer.pins_closed and not observer.thread.is_alive()
    finally:
        release.set()
        if observer.thread is not None:
            observer.thread.join(1)


def test_callback_lock_cannot_delay_root_retirement_or_error_evidence_past_cleanup_budget(
    root, launch, monkeypatch,
):
    from agent_procutil import _windows_caller_owned as native

    observer, api, _, _, _ = root
    release, entered = threading.Event(), threading.Event()
    order = []
    monkeypatch.setattr(native, "time", time)
    api.windows[20] = (603, 605)
    api.queue.append((603, 0x8002, 20, 605))

    def blocked_identity(hwnd):
        assert observer.lock._is_owned()
        entered.set()
        assert release.wait(2), "owned callback was not released"
        return api.windows[hwnd]

    api.window_identity = blocked_identity
    retire = native.JobHandle.terminate_and_wait

    def retirement(job, timeout):
        assert entered.is_set() and not release.is_set()
        order.append("root-retirement")
        return retire(job, timeout)

    original_close = observer.close

    def closing():
        order.append("observer-close")
        return original_close()

    monkeypatch.setattr(native.JobHandle, "terminate_and_wait", retirement)
    monkeypatch.setattr(observer, "close", closing)
    start = time.monotonic()
    cleanup = start + .3
    try:
        with pytest.raises(scoped.ObservationBlocked, match="barrier timed out") as caught:
            with observer:
                native.spawn(
                    ["fixture.exe"], before_resume=observer.admit,
                    command_deadline=start + .08, cleanup_deadline=cleanup,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
        assert time.monotonic() <= cleanup + .15
        assert order[:2] == ["root-retirement", "observer-close"]
        assert observer.job.closed and observer.process._handle is None
        assert caught.value.scoped_observer is observer
        assert isinstance(caught.value.scoped_observer_cleanup_error, scoped.ScopedCleanupError)
        evidence = caught.value.scoped_observer_evidence
        assert evidence["evidence_unknown"] is True and evidence["raw_events"] is None
        assert evidence["qualified"] is False and evidence["passed"] is False
        assert not release.is_set() and observer.thread.is_alive()
    finally:
        release.set()
        if observer.thread is not None:
            observer.thread.join(1)
            assert not observer.thread.is_alive()


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("terminated", [False, True])
def test_native_fixture_finally_requests_expired_root_termination_and_retains_custody(
    monkeypatch, interrupted, terminated,
):
    from agent_procutil import _windows_caller_owned as native
    from agent_procutil.owned_process import OwnedCleanupError

    module = load("sdk_scoped_fixture_retirement_controls", "test_caller_owned_native.py")
    calls = []
    primary = KeyboardInterrupt("fixture interrupted") if interrupted else None
    native_error = OSError("native termination request refused")
    process = SimpleNamespace(
        args=["owned-fixture"], _handle=601,
        retire_job=lambda *args: pytest.fail("positive retirement budget granted"),
        wait=lambda *args, **kwargs: pytest.fail("root wait after expiry"),
        close=lambda: pytest.fail("root handle closed without exit proof"),
    )
    job = SimpleNamespace(handle=501, close=lambda: pytest.fail("Job closed after expiry"))
    kernel = SimpleNamespace(
        TerminateJobObject=lambda handle, code: calls.append((handle, code)) or terminated,
        QueryInformationJobObject=lambda *args: pytest.fail("accounting query after expiry"),
    )
    monkeypatch.setattr(native, "_api", lambda: kernel)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 111))
    monkeypatch.setattr(module.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(module.ctypes, "WinError", lambda code: native_error, raising=False)
    with pytest.raises(OwnedCleanupError) as caught:
        module._retire_scoped_fixture(process, job, 110, primary)
    error = caught.value
    assert calls == [(501, 1)]
    assert error.process is process and error.job is job
    assert error.operation_error is primary and error._job_empty_confirmed is False
    assert error.__cause__ is (primary if primary is not None else error.cleanup_error)
    assert process._handle == 601 and job.handle == 501
    if not terminated:
        assert error.cleanup_error is native_error


def test_native_fixture_finally_preserves_nonexpired_retirement_sequence(monkeypatch):
    module = load("sdk_scoped_fixture_retirement_normal", "test_caller_owned_native.py")
    calls = []
    process = SimpleNamespace(
        args=["owned-fixture"], retire_job=lambda job, timeout: calls.append(("retire", job, timeout)),
        wait=lambda **kwargs: calls.append(("wait", kwargs["timeout"])),
        close=lambda: calls.append(("close",)),
    )
    job = object()
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 100))
    module._retire_scoped_fixture(process, job, 110, None)
    assert calls == [("retire", job, 10), ("wait", 10), ("close",)]


@pytest.mark.parametrize("release_after_retirement", [True, False])
def test_preident_start_interruption_retains_worker_and_pins_until_confirmed_completion(
    root, launch, monkeypatch, release_after_retirement,
):
    from agent_procutil import _windows_caller_owned as native

    observer, api, _, _, _ = root
    entered, release = threading.Event(), threading.Event()
    workers, order = [], []
    primary = KeyboardInterrupt("start interrupted after native worker creation")
    original_thread = threading.Thread
    monkeypatch.setattr(native, "time", time)

    def factory(**kwargs):
        worker = original_thread(**kwargs)
        original_set_ident = worker._set_ident

        def preident():
            entered.set()
            assert release.wait(2), "owned pre-ident worker was not released"
            original_set_ident()

        worker._set_ident = preident
        workers.append(worker)
        return worker

    start_name = (
        "_start_joinable_thread" if hasattr(threading, "_start_joinable_thread")
        else "_start_new_thread"
    )
    original_start = getattr(threading, start_name)

    def interrupted(*args, **kwargs):
        original_start(*args, **kwargs)
        assert entered.wait(1)
        raise primary

    retire = native.JobHandle.terminate_and_wait

    def retirement(job, timeout):
        assert workers[0].ident is None
        assert observer.thread is workers[0]
        assert not observer.pins_closed and "close-process-pin" not in api.calls
        order.append("root-retirement")
        result = retire(job, timeout)
        if release_after_retirement:
            release.set()
        return result

    monkeypatch.setattr(scoped.threading, "Thread", factory)
    monkeypatch.setattr(threading, start_name, interrupted)
    monkeypatch.setattr(native.JobHandle, "terminate_and_wait", retirement)
    start = time.monotonic()
    cleanup = start + .35
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            with observer:
                native.spawn(
                    ["fixture.exe"], before_resume=observer.admit,
                    command_deadline=start + .15, cleanup_deadline=cleanup,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
        assert caught.value is primary and order == ["root-retirement"]
        assert observer.job.closed and observer.process._handle is None
        assert observer.thread is workers[0]
        if release_after_retirement:
            assert observer.pins_closed
            assert observer.worker_started.is_set() and observer.worker_completed.is_set()
            assert not observer.thread.is_alive()
        else:
            assert time.monotonic() <= cleanup + .15
            assert workers[0].ident is None and not observer.pins_closed
            assert not observer.worker_started.is_set() and not observer.worker_completed.is_set()
            assert "close-process-pin" not in api.calls and ("close", 802) not in api.calls
            assert primary.scoped_observer is observer
            assert isinstance(primary.scoped_observer_cleanup_error, scoped.ScopedCleanupError)
            assert primary.scoped_observer_evidence["qualified"] is False
            assert primary.scoped_observer_evidence["passed"] is False
    finally:
        release.set()
        for worker in workers:
            assert worker._started.wait(1)
            worker.join(1)
            assert not worker.is_alive()
