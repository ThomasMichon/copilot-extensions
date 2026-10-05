"""CLI shim for ``worktree-manager mux-daemon ...``."""

from __future__ import annotations

import json
from pathlib import Path


def cmd_mux_daemon(rest: list[str]) -> int:
    from . import mux_daemon

    args = list(rest)
    if not args:
        print(
            "usage: worktree-manager mux-daemon "
            "<run|ensure|register|remove|show|status> [...]"
        )
        return 2
    action = args.pop(0)
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
            result = mux_daemon.register_managed_mapping(payload)
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
