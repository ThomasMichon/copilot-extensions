"""Explicitly consented, short-lived native window/focus sensitivity controls."""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import json
from pathlib import Path

import fixtures
from identity import until
from windows_api import NativeBlocked, WinAPI, WindowObserver, require_desktop


def sensitivity(packet: dict, hwnd: int, original: int) -> bool:
    raw = packet.get("raw_events", [])
    return bool(
        packet.get("sealed") and packet.get("hooks_closed")
        and packet.get("hooks_registered") == 2 and packet.get("samples", 0) > 0
        and packet.get("maximum_observation_gap_seconds", 1) <= 0.5
        and not any(packet.get(key) for key in
                    ("observer_error", "cleanup_error", "callback_error", "overflow"))
        and any(event["kind"] == "show-event" and event.get("hwnd") == hwnd for event in raw)
        and any(event["kind"] == "foreground-event" and event.get("hwnd") == hwnd for event in raw)
        and any(event["kind"] == "foreground-event" and event.get("hwnd") == original for event in raw)
        and packet.get("passed") is False
    )


def run_controls(workdir: Path, *, allow_visible_controls: bool) -> dict:
    if not allow_visible_controls:
        raise NativeBlocked(
            "visible-control-consent-required",
            "this control briefly shows one owned window and changes/restores foreground focus",
        )
    api = WinAPI()
    observer = None
    report = {
        "schema": 1, "passed": False, "kind": "native-observer-positive-controls",
        "zero_window_qualification": False, "cleanup_verified": False,
    }
    hwnd = 0
    original = 0
    output = None
    try:
        require_desktop(api.facts())
        output = fixtures.private_directory(workdir)
        signatures = (
            (api.user.CreateWindowExW,
             [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ct.c_int, ct.c_int, ct.c_int,
              ct.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID], wt.HWND),
            (api.user.SetForegroundWindow, [wt.HWND], wt.BOOL),
            (api.user.DestroyWindow, [wt.HWND], wt.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result
        original = api.foreground()
        observer = WindowObserver(api)
        with observer:
            try:
                hwnd = int(api.user.CreateWindowExW(
                    0, "STATIC", "Desktop observation positive control", 0x10CF0000,
                    0, 0, 320, 120, None, None, None, None,
                ) or 0)
                api._check(hwnd, "create owned positive-control window")
                report["control_hwnd"] = hwnd
                api._check(api.user.IsWindowVisible(hwnd), "positive-control window visible")
                api._check(api.user.SetForegroundWindow(hwnd), "activate positive-control window")
                until(api.foreground, lambda value: value == hwnd, timeout=2,
                      label="positive-control foreground")
                api._check(api.user.SetForegroundWindow(original), "restore original foreground")
                until(api.foreground, lambda value: value == original, timeout=2,
                      label="original foreground restoration")
                api._check(api.user.DestroyWindow(hwnd), "destroy owned positive-control window")
                hwnd = 0
                report["cleanup_verified"] = True
            finally:
                if hwnd:
                    failures = []
                    if api.foreground() == hwnd and not api.user.SetForegroundWindow(original):
                        failures.append("original foreground restoration failed")
                    if not api.user.DestroyWindow(hwnd):
                        failures.append("owned window destruction failed")
                    report["cleanup_verified"] = not failures
                    if failures:
                        report["cleanup_error"] = "; ".join(failures)
        packet = observer.packet()
        report["observation"] = packet
        report["passed"] = sensitivity(packet, report["control_hwnd"], original) and report["cleanup_verified"]
        return report
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if observer is not None:
            report["observation"] = observer.packet()
        if observer is None or not observer.thread.is_alive():
            api.close()
        if output is not None:
            (output / "observer-controls.json").write_text(
                json.dumps(report, sort_keys=True, indent=2), encoding="utf-8",
            )
