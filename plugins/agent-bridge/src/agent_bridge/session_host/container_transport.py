"""Trusted-container remote transport for agent-bridge Session Hosts.

agent-containers owns container readiness, trust validation, and SSH/auth
preparation.  agent-bridge owns the Session Host bundle, process lifecycle,
forwards, durable authority, and reattachment.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import posixpath
import re
import shlex
from pathlib import Path
from typing import Any

from agent_procutil import no_window_flags
from ssh_manager import ConnectionManager, SSHConfig

log = logging.getLogger("agent-bridge.session-host.container")


class ContainerRecreateAfterRemovalError(RuntimeError):
    """The provider removed the old identity before replacement failed."""


async def _run_provider(
    command: list[str],
    *,
    timeout: float,
) -> tuple[int, bytes, bytes]:
    from ..transport import _wrap_batch_for_windows

    env = os.environ.copy()
    command = _wrap_batch_for_windows(list(command), env)
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=no_window_flags(),
        start_new_session=os.name != "nt",
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError) as exc:
        from ..transport import AgentProcess, SpawnTarget

        with contextlib.suppress(Exception):
            await AgentProcess(proc, SpawnTarget(type="command")).kill()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise RuntimeError(
            f"provider command timed out after {timeout:.0f}s"
        ) from exc
    return proc.returncode or 0, out, err


class _StaticConfigSource:
    def __init__(self, config: SSHConfig) -> None:
        self._config = config

    def get_ssh_config(self) -> SSHConfig:
        return self._config

    def refresh(self) -> SSHConfig:
        return self._config


def _ssh_config(raw: dict[str, Any]) -> SSHConfig:
    return SSHConfig(
        host_alias=str(raw.get("host_alias") or ""),
        hostname=raw.get("hostname"),
        user=raw.get("user"),
        port=raw.get("port"),
        identity_file=raw.get("identity_file"),
        proxy_command=raw.get("proxy_command"),
        config_file=raw.get("config_file"),
        extra_options=dict(raw.get("extra_options") or {}),
    )


async def prepare_container_session_host(
    target: dict[str, Any],
    host_relay_port: int | None,
) -> dict[str, Any]:
    """Ask the provider for one launch's secret-backed remote command."""
    from ..transport import _reresolve_stale_interpreter

    command = _reresolve_stale_interpreter(
        list(target.get("provider_command") or [])
    )
    name = str(target.get("name") or "")
    if not command or not name:
        raise RuntimeError("trusted container metadata lacks provider command/name")
    command += ["session-host-prepare", name]
    if host_relay_port is not None:
        command += ["--host-relay-port", str(host_relay_port)]
    rc, out, err = await _run_provider(command, timeout=60.0)
    if rc != 0:
        raise RuntimeError(
            "agent-containers session-host preparation failed "
            f"(rc={rc}): "
            f"{(err or out).decode(errors='replace').strip()}"
        )
    try:
        result = json.loads(out.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "agent-containers session-host preparation returned invalid JSON"
        ) from exc
    if (
        result.get("name") != name
        or not isinstance(result.get("ssh"), dict)
        or not result.get("remote_command")
    ):
        raise RuntimeError(
            "agent-containers session-host preparation returned incomplete data"
        )
    return result


async def ensure_container_ready(target: dict[str, Any]) -> None:
    """Delegate lifecycle/readiness to agent-containers before replacement."""
    from ..transport import _reresolve_stale_interpreter

    command = _reresolve_stale_interpreter(
        list(target.get("provider_command") or [])
    )
    name = str(target.get("name") or "")
    if not command or not name:
        raise RuntimeError("trusted container metadata lacks provider command/name")
    rc, out, err = await _run_provider(
        [*command, "namespace-ensure-ready", name],
        timeout=120.0,
    )
    if rc != 0:
        try:
            failure = json.loads(out.decode()) if out else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            failure = {}
        if (
            isinstance(failure, dict)
            and failure.get("old_container_removed") is True
        ):
            raise ContainerRecreateAfterRemovalError(
                str(failure.get("error") or "container replacement failed")
            )
        raise RuntimeError(
            "agent-containers could not make the Session Host venue ready "
            f"(rc={rc}): {(err or out).decode(errors='replace').strip()}"
        )


async def container_state(target: dict[str, Any]) -> dict[str, Any]:
    """Read one container's non-waking lifecycle identity through its provider."""
    from ..transport import _reresolve_stale_interpreter

    command = _reresolve_stale_interpreter(
        list(target.get("state_command") or [])
    )
    if not command:
        provider = _reresolve_stale_interpreter(
            list(target.get("provider_command") or [])
        )
        name = str(target.get("name") or "")
        command = [*provider, "session-host-state", name]
    rc, out, err = await _run_provider(command, timeout=45.0)
    if rc != 0:
        raise RuntimeError(
            "agent-containers could not inspect the target "
            f"(rc={rc}): {(err or out).decode(errors='replace').strip()}"
        )
    try:
        result = json.loads(out.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "agent-containers returned invalid container state"
        ) from exc
    if not isinstance(result, dict):
        raise RuntimeError("agent-containers returned non-object container state")
    return result


async def recreate_container_for_parity(
    target: dict[str, Any],
    *,
    expected_container_id: str,
    timeout: float,
) -> dict[str, Any]:
    """Identity-check and recreate one container through the provider seam."""
    from ..transport import _reresolve_stale_interpreter

    command = _reresolve_stale_interpreter(
        list(target.get("provider_command") or [])
    )
    name = str(target.get("name") or "")
    if not command or not name:
        raise RuntimeError("trusted container metadata lacks provider command/name")
    rc, out, err = await _run_provider(
        [
            *command,
            "namespace-recreate",
            name,
            "--expected-container-id",
            expected_container_id,
            "--timeout",
            str(timeout),
        ],
        timeout=timeout,
    )
    if rc != 0:
        try:
            failure = json.loads(out.decode()) if out else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            failure = {}
        if (
            isinstance(failure, dict)
            and failure.get("old_container_removed") is True
        ):
            raise ContainerRecreateAfterRemovalError(
                str(failure.get("error") or "container replacement failed")
            )
        raise RuntimeError(
            "agent-containers target recreation failed "
            f"(rc={rc}): {(err or out).decode(errors='replace').strip()}"
        )
    try:
        result = json.loads(out.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "agent-containers target recreation returned invalid JSON"
        ) from exc
    new_container_id = str(result.get("new_container_id") or "")
    if (
        not isinstance(result, dict)
        or result.get("name") != name
        or result.get("old_container_id") != expected_container_id
        or not re.fullmatch(r"[0-9a-fA-F]{12,64}", new_container_id)
        or new_container_id.lower() == expected_container_id.lower()
        or result.get("identity_changed") is not True
        or result.get("running") is not True
    ):
        raise RuntimeError(
            "agent-containers target recreation did not confirm replacement"
        )
    return result


async def cleanup_container_session_host(
    target: dict[str, Any],
    prepared: dict[str, Any],
) -> bool:
    """Best-effort removal of one launch-only secret env file."""
    from ..transport import _reresolve_stale_interpreter

    remote_env = prepared.get("remote_env")
    if not remote_env:
        return True
    command = _reresolve_stale_interpreter(
        list(target.get("provider_command") or [])
    )
    name = str(target.get("name") or "")
    if not command or not name:
        return False
    rc, out, err = await _run_provider(
        [
            *command,
            "session-host-cleanup",
            name,
            "--remote-env",
            str(remote_env),
        ],
        timeout=30.0,
    )
    if rc != 0:
        log.warning(
            "agent-containers launch-env cleanup failed for %s (rc=%s): %s",
            name,
            rc,
            (err or out).decode(errors="replace").strip(),
        )
        return False
    return True


class ContainerTransport:
    """OpenSSH transport into one trusted local dev container."""

    boundary = "container"

    def __init__(
        self,
        name: str,
        ssh: dict[str, Any],
        *,
        state_command: list[str],
        reverse_forwards: list[str] | None = None,
        manager: ConnectionManager | None = None,
    ) -> None:
        self._name = name
        self._config = _ssh_config(ssh)
        self._source = _StaticConfigSource(self._config)
        self._manager = manager or ConnectionManager()
        from ..transport import _reresolve_stale_interpreter

        self._state_command = _reresolve_stale_interpreter(
            list(state_command)
        )
        self._reverse_forwards = list(reverse_forwards or [])
        self._connected = False
        self._home_dir: str | None = None
        match = re.search(
            r"-([0-9a-fA-F]{12})$", self._config.host_alias,
        )
        self._container_id_prefix = match.group(1).lower() if match else None

    async def _ensure(self) -> None:
        if not self._connected:
            await self._manager.ensure_connected(
                f"container:{self._name}", self._source, [],
            )
            self._connected = True

    async def run(
        self,
        command: str,
        *,
        timeout: float = 60.0,
        input_bytes: bytes | None = None,
    ) -> tuple[int, str, str]:
        await self._ensure()
        result = await self._manager.exec_command(
            f"container:{self._name}",
            command,
            timeout=timeout,
            input_bytes=input_bytes,
        )
        return result.exit_code, result.stdout, result.stderr

    async def path_exists(self, remote_path: str) -> bool:
        _rc, out, _err = await self.run(
            f"test -f {shlex.quote(remote_path)} && echo __EXISTS__ || true",
            timeout=30.0,
        )
        return "__EXISTS__" in out

    async def home_dir(self) -> str:
        if self._home_dir is not None:
            return self._home_dir
        rc, out, err = await self.run('printf "%s" "$HOME"', timeout=30.0)
        home = out.strip()
        if rc != 0 or not home.startswith("/"):
            raise RuntimeError(
                f"Could not resolve remote home for {self._name}: {err or out}"
            )
        self._home_dir = home
        return home

    async def push_file(self, local_path: str, remote_path: str) -> None:
        parent = posixpath.dirname(remote_path)
        temporary = f"{remote_path}.tmp"
        command = (
            f"mkdir -p {shlex.quote(parent)} && "
            f"cat > {shlex.quote(temporary)} && "
            f"chmod 600 {shlex.quote(temporary)} && "
            f"mv -f {shlex.quote(temporary)} {shlex.quote(remote_path)}"
        )
        payload = await asyncio.to_thread(Path(local_path).read_bytes)
        rc, out, err = await self.run(
            command,
            timeout=180.0,
            input_bytes=payload,
        )
        if rc != 0:
            raise RuntimeError(
                f"container Session Host bundle copy failed (rc={rc}): "
                f"{err or out}"
            )

    async def is_running(self) -> bool:
        """Read Docker state through the provider without starting the venue."""
        rc, out, err = await _run_provider(
            self._state_command,
            timeout=45.0,
        )
        if rc != 0:
            raise RuntimeError(
                f"Could not inspect container state: "
                f"{(err or out).decode(errors='replace').strip()}"
            )
        try:
            state = json.loads(out.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "agent-containers session-host-state returned invalid JSON"
            ) from exc
        current_id = str(state.get("container_id") or "").lower()
        identity_matches = (
            self._container_id_prefix is None
            or current_id.startswith(self._container_id_prefix)
        )
        return (
            state.get("name") == self._name
            and state.get("running") is True
            and identity_matches
        )

    def ssh_config(self) -> SSHConfig:
        return self._config

    def endpoint_extra(self) -> dict[str, Any]:
        return {
            "container": self._name,
            "state_command": self._state_command,
        }

    def reverse_forwards(self) -> list[str]:
        return list(self._reverse_forwards)


def build_container_spawner(
    target: dict[str, Any],
    *,
    prepared: dict[str, Any] | None = None,
    ready_timeout: float = 120.0,
    unexpected_reap_seconds: float = 60.0,
    active_reap_seconds: float = 0.0,
    require_relay_ready: bool = False,
):
    """Construct the generic remote spawner for a trusted container."""
    from .spawner import CodeSpaceSpawner

    source = prepared or target
    name = str(target["name"])
    state_command = list(
        source.get("state_command")
        or [*list(target["provider_command"]), "session-host-state", name]
    )
    transport = ContainerTransport(
        name,
        dict(source["ssh"]),
        state_command=state_command,
        reverse_forwards=list(source.get("reverse_forwards") or []),
    )
    return CodeSpaceSpawner(
        transport,
        remote_dir="/tmp/agent-bridge",
        ready_timeout=ready_timeout,
        unexpected_reap_seconds=unexpected_reap_seconds,
        active_reap_seconds=active_reap_seconds,
        require_relay_ready=require_relay_ready,
    )
