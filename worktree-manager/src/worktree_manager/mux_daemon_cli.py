"""CLI shim for ``worktree-manager mux-daemon ...``."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from agent_procutil import no_window_kwargs

from .mux_child_process import run as run_mux_child
from .mux_mapping_registry import get_mapping, register_next_mapping

_LOOKUP = object()


def _reconnect_cli_panes(payload: dict, incumbent: object = _LOOKUP) -> list[dict]:
    """Preserve a same-owner CLI unless native evidence confirms its loss."""
    identity = ("project", "worktree_id", "worktree_path", "mux_bin",
                "mux_session", "session_incarnation")
    if any(not isinstance(payload.get(key), str) or not payload[key] for key in identity):
        return []
    if incumbent is _LOOKUP:
        incumbent = get_mapping(payload["project"], payload["worktree_id"])
    if not incumbent or incumbent.get("live") is not True:
        return []
    if any(incumbent.get(key) != payload[key] for key in identity if key != "worktree_path"):
        return []
    if os.path.normcase(os.path.realpath(str(incumbent.get("worktree_path") or ""))) != (
        os.path.normcase(os.path.realpath(payload["worktree_path"]))
    ):
        return []
    cli = [
        pane for pane in incumbent.get("panes", [])
        if pane.get("role") == "cli" and pane.get("live") is True
        and re.fullmatch(r"%[0-9]+", str(pane.get("pane_id", "")))
    ]
    if len(cli) != 1 or not re.fullmatch(r"\$[0-9]+:[0-9]+", payload["session_incarnation"]):
        return []
    retained = [{"pane_id": cli[0]["pane_id"], "role": "cli", "live": True}]
    try:
        probe = run_mux_child(
            [payload["mux_bin"], "list-panes", "-s", "-t", "=" + payload["mux_session"], "-F",
             "#{pane_id}\t#{pane_dead}\t#{session_name}\t#{session_id}:#{session_created}"],
            capture_output=True, text=True, timeout=2, **no_window_kwargs(),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return retained
    if probe.returncode or not isinstance(probe.stdout, str):
        return retained
    rows = [line.split("\t") for line in probe.stdout.splitlines() if line.strip()]
    if any(
        len(row) != 4 or not re.fullmatch(r"%[0-9]+", row[0])
        or row[1] not in {"0", "1"} or not row[2] or not re.fullmatch(r"\$[0-9]+:[0-9]+", row[3])
        for row in rows
    ) or len({row[0] for row in rows}) != len(rows):
        return retained
    matches = [row for row in rows if row[0] == cli[0]["pane_id"]]
    if matches != [[cli[0]["pane_id"], "0", payload["mux_session"], payload["session_incarnation"]]]:
        return []
    return retained


def _confirm_creation_cli(payload: dict) -> dict:
    """Confirm the actual launch pane; psmux's session_path may remain HOME."""
    try:
        probe = run_mux_child(
            [payload["mux_bin"], "list-panes", "-s", "-t", "=" + payload["mux_session"], "-F",
             "#{pane_id}\t#{pane_dead}\t#{session_name}\t"
             "#{session_id}:#{session_created}\t#{pane_current_path}"],
            capture_output=True, text=True, timeout=2, **no_window_kwargs(),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return {"applied": False, "reason": "enumeration-unavailable"}
    if probe.returncode or not isinstance(probe.stdout, str):
        return {"applied": False, "reason": "enumeration-unavailable"}
    rows = [line.split("\t") for line in probe.stdout.splitlines() if line.strip()]
    if any(
        len(row) != 5 or not re.fullmatch(r"%[0-9]+", row[0])
        or row[1] not in {"0", "1"} or row[2] != payload["mux_session"]
        or not re.fullmatch(r"\$[0-9]+:[0-9]+", row[3])
        for row in rows
    ) or len({row[0] for row in rows}) != len(rows) or len({row[3] for row in rows}) > 1:
        return {"applied": False, "reason": "session-unconfirmed"}
    if rows and rows[0][3] != payload["session_incarnation"]:
        return {"applied": False, "reason": "stale-incarnation"}
    matches = [row for row in rows if row[0] == payload["panes"][0]["pane_id"]]
    if len(matches) != 1 or matches[0][1] != "0" or not matches[0][4]:
        return {"applied": False, "reason": "cli-unconfirmed"}
    if os.path.normcase(os.path.realpath(matches[0][4])) != os.path.normcase(
        os.path.realpath(payload["worktree_path"])
    ):
        return {"applied": False, "reason": "unattributed-path"}
    return {"applied": True}


def _register_reconnect(payload: dict, *, creation: bool = False) -> dict:
    """Fence both detached creation callbacks and reconnect reconciliation."""
    from . import mux_daemon

    mux_daemon.ensure_daemon_running()
    for attempt in range(3):
        try:
            incumbent = get_mapping(payload.get("project"), payload.get("worktree_id"))
        except (OSError, ValueError, RuntimeError):
            return {"applied": False, "reason": "registry-unavailable"}
        conflict = incumbent is not None and (
            incumbent.get("live") is not True
            or any(incumbent.get(field) != payload.get(field) for field in (
                "project", "worktree_id", "mux_bin", "mux_session", "session_incarnation", "worktree_path",
            ))
        )
        if attempt and (incumbent is None or conflict):
            return {"applied": False, "reason": "mapping-changed"}
        if conflict and re.fullmatch(r"\$[0-9]+:[0-9]+", str(incumbent.get("session_incarnation", ""))):
            from .mux_discovery import confirm_native_session

            if any(not isinstance(payload.get(field), str) or not payload[field] for field in (
                "mux_bin", "mux_session", "session_incarnation", "worktree_path",
            )):
                return {"applied": False, "reason": "session-unconfirmed"}
            try:
                confirmed = (
                    _confirm_creation_cli(payload) if creation
                    else confirm_native_session(payload, connected_only=False)
                )
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                return {"applied": False, "reason": "enumeration-unavailable"}
            if not confirmed.get("applied"):
                return confirmed
        reconciled = dict(
            payload, panes=payload["panes"] if creation else _reconnect_cli_panes(payload, incumbent),
        )
        result = register_next_mapping(reconciled, expected=incumbent)
        if result.get("reason") == "mapping-changed":
            continue
        if result.get("applied"):
            entry = get_mapping(payload["project"], payload["worktree_id"])
            if entry is not None:
                mux_daemon.publish_live_observation(entry, ensure_monitor=True)
        return result
    return {"applied": False, "reason": "mapping-changed"}


def cmd_mux_daemon(rest: list[str]) -> int:
    from . import mux_daemon

    args = list(rest)
    if not args:
        print(
            "usage: worktree-manager mux-daemon "
            "<run|ensure|register|remove|show|status|recovery-evidence> [...]"
        )
        return 2
    action = args.pop(0)
    if action == "recovery-evidence":
        from .restart_capabilities import inspect_recovery
        from .self_install import default_root

        check_attempt = None
        check_launch = None
        check_survivor = None
        if len(args) == 2 and args[0] == "--check-attempt":
            check_attempt = args[1]
        elif len(args) == 2 and args[0] == "--check-launch":
            check_launch = args[1]
        elif len(args) == 2 and args[0] == "--check-survivor":
            check_survivor = args[1]
        elif args:
            print("error: recovery-evidence accepts only --check-attempt, --check-launch "
                  "or --check-survivor <UUID>")
            return 2
        result = inspect_recovery(
            default_root(), check_attempt=check_attempt, check_launch=check_launch,
            check_survivor=check_survivor,
        )
        print(json.dumps(result))
        return 1 if result.get("blocker") else 0
    if action == "status":
        from .daemons_cli import cmd_daemons

        return cmd_daemons(["status", *args])
    if action == "run":
        root = None
        passive = False
        listen_port = None
        for arg in args:
            if arg.startswith("--root="):
                root = Path(arg.split("=", 1)[1])
            elif arg == "--passive":
                passive = True
            elif arg.startswith("--listen-port="):
                try:
                    listen_port = int(arg.split("=", 1)[1])
                except ValueError:
                    print("error: --listen-port must be an integer")
                    return 2
        # Boost here, not inside run_daemon_foreground (mux_daemon.py is at
        # its 1000-line module cap): harmlessly applies a beat early, to a
        # losing/standing-down instance too, since that instance exits
        # immediately after anyway. See process_priority.py's module
        # docstring for the full psmux#608-style rationale.
        from .process_priority import raise_current_process_priority
        raise_current_process_priority()
        return mux_daemon.run_daemon_foreground(
            root,
            passive=passive,
            listen_port=listen_port,
        )
    if action == "ensure":
        ok = mux_daemon.ensure_daemon_running()
        print(json.dumps({"running": ok}))
        return 0 if ok else 1
    if action == "register":
        values: dict[str, str] = {}
        for arg in args:
            if arg.startswith("--") and "=" in arg:
                key, _, value = arg[2:].partition("=")
                values[key.replace("-", "_")] = value
        payload: dict = dict(values)
        cli_pane = payload.pop("cli_pane", None)
        if cli_pane is not None:
            if not cli_pane.startswith("%") or not cli_pane[1:].isdigit():
                print("error: --cli-pane must be a native pane ID")
                return 2
            payload["panes"] = [{"pane_id": cli_pane, "role": "cli", "live": True}]
        for int_field in ("mapping_revision", "attached_clients"):
            if int_field in payload:
                try:
                    payload[int_field] = int(payload[int_field])
                except ValueError:
                    print(f"error: --{int_field.replace('_', '-')} must be an integer")
                    return 2
        if "live" in payload:
            payload["live"] = payload["live"].strip().lower() not in ("0", "false", "no")
        try:
            result = _register_reconnect(payload, creation=cli_pane is not None)
        except ValueError as exc:
            print(f"error: {exc}")
            return 2
        print(json.dumps(result))
        return 0 if result.get("applied") else 1
    if action == "remove":
        project = worktree_id = mux_session = session_incarnation = revision = None
        for arg in args:
            if arg.startswith("--project="):
                project = arg.split("=", 1)[1]
            elif arg.startswith("--worktree-id="):
                worktree_id = arg.split("=", 1)[1]
            elif arg.startswith("--mux-session="):
                mux_session = arg.split("=", 1)[1]
            elif arg.startswith("--session-incarnation="):
                session_incarnation = arg.split("=", 1)[1]
            elif arg.startswith("--mapping-revision="):
                try:
                    revision = int(arg.split("=", 1)[1])
                except ValueError:
                    print("error: --mapping-revision must be an integer")
                    return 2
        if not project or not worktree_id:
            print("error: remove needs --project=NAME --worktree-id=ID")
            return 2
        try:
            result = mux_daemon.remove_managed_mapping(
                project,
                worktree_id,
                mapping_revision=revision,
                mux_session=mux_session,
                session_incarnation=session_incarnation,
            )
        except ValueError as exc:
            print(f"error: {exc}")
            return 2
        print(json.dumps(result))
        return 0 if result.get("applied") else 1
    if action == "show":
        project = None
        worktree_id = None
        for arg in args:
            if arg.startswith("--project="):
                project = arg.split("=", 1)[1]
            elif arg.startswith("--worktree-id="):
                worktree_id = arg.split("=", 1)[1]
        if not project or not worktree_id:
            print("error: show needs --project=NAME --worktree-id=ID")
            return 2
        entry = mux_daemon.get_mapping(project, worktree_id)
        print(json.dumps(entry))
        return 0 if entry is not None else 1
    print(f"error: unknown mux-daemon action {action!r}")
    return 2
