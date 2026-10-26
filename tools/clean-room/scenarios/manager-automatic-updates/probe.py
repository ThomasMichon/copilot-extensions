#!/usr/bin/env python3
"""Portable, credential-free process qualification; never a live install command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Isolated Python omits the script directory; these are prepared-scenario modules.
import fixtures  # noqa: E402
import lifecycle  # noqa: E402
import native  # noqa: E402


def qualify(source: Path, workdir: Path, *, cycles: int, native_startup: bool) -> dict:
    if cycles not in (1, 2):
        raise ValueError("qualification supports one fast cycle or two real-cadence cycles")
    windows_native = native_startup and sys.platform == "win32"
    if windows_native and cycles != 2:
        raise native.UnsupportedNative("Windows native qualification requires --cycles 2 at real cadence")
    if native_startup:
        native.preflight()
    fixtures.verify_prepared(fixtures.payload_path(source))
    workdir = fixtures.private_directory(workdir)
    manager = fixtures.load_manager(source)
    from agent_procutil.daemon_instances import daemon_test_lifecycle, default_registry_root

    with daemon_test_lifecycle(
        owner="manager-automatic-updates-qualification", registry_root=default_registry_root(),
    ) as instance_environment:
        return _qualify_cases(
            source, workdir, manager, cycles=cycles, native_startup=native_startup,
            instance_environment=instance_environment,
        )


def _qualify_cases(
    source: Path, workdir: Path, manager: dict, *, cycles: int, native_startup: bool,
    instance_environment: dict[str, str],
) -> dict:
    windows_native = native_startup and sys.platform == "win32"
    report = {
        "schema": 1, "passed": False, "cycle_kind": "finite-guarded-command-fixture",
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
            try:
                observer = WindowObserver(api)
                with observer:
                    report["handover"] = lifecycle.case(
                        source, workdir / "h", manager, cycles=cycles,
                        native_startup=True, windows_observer=observer,
                        instance_environment=instance_environment,
                    )
                    report["failed_successor"] = lifecycle.case(
                        source, workdir / "f", manager, cycles=1,
                        failing_standby=True, windows_observer=observer,
                        instance_environment=instance_environment,
                    )
                packet = observer.packet()
                report["windows_observation"] = packet
                if not observation_passed(packet, report["handover"]):
                    raise NativeBlocked(
                        "windows-observation-failed", "inspect the fixture window/Job observation packet",
                    )
                report["windows_window_and_job_lifetime_proved"] = True
                report["passed"] = True
                return report
            finally:
                if observer is not None:
                    report["windows_observation"] = observer.packet()
                if observer is None or not observer.thread.is_alive():
                    api.close()
        report["handover"] = lifecycle.case(
            source, workdir / "handover", manager, cycles=cycles, native_startup=native_startup,
            instance_environment=instance_environment,
        )
        report["failed_successor"] = lifecycle.case(
            source, workdir / "failed-successor", manager, cycles=1, failing_standby=True,
            native_startup=native_startup,
            instance_environment=instance_environment,
        )
        if native_startup:
            report["native_signal_drain"] = lifecycle.signal_drain_case(
                source, workdir / "native-signal-drain", manager,
                instance_environment=instance_environment,
            )
        report["passed"] = True
        return report
    finally:
        (workdir / "qualification.json").write_text(
            json.dumps(report, sort_keys=True, indent=2), encoding="utf-8",
        )


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
    run.add_argument("--native-startup", action="store_true",
                     help="explicitly authorize fixture-only current-user native registration")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            output = fixtures.prepare(args.source, args.output)
            print(json.dumps({"prepared": True, "scenario": str(output)}))
        else:
            print(json.dumps(qualify(
                args.source, args.workdir, cycles=args.cycles, native_startup=args.native_startup,
            ), sort_keys=True))
    except native.UnsupportedNative as exc:
        print(json.dumps({
            "passed": False, "blocked": True, "unsupported": True,
            "blocker_code": getattr(exc, "code", "native-precondition"),
            "blocker": str(exc),
        }))
        return 2
    except (OSError, ValueError, RuntimeError, AssertionError, ImportError,
            subprocess.SubprocessError) as exc:
        print(json.dumps({"passed": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
