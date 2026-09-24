"""Detached, observable CLI-mode Copilot sessions for trusted containers."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from agent_procutil import no_window_flags
from venue_copilot import read_seed
from venue_copilot.detached import launch_detached, public_plan, stop_detached

_BUSY_EXIT = 75
_RESERVATION_TTL = 900.0
_RESERVE_RETRY_WINDOW = 90.0


def _progress(stage: str, detail: str = "") -> None:
    print(f"[DETACH] {stage}{': ' + detail if detail else ''}", file=sys.stderr, flush=True)


def plan_for(args: argparse.Namespace, target: Any) -> dict[str, Any]:
    workspace = getattr(target, "workspace_folder", "") or ""
    identity = args.worktree_id or f"anchor-{os.path.basename(workspace.rstrip('/')) or args.name}"
    scope = f"{identity}@{args.name}"
    mux = f"wt-{identity}"
    return {
        "container": args.name,
        "identity": identity,
        "workspace_folder": workspace or None,
        "workspace": workspace or None,
        "anchor": not args.worktree_id,
        "scope_id": scope,
        "mux_session": mux,
        "venue": {"kind": "container", "target": args.name, "mux_session_name": mux},
        "reservation_ttl": _RESERVATION_TTL,
        "reserve_retry_window": _RESERVE_RETRY_WINDOW,
        "registration_error": "could not provision registration credentials in the container",
        "bridge_probe_error": (
            "the container cannot reach the host bridge through the forward "
            "(authenticated probe failed)"
        ),
        "missing_agent_worktrees_error": (
            "agent-worktrees is not installed in the container; install it on "
            "the trusted fleet image before using detached CLI-mode sessions"
        ),
        "old_agent_worktrees_error": (
            "the container's agent-worktrees is too old for detached launch "
            "(--bridge-scope-id/--copilot-arg); update it in the container"
        ),
        "reservation_wait": "another launch on this container holds the reservation",
        "launch_detail": "`agent-worktrees embody` in the container",
    }


def _fail(message: str, plan: dict[str, Any] | None = None, **extra: Any) -> int:
    print(f"[FAIL] {message}", file=sys.stderr)
    print(json.dumps({"ok": False, "error": message, **(plan or {}), **extra}, indent=2))
    return 1


def _emit(rc: int, payload: dict[str, Any]) -> int:
    if not payload.get("ok"):
        print(f"[FAIL] {payload.get('error')}", file=sys.stderr)
    print(json.dumps(payload, indent=2))
    return rc


def _bash(command: str) -> str:
    return f"bash -lc {shlex.quote(command)}"


def _remote(
    ssh_config: Any,
    command: str,
    *,
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    from .ssh_transport import build_ssh_command

    argv = build_ssh_command(ssh_config, _bash(command), pty=False)
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=no_window_flags(),
    )
    return result.returncode, result.stdout or "", result.stderr or ""


def _relay_launch_env(
    args: argparse.Namespace,
    target: Any,
    *,
    require_live_relay_port: Callable[[], int],
    relay_healthy: Callable[[int], bool],
) -> tuple[dict[str, str], int | None, int | None]:
    if getattr(args, "no_relay", False):
        return {}, None, None
    config = target.config
    _forward, relay_enabled = config.credentials_for(target.fleet)
    if not relay_enabled:
        return {}, None, None
    from .container_shims import deploy as deploy_shims
    from .container_shims import git_credential_environment
    from .relay_provider import token_for

    host_relay_port = require_live_relay_port()
    if not relay_healthy(host_relay_port):
        raise RuntimeError(
            "Published credential relay at "
            f"127.0.0.1:{host_relay_port} failed its ping probe; "
            "restart agent-bridge or set relay.enabled: false in containers.yaml"
        )
    deploy_shims(args.name, ado=True)
    env = {
        "LC_GIT_CREDENTIAL_RELAY_HOST": "127.0.0.1",
        "LC_GIT_CREDENTIAL_RELAY": str(config.relay_port),
        "LC_GIT_CREDENTIAL_RELAY_TOKEN": token_for(args.name),
        **git_credential_environment(),
    }
    return env, int(config.relay_port), int(host_relay_port)


def _prepare_remote_env(args: argparse.Namespace, target: Any, values: dict[str, str]) -> str | None:
    if not values:
        return None
    from .ssh_transport import container_environment, write_remote_env

    launch_env = container_environment(args.name, target.user)
    launch_env.update(values)
    return write_remote_env(args.name, target.user, launch_env)


def _resolve_target(args: argparse.Namespace) -> Any:
    from .config import RESTRICTED_PROFILE, load_config
    from .resolver import resolve_live_exec_target

    target = resolve_live_exec_target(args.name, config=load_config())
    if target.actual_profile == RESTRICTED_PROFILE:
        raise RuntimeError(
            f"'{args.name}' is a restricted container -- detached CLI-mode sessions "
            "need trusted-container SSH key projection. Use a trusted fleet."
        )
    return target


class _ContainerAdapter:
    def __init__(
        self,
        *,
        name: str,
        ssh_config: Any,
        remote_env: str | None,
        relay_port: int | None,
        host_relay_port: int | None,
    ) -> None:
        self.name = name
        self.ssh_config = ssh_config
        self.remote_env = remote_env
        self.relay_port = relay_port
        self.host_relay_port = host_relay_port

    def run(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        return _remote(self.ssh_config, command, timeout=timeout)

    def launch(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        from .ssh_transport import build_remote_command, build_ssh_command

        remote_command = build_remote_command(command, self.remote_env)
        proc = subprocess.run(
            build_ssh_command(self.ssh_config, remote_command, pty=False),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=no_window_flags(),
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def ensure_keeper(self, *, venue_port: int, mux: str) -> dict[str, Any]:
        from . import forward_keeper

        return forward_keeper.ensure_running(
            self.name,
            venue_port=venue_port,
            mux=mux,
            relay_port=self.relay_port,
            host_relay_port=self.host_relay_port,
        )

    def stop_keeper(self) -> bool:
        from . import forward_keeper

        return forward_keeper.stop_keeper(self.name)

    def attach_command(self, plan: dict[str, Any]) -> str:
        return f"agent-containers copilot {self.name}"

    def stop_command(self, plan: dict[str, Any]) -> str:
        return f"agent-containers copilot {self.name} --stop"


def cmd_detach(
    args: argparse.Namespace,
    *,
    require_live_relay_port: Callable[[], int],
    relay_healthy: Callable[[int], bool],
    busy_exit: int = _BUSY_EXIT,
) -> int:
    from ssh_manager import TargetBusyError, TargetLock

    from .ssh_transport import cleanup_remote_env, prepare_ssh_config

    try:
        target = _resolve_target(args)
    except RuntimeError as exc:
        return _fail(str(exc))
    plan = plan_for(args, target)
    try:
        seed = read_seed(args)
    except (OSError, ValueError) as exc:
        return _fail(str(exc), plan)
    if getattr(args, "dry_run", False):
        print(json.dumps({"ok": True, "dry_run": True, **public_plan(plan), "seed_len": len(seed or "")}, indent=2))
        return 0

    target_lock = TargetLock(f"container:{args.name}", op="copilot")
    try:
        target_lock.acquire(force=getattr(args, "force", False))
    except TargetBusyError as busy:
        print(busy.user_message(), file=sys.stderr)
        return busy_exit

    remote_env: str | None = None
    try:
        relay_env, relay_port, host_relay_port = _relay_launch_env(
            args,
            target,
            require_live_relay_port=require_live_relay_port,
            relay_healthy=relay_healthy,
        )
        remote_env = _prepare_remote_env(args, target, relay_env)
        ssh_config = prepare_ssh_config(args.name, target.user)
        adapter = _ContainerAdapter(
            name=args.name,
            ssh_config=ssh_config,
            remote_env=remote_env,
            relay_port=relay_port,
            host_relay_port=host_relay_port,
        )
        _progress("prepare", "registration credentials on the container")
        rc, payload = launch_detached(
            adapter,
            plan,
            seed=seed,
            driver=args.driver,
            copilot_args=list(getattr(args, "copilot_args", None) or []),
            ensure_mux=bool(args.ensure_mux),
            register_timeout=float(args.register_timeout),
            progress=_progress,
        )
        return _emit(rc, payload)
    finally:
        if remote_env:
            try:
                cleanup_remote_env(args.name, target.user, remote_env)
            except RuntimeError:
                pass
        target_lock.release()


def cmd_stop(args: argparse.Namespace) -> int:
    from venue_copilot import live_session_for

    from . import forward_keeper
    from .ssh_transport import prepare_ssh_config

    try:
        target = _resolve_target(args)
    except RuntimeError as exc:
        return _fail(str(exc))
    plan = plan_for(args, target)
    state = forward_keeper.read_state(args.name)
    if state and state.get("mux"):
        plan["mux_session"] = str(state["mux"])
        plan["venue"]["mux_session_name"] = plan["mux_session"]
    ssh_config = prepare_ssh_config(args.name, target.user)
    adapter = _ContainerAdapter(
        name=args.name,
        ssh_config=ssh_config,
        remote_env=None,
        relay_port=None,
        host_relay_port=None,
    )
    rc, payload = stop_detached(
        adapter,
        plan,
        session_row=live_session_for(plan["scope_id"]),
    )
    return _emit(rc, payload)
