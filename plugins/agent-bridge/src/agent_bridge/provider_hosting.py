"""Read fresh policy through the established venue's actual guest execution."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
from typing import TYPE_CHECKING, Any, TypedDict

from repository_policy import AgentHostingError

if TYPE_CHECKING:
    from .spawn_target import SpawnTarget

# OpenSSH's own connection/transport failure status: no receiver answer exists.
SSH_TRANSPORT_FAILURE_EXIT = 255


class CodeSpaceNotRunning(AgentHostingError):
    """A read-only check cannot inspect this exact receiving CodeSpace."""


class _ProviderEnvironment(TypedDict, total=False):
    environment: dict[str, str | None]


def _require_workspace(workspace: str) -> None:
    """Require the actual POSIX execution path before quoting a guest command."""
    if not isinstance(workspace, str) or not workspace.startswith("/") or "\0" in workspace:
        raise AgentHostingError("Repository hosting requires an absolute guest execution checkout")


def _policy_check(
    workspace: str, operation: str, metadata: dict[str, Any], *, text_transport: bool = False,
) -> tuple[str, bytes | None]:
    _require_workspace(workspace)
    declaration = metadata.get("portable_receiving_policy")
    if declaration is None:
        return (
            f"cd -- {shlex.quote(workspace)} && "
            'WORKTREES=$(command -v agent-worktrees) && test -f "$WORKTREES" && '
            '"$WORKTREES" agent-policy check --path "$PWD" '
            f"--operation {shlex.quote(operation)} --json",
            None,
        )
    if (
        not isinstance(declaration, dict)
        or type(declaration.get("version")) is not int or declaration["version"] != 1
    ):
        raise AgentHostingError("Unsupported portable receiving policy declaration")
    from .portable_policy import guest_policy_check

    return guest_policy_check(
        workspace, operation, **({"text_transport": True} if text_transport else {}),
    )


def _answer(rc: int, output: str, error: str, *, portable: bool = True) -> dict[str, Any]:
    from .hosting_policy import validate_policy_result

    if rc:
        raise AgentHostingError(
            ("Guest repository hosting refused or receiver unsupported: " if portable else
             "Selected provider has no portable receiving handoff; legacy receiver refused or unsupported: ")
            + (error.strip() or output.strip() or f"exit {rc}")
        )
    try:
        result: dict[str, Any] = json.loads(output)
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
            raise CodeSpaceNotRunning(
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
        command, payload = await asyncio.to_thread(_policy_check, workspace, operation, metadata)
        rc, output, error = await transport.run(
            f"bash -lc {shlex.quote(command)}", timeout=90,
            **({"input_bytes": payload} if payload is not None else {}),
        )
        if rc == SSH_TRANSPORT_FAILURE_EXIT:
            raise AgentHostingError(
                "Guest repository hosting check is unavailable: SSH transport failed "
                "(exit 255) before the receiver answered: "
                + (error.strip() or output.strip() or "no diagnostic output")
            )
        return _answer(rc, output, error, portable=payload is not None)
    finally:
        await transport.close()


def _provider_command(metadata: dict[str, Any]) -> list[str] | None:
    command = metadata.get("provider_command")
    if not command:
        from .provider_sources import discover_provider_manifests

        provider = discover_provider_manifests().get("container")
        command = list(provider.command) if provider is not None else None
    if (
        not isinstance(command, (list, tuple)) or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        return None
    from .transport import _reresolve_stale_interpreter

    prefix = _reresolve_stale_interpreter(list(command))
    shim = Path(prefix[0])
    if sys.platform == "win32" and shim.is_absolute() and shim.name.lower() == "agent-containers.cmd":
        # The provider's batch shim invokes this sibling, but cmd.exe splits multiline argv.
        native = shim.with_suffix(".ps1")
        if native.is_file():
            host = shutil.which("pwsh") or str(
                Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            )
            return [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(native), *prefix[1:]]
    return prefix


async def _container_policy(target: SpawnTarget, operation: str) -> dict[str, Any]:
    from .session_host.container_transport import _run_provider

    venue = target.venue or {}
    # A driver-exec member has no Session Host container block; its venue
    # declares the portable receiving policy and provider command instead.
    metadata = target.container or (venue if venue.get("transport") == "driver-exec" else {})
    name = metadata.get("name") or str(venue.get("target_id") or "").removeprefix("container:")
    expected = venue.get("instance_id") or metadata.get("instance_id")
    identity = expected or name
    if not isinstance(identity, str) or not identity:
        raise AgentHostingError("Container hosting check requires an exact receiving instance")
    runtime = ["docker"]
    bound = "runtime_proxy" in venue
    binding = venue.get("runtime_proxy")
    environment: dict[str, str | None] = {}
    bound_provider: list[str] | None = None
    fleet = venue.get("fleet")
    driver = venue.get("driver")
    if driver:
        # The member lives in an external runtime driver's runtime, not
        # necessarily Docker's: the provider proxies the same inspect/exec.
        if bound:
            if (
                not isinstance(binding, dict) or type(binding.get("version")) is not int
                or binding["version"] != 1
                or not isinstance(binding.get("execution_runtime"), str)
                or not binding["execution_runtime"]
                or not isinstance(fleet, str) or not fleet
                or not isinstance(name, str) or not name
                or venue.get("target_id") != f"container:{name}"
                or not isinstance(expected, str) or not expected
                or venue.get("instance_id") != expected
                or metadata.get("instance_id", expected) != expected
            ):
                raise AgentHostingError("Invalid or unsupported bound container runtime proxy")
            provider = binding.get("provider_command")
            if (
                not isinstance(provider, list) or not provider
                or any(not isinstance(part, str) or not part for part in provider)
                or not Path(provider[0]).is_absolute()
            ):
                raise AgentHostingError("Bound runtime proxy lacks its attributed provider command")
            if not Path(provider[0]).is_file():
                raise AgentHostingError("Bound runtime proxy provider is unavailable; resolve the container again")
            scope = binding.get("provider_environment")
            if (
                not isinstance(scope, dict)
                or scope.keys() != {
                    "AGENT_CONTAINERS_HOME", "AGENT_CONTAINERS_STATE_DIR",
                    "AGENT_CONTAINERS_CONFIG", "AGENT_CONTAINERS_DRIVERS_DIR",
                    "AGENT_CONTAINERS_PAYLOAD_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
                }
                or any(value is not None and (not isinstance(value, str) or "\0" in value)
                       for value in scope.values())
            ):
                raise AgentHostingError("Invalid bound runtime proxy installation environment")
            # Direct replay skips the Containers runtime gate's environment cleanup.
            environment = {**scope, "PYTHONPATH": None, "PYTHONHOME": None}
            bound_provider = provider
            runtime = [*provider, "runtime-proxy", "--fleet", fleet,
                       "--member", name, "--expected-runtime", binding["execution_runtime"]]
        else:
            provider = _provider_command(metadata)
            if provider is None:
                raise AgentHostingError("Container receiver lacks an attributed runtime proxy")
            runtime = [*provider, "runtime-proxy", *(
                ["--fleet", fleet] if isinstance(fleet, str) and fleet else []
            )]
    elif bound:
        raise AgentHostingError("Bound container runtime proxy requires an external driver")
    options: _ProviderEnvironment = {"environment": environment} if bound else {}
    rc, output, error = await _run_provider([*runtime, "inspect", identity], timeout=30, **options)
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
            or expected and not (instance == expected if bound else instance.startswith(expected))
            or not isinstance(state, dict) or state.get("Running") is not True
        ):
            raise ValueError("selected container is changed or not running")
        if bound:
            labels = actual["Config"]["Labels"]
            if (
                actual.get("Name") != "/" + name or not isinstance(labels, dict)
                or labels.get("agent-containers.fleet") != fleet
                or labels.get("agent-containers.driver") != driver
            ):
                raise ValueError("provider inspected another container member")
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentHostingError("Cannot establish the actual running container for hosting policy") from exc
    workspace = (
        target.cwd if target.explicit_cwd else
        metadata.get("workspace_folder") or venue.get("workspace_folder") or target.cwd
    )
    user = target.user
    if not user or not workspace:
        resolver = bound_provider if bound else _provider_command(metadata)
        if resolver is None or not isinstance(name, str) or not name:
            raise AgentHostingError("Container receiver lacks an attributed context resolver")
        rc, output, error = await _run_provider([*resolver, "namespace-resolve", name], timeout=30, **options)
        try:
            spec = json.loads(output)
            refreshed_venue = spec.get("venue") if isinstance(spec, dict) else None
            if (
                rc or not isinstance(refreshed_venue, dict)
                or refreshed_venue.get("instance_id") != instance
                or bound and any(refreshed_venue.get(key) != venue.get(key) for key in
                                 ("target_id", "fleet", "driver", "runtime_proxy"))
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
    _require_workspace(workspace)
    prefix = [*runtime, "exec", "-u", user, instance, "bash", "-lc"]
    if target.worktree_id and not target.explicit_cwd:
        from plugin_resolve.reflection import parse_execution_checkout

        command = (
            f"cd -- {shlex.quote(workspace)} && "
            "agent-worktrees resolve --json --dry-run --no-fast-forward --defer-projections "
            f"--worktree-id {shlex.quote(target.worktree_id)}"
        )
        rc, output, error = await _run_provider([*prefix, command], timeout=30, **options)
        if rc:
            raise AgentHostingError(f"Container execution checkout unavailable: {error.decode(errors='replace')}")
        workspace = parse_execution_checkout(output.decode())
    command, payload = await asyncio.to_thread(
        _policy_check, workspace, operation, metadata, text_transport=bool(venue.get("driver")),
    )
    argv = [*runtime, "exec", *(["-i"] if payload is not None else []),
            "-u", user, instance, "bash", "-lc", command]
    if payload is None:
        rc, output, error = await _run_provider(argv, timeout=90, **options)
    else:
        rc, output, error = await _run_provider(argv, timeout=90, input_bytes=payload, **options)
    return _answer(rc, output.decode(), error.decode(), portable=payload is not None)
