"""Installed Source startup through Dispatch's existing process and cutover owners."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time

from . import __version__
from .install_paths import install_dir


def _path_name(path: Path) -> Path:
    if os.name == "nt":
        name = str(path)
        if name.startswith("\\\\?\\UNC\\"):
            return Path("\\\\" + name[8:])
        if name.startswith("\\\\?\\"):
            return Path(name[4:])
    return path


def _require_selected_facts(facts, *, owner: str = "Source") -> Path:
    from .self_update import read_current_version

    generation, slot_path, root_path = facts
    root, slot = Path(root_path), Path(slot_path)
    if (
        generation != __version__ or not root.samefile(install_dir())
        or _path_name(slot.resolve(strict=True)) != _path_name(slot)
        or not _path_name(Path(__file__).resolve()).is_relative_to(
            _path_name(Path(sys.prefix).resolve()),
        )
        or read_current_version(root) != generation
    ):
        raise ValueError(f"{owner} startup selection does not match the physical application")
    helper = slot / ".runtime-bootstrap" / "versioned_runtime.py"
    if _path_name(helper.resolve(strict=True)) != _path_name(helper):
        raise ValueError(f"{owner} completion helper is redirected")
    spec = importlib.util.spec_from_file_location("_dispatch_source_completion", helper)
    if spec is None or spec.loader is None:
        raise ValueError(f"{owner} completion helper is unavailable")
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    if not runtime.is_complete(root, generation):
        raise ValueError(f"{owner} startup requires canonical complete construction")
    return root


def require_selected_source() -> Path:
    from agent_procutil._daemon_instance_identity import runtime_slot_paths
    from .source_build import _SOURCE_VERSION

    facts = runtime_slot_paths(sys.prefix)
    if (
        _SOURCE_VERSION.fullmatch(__version__) is None or ".profile." not in __version__
        or facts is None or Path(sys.prefix).name != ".venv"
    ):
        raise ValueError("startup requires its physical selected Source application")
    return _require_selected_facts(facts)


def require_selected_generation() -> Path:
    """Admit a selected completed Source or direct repair-generation runtime."""
    from agent_procutil._daemon_instance_identity import runtime_slot_paths
    from .source_build import _REPAIR_VERSION, _SOURCE_VERSION

    if _SOURCE_VERSION.fullmatch(__version__) is not None:
        return require_selected_source()
    facts = runtime_slot_paths(sys.prefix)
    match = _REPAIR_VERSION.fullmatch(__version__)
    if (
        facts is None or match is None
        or ("+" in match["package"]) != (match["separator"] == ".")
        or _path_name(Path(sys.prefix).resolve()) != _path_name(Path(facts[1]).resolve())
    ):
        raise ValueError("reconcile requires its physical selected repair generation")
    return _require_selected_facts(facts, owner="Coordinator")


def config_identity(cfg) -> str:
    # Authentication is exercised by the probe, not serialized into health.
    selected = {
        "database": str(Path(cfg.db_path).expanduser().resolve()),
        "host": cfg.host,
        "pool_publication": cfg.pool_publication_enabled,
    }
    if cfg.relationship_admission is not None or cfg.relationship_authority is not None:
        selected["relationship_admission"] = cfg.relationship_admission
        selected["relationship_authority"] = cfg.relationship_authority
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def accept_readiness(payload, *, handle, version: str, config_identity: str, active=False) -> bool:
    from zdd.diagnostics import process_start_time

    slot = payload.get("slot") if isinstance(payload, dict) else None
    if not isinstance(slot, dict) or handle.poll() is not None:
        return False
    accept = getattr(handle, "accept_health", None)
    if accept is not None and not accept(payload):
        return False
    birth = process_start_time(handle.pid)
    return (
        type(handle.pid) is int and handle.pid > 0 and bool(birth)
        and payload.get("version") == version and payload.get("status") == "ok"
        and payload.get("runtime_config_identity") == config_identity
        and slot.get("pid") == handle.pid and slot.get("process_start_time") == birth
        and (not active or slot.get("role") == "active")
        and process_start_time(handle.pid) == birth
    )


def _route_owner_state(endpoint):
    from agent_procutil._daemon_instance_identity import birth_identity
    from zdd.diagnostics import process_start_time
    from .companion import _process_exists

    if type(endpoint.pid) is not int or endpoint.pid <= 0 or not endpoint.process_start_time:
        return "unknown"
    try:
        # Windows creation time remains readable after exit while handles survive.
        birth_identity(endpoint.pid)
    except ProcessLookupError:
        return "dead"
    except (OSError, ValueError):
        return "unknown"
    observed = process_start_time(endpoint.pid)
    if observed is not None:
        return "matching" if observed == endpoint.process_start_time else "reused"
    return "unknown" if _process_exists(endpoint.pid) else "dead"


def run_coordinator(
    cfg, routing_root, *, token, spawn_passive, pick_free_port, timeout=60.0,
    verify_selected=None, start_if_missing=True, confirm_inactive=None, expected_table=None,
):
    from types import SimpleNamespace
    from zdd import breadcrumb, routing
    from zdd.cutover import CutoverOrchestrator
    from zdd.diagnostics import process_start_time
    from .client import DispatchClient

    verify_selected = require_selected_source if verify_selected is None else verify_selected
    if (
        not callable(verify_selected) or type(start_if_missing) is not bool
        or (expected_table is not None and not isinstance(expected_table, dict))
    ):
        raise ValueError("coordinator selection owner is unavailable")
    verify_selected()
    table = routing.read_table(routing_root, strict=True) or {}
    if expected_table is not None and table != expected_table:
        raise RuntimeError("coordinator preflight route changed")
    raw = table.get("active")
    old = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
    if raw is not None and old is None:
        raise ValueError("Source startup cannot interpret incumbent routing ownership")
    if old is not None and not start_if_missing:
        from .coordinator_observation import _loopback

        _loopback(old.client_host)
    identity = config_identity(cfg)
    handles = {}

    if old is not None and old.version == __version__ and _route_owner_state(old) in {"dead", "reused"}:
        if not start_if_missing:
            raise RuntimeError("coordinator inactive state is unconfirmed")
        if breadcrumb.read_breadcrumb(routing_root) is not None or table.get("previous") is not None:
            raise RuntimeError("previous cutover remains unsettled; owner-led recovery is required")
        stale = old

        def refuse_stale(current):
            verify_selected()
            if (
                current != raw or routing.read_table(routing_root, strict=True) != table
                or _route_owner_state(stale) not in {"dead", "reused"}
            ):
                return "stale coordinator ownership changed or is unconfirmed"
            return None

        routing.withdraw_active_guarded(
            routing_root, expected_active=stale, refuse_current=refuse_stale,
            lock_deadline=time.monotonic() + 5.0,
        )
        old, raw = None, None

    if old is None and not start_if_missing:
        if (
            breadcrumb.read_breadcrumb(routing_root) is not None or table.get("previous") is not None
            or not callable(confirm_inactive) or confirm_inactive(table) is not True
        ):
            raise RuntimeError("coordinator inactive state is unconfirmed")
        verify_selected()
        if (
            (routing.read_table(routing_root, strict=True) or {}) != table
            or confirm_inactive(table) is not True
        ):
            raise RuntimeError("coordinator inactive state changed")
        verify_selected()
        return {"ok": True, "status": "inactive"}

    probe_timeout = 2.0 if start_if_missing else min(timeout, 2.0)

    def endpoint_url(host, port):
        if not start_if_missing:
            from .coordinator_observation import _loopback

            host = _loopback(host)
        return f"http://{routing.format_authority(host, port)}"

    def selected_client(url, request_timeout):
        if start_if_missing:
            return DispatchClient(url, token=token, timeout=request_timeout)
        from .coordinator_observation import _local_client

        return _local_client(url, token=token, timeout=request_timeout)

    def make_client(url):
        if not start_if_missing:
            from urllib.parse import urlsplit

            parsed = urlsplit(url)
            if (
                parsed.scheme != "http" or not parsed.hostname or parsed.port is None
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            ):
                raise ValueError("coordinator control endpoint is invalid")
            url = endpoint_url(parsed.hostname, parsed.port)
        return selected_client(url, timeout)

    def read_health(host, port):
        url = endpoint_url(host, port)
        if not start_if_missing:
            from .coordinator_observation import _local_health

            return _local_health(url, token=token, timeout=probe_timeout)
        with selected_client(url, probe_timeout) as client:
            return client.health()

    def old_is_live():
        if old is None or not old.process_start_time:
            return False
        if not start_if_missing:
            return _route_owner_state(old) == "matching"
        return process_start_time(old.pid) == old.process_start_time

    def probe(host, port, *, active=False):
        payload = read_health(host, port)
        handle = handles.get(port)
        if handle is None:
            if old is None or not old.process_start_time:
                return False
            handle = SimpleNamespace(
                pid=old.pid, poll=lambda: None if old_is_live() else 0,
            )
        return accept_readiness(
            payload, handle=handle, version=__version__, config_identity=identity, active=active,
        )

    if old is not None and old.version == __version__:
        if not probe(old.client_host, old.port, active=True):
            raise RuntimeError("selected coordinator owner failed native application readiness")
        verify_selected()
        if (
            (routing.read_table(routing_root, strict=True) or {}).get("active") != raw
            or not old_is_live()
        ):
            raise RuntimeError("selected coordinator owner changed during readiness")
        return {"ok": True, "status": "reused", "pid": old.pid, "new_port": old.port}
    if old is not None:
        payload = read_health(old.client_host, old.port)
        handle = SimpleNamespace(
            pid=old.pid, poll=lambda: None if old_is_live() else 0,
        )
        if not accept_readiness(
            payload, handle=handle, version=old.version, config_identity=identity, active=True,
        ):
            raise RuntimeError("incumbent effective configuration or native ownership is unconfirmed")
    if breadcrumb.read_breadcrumb(routing_root) is not None:
        raise RuntimeError("previous cutover remains unsettled; owner-led recovery is required")

    def guard(current):
        verify_selected()
        if current != raw:
            return "Source startup predecessor changed"
        if old is not None and not old_is_live():
            return "Source startup predecessor native ownership is unconfirmed"
        return None

    def spawn(port):
        verify_selected()
        if not start_if_missing and (
            not old_is_live()
            or (routing.read_table(routing_root, strict=True) or {}).get("active") != raw
        ):
            raise RuntimeError("coordinator predecessor native ownership changed before spawn")
        handle = spawn_passive(port)
        handles[port] = handle
        return handle

    def activate(endpoint):
        verify_selected()
        if not probe(endpoint.client_host, endpoint.port, active=True):
            raise RuntimeError("published coordinator has not admitted its selected native owner")

    orchestrator = CutoverOrchestrator(
        routing_root, bind="::" if cfg.host == "[::]" else cfg.host,
        version=__version__, spawn_passive=spawn, health_check=probe,
        make_client=make_client,
        pick_free_port=pick_free_port, refuse_old=guard, expected_old=old,
        self_retirement=True, activate_successor=activate,
        serving_health_check=lambda host, port: probe(host, port, active=True),
        service="agent-dispatch",
        **({"routing_lock_timeout": min(5.0, timeout)} if not start_if_missing else {}),
    )
    return orchestrator.run(
        health_timeout=timeout, drain_timeout=timeout,
        lock_timeout=5.0 if start_if_missing else min(5.0, timeout),
    ).to_dict()


@dataclass(frozen=True)
class SupervisorStartupPlan:
    root: Path
    args: argparse.Namespace
    machine: str | None
    env: str
    scope: str


_UNPLANNED = object()


def supervisor_plan(root: Path) -> SupervisorStartupPlan | None:
    """Capture supervisor intent without starting a service or publishing a route."""
    from . import __main__ as cli
    from . import remote_dispatch
    from .pool_source_snapshot import read_supervisor_declarations
    from .registrar_discovery import RegistrarSources, _parse_env_file
    from .supervise_cli import _registration_scope
    from .supervisor_registration import supervisor_lease_scope

    primary = root / "supervisor.env"
    try:
        primary.lstat()
    except FileNotFoundError:
        environment = {}
    else:
        environment = _parse_env_file(primary, strict=True)
    declarations = read_supervisor_declarations(RegistrarSources())
    if not declarations.complete:
        raise RuntimeError("supervisor declaration source is unconfirmed")
    mode = environment.get("AGENT_DISPATCH_SUPERVISE_MODE", "").strip().lower()
    legacy = bool(environment.get("AGENT_DISPATCH_SUPERVISE_LABELS", "").strip())
    profiles = root / "supervisors"
    try:
        profiles.lstat()
    except FileNotFoundError:
        pass
    else:
        for path in sorted(profiles.iterdir()):
            if path.suffix == ".env":
                profile = _parse_env_file(path, strict=True)
                if profile.get("AGENT_DISPATCH_SUPERVISE_LABELS", "").strip():
                    legacy = True
    if mode not in ("", "serve") or (legacy and mode != "serve"):
        raise RuntimeError("classic supervisor requires its existing owner-led migration")
    if not declarations.declarations and mode != "serve":
        return None
    argv = ["supervise", "serve"]
    if legacy:
        argv.append("--legacy-env")
    machine = environment.get("AGENT_DISPATCH_SUPERVISE_MACHINE")
    if machine:
        argv += ["--machine", machine]
    interval = float(environment.get("AGENT_DISPATCH_SUPERVISE_INTERVAL") or "5")
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Source supervisor interval must be finite and positive")
    argv += ["--interval", str(interval)]
    if environment.get("AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS", "").strip():
        raise ValueError("Source supervisor startup requires explicit structured configuration")
    args = cli.build_parser().parse_args(argv)
    machine, env = _registration_scope(args)
    if remote_dispatch.is_peer_machine(machine):
        raise RuntimeError("Source startup cannot take a foreign supervisor scope")
    scope = supervisor_lease_scope(machine, env)
    return SupervisorStartupPlan(root, args, machine, env, scope)


def ensure_supervisor(root: Path, *, plan=_UNPLANNED) -> dict:
    """Reconcile only captured, local supervisor intent through the existing owner."""
    from .companion import process_start_token
    from .config import run_dir
    from .single_instance import is_locked, lock_path_for, read_holder_pid
    from .supervisor_status_io import read_status, runtime_status_path
    from .supervisor_source import effective_settings, read_prepared, spawn_candidate
    from .client_authority import supervisor_authority_target

    if plan is _UNPLANNED:
        plan = supervisor_plan(root)
    if plan is None:
        return {"status": "unconfigured"}
    if not isinstance(plan, SupervisorStartupPlan) or not plan.root.samefile(root):
        raise ValueError("supervisor startup plan belongs to another installation")
    args, machine, env, scope = plan.args, plan.machine, plan.env, plan.scope
    lock = lock_path_for(run_dir(), scope)
    argv = ["supervise", "serve", "--interval", str(args.interval), "--env", env]
    if machine:
        argv += ["--machine", machine]
    if args.legacy_env:
        argv.append("--legacy-env")
    authority = supervisor_authority_target(args)
    child = None
    prepared = read_prepared(root, __version__, scope, authority=authority)
    expected_settings = effective_settings(args)
    if prepared is not None and prepared.get("settings") != expected_settings:
        raise RuntimeError("selected supervisor effective settings do not match startup plan")
    if prepared is None and not is_locked(lock):
        child = spawn_candidate(sys.executable, argv, root=root, version=__version__)
    deadline = time.monotonic() + 60.0
    while True:
        require_selected_source()
        if child is not None and child.poll() is not None and not is_locked(lock):
            raise RuntimeError("selected supervisor exited before application readiness")
        status, error = read_status(runtime_status_path(scope))
        generation = status.get("generation") if isinstance(status, dict) else None
        if error is not None:
            if error == "status-read-unavailable" and time.monotonic() < deadline:
                time.sleep(0.1)
                continue
            raise RuntimeError(f"supervisor readiness observation is unavailable: {error}")
        if isinstance(generation, dict):
            pid = generation.get("pid")
            birth = process_start_token(pid) if type(pid) is int and pid > 0 else None
            if (
                birth and generation.get("start_token") == birth
                and status.get("runtime_version") == __version__
                and is_locked(lock) and read_holder_pid(lock) == pid
                and type(status.get("cycle_id")) is int and status["cycle_id"] > 0
                and type(status.get("updated_at")) in (int, float)
                and type(status.get("cycle_started_at")) in (int, float)
                and math.isfinite(status["updated_at"]) and math.isfinite(status["cycle_started_at"])
                and status["updated_at"] >= status["cycle_started_at"]
            ):
                prepared = read_prepared(root, __version__, scope, authority=authority, pid=pid)
                if prepared is not None and prepared["generation"]["pid"] == pid:
                    if prepared.get("settings") != expected_settings:
                        raise RuntimeError("selected supervisor effective settings do not match startup plan")
                    return {"status": "ready", "pid": pid}
        if time.monotonic() >= deadline:
            raise RuntimeError("selected supervisor native application readiness remains unconfirmed")
        time.sleep(0.1)


def command(args: argparse.Namespace) -> int:
    from .coordinator_cli import _cmd_cutover
    from .install_paths import capture_service_environment

    root = _path_name(require_selected_source())
    captured = capture_service_environment(root)
    previous = dict(os.environ)
    try:
        os.environ.update(captured)
        plan = supervisor_plan(root)
        result = _cmd_cutover(
            argparse.Namespace(json=True, health_timeout=60.0, drain_timeout=60.0, force=False, recover=False),
            source_replacement=True, source_environment=captured,
        )
        if result != 0:
            return result
        ensure_supervisor(root, plan=plan)
        return 0
    finally:
        os.environ.clear()
        os.environ.update(previous)
