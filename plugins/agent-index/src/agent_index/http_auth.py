"""Explicit, endpoint-bound credentials for remote read clients."""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent_procutil.owned_process import run_caller_owned

from . import config
from .client import AgentIndexClient


class HttpAuthError(ValueError):
    pass


def configured() -> bool:
    return config._load_yaml(config.config_path()).get("http_auth") is not None


def _credential(command: list[str]) -> str:
    try:
        result = run_caller_owned(
            command, timeout=30, max_stdout_bytes=16386, max_stderr_bytes=4096,
        )
        if result.returncode:
            raise HttpAuthError("http-auth-command-failed")
        value = result.stdout.strip()
        value.encode("ascii")
        if not 1 <= len(value) <= 16384 or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value):
            raise HttpAuthError("http-auth-credential-invalid")
        return value
    except (OSError, UnicodeError, RuntimeError, subprocess.SubprocessError):
        raise HttpAuthError("http-auth-command-failed") from None


def read_client(
    base_url: str | None, *, client_factory: type[AgentIndexClient] = AgentIndexClient,
) -> AgentIndexClient:
    """Resolve only machine-local credential policy; never acquire a login."""
    if not isinstance(base_url, str) or not base_url:
        raise HttpAuthError("configured client endpoint is unavailable")
    raw = config._load_yaml(config.config_path()).get("http_auth")
    if raw is None:
        return client_factory(base_url)
    if (
        not isinstance(raw, dict)
        or set(raw) != {"endpoint", "token_command", "installation_id"}
        or not isinstance(raw["endpoint"], str)
        or raw["endpoint"].rstrip("/") != base_url.rstrip("/")
    ):
        raise HttpAuthError("http-auth-endpoint-mismatch-or-invalid-policy")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError:
        raise HttpAuthError("http-auth-endpoint-invalid") from None
    try:
        loopback = parsed.hostname == "localhost" or ipaddress.ip_address(
            parsed.hostname or ""
        ).is_loopback
    except ValueError:
        loopback = False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
        or any(ord(char) < 33 for char in base_url)
        or (parsed.scheme == "http" and not loopback)
    ):
        raise HttpAuthError("http-auth-requires-https-or-loopback")
    command = raw["token_command"]
    installation = raw["installation_id"]
    if (
        not isinstance(command, list)
        or not 1 <= len(command) <= 32
        or any(
            not isinstance(arg, str)
            or not arg
            or len(arg) > 4096
            or any(ord(char) < 32 for char in arg)
            for arg in command
        )
        or not Path(command[0]).is_absolute()
        or not isinstance(installation, str)
        or not 1 <= len(installation) <= 256
        or any(not 32 < ord(char) < 127 for char in installation)
    ):
        raise HttpAuthError("http-auth-policy-invalid")
    scheme = "Bear" + "er "
    headers = {"Authorization": scheme + _credential(command)}
    return client_factory(
        base_url, installation_id=installation, read_headers=headers
    )


def status_payload(base_url: str, role: str) -> dict[str, Any] | None:
    if role != "client" or not configured():
        return None
    remote = read_client(base_url)
    if remote.read_headers is None:
        raise HttpAuthError("http-auth-policy-changed")
    health = remote.health()
    payload = remote.status()
    if (
        health.get("installationId") != remote.installation_id
        or payload.get("installationId") != remote.installation_id
        or not isinstance(payload.get("index"), dict)
    ):
        raise HttpAuthError("remote read installation identity mismatch")
    ready = (
        health.get("status") == "ok"
        and payload.get("draining") is not True
        and payload.get("promoted") is not False
    )
    payload.update({
        "schema": "agent-index.lifecycle",
        "schema_version": 1,
        "state": "ready" if ready else "not_ready",
        "setup_required": False,
        "configured": True,
        "role": role,
        "runtime": {"state": "ready"},
        "running": ready,
        "endpoint": base_url,
    })
    return payload
