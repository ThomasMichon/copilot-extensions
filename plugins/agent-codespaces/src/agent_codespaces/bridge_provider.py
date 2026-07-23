"""Bridge provider -- register codespace agents with agent-bridge.

Converts active GitHub Codespaces into agent-bridge agent registrations.
Each codespace is exposed as a ``command``-type agent that uses
``agent-codespaces ssh --stdio`` for transport.

Usage:
    agent-codespaces bridge register   # push agents to bridge
    agent-codespaces bridge unregister # remove agents from bridge
    agent-codespaces bridge status     # show registration status
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ._invoke import module_argv
from .lifecycle import CodespaceInfo, list_codespaces

log = logging.getLogger("agent-codespaces")

DEFAULT_BRIDGE_URL = "http://127.0.0.1:9280"
DEFAULT_TTL = 300.0
PROVIDER_NAME = "codespaces"

# Where agent-bridge stores its auth token
_BRIDGE_AUTH_PATH = Path.home() / ".agent-bridge" / "auth.yaml"
# Legacy fallback
_BRIDGE_TOKEN_PATH = Path.home() / ".agent-bridge" / "auth_token"


def _load_bridge_token() -> str | None:
    """Load the agent-bridge auth token from its standard location.

    Reads from ``auth.yaml`` (current format) or falls back to
    ``auth_token`` (legacy plaintext file).
    """
    if _BRIDGE_AUTH_PATH.exists():
        import yaml

        data = yaml.safe_load(_BRIDGE_AUTH_PATH.read_text()) or {}
        token = data.get("token")
        if token:
            return str(token).strip()

    if _BRIDGE_TOKEN_PATH.exists():
        return _BRIDGE_TOKEN_PATH.read_text().strip()

    return None


def build_agent_configs(
    codespaces: list[CodespaceInfo] | None = None,
) -> list[dict[str, Any]]:
    """Convert active codespaces to agent-bridge provider agent configs.

    Includes Available and Shutdown codespaces.  Each agent's
    spawn_command uses ``effective_acp_command`` from ``codespaces.yaml``
    which resolves ``workspace_folder`` into a ``cd`` prefix.
    """
    from .config import load_merged_config

    if codespaces is None:
        codespaces = list_codespaces()

    config = load_merged_config()

    agents = []
    for cs in codespaces:
        # Include Available and Shutdown codespaces.  Shutdown ones can
        # be auto-started by gh during SSH connection establishment.
        if cs.state not in ("Available", "Shutdown"):
            log.debug(
                "Skipping codespace '%s' (state=%s)", cs.name, cs.state,
            )
            continue

        # Resolve the launch command per CodeSpace *repository* so each agent
        # lands in the right checkout. A CodeSpaces repo often differs from the
        # product checkout it hosts (e.g. example-web-codespaces ->
        # /workspaces/example-web); ``effective_acp_command_for`` applies the
        # per-repo workspace_folder / workspace_repo mapping (see config).
        acp_command = config.effective_acp_command_for(cs.repository)

        # Build the spawn command. Invoke the module directly
        # (python -m agent_codespaces), never the .cmd binstub, so
        # agent-bridge does not route the spawn through cmd.exe and mangle
        # %VAR% tokens in the --remote-cmd payload (see ._invoke).
        spawn_cmd = [
            *module_argv(),
            "ssh", "--stdio", cs.name,
            "--repo", cs.repository,
            "--remote-cmd", acp_command,
        ]

        # Sanitize name for agent-bridge (lowercase, alphanumeric + dash)
        agent_name = f"cs-{cs.name}".lower()
        agent_name = agent_name[:64]  # max length

        display = cs.display_name or cs.name
        repo_short = cs.repository.split("/")[-1] if cs.repository else ""
        description = f"GitHub Codespace: {cs.repository}"
        if cs.branch:
            description += f"@{cs.branch}"

        agents.append({
            "name": agent_name,
            "display_name": f"{display} ({repo_short})" if repo_short else display,
            "description": description,
            "icon": "codespace",
            "spawn_command": spawn_cmd,
            # Structured metadata for agent-bridge's Session-Host dispatch path
            # (#177): lets the daemon build the CodeSpaceSpawner directly instead
            # of parsing spawn_command. spawn_command stays for the legacy
            # front-owns-stdio path + back-compat.
            "codespace": {
                "name": cs.name,
                "repo": cs.repository,
                "acp_command": acp_command,
                "workspace_folder": config.workspace_folder_for(cs.repository),
            },
        })

    return agents


def register_with_bridge(
    bridge_url: str = DEFAULT_BRIDGE_URL,
    ttl: float = DEFAULT_TTL,
    codespaces: list[CodespaceInfo] | None = None,
) -> dict[str, Any]:
    """Push codespace agents to agent-bridge's provider API.

    Returns the response from agent-bridge.

    Raises:
        RuntimeError: If registration fails (bridge not reachable,
            auth failure, etc.)
    """
    token = _load_bridge_token()
    if not token:
        raise RuntimeError(
            f"Agent-bridge auth token not found at {_BRIDGE_TOKEN_PATH}. "
            "Is agent-bridge installed?"
        )

    agents = build_agent_configs(codespaces)

    payload = json.dumps({"agents": agents, "ttl": ttl}).encode()
    url = f"{bridge_url}/api/v1/providers/{PROVIDER_NAME}"

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Bridge registration failed (HTTP {exc.code}): {body}"
        ) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot reach agent-bridge at {bridge_url}: {exc.reason}"
        ) from None


def unregister_from_bridge(
    bridge_url: str = DEFAULT_BRIDGE_URL,
) -> dict[str, Any]:
    """Remove codespace agents from agent-bridge.

    Returns the response from agent-bridge.
    """
    token = _load_bridge_token()
    if not token:
        raise RuntimeError(
            f"Agent-bridge auth token not found at {_BRIDGE_TOKEN_PATH}"
        )

    url = f"{bridge_url}/api/v1/providers/{PROVIDER_NAME}"
    req = urllib.request.Request(
        url,
        method="DELETE",
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Idempotent: a 404 means the provider is already gone (e.g. the TTL
        # registration expired). Treat as success.
        if exc.code == 404:
            return {"status": "not_registered", "provider": PROVIDER_NAME}
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Bridge unregistration failed (HTTP {exc.code}): {body}"
        ) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot reach agent-bridge at {bridge_url}: {exc.reason}"
        ) from None


def get_bridge_status(
    bridge_url: str = DEFAULT_BRIDGE_URL,
) -> dict[str, Any] | None:
    """Query provider status from agent-bridge. Returns None on failure."""
    token = _load_bridge_token()
    if not token:
        return None

    url = f"{bridge_url}/api/v1/providers"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            providers = data.get("providers", [])
            for p in providers:
                if p.get("name") == PROVIDER_NAME:
                    return p
            return None
    except Exception:
        return None
