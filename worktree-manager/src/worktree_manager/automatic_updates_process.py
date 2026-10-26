"""Installed-runtime resolution and authenticated liveness for maintenance."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs, spawn_daemon
from agent_procutil.daemon_broker import WindowsDaemonProcess
from agent_procutil.daemon_instances import DaemonInstanceSpec

from .automatic_updates_policy import PolicyError, state_dir

STATUS_NAME = "worker.json"
RESTART_EXIT = 75


def selected_slot(root: Path) -> Path:
    from .automatic_updates_startup import completed_slot

    try:
        return completed_slot(root)
    except (OSError, ValueError) as exc:
        raise PolicyError(f"selected Manager installation is unavailable: {exc}") from exc


def selected_command(root: Path, args: list[str]) -> list[str]:
    """Resolve today's installed selection, not this resident's original code."""
    from .automatic_updates_startup import retained_command

    try:
        return retained_command(root, args, windows=os.name == "nt")
    except (OSError, ValueError) as exc:
        raise PolicyError(f"selected Manager installation is unavailable: {exc}") from exc


def runtime_identity() -> str:
    from .self_install import running_payload_dir

    prefix = Path(sys.prefix).resolve()
    if prefix.parent.name == "versions":
        return str(prefix)
    return str(running_payload_dir().resolve())


def update_environment(root: Path) -> dict[str, str]:
    """No session credentials or interactive authentication in a resident."""
    from .engine_client import _engine_environment
    from .mux_daemon_process import scrub_session_credentials

    env = {
        key: value
        for key, value in _engine_environment(scrub_session_credentials(dict(os.environ))).items()
        if not key.upper().startswith("GIT_")
    }
    for key in (
        "UV_PROJECT", "UV_PROJECT_ENVIRONMENT",
        "COPILOT_EXTENSIONS_UPDATE_RUN_ID", "COPILOT_EXTENSIONS_UPDATE_EVENTS",
        "COPILOT_EXTENSIONS_UPDATE_OWNER", "SSH_ASKPASS", "GIT_ASKPASS", "SUDO_ASKPASS",
        "WORKTREE_MANAGER_ENGINE_CMD", "WORKTREE_MANAGER_ENGINE_ARGV",
        "WORKTREE_MANAGER_AGENT_WORKTREES_SRC", "COPILOT_EXTENSIONS_CONTEXT",
        "COPILOT_PLUGIN_ROOT", "CE_UPDATE_AUTH",
        "AGENT_WORKTREES_UPDATE_CONTEXT", "AGENT_WORKTREES_PAYLOAD_ROOT",
    ):
        env.pop(key, None)
    env.update({
        "WORKTREE_MANAGER_ROOT": str(root),
        "GIT_TERMINAL_PROMPT": "0",
        "SSH_ASKPASS_REQUIRE": "never",
        "GCM_INTERACTIVE": "never",
        "GH_PROMPT_DISABLED": "1",
        "UV_NO_PROGRESS": "1",
        "PYTHONUTF8": "1",
        "CE_UPDATE_UNATTENDED": "1",
    })
    return env


def read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise PolicyError(f"cannot read maintenance status {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PolicyError(f"maintenance status is not an object: {path}")
    return value


def probe(snapshot: dict[str, Any] | None, *, timeout: float = 1.0) -> dict[str, Any] | None:
    """A live challenge/response, not a PID or heartbeat-file existence test."""
    if snapshot is None:
        return None
    port, cookie, worker_id = (
        snapshot.get("port"), snapshot.get("cookie"), snapshot.get("worker_id")
    )
    if (
        type(port) is not int or not 0 < port < 65536 or not isinstance(cookie, str)
        or len(cookie) != 64 or not isinstance(worker_id, str)
    ):
        raise PolicyError("maintenance status has an invalid liveness endpoint")
    challenge = secrets.token_hex(16)
    request = json.dumps({"cookie": cookie, "challenge": challenge}).encode() + b"\n"
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(request)
            data = bytearray()
            while not data.endswith(b"\n") and len(data) < 16384:
                chunk = connection.recv(min(4096, 16384 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
    except (OSError, TimeoutError):
        return None
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        return None
    if (
        not isinstance(result, dict) or result.get("challenge") != challenge
        or result.get("worker_id") != worker_id
    ):
        return None
    return result


def worker_status(root: Path) -> dict[str, Any]:
    snapshot = read_snapshot(state_dir(root) / STATUS_NAME)
    live = probe(snapshot)
    if live is None:
        previous = (
            {key: value for key, value in snapshot.items() if key != "cookie"}
            if snapshot is not None else None
        )
        return {"running": False, "state": "not-running", "last_observed": previous}
    return {"running": True, **live}


def spawn_worker(
    root: Path, *, standby: str | None = None,
) -> subprocess.Popen | WindowsDaemonProcess:
    args = ["automatic-updates", "run", "--root", str(root)]
    if standby is not None:
        args += ["--standby", standby]
    argv = selected_command(root, args)
    return spawn_daemon(
        argv, cwd=root, log_path=state_dir(root) / "worker.log",
        env=update_environment(root), console_children=True,
        breakaway=True, contained_fallback=False,
        display="Manager automatic maintenance",
        instance=DaemonInstanceSpec(
            "worktree-manager-automatic-updates", root.absolute(), Path(__file__).resolve(),
        ),
    )


def ensure_worker(root: Path, *, timeout: float = 15.0) -> dict[str, Any]:
    from .automatic_updates_autostart import activate_autostart, inspect_autostart
    from .automatic_updates_policy import load_policy

    if load_policy(root).owner != "manager":
        raise PolicyError("Manager automatic maintenance is not enabled")
    registration = inspect_autostart(root)
    status = worker_status(root)
    if registration.get("activation") == "blocked":
        return {
            **status, "activation": "blocked", "state": "blocked",
            "reason": registration.get("blockers"),
        }
    if status["running"]:
        if (
            os.name != "nt" and registration.get("registered") is True
            and registration.get("service_active") is not True
        ):
            return {
                **status, "activation": "blocked",
                "reason": "live worker is outside the registered native unit; drain/handover is required",
            }
        return status
    launcher = None
    if os.name != "nt" and registration.get("registered") is True:
        activation = activate_autostart(root)
        if activation.get("activation") == "blocked":
            return {"running": False, "state": "blocked", "reason": activation.get("blockers")}
    else:
        launcher = spawn_worker(root)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = worker_status(root)
        if status["running"]:
            return status
        if launcher is not None and launcher.poll() is not None:
            raise PolicyError(f"maintenance launcher exited {launcher.returncode}; see worker.log")
        time.sleep(0.1)
    return {
        "running": False, "state": "activation-pending",
        "reason": "worker readiness was not observed; inspect startup state and worker.log",
    }


def child_kwargs() -> dict[str, int]:
    return no_window_kwargs()
