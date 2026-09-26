"""CLI surface for the Worktree Manager mux-companion daemon."""

from __future__ import annotations

import json
from pathlib import Path


def cmd_mux_daemon(rest: list[str]) -> int:
    """Expose the Manager mux-daemon internals and Step-3 launch-path bundle."""
    from . import managed_mux_session, mux_daemon

    args = list(rest)
    if not args:
        print(
            "usage: worktree-manager mux-daemon "
            "<run|ensure|register|remove|show|activate|deactivate> [...]"
        )
        return 2
    action = args.pop(0)
    root = None
    for arg in args:
        if arg.startswith("--root="):
            root = Path(arg.split("=", 1)[1])
    if action == "run":
        return mux_daemon.run_daemon_foreground(root)
    if action == "ensure":
        ok = mux_daemon.ensure_daemon_running(root)
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
            result = mux_daemon.register_mapping(payload, root=root)
        except ValueError as exc:
            print(f"error: {exc}")
            return 2
        print(json.dumps(result))
        return 0 if result.get("applied") else 1
    if action == "remove":
        project = None
        worktree_id = None
        revision = None
        for arg in args:
            if arg.startswith("--project="):
                project = arg.split("=", 1)[1]
            elif arg.startswith("--worktree-id="):
                worktree_id = arg.split("=", 1)[1]
            elif arg.startswith("--mapping-revision="):
                revision_raw = arg.split("=", 1)[1]
                try:
                    revision = int(revision_raw)
                except ValueError:
                    print("error: --mapping-revision must be an integer")
                    return 2
        if not project or not worktree_id:
            print("error: remove needs --project=NAME --worktree-id=ID")
            return 2
        try:
            result = mux_daemon.remove_mapping(
                project, worktree_id, mapping_revision=revision, root=root
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
        entry = mux_daemon.get_mapping(project, worktree_id, root=root)
        print(json.dumps(entry))
        return 0 if entry is not None else 1
    if action == "activate":
        values: dict[str, str] = {}
        for arg in args:
            if arg.startswith("--") and "=" in arg:
                key, _, value = arg[2:].partition("=")
                values[key.replace("-", "_")] = value
        required = ("project", "worktree_id", "worktree_path", "mux_session", "mux_bin")
        missing = [field for field in required if not values.get(field)]
        if missing:
            print(
                "error: activate needs "
                "--project=NAME --worktree-id=ID --worktree-path=PATH "
                "--mux-session=NAME --mux-bin=PATH"
            )
            return 2
        result = managed_mux_session.activate_managed_session(
            values["project"],
            values["worktree_id"],
            values["worktree_path"],
            values["mux_session"],
            values["mux_bin"],
            root=root,
        )
        print(json.dumps(result))
        return 0 if result.get("mapping", {}).get("applied") else 1
    if action == "deactivate":
        values: dict[str, str] = {}
        for arg in args:
            if arg.startswith("--") and "=" in arg:
                key, _, value = arg[2:].partition("=")
                values[key.replace("-", "_")] = value
        if not values.get("project") or not values.get("worktree_id") or not values.get("mux_session"):
            print(
                "error: deactivate needs "
                "--project=NAME --worktree-id=ID --mux-session=NAME"
            )
            return 2
        result = managed_mux_session.deactivate_managed_session(
            values["project"],
            values["worktree_id"],
            values["mux_session"],
            root=root,
        )
        print(json.dumps(result))
        return 0 if result.get("mapping", {}).get("applied") else 1
    print(f"error: unknown mux-daemon action {action!r}")
    return 2
