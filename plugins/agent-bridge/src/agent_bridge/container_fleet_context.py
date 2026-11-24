"""Scope container provider calls to the requesting project's local checkout."""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from subprocess import SubprocessError

from .session_start_models import StartSessionRequest


_repository: ContextVar[str | None] = ContextVar("container_fleet_repository", default=None)
_qualified: ContextVar[tuple[str, ...] | None] = ContextVar("container_fleet_command", default=None)


def is_container_target(agent: str) -> bool:
    return agent.rsplit("@", 1)[-1].startswith("container:")


async def provider_arguments(command: list[str], namespace: str, argv: list[str]) -> list[str]:
    root = _repository.get()
    if namespace == "container" and root and argv[0] in {
        "namespace-resolve", "namespace-target-repo", "namespace-ensure-ready",
    }:
        from .provider_process import run_captured

        identity = tuple(command)
        if _qualified.get() != identity:
            _qualified.set(None)
            try:
                help_result = await asyncio.to_thread(run_captured, [*command, "--help"], timeout=8)
            except (OSError, SubprocessError) as exc:
                raise RuntimeError("Cannot verify fleet repository support from the selected Containers runtime") from exc
            if help_result.returncode or not re.search(r"(?m)^\s+--fleet-repository(?:[ =]|$)", help_result.stdout):
                raise RuntimeError("Update the selected Containers runtime before using a repository-scoped fleet")
            _qualified.set(identity)
        return ["--fleet-repository", root, *argv]
    return argv


def check_provider_arguments(arguments: list[str], returncode: int, stderr: str) -> None:
    # A stable shim can roll back behind the same argv. The scoped option
    # stays mandatory: argparse rejects it before dispatching any operation.
    if arguments[:1] == ["--fleet-repository"] and returncode == 2:
        _qualified.set(None)
        raise RuntimeError(stderr.strip() or "Containers exited with status 2")


@asynccontextmanager
async def container_fleet_context(agent: str, request: StartSessionRequest) -> AsyncIterator[None]:
    root = None
    if is_container_target(agent):
        from .caller_project import _OWNER_REF
        from .venue_plugins import resolve_controller_cwd
        from .provider_process import run_captured
        from .session_lifecycle_cli import _agent_worktrees_environment, _agent_worktrees_launch_prefix

        owner = _OWNER_REF.fullmatch(request.caller_owner_ref) if request.caller_owner_ref else None
        if request.caller_owner_ref and (not owner or owner["worktree"] != "@anchor"):
            root = await resolve_controller_cwd(request, caller_session_id=request.caller_session_id)
        else:
            project = owner["project"] if owner else request.sender_repo
            if project:
                # The receiving owner chooses the path. Neither caller affinity nor
                # the workspace inside the container identifies a host checkout.
                if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", project):
                    raise ValueError("Invalid container fleet project")
                prefix = _agent_worktrees_launch_prefix()
                if not prefix:
                    raise RuntimeError("Cannot establish the container fleet repository")
                result = await asyncio.to_thread(
                    run_captured, [*prefix, "--project", project, "get", "repo-dir"],
                    timeout=8, env=_agent_worktrees_environment(),
                )
                path = Path(result.stdout.strip())
                if result.returncode or not path.is_absolute() or not (path / ".git").exists():
                    raise RuntimeError("Cannot establish the container fleet repository")
                root = str(path)
    token = _repository.set(root)
    qualified = _qualified.set(None)
    try:
        yield
    finally:
        _qualified.reset(qualified)
        _repository.reset(token)
