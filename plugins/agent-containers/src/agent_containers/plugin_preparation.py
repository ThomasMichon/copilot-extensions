"""Trusted-container transport adapter for repository plugin preparation."""

from __future__ import annotations

import subprocess
import shlex
import hashlib
import logging
import json
import os
import shutil
from pathlib import Path

from agent_procutil import no_window_flags
from plugin_resolve.payload import is_harness_only
from plugin_resolve.reflection import (
    build_plan, build_prepare_script, parse_execution_checkout, parse_prepared, parse_prepared_context, selection_spec,
)
from plugin_resolve.target import github_repository_slug, requires_repository_binding


def _worktrees_registry(arguments) -> dict | None:
    from . import _peer_launch
    from .config import _installation_owner

    own = _installation_owner()
    if own is not None:
        peer = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
        if not peer.exists() and not peer.is_symlink():
            return None
        prefix = _peer_launch.launch_prefix(
            "agent-containers", Path(own["pluginRoot"]),
            os.environ[_peer_launch.CONTEXT_ENV], "agent-worktrees",
        )
    else:
        executable = shutil.which("agent-worktrees")  # marketplace-isolation: allow no-context legacy registry lookup; explicit context selects the attributable peer
        if not executable:
            return None
        prefix = [executable]
    result = subprocess.run(
        [*prefix, *arguments], capture_output=True, encoding="utf-8", timeout=30,
        creationflags=no_window_flags(),
    )
    if result.returncode:
        raise RuntimeError("Target plugin ownership registry command failed: " + result.stderr.strip())
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("Target plugin ownership registry returned no object")
    return data


def _registered_source_roots(repository: str) -> list[Path]:
    inventory = _worktrees_registry(["repos", "list", "--strict", "--json"])
    if inventory is None:
        logging.getLogger("agent-containers").warning(
            "Target-relative plugin ownership is unavailable without the repository registry",
        )
        return []
    if inventory.get("inventory_authority") != "complete" or not isinstance(inventory.get("repos"), list):
        raise RuntimeError("Target plugin ownership requires a complete repository registry")
    roots = []
    for row in inventory["repos"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise RuntimeError("Repository registry returned an invalid target binding")
        slug = github_repository_slug(row.get("remote", ""))
        if repository not in {row["name"], row.get("remote")} and not (
            slug and repository.casefold() == slug.casefold()
        ):
            continue
        found = _worktrees_registry(["repos", "find", row["name"], "--registered-only", "--json"])
        if (
            found is None or found.get("registration") != "ready"
            or found.get("name") != row["name"] or found.get("remote") != row.get("remote")
            or not isinstance(found.get("path"), str) or not Path(found["path"]).is_absolute()
        ):
            raise RuntimeError("Registered target plugin ownership changed during resolution")
        roots.append(Path(found["path"]).resolve(strict=True))
    return roots


def _script(args, target, *, run_input=None, remote_env=None, relay_port=None, prepared_context=None,
            include_compiled_context=False):
    if target.actual_profile != "trusted":
        raise RuntimeError("Restricted containers cannot receive plugin projection")
    specs = [selection_spec(value) for value in getattr(args, "stage_plugins", [])]
    specs = [spec for spec in specs if not is_harness_only(spec["source"])]
    roots = [Path(root) for root in getattr(args, "plugin_roots", [])]
    repository = (
        getattr(getattr(target, "info", None), "repo", None)
        or getattr(getattr(target, "fleet", None), "repo", None)
    )
    binding = {}
    if repository and requires_repository_binding(specs, roots):
        bound_roots = _registered_source_roots(repository)
        if bound_roots:
            binding = {"target_repository": repository, "target_source_roots": bound_roots}
    plan = build_plan(
        specs, roots,
        **binding,
    )
    if run_input is None:
        from .ssh_transport import build_ssh_command, prepare_ssh_config

        ssh = prepare_ssh_config(target.name, target.user)

        def run_input(command, stdin, *, timeout):
            result = subprocess.run(
                build_ssh_command(ssh, f"bash -lc {shlex.quote(command)}"),
                input=stdin, capture_output=True, timeout=timeout,
                creationflags=no_window_flags(),
            )
            return result.returncode, result.stdout.decode(), result.stderr.decode()

    workspace = target.workspace_folder
    if getattr(args, "worktree_id", None):
        command = (
            f"cd {shlex.quote(workspace)} && WORKTREES=$(command -v agent-worktrees) && "
            'test -f "$WORKTREES" && "$WORKTREES" resolve --json --dry-run '
            f"--no-fast-forward --defer-projections --worktree-id {shlex.quote(args.worktree_id)}"
        )
        rc, output, error = run_input(f"bash -lc {shlex.quote(command)}", b"", timeout=90)
        if rc:
            raise RuntimeError("Venue could not resolve the explicit execution worktree")
        workspace = parse_execution_checkout(output)
    from .hosting_policy import guest_check_command

    if not getattr(args, "hosting_live_attachment", False):
        rc, _output, error = run_input(
            f"bash -lc {shlex.quote(guest_check_command(workspace))}", b"", timeout=90,
        )
        if rc:
            raise RuntimeError(f"Guest repository hosting refused or receiver unsupported: {error.strip()}")
    script = build_prepare_script(
        plan, workspace, **({"include_prepared_context": True} if include_compiled_context else {}),
    )
    prefix = "set -e\n"
    if remote_env:
        prefix += f". {shlex.quote(remote_env)};\n"
    if relay_port is not None:
        probe = (
            f"exec 3<>/dev/tcp/127.0.0.1/{int(relay_port)} && "
            'printf "ping\\n\\n" >&3 && IFS= read -r -t 3 reply <&3 && [ "$reply" = "pong" ]'
        )
        wait = f"until timeout 3 bash -c {shlex.quote(probe)}; do sleep 0.25; done"
        prefix += f"timeout 5 bash -c {shlex.quote(wait)}\n"
    if prepared_context is not None:
        prepared_context["workspace"] = workspace
    return prefix + script, run_input


def prepare_plugins(args, target, *, run_input=None, remote_env=None, relay_port=None,
                    prepared_context=None) -> list[str]:
    script, run_input = _script(
        args, target, run_input=run_input, remote_env=remote_env, relay_port=relay_port,
        prepared_context=prepared_context,
        include_compiled_context=prepared_context is not None,
    )
    rc, output, error = run_input("bash -ls", script.encode(), timeout=600)
    if rc:
        raise RuntimeError(f"Required target plugin preparation failed: {error.strip()}")
    if prepared_context is not None:
        prepared_context["plugin_context"] = parse_prepared_context(
            output, expected_repo=prepared_context["workspace"],
        )
    return parse_prepared(output)


def attached_launch_command(command: str, workspace: str) -> str:
    if not isinstance(workspace, str) or not workspace.startswith("/") or "\0" in workspace:
        raise RuntimeError("Attached launch requires its prepared execution checkout")
    words = shlex.split(command)
    inner = words[2] if len(words) == 3 and words[:2] == ["bash", "-lc"] else command
    return f"bash -lc {shlex.quote(f'cd -- {shlex.quote(workspace)} && {inner}')}"


def stage_for_attached_launch(
    args, target, *, remote_env=None, relay_port=None, prepared_context=None,
) -> str:
    """Execute preparation inside the foreground SSH channel owning the -R."""
    context = {}
    script, run_input = _script(
        args, target, remote_env=remote_env, relay_port=relay_port, prepared_context=context,
    )
    digest = hashlib.sha256(script.encode()).hexdigest()
    path = f"$HOME/.acp-staged-plugins/preparation/{digest}.sh"
    command = f'mkdir -p "$HOME/.acp-staged-plugins/preparation" && cat > "{path}" && chmod 700 "{path}"'
    rc, _out, error = run_input(command, script.encode(), timeout=60)
    if rc:
        raise RuntimeError(f"Could not stage required plugin preparation: {error.strip()}")
    if prepared_context is not None:
        prepared_context.update(context)
    return f'bash "{path}" >&2 && '
