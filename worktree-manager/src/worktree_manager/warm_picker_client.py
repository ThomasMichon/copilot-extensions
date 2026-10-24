"""Attach a genuine terminal to an already-warm, scoped Manager UI process."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path


def run(handoff: dict) -> tuple[bool, dict | None]:
    from .production_picker.picker_tui.frame_health import append_launch_event

    append_launch_event("warm_picker_client_start")
    if not sys.stdin.isatty() or (os.name != "nt" and os.environ.get("TMUX")):
        return False, None
    from . import engine_client
    from .self_install import default_root
    from .warm_picker_contract import KIND, invocation_context, runtime_identity
    from work_coalescing_singleton import client as wcs_client
    from zdd import routing

    root = default_root()
    context = invocation_context(manager_root=root)
    if context is None:
        return False, None
    route = routing.read_active_endpoint(root / "mux-daemon-routing", verify_listener=False)
    if route is None or route.client_host not in {"127.0.0.1", "::1"}:
        return False, None
    client_id = wcs_client.new_client_id()
    token = ""

    def release():
        if token:
            wcs_client.release(route.client_host, route.port, token, client_id, timeout=0.5)

    try:
        token = (root / "mux-daemon.token").read_text(encoding="utf-8").strip()
        if not token:
            return False, None
        result = wcs_client.request(
            route.client_host, route.port, token, kind=KIND,
            payload={
                "handoff": handoff, "engine": engine_client.inherited_engine_command(),
                "manager_runtime": runtime_identity(), "context": context, "cwd": os.getcwd(),
                "launch_id": os.environ.get("AGENT_WORKTREES_LAUNCH_ID", ""),
                "binstub_started": os.environ.get("AGENT_WORKTREES_BINSTUB_STARTED", ""),
            },
            key=secrets.token_hex(16), request_deadline_s=0.5, client_id=client_id,
        )
    except (OSError, wcs_client.DaemonUnavailable, ValueError):
        release()
        return False, None
    if not result.get("available"):
        append_launch_event("warm_picker_unavailable", reason=result.get("reason", "unknown"))
        release()
        return False, None
    append_launch_event("warm_picker_session_acquired")
    directory = Path(result["directory"])
    if not directory.is_relative_to(root / "picker-sessions"):
        raise RuntimeError("resident UI returned a foreign session directory")
    environment = dict(os.environ)
    environment.pop("PSMUX_SESSION", None)
    environment.pop("TMUX", None)
    try:
        (directory / "attached").touch()
        # headless-guard: allow user-interactive mux attachment to the caller's TTY
        completed = subprocess.run(
            result["attach_command"], env=environment,
        )
        path = directory / "result.json"
        if not path.is_file():
            raise RuntimeError("warm Picker detached without a completed decision")
        outcome = json.loads(path.read_text(encoding="utf-8"))
        if completed.returncode or outcome.get("ok") is not True:
            raise RuntimeError("warm Picker failed; no decision was executed")
        return True, outcome.get("decision")
    finally:
        try:
            (directory / "consumed").touch()
        except FileNotFoundError:
            pass  # An ownerless worker can already have retired its slot.
        release()
