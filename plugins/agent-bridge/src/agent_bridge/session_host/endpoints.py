"""Rebuild a local ``-L`` forward from a **persisted** endpoint descriptor.

A remote (CodeSpace / machine-mesh) Session Host is reached over an ``ssh -N -L``
forward. The *live* forward is held by the Spawner while the frontend runs, but a
**restarted** frontend has only the durable :class:`~.host_index.HostIndex`. So
``HostRecord.endpoint`` must carry everything needed to rebuild the forward from
**ssh-manager alone** -- no live Spawner, and (critically) no ``agent-codespaces``
import in the agent-bridge daemon. This module is that codec.

The descriptor is a plain JSON dict (it round-trips through the host index), so it
holds only the serializable :class:`~ssh_manager.SSHConfig` fields plus the
remote/local ports and a ``kind`` tag.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from ssh_manager import (
    LocalForward,
    SSHConfig,
    SupervisedRelayForward,
    build_remote_exec_args,
)


class CredentialRelayReadinessError(RuntimeError):
    """A required remote credential-relay listener could not be proven ready."""


async def wait_for_relay_serving(
    probe: Callable[[], Awaitable[bool]],
    *,
    timeout: float = 5.0,
    interval: float = 0.25,
) -> None:
    """Wait briefly for a required far-side relay listener to accept."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    last_error: Exception | None = None
    while True:
        try:
            if await probe():
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval, remaining))
    error = CredentialRelayReadinessError(
        "credential relay reverse-forward did not become ready before timeout"
    )
    if last_error is not None:
        raise error from last_error
    raise error


def endpoint_from_ssh_config(
    config: SSHConfig,
    remote_port: int,
    local_port: int,
    *,
    kind: str,
    reverse_forwards: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize how to re-forward a remote Host endpoint into a durable dict."""
    return {
        "kind": kind,
        "remote_port": int(remote_port),
        "local_port": int(local_port),
        "reverse_forwards": list(reverse_forwards or []),
        "ssh": {
            "host_alias": config.host_alias,
            "hostname": config.hostname,
            "user": config.user,
            "port": config.port,
            "identity_file": config.identity_file,
            "proxy_command": config.proxy_command,
            "config_file": config.config_file,
            "extra_options": dict(config.extra_options),
        },
        **(extra or {}),
    }


def ssh_config_from_endpoint(endpoint: dict[str, Any]) -> SSHConfig:
    """Rebuild the :class:`SSHConfig` captured in an endpoint descriptor."""
    ssh = endpoint.get("ssh", {})
    return SSHConfig(
        host_alias=ssh.get("host_alias", ""),
        hostname=ssh.get("hostname"),
        user=ssh.get("user"),
        port=ssh.get("port"),
        identity_file=ssh.get("identity_file"),
        proxy_command=ssh.get("proxy_command"),
        config_file=ssh.get("config_file"),
        extra_options=ssh.get("extra_options") or {},
    )


def forward_from_endpoint(endpoint: dict[str, Any]) -> LocalForward:
    """Build a :class:`LocalForward` (not yet established) from a descriptor.

    Pins the previously-advertised ``local_port`` so a cached ``HostRecord.port``
    still resolves after the forward is re-established on reattach.
    """
    config = ssh_config_from_endpoint(endpoint)
    return LocalForward(
        config,
        int(endpoint["remote_port"]),
        local_port=int(endpoint["local_port"]),
    )


def relay_ports_from_reverse_forwards(reverse_forwards: Iterable[str]) -> list[int]:
    """Extract relay listen ports from ``-R`` specs carried in an endpoint.

    The credential relay specs emitted by the CodeSpace transport are
    ``<port>:127.0.0.1:<port>``. Invalid or non-integer specs are ignored so
    older/foreign descriptors degrade to the pre-relay-supervisor behavior.
    """
    ports: list[int] = []
    seen: set[int] = set()
    for spec in reverse_forwards:
        try:
            port = int(str(spec).split(":", 1)[0])
        except (TypeError, ValueError):
            continue
        if port in seen:
            continue
        seen.add(port)
        ports.append(port)
    return ports


def relay_forwards_from_ssh_config(
    config: SSHConfig,
    reverse_forwards: Iterable[str],
    *,
    serving_probe_for_port: Callable[[int], Callable[[], Awaitable[bool]] | None]
    | None = None,
    host_port_resolver: Callable[[], int] | None = None,
) -> list[SupervisedRelayForward]:
    """Build dedicated credential-relay supervisors from persisted ``-R`` specs.

    ``host_port_resolver`` (when given) is passed to each supervisor so the
    host-side ``-R`` target follows a relay that rebinds a new port across a
    daemon restart, while the CodeSpace-listen port stays stable (#855).
    """
    relays: list[SupervisedRelayForward] = []
    for relay_port in relay_ports_from_reverse_forwards(reverse_forwards):
        serving_probe = (
            serving_probe_for_port(relay_port) if serving_probe_for_port else None
        )
        relays.append(
            SupervisedRelayForward(
                config,
                relay_port,
                serving_probe=serving_probe,
                host_port_resolver=host_port_resolver,
            )
        )
    return relays


def relay_forwards_from_endpoint(
    endpoint: dict[str, Any],
    *,
    serving_probe_for_port: Callable[[int], Callable[[], Awaitable[bool]] | None]
    | None = None,
    host_port_resolver: Callable[[], int] | None = None,
) -> list[SupervisedRelayForward]:
    """Build credential-relay supervisors from an endpoint descriptor."""
    return relay_forwards_from_ssh_config(
        ssh_config_from_endpoint(endpoint),
        list(endpoint.get("reverse_forwards") or []),
        serving_probe_for_port=serving_probe_for_port,
        host_port_resolver=host_port_resolver,
    )


def endpoint_serving_probe_factory(
    endpoint: dict[str, Any],
    *,
    fail_open: bool = True,
) -> Callable[[int], Callable[[], Awaitable[bool]]]:
    """Build a ``serving_probe_for_port`` factory for a persisted endpoint.

    Each probe execs a one-shot far-side TCP-accept check on the CodeSpace-side
    relay listen port over a **fresh** SSH connection (no live transport is
    available on the daemon-restart reconstruction path). A ``False`` result
    means the ``-R`` process is alive but the far side is not accepting -- e.g.
    a remote bind that **silently failed** because a stale listener from the
    pre-restart ``-R`` had not been released yet -- so the relay supervisor
    re-establishes until the rebind takes (dotfiles #855). Transport failures
    return ``True`` (a health hint, never a reason to churn a possibly-fine
    relay on a transient SSH failure). Set ``fail_open=False`` for a launch or
    resume readiness gate where an inconclusive probe must block ACP delivery.
    Mirrors
    ``spawner._serving_probe_for_port`` for the restart path.
    """
    config = ssh_config_from_endpoint(endpoint)

    def _for_port(relay_port: int) -> Callable[[], Awaitable[bool]]:
        probe = (
            f'timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/{relay_port}" '
            "&& echo OK"
        )
        argv = build_remote_exec_args(config, f"bash -lc {shlex.quote(probe)}")

        async def _probe() -> bool:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            except Exception:
                if fail_open:
                    return True
                raise
            return proc.returncode == 0 and b"OK" in (out or b"")

        return _probe

    return _for_port
