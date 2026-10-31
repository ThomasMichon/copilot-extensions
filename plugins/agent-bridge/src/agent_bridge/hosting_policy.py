"""Fresh repository hosting admission, separate from discovery and source access."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repository_policy import AgentHostingError, require_agent_hosting, validate_hosting_result

if TYPE_CHECKING:
    from ssh_manager import CommandResult

    from .agent_registry_namespace import NamespaceResolver
    from .agent_registry_resolver import AgentResolver
    from .spawn_target import SpawnTarget
    from .session_manager import Session, SessionManager


@dataclass(frozen=True)
class SessionHostingFence:
    """Read-only stop generation captured before asynchronous policy admission."""

    session: Session
    stop_notices: int
    stop_token: str | None
    recorded: bool

    @classmethod
    def capture(cls, manager: SessionManager, session: Session) -> SessionHostingFence:
        row = manager.db.get_session(session.session_id)
        return cls(
            session, session._stop_notices,
            row["cold_recovery_stop_token"] if row is not None else None,
            row is not None,
        )

    def changed(self, manager: SessionManager) -> bool:
        return (
            manager._sessions.get(self.session.session_id) is not self.session
            or self.session._stop_requested
            or self.capture(manager, self.session) != self
        )

    def require_current(self, manager: SessionManager) -> None:
        if self.changed(manager):
            from .cold_outages import ColdRecoveryStoppedError

            raise ColdRecoveryStoppedError("A stop superseded repository hosting admission")

    def after_authorized_resume(self, manager: SessionManager) -> SessionHostingFence:
        """Accept only consumption of this fence's pre-existing stop token."""
        row = manager.db.get_session(self.session.session_id)
        fence = (
            replace(self, stop_token=None)
            if self.stop_token is not None and row is not None
            and row["cold_recovery_stop_token"] is None else self
        )
        fence.require_current(manager)
        return fence


async def require_session_hosting(
    manager: SessionManager, session: Session, *, operation: str,
    fence: SessionHostingFence | None = None,
) -> SessionHostingFence:
    """Admit without absorbing a newer stop into the operation's authority."""
    fence = fence if fence is not None else SessionHostingFence.capture(manager, session)
    if session.target.type != "remote-acp":
        await require_target_hosting(session.target, operation=operation)
    fence.require_current(manager)
    return fence


def marketplace_id() -> str:
    """Use the receiving installation's scope, never request environment overrides."""
    from . import _peer_launch
    from .install_paths import uses_legacy_install_dir
    from .session_lifecycle_cli import (
        _agent_bridge_installation_context,
        _agent_bridge_owner_root,
    )

    root = _agent_bridge_owner_root()
    context = _agent_bridge_installation_context()
    if not context:
        if not uses_legacy_install_dir(root):
            raise AgentHostingError(
                "Cannot establish repository hosting policy: non-legacy installation "
                "context is unavailable"
            )
        return ""
    try:
        own = _peer_launch.validate_owner("agent-bridge", root, context)
    except (OSError, ValueError, ImportError, _peer_launch.ContextRefused) as exc:
        raise AgentHostingError(
            f"Cannot establish repository hosting policy: invalid installation context: {exc}"
        ) from exc
    selected = own.get("marketplaceId")
    if not isinstance(selected, str) or not selected:
        raise AgentHostingError(
            "Cannot establish repository hosting policy: validated installation context "
            "has no marketplace identity"
        )
    return selected


def require_path_hosting(
    cwd: str | os.PathLike[str] | None, *, operation: str = "launch",
) -> None:
    """Re-read policy at an actual execution path; no permission is cached."""
    if not cwd:
        raise AgentHostingError("Cannot establish hosting policy: no actual execution directory")
    require_agent_hosting(cwd, marketplace_id=marketplace_id(), operation=operation)


def unsupported_receiver(boundary: str, *, operation: str = "launch") -> AgentHostingError:
    """An opaque receiver must not turn inability to check into permission."""
    return AgentHostingError(
        f"Managed agent {operation} refused: {boundary} receiver cannot enforce fresh "
        "repository expose_agent policy at its actual execution directory. "
        "Use an enforcing receiver or an eligible external harness for source-only work; "
        "existing sessions and inspection remain available."
    )


async def require_target_hosting(target: SpawnTarget, *, operation: str = "launch") -> None:
    """Check before preparation, session registration, recovery or retirement."""
    from .privileged_plugins import relay_agent

    if relay_agent(target) is not None:
        from dataclasses import replace

        await require_target_hosting(
            replace(target, type="local", spawn_command=None), operation=operation,
        )
        return
    if target.type == "ssh" and not target.spawn_command:
        await require_ssh_hosting(target, operation=operation)
        return
    if target.type != "local" or target.spawn_command or target.codespace or target.container:
        from .provider_hosting import query_provider_policy

        validate_policy_result(await query_provider_policy(target, operation=operation))
        return
    path = target.cwd
    if target.project and not target.explicit_cwd:
        if target.worktree_id:
            from .acquisition_authority import read_worktree_record, worktree_command

            row = await read_worktree_record(
                target.worktree_id, target.project, command=worktree_command,
            )
            path = row.get("path")
            if not isinstance(path, str) or not path:
                raise AgentHostingError("Cannot establish hosting policy: worktree owner returned no execution path")
        elif not path:
            path = await resolve_project_cwd(target)
    if not path:
        raise AgentHostingError("Cannot establish hosting policy: managed target has no execution directory")
    require_path_hosting(path, operation=operation)


async def resolve_project_cwd(target: SpawnTarget) -> str:
    """Ask the workspace owner without allocation, adoption or launch planning effects."""
    from .acquisition_authority import worktree_command

    if not target.project:
        raise AgentHostingError("Cannot resolve an execution checkout without its project owner")
    result = await worktree_command(
        policy_check_args(None, worktree_id=target.worktree_id), target.project,
    )
    validate_policy_result(result)
    return result["repository"]


def policy_check_args(
    cwd: str | None, *, operation: str = "launch", worktree_id: str | None = None,
) -> list[str]:
    """Independent owner query: no project adoption is needed to check policy."""
    selection = (
        ["--path", cwd] if cwd else ["--worktree-id", worktree_id]
        if worktree_id else ["--project-root"]
    )
    return ["agent-policy", "check", *selection, "--operation", operation, "--json"]


def validate_policy_result(payload: Any) -> None:
    """Validate the independent owner's versioned answer, never a truthy fallback."""
    validate_hosting_result(payload)


async def require_ssh_hosting(target: SpawnTarget, *, operation: str = "launch") -> None:
    """Ask the receiver, not a similarly named local checkout, before SSH effects."""
    validate_policy_result(await query_ssh_policy(target, operation=operation))


async def query_ssh_policy(target: SpawnTarget, *, operation: str) -> dict[str, Any]:
    """Read the actual receiving owner without starting an agent channel."""
    if not target.host or not target.project:
        raise unsupported_receiver("SSH without an execution owner", operation=operation)
    from ssh_manager import SSHProfileResolutionError, SSHProfileSource, get_default_manager

    from .worktree_owner_operation import owner_ssh_command

    command = owner_ssh_command(
        target.project,
        policy_check_args(
            target.cwd if target.explicit_cwd or not target.worktree_id else None,
            operation=operation, worktree_id=target.worktree_id,
        ),
        target.ssh_shell,
    )
    manager = get_default_manager()

    async def read_policy() -> CommandResult:
        await manager.ensure_connected(
            target.host, SSHProfileSource(host_alias=target.host, resolve_timeout=30),
            preserve_existing_forwards=True,
        )
        return await manager.exec_command(target.host, command, timeout=30)

    try:
        result = await asyncio.wait_for(read_policy(), timeout=30)
    except (
        OSError, ConnectionError, TimeoutError, asyncio.TimeoutError,
        SSHProfileResolutionError, subprocess.TimeoutExpired,
    ) as exc:
        raise unsupported_receiver("SSH policy check", operation=operation) from exc
    if result.timed_out or result.exit_code != 0:
        detail = (result.stderr or result.stdout or "receiver check unavailable").strip()
        raise AgentHostingError(f"Repository hosting check on {target.host} refused: {detail}")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise unsupported_receiver("SSH policy check", operation=operation) from exc
    return payload


async def require_owner_hosting(
    owner: str, resolver: Any, cwd: str, *, operation: str = "resume",
) -> None:
    """Check an exact owner workspace without starting or importing its session."""
    from dataclasses import replace

    target = replace(resolver.resolve(owner), cwd=cwd, explicit_cwd=True)
    await require_target_hosting(target, operation=operation)


def require_local_target_hosting(target: SpawnTarget, *, operation: str = "launch") -> None:
    """Synchronous import/retained-plan boundary with an already exact local path."""
    if target.type != "local" or target.spawn_command or not target.cwd:
        raise unsupported_receiver(target.type, operation=operation)
    require_path_hosting(Path(target.cwd), operation=operation)


async def require_namespace_hosting(
    owner: AgentResolver, prefix: str, name: str, *,
    repo: str | None = None, repo_remote: str | None = None,
) -> SpawnTarget | None:
    """Admit readiness, or return an inspection-only session reference."""
    import inspect

    from .admin_resolver import AdminResolver
    from .agent_registry_namespace import CliNamespaceResolver

    resolver = owner._namespace_resolvers[prefix]
    if repo is not None and "repo" not in inspect.signature(resolver.resolve).parameters:
        raise ValueError(f"Cross-repo dispatch is not supported by the '{prefix}:' resolver")
    if isinstance(resolver, AdminResolver):
        await resolver.ensure_ready(name)
        await require_target_hosting(owner._resolve_static(name), operation="admin readiness")
        return None
    if not isinstance(resolver, CliNamespaceResolver):
        raise unsupported_receiver(f"{prefix} namespace")
    target = await owner._call_resolver(
        resolver, name, extra_plugins=[], repo=repo, repo_remote=repo_remote,
    )
    if target.type == "session":
        return target
    await require_target_hosting(target, operation="namespace readiness")
    return None


async def resolve_namespace_with_plugins(
    owner: AgentResolver, resolver: NamespaceResolver, name: str, *,
    repo: str | None = None, repo_remote: str | None = None,
) -> SpawnTarget:
    """Revalidate before plugin staging and before publishing the final target."""
    from .admin_resolver import AdminResolver

    if isinstance(resolver, AdminResolver):
        return await owner._call_resolver(
            resolver, name, extra_plugins=[], repo=repo, repo_remote=repo_remote,
        )
    candidate = await owner._call_resolver(
        resolver, name, extra_plugins=[], repo=repo, repo_remote=repo_remote,
    )
    await require_target_hosting(candidate)
    extra = await owner._related_plugins_for(resolver, name)
    target = await owner._call_resolver(
        resolver, name, extra_plugins=extra, repo=repo, repo_remote=repo_remote,
    )
    await require_target_hosting(target)
    metadata = target.codespace or target.container
    if metadata is not None:
        metadata["related_plugins"] = [
            ref.selection() for ref in extra
        ]
    return target


async def resolve_elevated_relay(owner: AgentResolver, name: str) -> SpawnTarget | None:
    """Check the local execution repository before starting the known privileged relay."""
    import asyncio

    from . import elevated
    from .spawn_target import SpawnTarget

    config = owner._agents.get(name)
    if config is None or not config.requires_admin or not elevated.relay_applicable(config.requires_admin):
        return None
    target = owner._resolve_static(name)
    await require_target_hosting(target, operation="elevated relay")
    token = await asyncio.to_thread(elevated.ensure_running)
    return SpawnTarget(
        type="command", spawn_command=elevated.relay_spawn_command(name, token=token),
        project=target.project, cwd=target.cwd, worktree_id=target.worktree_id,
        explicit_cwd=target.explicit_cwd, elevated=True,
    )
