"""Portable observer sensitivity, raw evidence, lifecycle and WinEvent ownership controls."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_automatic_updates_native import DesktopStub


@pytest.fixture
def probe(monkeypatch):
    scenario = Path(__file__).resolve().parents[2] / "tools/clean-room/scenarios/manager-automatic-updates"
    monkeypatch.syspath_prepend(str(scenario))
    return importlib.import_module("windows_api")


def test_direct_foreground_identity_is_not_joined_to_earlier_window_enumeration(probe):
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)
    api.front = 20
    api.window_identity = lambda hwnd: (123456, 77) if hwnd == 20 else (api.root.pid, 42)
    observer.sample()
    packet = observer.packet()
    assert {event["kind"] for event in packet["violations"]} == {
        "foreground", "foreground-missing-enumeration",
    }
    assert all(event.get("identity") == (123456, 77) for event in packet["violations"])
    assert all(event["hwnd"] == 20 for event in packet["violations"])
    assert packet["raw_events"][0]["neighbors_before"] == []
    assert packet["raw_events"][0]["neighbor_after"]["index"] == 0


def test_null_and_direct_identity_failure_remain_distinct_and_observation_continues(probe):
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)
    api.front = 0
    observer.sample()
    api.front = 20
    api.window_identity = Mock(side_effect=RuntimeError("window destroyed before PID query"))
    observer.sample()
    assert observer.samples == 2
    packet = observer.packet()
    kinds = {event["kind"] for event in packet["violations"]}
    assert "foreground-null" in kinds and "foreground-identity-failed" in kinds
    assert "visible-capture-failed" in kinds
    assert any("destroyed" in event.get("error", "") for event in packet["raw_events"])
    assert packet["passed"] is False


def test_sampling_never_runs_toolhelp_process_pinning_or_lineage(probe):
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)
    api.refresh_processes = Mock(side_effect=AssertionError("slow census on critical path"))
    api.process = Mock(side_effect=AssertionError("slow pin on critical path"))
    api.lineage = Mock(side_effect=AssertionError("slow lineage on critical path"))
    for _ in range(3):
        observer.sample()
    assert observer.samples == 3
    api.refresh_processes.assert_not_called()
    api.process.assert_not_called()
    api.lineage.assert_not_called()


def test_hook_retains_a_b_a_between_identical_foreground_polls(probe):
    observer = probe.WindowObserver(DesktopStub(probe))
    observer.sample()
    observer.event(3, 20, 0, 0, 123, 100)
    observer.event(3, 10, 0, 0, 124, 101)
    observer.sample()
    events = observer.packet()["violations"]
    assert [event["hwnd"] for event in events] == [20, 10]
    assert all(event["kind"] == "foreground-event" for event in events)
    assert events[0]["source_thread"] == 123 and events[0]["generated_ms"] == 100


def test_short_lived_show_is_not_lost_when_enrichment_is_impossible(probe):
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)
    observer.event(0x8000, 20, 0, 0, 123, 100)
    observer.event(0x8002, 20, 0, 0, 123, 101)
    observer.event(0x8003, 20, 0, 0, 123, 102)
    observer.event(0x8001, 20, 0, 0, 123, 103)
    observer.sample()
    packet = observer.packet()
    assert len(packet["raw_events"]) == 4
    assert [event["kind"] for event in packet["violations"]] == ["show-event"]
    assert packet["violations"][0]["pid"] is None
    assert packet["violations"][0]["hwnd"] == 20


def test_delayed_non_window_callback_is_a_coverage_failure(probe):
    observer = probe.WindowObserver(DesktopStub(probe))
    observer.event(0x8000, 20, -4, 3, 123, 100, 501)
    packet = observer.packet()
    assert [event["kind"] for event in packet["violations"]] == ["delayed-event-delivery"]
    assert packet["raw_events"][0]["delivery_delay_ms"] == 501
    assert packet["passed"] is False


def test_callback_error_and_overflow_are_explicit_failures(probe):
    observer = probe.WindowObserver(DesktopStub(probe))
    observer.callback_failed(ValueError("invalid callback"))
    observer.EVENT_LIMIT = 2
    for i in range(3):
        observer.event(0x8001, i, 0, 0, 12, i)
    packet = observer.packet()
    assert len(packet["raw_events"]) == 2 and packet["overflow"] is True
    assert packet["callback_error"] == "ValueError: invalid callback"
    assert packet["passed"] is False


def test_capture_brackets_include_slow_calls_and_retain_neighbors(probe, monkeypatch):
    module = importlib.import_module("windows_observation")
    now = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)
    raw = api.raw_windows
    observer.sample()

    def slow_windows():
        now[0] += 0.6
        return raw()

    api.raw_windows = slow_windows
    observer.sample()
    packet = observer.packet()
    assert packet["maximum_observation_gap_seconds"] == pytest.approx(0.6)
    gap = next(event for event in packet["raw_events"] if event["kind"] == "coverage-gap")
    assert gap["channel"] == "visible"
    assert gap["end"] - gap["start"] == pytest.approx(0.6)
    assert gap["neighbors_before"][0]["index"] == 0
    assert gap["neighbor_after"]["index"] == 1
    assert packet["capture_stage_maximum_seconds"]["visible"] == pytest.approx(0.6)


def test_readiness_precedes_body_and_final_sample_precedes_sealing(probe):
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)
    with observer:
        assert observer.ready.is_set() and observer.samples >= 1
        before = observer.samples
        assert observer.packet()["passed"] is False
    packet = observer.packet()
    assert packet["sealed"] is True and packet["hooks_closed"] is True
    assert packet["samples"] > before and packet["passed"] is True
    assert packet["first_capture"]["foreground"] >= packet["observation_started"]
    assert packet["last_capture"]["visible"] <= packet["observation_ended"]
    with pytest.raises(probe.NativeBlocked, match="observer-sealed"):
        observer.sample()
    with pytest.raises(probe.NativeBlocked, match="observer-sealed"):
        observer.add_pid(os.getpid())


def test_completed_packet_is_immutable_and_never_extends_its_end_time(probe, monkeypatch):
    module = importlib.import_module("windows_observation")
    observer = probe.WindowObserver(DesktopStub(probe))
    with observer:
        pass
    first = observer.packet()
    monkeypatch.setattr(module.time, "monotonic", lambda: first["observation_ended"] + 100)
    altered = observer.packet()
    altered["raw_events"].append({"kind": "caller-mutation"})
    assert observer.packet() == first


def test_invalid_first_capture_blocks_before_fixture_launch(probe):
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)
    api.front = 0
    launched = False
    with pytest.raises(probe.NativeBlocked, match="observer-not-ready"):
        with observer:
            launched = True
    assert launched is False and not observer.thread.is_alive()
    assert observer.packet()["passed"] is False


def test_long_stop_tail_and_cleanup_failure_cannot_pass(probe, monkeypatch):
    module = importlib.import_module("windows_observation")
    now = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)

    def close():
        now[0] += 0.6
        raise RuntimeError("unhook failed")

    api.event_hooks = lambda *args: SimpleNamespace(
        count=2, start=lambda: None, drain=lambda: None, close=close,
    )
    observer.stop.set()
    observer._run()
    packet = observer.packet()
    assert packet["cleanup_error"] == "RuntimeError: unhook failed"
    assert "coverage-tail-gap" in {event["kind"] for event in packet["raw_events"]}
    assert packet["maximum_observation_gap_seconds"] == pytest.approx(0.6)
    assert packet["passed"] is False


def test_slow_startup_blocks_readiness_even_with_normal_final_captures(probe, monkeypatch):
    module = importlib.import_module("windows_observation")
    now = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)

    def start():
        now[0] += 0.6

    api.event_hooks = lambda *args: SimpleNamespace(
        count=2, start=start, drain=lambda: None, close=lambda: None,
    )
    with pytest.raises(probe.NativeBlocked, match="observer-not-ready"):
        with observer:
            pytest.fail("invalid start reached fixture")
    assert observer.packet()["maximum_observation_gap_seconds"] == pytest.approx(0.6)


def hooks_fixture():
    module = importlib.import_module("windows_events")
    user = SimpleNamespace(**{
        name: Mock() for name in ("SetWinEventHook", "UnhookWinEvent",
                                 "PeekMessageW", "TranslateMessage", "DispatchMessageW")
    })
    user.SetWinEventHook.side_effect = [11, 12]
    user.PeekMessageW.return_value = False
    user.UnhookWinEvent.return_value = True
    kernel = SimpleNamespace(GetTickCount64=Mock(return_value=0x100000005))
    receive, failure = Mock(), Mock()
    return module.WinEventHooks(user, kernel, receive, failure), user, receive, failure


def test_hooks_use_registering_thread_no_skip_flags_and_checked_unhook(probe):
    hooks, user, receive, failure = hooks_fixture()
    hooks.start()
    assert hooks.count == 2
    assert [call.args[:2] for call in user.SetWinEventHook.call_args_list] == [
        (3, 3), (0x8000, 0x8003),
    ]
    assert all(call.args[-3:] == (0, 0, 0) for call in user.SetWinEventHook.call_args_list)
    hooks.callback(11, 3, 20, 0, 0, 99, 0xfffffffe)
    assert receive.call_args.args == (3, 20, 0, 0, 99, 0xfffffffe, 7)
    failure.assert_not_called()
    hooks.drain()
    hooks.close()
    assert not hooks.handles and user.UnhookWinEvent.call_count == 2


def test_hooks_report_callback_failure_without_ctypes_discarding_it(probe):
    hooks, _, receive, failure = hooks_fixture()
    receive.side_effect = RuntimeError("callback witness")
    hooks.callback(11, 3, 20, 0, 0, 99, 1)
    assert isinstance(failure.call_args.args[0], RuntimeError)


def test_partial_hook_registration_still_retires_exact_first_hook(probe):
    hooks, user, _, _ = hooks_fixture()
    user.SetWinEventHook.side_effect = [11, 0]
    # Non-Windows ctypes has no last-error accessor.
    module = importlib.import_module("windows_events")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module.ct, "get_last_error", lambda: 5, raising=False)
        with pytest.raises(probe.NativeBlocked, match="event-hook-failed"):
            hooks.start()
        hooks.close()
    assert user.UnhookWinEvent.call_args.args == (11,)


def test_failed_unhook_does_not_prevent_other_hook_cleanup(probe, monkeypatch):
    hooks, user, _, _ = hooks_fixture()
    module = importlib.import_module("windows_events")
    monkeypatch.setattr(module.ct, "get_last_error", lambda: 5, raising=False)
    hooks.start()
    user.UnhookWinEvent.side_effect = [False, True]
    with pytest.raises(probe.NativeBlocked, match="event-hook-cleanup-failed"):
        hooks.close()
    assert user.UnhookWinEvent.call_count == 2 and hooks.handles == [11]


def test_foreign_thread_cannot_pump_or_unhook(probe):
    hooks, user, _, _ = hooks_fixture()
    failures = []

    def foreign():
        try:
            hooks.drain()
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=foreign)
    thread.start()
    thread.join(timeout=2)
    assert len(failures) == 1 and "hook-thread-mismatch" in str(failures[0])
    user.PeekMessageW.assert_not_called()


def test_native_positive_controls_require_new_visible_effect_consent(probe, tmp_path, monkeypatch):
    module = importlib.import_module("windows_controls")
    api = Mock(side_effect=AssertionError("native API before consent"))
    monkeypatch.setattr(module, "WinAPI", api)
    with pytest.raises(probe.NativeBlocked, match="visible-control-consent-required"):
        module.run_controls(tmp_path, allow_visible_controls=False)
    api.assert_not_called()


def test_positive_control_proof_needs_show_and_both_foreground_events(probe):
    module = importlib.import_module("windows_controls")
    packet = {
        "sealed": True, "hooks_closed": True, "hooks_registered": 2, "samples": 2,
        "maximum_observation_gap_seconds": 0.1, "passed": False,
        "raw_events": [{"kind": "show-event", "hwnd": 20},
                       {"kind": "foreground-event", "hwnd": 20},
                       {"kind": "foreground-event", "hwnd": 10}],
    }
    assert module.sensitivity(packet, 20, 10)
    for field in ("observer_error", "cleanup_error", "callback_error", "overflow"):
        assert not module.sensitivity({**packet, field: "failure"}, 20, 10)
    for index in range(3):
        assert not module.sensitivity(
            {**packet, "raw_events": [event for i, event in enumerate(packet["raw_events"]) if i != index]},
            20, 10,
        )
    assert not module.sensitivity({**packet, "passed": True}, 20, 10)
    assert not module.sensitivity({**packet, "maximum_observation_gap_seconds": 0.501}, 20, 10)


@pytest.mark.parametrize(("thread", "pid", "valid"), [(1, 2, True), (0, 2, False), (1, 0, False)])
def test_native_direct_identity_requires_both_thread_and_written_pid(
    probe, monkeypatch, thread, pid, valid,
):
    def identify(hwnd, pointer):
        assert hwnd == 20
        pointer._obj.value = pid
        return thread

    api = probe.WinAPI.__new__(probe.WinAPI)
    api.user = SimpleNamespace(GetWindowThreadProcessId=identify)
    monkeypatch.setattr(probe.ct, "get_last_error", lambda: 5, raising=False)
    if valid:
        assert api.window_identity(20) == (pid, thread)
    else:
        with pytest.raises(probe.NativeBlocked, match="direct window PID/TID"):
            api.window_identity(20)


def test_pump_overflow_and_quit_remain_blocking(probe):
    hooks, user, _, _ = hooks_fixture()
    user.PeekMessageW.return_value = True
    with pytest.raises(probe.NativeBlocked, match="hook-pump-overflow"):
        hooks.drain()
    assert user.DispatchMessageW.call_count == 4096

    def quit(pointer, *args):
        pointer._obj.message = 0x12
        return True

    user.PeekMessageW.side_effect = quit
    with pytest.raises(probe.NativeBlocked, match="hook-pump-quit"):
        hooks.drain()


def test_original_hook_error_and_separate_cleanup_error_both_survive(probe):
    api = DesktopStub(probe)
    observer = probe.WindowObserver(api)
    api.event_hooks = lambda *args: SimpleNamespace(
        count=0, start=Mock(side_effect=RuntimeError("registration failed")),
        close=Mock(side_effect=RuntimeError("unhook failed")),
    )
    observer._run()
    packet = observer.packet()
    assert packet["observer_error"] == "RuntimeError: registration failed"
    assert packet["cleanup_error"] == "RuntimeError: unhook failed"
    assert packet["passed"] is False
