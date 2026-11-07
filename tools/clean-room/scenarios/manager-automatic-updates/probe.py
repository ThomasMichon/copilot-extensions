#!/usr/bin/env python3
"""Portable, credential-free process qualification; never a live install command."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Isolated Python omits the script directory; these are prepared-scenario modules.
import fixtures  # noqa: E402
import lifecycle  # noqa: E402
import native  # noqa: E402
from identity import failure_details  # noqa: E402


CASES = ("all", "handover", "failed-successor", "signal-drain")


def run_selected(
    report: dict, key: str, root: Path, function: Callable[..., dict],
    source: Path, manager: dict, **kwargs: object,
) -> dict:
    report["active_case"] = key
    states = report.setdefault("case_results", {})
    states[key] = {"status": "running"}
    try:
        result = function(source, root, manager, **kwargs)
        states[key] = {"status": "returned"}
        report[key] = result
        if result.get("passed") is not True:
            raise RuntimeError("selected lifecycle case did not pass")
        return result
    except BaseException:
        try:
            deadline = os.environ.get("APERTURE_TEST_DEADLINE")
            absolute = float(deadline) if deadline is not None else None
            if absolute is not None:
                if not math.isfinite(absolute):
                    raise ValueError("case report requires a finite inherited work deadline")
                if time.time() >= absolute:
                    raise TimeoutError("case report read exceeds inherited work deadline")
            with (root / "probe/result.json").open("rb") as stream:
                content = stream.read((1 << 20) + 1)
            if absolute is not None and time.time() >= absolute:
                raise TimeoutError("case report read exceeded inherited work deadline")
            if len(content) > 1 << 20:
                raise ValueError("case report exceeds its bounded diagnostic capture")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("case report must be an object")
            report[key] = result
            states[key] = {"status": "retained-failure-report"}
        except BaseException as exc:
            states[key] = {
                "status": "deadline" if isinstance(exc, TimeoutError) else
                "missing" if isinstance(exc, FileNotFoundError) else "unreadable",
                "failure": failure_details(exc),
            }
        raise


def qualify(
    source: Path, workdir: Path, *, cycles: int, native_startup: bool,
    case: str = "all",
) -> dict:
    if case not in CASES:
        raise ValueError("select a declared qualification case")
    if cycles not in (1, 2):
        raise ValueError("qualification supports one fast cycle or two real-cadence cycles")
    if case not in ("all", "handover") and cycles != 1:
        raise ValueError("only the handover case observes periodic cycles")
    if case == "signal-drain" and (
        not native_startup or not sys.platform.startswith("linux")
    ):
        raise native.UnsupportedNative("signal-drain requires native Linux/WSL startup")
    windows_native = native_startup and sys.platform == "win32"
    if windows_native and case in ("all", "handover") and cycles != 2:
        raise native.UnsupportedNative("Windows native qualification requires --cycles 2 at real cadence")
    if native_startup:
        native.preflight()
    fixtures.verify_prepared(fixtures.payload_path(source))
    workdir = fixtures.private_directory(workdir)
    manager = fixtures.load_manager(source)
    from agent_procutil.daemon_instances import daemon_test_lifecycle, default_registry_root

    report = None
    try:
        with daemon_test_lifecycle(
            owner="manager-automatic-updates-qualification", registry_root=default_registry_root(),
        ) as instance_environment:
            report = _qualify_cases(
                source, workdir, manager, cycles=cycles, native_startup=native_startup,
                instance_environment=instance_environment, case=case,
            )
        return report
    except BaseException as exc:
        if report is not None:
            report.update(passed=False, lifecycle_finalization_failure=failure_details(exc))
            try:
                deadline = os.environ.get("APERTURE_TEST_DEADLINE")
                if deadline is not None:
                    absolute = float(deadline)
                    if not math.isfinite(absolute) or time.time() >= absolute:
                        raise TimeoutError("qualification report has no remaining work budget")
                (workdir / "qualification.json").write_text(
                    json.dumps(report, sort_keys=True, indent=2), encoding="utf-8",
                )
            except BaseException as reporting_error:
                raise reporting_error from exc
        raise


def _qualify_cases(
    source: Path, workdir: Path, manager: dict, *, cycles: int, native_startup: bool,
    instance_environment: dict[str, str], case: str = "all",
) -> dict:
    started = time.monotonic()
    windows_native = native_startup and sys.platform == "win32"
    report = {
        "schema": 1, "passed": False, "cycle_kind": "finite-guarded-command-fixture",
        "requested_case": case, "complete_case_set": case == "all",
        "requires_process_boundary": True,
        "intentional_cadence_wait_seconds": (
            300 if cycles == 2 and case in ("all", "handover") else 0
        ),
        "real_installation_cycles": False, "native_startup": native_startup,
        "windows_window_and_job_lifetime_proved": False,
        "legacy_controller_handoff_proved": False,
    }
    try:
        if windows_native:
            from windows_api import NativeBlocked, WinAPI, WindowObserver
            from windows_native import observation_passed

            api = WinAPI()
            observer = None
            primary = None
            try:
                observer = WindowObserver(api)
                with observer:
                    if case in ("all", "handover"):
                        report["handover"] = run_selected(
                            report, "handover", workdir / "h", lifecycle.case,
                            source=source, manager=manager, cycles=cycles,
                            native_startup=True, windows_observer=observer,
                            instance_environment=instance_environment,
                        )
                    if case in ("all", "failed-successor"):
                        report["failed_successor"] = run_selected(
                            report, "failed_successor", workdir / "f", lifecycle.case,
                            source=source, manager=manager, cycles=1,
                            failing_standby=True, native_startup=True, windows_observer=observer,
                            instance_environment=instance_environment,
                        )
                packet = observer.packet()
                report["windows_observation"] = packet
                if packet.get("passed") is not True or (
                    "handover" in report and not observation_passed(packet, report["handover"])
                ):
                    raise NativeBlocked(
                        "windows-observation-failed", "inspect the fixture window/Job observation packet",
                    )
                report["windows_window_and_job_lifetime_proved"] = "handover" in report
                report["passed"] = True
                return report
            except BaseException as exc:
                primary = exc
                report["passed"] = False
                raise
            finally:
                failures = []
                if observer is not None:
                    try:
                        report["windows_observation"] = observer.packet()
                    except BaseException as exc:
                        failures.append(exc)
                if observer is None or not observer.thread.is_alive():
                    try:
                        api.close()
                    except BaseException as exc:
                        failures.append(exc)
                else:
                    failures.append(NativeBlocked("observer-cleanup-unconfirmed", "observer is still alive"))
                if failures:
                    report.update(
                        passed=False,
                        windows_finalization_failures=[failure_details(exc) for exc in failures],
                    )
                    if primary is None:
                        raise failures[0]
        if case in ("all", "handover"):
            report["handover"] = run_selected(
                report, "handover", workdir / "handover", lifecycle.case,
                source=source, manager=manager, cycles=cycles, native_startup=native_startup,
                instance_environment=instance_environment,
            )
        if case in ("all", "failed-successor"):
            report["failed_successor"] = run_selected(
                report, "failed_successor", workdir / "failed-successor", lifecycle.case,
                source=source, manager=manager, cycles=1, failing_standby=True,
                native_startup=native_startup,
                instance_environment=instance_environment,
            )
        if native_startup and case in ("all", "signal-drain"):
            report["native_signal_drain"] = run_selected(
                report, "native_signal_drain", workdir / "native-signal-drain", lifecycle.signal_drain_case,
                source=source, manager=manager,
                instance_environment=instance_environment,
            )
        report["passed"] = True
        return report
    except BaseException as exc:
        report.update(passed=False, failure=failure_details(exc))
        raise
    finally:
        report["case_elapsed_seconds"] = time.monotonic() - started
        try:
            (workdir / "qualification.json").write_text(
                json.dumps(report, sort_keys=True, indent=2), encoding="utf-8",
            )
        except BaseException:
            report["passed"] = False
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("qualify")
    run.add_argument("--source", type=Path, required=True)
    run.add_argument("--workdir", type=Path, required=True)
    run.add_argument("--cycles", type=int, choices=(1, 2), default=1)
    run.add_argument("--case", choices=CASES, default="all",
                     help="select one independently supervised lifecycle case; all preserves the combined run")
    run.add_argument("--native-startup", action="store_true",
                     help="explicitly authorize fixture-only current-user native registration")
    controls = sub.add_parser("observe-controls")
    controls.add_argument("--workdir", type=Path, required=True)
    controls.add_argument("--allow-visible-controls", action="store_true",
                          help="authorize one short-lived owned window and foreground restoration")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            output = fixtures.prepare(args.source, args.output)
            print(json.dumps({"prepared": True, "scenario": str(output)}))
        elif args.action == "observe-controls":
            from windows_controls import run_controls
            report = run_controls(args.workdir, allow_visible_controls=args.allow_visible_controls)
            print(json.dumps(report, sort_keys=True))
            return 0 if report["passed"] else 2
        else:
            print(json.dumps(qualify(
                args.source, args.workdir, cycles=args.cycles, native_startup=args.native_startup,
                case=args.case,
            ), sort_keys=True))
    except native.UnsupportedNative as exc:
        print(json.dumps({
            "passed": False, "blocked": True, "unsupported": True,
            "blocker_code": getattr(exc, "code", "native-precondition"),
            "blocker": str(exc),
            "failure": failure_details(exc),
        }))
        return 2
    except (OSError, ValueError, RuntimeError, AssertionError, ImportError,
            subprocess.SubprocessError) as exc:
        print(json.dumps({
            "passed": False, "error": f"{type(exc).__name__}: {exc}",
            "failure": failure_details(exc),
        }))
        return 1
    except BaseException as exc:
        print(json.dumps({"passed": False, "failure": failure_details(exc)}))
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
