"""Explicitly consented, finite SDK visibility qualification; never a daemon."""

from __future__ import annotations

import argparse
import base64
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import traceback
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdk_desktop_observer import DesktopObserver, ObservationBlocked, validate_calibration
from sdk_visibility_cases import native_arguments, validate_case_packet, validate_case_packets, verify_native_junit
from sdk_visibility_sources import bind_sdk_import, require_sdk_sources

if TYPE_CHECKING:
    from sdk_desktop_win32 import WinAPI


def new_api():
    from sdk_desktop_win32 import WinAPI

    return WinAPI()


def validate_request(mode: str, value: object) -> dict:
    fields = {
        "probe": {"output", "python", "sdk_root", "sources", "visible_controls", "case_packets"},
        "canary": {"parent_pid", "parent_birth", "original", "visible_controls"},
        "worker": {"parent_pid", "parent_birth", "sdk_root", "output", "sources", "nodes", "packet"},
        "identity": {"parent_pid", "parent_birth"},
        "passive": {"python", "output"},
    }
    if mode not in fields or not isinstance(value, dict) or set(value) != {
        "nonce", "deadline", *fields[mode],
    }:
        raise ObservationBlocked("qualification request fields differ from the selected mode")
    if not isinstance(value["nonce"], str) or re.fullmatch(r"[a-f0-9]{32}", value["nonce"]) is None:
        raise ObservationBlocked("qualification requires a fresh correlated request")
    deadline = value["deadline"]
    if (type(deadline) not in (int, float) or not math.isfinite(deadline)
            or not 0 < deadline - time.monotonic() <= 180):
        raise ObservationBlocked("qualification requires an existing deadline within 180 seconds")
    for key in ("output", "sdk_root"):
        if key in value and (
            not isinstance(value[key], str) or not Path(value[key]).is_absolute()
        ):
            raise ObservationBlocked(f"{key} must name an absolute fixture path")
    if "python" in value and (
        not isinstance(value["python"], str) or not value["python"] or "\0" in value["python"]
    ):
        raise ObservationBlocked("qualification needs its existing test interpreter")
    if "visible_controls" in value and type(value["visible_controls"]) is not int:
        raise ObservationBlocked("visible control count must be an integer")
    for key, maximum in (("parent_pid", 1 << 32), ("parent_birth", 1 << 64)):
        if key in value and (type(value[key]) is not int or not 0 < value[key] < maximum):
            raise ObservationBlocked(f"{key} has no exact native identity")
    if "original" in value:
        original = value["original"]
        if not isinstance(original, dict) or set(original) != {"hwnd", "pid", "thread", "birth"}:
            raise ObservationBlocked("original foreground identity is incomplete")
        if any(type(number) is not int or number <= 0 for number in original.values()):
            raise ObservationBlocked("original foreground identity is not exact")
        if (original["hwnd"] >= 1 << 64 or original["birth"] >= 1 << 64
                or original["pid"] >= 1 << 32 or original["thread"] >= 1 << 32):
            raise ObservationBlocked("original foreground identity exceeds native widths")
    if "sources" in value:
        files = value["sources"]
        if not isinstance(files, list) or not files:
            raise ObservationBlocked("qualification source manifest is empty")
        paths = []
        for item in files:
            if (
                not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}
                or not isinstance(item["path"], str) or not Path(item["path"]).is_absolute()
                or type(item["bytes"]) is not int or item["bytes"] < 0
                or not isinstance(item["sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) is None
            ):
                raise ObservationBlocked("qualification source manifest entry is invalid")
            paths.append(os.path.normcase(item["path"]))
        if len(set(paths)) != len(paths):
            raise ObservationBlocked("qualification source closure is duplicated or incomplete")
        require_sdk_sources(Path(value["sdk_root"]), files, runner=Path(__file__))
    if mode == "probe":
        validate_case_packets(Path(value["sdk_root"]), value["case_packets"])
    elif mode == "worker":
        validate_case_packet(Path(value["sdk_root"]), value["nodes"])
        native_arguments(
            Path(value["sdk_root"]), Path(value["output"]), value["nodes"], value["packet"],
        )
    return value


def emit(value: dict) -> None:
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=True) + "\n").encode())
    sys.stdout.buffer.flush()


def write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def require_time(deadline: float, stage: str) -> None:
    if time.monotonic() >= deadline:
        raise ObservationBlocked(f"immutable deadline expired during {stage}")


def wait_for(predicate, deadline: float, stage: str, observer=None):
    while True:
        require_time(deadline, stage)
        if observer is not None and (
            not observer.healthy() or observer.ended is not None or not observer.thread.is_alive()
        ):
            raise ObservationBlocked(f"desktop observation became unavailable during {stage}")
        value = predicate()
        if value:
            return value
        time.sleep(0.01)


class Child:
    def __init__(
        self, api, argv: list[str], request: dict, directory: Path, name: str,
        evidence: dict | None = None,
    ):
        self.api, self.request, self.pins = api, request, []
        self.closed = False
        self.evidence = evidence if evidence is not None else {}
        self.evidence.update({
            "name": name, "launched": False, "exit": None,
            "stdout": None, "stderr": None, "stdout_b64": None, "stderr_b64": None,
            "reader_error": None, "cleanup_confirmed": False, "cleanup_errors": [],
        })
        self.lines: deque[bytes] = deque()
        self.output = bytearray()
        self.error = bytearray()
        self.reader_error: str | None = None
        self.lock = threading.Lock()
        self.readers = []
        request_file = directory / f"{name}-request.json"
        write_new(request_file, request)
        self.process = subprocess.Popen(
            [*argv, str(request_file)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.evidence["launched"] = True
        try:
            self.readers = [
                threading.Thread(target=self._read, args=(self.process.stdout, self.output, True),
                                 daemon=True, name="sdk-ui-stdout"),
                threading.Thread(target=self._read, args=(self.process.stderr, self.error, False),
                                 daemon=True, name="sdk-ui-stderr"),
            ]
            for reader in self.readers:
                reader.start()
            launch_birth = api.birth(int(self.process._handle))
            self.pins.append(api.pin(self.process.pid, launch_birth, owned=True))
        except BaseException as error:
            self.evidence["error"] = f"{type(error).__name__}: {error}"
            try:
                self.close()
            except BaseException as cleanup:
                self.evidence["cleanup_error"] = f"{type(cleanup).__name__}: {cleanup}"
            raise

    def _read(self, stream, output: bytearray, lines: bool) -> None:
        pending = bytearray()
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                with self.lock:
                    if len(output) + len(chunk) > 1024 * 1024:
                        raise ObservationBlocked("owned fixture stream exceeded its explicit budget")
                    output.extend(chunk)
                    if lines:
                        pending.extend(chunk)
                        while b"\n" in pending:
                            line, _, rest = pending.partition(b"\n")
                            self.lines.append(bytes(line))
                            pending = bytearray(rest)
        except Exception as error:
            self.reader_error = f"{type(error).__name__}: {error}"

    def line(self) -> dict | None:
        if self.reader_error:
            raise ObservationBlocked(self.reader_error)
        with self.lock:
            if self.lines:
                return json.loads(self.lines.popleft())
        exit_code = self.process.poll()
        if exit_code is not None and not any(reader.is_alive() for reader in self.readers):
            raise ObservationBlocked(
                f"owned child exited before its expected response (exit {exit_code}); "
                f"stderr: {bytes(self.error).decode('utf-8', errors='backslashreplace')}"
            )
        return None

    def admit(self, deadline: float, observer=None) -> dict:
        ready = wait_for(self.line, deadline, "owned child identity", observer)
        if ready.get("kind") != "ready" or ready.get("nonce") != self.request["nonce"]:
            raise ObservationBlocked(f"uncorrelated child identity: {ready}")
        identity = ready["identity"]
        if (type(identity.get("pid")) is not int or identity["pid"] == os.getpid()
                or not 0 < identity["pid"] <= 0xFFFFFFFF
                or type(identity.get("birth")) is not int or not 0 < identity["birth"] < 1 << 64):
            raise ObservationBlocked("owned child returned an invalid process identity")
        for pin in self.pins:
            if pin.pid == identity["pid"] and pin.birth != identity["birth"]:
                raise ObservationBlocked("owned launcher birth differs from its readiness receipt")
        if identity["pid"] not in {pin.pid for pin in self.pins}:
            self.pins.append(self.api.pin(identity["pid"], identity["birth"], owned=True))
        if any(pin.gone() for pin in self.pins):
            raise ObservationBlocked("owned fixture exited before admission")
        return ready

    def send(self, action: str) -> tuple[float, int]:
        received = time.monotonic()
        tick = int(self.api.kernel.GetTickCount64()) & 0xFFFFFFFF
        value = {"nonce": self.request["nonce"], "action": action, "after_tick": tick}
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        return received, tick

    def settled(self, deadline: float, observer=None) -> None:
        wait_for(lambda: all(pin.gone() for pin in self.pins), deadline,
                 "owned child retirement", observer)
        self.process.wait(timeout=max(0.01, deadline - time.monotonic()))
        for reader in self.readers:
            reader.join(max(0, deadline - time.monotonic()))
        if any(reader.is_alive() for reader in self.readers) or self.reader_error:
            raise ObservationBlocked(self.reader_error or "owned stream readers did not settle")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failures = []
        if not self.pins:
            try:
                handle = int(self.process._handle)
                if self.api.kernel.WaitForSingleObject(handle, 0) != 0:
                    self.api.check(self.api.kernel.TerminateProcess(handle, 1),
                                   "retire the unadmitted owned launcher")
                if self.api.kernel.WaitForSingleObject(handle, 1000) != 0:
                    raise ObservationBlocked("unadmitted owned launcher cleanup is unconfirmed")
            except Exception as error:
                failures.append(str(error))
        for pin in reversed(self.pins):
            try:
                if not pin.gone():
                    pin.terminate_owned()
            except Exception as error:
                failures.append(str(error))
        for pin in reversed(self.pins):
            try:
                if self.api.kernel.WaitForSingleObject(pin.handle, 1000) != 0:
                    raise ObservationBlocked("owned fixture cleanup is unconfirmed")
            except Exception as error:
                failures.append(str(error))
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except Exception as error:
                failures.append(str(error))
        for pin in self.pins:
            try:
                pin.close()
            except Exception as error:
                failures.append(str(error))
        for reader in self.readers:
            if reader.ident is not None:
                reader.join(1)
                if reader.is_alive():
                    failures.append("owned stream reader did not retire")
        try:
            self.process.wait(timeout=1)
            self.process._handle.Close()
        except Exception as error:
            failures.append(str(error))
        with self.lock:
            output, error = bytes(self.output), bytes(self.error)
        self.evidence.update({
            "exit": self.process.returncode,
            "stdout": output.decode("utf-8", errors="backslashreplace"),
            "stderr": error.decode("utf-8", errors="backslashreplace"),
            "stdout_b64": base64.b64encode(output).decode("ascii"),
            "stderr_b64": base64.b64encode(error).decode("ascii"),
            "reader_error": self.reader_error,
            "readers_alive": [reader.is_alive() for reader in self.readers],
            "cleanup_confirmed": not failures, "cleanup_errors": failures,
        })
        if failures:
            raise ObservationBlocked("owned fixture cleanup: " + "; ".join(failures))


def command(api: WinAPI, nonce: str, deadline: float) -> str:
    pending = bytearray()
    while True:
        require_time(deadline, "private command")
        api.pump()
        size = api.pipe_bytes()
        if size is None:
            raise ObservationBlocked("original caller closed the private command pipe")
        if size:
            pending.extend(os.read(0, min(size, 4096)))
            if len(pending) > 4096:
                raise ObservationBlocked("private command exceeded its frame limit")
            if b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                if rest:
                    raise ObservationBlocked("unexpected pipelined private commands")
                value = json.loads(line)
                if (set(value) != {"nonce", "action", "after_tick"} or value["nonce"] != nonce
                        or type(value["after_tick"]) is not int
                        or not 0 <= value["after_tick"] <= 0xFFFFFFFF):
                    raise ObservationBlocked("private command correlation changed")
                wait_generation_boundary(api, value["after_tick"], deadline)
                return value["action"]
        time.sleep(0.01)


def wait_generation_boundary(api: WinAPI, tick: int, deadline: float) -> None:
    while True:
        require_time(deadline, "private command generation boundary")
        delta = (int(api.kernel.GetTickCount64()) - tick) & 0xFFFFFFFF
        if 0 < delta <= 10000:
            return
        if delta > 10000:
            raise ObservationBlocked("private command generation boundary is stale")
        api.pump()
        time.sleep(0.001)


def canary(request: dict, allow_visible: bool) -> int:
    if not allow_visible or request.get("visible_controls") != 1:
        raise ObservationBlocked("single visible control consent is required")
    api = new_api()
    api.require_desktop()
    parent = api.require_parent_pipe(request)
    original = request["original"]
    original_pin = api.pin(original["pid"], original["birth"])
    hwnd = 0
    result = {"kind": "canary-result", "nonce": request["nonce"], "passed": False}
    try:
        emit({"kind": "ready", "nonce": request["nonce"], "identity": api.identity()})
        if command(api, request["nonce"], request["deadline"]) != "show":
            raise ObservationBlocked("visible control was not explicitly admitted")
        visible_deadline = min(request["deadline"], time.monotonic() + 8)
        if parent.gone() or original_pin.gone() or (
            api.window_identity(original["hwnd"]) != (original["pid"], original["thread"])
        ):
            raise ObservationBlocked("original foreground identity is no longer live")
        result["potentially_visible_attempted"] = True
        result["before_visible"] = time.monotonic()
        hwnd = int(api.user.CreateWindowExW(
            0, "STATIC", "SDK visibility calibration", 0x10CF0000,
            20, 20, 320, 100, None, None, None, None,
        ) or 0)
        api.check(hwnd, "create the single owned calibration window")
        result["hwnd"] = hwnd
        result["thread"] = api.window_identity(hwnd)[1]
        api.check(api.user.IsWindowVisible(hwnd), "calibration window visibility")
        api.check(api.user.SetForegroundWindow(hwnd), "calibration foreground activation")
        emit({"kind": "shown", "nonce": request["nonce"], "hwnd": hwnd,
              "thread": result["thread"], "identity": api.identity()})
        if command(api, request["nonce"], visible_deadline) != "restore":
            raise ObservationBlocked("calibration restoration was not admitted")
        if original_pin.gone() or api.window_identity(original["hwnd"]) != (
            original["pid"], original["thread"],
        ):
            raise ObservationBlocked("original foreground changed before restoration")
        api.check(api.user.SetForegroundWindow(original["hwnd"]), "restore original foreground")
        emit({"kind": "restoring", "nonce": request["nonce"]})
        if command(api, request["nonce"], visible_deadline) != "close":
            raise ObservationBlocked("calibration retirement was not admitted")
        api.check(api.user.DestroyWindow(hwnd), "destroy the owned calibration window")
        hwnd = 0
        result["after_destroy"] = time.monotonic()
        result["passed"] = result["after_destroy"] - result["before_visible"] <= 10
        emit(result)
        return 0 if result["passed"] else 1
    finally:
        try:
            if hwnd:
                try:
                    if api.foreground() == hwnd and not original_pin.gone():
                        if api.window_identity(original["hwnd"]) != (original["pid"], original["thread"]):
                            raise ObservationBlocked("original window identity changed during cleanup")
                        api.check(api.user.SetForegroundWindow(original["hwnd"]),
                                  "restore foreground during control cleanup")
                finally:
                    api.check(api.user.DestroyWindow(hwnd), "cleanup owned calibration window")
        finally:
            try:
                original_pin.close()
            finally:
                parent.close()


def worker(request: dict) -> int:
    require_sdk_sources(Path(request["sdk_root"]), request["sources"], runner=Path(__file__))
    verify_sources(request["sources"])
    validate_case_packet(Path(request["sdk_root"]), request["nodes"])
    arguments = native_arguments(
        Path(request["sdk_root"]), Path(request["output"]), request["nodes"], request["packet"],
    )
    bind_sdk_import(Path(request["sdk_root"]))
    api = new_api()
    api.require_desktop()
    parent = api.require_parent_pipe(request)
    try:
        emit({"kind": "ready", "nonce": request["nonce"], "identity": api.identity()})
        if command(api, request["nonce"], request["deadline"]) != "run" or parent.gone():
            raise ObservationBlocked("SDK test worker was not admitted by its original caller")
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        import pytest

        result = int(pytest.main(arguments))
        verify_sources(request["sources"])
        return result
    finally:
        parent.close()


def verify_sources(files: list[dict]) -> list[dict]:
    result = []
    for item in files:
        path = Path(item["path"])
        if (not path.is_absolute() or path.resolve() != path
                or path.is_symlink() or not path.is_file()):
            raise ObservationBlocked("qualification source is missing or redirected")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != item["bytes"] or digest != item["sha256"]:
            raise ObservationBlocked(f"qualification source changed: {path.name}")
        result.append({"path": str(path), "bytes": len(data), "sha256": digest})
    return result


def raw_after(observer, kind: str, hwnd: int, received: float, tick: int) -> list[dict]:
    return [
        event for event in observer.raw(kind, hwnd)
        if event["received"] >= received
        and 0 < ((event["generated_ms"] - tick) & 0xFFFFFFFF) <= 10000
    ]


def restore_incomplete_control(api: WinAPI, original: dict, pin, report: dict) -> None:
    if not report["visible_consent_consumed"] or report.get("canary", {}).get("passed"):
        return
    if pin.gone() or api.window_identity(original["hwnd"]) != (original["pid"], original["thread"]):
        raise ObservationBlocked("original window identity changed during control cleanup")
    if api.foreground() != original["hwnd"]:
        api.check(api.user.SetForegroundWindow(original["hwnd"]),
                  "restore original foreground during observed control cleanup")


def probe(request: dict, allow_visible: bool) -> int:
    if not allow_visible or request.get("visible_controls") != 1:
        raise ObservationBlocked("single visible control consent is required")
    require_sdk_sources(Path(request["sdk_root"]), request["sources"], runner=Path(__file__))
    source_before = verify_sources(request["sources"])
    packets = validate_case_packets(Path(request["sdk_root"]), request["case_packets"])
    start = time.monotonic()
    deadline = min(request["deadline"], start + 165)
    root = Path(request["output"])
    root.mkdir()
    report = {"kind": "sdk-visibility", "passed": False, "started": start,
              "visible_consent_consumed": False, "deadline": deadline,
              "source_before": source_before}
    api = new_api()
    original_pin = None
    control = tests = None
    calibration = quiet = None
    try:
        report["preflight"] = api.require_desktop()
        original_hwnd = api.foreground()
        original_pid, original_thread = api.window_identity(original_hwnd)
        original_pin = api.pin(original_pid)
        original = {"hwnd": original_hwnd, "pid": original_pid, "thread": original_thread,
                    "birth": original_pin.birth}
        report["original_foreground"] = original
        executable = request["python"]
        parent = api.identity()
        child_request = {
            "nonce": request["nonce"], "parent_pid": parent["pid"],
            "parent_birth": parent["birth"], "deadline": deadline,
        }
        argv = [executable, "-I", "-B", "-X", "utf8", __file__]
        calibration = DesktopObserver(api, "calibration")
        with calibration:
            try:
                report["control_attempt"] = {}
                control = Child(
                    api, [*argv, "canary", "--allow-visible-control-once"],
                    {**child_request, "original": original, "visible_controls": 1},
                    root, "canary", evidence=report["control_attempt"],
                )
                ready = control.admit(min(deadline, time.monotonic() + 5), calibration)
                require_time(min(deadline, start + 25), "calibration readiness")
                if calibration.packet()["violations"] or api.foreground() != original_hwnd:
                    raise ObservationBlocked("desktop changed before the single visible attempt")
                report["visible_consent_consumed"] = True
                write_new(root / "visible-attempt.json", {
                    "nonce": request["nonce"], "before_command": time.monotonic(),
                    "maximum_controls": 1,
                })
                control_deadline = min(deadline, time.monotonic() + 6)
                show_at, show_tick = control.send("show")
                shown = wait_for(control.line, control_deadline, "visible control identity", calibration)
                if (shown.get("kind") != "shown" or shown.get("nonce") != request["nonce"]
                        or shown.get("identity") != ready["identity"]):
                    raise ObservationBlocked(f"visible control identity changed: {shown}")
                hwnd, thread = shown["hwnd"], shown["thread"]
                if api.window_identity(hwnd) != (shown["identity"]["pid"], thread):
                    raise ObservationBlocked("calibration window owner changed")
                show = wait_for(lambda: raw_after(calibration, "show", hwnd, show_at, show_tick),
                                control_deadline, "raw calibration show", calibration)[0]
                activated = wait_for(
                    lambda: raw_after(calibration, "foreground", hwnd, show["received"], show_tick),
                    control_deadline, "raw owned foreground activation", calibration,
                )[0]
                restore_at, restore_tick = control.send("restore")
                restoring = wait_for(control.line, control_deadline, "restoration response", calibration)
                if restoring.get("kind") != "restoring" or restoring.get("nonce") != request["nonce"]:
                    raise ObservationBlocked(f"control restoration failed: {restoring}")
                restored = wait_for(
                    lambda: raw_after(calibration, "foreground", original_hwnd,
                                      restore_at, restore_tick),
                    control_deadline, "RAW original foreground restoration", calibration,
                )[0]
                if api.foreground() != original_hwnd or restored["received"] < activated["received"]:
                    raise ObservationBlocked("raw foreground restoration is not corroborated")
                close_at, close_tick = control.send("close")
                result = wait_for(control.line, control_deadline, "control retirement", calibration)
                if (result.get("kind") != "canary-result" or not result.get("passed")
                        or result.get("nonce") != request["nonce"]):
                    raise ObservationBlocked(f"control did not retire in its visible budget: {result}")
                control.settled(control_deadline, calibration)
                retired = wait_for(
                    lambda: raw_after(calibration, "hide", hwnd, close_at, close_tick)
                    or raw_after(calibration, "destroy", hwnd, close_at, close_tick),
                    control_deadline, "raw control window retirement", calibration,
                )[0]
                if api.user.IsWindow(hwnd) or original_pin.gone() or (
                    api.window_identity(original_hwnd) != (original_pid, original_thread)
                ):
                    raise ObservationBlocked("control or original foreground identity changed")
                report["canary"] = {
                    **result, "shown": shown,
                    "raw_witnesses": [show, activated, restored, retired],
                }
            finally:
                try:
                    if control is not None:
                        retiring, control = control, None
                        retiring.close()
                finally:
                    restore_incomplete_control(api, original, original_pin, report)
            time.sleep(0.6)
        packet = calibration.packet()
        report["calibration"] = packet
        validate_calibration(packet, report["canary"]["raw_witnesses"],
                             hwnd, thread, original_hwnd)
        require_time(min(deadline, start + 40), "calibration phase completion")
        quiet = DesktopObserver(api, "quiet-sdk")
        (root / "pytest.ini").open("x").close()
        report["pytest_packets"] = [
            native_arguments(Path(request["sdk_root"]), root, nodes, index)
            for index, nodes in enumerate(packets)
        ]
        report["workers"] = []
        with quiet:
            for index, nodes in enumerate(packets):
                attempt = {"packet": index, "stage": "launch", "succeeded": False}
                report["workers"].append(attempt)
                primary_error = None
                try:
                    tests = Child(
                        api, [*argv, "worker"],
                        {**child_request, "sdk_root": request["sdk_root"], "output": str(root),
                         "sources": request["sources"], "nodes": nodes, "packet": index},
                        root, f"worker-{index + 1:02}", evidence=attempt,
                    )
                    attempt["stage"] = "admit"
                    tests.admit(min(deadline, time.monotonic() + 5), quiet)
                    attempt["stage"] = "send"
                    tests.send("run")
                    attempt["stage"] = "settle"
                    tests.settled(min(deadline, start + 150), quiet)
                    attempt["stage"] = "validate-exit"
                    if tests.process.returncode != 0:
                        raise ObservationBlocked("SDK native worker failed; preserve exact JUnit and streams")
                    attempt.update(stage="completed", succeeded=True)
                except BaseException as error:
                    primary_error = error
                    attempt["error"] = f"{type(error).__name__}: {error}"
                    raise
                finally:
                    if tests is not None:
                        retiring, tests = tests, None
                        try:
                            retiring.close()
                        except BaseException as cleanup:
                            attempt["cleanup_error"] = f"{type(cleanup).__name__}: {cleanup}"
                            attempt["succeeded"] = False
                            if primary_error is None:
                                raise
            time.sleep(0.6)
        report["native_junit"] = verify_native_junit(root, Path(request["sdk_root"]), packets)
        report["quiet"] = quiet.packet()
        report["source_after"] = verify_sources(request["sources"])
        report["passed"] = bool(report["quiet"]["passed"])
        return 0 if report["passed"] else 1
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        failures = []
        if original_pin is not None:
            try:
                original_pin.close()
            except Exception as error:
                failures.append(str(error))
        for name, observer in (("calibration", calibration), ("quiet", quiet)):
            if observer is not None:
                report[name] = observer.packet()
        report["cleanup_errors"] = failures
        report["ended"] = time.monotonic()
        if report["ended"] > deadline:
            failures.append("immutable visibility deadline exceeded")
        if failures:
            report["passed"] = False
        write_new(root / "visibility-result.json", report)
        if failures:
            raise ObservationBlocked("visibility fixture cleanup is unconfirmed: " + "; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("probe", "canary", "worker", "passive", "identity"))
    parser.add_argument("--allow-visible-control-once", action="store_true")
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    request = validate_request(args.mode, json.loads(args.request.read_text("utf-8")))
    if args.mode == "probe":
        return probe(request, args.allow_visible_control_once)
    if args.mode == "canary":
        return canary(request, args.allow_visible_control_once)
    if args.mode == "worker":
        return worker(request)
    if args.mode == "identity":
        api = new_api()
        api.require_desktop()
        parent = api.require_parent_pipe(request)
        try:
            emit({"kind": "ready", "nonce": request["nonce"], "identity": api.identity()})
            if command(api, request["nonce"], request["deadline"]) != "retire":
                raise ObservationBlocked("passive identity child received an unexpected command")
            return 0
        finally:
            parent.close()
    api = new_api()
    facts = api.require_desktop()
    with DesktopObserver(api, "passive-binding") as observer:
        child = None
        try:
            if request.get("python") and request.get("output"):
                directory = Path(request["output"])
                directory.mkdir()
                parent = api.identity()
                child = Child(
                    api, [request["python"], "-I", "-B", "-X", "utf8", __file__, "identity"],
                    {"nonce": request["nonce"], "parent_pid": parent["pid"],
                     "parent_birth": parent["birth"], "deadline": request["deadline"]},
                    directory, "identity",
                )
                child.admit(min(request["deadline"], time.monotonic() + 5), observer)
                child.send("retire")
                child.settled(request["deadline"], observer)
                if child.process.returncode != 0:
                    raise ObservationBlocked("passive identity child failed")
        finally:
            if child is not None:
                child.close()
        require_time(request["deadline"], "passive binding tail")
        time.sleep(min(0.6, max(0, request["deadline"] - time.monotonic())))
        require_time(request["deadline"], "passive binding completion")
    emit({"kind": "passive-binding", "facts": facts, "observation": observer.packet(),
          "visible_controls_attempted": 0})
    return 0 if observer.packet()["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
