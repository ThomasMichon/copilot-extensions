"""Detached reverse-forward keeper for container CLI-mode sessions."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

from agent_procutil import (
    windowless_daemon_kwargs,
    windowless_python,
    windowless_python_env,
)
from ssh_manager import SupervisedRelayForward
from ssh_manager.forward_keeper import KeeperStore, run_supervised_loop, spawn_keeper

from .config import RESTRICTED_PROFILE, RUNTIME_DIR

_STATE_DIR = RUNTIME_DIR / "forward-keepers"
_STORE = KeeperStore(_STATE_DIR)


def state_path(name: str) -> Path:
    return _STORE.state_path(name)


def read_state(name: str) -> dict[str, Any] | None:
    return _STORE.read(name)


def ensure_running(
    name: str,
    *,
    venue_port: int,
    mux: str,
    relay_port: int | None = None,
    host_relay_port: int | None = None,
    popen: Any = subprocess.Popen,
) -> dict[str, Any]:
    """Start a keeper unless a live one already owns this container session."""
    existing = read_state(name)
    if (
        _STORE.alive(name)
        and existing.get("mux") == mux
        and int(existing.get("venue_port") or 0) == int(venue_port)
    ):
        return {"started": False, "state": existing}
    stop_keeper(name)
    argv = [
        windowless_python(),
        "-m",
        "agent_containers",
        "forward-keeper",
        name,
        "--venue-port",
        str(int(venue_port)),
        "--mux",
        mux,
        "--startup-grace",
        "300",
    ]
    if relay_port and host_relay_port:
        argv += [
            "--relay-port",
            str(int(relay_port)),
            "--host-relay-port",
            str(int(host_relay_port)),
        ]
    env = {**os.environ, **windowless_python_env()}
    state = spawn_keeper(
        argv,
        env,
        {
            "container": name,
            "venue_port": int(venue_port),
            "mux": mux,
            "relay_port": int(relay_port) if relay_port else None,
            "host_relay_port": int(host_relay_port) if host_relay_port else None,
        },
        popen=popen,
        popen_kwargs=windowless_daemon_kwargs(breakaway=True),
    )
    _STORE.write(name, state)
    return {"started": True, "state": state}


def stop_keeper(name: str) -> bool:
    return _STORE.stop(name)


def _write_self_state(args: argparse.Namespace) -> None:
    _STORE.write(
        args.name,
        {
            "pid": os.getpid(),
            "container": args.name,
            "venue_port": int(args.venue_port),
            "mux": args.mux,
            "relay_port": int(args.relay_port) if args.relay_port else None,
            "host_relay_port": (
                int(args.host_relay_port) if args.host_relay_port else None
            ),
        },
    )


def _remove_self_state(name: str) -> None:
    state = read_state(name)
    if state and int(state.get("pid") or 0) == os.getpid():
        _STORE.remove(name)


def _mux_exists(ssh_config: Any, mux: str) -> bool:
    from .ssh_transport import build_ssh_command

    target = "=" + mux
    command = f"tmux has-session -t {target!r}"
    argv = build_ssh_command(ssh_config, command, pty=False)
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


async def _run(args: argparse.Namespace) -> int:
    from venue_copilot import resolve_daemon_port

    from .resolver import resolve_live_exec_target
    from .ssh_transport import prepare_ssh_config

    target = resolve_live_exec_target(args.name)
    if target.actual_profile == RESTRICTED_PROFILE:
        raise RuntimeError("restricted containers do not support detached CLI-mode sessions")
    ssh_config = prepare_ssh_config(args.name, target.user)
    keepers = [
        SupervisedRelayForward(
            ssh_config,
            int(args.venue_port),
            host_port_resolver=lambda: resolve_daemon_port() or 0,
            monitor_interval=15.0,
        ),
    ]
    if args.relay_port and args.host_relay_port:
        keepers.append(
            SupervisedRelayForward(
                ssh_config,
                int(args.relay_port),
                host_port_resolver=lambda: int(args.host_relay_port),
                monitor_interval=15.0,
            )
        )
    return await run_supervised_loop(
        keepers,
        session_alive=lambda: _mux_exists(ssh_config, args.mux),
        write_state=lambda: _write_self_state(args),
        remove_state=lambda: _remove_self_state(args.name),
        probe_interval=float(args.probe_interval),
        startup_grace=float(args.startup_grace),
    )


def add_subparser(sub) -> None:
    p = sub.add_parser("forward-keeper", help=argparse.SUPPRESS)
    p.add_argument("name")
    p.add_argument("--venue-port", type=int, required=True)
    p.add_argument("--mux", required=True)
    p.add_argument("--relay-port", type=int)
    p.add_argument("--host-relay-port", type=int)
    p.add_argument("--probe-interval", type=float, default=120.0)
    p.add_argument("--startup-grace", type=float, default=300.0)
    p.set_defaults(func=cmd_forward_keeper)


def cmd_forward_keeper(args: argparse.Namespace) -> int:
    return asyncio.run(_run(args))
