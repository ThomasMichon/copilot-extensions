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
    VenueCopilotError,
    await_claim,
    bridge_probe_script,
    build_copilot_remote_command,
    last_json,
    observe_commands,
    read_seed,
    registration_credentials_script,
    release_cli_mode,
    reserve_with_retry,
    resolve_daemon_port,
    resolve_local_auth_token,
    seed_delivery,
    trust_folder_command,
    with_new_session,
)

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
    }


def _commands(plan: dict[str, Any], session_id: str) -> dict[str, str]:
    target = plan["target"]
    mux = plan["mux_session"]
    return {
        **observe_commands(session_id),
        "attach": f"ssh -t {shlex.quote(target)} tmux attach -t {shlex.quote(mux)}",
        "stop": (
            f"agent-ssh copilot {shlex.quote(target)} --stop "
            f"--workspace {shlex.quote(plan['workspace'])}"
        ),
    }


def _fail(message: str, plan: dict[str, Any] | None = None, **extra: Any) -> int:
    print(f"[FAIL] {message}", file=sys.stderr)
    print(json.dumps({"ok": False, "error": message, **(plan or {}), **extra}, indent=2))
    return 1


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


def _bridge_path_ok(ssh_config: Any, port: int) -> bool:
    for attempt in range(_PROBE_ATTEMPTS):
        rc, _out, _err = _remote(ssh_config, _bash(bridge_probe_script(port)), timeout=30.0)
        if rc == 0:
            return True
        if attempt + 1 < _PROBE_ATTEMPTS:
            import time

            time.sleep(5.0)
    return False


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


def _launch_command(plan: dict[str, Any], args: argparse.Namespace, seed: str | None) -> str:
    copilot_args = with_new_session(list(getattr(args, "copilot_args", None) or []))
    typed_seed, seed_prefix = seed_delivery(seed, plan["scope_id"])
    command = seed_prefix + build_copilot_remote_command(
        plan["identity"],
        anchor=True,
        driver=args.driver,
        seed=typed_seed,
        ensure_mux=True,
        detach=True,
        bridge_scope_id=plan["scope_id"],
        copilot_args=copilot_args,
        login_shell=False,
    )
    workspace = plan["workspace"]
    return _bash(
        f"cd {shlex.quote(workspace)} && {trust_folder_command(workspace)} && {command}"
    )


def _kill_mux(ssh_config: Any, mux: str) -> tuple[int, str, str]:
    mux_target = shlex.quote("=" + mux)
    return _remote(
        ssh_config,
        _bash(
            f"tmux kill-session -t {mux_target} 2>/dev/null; sleep 1; "
            f"if tmux has-session -t {mux_target} 2>/dev/null; "
            "then echo STILL_RUNNING; exit 3; fi; echo STOPPED"
        ),
        timeout=120.0,
    )


def cmd_detach(args: argparse.Namespace) -> int:
    plan = plan_for(args)
    try:
        seed = read_seed(args)
    except (OSError, ValueError) as exc:
        return _fail(str(exc), plan)
    if getattr(args, "dry_run", False):
        print(json.dumps({"ok": True, "dry_run": True, **plan, "seed_len": len(seed or "")}, indent=2))
        return 0

    reservation: dict[str, Any] | None = None
    created = False
    keeper_started = False
    ok = False
    ssh_config = _ssh_config(args.target)
    try:
        _ensure_posix(ssh_config)
        _ensure_remote_tooling(ssh_config)
        daemon_port = resolve_daemon_port()
        if not daemon_port:
            return _fail("the host agent-bridge daemon is not running (no routing table)", plan)
        token = resolve_local_auth_token()
        if not token:
            return _fail("the host agent-bridge daemon has no readable auth token", plan)

        rc, _out, err = _remote(
            ssh_config,
            _bash(registration_credentials_script(token, daemon_port)),
            timeout=60.0,
        )
        if rc != 0:
            return _fail("could not provision registration credentials on the SSH target", plan, detail=err)
        keeper = ensure_keeper(args.target, venue_port=daemon_port, mux=plan["mux_session"])
        keeper_started = bool(keeper.get("started"))
        if not _bridge_path_ok(ssh_config, daemon_port):
            return _fail(
                "the SSH target cannot reach the host bridge through the forward "
                "(authenticated probe failed)",
                plan,
            )
        reservation = reserve_with_retry(
            plan["scope_id"],
            plan["venue"],
            ttl_seconds=_RESERVATION_TTL,
            retry_window=_RESERVE_RETRY_WINDOW,
            on_wait=lambda: _progress("waiting", "another launch on this SSH target holds the reservation"),
        )
        proc_rc, stdout, stderr = _remote(
            ssh_config,
            _launch_command(plan, args, seed),
            timeout=float(args.register_timeout) + 300.0,
        )
        embodied = last_json(stdout)
        if "agent-worktrees: command not found" in stderr + stdout:
            return _fail("agent-worktrees is not installed on the SSH target", plan)
        if "unrecognized arguments" in stderr or "unrecognized arguments" in stdout:
            return _fail(
                "the SSH target's agent-worktrees is too old for detached launch "
                "(--bridge-scope-id/--copilot-arg); update agent-worktrees",
                plan,
            )
        if proc_rc != 0 or not embodied.get("ok"):
            return _fail(
                f"remote embody failed: {embodied.get('error') or stderr.strip()[-2000:] or f'exit {proc_rc}'}",
                plan,
            )
        created = bool(embodied.get("created"))
        actual_mux = embodied.get("session")
        if actual_mux and actual_mux != plan["mux_session"]:
            plan["mux_session"] = actual_mux
            plan["venue"]["mux_session_name"] = actual_mux
        if created and seed and not embodied.get("seed_submitted"):
            return _fail("Copilot never reached a ready prompt, so the seed was not submitted", plan)
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
    except (RuntimeError, VenueCopilotError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)
    finally:
        if reservation:
            release_cli_mode(plan["scope_id"], reservation_id=reservation.get("reservation_id"))
        if not ok:
            if created:
                _kill_mux(ssh_config, plan["mux_session"])
            if keeper_started:
                stop_keeper(args.target)


def cmd_stop(args: argparse.Namespace) -> int:
    from venue_copilot import deregister_live_session, live_session_for

    plan = plan_for(args)
    state = read_keeper_state(args.target)
    if state and state.get("mux"):
        plan["mux_session"] = str(state["mux"])
        plan["venue"]["mux_session_name"] = plan["mux_session"]
    row = live_session_for(plan["scope_id"])
    session_id = row.get("session_id") if (row.get("venue") or {}).get("target") == args.target else None
    ssh_config = _ssh_config(args.target)
    try:
        _ensure_posix(ssh_config)
        rc, out, err = _kill_mux(ssh_config, plan["mux_session"])
    except (RuntimeError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)
    if rc != 0 or "STOPPED" not in out:
        return _fail("could not verify the session stopped; nothing was released", plan, detail=err)
    keeper_stopped = stop_keeper(args.target)
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
