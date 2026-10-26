"""Explicit local enrollment and the canonical automatic-update admission gate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from zdd.cutover_lock import CutoverLock, CutoverLockedError

from .automatic_updates_policy import (
    Owner,
    Policy,
    PolicyError,
    admit_trigger,
    configure,
    load_policy,
    state_dir,
    validate,
)
from .self_install import default_root


def guarded_update(rest: list[str], execute: Callable[[list[str], str], int]) -> int:
    """All Manager updates share custody, including foreground and legacy calls."""
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--automatic-trigger",
                        choices=("manager", "external", "legacy-agent-machines"))
    parser.add_argument("--controller")
    trigger = "manual"
    try:
        args, forwarded = parser.parse_known_args(rest)
        if args.automatic_trigger:
            trigger = f"automatic:{args.automatic_trigger}"
        if bool(args.controller) != (args.automatic_trigger == "external"):
            raise PolicyError("--controller is required only with --automatic-trigger external")
        root = default_root().absolute()
        with CutoverLock(state_dir(root) / "update-transaction"):
            admit_trigger(root, args.automatic_trigger, args.controller)
            return execute(forwarded, trigger)
    except SystemExit:
        return 2
    except PolicyError as exc:
        return _report_admission_failure(str(exc), locked=False, trigger=trigger)
    except CutoverLockedError as exc:
        return _report_admission_failure(str(exc), locked=True, trigger=trigger)


def _report_admission_failure(message: str, *, locked: bool, trigger: str) -> int:
    from .update_events import UpdateRun

    run = UpdateRun(trigger=trigger)
    run.say(f"Update deferred before installation: {message}", state="failed")
    run.record_admission_error(message, locked=locked)
    return run.finish(5 if locked else 3)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worktree-manager automatic-updates", allow_abbrev=False)
    parser.add_argument(
        "action", nargs="?", default="status",
        choices=("status", "ownership", "enable", "enroll", "disable", "external", "ensure", "run"),
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--apply", action="store_true", help="authorize local policy/startup mutation")
    parser.add_argument("--harness", help="provenance of this explicitly executed trusted setup")
    parser.add_argument("--project", help="registered installation project for canonical updates")
    parser.add_argument("--controller", help="exclusive external automatic-update controller")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    parser.add_argument("--standby", help=argparse.SUPPRESS)
    return parser


def require_registered_project(name: str) -> None:
    from .harness_state import projects_registry

    try:
        projects = projects_registry(strict=True).get("projects", {})
    except (OSError, ValueError) as exc:
        raise PolicyError(f"cannot verify the registered update project: {exc}") from exc
    if not isinstance(projects, dict) or name not in projects:
        raise PolicyError(f"automatic update project {name!r} is not registered")


def _os_account_home() -> Path:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        library = ctypes.WinDLL("shell32", winmode=0x800)
        resolve = library.SHGetFolderPathW
        resolve.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ]
        resolve.restype = wintypes.LONG
        buffer = ctypes.create_unicode_buffer(32768)
        if resolve(None, 40, None, 0, buffer) != 0:
            raise PolicyError("cannot determine the operating-system account profile")
        home = Path(buffer.value)
    else:
        import pwd

        try:
            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (KeyError, OSError) as exc:
            raise PolicyError(f"cannot determine the operating-system account home: {exc}") from exc
    if not home.is_absolute():
        raise PolicyError("operating-system account profile must be absolute")
    return home


def require_legacy_root_binding(root: Path) -> None:
    home = _os_account_home()
    standard = home / ".worktree-manager"
    if root.resolve() != standard.resolve():
        raise PolicyError(
            "automatic enrollment requires the standard user-local Manager root; "
            "alternate roots need a durable binding shared with legacy update controllers"
        )
    for variable in ("HOME", "USERPROFILE"):
        value = os.environ.get(variable)
        if value and (not Path(value).is_absolute() or Path(value).resolve() != home.resolve()):
            raise PolicyError(
                f"{variable} differs from the operating-system account profile; "
                "automatic enrollment requires a durable binding shared with legacy controllers"
            )
    for binding in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_HOME"):
        if os.environ.get(binding):
            raise PolicyError(
                "automatic maintenance currently addresses the user-local legacy installation; "
                f"{binding} requires a durable enrollment binding"
            )


def _result(args: argparse.Namespace, root: Path) -> dict[str, object]:
    from .automatic_updates_autostart import inspect_autostart, register_autostart
    from .automatic_updates_process import ensure_worker, selected_slot, worker_status

    current = load_policy(root)
    if args.action == "ownership":
        return {"policy": asdict(current), "root": str(root)}
    if args.action == "status":
        return {
            "policy": asdict(current), "worker": worker_status(root),
            "startup": inspect_autostart(root), "root": str(root),
        }
    if args.action == "ensure":
        return {"policy": asdict(current), "worker": ensure_worker(root)}
    if args.action == "enroll" and not args.harness:
        raise PolicyError("trusted setup enrollment requires --harness provenance")
    if args.harness and args.action != "enroll":
        raise PolicyError("--harness is valid only for explicit setup enrollment")
    if args.action == "external" and not args.controller:
        raise PolicyError("external ownership requires --controller")
    if args.action != "external" and args.controller:
        raise PolicyError("--controller is valid only for external ownership")
    owner: Owner = "disabled" if args.action == "disable" else (
        "external" if args.action == "external" else "manager"
    )
    planned = {
        "action": args.action, "owner": owner, "root": str(root),
        "interval_seconds": args.interval_seconds, "project": args.project,
        "provenance": f"harness:{args.harness}" if args.harness else "user",
    }
    if args.action == "enroll" and args.apply and current.user_disabled:
        return {
            "state": "user-disabled", "policy": asdict(current),
            "reason": "explicit local disable takes precedence over setup preference",
        }
    validate(asdict(Policy(
        owner=owner, provenance=str(planned["provenance"]),
        interval_seconds=args.interval_seconds, project=args.project,
        external_controller=args.controller,
        user_disabled=owner == "disabled",
    )))
    if owner != "disabled":
        require_legacy_root_binding(root)
    if not args.apply:
        return {"state": "planned", "requested": planned, "policy": asdict(current)}
    if owner != "disabled":
        if owner == "manager":
            selected_slot(root)
            require_registered_project(args.project)
        elif worker_status(root)["running"]:
            raise PolicyError("the previous Manager worker must finish draining before handover")
    legacy_blocker = None
    with CutoverLock(state_dir(root) / "update-transaction") as transaction:
        if owner != "disabled":
            from .automatic_updates_legacy import require_legacy_retired

            require_legacy_retired()
        policy = configure(
            root, owner=owner, provenance=str(planned["provenance"]),
            interval_seconds=args.interval_seconds, project=args.project,
            external_controller=args.controller, harness=args.action == "enroll",
            held_update=transaction,
        )
        if owner == "disabled":
            from .automatic_updates_legacy import require_legacy_retired

            try:
                require_legacy_retired()
            except (PolicyError, OSError, subprocess.SubprocessError) as exc:
                legacy_blocker = str(exc)
    if args.action == "enroll" and policy.user_disabled:
        return {
            "state": "user-disabled", "policy": asdict(policy),
            "reason": "explicit local disable takes precedence over setup preference",
        }
    if owner != "manager":
        return {
            "state": "blocked" if legacy_blocker else (
                "disabled" if owner == "disabled" else "external"
            ),
            "policy": asdict(policy), "worker": worker_status(root),
            "reason": (
                "Manager preference is disabled, but legacy controllers require verification "
                f"or explicit retirement: {legacy_blocker}"
                if legacy_blocker else
                "existing Manager worker drains any active update before exiting"
            ),
        }
    startup = register_autostart(root)
    if startup.get("activation") == "blocked":
        return {
            "state": "blocked", "policy": asdict(policy), "startup": startup,
            "worker": worker_status(root),
        }
    worker = ensure_worker(root)
    if worker.get("activation") == "blocked":
        return {
            "state": "blocked", "policy": asdict(policy), "startup": startup,
            "worker": worker,
        }
    if worker.get("running") is True:
        startup = {
            **startup, "activation": "running", "worker_readiness": "verified",
            "blockers": [],
        }
    return {"policy": asdict(policy), "startup": startup, "worker": worker}


def main(argv: list[str]) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = (args.root or default_root()).expanduser().absolute()
    try:
        if args.apply and args.action in ("enable", "enroll", "external") and (
            args.action != "enroll" or not load_policy(root).user_disabled
        ):
            require_legacy_root_binding(root)
        if args.action == "run":
            from .automatic_updates_runtime import run_worker

            return run_worker(root, standby=args.standby)
        preview = args.action in ("enable", "enroll", "disable", "external") and not args.apply
        if args.action in ("status", "ownership") or preview:
            result = _result(args, root)
        else:
            with CutoverLock(state_dir(root) / "enrollment"):
                result = _result(args, root)
    except (OSError, PolicyError, CutoverLockedError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {"state": "blocked", "reason": str(exc), "root": str(root)}
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(f"automatic updates: blocked: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, sort_keys=True, indent=2))
    worker = result.get("worker")
    if result.get("state") == "blocked":
        return 1
    if isinstance(worker, dict) and worker.get("activation") == "blocked":
        return 1
    return 1 if isinstance(worker, dict) and worker.get("running") is False and args.action in (
        "enable", "enroll", "ensure",
    ) else 0
