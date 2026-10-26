"""Resolve Windows jump chains before any transport process is started."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from urllib.parse import unquote, urlsplit

from .config_sources import SSHConfig, SSHProfileSource, ssh_option

_MAX_JUMPS = 8


@dataclass(frozen=True)
class JumpRoute:
    argv: tuple[str, ...]
    config: SSHConfig
    upstream: JumpRoute | None


def _endpoint(config: SSHConfig) -> tuple[str, str, int]:
    host = config.hostname or ssh_option(config, "hostname") or config.host_alias
    user = config.user or ssh_option(config, "user") or ""
    port = config.port or int(ssh_option(config, "port") or 22)
    if not host or host.startswith("-") or not 1 <= port <= 65535:
        raise ValueError("Invalid SSH jump endpoint")
    return host, user, port


def _parse_jump(value: str) -> tuple[str, str | None, int | None]:
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Invalid SSH jump specification")
    uri = value.startswith("ssh://")
    parsed = urlsplit(value if uri else "ssh://" + value)
    if (
        parsed.scheme != "ssh" or not parsed.hostname or parsed.hostname.startswith("-")
        or parsed.username == ""
        or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
    ):
        raise ValueError("Invalid SSH jump specification")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid SSH jump port")
    authority = parsed.netloc.rsplit("@", 1)[-1]
    host = (
        authority[1:authority.index("]")]
        if authority.startswith("[") else authority.split(":")[0]
    )
    user = parsed.username
    if uri:
        host = unquote(host, errors="strict")
        user = unquote(user, errors="strict") if user is not None else None
    if not host or host.startswith("-") or any(
        ord(char) < 32 or ord(char) == 127 for char in host + (user or "")
    ):
        raise ValueError("Invalid SSH jump identity")
    return host, user, port


def _override_jump(config: SSHConfig, value: str) -> SSHConfig:
    options = {
        key: item for key, item in config.extra_options.items()
        if key.casefold() not in {"proxycommand", "proxyjump"}
    }
    options["ProxyJump"] = value
    effective = tuple(
        (key, item) for key, item in config.effective_config
        if key.casefold() not in {"proxycommand", "proxyjump"}
    )
    return replace(
        config, proxy_command=None, extra_options=options, effective_config=effective,
    )


async def resolve_jump_route(
    config: SSHConfig, executable: str,
    *, cwd: str | None = None, environment: Mapping[str, str] | None = None,
    seen: tuple[tuple[str, str, int], ...] = (),
) -> JumpRoute | None:
    """Freeze each hop's profile and reject loops before creating a broker."""
    endpoint = _endpoint(config)
    identity = (endpoint[0].casefold(), endpoint[1], endpoint[2])
    if identity in seen:
        raise ValueError("Cyclic SSH jump chain")
    proxy = config.proxy_command or ssh_option(config, "proxycommand")
    if proxy:
        return None
    value = ssh_option(config, "proxyjump")
    if not value or value.casefold() == "none":
        return None
    if len(seen) >= _MAX_JUMPS:
        raise ValueError("Over-budget SSH jump chain")
    jumps = value.split(",")
    if len(jumps) > _MAX_JUMPS or any(not item for item in jumps):
        raise ValueError("Invalid or over-budget SSH jump chain")
    host, user, port = _parse_jump(jumps[-1])
    source = SSHProfileSource(
        host, user=user, port=port, config_file=config.config_file,
        resolve_timeout=15, ssh_executable=executable, resolve_cwd=cwd,
        process_env=environment,
    )
    child = await asyncio.to_thread(source.get_ssh_config)
    if len(jumps) > 1:
        child = _override_jump(child, ",".join(jumps[:-1]))
    upstream = await resolve_jump_route(
        child, executable, cwd=cwd, environment=environment, seen=(*seen, identity),
    )
    target_host, _, target_port = endpoint
    if ":" in target_host and not target_host.startswith("["):
        target_host = "[" + target_host + "]"
    args = [executable]
    if child.config_file:
        args.extend(["-F", child.config_file])
    if port is not None:
        args.extend(["-p", str(port)])
    if user is not None:
        args.extend(["-l", user])
    args.extend([
        "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
        "-o", "ClearAllForwardings=yes", "-W", f"{target_host}:{target_port}", host,
    ])
    return JumpRoute(tuple(args), child, upstream)
