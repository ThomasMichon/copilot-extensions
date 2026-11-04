"""Source preparation within the supervisor's existing status and singleton owners."""

from __future__ import annotations

import json
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


def read_prepared(root: Path, version: str, scope: str, *, authority=None, instance_id=None, pid=None):
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
        if pid is not None and isinstance(generation, dict) and generation.get("pid") != pid:
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
            or process_start_token(generation["pid"]) != generation["start_token"]
            or not isinstance(document.get("birth"), str)
        ):
            raise RuntimeError("supervisor preparation does not bind its native generation")
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
        }
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
        if read_prepared(
            self.root, self.version, self.record["scope"],
            instance_id=self.record["instance_id"],
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
                and read_prepared(admission.root, admission.version, scope, pid=peer["pid"]) is not None
            ):
                return False
        daemon.sleep(daemon.poll_interval)


def configure_admission(daemon, args, guard, clients):
    from zdd.diagnostics import process_start_time
    from .config import load_config
    from .source_start import _path_name, config_identity, require_selected_source
    from .supervise_cli import _client, _supervisor_authority_target, _write_supervisor_cycle_start

    def probe():
        with _client(args, ensure=False, allow_ssh_failover=False) as peer:
            health = peer.health()
        slot = health.get("slot", {})
        pid = slot.get("pid")
        if (
            health.get("status") != "ok"
            or health.get("runtime_config_identity") != config_identity(load_config())
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
        _path_name(require_selected_source()), __version__, guard, admitted, probe=probe,
    )
    clients.callback(admission.close)
    return admission


def spawn_candidate(python, argv, *, root, version):
    from .procutil import spawn_dispatch_daemon

    return spawn_dispatch_daemon(
        [str(python), "-m", "agent_dispatch", *argv, "--source-candidate", version],
        log_name="logs/supervisor-candidate.log", cwd=root,
    )


def _authority(daemon):
    from . import __main__ as cli
    from .client_authority import supervisor_authority_target

    return supervisor_authority_target(cli.build_parser().parse_args(daemon._self_update_argv))


def advance_handoff(daemon, root, version, target):
    from .action_controls import action_inhibited
    from .self_update import read_current_version
    from .supervisor_retirement import _owned_pools, _retire_owned_workers, _tracked_work
    from .supervisor_daemon import _SHUTDOWN_GRACE_SECONDS
    from .supervisor_daemon import native_restart_unit

    if read_current_version(root) != version:
        return False
    if action_inhibited("supervisor-self-update", root=root, automatic=True, logger=log):
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
        return False
    scope = supervisor_lease_scope(daemon.machine, daemon.env)
    try:
        ready = read_prepared(root, version, scope, authority=_authority(daemon))
    except (OSError, RuntimeError, ValueError):
        log.warning("source supervisor preparation is unconfirmed; original custody retained", exc_info=True)
        return False
    state = daemon._source_handoff
    if ready is None:
        if state is None:
            try:
                child = spawn_candidate(
                    target, list(daemon._self_update_argv), root=root, version=version,
                )
            except Exception:
                daemon._source_handoff = {
                    "root": root, "version": version, "target": target,
                    "phase": "unconfirmed", "child": None,
                }
                log.warning("source supervisor creation outcome is unconfirmed; original retained", exc_info=True)
                return False
            daemon._source_handoff = {
                "root": root, "version": version, "target": target, "phase": "preparing", "child": child,
            }
            if getattr(child, "instance_receipt", None) is None:
                raise RuntimeError("supervisor candidate has no native instance custody")
        elif state.get("child") is not None and state["child"].poll() is not None:
            from agent_procutil.daemon_instances import cleanup_instance

            if cleanup_instance(state["child"].instance_receipt, terminate=False).status in {"settled", "missing"}:
                daemon._source_handoff = None
        return False
    if state is None:
        state = {"root": root, "version": version, "target": target}
        daemon._source_handoff = state
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
        confirmed = read_prepared(root, version, scope, instance_id=ready["instance_id"])
    except (OSError, RuntimeError, ValueError):
        return False
    if confirmed is None:
        return False
    if read_current_version(root) != version:
        return False
    if action_inhibited("supervisor-self-update", root=root, automatic=True, logger=log):
        return False
    verdict = daemon._recheck_governance("source-pre-release")
    if verdict is not None and verdict.get("status") != "ready":
        return False
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
