"""Synchronous pane-boundary admission; never install or start a service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from . import mux_mapping_registry as mappings, source_config

SCHEMA = "worktree-manager.pane-lifecycle"
VERSION = 1


def installation_identity(root: Path) -> str:
    """Identify the owning installation, not its replaceable runtime generation."""
    if not root.is_absolute():
        raise ValueError("Pane lifecycle requires an absolute installation root")
    owner = {
        "product": "worktree-manager",
        "root": os.path.normcase(os.path.abspath(root)),
        "environment": sys.platform,
        "source": source_config.resolved_repo(root),
        "ref": source_config.resolved_ref(root),
    }
    return hashlib.sha256(json.dumps(owner, sort_keys=True).encode("utf-8")).hexdigest()


def require_current_runtime(root: Path) -> None:
    """Refuse a captured old slot, source-mode fallback or incomplete install."""
    prefix = Path(sys.prefix)
    if (
        prefix.parent.name != "versions"
        or os.path.normcase(os.path.abspath(prefix.parent.parent))
        != os.path.normcase(os.path.abspath(root))
        or (root / "current-version").read_text(encoding="utf-8").strip() != prefix.name
        or not (prefix / ".install-complete.json").is_file()
        or not (prefix / ".runtime-bootstrap" / "runtime_launcher.py").is_file()
    ):
        raise ValueError("Pane lifecycle requires this installation's current completed runtime")


def _register_pane(payload: dict, root: Path) -> dict:
    for _attempt in range(3):
        try:
            current = mappings.get_mapping(
                payload["project"], payload["worktree_id"], root=root, strict=True,
            )
        except (OSError, ValueError):
            return {"applied": False, "reason": "registry-unavailable"}
        candidate = payload
        if current is not None and current["live"]:
            if (
                any(current.get(field) != payload[field] for field in (
                    "mux_session", "session_incarnation",
                ))
                or not mappings._same_worktree_path(current["worktree_path"], payload["worktree_path"])
                or not mappings._same_mux_executable(current["mux_bin"], payload["mux_bin"])
            ):
                return {"applied": False, "reason": "incumbent-mapping"}
            pane = payload["panes"][0]
            prior = [item for item in current["panes"] if item["pane_id"] == pane["pane_id"]]
            if prior and prior != [pane]:
                return {"applied": False, "reason": "incumbent-pane"}
            candidate = dict(
                current, mux_bin=payload["mux_bin"],
                panes=[*current["panes"], *([] if prior else [pane])],
            )
            candidate.pop("mapping_revision")
        elif current is not None and current["session_incarnation"] == payload["session_incarnation"]:
            return {"applied": False, "reason": "incumbent-tombstone"}
        elif current is not None:
            try:
                from .mux_discovery import confirm_creation_cli

                confirmed = confirm_creation_cli(payload)
            except (ImportError, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                return {"applied": False, "reason": "enumeration-unavailable"}
            if not confirmed.get("applied"):
                return confirmed
        result = mappings.register_next_mapping(candidate, root=root, expected=current)
        if result.get("reason") != "mapping-changed":
            return result
    return {"applied": False, "reason": "mapping-changed"}


def _exit_activity(project: str, worktree_id: str, exit_code: int, runtime: int, launch_id: str | None) -> dict:
    try:
        from agent_procutil import no_window_kwargs

        from . import engine_client

        command = engine_client.installed_engine_command()
        if command is None:
            return {"applied": False, "reason": "engine-unavailable"}
        arguments = [
            *command, "--project", project, "activity-log", "pane_exited",
            "--source", "launcher", "--worktree-id", worktree_id,
            "--field", f"exit_code={exit_code}", "--field", f"runtime={runtime}",
            *(["--launch-id", launch_id] if launch_id else []),
        ]
        result = subprocess.run(
            arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=1.0, env=engine_client._engine_environment(),
            **no_window_kwargs(),
        )
        return {"attempted": True, "command_exit_code": result.returncode}
    except (ImportError, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {"applied": False, "reason": "activity-unconfirmed"}


def admit(
    action: str, *, root: Path, expected_installation: str | None,
    project: str, worktree_id: str, mux_session: str,
    session_incarnation: str, pane_id: str,
    worktree_path: str | None = None, mux_bin: str | None = None,
    exit_code: int | None = None,
    runtime: int | None = None, launch_id: str | None = None,
) -> dict:
    identity = installation_identity(root)
    if expected_installation is not None and expected_installation != identity:
        raise ValueError("Pane lifecycle installation changed")
    if (
        not project or not worktree_id or not mux_session
        or re.fullmatch(r"\$[0-9]+:[0-9]+", session_incarnation) is None
        or re.fullmatch(r"%[0-9]+", pane_id) is None
    ):
        raise ValueError("Pane lifecycle requires exact native session and pane identities")
    if action == "start":
        if not worktree_path or not Path(worktree_path).is_absolute() or not mux_bin:
            raise ValueError("Pane start requires its worktree and selected mux executable")
        result = _register_pane({
            "project": project, "worktree_id": worktree_id,
            "worktree_path": worktree_path, "mux_session": mux_session,
            "mux_bin": mux_bin, "session_incarnation": session_incarnation,
            "panes": [{"pane_id": pane_id, "role": "cli", "live": True}],
        }, root)
    elif action in {"end", "cancel"}:
        if expected_installation is None:
            raise ValueError("Pane teardown requires its original installation")
        if action == "end" and type(exit_code) is not int:
            raise ValueError("Pane end requires its observed exit code")
        if action == "cancel" and any(value is not None for value in (exit_code, runtime, launch_id)):
            raise ValueError("Pane cancellation cannot claim a child exit")
        if runtime is not None and (type(runtime) is not int or runtime < 0):
            raise ValueError("Pane runtime must be a nonnegative integer")
        if launch_id is not None and (
            not isinstance(launch_id, str) or not launch_id or len(launch_id) > 512
        ):
            raise ValueError("Invalid pane launch correlation")
        result = mappings.remove_mapping(
            project, worktree_id, mux_session=mux_session,
            session_incarnation=session_incarnation, pane_id=pane_id,
            exit_code=exit_code, root=root,
        )
    else:
        raise ValueError("Unsupported pane lifecycle boundary")
    if result.get("applied"):
        try:
            from .mux_daemon_live import publish_live_observation

            entry = mappings.get_mapping(project, worktree_id, root=root, strict=True)
            if entry is not None:
                result = dict(result, observation=publish_live_observation(
                    entry, ensure_monitor=False, request_deadline_s=2.0,
                ))
        except (ImportError, OSError, ValueError, RuntimeError):
            result = dict(result, observation={"applied": False, "reason": "monitor-unavailable"})
    if (
        action == "end" and runtime is not None and result.get("applied")
        and result.get("reason") != "already-observed"
    ):
        result = dict(result, activity=_exit_activity(project, worktree_id, exit_code, runtime, launch_id))
    return {
        "schema": SCHEMA, "version": VERSION, "action": action,
        "installation": identity, "result": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "end", "cancel"))
    parser.add_argument("--contract-version", type=int, choices=(VERSION,), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-installation")
    for name in ("project", "worktree-id", "mux-session", "session-incarnation", "pane-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--worktree-path")
    parser.add_argument("--mux-bin")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--runtime", type=int)
    parser.add_argument("--launch-id")
    parser.add_argument("--shell", action="store_true")
    args = parser.parse_args()
    shell = vars(args).pop("shell")
    vars(args).pop("contract_version")
    try:
        require_current_runtime(args.root)
        result = admit(**vars(args))
    except (OSError, ValueError) as error:
        print(json.dumps({"schema": SCHEMA, "version": VERSION, "error": str(error)}))
        return 3
    if shell:
        print("\t".join((
            "worktree-pane-boundary-v1", result["action"], result["installation"],
            "1" if result["result"].get("applied") else "0",
        )))
    else:
        print(json.dumps(result))
    return 0 if result["result"].get("applied") else 1


if __name__ == "__main__":
    raise SystemExit(main())
