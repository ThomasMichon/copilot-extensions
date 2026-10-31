"""Optional provider-owned terminal attachment without another Python front door."""

from __future__ import annotations

import secrets
import hashlib
import json
import os
import sys
from pathlib import Path

_CONTEXT_KEYS = (
    "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
    "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_HOME",
)
_TRANSPORT_KEYS = {
    "COLUMNS", "LINES", "TERM", "COLORTERM", "PSMUX_SESSION", "TMUX",
    "WT_SESSION", "WT_PROFILE_ID", "_", "SHLVL", "PWD", "OLDPWD", "PROMPT",
    "TMUX_PANE", "PSMUX_TARGET_SESSION",
    "TERM_PROGRAM", "TERM_PROGRAM_VERSION",
    "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV",
    "PYTHONUTF8", "AGENT_WORKTREES_LAUNCH_ID", "AGENT_WORKTREES_BINSTUB_STARTED",
    "UV_INTERNAL__PYTHONHOME", "__PYVENV_LAUNCHER__",
    "WORKTREE_MANAGER_ENGINE_ARGV", "WORKTREE_MANAGER_RESIDENT_BOOTSTRAP",
    "PYTEST_CURRENT_TEST",  # pytest changes its phase marker inside one invocation
}


def invocation_context(
    environment: dict[str, str], *, manager_root: Path | None = None,
) -> dict[str, str] | None:
    # Only a digest crosses the process boundary. Unknown differences retain the
    # cold path rather than silently changing child actions or identity.
    if any(value and (
        any(word in key.upper() for word in ("TOKEN", "PASSWORD", "SECRET"))
        or any(word in key.upper() for word in ("BIND_", "HANDOFF", "UPDATE_AUTH"))
        or key.upper().startswith((
            "COPILOT_AGENT_", "AGENT_WORKTREES_SESSION", "AGENT_WORKTREES_AHP_",
            "AGENT_WORKTREES_UPDATE_", "CE_UPDATE_", "AGENT_DISPATCH_", "AGENT_BRIDGE_",
        ))
    ) for key, value in environment.items()):
        return None
    values = {
        key.upper() if os.name == "nt" else key: value
        for key, value in environment.items() if key.upper() not in _TRANSPORT_KEYS
    }
    selected_root = values.get("WORKTREE_MANAGER_ROOT") or manager_root
    if selected_root is not None:
        if "\0" in str(selected_root):
            return None
        try:
            values["WORKTREE_MANAGER_ROOT"] = os.path.normcase(str(Path(selected_root).expanduser().resolve()))
        except (OSError, RuntimeError, ValueError):
            return None
    digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return {**{key: environment.get(key, "") for key in _CONTEXT_KEYS}, "environment_sha256": digest}


def _command(value: object) -> list[str]:
    if (
        not isinstance(value, list) or not value or len(value) > 64
        or any(not isinstance(part, str) or not part or "\0" in part for part in value)
        or not Path(value[0]).is_absolute()
    ):
        raise ValueError("resident provider returned an invalid terminal command")
    return value


def _wait(command: list[str], environment: dict[str, str]) -> int:
    from agent_procutil import spawn_sync_in_kill_on_close_job

    # headless-guard: allow provider-owned user-interactive terminal attachment
    process, custody = spawn_sync_in_kill_on_close_job(command, env=environment)
    try:
        try:
            return process.wait()
        except KeyboardInterrupt:
            return 130
    finally:
        if custody is not None:
            custody.close()
        elif process.poll() is None:
            process.kill()
            process.wait()


def open_session(handoff: dict, engine: list[str], environment: dict[str, str]) -> int | None:
    from .launch_trace import append_launch_event

    append_launch_event("provider_session_entry_start")
    descriptor = handoff.get("manager_session")
    if (
        not isinstance(descriptor, dict) or type(descriptor.get("version")) is not int
        or descriptor["version"] != 1 or not isinstance(descriptor.get("root"), str)
        or not Path(descriptor["root"]).is_absolute()
    ):
        return None
    from work_coalescing_singleton import client
    from zdd import routing

    root = Path(descriptor["root"]).resolve()
    context = invocation_context(environment, manager_root=root)
    if context is None:
        return None
    endpoint = routing.read_active_endpoint(root / "mux-daemon-routing", verify_listener=False)
    append_launch_event("provider_session_route_read")
    if endpoint is None or endpoint.client_host not in {"127.0.0.1", "::1"}:
        return None
    token = ""
    admitted = False
    client_id = client.new_client_id()
    try:
        token = (root / "mux-daemon.token").read_text(encoding="utf-8").strip()
        if not token:
            return None
        try:
            append_launch_event("provider_session_request_start")
            result = client.request(
                endpoint.client_host, endpoint.port, token,
                kind="picker-session-v1", key=secrets.token_hex(16),
                payload={
                    "handoff": handoff, "engine": engine,
                    "provider_generation": endpoint.version,
                    "context": context, "cwd": os.getcwd(),
                    "launch_id": environment.get("AGENT_WORKTREES_LAUNCH_ID", ""),
                    "binstub_started": environment.get("AGENT_WORKTREES_BINSTUB_STARTED", ""),
                },
                request_deadline_s=0.5, client_id=client_id,
            )
        except (OSError, client.DaemonUnavailable, ValueError):
            return None
        if not result.get("available"):
            return None
        admitted = True
        append_launch_event("provider_session_acquired")
        directory = Path(result["directory"]).resolve(strict=True)
        if not directory.is_relative_to(root / "picker-sessions"):
            raise ValueError("resident provider returned a foreign session directory")
        attach = _command(result.get("attach_command"))
        complete = _command(result.get("complete_command"))
        (directory / "attached").touch()
        attach_environment = {
            key: value for key, value in environment.items()
            if key not in {"PSMUX_SESSION", "TMUX"}
        }
        try:
            code = _wait(attach, attach_environment)
        except OSError:
            code = 1
        from agent_procutil.interactive import run_interactive

        return run_interactive([*complete, "--attach-exit", str(code)], env=environment)
    except (OSError, ValueError, KeyError, TypeError):
        if admitted:
            print("error: admitted provider session failed; no cold launch was repeated", file=sys.stderr)
            return 1
        return None
    finally:
        if token:
            client.release(endpoint.client_host, endpoint.port, token, client_id, timeout=0.5)
