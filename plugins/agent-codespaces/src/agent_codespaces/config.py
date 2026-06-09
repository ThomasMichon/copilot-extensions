"""Configuration loading and validation for agent-codespaces.

All configuration lives in adopting repos in ``codespaces.yaml``. The
runtime directory (``~/.agent-codespaces/``) contains only the adoption
manifest (``adopted-repos.yaml``) -- a list of repo paths. On every
start/reload the service reads ``codespaces.yaml`` live from each
adopted repo and merges in memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("agent-codespaces")

# Canonical paths
RUNTIME_DIR = Path.home() / ".agent-codespaces"
ADOPTED_REPOS_FILE = RUNTIME_DIR / "adopted-repos.yaml"
SOCKET_DIR = RUNTIME_DIR / "sockets"
LOG_FILE = RUNTIME_DIR / "agent-codespaces.log"
CONFIG_FILENAME = "codespaces.yaml"


@dataclass
class CredentialSourceConfig:
    """Configuration for a single credential source type."""

    enabled: bool = False
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_resources: list[str] = field(default_factory=list)


@dataclass
class CredentialsConfig:
    """Credential relay configuration."""

    sources: dict[str, CredentialSourceConfig] = field(default_factory=dict)
    relay_port: int = 9847


@dataclass
class RepoConfig:
    """Per-target-repo CodeSpace settings."""

    workspace_repo: str | None = None
    machine_type: str | None = None
    location: str | None = None
    bootstrap_post_create: str | None = None


@dataclass
class CodespacesConfig:
    """Merged configuration from all adopted repos."""

    # Defaults for CodeSpace creation
    default_machine_type: str = "largePremiumLinux"
    default_location: str = "EastUs"
    dotfiles_repo: str | None = None
    ssh_user: str = "vscode"

    # Workspace folder on the CodeSpace.  When set, the remote agent
    # command ``cd``s into this directory before launching Copilot CLI,
    # ensuring a cold-started CodeSpace lands in the repo root even if
    # the workspace volume is still mounting when the SSH session
    # connects.  Typical value: ``/workspaces/odsp-web``.
    workspace_folder: str | None = None

    # Remote agent command -- what to run on the CodeSpace when
    # connecting via agent-bridge.  Built dynamically from
    # ``workspace_folder`` if not explicitly overridden.  Only set
    # this if you need a completely custom launch command.
    acp_command: str | None = None

    # Credential relay
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)

    # Per-target-repo settings
    repos: dict[str, RepoConfig] = field(default_factory=dict)

    # Source tracking
    source_paths: list[Path] = field(default_factory=list)

    @property
    def effective_acp_command(self) -> str:
        """Return the resolved remote agent command.

        Priority:
        1. Explicit ``acp_command`` if set.
        2. ``cd <workspace_folder> && copilot --acp --stdio`` when
           ``workspace_folder`` is configured.
        3. Bare ``copilot --acp --stdio`` as last-resort fallback.
        """
        if self.acp_command:
            return self.acp_command
        if self.workspace_folder:
            return f"cd {self.workspace_folder} && copilot --acp --stdio"
        return "copilot --acp --stdio"


@dataclass
class AdoptedRepo:
    """A repo registered in the adoption manifest."""

    path: Path
    adopted_at: str | None = None


def load_adopted_repos() -> list[AdoptedRepo]:
    """Load the adoption manifest from the runtime directory."""
    if not ADOPTED_REPOS_FILE.exists():
        return []

    with open(ADOPTED_REPOS_FILE) as f:
        data = yaml.safe_load(f) or {}

    repos = []
    for entry in data.get("repos", []):
        repos.append(AdoptedRepo(
            path=Path(entry["path"]),
            adopted_at=entry.get("adopted_at"),
        ))
    return repos


def save_adopted_repos(repos: list[AdoptedRepo]) -> None:
    """Write the adoption manifest to the runtime directory."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "repos": [
            {"path": str(r.path), "adopted_at": r.adopted_at}
            for r in repos
        ]
    }
    with open(ADOPTED_REPOS_FILE, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False)


def load_repo_config(repo_path: Path) -> dict[str, Any] | None:
    """Load codespaces.yaml from a single repo. Returns None if missing."""
    config_file = repo_path / CONFIG_FILENAME
    if not config_file.exists():
        log.warning("No %s found in %s", CONFIG_FILENAME, repo_path)
        return None

    with open(config_file) as f:
        return yaml.safe_load(f) or {}


def _parse_credential_source(raw: dict[str, Any]) -> CredentialSourceConfig:
    """Parse a credential source config block."""
    return CredentialSourceConfig(
        enabled=raw.get("enabled", False),
        allowed_hosts=raw.get("allowed_hosts", []),
        allowed_resources=raw.get("allowed_resources", []),
    )


def _parse_repo_config(raw: dict[str, Any]) -> RepoConfig:
    """Parse a per-target-repo config block."""
    bootstrap = raw.get("bootstrap", {})
    return RepoConfig(
        workspace_repo=raw.get("workspace_repo"),
        machine_type=raw.get("machine_type"),
        location=raw.get("location"),
        bootstrap_post_create=bootstrap.get("post_create"),
    )


def load_merged_config() -> CodespacesConfig:
    """Load and merge config from all adopted repos.

    Reads ``codespaces.yaml`` live from each adopted repo path.
    First repo's values win on conflicts (except credential sources
    which are unioned).
    """
    adopted = load_adopted_repos()
    if not adopted:
        return CodespacesConfig()

    merged = CodespacesConfig()
    defaults_set = False

    for entry in adopted:
        raw = load_repo_config(entry.path)
        if raw is None:
            continue

        merged.source_paths.append(entry.path)

        # Defaults (first wins)
        defaults = raw.get("defaults", {})
        if not defaults_set and defaults:
            merged.default_machine_type = defaults.get(
                "machine_type", merged.default_machine_type
            )
            merged.default_location = defaults.get(
                "location", merged.default_location
            )
            merged.dotfiles_repo = defaults.get(
                "dotfiles_repo", merged.dotfiles_repo
            )
            merged.ssh_user = defaults.get(
                "ssh_user", merged.ssh_user
            )
            merged.acp_command = defaults.get(
                "acp_command", merged.acp_command
            )
            merged.workspace_folder = defaults.get(
                "workspace_folder", merged.workspace_folder
            )
            defaults_set = True

        # Credentials (union sources across repos)
        creds_raw = raw.get("credentials", {})
        if creds_raw:
            merged.credentials.relay_port = creds_raw.get(
                "relay_port", merged.credentials.relay_port
            )
            for source_name, source_raw in creds_raw.get("sources", {}).items():
                if source_name not in merged.credentials.sources:
                    merged.credentials.sources[source_name] = _parse_credential_source(
                        source_raw
                    )
                else:
                    # Union allowed hosts
                    existing = merged.credentials.sources[source_name]
                    new_hosts = set(existing.allowed_hosts) | set(
                        source_raw.get("allowed_hosts", [])
                    )
                    existing.allowed_hosts = sorted(new_hosts)

        # Repos (first wins on conflicts)
        for repo_key, repo_raw in raw.get("repos", {}).items():
            if repo_key not in merged.repos:
                merged.repos[repo_key] = _parse_repo_config(repo_raw)

    return merged


def validate_config(config: CodespacesConfig) -> list[str]:
    """Validate a merged config. Returns a list of warnings/errors."""
    issues: list[str] = []

    if not config.source_paths:
        issues.append("No adopted repos with codespaces.yaml found")

    for source_name, source_cfg in config.credentials.sources.items():
        if source_cfg.enabled and not source_cfg.allowed_hosts:
            issues.append(
                f"Credential source '{source_name}' is enabled but has no allowed_hosts"
            )

    return issues
