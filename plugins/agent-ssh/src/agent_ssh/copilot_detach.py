"""Detached, observable CLI-mode Copilot sessions on POSIX SSH targets."""

from __future__ import annotations

from agent_procutil.daemon_instances import DaemonInstanceSpec, daemon_entrypoint, spawn_instance
from functools import partial

import argparse
import base64
import hashlib
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
from remote_login_shell import wrap_login_shell
from ssh_manager import SSHProfileSource, SupervisedRelayForward, build_remote_exec_args
from ssh_manager.forward_keeper import (
    KeeperStore,
    process_identity,
    run_supervised_loop,
    spawn_keeper,
)
from ssh_manager.keeper_holds import (
    KEEPER_HOLDS_PROTOCOL,
    HoldProbe,
    KeeperHoldStore,
)
from venue_copilot import (
    VenueCopilotError,
    read_seed,
    resolve_daemon_port,
)
from venue_copilot.detached import launch_detached, public_plan, stop_detached
from venue_copilot.models import model_copilot_args
from venue_copilot.supervisor import with_supervisor
from venue_copilot.refs import upload_for

_RESERVATION_TTL = 900.0
_RESERVE_RETRY_WINDOW = 90.0
_PROBE_ATTEMPTS = 2
_KEEPER_PROTOCOL = KEEPER_HOLDS_PROTOCOL
_LEGACY_ROOT = ".agent-ssh"  # marketplace-isolation: allow legacy compatibility root
_KEEPER_TOKEN_ENV = "AGENT_SSH_KEEPER_TOKEN"
_STATE_DIR = Path.home() / _LEGACY_ROOT / "forward-keepers"
_STORE = KeeperStore(_STATE_DIR)
_COPILOT_HOSTS_FILE = "copilot-hosts.json"
_ATTACHED_HOLD_PREFIX = "__attached_pid__:"


class CopilotConfigError(ValueError):
    """Invalid persisted `agent-ssh copilot-config` state."""


def _with_caller_model(requested: list[str]) -> list[str]:
    """The caller's `--copilot-arg` list plus its own model / reasoning effort /
    context tier (`venue_copilot.models`), so the worker doesn't silently run on
    the venue's CLI defaults; an explicitly passed flag wins."""
    return requested + model_copilot_args(requested)


def _normalize_workspace(value: str) -> str:
    stripped = value.strip()
    # Trim trailing slashes, but keep the root itself ("/", "//") as "/".
    workspace = stripped.rstrip("/") or ("/" if stripped.startswith("/") else "")
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
    from .copilot_plugins import PREPARATION_RESERVATION_BUDGET

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
        "preparation_reservation_budget": PREPARATION_RESERVATION_BUDGET,
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
        start_new_session=os.name != "nt",
        creationflags=no_window_flags(),
    )
    return (
        result.returncode,
        (result.stdout or b"").decode("utf-8", "replace"),
        (result.stderr or b"").decode("utf-8", "replace"),
    )


def _bash(command: str) -> str:
    return wrap_login_shell(command)


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


def _holds() -> KeeperHoldStore:
    return KeeperHoldStore(_STORE, protocol=_KEEPER_PROTOCOL)


def _lifecycle_lock(scope: str):
    key = "copilot-lifecycle-" + hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return KeeperHoldStore(_STORE, lock_timeout=0.25, lock_poll=0.025).lock(key)


def read_keeper_state(target: str) -> dict[str, Any] | None:
    return _STORE.read(_state_key(target))


def stop_keeper(
    target: str,
    *,
    hold_id: str | None = None,
    probe: HoldProbe | None = None,
    expected_updated_at: float | None = None,
) -> bool:
    if hold_id is not None and read_keeper_state(target) is None:
        return False
    return _holds().release_hold(
        _state_key(target),
        hold_id=hold_id,
        probe=probe,
        expected_updated_at=expected_updated_at,
    )


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
        "target": target,
        "venue_port": int(venue_port),
        "children": children,
        "instance_token": os.environ.get(_KEEPER_TOKEN_ENV, ""),
    }


def _keeper_route_active(target: str, daemon_port: int) -> bool:
    state = read_keeper_state(target)
    if not state or int(state.get("venue_port") or 0) != int(daemon_port):
        return False
    return _STORE.alive(_state_key(target))


def _attached_hold_mux(pid: int) -> str:
    identity = process_identity(int(pid)) or ""
    payload = json.dumps(
        {"pid": int(pid), "identity": identity},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _ATTACHED_HOLD_PREFIX + base64.urlsafe_b64encode(payload).decode("ascii")


def _probe_hold(ssh_config: Any, mux: str) -> bool | None:
    if mux.startswith(_ATTACHED_HOLD_PREFIX):
        encoded = mux[len(_ATTACHED_HOLD_PREFIX):]
        try:
            raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
            payload = json.loads(raw.decode("utf-8"))
            pid = int(payload.get("pid") or 0)
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return False
        identity = payload.get("identity")
        if not isinstance(identity, str) or not identity or pid <= 0:
            return False
        return process_identity(pid) == identity
    return _mux_exists(ssh_config, mux)


def _update_keeper_hold_mux(
    target: str, hold_id: str, mux: str, *, expected_updated_at: float | None = None,
) -> float | None:
    holds_store = _holds()
    with holds_store.lock(_state_key(target)):
        state = read_keeper_state(target)
        holds = holds_store.read_holds(state)
        if not state or hold_id not in holds:
            if expected_updated_at is not None:
                raise RuntimeError("SSH launch keeper hold disappeared before handoff")
            return
        if expected_updated_at is not None and holds[hold_id]["updated_at"] != expected_updated_at:
            raise RuntimeError("SSH launch keeper hold changed before handoff")
        holds = holds_store.refresh_hold(holds, hold_id, mux, confirmed=True)
        _STORE.write(_state_key(target), holds_store.state_with_holds(state, holds))
        return float(holds[hold_id]["updated_at"])


def _update_keeper_hold_pid(target: str, hold_id: str, pid: int) -> float | None:
    return _update_keeper_hold_mux(target, hold_id, _attached_hold_mux(pid))


def _keeper_token_matches(current: dict[str, Any] | None, token: str | None) -> bool:
    """False when the stored state belongs to a different keeper instance."""
    stored = (current or {}).get("instance_token")
    return not stored or stored == token


def _write_keeper_state(target: str, payload: dict[str, Any]) -> None:
    """Caller must hold ``_holds().lock(_state_key(target))``."""
    current = read_keeper_state(target)
    token = payload.get("instance_token")
    if not _keeper_token_matches(current, token):
        return
    if (
        current
        and current.get("instance_token")
        and current.get("instance_token") == token
        and current.get("children")
        and not payload.get("children")
    ):
        payload = {**payload, "children": current["children"]}
    if current and current.get("holds") and not payload.get("holds"):
        payload = {**payload, "holds": current["holds"]}
    _STORE.write(_state_key(target), payload)


def ensure_keeper(
    target: str,
    *,
    venue_port: int,
    mux: str,
    hold_id: str | None = None,
    hold_pid: int | None = None,
    preserve_live_mux: bool = False,
    probe: HoldProbe | None = None,
) -> dict[str, Any]:
    holds_store = _holds()
    hold_id = hold_id or mux
    hold_mux = _attached_hold_mux(hold_pid) if hold_pid and hold_pid > 0 else mux
    _state, _holds_snapshot, live_muxes = holds_store.prune_snapshot(_state_key(target), probe=probe)
    with holds_store.lock(_state_key(target)):
        state = read_keeper_state(target)
        holds = holds_store.read_holds(state)
        previous_hold = holds.get(hold_id)
        prior_mux = holds.get(hold_id, {}).get("mux")
        if preserve_live_mux and prior_mux in live_muxes and not prior_mux.startswith(_ATTACHED_HOLD_PREFIX):
            hold_mux = prior_mux
        holds, hold_added, hold_updated_at = holds_store.refresh_hold_with_status(
            holds,
            hold_id,
            hold_mux,
        )
        if (
            state
            and state.get("keeper_protocol") == _KEEPER_PROTOCOL
            and
            _STORE.alive(_state_key(target))
            and int(state.get("venue_port") or 0) == int(venue_port)
        ):
            state = holds_store.state_with_holds(state, holds)
            _STORE.write(_state_key(target), state)
            return {
                "started": False,
                "hold_added": hold_added,
                "hold_updated_at": hold_updated_at,
                "previous_hold": previous_hold,
                "state": state,
            }
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
            "--hold-id",
            hold_id,
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
                "keeper_protocol": _KEEPER_PROTOCOL,
                "target": target,
                "venue_port": int(venue_port),
                "mux": mux,
                "holds": holds,
                "instance_token": instance_token,
            },
            popen=partial(
                spawn_instance,
                instance=DaemonInstanceSpec(
                    "agent-ssh-forward-keeper",
                    Path(os.path.abspath(_STORE.state_path(_state_key(target)))).parent,
                    Path(__file__).resolve(),
                ),
            ),
            popen_kwargs=windowless_daemon_kwargs(breakaway=True),
        )
        current_holds = holds_store.read_holds(read_keeper_state(target) or {})
        current_holds.update(holds)
        state = holds_store.state_with_holds({**state, "holds": current_holds}, current_holds)
        _write_keeper_state(target, state)
        return {
            "started": True,
            "hold_added": hold_added,
            "hold_updated_at": hold_updated_at,
            "previous_hold": previous_hold,
            "state": state,
        }


class _SshAdapter:
    probe_attempts = _PROBE_ATTEMPTS

    def __init__(self, *, target: str, ssh_config: Any, hold_id: str | None = None, plugin_args=None) -> None:
        self.target = target
        self.ssh_config = ssh_config
        self.hold_id = hold_id
        self.plugin_args = plugin_args
        self.pending_delivery = False
        self.keeper_hold_acquired = False
        self._hold_updated_at: float | None = None
        self._launch_pending = False
        self._previous_hold: dict | None = None

    def prepare(self, plan: dict) -> dict:
        from .copilot_plugins import prepare
        return prepare(self.plugin_args, self, plan)

    def record_pending_delivery(self, plan: dict, pending: dict | None) -> None:
        from .copilot_delivery import record
        if pending is not None:
            pending = {
                **pending, "requested_workspace": self.plugin_args.workspace,
                "keeper_hold_id": self.hold_id,
            }
        record(self.target, plan["scope_id"], pending)
        self.pending_delivery = pending is not None

    def run(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        return _remote(self.ssh_config, _bash(command), timeout=timeout)

    def launch(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        return self.run(command, timeout=timeout)

    def handoff_keeper(self, plan: dict) -> None:
        self._hold_updated_at = _update_keeper_hold_mux(
            self.target, self.hold_id, plan["mux_session"],
            expected_updated_at=self._hold_updated_at,
        )
        self._launch_pending = False

    def restore_keeper_hold(self) -> None:
        previous = self._previous_hold
        if not self._launch_pending or not previous or previous["mux"].startswith(_ATTACHED_HOLD_PREFIX):
            return
        holds_store = _holds()
        with holds_store.lock(_state_key(self.target)):
            state = read_keeper_state(self.target)
            holds = holds_store.read_holds(state)
            current = holds.get(self.hold_id)
            if not current or current["updated_at"] != self._hold_updated_at:
                raise RuntimeError("SSH launch hold changed before original mux restoration")
            if current["mux"] == previous["mux"]:
                return
            if current["mux"] != _attached_hold_mux(os.getpid()):
                raise RuntimeError("SSH launcher ownership changed before original mux restoration")
            holds[self.hold_id] = dict(previous)
            _STORE.write(_state_key(self.target), holds_store.state_with_holds(state, holds))
            self._hold_updated_at = previous["updated_at"]

    def run_input(self, command: str, stdin: bytes, *, timeout: float) -> tuple[int, str, str]:
        return _remote_input(self.ssh_config, _bash(command), stdin, timeout=timeout)

    def ensure_keeper(self, *, venue_port: int, mux: str) -> dict[str, Any]:
        result = ensure_keeper(
            self.target,
            venue_port=venue_port,
            mux=mux,
            hold_id=self.hold_id,
            hold_pid=os.getpid() if self._launch_pending else None,
            preserve_live_mux=self._launch_pending,
            probe=lambda held_mux: _probe_hold(self.ssh_config, held_mux),
        )
        raw_updated = result.get("hold_updated_at")
        self._previous_hold = result.pop("previous_hold", None)
        self._hold_updated_at = float(raw_updated) if raw_updated is not None else None
        self.keeper_hold_acquired = bool(result.get("hold_added"))
        return result

    def stop_keeper(self) -> bool:
        result = stop_keeper(
            self.target,
            hold_id=self.hold_id,
            probe=lambda mux: _probe_hold(self.ssh_config, mux),
            expected_updated_at=self._hold_updated_at,
        )
        self.keeper_hold_acquired = False
        return result

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


def _launch(args, adapter, plan, *, seed, refs=None):
    from . import copilot_delivery, copilot_plugins

    copilot_plugins.resolve_launch(adapter, plan)
    with _lifecycle_lock(plan["scope_id"]):
        pending = copilot_delivery.read(
            args.target, plan["scope_id"], requested_workspace=args.workspace,
            execution_workspace=plan["workspace"],
        )
        if pending is not None:
            adapter.pending_delivery = True
            return copilot_delivery.recover(adapter, plan, pending)
        plan["bridge_owned_seed"] = True
        adapter._launch_pending = True
        try:
            return launch_detached(
                adapter, plan, seed=seed, driver=args.driver,
                copilot_args=(
                    _with_caller_model(list(getattr(args, "copilot_args", None) or []))
                    if getattr(args, "detach", False) else []
                ),
                ensure_mux=True, register_timeout=float(args.register_timeout), progress=_progress, refs=refs,
            )
        finally:
            adapter.restore_keeper_hold()


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
        rc, payload = _launch(
            args, _SshAdapter(
                target=args.target, ssh_config=ssh_config, hold_id=plan["scope_id"], plugin_args=args,
            ), plan, seed=seed, refs=refs,
        )
        return _emit(rc, payload)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)


def cmd_stop(args: argparse.Namespace) -> int:
    from venue_copilot import live_session_for
    from . import copilot_delivery

    if getattr(args, "ttl_seconds", None) is not None:
        print("[FAIL] --ttl-seconds applies only to attached mode, not --stop", file=sys.stderr)
        return 2
    try:
        ssh_config = _prepare_ssh_and_workspace(args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        return _fail(str(exc))
    plan = plan_for(args)
    from .copilot_plugins import resolve_launch
    try:
        resolve_launch(_SshAdapter(target=args.target, ssh_config=ssh_config, plugin_args=args), plan)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)
    try:
        with _lifecycle_lock(plan["scope_id"]):
            held_mux = _holds().hold_mux_or_none(_state_key(args.target), plan["scope_id"])
            if held_mux:
                if held_mux.startswith(_ATTACHED_HOLD_PREFIX):
                    return _fail("SSH launch is still preparing this scope; retry stop after it settles", plan)
                plan["mux_session"] = held_mux
                plan["venue"]["mux_session_name"] = plan["mux_session"]
            pending = copilot_delivery.read(
                args.target, plan["scope_id"], requested_workspace=args.workspace,
                execution_workspace=plan["workspace"],
            )
            if pending is not None and (
                pending.get("scope_id") != plan["scope_id"]
                or pending.get("mux_session") != plan["mux_session"]
                or pending.get("envelope", {}).get("plugin_context", {}).get("cwd") != plan["workspace"]
            ):
                raise RuntimeError("Pending SSH delivery does not match the native stop target")
            pending_hold = None
            if pending is not None:
                hold_id = copilot_delivery.pending_keeper_hold_id(pending, plan["scope_id"])
                with _holds().lock(_state_key(args.target)):
                    hold = _holds().read_holds(read_keeper_state(args.target)).get(hold_id)
                    if hold is not None:
                        if hold["mux"] != pending["mux_session"]:
                            raise RuntimeError("Pending SSH keeper hold changed before stop")
                        pending_hold = (hold_id, hold["updated_at"])
            row = live_session_for(plan["scope_id"])
            rc, payload = stop_detached(
                _SshAdapter(target=args.target, ssh_config=ssh_config, hold_id=plan["scope_id"]),
                plan,
                session_row=row,
            )
            if rc == 0 and pending is not None:
                try:
                    if pending_hold is not None:
                        stop_keeper(
                            args.target, hold_id=pending_hold[0],
                            expected_updated_at=pending_hold[1],
                            probe=lambda mux: _probe_hold(ssh_config, mux),
                        )
                    copilot_delivery.retire_stopped(args.target, plan, pending)
                except (OSError, ValueError, RuntimeError) as exc:
                    return _fail(f"Native session stopped but delivery journal retirement failed: {exc}", plan)
            return _emit(rc, payload)
    except (OSError, ValueError, RuntimeError) as exc:
        return _fail(str(exc), plan)


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
    if getattr(args, "ttl_seconds", None) is not None:
        plan["reservation_ttl"] = float(args.ttl_seconds)
    adapter = _SshAdapter(
        target=args.target, ssh_config=ssh_config,
        hold_id=f"attached:{os.getpid()}:{uuid.uuid4().hex}", plugin_args=args,
    )
    try:
        rc, payload = _launch(args, adapter, plan, seed=seed)
        if rc:
            return _emit(rc, payload)
        command = _bash(
            "tmux attach-session -t " + shlex.quote("=" + plan["mux_session"]),
        )
        proc = subprocess.Popen(
            build_remote_exec_args(ssh_config, command, pty=True),
            creationflags=no_window_flags(),
        )
        try:
            adapter._hold_updated_at = _update_keeper_hold_pid(args.target, adapter.hold_id, proc.pid)
        except BaseException:
            _reap_child(proc)
            raise
        return int(proc.wait())
    except (OSError, ValueError, RuntimeError, VenueCopilotError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)
    finally:
        if adapter.keeper_hold_acquired and not adapter.pending_delivery:
            try:
                adapter.stop_keeper()
            except (RuntimeError, OSError) as exc:
                print(f"[WARN] could not release the attached hold: {exc}", file=sys.stderr)


def _reap_child(proc: Any, timeout: float = 10.0) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    except OSError:
        pass


def _mux_exists(ssh_config: Any, mux: str) -> bool | None:
    try:
        rc, _out, _err = _remote(
            ssh_config,
            f"tmux has-session -t {shlex.quote('=' + mux)}",
            timeout=60.0,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None


def _any_hold_alive(target: str, ssh_config: Any) -> bool:
    return _holds().alive_or_fail_open(
        _state_key(target),
        probe=lambda mux: _probe_hold(ssh_config, mux),
    )


async def _run_forward_keeper(args: argparse.Namespace) -> int:
    ssh_config = _ssh_config(args.target)

    instance_token = os.environ.get(_KEEPER_TOKEN_ENV, "")

    def write_state() -> None:
        _holds().write_self_state(
            _state_key(args.target),
            _keeper_state(
                args.target,
                int(args.venue_port),
                args.mux,
                forwards,
            ),
            fallback_hold_id=getattr(args, "hold_id", None),
            fallback_mux=args.mux,
            only_if=lambda current: _keeper_token_matches(current, instance_token),
        )

    def remove_state() -> None:
        _holds().remove_self_state(_state_key(args.target))

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
        session_alive=lambda: _any_hold_alive(args.target, ssh_config),
        write_state=write_state,
        remove_state=remove_state,
        probe_interval=float(args.probe_interval),
        startup_grace=float(args.startup_grace),
    )


@daemon_entrypoint("agent-ssh-forward-keeper", source=__file__, purpose="production")
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
    p.add_argument("--stage-plugin", dest="stage_plugins", action="append", default=[])
    from plugin_resolve.reflection import selection_json
    p.add_argument(
        "--stage-plugin-spec", dest="stage_plugins", action="append", type=selection_json,
    )
    from .copilot_plugins import disabled_plugin
    p.add_argument(
        "--stage-plugin-disabled", dest="stage_plugins", action="append", type=disabled_plugin,
    )
    p.add_argument("--plugin-root", dest="plugin_roots", action="append", default=[])
    p.add_argument("--expected-repository", help="Refuse if the selected SSH workspace changed repository")
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
    p.add_argument("--hold-id")
    p.add_argument("--probe-interval", type=float, default=120.0)
    p.add_argument("--startup-grace", type=float, default=300.0)
    p.set_defaults(func=cmd_forward_keeper)
