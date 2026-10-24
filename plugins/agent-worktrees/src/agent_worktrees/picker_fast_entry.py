"""Optional bare-TTY handoff without importing the ordinary CLI dispatcher."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

from . import registry_paths

BOOTSTRAP_ENV = "WORKTREE_MANAGER_RESIDENT_BOOTSTRAP"
_MAX_RESPONSE = 64 * 1024


def _request(root: Path, project: str) -> dict | None:
    deadline = time.monotonic() + 1.0
    from zdd import routing

    endpoint = routing.read_active_endpoint(root / "status-monitor-routing", verify_listener=False)
    if endpoint is None or endpoint.client_host not in {"127.0.0.1", "::1"}:
        return None
    token = (root / "status-monitor-control.token").read_text(encoding="utf-8").strip()
    if not token:
        return None
    payload = {
        "contract_version": 1, "project": project, "cwd": str(Path.cwd()),
        "default_live": not any(os.environ.get(key) for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")),
        "provider": os.environ.get("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", "").strip(),
    }
    directory = os.environ.get("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", "").strip()
    if directory:
        payload["providers_dir"] = str(Path(directory).expanduser().resolve())
    body = json.dumps({"version": 1, "token": token, "action": "picker-bootstrap", "payload": payload})
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    with socket.create_connection((endpoint.client_host, endpoint.port), timeout=remaining) as sock:
        sock.sendall(body.encode("utf-8") + b"\n")
        pending = bytearray()
        while b"\n" not in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            chunk = sock.recv(min(65536, _MAX_RESPONSE + 1 - len(pending)))
            if not chunk:
                return None
            pending.extend(chunk)
            if len(pending) > _MAX_RESPONSE:
                return None
    response = json.loads(pending)
    result = response.get("result") if isinstance(response, dict) else None
    if (
        not isinstance(result, dict) or result.get("ok") is not True
        or type(result.get("contract_version")) is not int or result["contract_version"] != 1
        or not isinstance(result.get("runtime_root"), str)
        or not Path(result["runtime_root"]).is_absolute()
        or Path(result["runtime_root"]).resolve() != root.resolve()
    ):
        return None
    bootstrap, command = result.get("bootstrap"), result.get("manager_command")
    if (
        not isinstance(bootstrap, dict) or type(bootstrap.get("version")) is not int
        or bootstrap["version"] != 1
        or bootstrap.get("project") != project or bootstrap.get("headless") is not False
        or not isinstance(command, list) or not command
        or any(not isinstance(part, str) or not part or "\0" in part for part in command)
    ):
        return None
    return result


def dispatch(argv: list[str]) -> int | None:
    if len(argv) != 2 or argv[0] != "--project":
        return None
    if os.environ.get("WORKTREE_MANAGER_ENGINE_CMD"):
        return None
    try:
        if not sys.stdin.isatty():
            return None
        root = registry_paths.registry_root()
        result = _request(root, argv[1])
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    if result is None:
        return None
    environment = dict(os.environ)
    environment[BOOTSTRAP_ENV] = json.dumps(result, separators=(",", ":"))
    environment["WORKTREE_MANAGER_ENGINE_ARGV"] = json.dumps([sys.executable, "-m", "agent_worktrees"])
    environment["PYTHONUTF8"] = "1"
    environment.pop("PYTHONHOME", None)
    environment.pop("UV_INTERNAL__PYTHONHOME", None)
    if result.get("manager_session"):
        from .picker_session_entry import open_session

        code = open_session(result, [sys.executable, "-m", "agent_worktrees"], environment)
        if code is not None:
            return code
    from agent_procutil import spawn_sync_in_kill_on_close_job

    command = [*result["manager_command"], "--project", argv[1]]
    try:
        if os.name != "nt":
            os.execvpe(command[0], command, environment)
            return 1
        process, custody = spawn_sync_in_kill_on_close_job(command, env=environment)
    except OSError:
        return None
    try:
        try:
            return process.wait()
        except KeyboardInterrupt:
            import subprocess

            try:
                return process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                return 130
    finally:
        if custody is not None:
            custody.close()
        elif process.poll() is None:
            process.kill()
            process.wait()
