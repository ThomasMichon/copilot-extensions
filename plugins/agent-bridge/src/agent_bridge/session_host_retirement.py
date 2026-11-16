"""Account-aware remote Host retirement with confirmed process exit."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

log = logging.getLogger("agent-bridge")


async def reap_remote_host(
    session_id: str, host_pid: int, child_pid: int, endpoint: dict[str, Any],
) -> bool:
    from ssh_manager import ConnectionManager, SSHConfig

    from .session_host.endpoints import ssh_config_from_endpoint

    class _StaticSource:
        def __init__(self, cfg: SSHConfig) -> None:
            self._cfg = cfg

        def get_ssh_config(self) -> SSHConfig:
            return self._cfg

        def refresh(self) -> SSHConfig:
            return self._cfg

    host = None
    mgr = None
    confirmed_dead = False
    try:
        mgr = ConnectionManager()
        # The Host leads its setsid process group; reap the child too, allowing
        # SIGTERM to flush before SIGKILL and checking both processes for exit.
        pid = int(host_pid)
        child_pid = int(child_pid)
        command = (
            f"kill -TERM -{pid} 2>/dev/null || kill -TERM {pid} 2>/dev/null; "
            f"sleep 1; kill -KILL -{pid} 2>/dev/null || kill -KILL {pid} "
            "2>/dev/null || true; "
            "alive() { test -r /proc/$1/stat && "
            "test \"$(awk '{print $3}' /proc/$1/stat 2>/dev/null)\" != Z; }; "
            "i=0; while { alive "
            f"{pid} || alive {child_pid}; "
            "} && test $i -lt 20; do sleep 0.1; i=$((i+1)); done; "
            f"if alive {pid} || alive {child_pid}; then exit 42; fi; "
            "printf __REAPED__"
        )
        if endpoint.get("kind") == "codespace" and "codespace" in endpoint:
            from .session_host.codespace_transport import CodeSpaceTransport

            host = endpoint.get("codespace")
            if not isinstance(host, str) or not host.strip():
                raise ValueError("remote reap requires the persisted CodeSpace name")
            # Durable endpoints intentionally contain no credentials.
            transport = CodeSpaceTransport(
                host, str(endpoint.get("repo") or ""), manager=mgr,
            )
            rc, stdout, _stderr = await transport.run(command, timeout=20.0)
        else:
            # Legacy descriptors contain only the captured SSH configuration.
            cfg = ssh_config_from_endpoint(endpoint)
            host = cfg.host_alias
            await mgr.ensure_connected(host, _StaticSource(cfg), [])
            result = await mgr.exec_command(host, command, timeout=20.0)
            rc, stdout = result.exit_code, result.stdout
        confirmed_dead = rc == 0 and "__REAPED__" in (stdout or "")
        if not confirmed_dead:
            raise RuntimeError(
                f"remote reap could not verify process death (rc={rc}, output={stdout!r})"
            )
        log.info(
            "Remote-reaped Session Host group for session %s (far pid=%s)",
            session_id, host_pid,
        )
    except Exception:
        log.warning(
            "Best-effort remote reap failed for session %s (far pid=%s); "
            "the detached Host will exit when the CodeSpace stops",
            session_id, host_pid, exc_info=True,
        )
    finally:
        if mgr is not None and host:
            with contextlib.suppress(Exception):
                await mgr.disconnect(host)
    return confirmed_dead
