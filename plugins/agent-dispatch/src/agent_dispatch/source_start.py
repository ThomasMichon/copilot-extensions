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
import re
import sys
import time
import uuid

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


def config_identity(cfg, *, base_dir: Path | None = None) -> str:
    # Authentication is exercised by the probe, not serialized into health.
    database = Path(cfg.db_path).expanduser()
    if base_dir is not None and not database.is_absolute():
        if database.drive:
            raise ValueError("drive-relative coordinator database is ambiguous")
        database = Path(base_dir).expanduser().resolve() / database
    selected = {
        "database": str(database.resolve()),
        "host": cfg.host,
        "pool_publication": cfg.pool_publication_enabled,
    }
    if cfg.relationship_admission is not None or cfg.relationship_authority is not None:
        selected["relationship_admission"] = cfg.relationship_admission
        selected["relationship_authority"] = cfg.relationship_authority
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def behavior_identity(cfg) -> str:
    """Fingerprint the version-1 nonsecret coordinator behavior settings."""
    selected = {
        "sweep_interval": float(cfg.sweep_interval),
        "orphan_grace": float(cfg.orphan_grace),
        "handoff_fallback_enabled": cfg.handoff_fallback_enabled,
        "handoff_fallback_grace": float(cfg.handoff_fallback_grace),
    }
    return hashlib.sha256(json.dumps(selected, sort_keys=True, allow_nan=False).encode()).hexdigest()


def data_auth_matches(payload, required: bool) -> bool:
    return (
        payload.get("data_auth_required") is required
        and payload.get("data_token_match_v1") is required
    )


def accept_readiness(
    payload, *, handle, version: str, config_identity: str,
    active=False, expected_behavior: str | None = None, expected_data_auth: bool | None = None,
) -> bool:
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
        and (expected_behavior is None or payload.get("runtime_behavior_identity_v1") == expected_behavior)
        and (expected_data_auth is None or data_auth_matches(payload, expected_data_auth))
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
    if not start_if_missing and os.path.lexists(breadcrumb.breadcrumb_path(routing_root)):
        raise RuntimeError("previous cutover remains unsettled; owner-led recovery is required")
    raw = table.get("active")
    old = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
    if raw is not None and old is None:
        raise ValueError("Source startup cannot interpret incumbent routing ownership")
    if old is not None and not start_if_missing:
        from .coordinator_observation import _loopback

        _loopback(old.client_host, old.port)
    identity = config_identity(cfg, base_dir=install_dir())
    behavior = behavior_identity(cfg) if not start_if_missing else None
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

            host = _loopback(host, port)
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
            expected_behavior=behavior,
            expected_data_auth=None if start_if_missing else token is not None,
        )

    if old is not None:
        payload = read_health(old.client_host, old.port)
        handle = SimpleNamespace(
            pid=old.pid, poll=lambda: None if old_is_live() else 0,
        )
        if not accept_readiness(
            payload, handle=handle, version=old.version, config_identity=identity, active=True,
        ):
            raise RuntimeError("incumbent effective configuration or native ownership is unconfirmed")
        if old.version == __version__ and (
            start_if_missing or (
                payload.get("runtime_behavior_identity_v1") == behavior
                and data_auth_matches(payload, token is not None)
            )
        ):
            verify_selected()
            if (
                (routing.read_table(routing_root, strict=True) or {}).get("active") != raw
                or not old_is_live()
                or (not start_if_missing and os.path.lexists(breadcrumb.breadcrumb_path(routing_root)))
            ):
                raise RuntimeError("selected coordinator owner changed during readiness")
            return {"ok": True, "status": "reused", "pid": old.pid, "new_port": old.port}
    retirement = payload.get("slot", {}).get("self_retire", {}) if old is not None else {}
    manual_retirement = (
        not start_if_missing and old is not None
        and not (
            isinstance(retirement, dict)
            and retirement.get("enabled") is True and retirement.get("armed") is True
        )
    )
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

    def retire_predecessor(endpoint):
        deadline = time.monotonic() + timeout

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("coordinator predecessor retirement timed out")
            return value

        def successor(expected=None):
            verify_selected()
            table = routing.read_table(routing_root, strict=True) or {}
            active = table.get("active")
            target = routing.Endpoint.from_dict(active) if isinstance(active, dict) else None
            handle = handles.get(target.port) if target is not None else None
            if (
                target is None or (expected is not None and active != expected)
                or target.version != __version__ or handle is None or handle.pid != target.pid
                or handle.poll() is not None or not target.process_start_time
                or process_start_time(target.pid) != target.process_start_time
                or not probe(target.client_host, target.port, active=True)
            ):
                raise RuntimeError("coordinator successor is unconfirmed")
            remaining()
            return active

        try:
            if endpoint != old:
                raise RuntimeError("coordinator predecessor changed")
            active = successor()
            state = _route_owner_state(endpoint)
            if state in {"dead", "reused"}:
                return {"exit_confirmed": True, "reason": "already-exited"}
            if state != "matching":
                raise RuntimeError("coordinator predecessor identity is unconfirmed")
            previous = read_health(endpoint.client_host, endpoint.port)
            handle = SimpleNamespace(pid=endpoint.pid, poll=lambda: None if old_is_live() else 0)
            if not accept_readiness(
                previous, handle=handle, version=endpoint.version, config_identity=identity,
            ):
                raise RuntimeError("coordinator predecessor health is unconfirmed")
            successor(active)
            with selected_client(endpoint_url(endpoint.client_host, endpoint.port), remaining()) as client:
                drained = client.drain(timeout=remaining(), poll=min(1.0, remaining()), force=False)
                if (
                    not isinstance(drained, dict) or drained.get("drained") is not True
                    or drained.get("clean") is not True or drained.get("forced") is not False
                ):
                    raise RuntimeError("coordinator predecessor did not drain cleanly")
                successor(active)
                if _route_owner_state(endpoint) != "matching":
                    raise RuntimeError("coordinator predecessor changed before shutdown")
                shutdown = client.shutdown()
                if not isinstance(shutdown, dict) or shutdown.get("shutdown") is not True:
                    raise RuntimeError("coordinator predecessor shutdown is unconfirmed")
            while True:
                state = _route_owner_state(endpoint)
                if state in {"dead", "reused"}:
                    successor(active)
                    return {"exit_confirmed": True, "reason": "drained-and-exited"}
                if state != "matching":
                    raise RuntimeError("coordinator predecessor exit is unconfirmed")
                time.sleep(min(0.1, remaining()))
        except Exception as error:
            if callable(getattr(error, "retry_cleanup", None)):
                raise
            return {"exit_confirmed": False, "reason": type(error).__name__}

    orchestrator = CutoverOrchestrator(
        routing_root, bind="::" if cfg.host == "[::]" else cfg.host,
        version=__version__, spawn_passive=spawn, health_check=probe,
        make_client=make_client,
        pick_free_port=pick_free_port, refuse_old=guard, expected_old=old,
        self_retirement=True, activate_successor=activate,
        serving_health_check=lambda host, port: probe(host, port, active=True),
        service="agent-dispatch",
        **({
            "retire_failed_predecessor": retire_predecessor,
            "require_predecessor_retirement": True,
        } if manual_retirement else {}),
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
    argv: tuple[str, ...] = ()
    profile: Path | None = None
    profile_generation: str | None = None


@dataclass(frozen=True)
class ClassicStartupPlan:
    root: Path
    profiles: tuple[tuple[Path, list[str], str], ...]


_UNPLANNED = object()


def supervisor_plan(root: Path, *, profiles_only=False) -> SupervisorStartupPlan | ClassicStartupPlan | None:
    """Capture supervisor intent without starting a service or publishing a route."""
    from .supervise_registration_cli import build_profile_parser
    from . import remote_dispatch
    from .pool_source_snapshot import read_supervisor_declarations
    from .registrar_discovery import RegistrarSources, _parse_env_file
    from .supervise_cli import _registration_scope
    from .supervisor_registration import supervisor_lease_scope

    primary = root / "supervisor.env"
    try:
        primary.lstat()
    except FileNotFoundError:
        primary_content = None
        environment = {}
    else:
        primary_content = primary.read_bytes()
        environment = _parse_env_file(primary, strict=True, content=primary_content)
    def modern_declarations():
        result = read_supervisor_declarations(RegistrarSources())
        if not result.complete:
            raise RuntimeError("supervisor declaration source is unconfirmed")
        return result
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
                if (profile.get("AGENT_DISPATCH_SUPERVISE_LABELS", "").strip()
                        or profile.get("AGENT_DISPATCH_SUPERVISE_MODE", "").strip().lower() == "serve"):
                    legacy = True
    if mode not in ("", "legacy", "serve"):
        raise RuntimeError("supervisor mode is unsupported")
    if profiles_only or (legacy and mode != "serve"):
        from .supervisor_lane_status import profile_arguments

        modern_declarations()
        paths = [] if profiles_only else [primary]
        if profiles.is_dir():
            paths += sorted(path for path in profiles.iterdir() if path.suffix == ".env")
        selected = []
        for path in paths:
            try:
                content = path.read_bytes()
            except FileNotFoundError:
                continue
            values = _parse_env_file(path, strict=True)
            explicit_master = values.get("AGENT_DISPATCH_SUPERVISE_MODE", "").strip().lower() == "serve"
            if not explicit_master and not any(re.split(
                r"[\s,]+", values.get("AGENT_DISPATCH_SUPERVISE_LABELS", "").strip(),
            )):
                continue
            if path != primary and re.fullmatch(r"[A-Za-z0-9_-]+", path.stem) is None:
                raise ValueError("classic supervisor profile name is unsupported")
            arguments = profile_arguments(values)
            try:
                parsed = build_profile_parser().parse_args(arguments)
            except SystemExit as error:
                raise ValueError("classic supervisor profile arguments are invalid") from error
            if not explicit_master and not parsed.label:
                continue
            if (getattr(parsed, "once", False) or getattr(parsed, "no_single_instance", False)
                    or not math.isfinite(parsed.interval) or parsed.interval <= 0):
                raise ValueError("classic readiness requires a resident lane with a finite positive interval")
            if path.read_bytes() != content:
                raise RuntimeError("classic supervisor profile changed during preflight")
            selected.append((path, arguments, hashlib.sha256(content).hexdigest()))
        return ClassicStartupPlan(root, tuple(selected))
    if mode == "legacy":
        modern_declarations()
        return None
    from .supervisor_lane_status import profile_arguments
    from .supervisor_source import read_legacy_declarations

    argv = profile_arguments(
        {**environment, "AGENT_DISPATCH_SUPERVISE_MODE": "serve"},
        master_default_interval="5", master_legacy=legacy,
    )
    try:
        args = build_profile_parser().parse_args(argv)
    except SystemExit as error:
        raise ValueError("Source supervisor profile arguments are invalid") from error
    if not math.isfinite(args.interval) or args.interval <= 0:
        raise ValueError("Source supervisor interval must be finite and positive")
    if args.once or args.no_single_instance or args.source_candidate is not None:
        raise ValueError("Source supervisor startup requires its resident admitted role")
    declarations = read_legacy_declarations(enabled=args.legacy_env) if args.no_declared else modern_declarations()
    if not declarations.complete:
        raise RuntimeError("supervisor declaration source is unconfirmed")
    if not declarations.declarations and mode != "serve":
        return None
    machine, env = _registration_scope(args)
    if remote_dispatch.is_peer_machine(machine):
        raise RuntimeError("Source startup cannot take a foreign supervisor scope")
    scope = supervisor_lease_scope(machine, env)
    if primary_content is not None and primary.read_bytes() != primary_content:
        raise RuntimeError("Source supervisor primary profile changed during preflight")
    profile = primary if mode == "serve" else None
    generation = hashlib.sha256(primary_content).hexdigest() if profile is not None else None
    return SupervisorStartupPlan(root, args, machine, env, scope, tuple(argv), profile, generation)


def ensure_supervisor(root: Path, *, plan=_UNPLANNED) -> dict:
    """Reconcile only captured, local supervisor intent through the existing owner."""
    from .companion import process_start_token
    from .config import run_dir
    from .single_instance import is_locked, lock_path_for, read_holder_pid
    from .supervisor_status_io import completed_cycle_ready, read_status, runtime_status_path
    from .supervisor_source import effective_settings, read_prepared, spawn_candidate
    from .client_authority import supervisor_authority_target, target_cli_args

    if plan is _UNPLANNED:
        plan = supervisor_plan(root)
    if plan is None:
        return {"status": "unconfigured"}
    if isinstance(plan, ClassicStartupPlan):
        from .installation_policy import windows_policy
        from .supervisor_lane_status import ensure

        if not plan.root.samefile(root):
            raise ValueError("classic startup plan belongs to another installation")
        count = 0
        for profile, arguments, generation in plan.profiles:
            name = None if profile == root / "supervisor.env" else profile.stem
            if windows_policy(root, supervisor_profile=name)["start_supervisor"]:
                ensure(root, profile, arguments, generation=generation)
                count += 1
        return {"status": "ready" if count else "disabled", "profiles": count}
    if not isinstance(plan, SupervisorStartupPlan) or not plan.root.samefile(root):
        raise ValueError("supervisor startup plan belongs to another installation")
    from .installation_policy import windows_policy
    if not windows_policy(root)["start_supervisor"]:
        return {"status": "disabled"}
    args, machine, env, scope = plan.args, plan.machine, plan.env, plan.scope
    lock = lock_path_for(run_dir(), scope)
    argv = [*target_cli_args(args), "supervise", "serve", "--interval", str(args.interval), "--env", env]
    if machine:
        argv += ["--machine", machine]
    if args.legacy_env:
        argv.append("--legacy-env")
    if getattr(args, "no_declared", False):
        argv.append("--no-declared")
    if plan.argv:
        argv = list(plan.argv)
    authority = supervisor_authority_target(args)
    from .supervisor_profile import MasterProfile

    binding = (
        MasterProfile(plan.profile, plan.profile_generation, argv, source_primary=True)
        if plan.profile is not None else None
    )
    expected_settings = effective_settings(args)
    criteria = (
        {"expected": {"settings": expected_settings, "profile_binding": binding.preparation()}}
        if binding is not None else {}
    )
    child = None
    prepared = read_prepared(root, __version__, scope, authority=authority, **criteria)
    if prepared is not None and prepared.get("settings") != expected_settings:
        raise RuntimeError("selected supervisor effective settings do not match startup plan")
    if prepared is None and not is_locked(lock):
        child = spawn_candidate(
            sys.executable, argv, root=root, version=__version__,
            **({"profile_binding": binding.environment()} if binding is not None else {}),
        )
    deadline = time.monotonic() + 60.0
    while True:
        require_selected_source()
        if binding is not None and hashlib.sha256(binding.path.read_bytes()).hexdigest() != binding.generation:
            raise RuntimeError("Source supervisor primary profile changed during readiness")
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
                and completed_cycle_ready(status)
            ):
                prepared = read_prepared(root, __version__, scope, authority=authority, pid=pid, **criteria)
                if prepared is not None and prepared["generation"]["pid"] == pid:
                    if prepared.get("settings") != expected_settings:
                        raise RuntimeError("selected supervisor effective settings do not match startup plan")
                    return {"status": "ready", "pid": pid}
        if time.monotonic() >= deadline:
            raise RuntimeError("selected supervisor native application readiness remains unconfirmed")
        time.sleep(0.1)


def prepare_scheduled_launchers(root: Path, *, create=True) -> None:
    """Explicit Source scheduling activates the existing application owner."""
    if not _path_name(require_selected_source()).samefile(root):
        raise RuntimeError("scheduled activation belongs to another installation")
    body = """# agent-dispatch Source scheduled activation v1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$version = [IO.File]::ReadAllText((Join-Path $root 'current-version')).Trim()
if ($version -notmatch '^[A-Za-z0-9_+.-]+$' -or $version -in @('.', '..')) {
    throw 'Selected Dispatch runtime is unavailable'
}
$slot = Join-Path (Join-Path $root 'versions') $version
$python = Join-Path $slot '.venv\\Scripts\\python.exe'
if (-not [IO.File]::Exists($python)) { $python = Join-Path $slot 'Scripts\\python.exe' }
if (-not [IO.File]::Exists($python)) { throw 'Selected Dispatch runtime is unavailable' }
$env:AGENT_DISPATCH_INSTALL_DIR = $root
& $python -I -B -c "import sys; from pathlib import Path; from agent_dispatch.action_controls import action_decision; from agent_dispatch.installation_policy import windows_policy; d=action_decision('automatic-runtime-reconcile', automatic=True); p=windows_policy(Path(sys.argv[1]), observe_supervisor=False); raise SystemExit(3 if d.inhibited or d.error or not p['@INTENT@'] else 0)" $root
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -I -B -m agent_dispatch @COMMAND@
exit $LASTEXITCODE
"""
    for name in ("serve-service.ps1", "supervise-service.ps1"):
        path = root / name
        command = "_ensure-coordinator" if name == "serve-service.ps1" else "__source-start"
        intent = "start_coordinator" if name == "serve-service.ps1" else "start_supervisor"
        rendered = body.replace("@COMMAND@", command).replace("@INTENT@", intent)
        try:
            info = path.lstat()
        except FileNotFoundError:
            if create:
                with path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(rendered)
        else:
            if not path.is_file() or path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                if create:
                    raise RuntimeError("scheduled activation launcher is not an ordinary file")
                continue
            existing = path.read_text(encoding="utf-8-sig")
            first = existing.splitlines()[0] if existing else ""
            owned = (
                first == "# agent-dispatch Source scheduled activation v1"
                or first == "# agent-dispatch-supervisor-launch-v1"
                or first.startswith("# agent-dispatch coordinator launcher (generated by install.ps1)")
            )
            if owned and existing != rendered:
                pending = path.with_name(path.name + "." + uuid.uuid4().hex + ".pending")
                created = False
                try:
                    with pending.open("x", encoding="utf-8", newline="\n") as stream:
                        created = True
                        stream.write(rendered)
                    os.replace(pending, path)
                finally:
                    if created:
                        pending.unlink(missing_ok=True)


def command(args: argparse.Namespace) -> int:
    from .install_paths import capture_service_environment
    from .installation_policy import windows_policy

    root = _path_name(require_selected_source())
    policy = windows_policy(root, observe_supervisor=False, include_primary=True)
    prepare_scheduled_launchers(root, create=policy["windows_scheduled_task"] == "enabled")
    if policy["windows_scheduled_task"] in {"enabled", "absent"}:
        policy = windows_policy(root, reconcile=True, observe_supervisor=False, include_primary=True)
    captured = capture_service_environment(root)
    previous = dict(os.environ)
    try:
        os.environ.update(captured)
        plan = None
        if policy["start_supervisor"]:
            primary_enabled = policy["primary_start_supervisor"]
            plan = supervisor_plan(root) if primary_enabled else supervisor_plan(root, profiles_only=True)
        result = 0
        if policy["start_coordinator"]:
            from .coordinator_cli import _cmd_cutover

            result = _cmd_cutover(
                argparse.Namespace(json=True, health_timeout=60.0, drain_timeout=60.0, force=False, recover=False),
                source_replacement=True, source_environment=captured,
            )
        if result != 0:
            return result
        if policy["start_supervisor"]:
            ensure_supervisor(root, plan=plan)
        return 0
    finally:
        os.environ.clear()
        os.environ.update(previous)
