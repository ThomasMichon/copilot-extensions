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
from venue_copilot import (
    bridge_probe_script,
    last_json,
    observe_commands,
    read_seed,
    reserve_with_retry,
)

_BUSY_EXIT = 75
_RESERVATION_TTL = 900.0
_RESERVE_RETRY_WINDOW = 90.0
_PROBE_ATTEMPTS = 2


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
        "anchor": not args.worktree_id,
        "scope_id": scope,
        "mux_session": mux,
        "venue": {"kind": "container", "target": args.name, "mux_session_name": mux},
    }


def _commands(plan: dict[str, Any], session_id: str) -> dict[str, str]:
    name = plan["container"]
    return {
        **observe_commands(session_id),
        "attach": f"agent-containers copilot {name}",
        "stop": f"agent-containers copilot {name} --stop",
    }


def _fail(message: str, plan: dict[str, Any] | None = None, **extra: Any) -> int:
    print(f"[FAIL] {message}", file=sys.stderr)
    print(json.dumps({"ok": False, "error": message, **(plan or {}), **extra}, indent=2))
    return 1


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


def _bridge_path_ok(ssh_config: Any, port: int) -> bool:
    probe = bridge_probe_script(port)
    for attempt in range(_PROBE_ATTEMPTS):
        rc, _out, _err = _remote(ssh_config, probe, timeout=30.0)
        if rc == 0:
            return True
        if attempt + 1 < _PROBE_ATTEMPTS:
            time.sleep(5.0)
    return False


def _reserve(plan: dict[str, Any]) -> dict[str, Any]:
    return reserve_with_retry(
        plan["scope_id"],
        plan["venue"],
        ttl_seconds=_RESERVATION_TTL,
        retry_window=_RESERVE_RETRY_WINDOW,
        on_wait=lambda: _progress(
            "waiting", "another launch on this container holds the reservation",
        ),
    )


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


def _remote_launch_command(
    plan: dict[str, Any],
    args: argparse.Namespace,
    *,
    seed: str | None,
    remote_env: str | None,
) -> str:
    from venue_copilot import build_copilot_remote_command, seed_delivery, trust_folder_command

    from .ssh_transport import build_remote_command

    copilot_args = list(getattr(args, "copilot_args", None) or [])
    from venue_copilot import with_new_session

    copilot_args = with_new_session(copilot_args)
    typed_seed, seed_prefix = seed_delivery(seed, plan["scope_id"])
    command = seed_prefix + build_copilot_remote_command(
        plan["identity"],
        anchor=plan["anchor"],
        driver=args.driver,
        seed=typed_seed,
        ensure_mux=args.ensure_mux,
        detach=True,
        bridge_scope_id=plan["scope_id"],
        copilot_args=copilot_args,
        login_shell=False,
    )
    workspace = plan.get("workspace_folder")
    if workspace:
        command = f"cd {shlex.quote(workspace)} && {trust_folder_command(workspace)} && {command}"
    return build_remote_command(command, remote_env)


def _kill_mux(ssh_config: Any, mux: str) -> None:
    _remote(ssh_config, f"tmux kill-session -t {shlex.quote('=' + mux)} 2>/dev/null || true")


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


def cmd_detach(
    args: argparse.Namespace,
    *,
    require_live_relay_port: Callable[[], int],
    relay_healthy: Callable[[int], bool],
    busy_exit: int = _BUSY_EXIT,
) -> int:
    from venue_copilot import (
        VenueCopilotError,
        await_claim,
        registration_credentials_script,
        release_cli_mode,
        resolve_daemon_port,
        resolve_local_auth_token,
    )

    from ssh_manager import TargetBusyError, TargetLock

    from . import forward_keeper
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
        print(json.dumps({"ok": True, "dry_run": True, **plan, "seed_len": len(seed or "")}, indent=2))
        return 0

    target_lock = TargetLock(f"container:{args.name}", op="copilot")
    try:
        target_lock.acquire(force=getattr(args, "force", False))
    except TargetBusyError as busy:
        print(busy.user_message(), file=sys.stderr)
        return busy_exit

    reservation: dict[str, Any] | None = None
    remote_env: str | None = None
    created = False
    keeper_started = False
    ok = False
    ssh_config = None
    try:
        daemon_port = resolve_daemon_port()
        if not daemon_port:
            return _fail("the host agent-bridge daemon is not running (no routing table)", plan)
        token = resolve_local_auth_token()
        if not token:
            return _fail("the host agent-bridge daemon has no readable auth token", plan)

        relay_env, relay_port, host_relay_port = _relay_launch_env(
            args,
            target,
            require_live_relay_port=require_live_relay_port,
            relay_healthy=relay_healthy,
        )
        remote_env = _prepare_remote_env(args, target, relay_env)
        ssh_config = prepare_ssh_config(args.name, target.user)
        _progress("prepare", "registration credentials on the container")
        rc, _out, err = _remote(
            ssh_config,
            registration_credentials_script(token, daemon_port),
            timeout=60.0,
        )
        if rc != 0:
            return _fail(
                "could not provision registration credentials in the container",
                plan,
                detail=err.strip()[-1000:],
            )

        keeper = forward_keeper.ensure_running(
            args.name,
            venue_port=daemon_port,
            mux=plan["mux_session"],
            relay_port=relay_port,
            host_relay_port=host_relay_port,
        )
        keeper_started = bool(keeper.get("started"))
        _progress("forward", "authenticated bridge probe through the keeper")
        if not _bridge_path_ok(ssh_config, daemon_port):
            return _fail(
                "the container cannot reach the host bridge through the forward "
                "(authenticated probe failed)",
                plan,
            )
        reservation = _reserve(plan)
        _progress("reserved", reservation.get("reservation_id", ""))
        command = _remote_launch_command(plan, args, seed=seed, remote_env=remote_env)
        _progress("launch", "`agent-worktrees embody` in the container")
        from .ssh_transport import build_ssh_command

        proc = subprocess.run(
            build_ssh_command(ssh_config, command, pty=False),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=float(args.register_timeout) + 300.0,
            creationflags=no_window_flags(),
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        embodied = last_json(stdout)
        if "agent-worktrees: command not found" in stderr + stdout:
            return _fail(
                "agent-worktrees is not installed in the container; install it on "
                "the trusted fleet image before using detached CLI-mode sessions",
                plan,
            )
        if "unrecognized arguments" in stderr or "unrecognized arguments" in stdout:
            return _fail(
                "the container's agent-worktrees is too old for detached launch "
                "(--bridge-scope-id/--copilot-arg); update it in the container",
                plan,
                detail=stderr.strip()[-2000:],
            )
        if proc.returncode != 0 or not embodied.get("ok"):
            return _fail(
                f"remote embody failed: {embodied.get('error') or stderr.strip()[-2000:] or f'exit {proc.returncode}'}",
                plan,
            )
        created = bool(embodied.get("created"))
        actual_mux = embodied.get("session")
        if actual_mux and actual_mux != plan["mux_session"]:
            plan["mux_session"] = actual_mux
            plan["venue"]["mux_session_name"] = actual_mux
        if created and seed and not embodied.get("seed_submitted"):
            return _fail(
                "Copilot never reached a ready prompt, so the seed was not submitted "
                f"({embodied.get('seed_reason') or 'unknown'})",
                plan,
            )
        _progress("register", "waiting for the session to register with the host bridge")
        session_id = await_claim(plan["scope_id"], reservation["reservation_id"], args.register_timeout)
        if not session_id:
            return _fail("the session is running but never registered with the host bridge", plan)
        ok = True
        print(json.dumps({
            "ok": True,
            **plan,
            "session_id": session_id,
            "created": created,
            "resumed": not created,
            "seeded": bool(created and seed),
            "keeper": keeper,
            "commands": _commands(plan, session_id),
        }, indent=2))
        return 0
    except VenueCopilotError as exc:
        return _fail(str(exc), plan)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)
    finally:
        if reservation:
            release_cli_mode(plan["scope_id"], reservation_id=reservation.get("reservation_id"))
        if remote_env:
            try:
                cleanup_remote_env(args.name, target.user, remote_env)
            except RuntimeError:
                pass
        if not ok:
            if created and ssh_config is not None:
                _progress("cleanup", f"stopping the unrepresented session {plan['mux_session']}")
                _kill_mux(ssh_config, plan["mux_session"])
            if keeper_started:
                forward_keeper.stop_keeper(args.name)
        target_lock.release()


def cmd_stop(args: argparse.Namespace) -> int:
    from venue_copilot import deregister_live_session, live_session_for, release_cli_mode

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
    row = live_session_for(plan["scope_id"])
    session_id = row.get("session_id") if (row.get("venue") or {}).get("target") == args.name else None
    ssh_config = prepare_ssh_config(args.name, target.user)
    mux_target = shlex.quote("=" + plan["mux_session"])
    script = (
        f"tmux kill-session -t {mux_target} 2>/dev/null; sleep 1; "
        f"if tmux has-session -t {mux_target} 2>/dev/null; then echo STILL_RUNNING; exit 3; fi; "
        "echo STOPPED"
    )
    rc, out, err = _remote(ssh_config, script, timeout=120.0)
    if rc != 0 or "STOPPED" not in out:
        return _fail("could not verify the session stopped; nothing was released", plan, detail=err)
    keeper_stopped = forward_keeper.stop_keeper(args.name)
    release_cli_mode(plan["scope_id"])
    deregistered = bool(session_id) and deregister_live_session(str(session_id))
    print(json.dumps({
        "ok": True,
        "stopped": True,
        "keeper_stopped": keeper_stopped,
        "deregistered": session_id if deregistered else None,
        **plan,
    }, indent=2))
    return 0
