"""Detached, observable CLI-mode Copilot sessions on POSIX SSH targets."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from agent_procutil import (
    no_window_flags,
    windowless_daemon_kwargs,
    windowless_python,
    windowless_python_env,
)
from .file_lock import exclusive_file_lock
from ssh_manager import SSHProfileSource, SupervisedRelayForward, build_remote_exec_args
from ssh_manager.forward_keeper import (
    KeeperStore,
    process_identity,
    run_supervised_loop,
    spawn_keeper,
)
from venue_copilot import (
    DEFAULT_TTL_SECONDS,
    VenueCopilotError,
    bridge_probe_script,
    registration_credentials_script,
    read_seed,
    resolve_daemon_port,
    resolve_local_auth_token,
    run_venue_copilot,
    trust_folder_command,
)
from venue_copilot.detached import launch_detached, public_plan, stop_detached
from venue_copilot.models import model_copilot_args
from venue_copilot.supervisor import with_supervisor
from venue_copilot.refs import upload_for

_RESERVATION_TTL = 900.0
_RESERVE_RETRY_WINDOW = 90.0
_PROBE_ATTEMPTS = 2
_LEGACY_ROOT = ".agent-ssh"  # marketplace-isolation: allow legacy compatibility root
_KEEPER_TOKEN_ENV = "AGENT_SSH_KEEPER_TOKEN"
_STATE_DIR = Path.home() / _LEGACY_ROOT / "forward-keepers"
_STORE = KeeperStore(_STATE_DIR)
_COPILOT_HOSTS_FILE = "copilot-hosts.json"


class CopilotConfigError(ValueError):
    """Invalid persisted `agent-ssh copilot-config` state."""


def _with_caller_model(requested: list[str]) -> list[str]:
    """The caller's `--copilot-arg` list plus its own model / reasoning effort /
    context tier (`venue_copilot.models`), so the worker doesn't silently run on
    the venue's CLI defaults; an explicitly passed flag wins."""
    return requested + model_copilot_args(requested)


def _normalize_workspace(value: str) -> str:
    workspace = value.strip().rstrip("/")
    if not workspace:
        raise ValueError(
            "agent-ssh copilot could not resolve a remote workspace. Pass "
            "--workspace /path/to/checkout, or set it with "
            "`agent-ssh copilot-config set <host> --workspace /path/to/checkout`."
        )
    if not workspace.startswith("/"):
        raise ValueError(f"remote workspace must be an absolute POSIX path, got {value!r}")
    return workspace


def _copilot_config_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / _LEGACY_ROOT / _COPILOT_HOSTS_FILE


def _load_copilot_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or _copilot_config_path()
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        raise CopilotConfigError(f"could not read {config_path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CopilotConfigError(f"{config_path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CopilotConfigError(f"{config_path} must contain a JSON object")
    return raw


def _workspace_from_host_config(target: str, path: Path | None = None) -> str | None:
    raw = _load_copilot_config(path)
    hosts = raw.get("hosts")
    if not isinstance(hosts, dict):
        return None
    target_key = target.casefold()
    entries = []
    exact = hosts.get(target_key)
    if isinstance(exact, dict):
        entries.append(exact)
    entries.extend(
        entry
        for name, entry in hosts.items()
        if isinstance(name, str) and name.casefold() == target_key and name != target_key
    )
    for entry in entries:
        if isinstance(entry, dict):
            workspace = entry.get("workspace")
            if isinstance(workspace, str) and workspace.strip():
                return _normalize_workspace(workspace)
    return None


def set_host_workspace(target: str, workspace: str, path: Path | None = None) -> Path:
    normalized = _normalize_workspace(workspace)
    config_path = path or _copilot_config_path()
    raw = _load_copilot_config(config_path)
    hosts = raw.get("hosts")
    if not isinstance(hosts, dict):
        hosts = {}
    target_key = target.casefold()
    hosts = {
        name: entry
        for name, entry in hosts.items()
        if not isinstance(name, str) or name.casefold() != target_key
    }
    hosts[target_key] = {"workspace": normalized}
    payload = {"version": 1, "hosts": hosts}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, config_path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    return config_path


def resolve_workspace(args: argparse.Namespace) -> str:
    explicit = getattr(args, "workspace", None)
    if explicit:
        return _normalize_workspace(str(explicit))
    configured = _workspace_from_host_config(args.target)
    if configured:
        return configured
    raise ValueError(
        "agent-ssh copilot could not resolve a remote workspace. Pass "
        "--workspace /path/to/checkout, or set it with "
        "`agent-ssh copilot-config set <host> --workspace /path/to/checkout`."
    )


def _progress(stage: str, detail: str = "") -> None:
    print(f"[DETACH] {stage}{': ' + detail if detail else ''}", file=sys.stderr, flush=True)


def plan_for(args: argparse.Namespace) -> dict[str, Any]:
    workspace = _normalize_workspace(str(args.workspace))
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
    print(json.dumps({"ok": False, "error": message, **public_plan(plan or {}), **extra}, indent=2))
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


def _remote_input(ssh_config: Any, command: str, stdin: bytes, *, timeout: float) -> tuple[int, str, str]:
    result = subprocess.run(
        build_remote_exec_args(ssh_config, command, pty=False),
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
    _fabric = "agent-worktrees"  # marketplace-isolation: allow remote-management
    script = (
        "command -v bash >/dev/null && command -v tmux >/dev/null && "
        f"command -v {_fabric} >/dev/null && command -v copilot >/dev/null && "
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


def _prepare_ssh_and_workspace(args: argparse.Namespace, *, dry_run: bool = False) -> Any:
    ssh_config = _ssh_config(args.target)
    if dry_run:
        args.workspace = resolve_workspace(args)
        return ssh_config
    _ensure_posix(ssh_config)
    args.workspace = resolve_workspace(args)
    return ssh_config


def _state_key(target: str) -> str:
    return target


def _state_lock_path(target: str) -> Path:
    return _STORE.state_path(_state_key(target)).with_suffix(".lock")


def read_keeper_state(target: str) -> dict[str, Any] | None:
    return _STORE.read(_state_key(target))


def stop_keeper(target: str) -> bool:
    with exclusive_file_lock(_state_lock_path(target)):
        return _STORE.stop(_state_key(target))


def _keeper_state(
    target: str, venue_port: int, mux: str, forwards: list[SupervisedRelayForward] | None = None,
) -> dict[str, Any]:
    children = [
        {"pid": pid, "identity": identity}
        for forward in (forwards or [])
        for pid, identity in [(forward.process_pid, forward.process_birth_identity)]
        if isinstance(pid, int) and pid > 0 and isinstance(identity, str) and identity
    ]
    return {
        "pid": os.getpid(),
        "pid_identity": process_identity(os.getpid()) or "",
        "target": target,
        "venue_port": int(venue_port),
        "mux": mux,
        "children": children,
        "instance_token": os.environ.get(_KEEPER_TOKEN_ENV, ""),
    }


def _keeper_route_active(target: str, daemon_port: int) -> bool:
    state = read_keeper_state(target)
    if not state or int(state.get("venue_port") or 0) != int(daemon_port):
        return False
    return _STORE.alive(_state_key(target))


def _bridge_route_reachable(ssh_config: Any, daemon_port: int) -> bool:
    rc, _out, _err = _remote(ssh_config, _bash(bridge_probe_script(daemon_port)), timeout=30.0)
    return rc == 0


def _write_keeper_state(target: str, payload: dict[str, Any]) -> None:
    current = read_keeper_state(target)
    token = payload.get("instance_token")
    if (
        current
        and current.get("instance_token")
        and current.get("instance_token") != token
    ):
        return
    if (
        current
        and current.get("instance_token")
        and current.get("instance_token") == token
        and current.get("children")
        and not payload.get("children")
    ):
        payload = {**payload, "children": current["children"]}
    _STORE.write(_state_key(target), payload)


def _remove_keeper_state(target: str, instance_token: str) -> None:
    current = read_keeper_state(target)
    if (
        not current
        or current.get("instance_token") != instance_token
    ):
        return
    _STORE.remove(_state_key(target))


def ensure_keeper(target: str, *, venue_port: int, mux: str) -> dict[str, Any]:
    with exclusive_file_lock(_state_lock_path(target)):
        state = read_keeper_state(target)
        if (
            _STORE.alive(_state_key(target))
            and state
            and state.get("mux") == mux
            and int(state.get("venue_port") or 0) == int(venue_port)
        ):
            return {"started": False, "state": state}
        _STORE.stop(_state_key(target))
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
        instance_token = uuid.uuid4().hex
        state = spawn_keeper(
            argv,
            {
                **os.environ,
                **windowless_python_env(),
                _KEEPER_TOKEN_ENV: instance_token,
            },
            {
                "target": target,
                "venue_port": int(venue_port),
                "mux": mux,
                "instance_token": instance_token,
            },
            popen_kwargs=windowless_daemon_kwargs(breakaway=True),
        )
        _write_keeper_state(target, state)
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

    def run_input(self, command: str, stdin: bytes, *, timeout: float) -> tuple[int, str, str]:
        return _remote_input(self.ssh_config, _bash(command), stdin, timeout=timeout)

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
    if getattr(args, "ttl_seconds", None) is not None:
        print("[FAIL] --ttl-seconds applies only to attached mode, not --detach", file=sys.stderr)
        return 2
    try:
        ssh_config = _prepare_ssh_and_workspace(args, dry_run=bool(getattr(args, "dry_run", False)))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return _fail(str(exc))
    plan = plan_for(args)
    plan["venue"] = with_supervisor(plan["venue"])  # successor can find this worker
    try:
        seed = read_seed(args)
        refs = upload_for(list(getattr(args, "ref_files", None) or []), plan["scope_id"])
    except (OSError, ValueError) as exc:
        return _fail(str(exc), plan)
    if getattr(args, "dry_run", False):
        print(json.dumps({
            "ok": True, "dry_run": True, **public_plan(plan), "seed_len": len(seed or ""),
            "ref_files": [name for name, _ in refs[2]] if refs else [],
        }, indent=2))
        return 0
    try:
        _ensure_remote_tooling(ssh_config)
        rc, payload = launch_detached(
            _SshAdapter(target=args.target, ssh_config=ssh_config),
            plan,
            seed=seed,
            driver=args.driver,
            copilot_args=_with_caller_model(list(getattr(args, "copilot_args", None) or [])),
            ensure_mux=True,
            register_timeout=float(args.register_timeout),
            progress=_progress,
            refs=refs,
        )
        return _emit(rc, payload)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)


def cmd_stop(args: argparse.Namespace) -> int:
    from venue_copilot import live_session_for

    if getattr(args, "ttl_seconds", None) is not None:
        print("[FAIL] --ttl-seconds applies only to attached mode, not --stop", file=sys.stderr)
        return 2
    try:
        ssh_config = _prepare_ssh_and_workspace(args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return _fail(str(exc))
    plan = plan_for(args)
    state = read_keeper_state(args.target)
    if state and state.get("mux"):
        plan["mux_session"] = str(state["mux"])
        plan["venue"]["mux_session_name"] = plan["mux_session"]
    row = live_session_for(plan["scope_id"])
    rc, payload = stop_detached(
        _SshAdapter(target=args.target, ssh_config=ssh_config),
        plan,
        session_row=row,
    )
    return _emit(rc, payload)


def cmd_attached(args: argparse.Namespace) -> int:
    if getattr(args, "ref_files", None):
        print("[FAIL] --ref-file requires --detach", file=sys.stderr)
        return 2
    if getattr(args, "copilot_args", None):
        print("[FAIL] --copilot-arg requires --detach", file=sys.stderr)
        return 2
    if getattr(args, "dry_run", False):
        print("[FAIL] --dry-run requires --detach", file=sys.stderr)
        return 2
    try:
        ssh_config = _prepare_ssh_and_workspace(args)
        seed = read_seed(args)
        _ensure_remote_tooling(ssh_config)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return _fail(str(exc))

    plan = plan_for(args)
    daemon_port = resolve_daemon_port()
    token = resolve_local_auth_token()
    reverse_forwards: list[str] = []
    if daemon_port and token:
        rc, _out, err = _remote(
            ssh_config,
            _bash(registration_credentials_script(token, daemon_port)),
            timeout=60.0,
        )
        if rc != 0:
            return _fail(
                "could not provision registration credentials on the SSH target"
                + (f" ({err.strip()})" if err.strip() else ""),
                plan,
            )
        if not (
            _keeper_route_active(args.target, daemon_port)
            or _bridge_route_reachable(ssh_config, daemon_port)
        ):
            reverse_forwards.append(f"{daemon_port}:127.0.0.1:{daemon_port}")
    else:
        print(
            "[WARN] Could not resolve the host agent-bridge daemon's live "
            "port/token; the remote session may not register back to it.",
            file=sys.stderr,
        )

    def connect(remote_command: str) -> int:
        workspace = shlex.quote(plan["workspace"])
        command = _bash(
            f"cd {workspace} && {trust_folder_command(plan['workspace'])} && "
            f"{remote_command}"
        )
        return subprocess.run(
            build_remote_exec_args(
                ssh_config,
                command,
                reverse_forwards=reverse_forwards,
                pty=True,
            ),
            creationflags=no_window_flags(),
        ).returncode

    try:
        return run_venue_copilot(
            plan["identity"],
            connect=connect,
            anchor=True,
            ttl_seconds=(
                DEFAULT_TTL_SECONDS
                if getattr(args, "ttl_seconds", None) is None
                else float(args.ttl_seconds)
            ),
            driver=args.driver,
            seed=seed,
            ensure_mux=True,
        )
    except VenueCopilotError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


def _mux_exists(ssh_config: Any, mux: str) -> bool:
    rc, _out, _err = _remote(ssh_config, f"tmux has-session -t {shlex.quote('=' + mux)}", timeout=60.0)
    return rc == 0


async def _run_forward_keeper(args: argparse.Namespace) -> int:
    ssh_config = _ssh_config(args.target)
    instance_token = os.environ.get(_KEEPER_TOKEN_ENV, "")

    def write_state() -> None:
        with exclusive_file_lock(_state_lock_path(args.target)):
            _write_keeper_state(
                args.target,
                _keeper_state(
                    args.target,
                    int(args.venue_port),
                    args.mux,
                    forwards,
                ),
            )

    def remove_state() -> None:
        with exclusive_file_lock(_state_lock_path(args.target)):
            _remove_keeper_state(args.target, instance_token)

    forwards = [
        SupervisedRelayForward(
            ssh_config,
            int(args.venue_port),
            host_port_resolver=lambda: resolve_daemon_port() or 0,
            monitor_interval=15.0,
            on_pid_change=write_state,
        )
    ]
    return await run_supervised_loop(
        forwards,
        session_alive=lambda: _mux_exists(ssh_config, args.mux),
        write_state=write_state,
        remove_state=remove_state,
        probe_interval=float(args.probe_interval),
        startup_grace=float(args.startup_grace),
    )


def cmd_forward_keeper(args: argparse.Namespace) -> int:
    import asyncio

    return asyncio.run(_run_forward_keeper(args))


def add_copilot_subparser(sub) -> None:
    p = sub.add_parser(
        "copilot",
        help="Attach, start, or stop CLI-mode Copilot on a POSIX SSH target.",
    )
    p.add_argument("target", help="SSH host alias")
    p.add_argument(
        "--workspace",
        help="Remote checkout path. Defaults to the target's `agent-ssh "
             "copilot-config` workspace.",
    )
    p.add_argument("--seed")
    p.add_argument("--seed-file", dest="seed_file")
    p.add_argument("--copilot-arg", dest="copilot_args", action="append", default=[])
    p.add_argument(
        "--ref-file", dest="ref_files", action="append", default=[], metavar="PATH",
        help="With --detach: copy PATH (file or directory) into the target outside the "
             "checkout and tell the worker where it is (repeatable)",
    )
    p.add_argument("--driver", default="cli-mode")
    p.add_argument(
        "--ttl-seconds",
        type=float,
        default=None,
        help="Attached-mode CLI reservation lifetime before reclaim (default 300). "
             "Not valid with --detach.",
    )
    p.add_argument("--register-timeout", type=float, default=180.0)
    p.add_argument("--dry-run", action="store_true")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--detach", action="store_true")
    mode.add_argument("--stop", action="store_true")
    p.set_defaults(
        func=lambda args: (
            cmd_stop(args)
            if args.stop
            else cmd_detach(args)
            if args.detach
            else cmd_attached(args)
        )
    )


def add_forward_keeper_subparser(sub) -> None:
    p = sub.add_parser("forward-keeper", help=argparse.SUPPRESS)
    p.add_argument("target")
    p.add_argument("--venue-port", type=int, required=True)
    p.add_argument("--mux", required=True)
    p.add_argument("--probe-interval", type=float, default=120.0)
    p.add_argument("--startup-grace", type=float, default=300.0)
    p.set_defaults(func=cmd_forward_keeper)
