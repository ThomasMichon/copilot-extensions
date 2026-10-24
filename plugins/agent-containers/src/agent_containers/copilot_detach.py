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
from venue_copilot.models import model_copilot_args
from venue_copilot.supervisor import with_supervisor
from venue_copilot.refs import upload_for

_BUSY_EXIT = 75
_RESERVATION_TTL = 900.0
_RESERVE_RETRY_WINDOW = 90.0
_DISPATCH_ENV_KEYS = (
    "AGENT_DISPATCH_URL",
    "AGENT_DISPATCH_TOKEN",
    "AGENT_DISPATCH_SHARED_URL",
    "AGENT_DISPATCH_SHARED_TOKEN",
    "AGENT_DISPATCH_SHARED_TOKEN_COMMAND",
    "AGENT_DISPATCH_ENDPOINT",
    "AGENT_DISPATCH_SUPERVISE_MACHINE",
    "AGENT_DISPATCH_FAILOVER_MACHINE",
    "AGENT_DISPATCH_WSL_WINDOWS_CLIENT",
    "AGENT_DISPATCH_NO_AUTOSTART",
)


def _with_caller_model(requested: list[str]) -> list[str]:
    """The caller's `--copilot-arg` list plus its own model / reasoning effort /
    context tier (`venue_copilot.models`), so the worker doesn't silently run on
    the venue's CLI defaults; an explicitly passed flag wins."""
    return requested + model_copilot_args(requested)


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
            "agent-worktrees was still not runnable in the container after the "
            "pre-launch provisioning step"
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
    print(
        json.dumps({"ok": False, "error": message, **public_plan(plan or {}), **extra}, indent=2)
    )
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


def _remote_input(
    ssh_config: Any, command: str, stdin: bytes, *, timeout: float
) -> tuple[int, str, str]:
    from .ssh_transport import build_ssh_command

    result = subprocess.run(
        build_ssh_command(ssh_config, _bash(command), pty=False),
        input=stdin,
        capture_output=True,
        timeout=timeout,
        creationflags=no_window_flags(),
    )
    return (
        result.returncode,
        (result.stdout or b"").decode("utf-8", "replace"),
        (result.stderr or b"").decode("utf-8", "replace"),
    )


def _launch_env(
    args: argparse.Namespace,
    target: Any,
    *,
    require_live_relay_port: Callable[[], int],
    relay_healthy: Callable[[int], bool],
) -> tuple[dict[str, str], int | None, int | None]:
    config = target.config
    forward, relay_enabled = config.credentials_for(target.fleet)
    env: dict[str, str] = {}
    if forward:
        from .resolver import host_gh_token

        github_token = host_gh_token()
        if not github_token:
            raise RuntimeError(
                "forward_gh_token is enabled but `gh auth token` returned nothing; "
                "the container's Copilot CLI would start signed out"
            )
        env["GH_TOKEN"] = github_token
    for key in _DISPATCH_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            env[key] = value
    from .container_shims import ensure_agent_worktrees
    from .container_shims import ensure_agent_worktrees_workspace_registered
    from .container_shims import deploy as deploy_shims
    from .container_shims import git_credential_environment
    from .relay_provider import token_for

    ensure_agent_worktrees(args.name, user=target.user)
    if getattr(target, "workspace_folder", ""):
        ensure_agent_worktrees_workspace_registered(
            args.name,
            user=target.user,
            workspace_folder=target.workspace_folder,
        )
    if getattr(args, "no_relay", False) or not relay_enabled:
        return env, None, None
    host_relay_port = require_live_relay_port()
    if not relay_healthy(host_relay_port):
        raise RuntimeError(
            "Published credential relay at "
            f"127.0.0.1:{host_relay_port} failed its ping probe; "
            "restart agent-bridge or set relay.enabled: false in containers.yaml"
        )
    deploy_shims(args.name, ado=True)
    env.update(
        {
            "LC_GIT_CREDENTIAL_RELAY_HOST": "127.0.0.1",
            "LC_GIT_CREDENTIAL_RELAY": str(config.relay_port),
            "LC_GIT_CREDENTIAL_RELAY_TOKEN": token_for(args.name),
            **git_credential_environment(),
        }
    )
    return env, int(config.relay_port), int(host_relay_port)


def _prepare_remote_env(
    args: argparse.Namespace, target: Any, values: dict[str, str]
) -> str | None:
    if not values:
        return None
    from .ssh_transport import container_environment, write_remote_env

    launch_env = container_environment(args.name, target.user)
    launch_env.update(values)
    from .copilot_process_auth import stage_env

    return stage_env(
        args.name, target.user, launch_env, write_remote_env,
        config=target.config, fleet=target.fleet,
    )


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
        hold_id: str,
        remote_env: str | None,
        relay_port: int | None,
        host_relay_port: int | None,
        target: Any = None,
    ) -> None:
        self.name = name
        self.ssh_config = ssh_config
        self.hold_id = hold_id
        self.remote_env = remote_env
        self.relay_port = relay_port
        self.host_relay_port = host_relay_port
        self.target = target
        self.inputs_safe_to_cleanup = True
        self._hold_updated_at: float | None = None
        self.plugin_args = None
        self._plugins_prepared = False
        self.pending_delivery = False

    def record_pending_delivery(self, plan, pending):
        from . import forward_keeper
        if pending is not None:
            pending = {
                **pending, "remote_env": self.remote_env,
                "relay_port": self.relay_port, "host_relay_port": self.host_relay_port,
            }
        forward_keeper.record_pending_delivery(self.name, self.hold_id, pending)
        self.pending_delivery = pending is not None

    def prepare(self, plan):
        from .plugin_preparation import prepare_plugins
        from venue_copilot.refs import require_cli_context
        context = {}
        prepare_plugins(
            self.plugin_args, self.target, run_input=self.run_input,
            remote_env=self.remote_env, relay_port=self.relay_port, prepared_context=context,
        )
        self._plugins_prepared = True
        return require_cli_context(context.get("plugin_context"))

    def run(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        return _remote(self.ssh_config, command, timeout=timeout)

    def run_input(self, command: str, stdin: bytes, *, timeout: float) -> tuple[int, str, str]:
        return _remote_input(self.ssh_config, command, stdin, timeout=timeout)

    def launch(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        from .ssh_transport import build_remote_command, build_ssh_command
        from .copilot_process_auth import (
            created_child, prepare_launch, wait_for_consumption,
            without_inference_token, wrap_command,
        )
        from .ssh_transport import cleanup_remote_env

        auth_env = None
        try:
            if self.plugin_args is not None and not self._plugins_prepared:
                self.prepare({})
            if self.target is not None:
                auth_env = prepare_launch(
                    self.target.config, self.target.fleet, self.name, self.target.user,
                )
            command = wrap_command(command, auth_env, manager=True)
            remote_command = build_remote_command(command, self.remote_env)
            ssh_command = build_ssh_command(self.ssh_config, remote_command, pty=False)
            self.inputs_safe_to_cleanup = auth_env is None
            proc = subprocess.run(
                ssh_command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=no_window_flags(),
                env=without_inference_token(dict(os.environ)),
            )
            if self.target is not None and auth_env:
                try:
                    result = json.loads(proc.stdout)
                except (ValueError, TypeError) as exc:
                    if proc.returncode == 0:
                        raise RuntimeError("Copilot embodiment returned an invalid launch receipt") from exc
                else:
                    if created_child(result):
                        wait_for_consumption(self.name, self.target.user, auth_env, timeout=timeout)
                    self.inputs_safe_to_cleanup = True
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        finally:
            if self.target is not None and self.inputs_safe_to_cleanup:
                cleanup_remote_env(self.name, self.target.user, auth_env)
            elif auth_env:
                print(
                    "[WARN] Retaining unconfirmed Copilot launch inputs for bounded abandoned-input cleanup.",
                    file=sys.stderr,
                )

    def ensure_keeper(self, *, venue_port: int, mux: str) -> dict[str, Any]:
        from . import forward_keeper

        result = forward_keeper.ensure_running(
            self.name,
            venue_port=venue_port,
            mux=mux,
            hold_id=self.hold_id,
            relay_port=self.relay_port,
            host_relay_port=self.host_relay_port,
            mux_alive=lambda held_mux: forward_keeper._mux_exists(self.ssh_config, held_mux),
        )
        raw_updated = result.get("hold_updated_at")
        self._hold_updated_at = float(raw_updated) if raw_updated is not None else None
        return result

    def stop_keeper(self) -> bool:
        from . import forward_keeper

        return forward_keeper.stop_keeper(
            self.name,
            hold_id=self.hold_id,
            mux_alive=lambda held_mux: forward_keeper._mux_exists(self.ssh_config, held_mux),
            expected_updated_at=self._hold_updated_at,
        )

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
    plan["venue"] = with_supervisor(plan["venue"])  # successor can find this worker
    try:
        seed = read_seed(args)
        refs = upload_for(list(getattr(args, "ref_files", None) or []), plan["scope_id"])
    except (OSError, ValueError) as exc:
        return _fail(str(exc), plan)
    if getattr(args, "dry_run", False):
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    **public_plan(plan),
                    "seed_len": len(seed or ""),
                    "ref_files": [name for name, _ in refs[2]] if refs else [],
                },
                indent=2,
            )
        )
        return 0

    from venue_copilot import live_session_for
    from . import forward_keeper
    from .hosting_policy import require_container_hosting

    rejoining = (live_session_for(plan["scope_id"]) or {}).get("status") == "live"
    args.hosting_live_attachment = rejoining
    retained = (forward_keeper.read_state(args.name) or {}).get("pending_deliveries") or {}
    recovering = bool(retained.get(plan["scope_id"]))
    try:
        if not recovering:
            require_container_hosting(
                target, worktree_id=getattr(args, "worktree_id", None), live_attachment=rejoining,
            )
    except RuntimeError as exc:
        return _fail(str(exc), plan)
    target_lock = TargetLock(f"container:{args.name}", op="copilot")
    try:
        target_lock.acquire(force=getattr(args, "force", False))
    except TargetBusyError as busy:
        print(busy.user_message(), file=sys.stderr)
        return busy_exit

    remote_env: str | None = None
    adapter: _ContainerAdapter | None = None
    try:
        held = forward_keeper.read_state(args.name) or {}
        pending = (held.get("pending_deliveries") or {}).get(plan["scope_id"])
        if pending:
            from venue_copilot.refs import reconcile_owned_delivery, settle_deferred_seed
            ssh_config = prepare_ssh_config(args.name, target.user)
            if pending.get("status") != "admitted":
                from venue_copilot.detached import _bridge_path_ok
                port = pending.get("daemon_port", held.get("venue_port"))
                mux = pending.get("mux_session") or (held.get("holds", {}).get(
                    plan["scope_id"], {}).get("mux"))
                try:
                    if type(port) is not int or not 0 < port <= 65535 or not isinstance(mux, str) or not mux:
                        raise RuntimeError("Pending delivery lacks its original native forward metadata")
                    adapter = _ContainerAdapter(
                        name=args.name, ssh_config=ssh_config, hold_id=plan["scope_id"],
                        remote_env=pending.get("remote_env"),
                        relay_port=pending.get("relay_port", held.get("relay_port")),
                        host_relay_port=pending.get("host_relay_port", held.get("host_relay_port")),
                    )
                    adapter.ensure_keeper(venue_port=port, mux=mux)
                    if not _bridge_path_ok(adapter, port):
                        raise RuntimeError("The retained native session's bridge forward is unavailable")
                except (RuntimeError, OSError) as exc:
                    return _emit(busy_exit, {
                        "ok": False, **public_plan(plan), "pending": True,
                        "session_id": pending["session_id"], "error": str(exc),
                    })
            result = reconcile_owned_delivery(pending)
            def settle(command, stdin):
                return _remote_input(
                    ssh_config, command, stdin, timeout=60,
                )
            if result.status == "admitted" and settle_deferred_seed(
                settle, plan["identity"], pending["envelope"]["plugin_context"]["cwd"],
                pending.get("creation_receipt"),
            ):
                forward_keeper.record_pending_delivery(args.name, plan["scope_id"], None)
                if pending.get("remote_env"):
                    cleanup_remote_env(args.name, target.user, pending["remote_env"])
                from venue_copilot import release_cli_mode
                release_cli_mode(plan["scope_id"], reservation_id=pending.get("reservation_id"))
                return _emit(0, {"ok": True, **public_plan(plan), "session_id": result.session_id,
                                 "recovered": True, "seed_admitted": True, "message_id": result.message_id})
            pending.update(result.recovery())
            forward_keeper.record_pending_delivery(args.name, plan["scope_id"], pending)
            return _emit(_BUSY_EXIT, {
                "ok": False, **public_plan(plan), "pending": True, "session_id": pending["session_id"],
                "pending_delivery": result.recovery(),
                "error": "owned delivery remains pending; original native session and ownership retained",
            })
        try:
            if recovering:
                require_container_hosting(
                    target, worktree_id=getattr(args, "worktree_id", None), live_attachment=rejoining,
                )
            launch_env, relay_port, host_relay_port = _launch_env(
                args,
                target,
                require_live_relay_port=require_live_relay_port,
                relay_healthy=relay_healthy,
            )
            remote_env = _prepare_remote_env(args, target, launch_env)
            ssh_config = prepare_ssh_config(args.name, target.user)
        except RuntimeError as exc:
            return _fail(str(exc), plan)
        adapter = _ContainerAdapter(
            name=args.name,
            ssh_config=ssh_config,
            hold_id=plan["scope_id"],
            remote_env=remote_env,
            relay_port=relay_port,
            host_relay_port=host_relay_port,
            target=target,
        )
        _progress("prepare", "registration credentials on the container")
        adapter.plugin_args = args
        plan["bridge_owned_seed"] = True
        rc, payload = launch_detached(
            adapter,
            plan,
            seed=seed,
            driver=args.driver,
            copilot_args=_with_caller_model(list(getattr(args, "copilot_args", None) or [])),
            ensure_mux=bool(args.ensure_mux),
            register_timeout=float(args.register_timeout),
            progress=_progress,
            refs=refs,
        )
        return _emit(rc, payload)
    finally:
        if remote_env and (adapter is None or (
            adapter.inputs_safe_to_cleanup and not adapter.pending_delivery
        )):
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
    if held_mux := forward_keeper.hold_mux(args.name, plan["scope_id"]):
        plan["mux_session"] = held_mux
        plan["venue"]["mux_session_name"] = held_mux
    ssh_config = prepare_ssh_config(args.name, target.user)
    adapter = _ContainerAdapter(
        name=args.name,
        ssh_config=ssh_config,
        hold_id=plan["scope_id"],
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
