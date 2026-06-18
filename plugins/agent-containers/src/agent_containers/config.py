"""Configuration for agent-containers.

Runtime state lives under ``~/.agent-containers/`` (lease file, log).
Fleet/agent settings are read from a ``containers.yaml`` file, looked up
(in order) from:

1. ``$AGENT_CONTAINERS_CONFIG`` if set,
2. ``./containers.yaml`` in the current working directory,
3. ``~/.agent-containers/containers.yaml``.

A missing config is fine -- built-in defaults target a generic VS Code dev
container (user ``vscode``, workspace ``/workspace``). Point them at a real
repo by writing a ``containers.yaml`` (see the README / containers-fleet skill).
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
class DotfilesConfig:
    """A designated host *dotfiles* repo to reproduce in a fleet container.

    Mirrors the GitHub Codespaces dotfiles flow (clone the repo, run its
    ``install.sh``, symlink ``.*`` files into ``$HOME``) for a local Docker
    container. After the container is created the host repo is copied in with
    ``docker cp`` to ``target`` (owned by the remote user), then
    ``install_command`` is run in ``target``.

    Copying (rather than bind-mounting) keeps the host repo pristine -- it is
    only read, never mounted, so ``install.sh`` writes git config / symlinks
    against the container-local copy. Optional + per-user: a missing ``repo``
    disables the whole step.
    """

    # Host path to the dotfiles repo to reproduce (absolute or ~-expanded).
    repo: str | None = None
    # Container path the repo is materialised at (matches the Codespaces layout
    # so dotfiles that hard-code it keep working).
    target: str = "/workspaces/.codespaces/.persistedshare/dotfiles"
    # Command run in ``target`` after the copy (login shell, as the remote
    # user). Empty / null skips the install step (mount + copy still happen).
    install_command: str | None = "bash install.sh"

    def host_repo(self) -> Path | None:
        return Path(self.repo).expanduser() if self.repo else None


@dataclass
class FleetConfig:
    """A named pool of dev containers built from one devcontainer spec.

    Keyed by fleet name (e.g. ``myrepo``). Containers are named
    ``<name_prefix>-<n>`` (e.g. ``myrepo-1``).
    """

    repo: str = ""
    # Path to the devcontainer project (dir containing .devcontainer/) used
    # to build/create containers for this fleet. Resolved on the host.
    devcontainer_path: str | None = None
    # Path to a specific devcontainer.json, passed to the devcontainer CLI as
    # ``--config``. Needed when the spec is NOT at the default
    # ``<devcontainer_path>/.devcontainer/devcontainer.json`` -- e.g. a nested
    # ``.devcontainer/docker/devcontainer.json``. Resolved relative to
    # ``devcontainer_path`` when not absolute.
    devcontainer_config: str | None = None
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

    def resolved_config(self) -> str | None:
        """Absolute path to the devcontainer.json for ``--config``, or None.

        Relative ``devcontainer_config`` is resolved against
        ``devcontainer_path``; an absolute value is returned as-is.
        """
        if not self.devcontainer_config:
            return None
        p = Path(self.devcontainer_config).expanduser()
        if not p.is_absolute() and self.devcontainer_path:
            p = Path(self.devcontainer_path).expanduser() / p
        return str(p)


@dataclass
class ContainersConfig:
    """Top-level agent-containers configuration."""

    # Global defaults (overridable per-fleet)
    exec_user: str = "vscode"
    workspace_folder: str = "/workspace"
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
    # Azure scopes the relay may mint tokens for. "*" = any scope (gated behind
    # the per-container relay token; mirrors agent-codespaces). Faithfully serves
    # whatever scope the official `azure-auth-helper get-access-token "<scope>"`
    # broker requests -- storage.azure.com/.default, account-specific blob
    # scopes, etc. -- so the in-container consumer (rush build cache, dev-deploy
    # user-delegation SAS) gets a scope-matching AAD token.
    relay_azure_resources: list[str] = field(
        default_factory=lambda: ["*"]
    )
    # Image-name prefixes used as a discovery fallback when a container lacks
    # the devcontainer.local_folder / FLEET_LABEL labels. The default ``vsc-``
    # matches any VS Code devcontainer image; narrow it per machine if needed.
    image_prefixes: list[str] = field(
        default_factory=lambda: ["vsc-"]
    )
    # Optional designated dotfiles repo reproduced inside fleet containers
    # (Codespaces-style clone + install.sh). None disables the step.
    dotfiles: DotfilesConfig | None = None
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

    dotfiles = data.get("dotfiles", None)
    if isinstance(dotfiles, dict) and dotfiles.get("repo"):
        df = DotfilesConfig(repo=str(dotfiles["repo"]))
        if dotfiles.get("target"):
            df.target = str(dotfiles["target"])
        if "install_command" in dotfiles:
            ic = dotfiles["install_command"]
            df.install_command = str(ic) if ic else None
        config.dotfiles = df

    fleets = data.get("fleets", {}) or {}
    for name, raw in fleets.items():
        raw = raw or {}
        config.fleets[name] = FleetConfig(
            repo=raw.get("repo", ""),
            devcontainer_path=raw.get("devcontainer_path"),
            devcontainer_config=raw.get("devcontainer_config"),
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
