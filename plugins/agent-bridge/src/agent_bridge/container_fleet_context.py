"""Scope container provider calls to the requesting project's local checkout."""

from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
import re

from .session_start_models import StartSessionRequest


_repository: ContextVar[str | None] = ContextVar("container_fleet_repository", default=None)


def is_container_target(agent: str) -> bool:
    return agent.rsplit("@", 1)[-1].startswith("container:")


def provider_arguments(namespace: str, argv: list[str]) -> list[str]:
    root = _repository.get()
    if namespace == "container" and root and argv[0] in {
        "namespace-resolve", "namespace-target-repo", "namespace-ensure-ready",
    }:
        return ["--fleet-repository", root, *argv]
    return argv


@asynccontextmanager
async def container_fleet_context(agent: str, request: StartSessionRequest):
    root = None
    if is_container_target(agent):
        from .caller_project import _OWNER_REF
        from .venue_plugins import resolve_controller_cwd
        from .acquisition_authority import worktree_command

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
                result = await worktree_command(["repos", "find", project, "--registered-only", "--json"], None)
                path = result.get("path")
                if (result.get("registration") != "ready" or result.get("name") != project
                        or not isinstance(path, str) or not Path(path).is_absolute()):
                    raise RuntimeError("Cannot establish the container fleet repository")
                root = path
    token = _repository.set(root)
    try:
        yield
    finally:
        _repository.reset(token)
