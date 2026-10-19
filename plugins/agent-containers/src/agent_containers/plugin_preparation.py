"""Trusted-container transport adapter for repository plugin preparation."""

from __future__ import annotations

import subprocess
import shlex
import hashlib
from pathlib import Path

from agent_procutil import no_window_flags
from plugin_resolve.payload import is_harness_only
from plugin_resolve.reflection import (
    build_plan, build_prepare_script, parse_execution_checkout, parse_prepared, selection_spec,
)


def _script(args, target, *, run_input=None, remote_env=None, relay_port=None, prepared_context=None):
    if target.actual_profile != "trusted":
        raise RuntimeError("Restricted containers cannot receive plugin projection")
    specs = [selection_spec(value) for value in getattr(args, "stage_plugins", [])]
    plan = build_plan(
        [spec for spec in specs if not is_harness_only(spec["source"])],
        [Path(root) for root in getattr(args, "plugin_roots", [])],
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
    script = build_prepare_script(plan, workspace)
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


def prepare_plugins(args, target, *, run_input=None, remote_env=None, relay_port=None) -> list[str]:
    script, run_input = _script(
        args, target, run_input=run_input, remote_env=remote_env, relay_port=relay_port,
    )
    rc, output, error = run_input("bash -ls", script.encode(), timeout=600)
    if rc:
        raise RuntimeError(f"Required target plugin preparation failed: {error.strip()}")
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
