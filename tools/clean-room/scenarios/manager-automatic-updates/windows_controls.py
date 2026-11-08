"""Explicitly consented, short-lived native window/focus sensitivity controls."""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import json
from pathlib import Path

import fixtures
from identity import failure_details, until
from windows_api import NativeBlocked, WinAPI, WindowObserver, require_desktop


def sensitivity(packet: dict, hwnd: int, original: int) -> bool:
    raw = packet.get("raw_events", [])
    activation = next((
        index for index, event in enumerate(raw)
        if event["kind"] == "foreground-event" and event.get("hwnd") == hwnd
    ), len(raw))
    return bool(
        packet.get("sealed") and packet.get("hooks_closed")
        and packet.get("hooks_registered") == 2 and packet.get("samples", 0) > 0
        and packet.get("maximum_observation_gap_seconds", 1) <= 0.5
        and not any(packet.get(key) for key in
                    ("observer_error", "cleanup_error", "callback_error", "overflow"))
        and any(event["kind"] == "show-event" and event.get("hwnd") == hwnd for event in raw)
        and any(event["kind"] == "foreground-event" and event.get("hwnd") == original
                for event in raw[activation + 1:])
        and packet.get("passed") is False
    )


def restore_focus(api: WinAPI, original: int, report: dict) -> None:
    report["focus_restoration_attempted"] = True
    accepted = bool(api.user.SetForegroundWindow(original))
    report["focus_restoration_api_accepted"] = accepted
    if not accepted:
        raise NativeBlocked(
            "positive-control-restoration-failed",
            "SetForegroundWindow did not restore the original foreground",
        )
    report["final_foreground"] = until(
        api.foreground, lambda value: value == original, timeout=2,
        label="original foreground restoration",
    )
    report["focus_restoration_observed"] = True


def run_controls(workdir: Path, *, allow_visible_controls: bool) -> dict:
    if not allow_visible_controls:
        raise NativeBlocked(
            "visible-control-consent-required",
            "this control briefly shows one owned window and changes/restores foreground focus",
        )
    api = None
    observer = None
    report = {
        "schema": 1, "passed": False, "kind": "native-observer-positive-controls",
        "zero_window_qualification": False, "cleanup_verified": False,
        "original_foreground": None, "final_foreground": None,
        "activation_attempted": False, "activation_api_accepted": None,
        "activation_observed": None,
        "focus_restoration_attempted": False, "focus_restoration_api_accepted": None,
        "focus_restoration_observed": None, "focus_restoration_required": None,
        "window_cleanup_verified": False, "observer_cleanup_verified": False,
        "api_cleanup_verified": False,
    }
    hwnd = 0
    original = 0
    output = None
    primary = None
    try:
        api = WinAPI()
        require_desktop(api.facts())
        output = fixtures.private_directory(workdir)
        signatures = (
            (api.user.CreateWindowExW,
             [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ct.c_int, ct.c_int, ct.c_int,
              ct.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID], wt.HWND),
            (api.user.SetForegroundWindow, [wt.HWND], wt.BOOL),
            (api.user.SetWindowPos,
             [wt.HWND, wt.HWND, ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.UINT], wt.BOOL),
            (api.user.DestroyWindow, [wt.HWND], wt.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result
        original = api.foreground()
        report["original_foreground"] = original
        if not original:
            raise NativeBlocked("foreground-unavailable", "original foreground disappeared before control")
        observer = WindowObserver(api)
        with observer:
            try:
                hwnd = int(api.user.CreateWindowExW(
                    0, "STATIC", "Desktop observation positive control", 0x10CF0000,
                    0, 0, 320, 120, None, None, None, None,
                ) or 0)
                api._check(hwnd, "create owned positive-control window")
                report["control_hwnd"] = hwnd
                # Hidden launch STARTUPINFO can override the creation-time show state.
                api._check(
                    api.user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0057),
                    "show owned positive-control window",
                )
                if not api.user.IsWindowVisible(hwnd):
                    raise NativeBlocked(
                        "positive-control-not-visible",
                        "owned window remained hidden after the explicit show request",
                    )
                report["activation_attempted"] = True
                report["activation_api_accepted"] = bool(api.user.SetForegroundWindow(hwnd))
                if not report["activation_api_accepted"]:
                    raise NativeBlocked(
                        "positive-control-activation-failed",
                        "SetForegroundWindow did not activate the owned control",
                    )
                until(api.foreground, lambda value: value == hwnd, timeout=2,
                      label="positive-control foreground")
                report["activation_observed"] = True
                report["focus_restoration_required"] = True
                restore_focus(api, original, report)
                api._check(api.user.DestroyWindow(hwnd), "destroy owned positive-control window")
                hwnd = 0
            except BaseException as exc:
                primary = exc
                report["failure"] = failure_details(exc)
                raise
            finally:
                failures = []
                if hwnd:
                    try:
                        foreground = api.foreground()
                        report["final_foreground"] = foreground
                        report["focus_restoration_required"] = foreground == hwnd
                        if foreground == hwnd:
                            restore_focus(api, original, report)
                    except BaseException as exc:
                        failures.append(exc)
                    try:
                        api._check(api.user.DestroyWindow(hwnd), "destroy owned positive-control window")
                        hwnd = 0
                    except BaseException as exc:
                        failures.append(exc)
                report["window_cleanup_verified"] = not failures and not hwnd
                if failures:
                    report["window_cleanup_failures"] = [failure_details(exc) for exc in failures]
                    if primary is None:
                        raise failures[0]
        packet = observer.packet()
        report["observation"] = packet
        report["passed"] = sensitivity(packet, report["control_hwnd"], original)
        return report
    except BaseException as exc:
        primary = primary or exc
        report["passed"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        report.setdefault("failure", failure_details(exc))
        raise
    finally:
        failures = []
        if observer is not None:
            try:
                report["observation"] = observer.packet()
                report["observer_cleanup_verified"] = bool(
                    report["observation"].get("sealed")
                    and report["observation"].get("hooks_closed")
                    and not report["observation"].get("cleanup_error")
                    and not observer.thread.is_alive()
                )
            except BaseException as exc:
                failures.append(exc)
        else:
            report["observer_cleanup_verified"] = True
        if api is not None:
            if observer is None or not observer.thread.is_alive():
                try:
                    api.close()
                    report["api_cleanup_verified"] = True
                except BaseException as exc:
                    failures.append(exc)
            else:
                report["api_cleanup_status"] = "unconfirmed-observer-alive"
        report["cleanup_verified"] = all(report[key] for key in (
            "window_cleanup_verified", "observer_cleanup_verified", "api_cleanup_verified",
        ))
        if failures or not report["cleanup_verified"]:
            report["passed"] = False
        if failures:
            report["finalization_failures"] = [failure_details(exc) for exc in failures]
        if output is not None:
            try:
                (output / "observer-controls.json").write_text(
                    json.dumps(report, sort_keys=True, indent=2), encoding="utf-8",
                )
            except BaseException as exc:
                report.update(passed=False, report_failure=failure_details(exc))
                failures.append(exc)
        if failures and primary is None:
            raise failures[0]
