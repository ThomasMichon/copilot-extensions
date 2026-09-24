"""Detached reverse-forward keeper for container CLI-mode sessions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_procutil import (
    no_window_flags,
    windowless_daemon_kwargs,
    windowless_python,
    windowless_python_env,
)
from ssh_manager import SupervisedRelayForward
from ssh_manager.locks import pid_alive

from .config import RESTRICTED_PROFILE, RUNTIME_DIR
from .private_state import atomic_write_json, ensure_private_dir

_STATE_DIR = RUNTIME_DIR / "forward-keepers"


def _safe(name: str) -> str:
    return (re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "container")[:120]


def state_path(name: str) -> Path:
    return _STATE_DIR / f"{_safe(name)}.json"


def read_state(name: str) -> dict[str, Any] | None:
    try:
        data = json.loads(state_path(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(name: str, payload: dict[str, Any]) -> None:
    atomic_write_json(state_path(name), payload, indent=2, sort_keys=True)


def _state_alive(state: dict[str, Any] | None) -> bool:
    try:
        return bool(state and pid_alive(int(state.get("pid") or 0)))
    except (TypeError, ValueError):
        return False


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
        _state_alive(existing)
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
    proc = popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        **windowless_daemon_kwargs(breakaway=True),
    )
    state = {
        "pid": int(proc.pid),
        "container": name,
        "venue_port": int(venue_port),
        "mux": mux,
        "relay_port": int(relay_port) if relay_port else None,
        "host_relay_port": int(host_relay_port) if host_relay_port else None,
        "started_at": time.time(),
    }
    _write_state(name, state)
    return {"started": True, "state": state}


def _terminate_pid(pid: int) -> None:
    if pid <= 0 or not pid_alive(pid):
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=no_window_flags(),
            )
        except OSError:
            pass
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return


def stop_keeper(name: str) -> bool:
    state = read_state(name)
    if not state:
        return False
    try:
        pid = int(state.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    alive = pid_alive(pid) if pid > 0 else False
    if alive:
        _terminate_pid(pid)
    state_path(name).unlink(missing_ok=True)
    return alive


def _write_self_state(args: argparse.Namespace) -> None:
    _write_state(
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
            "started_at": time.time(),
        },
    )


def _remove_self_state(name: str) -> None:
    state = read_state(name)
    if state and int(state.get("pid") or 0) == os.getpid():
        state_path(name).unlink(missing_ok=True)


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
            creationflags=no_window_flags(),
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
    ensure_private_dir(_STATE_DIR)
    _write_self_state(args)
    try:
        for keeper in keepers:
            await keeper.start()
        startup_deadline = (
            asyncio.get_running_loop().time() + max(0.0, float(args.startup_grace))
        )
        while True:
            if await asyncio.to_thread(_mux_exists, ssh_config, args.mux):
                break
            if asyncio.get_running_loop().time() >= startup_deadline:
                return 0
            await asyncio.sleep(min(5.0, max(1.0, float(args.probe_interval))))
        while True:
            await asyncio.sleep(max(1.0, float(args.probe_interval)))
            if not await asyncio.to_thread(_mux_exists, ssh_config, args.mux):
                return 0
    finally:
        for keeper in reversed(keepers):
            await keeper.stop()
        _remove_self_state(args.name)


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
