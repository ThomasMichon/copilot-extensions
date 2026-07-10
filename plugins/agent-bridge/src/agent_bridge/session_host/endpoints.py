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

from typing import Any

from ssh_manager import LocalForward, SSHConfig


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
        reverse_forwards=list(endpoint.get("reverse_forwards") or []),
    )
