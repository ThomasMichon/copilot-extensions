"""Resolve Windows jump chains before any transport process is started."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
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


def _expand_hostname(value: str, alias: str) -> str:
    def expand(match: re.Match[str]) -> str:
        token = match.group(1)
        if token not in {"h", "%"}:
            raise ValueError("Unsupported SSH HostName token")
        return alias if token == "h" else "%"

    return re.sub(r"%(.)|%$", expand, value)


def prepare_jump_arguments(
    args: Sequence[str], config: SSHConfig,
) -> tuple[tuple[str, ...], SSHConfig]:
    """Consume native jump options while preserving their route and remote argv."""
    prepared = [args[0]]
    jump: str | None = None
    overrides: dict[str, str] = {}
    destination_seen = False
    index = 1
    while index < len(args):
        argument = args[index]
        if argument == "--":
            prepared.extend(args[index:])
            break
        if not argument.startswith("-") or argument == "-":
            if destination_seen:
                prepared.extend(args[index:])
                break
            prepared.append(argument)
            destination_seen = True
            index += 1
            continue
        for offset, option in enumerate(argument[1:], 1):
            if option not in "BbcDEeFIiJLlmOopQRSWw":
                continue
            attached = argument[offset + 1:]
            if attached:
                value = attached
            else:
                index += 1
                if index == len(args):
                    raise ValueError(f"SSH option -{option} requires a value")
                value = args[index]
            consumed = option == "J"
            key, setting = {"p": "port", "l": "user"}.get(option, ""), value
            if option == "o":
                key, separator, setting = value.partition("=")
                if not separator:
                    fields = value.split(None, 1)
                    if not fields:
                        raise ValueError("SSH option -o requires a setting")
                    key, setting = fields[0], fields[1] if len(fields) == 2 else ""
                if key.strip().casefold() == "proxyjump":
                    consumed, value = True, setting.strip()
            normalized = key.strip().casefold()
            if normalized in {"hostname", "port", "user"}:
                overrides.setdefault(normalized, setting.strip())
            if consumed:
                if not value:
                    raise ValueError("SSH jump option requires a value")
                if option == "J" and jump is not None:
                    raise ValueError("Duplicate SSH jump option")
                if jump is None:
                    jump = value
                if offset > 1:
                    prepared.append(argument[:offset])
            else:
                prepared.append(argument)
                if not attached:
                    prepared.append(value)
            break
        else:
            prepared.append(argument)
        index += 1
    if overrides:
        port = int(overrides["port"]) if "port" in overrides else config.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Invalid SSH destination port")
        if any(not value for value in overrides.values()):
            raise ValueError("SSH destination override requires a value")
        config = replace(
            config,
            hostname=(
                _expand_hostname(overrides["hostname"], config.host_alias)
                if "hostname" in overrides else config.hostname
            ),
            user=overrides.get("user", config.user),
            port=port,
            extra_options={
                **{key: value for key, value in config.extra_options.items()
                   if key.casefold() not in overrides},
                **overrides,
            },
            effective_config=(
                *[(key, value) for key, value in config.effective_config
                  if key.casefold() not in overrides],
                *overrides.items(),
            ),
        )
    return tuple(prepared), _override_jump(config, jump) if jump is not None else config


async def resolve_jump_route(
    config: SSHConfig, executable: str,
    *, cwd: str | None = None, environment: Mapping[str, str] | None = None,
    seen: tuple[tuple[str, str, int], ...] = (),
) -> JumpRoute | None:
    """Freeze each hop's profile and reject loops before creating a broker."""
    endpoint = _endpoint(config)
    identity = (config.host_alias.casefold(), endpoint[1], endpoint[2])
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
