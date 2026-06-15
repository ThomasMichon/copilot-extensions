"""Configuration for agent-containers.

Runtime state lives under ``~/.agent-containers/`` (lease file, log).
Fleet/agent settings are read from a ``containers.yaml`` file, looked up
(in order) from:

1. ``$AGENT_CONTAINERS_CONFIG`` if set,
2. ``./containers.yaml`` in the current working directory,
3. ``~/.agent-containers/containers.yaml``.

A missing config is fine -- built-in defaults target the odsp-web local
Docker dev container (user ``vscode``, workspace ``/workspaces/odsp-web``).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("agent-containers")

# Canonical runtime paths
RUNTIME_DIR = Path.home() / ".agent-containers"
LEASE_FILE = RUNTIME_DIR / "leases.json"
LOG_FILE = RUNTIME_DIR / "agent-containers.log"
CONFIG_FILENAME = "containers.yaml"

# Label written on fleet containers at `up` time so discovery can find
# containers that were not created by the VS Code devcontainer flow (which
# would otherwise carry `devcontainer.local_folder`).
FLEET_LABEL = "agent-containers.fleet"

# Default ACP launch command run inside the container. Mirrors the codespaces
# resolver. ``--allow-all-tools`` is required for headless dispatch.
DEFAULT_ACP_COMMAND = "copilot --acp --stdio --allow-all-tools"


@dataclass
class FleetConfig:
    """A named pool of dev containers built from one devcontainer spec.

    Keyed by fleet name (e.g. ``odsp-web``). Containers are named
    ``<name_prefix>-<n>`` (e.g. ``odsp-web-1``).
    """

    repo: str = ""
    # Path to the devcontainer project (dir containing .devcontainer/) used
    # to build/create containers for this fleet. Resolved on the host.
    devcontainer_path: str | None = None
    # Container image to `docker run` when not using the devcontainer CLI.
    image: str | None = None
    size: int = 1
    name_prefix: str | None = None
    workspace_folder: str | None = None
    exec_user: str | None = None
    acp_command: str | None = None
    # "clone" (Model A, default) or "mount" (Model B, future).
    code_model: str = "clone"

    def prefix(self, fleet_name: str) -> str:
        return self.name_prefix or fleet_name


@dataclass
class ContainersConfig:
    """Top-level agent-containers configuration."""

    # Global defaults (overridable per-fleet)
    exec_user: str = "vscode"
    workspace_folder: str = "/workspaces/odsp-web"
    acp_command: str | None = None
    # Forward the host `gh auth token` into the container as GH_TOKEN so the
    # in-container Copilot CLI is authenticated headlessly.
    forward_gh_token: bool = True
    # On-demand credential relay: deploy in-container shims at connect that fetch
    # tokens from the host relay (over host.docker.internal). Fixes rush
    # dev-deploy (Azure storage) by serving the host az-login identity.
    relay_enabled: bool = True
    relay_host: str = "host.docker.internal"
    relay_port: int = 9857
    # Also deploy ado-auth-helper (ADO PAT / git credential relay). Off by
    # default to avoid disturbing already-working in-container ADO auth.
    relay_deploy_ado: bool = False
    relay_azure_resources: list[str] = field(
        default_factory=lambda: ["https://storage.azure.com/"]
    )
    # Image-name prefixes used as a discovery fallback when a container lacks
    # the devcontainer.local_folder / FLEET_LABEL labels.
    image_prefixes: list[str] = field(
        default_factory=lambda: ["vsc-odsp-web-codespaces-"]
    )
    fleets: dict[str, FleetConfig] = field(default_factory=dict)

    def effective_acp_command(
        self, workspace_folder: str | None = None, acp_command: str | None = None
    ) -> str:
        """Resolve the ACP launch command for a container.

        Priority: explicit ``acp_command`` arg > fleet/global ``acp_command``
        > ``cd <workspace_folder> && <DEFAULT_ACP_COMMAND>``.
        """
        cmd = acp_command or self.acp_command
        if cmd:
            return cmd
        ws = workspace_folder or self.workspace_folder
        if ws:
            return f"cd {ws} && {DEFAULT_ACP_COMMAND}"
        return DEFAULT_ACP_COMMAND


def _config_path() -> Path | None:
    """Locate the containers.yaml config file, or None if not found."""
    env = os.environ.get("AGENT_CONTAINERS_CONFIG")
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None
    cwd = Path.cwd() / CONFIG_FILENAME
    if cwd.exists():
        return cwd
    runtime = RUNTIME_DIR / CONFIG_FILENAME
    if runtime.exists():
        return runtime
    return None


def load_config() -> ContainersConfig:
    """Load configuration from containers.yaml, merged over defaults."""
    config = ContainersConfig()
    path = _config_path()
    if not path:
        log.debug("No containers.yaml found; using built-in defaults")
        return config

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("Failed to read %s: %s", path, exc)
        return config

    config.exec_user = data.get("exec_user", config.exec_user)
    config.workspace_folder = data.get("workspace_folder", config.workspace_folder)
    config.acp_command = data.get("acp_command", config.acp_command)
    config.forward_gh_token = bool(
        data.get("forward_gh_token", config.forward_gh_token)
    )
    relay = data.get("relay", {}) or {}
    if isinstance(relay, dict):
        config.relay_enabled = bool(relay.get("enabled", config.relay_enabled))
        config.relay_host = relay.get("host", config.relay_host)
        config.relay_port = int(relay.get("port", config.relay_port))
        config.relay_deploy_ado = bool(relay.get("deploy_ado", config.relay_deploy_ado))
        if isinstance(relay.get("azure_resources"), list):
            config.relay_azure_resources = [str(r) for r in relay["azure_resources"]]
    if "image_prefixes" in data and isinstance(data["image_prefixes"], list):
        config.image_prefixes = [str(p) for p in data["image_prefixes"]]

    fleets = data.get("fleets", {}) or {}
    for name, raw in fleets.items():
        raw = raw or {}
        config.fleets[name] = FleetConfig(
            repo=raw.get("repo", ""),
            devcontainer_path=raw.get("devcontainer_path"),
            image=raw.get("image"),
            size=int(raw.get("size", 1)),
            name_prefix=raw.get("name_prefix"),
            workspace_folder=raw.get("workspace_folder"),
            exec_user=raw.get("exec_user"),
            acp_command=raw.get("acp_command"),
            code_model=raw.get("code_model", "clone"),
        )

    log.debug("Loaded containers.yaml from %s (%d fleets)", path, len(config.fleets))
    return config


def ensure_runtime_dir() -> None:
    """Create the runtime directory if it does not exist."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
