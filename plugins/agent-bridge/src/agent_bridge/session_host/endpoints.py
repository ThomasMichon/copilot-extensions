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

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from ssh_manager import LocalForward, SSHConfig, SupervisedRelayForward


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
