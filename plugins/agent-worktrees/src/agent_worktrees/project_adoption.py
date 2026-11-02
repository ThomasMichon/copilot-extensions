"""Project adoption admission and subsequent namespaced state preparation."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

from agent_procutil import no_window_kwargs
from repository_policy import AgentHostingError

from . import agent_policy, git_ops, output

if TYPE_CHECKING:
    from .repos import RepoEntry


def admit_cli(
    repo_dir: Path, *, operation: str, checkout: Path | None = None,
) -> bool:
    """Refuse adoption before callers prepare namespaced or installed state."""
    try:
        if checkout is not None:
            agent_policy.require(checkout, operation=operation)
        agent_policy.require(repo_dir, operation=operation)
    except AgentHostingError as exc:
        output.err(str(exc))
        return False
    return True


def _repository_registration(project: str, repo_dir: Path) -> tuple[RepoEntry | None, str]:
    from . import repos as repos_mod

    existing = repos_mod.read_registry(strict=True).repos.get(project)
    remote = ""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            **no_window_kwargs(),
            env=git_ops.repository_identity_env(),
        )
        if result.returncode == 0:
            remote = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if not remote and existing is not None:
        remote = existing.remote
    return existing, remote


def existing_project_state(project: str, repo_dir: Path) -> Path | None:
    """Resolve only already-owned target state before adoption writes."""
    from . import config, project_state

    if not project_state.namespaced():
        return config.project_dir(project)
    _, remote = _repository_registration(project, repo_dir)
    return project_state.existing_adoption_state(project, remote)


def prepare_namespaced_project_state(
    project: str,
    repo_dir: Path,
    platform_name: str,
) -> bool:
    """Publish repository identity before the first cell-local state lookup."""
    from . import project_state, repos as repos_mod

    if not project_state.namespaced():
        return True
    try:
        existing, remote = _repository_registration(project, repo_dir)
        repos_mod.add_repo(
            project,
            str(repo_dir),
            repo_class=(
                existing.repo_class
                if existing is not None and existing.repo_class != "reference"
                else "worktree"
            ),
            remote=remote,
            agent=existing.agent if existing is not None else True,
            plat=platform_name,
        )
        project_state.ensure_project_state(project)
    except ValueError as error:
        output.err(f"Could not establish repository identity for {project}: {error}")
        return False
    return True
