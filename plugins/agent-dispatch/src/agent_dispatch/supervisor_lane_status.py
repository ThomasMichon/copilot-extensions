"""Optional completed-cycle observations for installer-owned classic profiles.

This extends the existing supervisor status format, not worker admission or a
new service role. Older lanes have no such observation and are not ready merely
because their native process is alive.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

from . import __version__
from .supervisor_status_io import completed_cycle_ready, read_status, runtime_status_path

PROFILE_ENV = "AGENT_DISPATCH_SUPERVISOR_PROFILE"
GENERATION_ENV = "AGENT_DISPATCH_SUPERVISOR_PROFILE_GENERATION"
CAPABILITY = "classic-completed-cycle-v1"


def classic_arguments(environment: dict[str, str]) -> list[str]:
    """Keep the existing generated launcher's flags, defaults and extra tokens."""
    def value(name, default=""):
        return environment.get("AGENT_DISPATCH_SUPERVISE_" + name) or default

    def words(name):
        return [word for word in re.split(r"[\s,]+", value(name)) if word]

    arguments = [
        "supervise", "--all-repos", "--interval", value("INTERVAL", "30"),
        "--max-concurrent", value("MAX_CONCURRENT", "1"),
        "--max-attempts", value("MAX_ATTEMPTS", "3"),
    ]
    for name, flag in (("LABELS", "--label"), ("LABEL_MAX_ATTEMPTS", "--label-max-attempts")):
        for word in words(name):
            arguments += [flag, word]
    if value("EMBODY_BACKEND"):
        arguments += ["--embody-backend", value("EMBODY_BACKEND")]
    for name, flag in (("CLI_LABELS", "--cli-label"), ("HEADLESS_LABELS", "--headless-label")):
        for word in words(name):
            arguments += [flag, word]
    if value("HEADLESS_AGENT"):
        arguments += ["--headless-agent", value("HEADLESS_AGENT")]
    arguments += value("EXTRA_ARGS").split()
    return arguments


def profile_arguments(
    environment: dict[str, str], *, master_default_interval="30", master_legacy=True,
) -> list[str]:
    if environment.get("AGENT_DISPATCH_SUPERVISE_MODE", "").strip().lower() != "serve":
        return classic_arguments(environment)
    arguments = ["supervise", "serve"]
    if master_legacy:
        arguments.append("--legacy-env")
    arguments += ["--interval", environment.get("AGENT_DISPATCH_SUPERVISE_INTERVAL") or master_default_interval]
    if machine := environment.get("AGENT_DISPATCH_SUPERVISE_MACHINE"):
        arguments += ["--machine", machine]
    return arguments + environment.get("AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS", "").split()


def profile_scope(profile: Path) -> str:
    from .install_paths import normalized_path
    from .source_start import _path_name

    return "lane:" + hashlib.sha256(normalized_path(_path_name(profile)).encode()).hexdigest()


def settings_identity(arguments: list[str]) -> str:
    return hashlib.sha256(json.dumps(arguments, separators=(",", ":")).encode()).hexdigest()


def require_selected_runtime(root: Path) -> None:
    from agent_procutil._daemon_instance_identity import runtime_slot_paths
    from .install_paths import install_dir
    from .self_update import read_current_version, slot_python

    facts = runtime_slot_paths(sys.prefix)
    selected = slot_python(root, __version__)
    if (
        facts is None or facts[0] != __version__
        or not Path(facts[2]).samefile(root) or not install_dir().samefile(root)
        or read_current_version(root) != __version__
        or selected is None or not Path(sys.executable).samefile(selected)
        or not Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    ):
        raise RuntimeError("classic readiness requires its physical selected runtime")


class LaneSuperseded(KeyboardInterrupt):
    """Leave the classic loop at a cycle boundary, without touching workers."""


class LaneStatus:
    def __init__(self, root: Path, profile: Path, arguments: list[str], guard, *, generation=None):
        from .single_instance import SingleInstance, lock_path_for
        from .config import run_dir

        self.root, self.profile, self.guard = root, profile, guard
        self.scope = profile_scope(profile)
        self.path = runtime_status_path(
            self.scope + ":instance:" + guard.receipt.instance_id, directory=root / "run",
        )
        self.lock = SingleInstance(lock_path_for(run_dir(), self.scope))
        self.identity = settings_identity(arguments)
        self.profile_generation = generation or hashlib.sha256(profile.read_bytes()).hexdigest()
        self.cycle = 0
        self.started = None
        self.completed_cycle = None
        self.registered = False
        self.artifact_identity = None

    def __enter__(self):
        from .source_start import _path_name

        require_selected_runtime(self.root)
        for parent in (self.root, self.path.parent):
            if parent.exists() and _path_name(parent.resolve(strict=True)) != _path_name(parent.absolute()):
                raise RuntimeError("classic native status directory is redirected")
        if hashlib.sha256(self.profile.read_bytes()).hexdigest() != self.profile_generation:
            raise RuntimeError("classic profile changed before admission")
        if not self.lock.acquire():
            raise LaneSuperseded()
        return self

    def start(self):
        from .self_update import stale_target

        if (
            stale_target(self.root, __version__) is not None
            or hashlib.sha256(self.profile.read_bytes()).hexdigest() != self.profile_generation
        ):
            raise LaneSuperseded()
        self.cycle += 1
        self.started = time.time()
        self._write(None)

    def complete(self):
        self._write(time.time())

    def _write(self, completed):
        from agent_procutil._daemon_instance_identity import birth_identity, path_identity
        from .companion import process_start_token

        receipt = self.guard.receipt
        record = {
            "runtime_version": __version__, "cycle_id": self.cycle,
            "cycle_started_at": self.started, "updated_at": completed,
            "generation": {"pid": os.getpid(), "start_token": process_start_token(os.getpid())},
            "lane_readiness": {
                "capability": CAPABILITY, "profile": str(self.profile),
                "settings_identity": self.identity, "instance_id": receipt.instance_id,
                "profile_generation": self.profile_generation,
                "registry_root": str(receipt.registry_root), "birth": birth_identity(os.getpid()),
            },
        }
        if completed is not None:
            self.completed_cycle = {
                "cycle_id": self.cycle, "started_at": self.started, "completed_at": completed,
                "generation": record["generation"], "runtime_version": __version__,
            }
        if self.completed_cycle is not None:
            record["completed_cycle"] = self.completed_cycle
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the artifact's native file identity stable while publishing.
        # A partial concurrent read is unavailable, never readiness.
        if self.registered and path_identity(self.path) != self.artifact_identity:
            raise RuntimeError("classic native status artifact identity changed")
        with self.path.open("w" if self.registered else "x", encoding="utf-8") as stream:
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if not self.registered:
            self.artifact_identity = path_identity(self.path)
            self.guard.add_artifact(self.path)
            self.registered = True

    def __exit__(self, *_):
        from agent_procutil._daemon_instance_identity import delete_owned_file
        try:
            if self.artifact_identity is not None:
                delete_owned_file(self.path, self.artifact_identity)
        finally:
            self.lock.release()


def _status_records(root, pattern):
    """Dead files do not consume live-profile bounds or authorize blind deletion."""
    from agent_procutil._daemon_instance_identity import delete_owned_file, identity_state
    from agent_procutil.daemon_instances import DaemonInstanceReceipt, inspect_instances
    from .source_start import _path_name

    registries = {}
    reclaimed = False
    for path in (root / "run").glob(pattern):
        payload, error = read_status(path)
        if error or payload is None:
            continue
        generation, details = payload.get("generation"), payload.get("lane_readiness")
        if isinstance(generation, dict) and isinstance(details, dict):
            pid, birth = generation.get("pid"), details.get("birth")
            if type(pid) is int and pid > 0 and isinstance(birth, str) and birth:
                if identity_state(pid, birth) in {"dead", "reused"}:
                    if reclaimed:
                        continue
                    try:
                        receipt = DaemonInstanceReceipt(details.get("instance_id", ""), Path(details.get("registry_root", "")))
                        if receipt.registry_root not in registries:
                            registries[receipt.registry_root] = {
                                row.get("instance_id"): row for row in inspect_instances(receipt.registry_root)
                            }
                        row = registries[receipt.registry_root].get(receipt.instance_id)
                        if (
                            row is not None and row.get("pid") == str(pid) and row.get("birth") == birth
                            and Path(row.get("runtime_root", "")).samefile(root)
                        ):
                            for index in range(int(row.get("artifact_count", "0"))):
                                recorded = Path(row.get(f"artifact_{index}_path", ""))
                                if _path_name(recorded.absolute()) == _path_name(path.absolute()):
                                    delete_owned_file(path, (
                                        row.get(f"artifact_{index}_device"), row.get(f"artifact_{index}_inode"),
                                    ))
                                    reclaimed = True
                    except (OSError, RuntimeError, TypeError, ValueError):
                        pass  # Proven dead, but deletion authority remains unconfirmed.
                    continue
        yield path, payload


def serve_classic(supervisor, interval, profile, generation, on_cycle):
    from agent_procutil.daemon_instances import admit_daemon_instance
    from .install_paths import install_dir

    # Nested admission retains the existing decorated lane's native receipt.
    with admit_daemon_instance(
        "agent-dispatch-lane", runtime_root=install_dir(), source=__file__, purpose="production",
    ) as guard:
        try:
            with LaneStatus(
                install_dir(), profile, list(sys.argv[1:]), guard, generation=generation,
            ) as status:
                def completed(spawned):
                    status.complete()
                    on_cycle(spawned)

                supervisor.serve(interval=interval, on_cycle=completed, on_cycle_start=status.start)
        except LaneSuperseded:
            pass


def observation(root: Path, profile: Path, arguments: list[str]) -> dict | None:
    from agent_procutil._daemon_instance_identity import identity_state, path_identity
    from agent_procutil.daemon_instances import inspect_instances
    from .companion import process_start_token
    from .config import run_dir
    from .single_instance import is_locked, lock_path_for, read_holder_pid
    from .source_start import _path_name

    scope = profile_scope(profile)
    lock = lock_path_for(run_dir(), scope)
    if not is_locked(lock):
        return None
    holder = read_holder_pid(lock)
    holder_start = process_start_token(holder) if type(holder) is int and holder > 0 else None
    directory = root / "run"
    prefix = runtime_status_path(scope + ":instance:", directory=directory).stem.removesuffix(".status")
    matching = []
    count = 0
    for candidate, value in _status_records(root, prefix + "*.status.json"):
        count += 1
        if count > 128:
            raise RuntimeError("classic live profile status inventory exceeds its bound")
        generation = value.get("generation") if isinstance(value, dict) else None
        if (isinstance(generation, dict) and generation.get("pid") == holder
                and holder_start and generation.get("start_token") == holder_start):
            matching.append(candidate)
    if not matching:
        return {"status": "starting", "pid": holder}
    if len(matching) != 1:
        raise RuntimeError("classic native status is ambiguous")
    path = matching[0]
    payload, error = read_status(path)
    if error:
        raise RuntimeError("classic lane status is unavailable: " + error)
    if payload is None:
        return {"status": "starting", "pid": holder}
    details = payload.get("lane_readiness")
    generation = payload.get("generation")
    if not isinstance(details, dict) or details.get("capability") != CAPABILITY:
        raise RuntimeError("classic completed-cycle capability is unavailable")
    if not isinstance(generation, dict):
        raise RuntimeError("classic native generation is unavailable")
    pid, birth = generation.get("pid"), details.get("birth")
    if type(pid) is not int or pid <= 0 or not isinstance(birth, str) or not birth:
        raise RuntimeError("classic native generation is malformed")
    native_state = identity_state(pid, birth)
    if native_state in {"dead", "reused"}:
        return {"status": "starting", "pid": holder}
    if native_state != "matching" or process_start_token(pid) != generation.get("start_token"):
        raise RuntimeError("classic native generation is unconfirmed")
    registry = Path(details.get("registry_root", ""))
    if not registry.is_absolute():
        raise RuntimeError("classic native custody is unavailable")
    native = next((row for row in inspect_instances(registry)
                   if row.get("instance_id") == details.get("instance_id")), None)
    if (
        native is None or native.get("state") != "running"
        or native.get("pid") != str(pid) or native.get("birth") != birth
        or not Path(native.get("runtime_root", "")).samefile(root)
    ):
        raise RuntimeError("classic native custody does not match")
    device, inode = path_identity(path)
    if not any(
        _path_name(Path(native.get(f"artifact_{index}_path", "")).absolute()) == _path_name(path.absolute())
        and native.get(f"artifact_{index}_device") == device
        and native.get(f"artifact_{index}_inode") == inode
        for index in range(int(native.get("artifact_count", "0")))
    ):
        return {"status": "starting", "pid": pid}
    if not is_locked(lock) or read_holder_pid(lock) != pid:
        raise RuntimeError("classic profile ownership is unconfirmed")
    if (
        payload.get("runtime_version") != __version__
        or details.get("settings_identity") != settings_identity(arguments)
        or _path_name(Path(details.get("profile", "")).absolute()) != _path_name(profile.absolute())
        or details.get("profile_generation") != hashlib.sha256(profile.read_bytes()).hexdigest()
    ):
        return {"status": "incumbent", "pid": pid, "native_observed": True}
    ready = completed_cycle_ready(payload)
    return {"status": "ready" if ready else "starting", "pid": pid, "native_observed": True}


def master_observation(root: Path, arguments: list[str]) -> dict | None:
    from agent_procutil.daemon_instances import inspect_instances
    from agent_procutil._daemon_instance_identity import identity_state
    from .supervise_registration_cli import build_profile_parser
    from .companion import process_start_token
    from .config import run_dir
    from .single_instance import is_locked, lock_path_for, read_holder_pid
    from .supervisor_registration import registration_scope, supervisor_lease_scope

    args = build_profile_parser().parse_args(arguments)
    scope = supervisor_lease_scope(*registration_scope(args))
    lock = lock_path_for(run_dir(), scope)
    if not lock.exists():
        return None
    if not is_locked(lock):
        return None
    payload, error = read_status(runtime_status_path(scope))
    if error:
        raise RuntimeError("master supervisor status is unavailable: " + error)
    if payload is None:
        return {"status": "starting"}
    generation = payload.get("generation", {})
    pid = generation.get("pid")
    if type(pid) is not int or not generation.get("start_token"):
        raise RuntimeError("master supervisor native ownership is unconfirmed")
    if read_holder_pid(lock) != pid or process_start_token(pid) != generation["start_token"]:
        # A successor may hold the lease before its first publication replaces
        # the predecessor's status. This is a hold, never absent or ready.
        return {"status": "starting"}
    native = next(
        (row for row in inspect_instances() if row.get("pid") == str(pid)
         and row.get("state") == "running"
         and Path(row.get("runtime_root", "")).absolute() == root.absolute()),
        None,
    )
    if native is None or identity_state(pid, native.get("birth")) != "matching":
        raise RuntimeError("master supervisor native custody is unavailable")
    details = payload.get("lane_readiness", {})
    if details.get("capability") != CAPABILITY:
        from .client_authority import supervisor_authority_target
        from .supervisor_source import effective_settings, read_prepared

        prepared = read_prepared(root, __version__, scope, pid=pid, authority=supervisor_authority_target(args))
        if prepared is None or prepared.get("settings") != effective_settings(args):
            raise RuntimeError("incumbent master completed-cycle support is unavailable")
    if (
        payload.get("runtime_version") != __version__
        or (details.get("capability") == CAPABILITY
            and details.get("settings_identity") != settings_identity(arguments))
    ):
        return {"status": "incumbent", "pid": pid}
    ready = completed_cycle_ready(payload)
    return {"status": "ready" if ready else "starting", "pid": pid}


def require_observed_incumbents(root: Path) -> None:
    if os.name != "nt":
        raise RuntimeError(
            "classic incumbent discovery is unavailable on this platform; no duplicate was started"
        )
    from .supervisor_processes import _is_supervisor_runtime, iter_windows_processes

    processes = [row for row in iter_windows_processes() if _is_supervisor_runtime(row, root)]
    if not processes:
        return
    known = set()
    for path, payload in _status_records(root, "lane-*.status.json"):
        details = payload.get("lane_readiness", {}) if payload else {}
        if not isinstance(details, dict):
            continue
        profile = details.get("profile")
        if isinstance(profile, str) and Path(profile).is_absolute():
            result = observation(root, Path(profile), [])
            if result is not None and result.get("native_observed"):
                known.add(result.get("pid"))
    if any(row.pid not in known for row in processes):
        raise RuntimeError(
            "incumbent supervisor has no supported profile readiness; "
            "no duplicate was started and no incumbent or worker was stopped"
        )


def ensure(root: Path, profile: Path, arguments: list[str], *, timeout=60.0, generation=None):
    from .action_controls import action_decision
    from .procutil import resolve_own_runtime_python, spawn_dispatch_daemon
    from .single_instance import SingleInstance, lock_path_for
    from .config import run_dir
    from .supervise_registration_cli import build_profile_parser

    require_selected_runtime(root)
    parsed = build_profile_parser().parse_args(arguments)
    if (
        parsed.command != "supervise" or getattr(parsed, "once", False)
        or getattr(parsed, "no_single_instance", False)
        or (arguments[1:2] != ["serve"] and not parsed.label)
        or not math.isfinite(parsed.interval) or parsed.interval <= 0
    ):
        raise ValueError("supervisor readiness requires its configured resident, label-gated role")
    generation = generation or hashlib.sha256(profile.read_bytes()).hexdigest()
    decision = action_decision("supervisor-reconcile", root=root)
    if decision.inhibited or decision.error:
        raise RuntimeError("classic startup policy is inhibited or unconfirmed")
    lock = SingleInstance(lock_path_for(run_dir(), profile_scope(profile) + ":ensure"))
    if not lock.acquire():
        raise RuntimeError("classic readiness ensure is already in progress")
    child = None
    master_handoff = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            require_selected_runtime(root)
            if hashlib.sha256(profile.read_bytes()).hexdigest() != generation:
                raise RuntimeError("supervisor profile changed during readiness")
            try:
                result = (
                    master_observation(root, arguments) if arguments[1:2] == ["serve"]
                    else observation(root, profile, arguments)
                )
            except RuntimeError as error:
                if str(error).endswith(("status-json-invalid", "status-read-unavailable")):
                    result = {"status": "unavailable"}
                else:
                    raise
            if result is not None and result["status"] == "ready":
                return result
            if arguments[1:2] == ["serve"] and result is not None:
                master_handoff = True
            if result is None and child is None and not master_handoff:
                if arguments[1:2] != ["serve"] or os.name == "nt":
                    require_observed_incumbents(root)
                environment = dict(os.environ, **{PROFILE_ENV: str(profile), GENERATION_ENV: generation})
                child = spawn_dispatch_daemon(
                    [resolve_own_runtime_python(), "-m", "agent_dispatch", *arguments],
                    log_name="supervise-service.log", env=environment, cwd=root,
                )
            if child is not None and child.poll() is not None:
                raise RuntimeError("classic supervisor exited before a completed cycle")
            if time.monotonic() >= deadline:
                raise RuntimeError("classic completed-cycle readiness remains unconfirmed")
            time.sleep(0.1)
    finally:
        lock.release()
