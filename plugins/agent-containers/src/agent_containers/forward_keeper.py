"""Detached reverse-forward keeper for container CLI-mode sessions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agent_procutil import (
    no_window_flags,
    windowless_daemon_kwargs,
    windowless_python,
    windowless_python_env,
)
from ssh_manager import SupervisedRelayForward
from ssh_manager.forward_keeper import KeeperStore, run_supervised_loop, spawn_keeper
from ssh_manager.locks import pid_alive, process_identity

from .config import RESTRICTED_PROFILE, RUNTIME_DIR

_STATE_DIR = RUNTIME_DIR / "forward-keepers"
_STORE = KeeperStore(_STATE_DIR)
_KEEPER_PROTOCOL = 2
_HOLD_STARTUP_GRACE = 300.0
_UNKNOWN_HOLD_GRACE = 1800.0
_LOCK_TIMEOUT = 10.0
_LOCK_POLL = 0.05

MuxProbe = Callable[[str], bool | None]


def state_path(name: str) -> Path:
    return _STORE.state_path(name)


def read_state(name: str) -> dict[str, Any] | None:
    return _STORE.read(name)


@contextmanager
def _keeper_lock(name: str) -> Iterator[None]:
    _STORE.state_dir.mkdir(parents=True, exist_ok=True)
    lock = _STORE.state_path(name).with_suffix(".lock")
    owner = {
        "pid": os.getpid(),
        "identity": process_identity(os.getpid()),
        "token": uuid.uuid4().hex,
        "created_at": time.time(),
    }
    deadline = time.monotonic() + _LOCK_TIMEOUT
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(owner, stream)
            break
        except (FileExistsError, PermissionError):
            if time.monotonic() >= deadline:
                if _lock_owner_dead(lock):
                    if _unlink_lock(lock):
                        continue
                raise RuntimeError("Could not acquire forward-keeper state lock") from None
            time.sleep(_LOCK_POLL)
    try:
        yield
    finally:
        _release_lock(lock, owner)


def _lock_owner_dead(lock: Path) -> bool:
    try:
        raw = json.loads(lock.read_text(encoding="utf-8"))
        pid = int(raw.get("pid") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    identity = raw.get("identity")
    if isinstance(identity, str) and identity:
        return process_identity(pid) != identity
    return not pid_alive(pid)


def _release_lock(lock: Path, owner: dict[str, Any]) -> None:
    try:
        raw = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return
    if raw.get("token") == owner["token"]:
        _unlink_lock(lock)


def _unlink_lock(lock: Path, attempts: int = 3) -> bool:
    for attempt in range(max(1, attempts)):
        try:
            lock.unlink(missing_ok=True)
            return True
        except PermissionError:
            if attempt + 1 >= max(1, attempts):
                return False
            time.sleep(_LOCK_POLL)
    return False


def _same_forward(
    state: dict[str, Any],
    *,
    venue_port: int,
    relay_port: int | None,
    host_relay_port: int | None,
) -> bool:
    return (
        int(state.get("venue_port") or 0) == int(venue_port)
        and (int(state["relay_port"]) if state.get("relay_port") else None)
        == (int(relay_port) if relay_port else None)
        and (int(state["host_relay_port"]) if state.get("host_relay_port") else None)
        == (int(host_relay_port) if host_relay_port else None)
    )


def _read_holds(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not state:
        return {}
    raw = state.get("holds")
    holds: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for hold_id, hold in raw.items():
            if not isinstance(hold_id, str) or not isinstance(hold, dict):
                continue
            mux = hold.get("mux")
            if not isinstance(mux, str) or not mux:
                continue
            try:
                updated_at = float(hold.get("updated_at", 0.0))
            except (TypeError, ValueError):
                updated_at = 0.0
            normalized: dict[str, Any] = {"mux": mux, "updated_at": updated_at}
            try:
                confirmed_at = float(hold.get("confirmed_at", 0.0))
            except (TypeError, ValueError):
                confirmed_at = 0.0
            if confirmed_at:
                normalized["confirmed_at"] = confirmed_at
            holds[hold_id] = normalized
    if raw is None and not holds and isinstance(state.get("mux"), str) and state["mux"]:
        try:
            updated_at = float(state.get("started_at", 0.0))
        except (TypeError, ValueError):
            updated_at = 0.0
        holds[str(state["mux"])] = {"mux": str(state["mux"]), "updated_at": updated_at}
    return holds


def _probe_holds(
    holds: dict[str, dict[str, Any]],
    *,
    mux_alive: MuxProbe | None,
    startup_grace: float,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    now = time.time()
    kept: dict[str, dict[str, Any]] = {}
    live_muxes: set[str] = set()
    for hold_id, hold in holds.items():
        mux = str(hold.get("mux") or "")
        if not mux:
            continue
        try:
            updated_at = float(hold.get("updated_at", 0.0))
        except (TypeError, ValueError):
            updated_at = 0.0
        try:
            confirmed_at = float(hold.get("confirmed_at", 0.0))
        except (TypeError, ValueError):
            confirmed_at = 0.0
        if mux_alive is None:
            kept[hold_id] = hold
            continue
        try:
            verdict = mux_alive(mux)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            verdict = None
        if verdict is True:
            kept[hold_id] = {**hold, "confirmed_at": now}
            live_muxes.add(mux)
        elif now - updated_at <= startup_grace:
            kept[hold_id] = hold
        elif (
            verdict is None
            and confirmed_at > 0
            and now - confirmed_at <= _UNKNOWN_HOLD_GRACE
        ):
            kept[hold_id] = hold
    return kept, live_muxes


def _prune_snapshot(
    name: str,
    *,
    mux_alive: MuxProbe | None,
    startup_grace: float = _HOLD_STARTUP_GRACE,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], set[str]]:
    with _keeper_lock(name):
        state = read_state(name) or {}
        holds = _read_holds(state)
        if state and not holds and int(state.get("pid") or 0) == os.getpid():
            _STORE.remove(name)
            return state, {}, set()
        holds, confirmed_changed = _confirm_missing_holds(holds)
        if state and confirmed_changed:
            _STORE.write(name, _state_with_holds(state, holds))
    kept, live_muxes = _probe_holds(
        holds,
        mux_alive=mux_alive,
        startup_grace=startup_grace,
    )
    stale = {
        hold_id: hold
        for hold_id, hold in holds.items()
        if hold_id not in kept
    }
    refreshed = {
        hold_id: hold
        for hold_id, hold in kept.items()
        if hold != holds.get(hold_id)
    }
    if not stale and not refreshed:
        return state, kept, live_muxes
    with _keeper_lock(name):
        current = read_state(name) or {}
        current_holds = _read_holds(current)
        for hold_id, stale_hold in stale.items():
            current_hold = current_holds.get(hold_id)
            if (
                current_hold
                and current_hold.get("updated_at") == stale_hold.get("updated_at")
            ):
                current_holds.pop(hold_id, None)
        for hold_id, refreshed_hold in refreshed.items():
            original = holds.get(hold_id) or {}
            current_hold = current_holds.get(hold_id)
            if (
                current_hold
                and current_hold.get("updated_at") == original.get("updated_at")
            ):
                current_holds[hold_id] = {**current_hold, **refreshed_hold}
        if current:
            if not current_holds and int(current.get("pid") or 0) == os.getpid():
                _STORE.remove(name)
            else:
                _STORE.write(name, _state_with_holds(current, current_holds))
        return current, current_holds, {
            mux
            for hold in current_holds.values()
            for mux in [str(hold.get("mux") or "")]
            if mux in live_muxes
        }


def _hold_live(
    hold: dict[str, Any],
    *,
    now: float,
    mux_alive: MuxProbe | None,
    startup_grace: float,
) -> bool:
    mux = str(hold.get("mux") or "")
    if not mux:
        return False
    if mux_alive is None:
        return True
    try:
        verdict = mux_alive(mux)
        if verdict is True:
            return True
        if verdict is None:
            return True
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return True
    try:
        updated_at = float(hold.get("updated_at", 0.0))
    except (TypeError, ValueError):
        updated_at = 0.0
    return now - updated_at <= startup_grace


def _prune_holds(
    holds: dict[str, dict[str, Any]],
    *,
    mux_alive: MuxProbe | None,
    startup_grace: float = _HOLD_STARTUP_GRACE,
) -> dict[str, dict[str, Any]]:
    now = time.time()
    return {
        hold_id: hold
        for hold_id, hold in holds.items()
        if _hold_live(
            hold,
            now=now,
            mux_alive=mux_alive,
            startup_grace=startup_grace,
        )
    }


def _state_with_holds(state: dict[str, Any], holds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = {**state, "holds": holds}
    if holds:
        out["mux"] = next(iter(holds.values()))["mux"]
    else:
        out.pop("mux", None)
    return out


def _confirm_missing_holds(
    holds: dict[str, dict[str, Any]],
    *,
    now: float | None = None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    confirmed_now = time.time() if now is None else now
    changed = False
    confirmed: dict[str, dict[str, Any]] = {}
    for hold_id, hold in holds.items():
        if hold.get("confirmed_at"):
            confirmed[hold_id] = hold
        else:
            confirmed[hold_id] = {**hold, "confirmed_at": confirmed_now}
            changed = True
    return confirmed, changed


def hold_mux(name: str, hold_id: str) -> str | None:
    with _keeper_lock(name):
        state = read_state(name)
        hold = _read_holds(state).get(hold_id)
    mux = hold.get("mux") if hold else None
    return str(mux) if isinstance(mux, str) and mux else None


def list_holds(
    name: str,
    *,
    mux_alive: Callable[[str], bool] | None = None,
    prune: bool = True,
) -> dict[str, dict[str, Any]]:
    if prune:
        _state, holds, _live_muxes = _prune_snapshot(name, mux_alive=mux_alive)
        return holds
    with _keeper_lock(name):
        return _read_holds(read_state(name) or {})


def ensure_running(
    name: str,
    *,
    venue_port: int,
    mux: str,
    hold_id: str | None = None,
    relay_port: int | None = None,
    host_relay_port: int | None = None,
    mux_alive: MuxProbe | None = None,
    popen: Any = subprocess.Popen,
) -> dict[str, Any]:
    """Start a keeper unless a live one already owns this container forward."""
    hold_id = hold_id or mux
    _prune_snapshot(name, mux_alive=mux_alive)
    with _keeper_lock(name):
        existing = read_state(name) or {}
        holds = _read_holds(existing)
        holds[hold_id] = {"mux": mux, "updated_at": time.time()}
        can_reuse = (
            existing.get("keeper_protocol") == _KEEPER_PROTOCOL
            and _STORE.alive(name)
            and _same_forward(
                existing,
                venue_port=venue_port,
                relay_port=relay_port,
                host_relay_port=host_relay_port,
            )
        )
        if can_reuse:
            state = _state_with_holds(existing, holds)
            _STORE.write(name, state)
            return {"started": False, "state": state}
        if existing:
            _STORE.stop(name)
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
            "--hold-id",
            hold_id,
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
                "keeper_protocol": _KEEPER_PROTOCOL,
                "container": name,
                "venue_port": int(venue_port),
                "mux": mux,
                "holds": holds,
                "relay_port": int(relay_port) if relay_port else None,
                "host_relay_port": int(host_relay_port) if host_relay_port else None,
            },
            popen=popen,
            popen_kwargs=windowless_daemon_kwargs(breakaway=True),
        )
        current_holds = _read_holds(read_state(name) or {})
        current_holds.update(holds)
        state = _state_with_holds({**state, "holds": current_holds}, current_holds)
        _STORE.write(name, state)
        return {"started": True, "state": state}


def stop_keeper(
    name: str,
    *,
    hold_id: str | None = None,
    mux_alive: MuxProbe | None = None,
) -> bool:
    if hold_id is None:
        with _keeper_lock(name):
            return _STORE.stop(name)
    with _keeper_lock(name):
        state = read_state(name)
        if not state:
            return False
        holds = _read_holds(state)
        holds.pop(hold_id, None)
        _STORE.write(name, _state_with_holds(state, holds))
        if not holds:
            return _STORE.stop(name)
    _prune_snapshot(name, mux_alive=mux_alive)
    with _keeper_lock(name):
        state = read_state(name)
        if not state:
            return False
        holds = _read_holds(state)
        if holds:
            _STORE.write(name, _state_with_holds(state, holds))
            return False
        return _STORE.stop(name)


def _write_self_state(args: argparse.Namespace) -> None:
    with _keeper_lock(args.name):
        existing = read_state(args.name) or {}
        holds = _read_holds(existing)
        now = time.time()
        if not holds and args.hold_id:
            holds[str(args.hold_id)] = {
                "mux": args.mux,
                "updated_at": now,
                "confirmed_at": now,
            }
        holds = _confirm_missing_holds(holds, now=now)[0]
        _STORE.write(
            args.name,
            _state_with_holds(
                {
                    **existing,
                    "keeper_protocol": _KEEPER_PROTOCOL,
                    "pid": os.getpid(),
                    "pid_identity": process_identity(os.getpid()),
                    "container": args.name,
                    "venue_port": int(args.venue_port),
                    "relay_port": int(args.relay_port) if args.relay_port else None,
                    "host_relay_port": (
                        int(args.host_relay_port) if args.host_relay_port else None
                    ),
                },
                holds,
            ),
        )


def _remove_self_state(name: str) -> None:
    with _keeper_lock(name):
        state = read_state(name)
        if (
            state
            and int(state.get("pid") or 0) == os.getpid()
            and not _read_holds(state)
        ):
            _STORE.remove(name)


def _mux_exists(ssh_config: Any, mux: str) -> bool | None:
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
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _any_hold_alive(
    name: str,
    ssh_config: Any,
    *,
    startup_grace: float,
) -> bool:
    try:
        _state, holds, _live_muxes = _prune_snapshot(
            name,
            mux_alive=lambda mux: _mux_exists(ssh_config, mux),
            startup_grace=startup_grace,
        )
    except (RuntimeError, OSError):
        return True
    return bool(holds)


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
        session_alive=lambda: _any_hold_alive(
            args.name,
            ssh_config,
            startup_grace=float(args.startup_grace),
        ),
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
    p.add_argument("--hold-id")
    p.add_argument("--relay-port", type=int)
    p.add_argument("--host-relay-port", type=int)
    p.add_argument("--probe-interval", type=float, default=120.0)
    p.add_argument("--startup-grace", type=float, default=300.0)
    p.set_defaults(func=cmd_forward_keeper)


def cmd_forward_keeper(args: argparse.Namespace) -> int:
    return asyncio.run(_run(args))
