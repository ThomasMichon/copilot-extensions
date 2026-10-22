"""Detached reverse-forward keeper for container CLI-mode sessions."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
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
from ssh_manager.keeper_holds import (
    DEFAULT_HOLD_STARTUP_GRACE,
    DEFAULT_LOCK_POLL,
    DEFAULT_LOCK_TIMEOUT,
    DEFAULT_UNKNOWN_HOLD_GRACE,
    KEEPER_HOLDS_PROTOCOL,
    HoldProbe,
    KeeperHoldStore,
)

from .config import RESTRICTED_PROFILE, RUNTIME_DIR

_STATE_DIR = RUNTIME_DIR / "forward-keepers"
_STORE = KeeperStore(_STATE_DIR)
_KEEPER_PROTOCOL = KEEPER_HOLDS_PROTOCOL
_HOLD_STARTUP_GRACE = DEFAULT_HOLD_STARTUP_GRACE
_UNKNOWN_HOLD_GRACE = DEFAULT_UNKNOWN_HOLD_GRACE
_LOCK_TIMEOUT = DEFAULT_LOCK_TIMEOUT
_LOCK_POLL = DEFAULT_LOCK_POLL


def state_path(name: str) -> Path:
    return _STORE.state_path(name)


def read_state(name: str) -> dict[str, Any] | None:
    state = _STORE.read(name)
    embedded = (state or {}).get("pending_deliveries") or {}
    if not isinstance(embedded, dict):
        raise RuntimeError("Invalid embedded pending delivery journal")
    pending = {**embedded, **_read_pending_deliveries(name)}
    if pending:
        return {**(state or {}), "pending_deliveries": pending}
    return state


def _delivery_store() -> KeeperStore:
    return KeeperStore(_STORE.state_dir / "pending-deliveries")


def pending_delivery_path(name: str) -> Path:
    return _delivery_store().state_path(name)


def _read_pending_deliveries(name: str) -> dict[str, Any]:
    path = pending_delivery_path(name)
    data = _delivery_store().read(name)
    if data is None:
        if path.exists():
            raise RuntimeError("Pending delivery journal is unreadable; refusing to discard ownership")
        return {}
    if type(data.get("version")) is not int or data["version"] != 1 or not isinstance(
        data.get("pending_deliveries"), dict,
    ):
        raise RuntimeError("Unsupported pending delivery journal")
    return dict(data["pending_deliveries"])


def _write_pending_deliveries(name: str, pending: dict[str, Any]) -> None:
    if pending:
        _delivery_store().write(name, {"version": 1, "pending_deliveries": pending})
    else:
        _delivery_store().remove(name)


def _migrate_pending_deliveries_locked(name: str, state: dict[str, Any]) -> dict[str, Any]:
    """Move the unreleased embedded format before any disposable-state removal."""
    embedded = state.get("pending_deliveries")
    if embedded is None:
        return state
    if not isinstance(embedded, dict):
        raise RuntimeError("Invalid embedded pending delivery journal")
    pending = {**embedded, **_read_pending_deliveries(name)}
    _write_pending_deliveries(name, pending)
    state = {key: value for key, value in state.items() if key != "pending_deliveries"}
    _STORE.write(name, state)
    return state


def record_pending_delivery(name: str, hold_id: str, pending: dict | None) -> None:
    with _keeper_lock(name):
        state = _migrate_pending_deliveries_locked(name, _STORE.read(name) or {})
        ledger = _read_pending_deliveries(name)
        if hold_id not in _read_holds(state) and hold_id not in ledger:
            raise RuntimeError("Pending delivery has no owning forward-keeper hold")
        if pending is None:
            ledger.pop(hold_id, None)
        else:
            ledger[hold_id] = pending
        _write_pending_deliveries(name, ledger)


def _holds() -> KeeperHoldStore:
    return KeeperHoldStore(
        _STORE,
        protocol=_KEEPER_PROTOCOL,
        startup_grace=_HOLD_STARTUP_GRACE,
        unknown_grace=_UNKNOWN_HOLD_GRACE,
        lock_timeout=_LOCK_TIMEOUT,
        lock_poll=_LOCK_POLL,
    )


def _keeper_lock(name: str):
    return _holds().lock(name)


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
    return _holds().read_holds(state)


def _prune_snapshot(
    name: str,
    *,
    mux_alive: HoldProbe | None,
    startup_grace: float = _HOLD_STARTUP_GRACE,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], set[str]]:
    return _holds().prune_snapshot(name, probe=mux_alive, startup_grace=startup_grace)


def _state_with_holds(state: dict[str, Any], holds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return _holds().state_with_holds(state, holds)


def _confirm_missing_holds(
    holds: dict[str, dict[str, Any]],
    *,
    now: float | None = None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    return _holds().confirm_missing_holds(holds, now=now)


def hold_mux(name: str, hold_id: str) -> str | None:
    return _holds().hold_mux_or_none(name, hold_id)


def list_holds(
    name: str,
    *,
    mux_alive: HoldProbe | None = None,
    prune: bool = True,
) -> dict[str, dict[str, Any]]:
    return _holds().list_holds(name, probe=mux_alive, prune=prune)


def ensure_running(
    name: str,
    *,
    venue_port: int,
    mux: str,
    hold_id: str | None = None,
    relay_port: int | None = None,
    host_relay_port: int | None = None,
    mux_alive: HoldProbe | None = None,
    popen: Any = subprocess.Popen,
) -> dict[str, Any]:
    """Start a keeper unless a live one already owns this container forward."""
    holds = _holds()
    hold_id = hold_id or mux
    with holds.lock(name):
        _migrate_pending_deliveries_locked(name, _STORE.read(name) or {})
    holds.prune_snapshot(name, probe=mux_alive)
    with holds.lock(name):
        existing = _migrate_pending_deliveries_locked(name, _STORE.read(name) or {})
        current_holds = holds.read_holds(existing)
        current_holds, hold_added, hold_updated_at = holds.refresh_hold_with_status(
            current_holds,
            hold_id,
            mux,
        )
        if not (relay_port and host_relay_port) and existing.get("relay_port") and existing.get("host_relay_port"):
            # A --no-relay launch doesn't need the relay, but other holds on the
            # shared keeper may: keep it rather than respawn the keeper without it.
            relay_port, host_relay_port = int(existing["relay_port"]), int(existing["host_relay_port"])
        can_reuse = (
            existing.get("keeper_protocol") == holds.protocol
            and _STORE.alive(name)
            and _same_forward(
                existing,
                venue_port=venue_port,
                relay_port=relay_port,
                host_relay_port=host_relay_port,
            )
        )
        if can_reuse:
            state = holds.state_with_holds(existing, current_holds)
            _STORE.write(name, state)
            return {
                "started": False,
                "hold_added": hold_added,
                "hold_updated_at": hold_updated_at,
                "state": state,
            }
        if existing:
            _STORE.stop(name)
        # Keep surviving holds available if process creation fails. There is no
        # pid here, so this snapshot cannot claim a replacement is running.
        _STORE.write(name, {
            "keeper_protocol": _KEEPER_PROTOCOL, "container": name, "holds": current_holds,
            "venue_port": existing.get("venue_port"),
            "relay_port": existing.get("relay_port"),
            "host_relay_port": existing.get("host_relay_port"),
        })
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
                "holds": current_holds,
                "relay_port": int(relay_port) if relay_port else None,
                "host_relay_port": int(host_relay_port) if host_relay_port else None,
            },
            popen=popen,
            popen_kwargs=windowless_daemon_kwargs(breakaway=True),
        )
        latest_holds = holds.read_holds(_STORE.read(name) or {})
        latest_holds.update(current_holds)
        state = holds.state_with_holds({**state, "holds": latest_holds}, latest_holds)
        _STORE.write(name, state)
        return {
            "started": True,
            "hold_added": hold_added,
            "hold_updated_at": hold_updated_at,
            "state": state,
        }


def stop_keeper(
    name: str,
    *,
    hold_id: str | None = None,
    mux_alive: HoldProbe | None = None,
    expected_updated_at: float | None = None,
) -> bool:
    with _keeper_lock(name):
        _migrate_pending_deliveries_locked(name, _STORE.read(name) or {})
    return _holds().release_hold(
        name,
        hold_id=hold_id,
        probe=mux_alive,
        expected_updated_at=expected_updated_at,
    )


def _write_self_state(args: argparse.Namespace) -> None:
    _holds().write_self_state(
        args.name,
        {
            "container": args.name,
            "venue_port": int(args.venue_port),
            "relay_port": int(args.relay_port) if args.relay_port else None,
            "host_relay_port": (
                int(args.host_relay_port) if args.host_relay_port else None
            ),
        },
        fallback_hold_id=str(args.hold_id) if args.hold_id else None,
        fallback_mux=args.mux,
    )


def _remove_self_state(name: str) -> None:
    with _keeper_lock(name):
        _migrate_pending_deliveries_locked(name, _STORE.read(name) or {})
    _holds().remove_self_state(name)


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
