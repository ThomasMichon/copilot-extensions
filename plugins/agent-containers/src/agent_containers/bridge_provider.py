"""Bridge provider -- push fleet containers to agent-bridge as provider agents.

This is the optional *push* model (parallel to agent-codespaces). The primary
mechanism is the on-demand ``container:`` namespace resolver in ``resolver.py``.
Both paths spawn the same ``agent-containers exec --stdio <name>`` transport
wrapper, which fetches the host ``gh`` token at spawn time -- so the token is
never embedded in a registration payload, a SpawnTarget, or a log.

Usage:
    agent-containers bridge register
    agent-containers bridge unregister
    agent-containers bridge status
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import load_config
from .lifecycle import list_containers
from .resolver import build_wrapper_command

log = logging.getLogger("agent-containers")

# Fallback only. Post-#694 the daemon binds an OS-assigned ephemeral port (never
# :9280) and advertises it in ~/.agent-bridge/active.json; _resolve_bridge_url()
# reads that table so a redeploy that flipped the port still reaches the live
# daemon. Registering against this hardcoded default fails after any restart
# (dotfiles #826) -- keep it strictly as the no-routing-table fallback.
DEFAULT_BRIDGE_URL = "http://127.0.0.1:9280"
DEFAULT_TTL = 300.0
PROVIDER_NAME = "containers"


def _bridge_config_dir() -> Path:
    return Path(
        os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")
    ).expanduser()


_BRIDGE_AUTH_PATH = _bridge_config_dir() / "auth.yaml"
_BRIDGE_TOKEN_PATH = _bridge_config_dir() / "auth_token"


def _resolve_bridge_url() -> str:
    """Resolve the live agent-bridge daemon URL.

    Precedence mirrors the agent-bridge CLI client (client.py): an explicit
    ``AGENT_BRIDGE_BASE_URL`` env wins (deploy orchestrator dials a specific
    daemon), then the routing table (``active.json``) written by the running
    daemon, then the static :data:`DEFAULT_BRIDGE_URL` fallback. The table is an
    optimization, never a hard dependency -- any read/parse error falls through
    to the default.
    """
    explicit = os.environ.get("AGENT_BRIDGE_BASE_URL")
    if explicit:
        return explicit.rstrip("/")

    active_path = _bridge_config_dir() / "active.json"
    try:
        data = json.loads(active_path.read_text())
        active = data.get("active") or {}
        port = int(active.get("port") or 0)
        if port > 0:
            bind = active.get("bind") or "127.0.0.1"
            if bind in ("0.0.0.0", "", None):  # noqa: S104 -- normalize probe target, not a bind
                bind = "127.0.0.1"
            elif bind == "::":
                bind = "::1"
            return f"http://{bind}:{port}"
    except Exception:  # noqa: S110 -- routing table is best-effort; fall back to default
        pass
    return DEFAULT_BRIDGE_URL


def _load_bridge_token() -> str | None:
    if _BRIDGE_AUTH_PATH.exists():
        import yaml

        data = yaml.safe_load(_BRIDGE_AUTH_PATH.read_text()) or {}
        token = data.get("token")
        if token:
            return str(token).strip()
    if _BRIDGE_TOKEN_PATH.exists():
        return _BRIDGE_TOKEN_PATH.read_text().strip()
    return None


def build_agent_configs() -> list[dict[str, Any]]:
    """Convert fleet containers to agent-bridge provider agent configs."""
    config = load_config()
    agents = []
    for c in list_containers(config):
        spawn_cmd = build_wrapper_command(c.name)
        agent_name = f"ctr-{c.name}".lower()[:64]
        repo = c.repo or (c.fleet or "")
        agents.append({
            "name": agent_name,
            "display_name": f"{c.name} ({repo})" if repo else c.name,
            "description": f"Local dev container: {c.image}",
            "icon": "container",
            "spawn_command": spawn_cmd,
        })
    return agents


def register_with_bridge(
    bridge_url: str | None = None, ttl: float = DEFAULT_TTL
) -> dict[str, Any]:
    """Push container agents to agent-bridge's provider API."""
    bridge_url = bridge_url or _resolve_bridge_url()
    token = _load_bridge_token()
    if not token:
        raise RuntimeError(
            f"Agent-bridge auth token not found at {_BRIDGE_AUTH_PATH}. "
            "Is agent-bridge installed?"
        )
    agents = build_agent_configs()
    payload = json.dumps({"agents": agents, "ttl": ttl}).encode()
    url = f"{bridge_url}/api/v1/providers/{PROVIDER_NAME}"
    req = urllib.request.Request(
        url, data=payload, method="POST",
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
        raise RuntimeError(f"Bridge registration failed (HTTP {exc.code}): {body}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach agent-bridge at {bridge_url}: {exc.reason}") from None


def unregister_from_bridge(bridge_url: str | None = None) -> dict[str, Any]:
    """Remove container agents from agent-bridge."""
    bridge_url = bridge_url or _resolve_bridge_url()
    token = _load_bridge_token()
    if not token:
        raise RuntimeError(f"Agent-bridge auth token not found at {_BRIDGE_AUTH_PATH}")
    url = f"{bridge_url}/api/v1/providers/{PROVIDER_NAME}"
    req = urllib.request.Request(
        url, method="DELETE", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"status": "not_registered", "provider": PROVIDER_NAME}
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Bridge unregistration failed (HTTP {exc.code}): {body}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach agent-bridge at {bridge_url}: {exc.reason}") from None


def get_bridge_status(bridge_url: str | None = None) -> dict[str, Any] | None:
    """Query provider status from agent-bridge. Returns None on failure."""
    bridge_url = bridge_url or _resolve_bridge_url()
    token = _load_bridge_token()
    if not token:
        return None
    url = f"{bridge_url}/api/v1/providers"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            for p in data.get("providers", []):
                if p.get("name") == PROVIDER_NAME:
                    return p
            return None
    except Exception:
        return None
