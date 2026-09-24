"""Detached, observable CLI-mode Copilot sessions on POSIX SSH targets."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_procutil import (
    no_window_flags,
    windowless_daemon_kwargs,
    windowless_python,
    windowless_python_env,
)
from ssh_manager import SSHProfileSource, SupervisedRelayForward, build_remote_exec_args
from ssh_manager.forward_keeper import KeeperStore, run_supervised_loop, spawn_keeper
from venue_copilot import (
    read_seed,
    resolve_daemon_port,
)
from venue_copilot.detached import launch_detached, public_plan, stop_detached

_RESERVATION_TTL = 900.0
_RESERVE_RETRY_WINDOW = 90.0
_PROBE_ATTEMPTS = 2
_STATE_DIR = Path.home() / ".agent-ssh" / "forward-keepers"
_STORE = KeeperStore(_STATE_DIR)


def _progress(stage: str, detail: str = "") -> None:
    print(f"[DETACH] {stage}{': ' + detail if detail else ''}", file=sys.stderr, flush=True)


def plan_for(args: argparse.Namespace) -> dict[str, Any]:
    workspace = str(args.workspace).rstrip("/")
    identity = f"anchor-{os.path.basename(workspace) or args.target}"
    scope = f"{identity}@{args.target}"
    mux = f"wt-{identity}"
    return {
        "target": args.target,
        "identity": identity,
        "workspace": workspace,
        "scope_id": scope,
        "mux_session": mux,
        "venue": {"kind": "ssh", "target": args.target, "mux_session_name": mux},
        "anchor": True,
        "reservation_ttl": _RESERVATION_TTL,
        "reserve_retry_window": _RESERVE_RETRY_WINDOW,
        "registration_error": "could not provision registration credentials on the SSH target",
        "bridge_probe_error": (
            "the SSH target cannot reach the host bridge through the forward "
            "(authenticated probe failed)"
        ),
        "missing_agent_worktrees_error": "agent-worktrees is not installed on the SSH target",
        "old_agent_worktrees_error": (
            "the SSH target's agent-worktrees is too old for detached launch "
            "(--bridge-scope-id/--copilot-arg); update agent-worktrees"
        ),
        "reservation_wait": "another launch on this SSH target holds the reservation",
        "launch_detail": "`agent-worktrees embody` on the SSH target",
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


def _ssh_config(target: str) -> Any:
    return SSHProfileSource(target).get_ssh_config()


def _remote(
    ssh_config: Any,
    command: str,
    *,
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    argv = build_remote_exec_args(ssh_config, command, pty=False)
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=no_window_flags(),
    )
    return result.returncode, result.stdout or "", result.stderr or ""


def _bash(command: str) -> str:
    return f"bash -lc {shlex.quote(command)}"


def _ensure_posix(ssh_config: Any) -> None:
    rc, _out, err = _remote(ssh_config, "sh -lc 'printf posix'", timeout=30.0)
    if rc != 0:
        raise RuntimeError(
            "Windows SSH targets are not supported yet; run the orchestrator on "
            "that machine and use a local `agent-worktrees embody`"
            + (f" ({err.strip()})" if err.strip() else "")
        )


def _ensure_remote_tooling(ssh_config: Any) -> None:
    script = (
        "command -v bash >/dev/null && command -v tmux >/dev/null && "
        "command -v agent-worktrees >/dev/null && command -v copilot >/dev/null && "
        "find ~/.copilot/installed-plugins -type d -name agent-bridge -print -quit "
        "| grep -q ."
    )
    rc, _out, err = _remote(ssh_config, _bash(script), timeout=60.0)
    if rc != 0:
        raise RuntimeError(
            "the SSH target must already have bash, tmux, copilot, "
            "agent-worktrees, and the agent-bridge Copilot plugin installed"
            + (f" ({err.strip()})" if err.strip() else "")
        )


def _state_key(target: str) -> str:
    return target


def read_keeper_state(target: str) -> dict[str, Any] | None:
    return _STORE.read(_state_key(target))


def stop_keeper(target: str) -> bool:
    return _STORE.stop(_state_key(target))


def ensure_keeper(target: str, *, venue_port: int, mux: str) -> dict[str, Any]:
    state = read_keeper_state(target)
    if (
        _STORE.alive(_state_key(target))
        and state
        and state.get("mux") == mux
        and int(state.get("venue_port") or 0) == int(venue_port)
    ):
        return {"started": False, "state": state}
    stop_keeper(target)
    argv = [
        windowless_python(),
        "-m",
        "agent_ssh",
        "forward-keeper",
        target,
        "--venue-port",
        str(int(venue_port)),
        "--mux",
        mux,
        "--startup-grace",
        "300",
    ]
    state = spawn_keeper(
        argv,
        {**os.environ, **windowless_python_env()},
        {"target": target, "venue_port": int(venue_port), "mux": mux},
        popen_kwargs=windowless_daemon_kwargs(breakaway=True),
    )
    _STORE.write(_state_key(target), state)
    return {"started": True, "state": state}


class _SshAdapter:
    probe_attempts = _PROBE_ATTEMPTS

    def __init__(self, *, target: str, ssh_config: Any) -> None:
        self.target = target
        self.ssh_config = ssh_config

    def run(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        return _remote(self.ssh_config, _bash(command), timeout=timeout)

    def launch(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        return self.run(command, timeout=timeout)

    def ensure_keeper(self, *, venue_port: int, mux: str) -> dict[str, Any]:
        return ensure_keeper(self.target, venue_port=venue_port, mux=mux)

    def stop_keeper(self) -> bool:
        return stop_keeper(self.target)

    def attach_command(self, plan: dict[str, Any]) -> str:
        return (
            f"ssh -t {shlex.quote(self.target)} tmux attach -t "
            f"{shlex.quote(plan['mux_session'])}"
        )

    def stop_command(self, plan: dict[str, Any]) -> str:
        return (
            f"agent-ssh copilot {shlex.quote(self.target)} --stop "
            f"--workspace {shlex.quote(plan['workspace'])}"
        )


def cmd_detach(args: argparse.Namespace) -> int:
    plan = plan_for(args)
    try:
        seed = read_seed(args)
    except (OSError, ValueError) as exc:
        return _fail(str(exc), plan)
    if getattr(args, "dry_run", False):
        print(json.dumps({"ok": True, "dry_run": True, **public_plan(plan), "seed_len": len(seed or "")}, indent=2))
        return 0

    ssh_config = _ssh_config(args.target)
    try:
        _ensure_posix(ssh_config)
        _ensure_remote_tooling(ssh_config)
        rc, payload = launch_detached(
            _SshAdapter(target=args.target, ssh_config=ssh_config),
            plan,
            seed=seed,
            driver=args.driver,
            copilot_args=list(getattr(args, "copilot_args", None) or []),
            ensure_mux=True,
            register_timeout=float(args.register_timeout),
            progress=_progress,
        )
        return _emit(rc, payload)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)


def cmd_stop(args: argparse.Namespace) -> int:
    from venue_copilot import live_session_for

    plan = plan_for(args)
    state = read_keeper_state(args.target)
    if state and state.get("mux"):
        plan["mux_session"] = str(state["mux"])
        plan["venue"]["mux_session_name"] = plan["mux_session"]
    row = live_session_for(plan["scope_id"])
    ssh_config = _ssh_config(args.target)
    try:
        _ensure_posix(ssh_config)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)
    rc, payload = stop_detached(
        _SshAdapter(target=args.target, ssh_config=ssh_config),
        plan,
        session_row=row,
    )
    return _emit(rc, payload)


def _mux_exists(ssh_config: Any, mux: str) -> bool:
    rc, _out, _err = _remote(ssh_config, f"tmux has-session -t {shlex.quote('=' + mux)}", timeout=60.0)
    return rc == 0


async def _run_forward_keeper(args: argparse.Namespace) -> int:
    ssh_config = _ssh_config(args.target)
    forwards = [
        SupervisedRelayForward(
            ssh_config,
            int(args.venue_port),
            host_port_resolver=lambda: resolve_daemon_port() or 0,
            monitor_interval=15.0,
        )
    ]
    return await run_supervised_loop(
        forwards,
        session_alive=lambda: _mux_exists(ssh_config, args.mux),
        write_state=lambda: _STORE.write(
            _state_key(args.target),
            {
                "pid": os.getpid(),
                "target": args.target,
                "venue_port": int(args.venue_port),
                "mux": args.mux,
            },
        ),
        remove_state=lambda: _STORE.remove(_state_key(args.target)),
        probe_interval=float(args.probe_interval),
        startup_grace=float(args.startup_grace),
    )


def cmd_forward_keeper(args: argparse.Namespace) -> int:
    import asyncio

    return asyncio.run(_run_forward_keeper(args))


def add_copilot_subparser(sub) -> None:
    p = sub.add_parser("copilot", help="Start/stop detached CLI-mode Copilot on a POSIX SSH target.")
    p.add_argument("target", help="SSH host alias")
    p.add_argument("--workspace", required=True, help="Remote checkout path")
    p.add_argument("--seed")
    p.add_argument("--seed-file", dest="seed_file")
    p.add_argument("--copilot-arg", dest="copilot_args", action="append", default=[])
    p.add_argument("--driver", default="cli-mode")
    p.add_argument("--register-timeout", type=float, default=180.0)
    p.add_argument("--dry-run", action="store_true")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--detach", action="store_true")
    mode.add_argument("--stop", action="store_true")
    p.set_defaults(func=lambda args: cmd_stop(args) if args.stop else cmd_detach(args))


def add_forward_keeper_subparser(sub) -> None:
    p = sub.add_parser("forward-keeper", help=argparse.SUPPRESS)
    p.add_argument("target")
    p.add_argument("--venue-port", type=int, required=True)
    p.add_argument("--mux", required=True)
    p.add_argument("--probe-interval", type=float, default=120.0)
    p.add_argument("--startup-grace", type=float, default=300.0)
    p.set_defaults(func=cmd_forward_keeper)
