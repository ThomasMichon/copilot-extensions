"""Read fresh policy through the established venue's actual guest execution."""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING, Any

from repository_policy import AgentHostingError

if TYPE_CHECKING:
    from .spawn_target import SpawnTarget


def guest_command(workspace: str, operation: str) -> str:
    """Invoke the guest's own owner; caller installation/environment is not authority."""
    if not isinstance(workspace, str) or not workspace.startswith("/") or "\0" in workspace:
        raise AgentHostingError("Repository hosting requires an absolute guest execution checkout")
    return (
        f"cd -- {shlex.quote(workspace)} && "
        'WORKTREES=$(command -v agent-worktrees) && test -f "$WORKTREES" && '
        '"$WORKTREES" agent-policy check --path "$PWD" '
        f"--operation {shlex.quote(operation)} --json"
    )


def _answer(rc: int, output: str, error: str) -> dict[str, Any]:
    from .hosting_policy import validate_policy_result

    if rc:
        raise AgentHostingError(
            "Guest repository hosting refused or receiver unsupported: "
            + (error.strip() or output.strip() or f"exit {rc}")
        )
    try:
        result = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise AgentHostingError("Guest repository hosting returned invalid policy JSON") from exc
    validate_policy_result(result)
    return result


async def query_provider_policy(target: SpawnTarget, *, operation: str) -> dict[str, Any]:
    """Never provision credentials, allocate a workspace or launch a managed agent."""
    from .hosting_policy import unsupported_receiver
    from .session_host.codespace_transport import parse_codespace_target

    codespace = target.codespace or (
        parse_codespace_target(target.spawn_command) if target.spawn_command else None
    )
    try:
        if codespace:
            return await _codespace_policy(target, codespace, operation)
        venue = target.venue or {}
        if target.container or venue.get("provider") == "agent-containers":
            return await _container_policy(target, operation)
    except AgentHostingError:
        raise
    except (RuntimeError, OSError, ValueError) as exc:
        raise AgentHostingError(f"Guest repository hosting check is unavailable: {exc}") from exc
    raise unsupported_receiver(target.type, operation=operation)


async def _codespace_policy(
    target: SpawnTarget, metadata: dict[str, Any], operation: str,
) -> dict[str, Any]:
    from .session_host.codespace_transport import CodeSpaceTransport, resolve_execution_workspace

    name = metadata.get("name")
    if not isinstance(name, str) or not name:
        raise AgentHostingError("CodeSpace hosting check requires an exact receiving venue")
    transport = CodeSpaceTransport(name, metadata.get("repo") or "")
    try:
        if not await transport.is_running():
            raise AgentHostingError(
                "Cannot inspect repository hosting policy without waking the CodeSpace; "
                "make the source checkout inspectable before managed agent launch"
            )
        workspace = (
            target.cwd if target.explicit_cwd
            else await resolve_execution_workspace(dict(metadata), transport)
        )
        if not isinstance(workspace, str) or not workspace:
            raise AgentHostingError("CodeSpace receiver did not resolve an execution checkout")
        if target.worktree_id and not target.explicit_cwd:
            from plugin_resolve.reflection import parse_execution_checkout

            command = (
                f"cd -- {shlex.quote(workspace)} && "
                "agent-worktrees resolve --json --dry-run --no-fast-forward --defer-projections "
                f"--worktree-id {shlex.quote(target.worktree_id)}"
            )
            rc, output, error = await transport.run(f"bash -lc {shlex.quote(command)}", timeout=30)
            if rc:
                raise AgentHostingError(f"Guest execution checkout is unavailable: {error or output}")
            workspace = parse_execution_checkout(output)
        command = guest_command(workspace, operation)
        return _answer(*await transport.run(f"bash -lc {shlex.quote(command)}", timeout=90))
    finally:
        await transport.close()


async def _container_policy(target: SpawnTarget, operation: str) -> dict[str, Any]:
    from .session_host.container_transport import _run_provider

    venue = target.venue or {}
    metadata = target.container or {}
    name = metadata.get("name") or str(venue.get("target_id") or "").removeprefix("container:")
    expected = venue.get("instance_id") or metadata.get("instance_id")
    identity = expected or name
    if not isinstance(identity, str) or not identity:
        raise AgentHostingError("Container hosting check requires an exact receiving instance")
    rc, output, error = await _run_provider(["docker", "inspect", identity], timeout=30)
    if rc:
        raise AgentHostingError(f"Container hosting inspection failed: {error.decode(errors='replace')}")
    try:
        rows = json.loads(output)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("expected one container")
        actual = rows[0]
        instance = actual["Id"]
        state = actual.get("State")
        if (
            not isinstance(instance, str) or not instance
            or expected and not instance.startswith(expected)
            or not isinstance(state, dict) or state.get("Running") is not True
        ):
            raise ValueError("selected container is changed or not running")
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentHostingError("Cannot establish the actual running container for hosting policy") from exc
    workspace = (
        target.cwd if target.explicit_cwd else
        metadata.get("workspace_folder") or venue.get("workspace_folder") or target.cwd
    )
    user = target.user
    if not user or not workspace:
        command = metadata.get("provider_command")
        if not command:
            from .provider_sources import discover_provider_manifests

            provider = discover_provider_manifests().get("container")
            command = list(provider.command) if provider is not None else None
        if (
            not isinstance(command, (list, tuple)) or not command
            or any(not isinstance(part, str) or not part for part in command)
            or not isinstance(name, str) or not name
        ):
            raise AgentHostingError("Container receiver lacks an attributed context resolver")
        rc, output, error = await _run_provider([*command, "namespace-resolve", name], timeout=30)
        try:
            spec = json.loads(output)
            refreshed_venue = spec.get("venue") if isinstance(spec, dict) else None
            if (
                rc or not isinstance(refreshed_venue, dict)
                or refreshed_venue.get("instance_id") != instance
            ):
                raise ValueError("provider resolved another or unknown instance")
            user = user or spec.get("user")
            workspace = workspace or spec.get("workspace_folder")
        except (TypeError, ValueError) as exc:
            raise AgentHostingError("Container receiver could not resolve its execution context") from exc
    if not isinstance(user, str) or not user:
        raise AgentHostingError("Container hosting check requires the provider's execution user")
    if not isinstance(workspace, str):
        raise AgentHostingError("Container receiver did not resolve an execution checkout")
    guest_command(workspace, operation)
    prefix = ["docker", "exec", "-u", user, instance, "bash", "-lc"]
    if target.worktree_id and not target.explicit_cwd:
        from plugin_resolve.reflection import parse_execution_checkout

        command = (
            f"cd -- {shlex.quote(workspace)} && "
            "agent-worktrees resolve --json --dry-run --no-fast-forward --defer-projections "
            f"--worktree-id {shlex.quote(target.worktree_id)}"
        )
        rc, output, error = await _run_provider([*prefix, command], timeout=30)
        if rc:
            raise AgentHostingError(f"Container execution checkout unavailable: {error.decode(errors='replace')}")
        workspace = parse_execution_checkout(output.decode())
    rc, output, error = await _run_provider([*prefix, guest_command(workspace, operation)], timeout=90)
    return _answer(rc, output.decode(), error.decode())
