"""Selection adapter for the shared venue plugin preparation barrier."""

from __future__ import annotations

from pathlib import Path
import asyncio
import json
import logging
import shlex
import time

from plugin_resolve.reflection import (
    build_plan, build_prepare_script, parse_execution_checkout, parse_prepared, parse_prepared_context,
    selection_spec,
)
from plugin_resolve.target import requires_repository_binding

from .codespace_plugins import (
    is_harness_plugin,
    parse_operator_plugins,
    plugin_names_from_enabled,
    resolve_codespace_plugins,
)
from .config import repo_copilot_settings


def _registered_target_binding(repository: str) -> dict:
    from .coordination import _run
    from .config import _normalize_repository_remote

    def query(arguments):
        result = _run(arguments, timeout=30)
        if result is None:
            return None
        if result.returncode:
            raise RuntimeError("Target plugin ownership registry command failed: " + result.stderr.strip())
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise RuntimeError("Target plugin ownership registry returned no object")
        return data

    inventory = query(["repos", "list", "--strict", "--json"])
    if inventory is None:
        logging.getLogger("agent-codespaces").warning(
            "Target-relative plugin ownership is unavailable without the repository registry",
        )
        return {}
    if inventory.get("inventory_authority") != "complete" or not isinstance(inventory.get("repos"), list):
        raise RuntimeError("Target plugin ownership requires a complete repository registry")
    remote = repository if "://" in repository or repository.startswith("git@") else f"https://github.com/{repository}"
    identity = _normalize_repository_remote(remote)
    roots = []
    for row in inventory["repos"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise RuntimeError("Repository registry returned an invalid target binding")
        if row["name"] != repository and not (
            identity and _normalize_repository_remote(row.get("remote", "")) == identity
        ):
            continue
        found = query(["repos", "find", row["name"], "--registered-only", "--json"])
        if (
            found is None or found.get("registration") != "ready"
            or found.get("name") != row["name"] or found.get("remote") != row.get("remote")
            or not isinstance(found.get("path"), str) or not Path(found["path"]).is_absolute()
        ):
            raise RuntimeError("Registered target plugin ownership changed during resolution")
        roots.append(Path(found["path"]).resolve(strict=True))
    return {"target_repository": identity or repository, "target_source_roots": roots} if roots else {}


def checkout_provision_command(config, repo, *, requested_repo=None, repo_remote=None) -> str:
    if requested_repo is None or not repo_remote:
        return ""
    folder, prepopulated = config.workspace_folder_for_request(repo, requested_repo)
    if folder is None or prepopulated:
        return ""
    return (
        f"[ -e {shlex.quote(folder + '/.git')} ] || "
        f"git clone -- {shlex.quote(repo_remote)} {shlex.quote(folder)}"
    )


def checkout_acp_command(config, repo, *, requested_repo=None, repo_remote=None) -> str:
    from .config import _WORKSPACE_CD
    copilot = "copilot --acp --stdio --allow-all --experimental"
    if requested_repo is not None:
        folder, _ = config.workspace_folder_for_request(repo, requested_repo)
        if folder is None:
            return f"{_WORKSPACE_CD} && {copilot}"
        clone = checkout_provision_command(
            config, repo, requested_repo=requested_repo, repo_remote=repo_remote,
        )
        command = f"cd {shlex.quote(folder)} && {copilot}"
        return f"{clone} && {command}" if clone else command
    if config.acp_command:
        return config.acp_command
    folder = config.workspace_folder_for(repo)
    return f"cd {folder} && {copilot}" if folder else f"{_WORKSPACE_CD} && {copilot}"


def plugin_plan(repo: str | None, config, sources=(), *, repo_roots=()) -> dict:
    settings_roots = list(dict.fromkeys(
        Path(root) for root in [*(getattr(config, "source_paths", ()) or ()), *repo_roots]
    ))
    roots = list(dict.fromkeys([
        *(Path(root) for root in repo_roots),
        *(Path(root) for root in reversed(getattr(config, "source_paths", ()) or ())),
    ]))
    settings = repo_copilot_settings(settings_roots)
    specs = resolve_codespace_plugins(
        repo, extra_specs=parse_operator_plugins(getattr(config, "codespace_plugins", []) or []),
        enabled_names=plugin_names_from_enabled(settings.get("enabledPlugins")),
        enabled_sources=settings.get("enabledPlugins"), repo_roots=roots,
    )
    selected = {spec.source: {"source": spec.source, "enable": spec.enable} for spec in specs}
    for value in sources:
        spec = selection_spec(value)
        if not is_harness_plugin(spec["source"]):
            selected.setdefault(spec["source"], spec)
            if spec.get("allow_missing"):
                selected[spec["source"]]["allow_missing"] = True
    binding = {}
    if repo and requires_repository_binding(selected.values(), roots):
        binding = _registered_target_binding(repo)
    return build_plan(list(selected.values()), roots,
                      marketplaces=settings.get("extraKnownMarketplaces"), **binding)


async def prepare_plugins(manager, name: str, repo: str | None, config, workspace: str,
                          sources=(), *, worktree_id: str | None = None, repo_roots=(),
                          prelude: str = "", relay_port: int | None = None,
                          prepared_context: dict | None = None,
                          include_compiled_context: bool = True,
                          live_attachment: bool = False,
                          hosting_required: bool = True) -> list[str]:
    from ._ssh_retry import exec_with_retry

    plan = plugin_plan(repo, config, sources, repo_roots=repo_roots)
    if relay_port is not None:
        await require_relay_ready(manager, name, relay_port)
    if worktree_id:
        # Resolve in the same venue login environment as the native launcher,
        # then invoke the exact discovered command, not a host PATH executable.
        command = (
            f"cd {shlex.quote(workspace)} && WORKTREES=$(command -v agent-worktrees) && "
            'test -f "$WORKTREES" && "$WORKTREES" resolve --json --dry-run '
            f"--no-fast-forward --defer-projections --worktree-id {shlex.quote(worktree_id)}"
        )
        resolved = await exec_with_retry(
            manager, name, f"bash -lc {shlex.quote(command)}", timeout=90,
        )
        if resolved.exit_code:
            raise RuntimeError("Venue could not resolve the explicit execution worktree")
        workspace = parse_execution_checkout(resolved.stdout)
    from .hosting_policy import require_guest_hosting

    if hosting_required and not live_attachment:
        await require_guest_hosting(manager, name, workspace, preflight=True)
    script = prelude + "\n" + build_prepare_script(
        plan, workspace,
        **({"include_prepared_context": True} if prepared_context is not None and include_compiled_context else {}),
    )
    result = await exec_with_retry(
        manager, name, "bash -ls", input_bytes=script.encode(), timeout=600.0,
    )
    if result.exit_code:
        raise RuntimeError(f"Required target plugin preparation failed: {result.stderr.strip()}")
    directories = parse_prepared(result.stdout)
    if prepared_context is not None:
        prepared_context["workspace"] = workspace
        if include_compiled_context:
            prepared_context["plugin_context"] = parse_prepared_context(result.stdout, expected_repo=workspace)
    return directories


def attached_launch_command(command: str, workspace: str) -> str:
    if not isinstance(workspace, str) or not workspace.startswith("/") or "\0" in workspace:
        raise RuntimeError("Attached launch requires its prepared execution checkout")
    words = shlex.split(command)
    inner = words[2] if len(words) == 3 and words[:2] == ["bash", "-lc"] else command
    return f"bash -lc {shlex.quote(f'cd -- {shlex.quote(workspace)} && {inner}')}"


async def require_relay_ready(manager, name: str, port: int) -> None:
    from ._ssh_retry import exec_with_retry
    script = (
        f"exec 3<>/dev/tcp/127.0.0.1/{int(port)} && "
        'printf "ping\\n\\n" >&3 && IFS= read -r -t 3 reply <&3 && [ "$reply" = "pong" ]'
    )
    command = f"timeout 3 bash -c {shlex.quote(script)}"
    deadline = time.monotonic() + 5
    while True:
        result = await exec_with_retry(manager, name, command, timeout=5)
        if result.exit_code == 0:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Credential relay is not ready before plugin installation")
        await asyncio.sleep(0.25)


def disabled_plugin(source: str) -> dict:
    return selection_spec({"source": source, "enable": False})


async def prepare_attached(
    args, config, *, on_ready=None, release=None, prepared_context: dict | None = None,
) -> None:
    from ssh_manager import CodespaceConfigSource, ConnectionManager
    from .__main__ import _lookup_codespace_repo
    from venue_copilot import live_session_for
    from . import connection_owner, relay_launch
    from .__main__ import _provision_relay_helpers, _provision_repo_hooks, _start_supervised_relay

    repo = _lookup_codespace_repo(args.name)
    workspace = config.resolved_workspace_folder_for(repo)
    identity = args.worktree_id or f"anchor-{workspace.rstrip('/').rsplit('/', 1)[-1]}"
    rejoining = (live_session_for(f"{identity}@{args.name}") or {}).get("status") == "live"
    manager = ConnectionManager()
    relay = None
    try:
        connection = await manager.ensure_connected(args.name, CodespaceConfigSource(args.name), [])
        prelude, port = "", None
        if not args.no_relay:
            prelude, port = relay_launch.build_relay_launch_env(args.name)
            if not await connection_owner.await_owner_relay(args.name):
                relay = await _start_supervised_relay(
                    args.name, connection.config, port,
                    context="Attached CodeSpace plugin preparation",
                    host_port_resolver=lambda: relay_launch.effective_relay_port(config),
                )
            await require_relay_ready(manager, args.name, port)
            if not rejoining:
                await _provision_relay_helpers(manager, args.name)
        if not rejoining:
            await _provision_repo_hooks(manager, args.name, config, repo, relay_env=prelude)
        # A live host registration cannot guarantee the remote child survives.
        await prepare_plugins(
            manager, args.name, repo, config, workspace,
            getattr(args, "stage_plugins", []), worktree_id=args.worktree_id,
            repo_roots=getattr(args, "plugin_roots", []),
            prelude=prelude, relay_port=port,
            **({"prepared_context": prepared_context} if prepared_context is not None else {}),
            include_compiled_context=False,
            live_attachment=rejoining,
        )
        if on_ready is not None:
            on_ready(not args.no_relay)
            await release()
    finally:
        if relay is not None:
            await relay.stop()
        await manager.disconnect(args.name)
