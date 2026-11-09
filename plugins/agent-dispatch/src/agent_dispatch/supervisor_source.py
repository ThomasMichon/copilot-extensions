"""Source preparation within the supervisor's existing status and singleton owners."""

from __future__ import annotations

import json
import hashlib
import logging
import os
from pathlib import Path
import re
import sys
import time

from . import __version__
from .supervisor_registration import supervisor_lease_scope
from .supervisor_status_io import read_status

log = logging.getLogger("agent-dispatch.supervisor-source")


def _directory(root: Path) -> Path:
    return root / "run" / "supervisor-candidates"


def read_prepared(
    root: Path, version: str, scope: str, *,
    authority=None, instance_id=None, pid=None, exclude_pid=None, expected=None,
):
    from agent_procutil._daemon_instance_identity import identity_state, path_identity
    from agent_procutil.daemon_instances import inspect_instances
    from .source_start import _path_name
    from .companion import process_start_token

    directory = _directory(root)
    try:
        directory.lstat()
    except FileNotFoundError:
        return None
    if _path_name(directory.resolve(strict=True)) != _path_name(directory.absolute()):
        raise RuntimeError("supervisor preparation directory is redirected")
    paths = sorted(path for path in directory.iterdir() if path.suffix == ".json")
    if len(paths) > 128:
        raise RuntimeError("supervisor preparation inventory exceeds its bound")
    for path in paths:
        if instance_id is not None and path.stem != instance_id:
            continue
        info = path.lstat()
        if (
            re.fullmatch(r"[0-9a-f]{32}", path.stem) is None
            or path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400
            or info.st_nlink != 1 or info.st_size > 16384
        ):
            raise RuntimeError("supervisor preparation is not an ordinary bounded status file")
        document, error = read_status(path)
        if error or document is None:
            raise RuntimeError("supervisor preparation status is unavailable")
        if document.get("runtime_version") != version or document.get("scope") != scope:
            continue
        generation = document.get("generation")
        if exclude_pid is not None and isinstance(generation, dict) and generation.get("pid") == exclude_pid:
            continue
        if pid is not None and isinstance(generation, dict) and generation.get("pid") != pid:
            continue
        if expected is not None and any(document.get(key) != value for key, value in expected.items()):
            continue
        owner = document.get("owner", {})
        if not isinstance(owner, dict):
            raise RuntimeError("supervisor preparation owner is malformed")
        if authority is not None and owner.get("authority_target") != authority:
            continue
        if (
            not isinstance(document.get("progress"), dict)
            or document["progress"].get("phase") != "prepared"
            or document.get("instance_id") != path.stem
            or not isinstance(generation, dict)
            or type(generation.get("pid")) is not int
            or not generation.get("start_token")
            or not isinstance(document.get("birth"), str)
        ):
            raise RuntimeError("supervisor preparation does not bind its native generation")
        observed = identity_state(generation["pid"], document["birth"])
        if observed in {"dead", "reused"}:
            continue
        if observed != "matching" or process_start_token(generation["pid"]) != generation["start_token"]:
            raise RuntimeError("supervisor preparation native generation is unavailable")
        registry = Path(document.get("registry_root", ""))
        if not registry.is_absolute():
            raise RuntimeError("supervisor preparation has no instance custody")
        native = next(
            (row for row in inspect_instances(registry) if row.get("instance_id") == path.stem),
            None,
        )
        if native is None or native.get("state") != "running":
            continue
        if (
            not Path(native.get("runtime_root", "")).samefile(root)
            or native.get("pid") != str(generation["pid"])
            or native.get("birth") != document["birth"]
            or identity_state(generation["pid"], document["birth"]) != "matching"
        ):
            raise RuntimeError("supervisor preparation native ownership changed")
        device, inode = path_identity(path)
        if not any(
            _path_name(Path(native.get(f"artifact_{index}_path", "")).absolute()) == _path_name(path.absolute())
            and native.get(f"artifact_{index}_device") == device
            and native.get(f"artifact_{index}_inode") == inode
            for index in range(int(native.get("artifact_count", "0")))
        ):
            continue
        return document
    return None


class CandidateAdmission:
    def __init__(self, root, version, guard, admitted, *, probe):
        self.root, self.version, self.guard = root, version, guard
        self.admitted, self.probe = admitted, probe
        self.record = None
        self.artifact = None

    def prepare(self, daemon):
        from .action_controls import action_inhibited
        from .source_start import require_selected_source

        if not require_selected_source().samefile(self.root) or self.version != __version__:
            raise RuntimeError("supervisor bootstrap does not match the selected Source")
        if action_inhibited("supervisor-reconcile", root=self.root, automatic=False, logger=log):
            raise RuntimeError("supervisor bootstrap is inhibited")
        verdict = daemon._recheck_governance("source-passive-bootstrap")
        if verdict is not None and verdict.get("status") != "ready":
            raise RuntimeError("supervisor bootstrap governance is unconfirmed")
        declarations = daemon.declared_source() if daemon.declared_source is not None else None
        if declarations is None or getattr(declarations, "complete", False) is not True:
            raise RuntimeError("supervisor bootstrap cohort source is unconfirmed")
        self.probe()
        self._publish(daemon)

    def _publish(self, daemon):
        from agent_procutil._daemon_instance_identity import birth_identity, path_identity
        from .client_authority import supervisor_authority_target
        from .companion import process_start_token
        from . import __main__ as cli

        receipt = self.guard.receipt
        directory = _directory(self.root)
        from .source_start import _path_name

        for parent in (self.root, self.root / "run", directory):
            if parent.exists() and _path_name(parent.resolve(strict=True)) != _path_name(parent.absolute()):
                raise RuntimeError("supervisor preparation directory is redirected")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (receipt.instance_id + ".json")
        arguments = cli.build_parser().parse_args(daemon._self_update_argv)
        self.record = {
            "runtime_version": self.version, "scope": supervisor_lease_scope(daemon.machine, daemon.env),
            "generation": {"pid": os.getpid(), "start_token": process_start_token(os.getpid())},
            "progress": {"phase": "prepared"}, "cycle_id": 0, "updated_at": time.time(),
            "owner": {"pid": os.getpid(), "authority_target": supervisor_authority_target(arguments)},
            "instance_id": receipt.instance_id, "registry_root": str(receipt.registry_root),
            "birth": birth_identity(os.getpid()),
            "settings": effective_settings(arguments),
        }
        binding = getattr(daemon, "_master_profile", None)
        if binding is not None:
            self.record["profile_binding"] = binding.preparation()
        if not self.record["generation"]["start_token"] or not self.record["birth"]:
            raise RuntimeError("supervisor preparation native birth is unavailable")
        pending = path.with_suffix(".pending")
        with pending.open("x", encoding="utf-8") as stream:
            json.dump(self.record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise RuntimeError("supervisor preparation already has a publisher")
        os.rename(pending, path)
        self.artifact = path, path_identity(path)
        self.guard.add_artifact(path)

    def check(self):
        from .self_update import read_current_version

        if read_current_version(self.root) != self.version:
            raise RuntimeError("supervisor candidate Source selection changed")
        if not _profile_current(self.record.get("profile_binding")):
            raise RuntimeError("supervisor candidate profile generation changed")
        if read_prepared(
            self.root, self.version, self.record["scope"],
            instance_id=self.record["instance_id"],
            expected={key: self.record.get(key) for key in ("settings", "profile_binding")},
        ) is None:
            raise RuntimeError("supervisor candidate preparation lost native custody")

    def close(self):
        if self.artifact is not None:
            from agent_procutil._daemon_instance_identity import delete_owned_file

            path, identity = self.artifact
            delete_owned_file(path, identity)


def acquire_candidate(daemon, admission, *, once, single_instance):
    from .companion import process_start_token
    from .config import run_dir
    from .single_instance import is_locked, lock_path_for, read_holder_pid
    from .supervisor_status_io import runtime_status_path

    if once or not single_instance:
        raise ValueError("passive supervisor cannot bypass singleton admission")
    admission.prepare(daemon)
    while True:
        admission.check()
        verdict = daemon._recheck_governance("source-passive-admission")
        if verdict is None or verdict.get("status") == "ready":
            if daemon.acquire_singleton():
                try:
                    admission.check()
                    admission.probe()
                    admission.admitted()
                    return True
                except BaseException:
                    daemon.release_singleton()
                    raise
            scope = supervisor_lease_scope(daemon.machine, daemon.env)
            status, error = read_status(runtime_status_path(scope))
            peer = status.get("generation") if isinstance(status, dict) else None
            if (
                error is None and isinstance(peer, dict)
                and peer.get("pid") != os.getpid()
                and status.get("runtime_version") == admission.version
                and peer.get("start_token")
                and process_start_token(peer["pid"]) == peer["start_token"]
                and is_locked(lock_path_for(run_dir(), scope))
                and read_holder_pid(lock_path_for(run_dir(), scope)) == peer["pid"]
                and read_prepared(
                    admission.root, admission.version, scope, pid=peer["pid"],
                    **({
                        "authority": admission.record["owner"]["authority_target"],
                        "expected": {key: admission.record.get(key) for key in ("settings", "profile_binding")},
                    } if getattr(admission, "record", None) is not None else {}),
                ) is not None
            ):
                return False
        daemon.sleep(daemon.poll_interval)


def configure_admission(daemon, args, guard, clients):
    from zdd.diagnostics import process_start_time
    from .config import load_config
    from .install_paths import install_dir
    from .source_start import _path_name, config_identity, require_selected_source
    from .supervise_cli import _client, _supervisor_authority_target, _write_supervisor_cycle_start

    root = _path_name(require_selected_source())
    if args is not None and getattr(daemon, "_master_profile", None) is None:
        _adopt_declared_primary(daemon, args, root)
    if getattr(args, "no_declared", False):
        daemon.declared_source = lambda: read_legacy_declarations(enabled=args.legacy_env)

    def probe():
        with _client(args, ensure=False, allow_ssh_failover=False) as peer:
            health = peer.health()
        slot = health.get("slot", {})
        pid = slot.get("pid")
        if (
            health.get("status") != "ok"
            or health.get("runtime_config_identity") != config_identity(load_config(), base_dir=install_dir())
            or type(pid) is not int or pid <= 0
            or not slot.get("process_start_time")
            or process_start_time(pid) != slot["process_start_time"]
        ):
            raise RuntimeError("supervisor bootstrap data access is not ready")

    def admitted():
        daemon.client = clients.enter_context(_client(args, ensure=False, allow_ssh_failover=False))
        _write_supervisor_cycle_start(
            supervisor_lease_scope(daemon.machine, daemon.env), 0, time.time(),
            authority_target=_supervisor_authority_target(args),
        )

    admission = CandidateAdmission(
        root, __version__, guard, admitted, probe=probe,
    )
    clients.callback(admission.close)
    return admission


def _adopt_declared_primary(daemon, args, root):
    from .client_authority import supervisor_authority_target
    from .source_start import SupervisorStartupPlan, supervisor_plan
    from .supervisor_profile import MasterProfile, configure_master_profile
    from .supervisor_registration import registration_scope
    from .registrar import RegistrarError

    try:
        (root / "supervisor.env").lstat()
        plan = supervisor_plan(root)
        if (
            not isinstance(plan, SupervisorStartupPlan) or plan.profile is None
            or getattr(args, "once", False) or getattr(args, "no_single_instance", False)
            or registration_scope(args) != (daemon.machine, daemon.env)
            or (plan.machine, plan.env) != (daemon.machine, daemon.env)
            or effective_settings(args) != effective_settings(plan.args)
            or supervisor_authority_target(args) != supervisor_authority_target(plan.args)
        ):
            return
        binding = MasterProfile(
            plan.profile, plan.profile_generation, daemon._self_update_argv, source_primary=True,
        )
    except (OSError, ValueError, RuntimeError, RegistrarError):
        # An unknown primary is not evidence that an unmarked custom role owns it.
        log.warning("Source primary binding is unconfirmed; unmarked candidate remains unbound")
        return
    configure_master_profile(daemon, binding)


def spawn_candidate(python, argv, *, root, version, profile_binding=None):
    from .procutil import spawn_dispatch_daemon

    return spawn_dispatch_daemon(
        [str(python), "-m", "agent_dispatch", *argv, "--source-candidate", version],
        log_name="logs/supervisor-candidate.log", cwd=root,
        **({"env": dict(os.environ, **profile_binding)} if profile_binding is not None else {}),
    )


def _authority(daemon):
    from . import __main__ as cli
    from .client_authority import supervisor_authority_target

    return supervisor_authority_target(cli.build_parser().parse_args(daemon._self_update_argv))


def is_profiled_application(root, version, target):
    from .source_build import _SOURCE_VERSION
    from .source_start import _path_name

    return (
        ".profile." in version and _SOURCE_VERSION.fullmatch(version) is not None
        and _path_name(Path(target).absolute().parent.parent)
        == _path_name(Path(root).absolute() / "versions" / version / ".venv")
    )


def effective_settings(args):
    from .supervisor_registration import registration_scope

    machine, env = registration_scope(args)
    return {
        "machine": machine, "env": env, "interval": float(args.interval),
        "legacy_env": bool(args.legacy_env), "no_declared": bool(getattr(args, "no_declared", False)),
    }


def read_legacy_declarations(*, enabled):
    from .pool_source_snapshot import DeclarationRead
    from .registrar import RegistrarError
    from .registrar_discovery import read_legacy_env_profiles

    try:
        profiles = read_legacy_env_profiles(strict=True) if enabled else ()
    except (OSError, UnicodeError, RegistrarError):
        return DeclarationRead((), False, "legacy-source-unavailable")
    return DeclarationRead(tuple(profiles), True)


def _expected_preparation(daemon):
    from . import __main__ as cli

    binding = getattr(daemon, "_master_profile", None)
    return {
        "settings": effective_settings(cli.build_parser().parse_args(daemon._self_update_argv)),
        "profile_binding": None if binding is None else binding.preparation(incoming=True),
    }


def _profile_current(binding):
    if binding is None:
        return True
    try:
        return hashlib.sha256(Path(binding["path"]).read_bytes()).hexdigest() == binding["generation"]
    except OSError:
        return False


def _cancel_owned_candidate(child, root):
    from agent_procutil.daemon_instances import cleanup_instance, inspect_instances
    from agent_procutil._daemon_instance_identity import identity_state, stop_identity

    receipt = getattr(child, "instance_receipt", None)
    if receipt is None:
        return False
    result = cleanup_instance(receipt, terminate=False)
    if result.status == "settled":
        return True
    if result.status == "missing":
        return child.poll() is not None
    if result.status != "live":
        return False
    row = next((item for item in inspect_instances(receipt.registry_root)
                if item.get("instance_id") == receipt.instance_id), None)
    if (
        row is None or row.get("state") != "running"
        or type(getattr(child, "pid", None)) is not int or child.pid <= 0
        or row.get("pid") != str(child.pid)
        or not Path(row.get("runtime_root", "")).samefile(root)
    ):
        return False
    targets = []
    for pid_key, birth_key in (("supervisor_pid", "supervisor_birth"), ("pid", "birth")):
        if not row.get(pid_key):
            continue
        if not isinstance(row.get(birth_key), str) or not row[birth_key]:
            return False
        pid, birth = int(row[pid_key]), row[birth_key]
        observed = identity_state(pid, birth)
        if observed == "dead":
            continue
        if observed != "matching":
            return False
        targets.append((pid, birth, pid_key == "supervisor_pid"))
    for pid, birth, graceful in targets:
        if not stop_identity(pid, birth, graceful=graceful):
            return False
    return cleanup_instance(receipt, terminate=False).status in {"settled", "missing"}


def _settled_launch_error(state):
    from agent_procutil.daemon_instances import cleanup_instance, inspect_instances
    from agent_procutil._daemon_instance_identity import identity_state

    error = state.get("spawn_error")
    receipt = getattr(error, "instance_receipt", None)
    if receipt is None or not getattr(receipt, "registry_root", None) or not getattr(receipt, "instance_id", None):
        return False
    try:
        row = next((item for item in inspect_instances(receipt.registry_root)
                    if item.get("instance_id") == receipt.instance_id), None)
        if row is None:
            identity = state.get("spawn_binding")
            if identity is None or identity_state(*identity) != "dead":
                return False
            return cleanup_instance(receipt, terminate=False).status == "settled"
        if (
            row.get("state") in {"reserved", "pending", "starting"}
            or not Path(row.get("runtime_root", "")).samefile(state["root"])
        ):
            return False
        pid, birth = int(row.get("pid", "0")), row.get("birth")
        if pid <= 0 or not isinstance(birth, str) or not birth:
            return False
        identity = (pid, birth)
        retained = getattr(error, "process_identity", None)
        if retained is not None and retained != identity:
            return False
        state["spawn_binding"] = identity
        if identity_state(*identity) != "dead":
            return False
        return cleanup_instance(receipt, terminate=False).status == "settled"
    except (OSError, RuntimeError, TypeError, ValueError):
        log.warning("retained supervisor launch outcome is still unconfirmed", exc_info=True)
        return False


def _restore_admission(daemon, state):
    from .supervisor_retirement import _retire_owned_workers, _tracked_work
    from .supervisor_daemon import _SHUTDOWN_GRACE_SECONDS

    pools = state.get("pools", set())
    if any(not future.done() for future in _tracked_work(daemon)):
        return False
    if pools and not _retire_owned_workers(pools, time.monotonic() + _SHUTDOWN_GRACE_SECONDS):
        return False
    if daemon._owns_runtime_executor and daemon._runtime_executor in pools:
        daemon._runtime_executor = None
    daemon._shutdown_drained = True
    with daemon._pool_metadata_admission_lock:
        daemon._pool_metadata_closed.clear()
    state.pop("pools", None)
    return True


def _settle_handoff_candidate(daemon, state):
    child = state.get("child")
    if child is None:
        ready = state.get("prepared")
        if ready is None:
            if not state.get("spawn_settled"):
                if not _settled_launch_error(state):
                    return False
                state["spawn_settled"] = True
                daemon._self_update_last_spawn = daemon.clock()
        else:
            from agent_procutil._daemon_instance_identity import identity_state

            # A discovered peer is not ours to terminate, even while we hold the lock.
            if identity_state(ready["generation"]["pid"], ready["birth"]) not in {"dead", "reused"}:
                return False
    else:
        try:
            settled = _cancel_owned_candidate(child, state["root"])
        except (OSError, RuntimeError, ValueError):
            log.warning("obsolete supervisor candidate custody is unconfirmed", exc_info=True)
            return False
        if not settled:
            return False
    return True


def _retarget_handoff(daemon, state):
    if not _settle_handoff_candidate(daemon, state):
        return False
    if _restore_admission(daemon, state):
        daemon._source_handoff = None
    return False


def _pause_handoff(daemon):
    state = daemon._source_handoff
    if state is not None:
        settled = _settle_handoff_candidate(daemon, state)
        if _restore_admission(daemon, state):
            if settled:
                daemon._source_handoff = None
            else:
                state["phase"] = "unconfirmed" if state.get("spawn_error") is not None else "preparing"
    return False


def advance_handoff(daemon, root, version, target):
    from .action_controls import action_inhibited
    from .self_update import read_current_version
    from .supervisor_retirement import _owned_pools, _retire_owned_workers, _tracked_work
    from .supervisor_daemon import _SHUTDOWN_GRACE_SECONDS
    from .supervisor_daemon import native_restart_unit

    state = daemon._source_handoff
    if action_inhibited("supervisor-self-update", root=root, automatic=True, logger=log):
        return _pause_handoff(daemon)
    if read_current_version(root) != version or (state is not None and state.get("retargeting")):
        if state is not None:
            state["retargeting"] = True
            return _retarget_handoff(daemon, state)
        return False
    try:
        native_unit = native_restart_unit()
    except RuntimeError:
        log.warning("Source supervisor native service ownership is unconfirmed")
        return False
    if native_unit is not None:
        log.warning("Source supervisor handoff remains with its native service owner")
        return False
    verdict = daemon._recheck_governance("source-handoff")
    if verdict is not None and verdict.get("status") != "ready":
        return _pause_handoff(daemon)
    scope = supervisor_lease_scope(daemon.machine, daemon.env)
    try:
        expected = _expected_preparation(daemon)
        if not _profile_current(expected["profile_binding"]):
            return _retarget_handoff(daemon, state) if state is not None else False
        ready = read_prepared(
            root, version, scope, authority=_authority(daemon),
            exclude_pid=os.getpid(), expected=expected,
        )
    except (OSError, RuntimeError, ValueError):
        log.warning("source supervisor preparation is unconfirmed; original custody retained", exc_info=True)
        return False
    state = daemon._source_handoff
    if ready is None:
        if state is None:
            from agent_procutil import DaemonSpawnError
            from agent_procutil.daemon_instances import cleanup_instance

            daemon._self_update_last_spawn = daemon.clock()
            try:
                child = spawn_candidate(
                    target, list(daemon._self_update_argv), root=root, version=version,
                    **({"profile_binding": daemon._master_profile.environment()}
                       if getattr(daemon, "_master_profile", None) is not None else {}),
                )
            except Exception as error:
                if (
                    isinstance(error, DaemonSpawnError) and error.process_created is False
                    and error.cleanup_confirmed and error.process_identity is None
                ):
                    receipt = getattr(error, "instance_receipt", None)
                    try:
                        settled = receipt is None or cleanup_instance(receipt, terminate=False).status in {"settled", "missing"}
                    except (OSError, RuntimeError, ValueError):
                        settled = False
                    if settled:
                        log.warning("source supervisor confirmed nonstart; retry deferred to cooldown")
                        return False
                daemon._source_handoff = {
                    "root": root, "version": version, "target": target,
                    "phase": "unconfirmed", "child": None, "spawn_error": error,
                }
                log.warning("source supervisor creation outcome is unconfirmed; original retained", exc_info=True)
                return False
            daemon._source_handoff = {
                "root": root, "version": version, "target": target, "phase": "preparing", "child": child,
            }
            if getattr(child, "instance_receipt", None) is None:
                raise RuntimeError("supervisor candidate has no native instance custody")
        elif (
            state.get("child") is not None and state["child"].poll() is not None
        ) or state.get("prepared") is not None or state.get("spawn_error") is not None:
            return _retarget_handoff(daemon, state)
        return False
    if state is None:
        state = {"root": root, "version": version, "target": target, "phase": "preparing"}
        daemon._source_handoff = state
    state["prepared"] = ready
    if daemon._managed_uncertain:
        return False
    state["phase"] = "withdrawing"
    if any(not future.done() for future in _tracked_work(daemon)):
        return False
    pools = state.setdefault("pools", set())
    pools.update(_owned_pools(daemon))
    if not daemon.shutdown() or daemon._units or daemon._managed_uncertain:
        return False
    pools.update(_owned_pools(daemon))
    if not _retire_owned_workers(pools, time.monotonic() + _SHUTDOWN_GRACE_SECONDS):
        return False
    if any(not future.done() for future in _tracked_work(daemon)):
        return False
    try:
        confirmed = read_prepared(
            root, version, scope, instance_id=ready["instance_id"],
            authority=_authority(daemon), exclude_pid=os.getpid(), expected=expected,
        )
        if not _profile_current(expected["profile_binding"]):
            return _retarget_handoff(daemon, state)
    except (OSError, RuntimeError, ValueError):
        return False
    if confirmed is None:
        return False
    if read_current_version(root) != version:
        return False
    if action_inhibited("supervisor-self-update", root=root, automatic=True, logger=log):
        return _pause_handoff(daemon)
    verdict = daemon._recheck_governance("source-pre-release")
    if verdict is not None and verdict.get("status") != "ready":
        return _pause_handoff(daemon)
    daemon.release_singleton()
    return True


def entry(args):
    from agent_procutil.daemon_instances import admit_daemon_instance
    from .source_start import _path_name, require_selected_source
    from .supervise_cli import _cmd_supervise_serve

    root = _path_name(require_selected_source())
    arguments = list(sys.argv[1:])
    if args.source_candidate != __version__ or arguments[-2:] != ["--source-candidate", __version__]:
        raise ValueError("supervisor candidate is not the selected complete Source")
    with admit_daemon_instance(
        "agent-dispatch", runtime_root=root, source=__file__, purpose="production",
    ) as guard:
        return _cmd_supervise_serve(args, source_guard=guard, source_argv=arguments[:-2])
