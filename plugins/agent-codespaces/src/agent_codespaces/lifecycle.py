"""CodeSpace lifecycle management -- create, delete, list, status.

Wraps ``gh codespace`` commands with configuration from codespaces.yaml.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass

from .config import CodespacesConfig, RepoConfig

log = logging.getLogger("agent-codespaces")


@dataclass
class CodespaceInfo:
    """Summary of a CodeSpace from ``gh codespace list``."""

    name: str
    display_name: str
    repository: str
    branch: str
    state: str
    machine: str


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def list_codespaces() -> list[CodespaceInfo]:
    """List active CodeSpaces via ``gh codespace list``."""
    args = [
        "gh", "codespace", "list",
        "--json", "name,displayName,repository,gitStatus,state,machine",
        "--limit", "50",
    ]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30,
            creationflags=_creation_flags(),
        )
    except FileNotFoundError:
        raise RuntimeError("gh CLI not found") from None

    if result.returncode != 0:
        raise RuntimeError(f"gh codespace list failed: {result.stderr.strip()}")

    entries = json.loads(result.stdout) if result.stdout.strip() else []
    codespaces = []
    for e in entries:
        git_status = e.get("gitStatus", {})
        codespaces.append(CodespaceInfo(
            name=e.get("name", ""),
            display_name=e.get("displayName", ""),
            repository=e.get("repository", ""),
            branch=git_status.get("ref", "") if isinstance(git_status, dict) else "",
            state=e.get("state", ""),
            machine=e.get("machine", ""),
        ))
    return codespaces


def create_codespace(
    repo: str,
    config: CodespacesConfig,
    branch: str | None = None,
) -> CodespaceInfo:
    """Create a CodeSpace for the given repo using config defaults."""
    repo_config = config.repos.get(repo, RepoConfig())
    machine_type = repo_config.machine_type or config.default_machine_type
    location = repo_config.location or config.default_location

    args = [
        "gh", "codespace", "create",
        "--repo", repo,
        "--machine", machine_type,
        "--location", location,
    ]
    if branch:
        args.extend(["--branch", branch])
    if config.dotfiles_repo:
        args.extend(["--dotfiles", config.dotfiles_repo])

    log.info("Creating codespace: %s", " ".join(args))

    result = subprocess.run(
        args, capture_output=True, text=True, timeout=300,
        creationflags=_creation_flags(),
    )

    if result.returncode != 0:
        raise RuntimeError(f"gh codespace create failed: {result.stderr.strip()}")

    # gh codespace create prints the name on stdout
    name = result.stdout.strip()
    return CodespaceInfo(
        name=name,
        display_name=name,
        repository=repo,
        branch=branch or "",
        state="Available",
        machine=machine_type,
    )


def delete_codespace(name: str, force: bool = False) -> None:
    """Delete a CodeSpace by name."""
    args = ["gh", "codespace", "delete", "-c", name]
    if force:
        args.append("--force")

    log.info("Deleting codespace: %s", name)

    result = subprocess.run(
        args, capture_output=True, text=True, timeout=60,
        creationflags=_creation_flags(),
    )

    if result.returncode != 0:
        raise RuntimeError(f"gh codespace delete failed: {result.stderr.strip()}")
