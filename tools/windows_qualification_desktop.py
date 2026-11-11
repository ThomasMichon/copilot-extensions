"""Compose the existing SDK calibration only on a disposable hosted desktop."""

from __future__ import annotations

import importlib
from pathlib import Path
import re
import sys
import time


def require_hosted_owner(environment, *, system: str, contained: bool) -> None:
    temporary = environment.get("RUNNER_TEMP", "")
    if (
        system != "Windows" or environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not temporary or not Path(temporary).is_absolute()
        or (environment.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1") is not contained
        or environment.get("WINDOWS_QUALIFICATION_DESKTOP") != "1"
    ):
        raise ValueError("desktop capability requires its explicit disposable hosted owner")


def helpers(root: Path):
    directory = (root / "libs/agent-procutil/tests").resolve()
    sys.path.insert(0, str(directory))
    for name in (
        "sdk_visibility", "sdk_desktop_observer", "sdk_desktop_win32",
        "sdk_native_win32", "sdk_visibility_sources", "sdk_visibility_cases",
    ):
        module = importlib.import_module(name)
        if Path(module.__file__).resolve().parent != directory:
            raise ValueError("desktop calibration helper belongs to another source checkout")
    return sys.modules["sdk_visibility"]


def validate_evidence(value: dict, nonce: str, *, helper=None) -> None:
    """Keep calibration, retirement and cleanup independent of pytest's exit."""
    helper = helper or helpers(Path(__file__).resolve().parents[1])
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), dict)
        for key in ("canary", "original_foreground", "control_attempt", "preflight", "calibration")
    ):
        raise ValueError("desktop capability evidence is incomplete")
    control, original = value.get("canary", {}), value.get("original_foreground", {})
    attempt = value.get("control_attempt", {})
    shown = control.get("shown", {})
    facts = value["preflight"]
    if not isinstance(shown, dict):
        raise ValueError("desktop control identity is incomplete")
    if (
        type(value.get("version")) is not int or value["version"] != 1
        or value.get("kind") != "desktop-capability"
        or value.get("nonce") != nonce or value.get("outcome") != "qualified"
        or value.get("visible_consent_consumed") is not True
        or value.get("cleanup_confirmed") is not True or value.get("cleanup_errors") != []
        or control.get("kind") != "canary-result" or control.get("passed") is not True
        or control.get("nonce") != nonce or shown.get("nonce") != nonce
        or attempt.get("cleanup_confirmed") is not True or attempt.get("cleanup_errors") != []
        or type(attempt.get("exit")) is not int or attempt["exit"] != 0
        or attempt.get("reader_error") is not None
        or attempt.get("readers_alive") != [False, False]
        or type(facts.get("session")) is not int or facts["session"] <= 0
        or facts.get("session") != facts.get("console_session")
        or facts.get("station") != "WinSta0" or facts.get("thread_desktop") != "Default"
        or facts.get("input_desktop") != "Default"
        or any(facts.get(key) is not True for key in (
            "station_visible", "foreground_visible", "same_session", "inherited_job_membership",
        ))
        or any(facts.get(key) is not False for key in (
            "elevated", "ssh", "remote_session", "remote_control",
        ))
        or any(type(original.get(key)) is not int or original[key] <= 0
               for key in ("hwnd", "pid", "thread", "birth"))
        or any(type(control.get(key)) is not int or control[key] <= 0
               for key in ("hwnd", "thread"))
        or shown.get("kind") != "shown" or shown.get("hwnd") != control.get("hwnd")
        or shown.get("thread") != control.get("thread")
    ):
        raise ValueError("desktop calibration or exact cleanup is incomplete")
    helper.validate_calibration(
        value["calibration"], control["raw_witnesses"],
        control["hwnd"], control["thread"], original["hwnd"],
    )


def capture(root: Path, output: Path, nonce: str, *, helper=None) -> dict:
    """One visible attempt; no SDK worker, acquisition, session change or retry."""
    if not isinstance(nonce, str) or re.fullmatch(r"[a-f0-9]{32}", nonce) is None:
        raise ValueError("desktop capability requires the qualification request identity")
    helper = helper or helpers(root)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {
        "version": 1, "kind": "desktop-capability", "nonce": nonce,
        "outcome": "incomplete", "visible_consent_consumed": False,
        "started": started, "deadline": started + 45,
        "cleanup_confirmed": False, "cleanup_errors": [],
    }
    api = observer = original_pin = control = None
    try:
        api = helper.new_api()
        report["preflight"] = api.require_desktop()
        hwnd = api.foreground()
        pid, thread = api.window_identity(hwnd)
        original_pin = api.pin(pid)
        original = {"hwnd": hwnd, "pid": pid, "thread": thread, "birth": original_pin.birth}
        report["original_foreground"] = original
        parent = api.identity()
        request = {
            "nonce": nonce, "parent_pid": parent["pid"], "parent_birth": parent["birth"],
            "deadline": report["deadline"], "original": original, "visible_controls": 1,
        }
        observer = helper.DesktopObserver(api, "hosted-desktop-calibration")
        with observer:
            try:
                report["control_attempt"] = {}
                control = helper.Child(
                    api, [sys.executable, "-I", "-B", "-X", "utf8", helper.__file__,
                          "canary", "--allow-visible-control-once"],
                    request, output, "canary", evidence=report["control_attempt"],
                )
                ready = control.admit(min(report["deadline"], time.monotonic() + 5), observer)
                helper.require_time(started + 25, "desktop calibration readiness")
                if observer.packet()["violations"] or api.foreground() != hwnd:
                    raise helper.ObservationBlocked("desktop changed before the single visible attempt")
                report["visible_consent_consumed"] = True
                helper.write_new(output / "visible-attempt.json", {
                    "nonce": nonce, "before_command": time.monotonic(), "maximum_controls": 1,
                })
                deadline = min(report["deadline"], time.monotonic() + 6)
                show_at, show_tick = control.send("show")
                shown = helper.wait_for(control.line, deadline, "visible control identity", observer)
                if (
                    shown.get("kind") != "shown" or shown.get("nonce") != nonce
                    or shown.get("identity") != ready["identity"]
                    or api.window_identity(shown["hwnd"]) != (shown["identity"]["pid"], shown["thread"])
                ):
                    raise helper.ObservationBlocked("visible control identity changed")
                target = shown["hwnd"]
                show = helper.wait_for(
                    lambda: helper.raw_after(observer, "show", target, show_at, show_tick),
                    deadline, "raw control visibility", observer,
                )[0]
                activated = helper.wait_for(
                    lambda: helper.raw_after(observer, "foreground", target, show["received"], show_tick),
                    deadline, "raw control foreground", observer,
                )[0]
                restore_at, restore_tick = control.send("restore")
                restoring = helper.wait_for(control.line, deadline, "restoration response", observer)
                if restoring.get("kind") != "restoring" or restoring.get("nonce") != nonce:
                    raise helper.ObservationBlocked("control restoration response changed")
                restored = helper.wait_for(
                    lambda: helper.raw_after(observer, "foreground", hwnd, restore_at, restore_tick),
                    deadline, "raw original foreground restoration", observer,
                )[0]
                if api.foreground() != hwnd or restored["received"] < activated["received"]:
                    raise helper.ObservationBlocked("original foreground restoration is not corroborated")
                close_at, close_tick = control.send("close")
                result = helper.wait_for(control.line, deadline, "control retirement", observer)
                if (
                    result.get("kind") != "canary-result" or result.get("passed") is not True
                    or result.get("nonce") != nonce
                ):
                    raise helper.ObservationBlocked("control did not retire in its visible budget")
                control.settled(deadline, observer)
                retired = helper.wait_for(
                    lambda: helper.raw_after(observer, "hide", target, close_at, close_tick)
                    or helper.raw_after(observer, "destroy", target, close_at, close_tick),
                    deadline, "raw control retirement", observer,
                )[0]
                if (
                    api.user.IsWindow(target) or original_pin.gone()
                    or api.window_identity(hwnd) != (pid, thread)
                ):
                    raise helper.ObservationBlocked("control or original foreground identity changed")
                report["canary"] = {
                    **result, "shown": shown, "raw_witnesses": [show, activated, restored, retired],
                }
            finally:
                try:
                    if control is not None:
                        retiring, control = control, None
                        retiring.close()
                finally:
                    helper.restore_incomplete_control(api, original, original_pin, report)
            time.sleep(.6)
        helper.validate_calibration(
            observer.packet(), report["canary"]["raw_witnesses"],
            target, shown["thread"], hwnd,
        )
        helper.require_time(started + 40, "desktop calibration completion")
        report["outcome"] = "qualified"
    except Exception as error:
        report["outcome"] = (
            "unsupported" if isinstance(error, helper.ObservationBlocked)
            and not report["visible_consent_consumed"] else "failed"
        )
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if original_pin is not None:
            try:
                original_pin.close()
            except Exception as error:
                report["cleanup_errors"].append(str(error))
        if observer is not None:
            report["calibration"] = observer.packet()
            if (
                observer.thread.is_alive() or not observer.sealed
                or not observer.hooks_closed or observer.cleanup_error
            ):
                report["cleanup_errors"].append("desktop observer cleanup is unconfirmed")
        if report.get("control_attempt", {}).get("cleanup_confirmed") is False:
            report["cleanup_errors"].append("control process or stream cleanup is unconfirmed")
        report["ended"] = time.monotonic()
        if report["ended"] >= report["deadline"]:
            report["cleanup_errors"].append("desktop capability deadline exceeded")
        report["cleanup_confirmed"] = not report["cleanup_errors"]
        if report["cleanup_errors"]:
            report["outcome"] = "failed"
        helper.write_new(output / "desktop-capability.json", report)
    return report
