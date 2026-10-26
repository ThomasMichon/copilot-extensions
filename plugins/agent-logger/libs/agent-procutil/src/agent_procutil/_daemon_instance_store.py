"""Filesystem implementation shared by daemon entry points and explicit GC."""

from __future__ import annotations

import base64
import contextlib
import errno
import math
import logging
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path

from ._daemon_instance_identity import (
    birth_identity, delete_owned_file, identity_state, path_identity, retire_self, stop_identity,
)
from .daemon_instances import (
    INSTANCE_ENV, REGISTRY_ENV, TEST_CONTEXT_ENV, DaemonInstanceGuard, DaemonInstanceReceipt,
    DaemonInstanceSpec, DaemonLeasePolicy, InstanceCleanupResult,
)

_MAX_RECORD = 65536
_ID = re.compile(r"[a-f0-9]{32}\Z")
_local_lock = threading.RLock()
_guards: dict[str, tuple[DaemonInstanceReceipt, threading.Event]] = {}
_monitor: threading.Thread | None = None
_monitor_pid = 0
_owners: dict[str, dict] = {}
_admitted: tuple[int, DaemonInstanceReceipt, int] | None = None
_expiry_job = None
_pause = threading.Event()
_wall_clock = time.time
_monotonic_clock = time.monotonic
_log = logging.getLogger("agent_procutil.daemon_instances")
_cursor_guard = threading.Lock()
_CURSOR_HEADER = b"copilot-daemon-reconcile-v1\n"


def _after_fork():
    global _local_lock, _monitor, _monitor_pid, _admitted, _pause, _cursor_guard
    _local_lock = threading.RLock()
    _monitor = None
    _monitor_pid = 0
    _admitted = None
    _pause = threading.Event()
    _cursor_guard = threading.Lock()
    _owners.clear()
    _guards.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _test_context(environment=None):
    encoded = (os.environ if environment is None else environment).get(TEST_CONTEXT_ENV)
    if not encoded:
        encoded = os.environ.get(TEST_CONTEXT_ENV)
    if not encoded:
        return None
    if len(encoded) > 8192:
        raise ValueError("test lifecycle declaration exceeds its bound")
    context = {}
    for line in base64.b64decode(encoded, validate=True).splitlines():
        key, value = line.split(b"\t")
        name = key.decode("ascii")
        if name in context or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise ValueError("invalid test lifecycle field")
        context[name] = base64.b64decode(value, validate=True).decode("utf-8")
    if (
        context["purpose"] not in ("unattended-test", "interactive-test")
        or identity_state(int(context["pid"]), context["birth"]) != "matching"
    ):
        raise ValueError("test lifecycle owner identity is unavailable")
    DaemonLeasePolicy(float(context["ttl"]), float(context["max_lifetime"]))
    return context


def require_declaration(purpose):
    with _local_lock:
        nested = _admitted is not None and _admitted[0] == os.getpid()
    if nested or os.environ.get(INSTANCE_ENV) or _test_context() is not None:
        return
    if purpose is None:
        raise ValueError("direct daemon startup requires an explicit purpose or lifecycle reservation")


def registry_root() -> Path:
    override = os.environ.get(REGISTRY_ENV)
    if override:
        return Path(override)
    if os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 28, None, 0, buffer) != 0:
            raise OSError("daemon registry user profile unavailable")
        return Path(buffer.value) / "copilot-extensions" / "daemon-instances"
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "state" / (
        "copilot-extensions"
    ) / "daemon-instances"


def _plain(path: Path, *, directory: bool = False, owned: bool = True) -> os.stat_result:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected(info.st_mode) or path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise OSError("daemon instance path is not an owned plain entry")
    if owned and os.name != "nt" and info.st_uid != os.getuid():
        raise OSError("daemon instance path has a foreign owner")
    if owned and os.name == "nt":
        from ._daemon_instance_owner import assert_path_owner
        assert_path_owner(path)
    return info


def _root(path: Path, *, create: bool = False) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("daemon registry root must be absolute")
    if create:
        # Reject indirection before creating anything below an existing prefix.
        missing = []
        current = path
        while not current.exists():
            missing.append(current)
            current = current.parent
        if current.resolve(strict=True) != current:
            raise OSError("daemon registry parent is redirected")
        for entry in reversed(missing):
            entry.mkdir(mode=0o700, exist_ok=True)
    if path.resolve(strict=True) != path:
        raise OSError("daemon registry root is redirected")
    _plain(path, directory=True)
    return path


def _runtime_anchor(path: Path) -> Path:
    path = Path(path)
    if not path.exists():
        return _root(path, create=True)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise OSError("daemon runtime anchor is redirected")
    # Existing code/config roots may be borrowed read-only metadata. This
    # never grants deletion of the directory or ownership of files below it.
    _plain(path, directory=True, owned=False)
    return path


def _receipt(receipt: DaemonInstanceReceipt) -> Path:
    if not _ID.fullmatch(receipt.instance_id):
        raise ValueError("invalid daemon instance id")
    return _root(receipt.registry_root) / (receipt.instance_id + ".instance")


@contextlib.contextmanager
def _locked(receipt: DaemonInstanceReceipt, *, wait_seconds: float = 0):
    record = _receipt(receipt)
    with _file_lock(record.with_suffix(".lock"), wait_seconds=wait_seconds):
        yield record


@contextlib.contextmanager
def _file_lock(path, *, wait_seconds=0, guard=None, nonblocking=False):
    guard = _local_lock if guard is None else guard
    if not guard.acquire(blocking=not nonblocking):
        raise BlockingIOError(errno.EAGAIN, "inventory collection already owned")
    try:
        try:
            _plain(path)
        except FileNotFoundError:
            pass
        descriptor = os.open(
            path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600,
        )
        try:
            with os.fdopen(descriptor, "r+b", buffering=0) as stream:
                before = os.fstat(stream.fileno())
                if not os.path.samestat(before, _plain(path)):
                    raise OSError("daemon instance lock replaced")
                deadline = _monotonic_clock() + wait_seconds
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            stream.seek(1 << 30)
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN) or _monotonic_clock() >= deadline:
                            raise
                        _pause.wait(.005)
                if not os.path.samestat(before, _plain(path)):
                    raise OSError("daemon instance lock replaced")
                yield
        except BaseException:
            raise
    finally:
        guard.release()


def _read(path: Path) -> dict[str, str]:
    before = _plain(path)
    with path.open("rb") as stream:
        if not os.path.samestat(before, os.fstat(stream.fileno())):
            raise OSError("daemon instance record replaced")
        raw = stream.read(_MAX_RECORD + 1)
    if len(raw) > _MAX_RECORD:
        raise ValueError("daemon instance record exceeds its bound")
    record = {}
    for line in raw.splitlines():
        key, value = line.split(b"\t")
        name = key.decode("ascii")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name in record:
            raise ValueError("invalid daemon instance field")
        record[name] = base64.b64decode(value, validate=True).decode("utf-8", errors="surrogateescape")
    if (
        record.get("schema") != "1" or record.get("instance_id") != path.stem
        or not _ID.fullmatch(record.get("instance_id", ""))
    ):
        raise ValueError("invalid daemon instance record identity")
    return record


def _write(path: Path, record: dict[str, str]) -> None:
    data = b"".join(
        key.encode("ascii") + b"\t" + base64.b64encode(
            str(value).encode("utf-8", errors="surrogateescape")
        ) + b"\n"
        for key, value in sorted(record.items())
    )
    if len(data) > _MAX_RECORD:
        raise ValueError("daemon instance record exceeds its bound")
    staging = path.with_name(path.name + "." + uuid.uuid4().hex + ".pending")
    try:
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(path):
            _plain(path)
        os.replace(staging, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            staging.unlink()


def source_provenance(source: Path) -> tuple[Path, str]:
    """Resolve a real source file or an existing member of a real zip artifact."""
    source = Path(source)
    if not source.is_absolute():
        raise ValueError("daemon source provenance must be absolute")
    candidate, members = source, []
    while not candidate.exists():
        members.insert(0, candidate.name)
        if candidate.parent == candidate:
            raise ValueError("daemon source provenance does not exist")
        candidate = candidate.parent
    if not candidate.is_file():
        raise ValueError("daemon source provenance must name an existing artifact")
    artifact = candidate.resolve(strict=True)
    if not members:
        return artifact, ""
    if artifact.suffix.lower() not in (".pyz", ".zip") or any(
        part in ("", ".", "..") for part in members
    ):
        raise ValueError("daemon source provenance is not a zip member")
    member = "/".join(members)
    try:
        with zipfile.ZipFile(artifact) as archive:
            if archive.getinfo(member).is_dir():
                raise ValueError("daemon source provenance is not a source member")
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError("daemon source member does not exist") from error
    return artifact, member


def reserve(spec: DaemonInstanceSpec, *, environment=None) -> DaemonInstanceReceipt:
    context = _test_context(environment)
    if context is not None:
        if spec.purpose == "interactive-test" and context["purpose"] != "interactive-test":
            raise ValueError("interactive daemon requires a pause-tolerant lifecycle owner")
        spec = replace(
            spec, purpose=context["purpose"], owner=context["owner"],
            registry_root=Path(context["registry_root"]),
            lease=DaemonLeasePolicy(float(context["ttl"]), float(context["max_lifetime"])),
        )
    source, source_member = source_provenance(spec.source)
    root = _root(spec.registry_root or registry_root(), create=True)
    runtime = _runtime_anchor(spec.runtime_root)
    receipt = DaemonInstanceReceipt(uuid.uuid4().hex, root)
    now = _wall_clock()
    device, inode = path_identity(runtime)
    record = {
        "schema": "1", "instance_id": receipt.instance_id, "state": "reserved",
        "service": spec.service, "purpose": spec.purpose,
        "lifecycle": spec.lifecycle,
        "owner": spec.owner or f"process:{os.getpid()}",
        "owner_pid": str(os.getpid()), "owner_birth": birth_identity(os.getpid()),
        "launcher_pid": str(os.getpid()), "launcher_birth": birth_identity(os.getpid()),
        "runtime_root": str(runtime), "runtime_device": device,
        "runtime_inode": inode,
        "source": str(source), "source_member": source_member,
        "created": str(now), "updated": str(now), "artifact_count": "0",
        "ttl": str(spec.lease.ttl_seconds if spec.lease else 0),
        "hard_deadline": str(now + spec.lease.max_lifetime_seconds if spec.lease else 0),
        "deadline": str(now + spec.lease.ttl_seconds if spec.lease else 0),
        "run_id": context["run_id"] if context else "",
        "protected": "0",
    }
    if context:
        record.update(owner_pid=str(context["pid"]), owner_birth=context["birth"])
    with _locked(receipt, wait_seconds=1) as path:
        if path.exists():
            raise OSError("daemon instance reservation already exists")
        _write(path, record)
    return receipt


def bind_process(receipt: DaemonInstanceReceipt, pid: int, *, observed_birth: str | None = None) -> None:
    birth = observed_birth if observed_birth is not None else birth_identity(pid)
    with _locked(receipt, wait_seconds=1) as path:
        record = _read(path)
        if record["state"] in ("running", "finished") and (
            record.get("pid"), record.get("birth")
        ) == (str(pid), birth):
            return
        if record["state"] not in ("reserved", "bound"):
            raise ValueError("daemon instance reservation already consumed")
        if (record["launcher_pid"], record["launcher_birth"]) != (
            str(os.getpid()), birth_identity(os.getpid()),
        ):
            raise ValueError("daemon launch belongs to a different process")
        if record["state"] == "bound" and (record["pid"], record["birth"]) != (str(pid), birth):
            raise ValueError("daemon instance process changed")
        record.update(state="bound", pid=str(pid), birth=birth, updated=str(_wall_clock()))
        _write(path, record)


def cancel_reservation(receipt, creation_refused):
    if creation_refused is not True:
        raise ValueError("unknown creation outcomes must retain their instance record")
    with _locked(receipt) as path:
        record = _read(path)
        if (
            record["state"] != "reserved" or "pid" in record
            or (record["launcher_pid"], record["launcher_birth"]) != (
                str(os.getpid()), birth_identity(os.getpid()),
            )
        ):
            raise ValueError("daemon reservation cannot be cancelled")
        path.unlink()


def finish_failed_launch(receipt, identity, *, exit_confirmed, admission_possible):
    """Settle tracking only; failed launch never grants application-file deletion."""
    with _locked(receipt, wait_seconds=1) as path:
        record = _read(path)
        if (record["launcher_pid"], record["launcher_birth"]) != (
            str(os.getpid()), birth_identity(os.getpid()),
        ):
            return False
        if identity is not None:
            pid, birth = identity
            if "pid" in record and (record["pid"], record["birth"]) != (str(pid), birth):
                return False
            if record["state"] not in ("reserved", "bound", "running", "finished"):
                return False
            record.update(pid=str(pid), birth=birth)
        if not exit_confirmed or identity is None:
            record["spawn_failure"] = "exit-unconfirmed" if not exit_confirmed else "identity-unavailable"
            _write(path, record)
            return False
        observed = identity_state(identity[0], identity[1])
        if observed != "dead":
            record["spawn_failure"] = "process-" + observed
            _write(path, record)
            return False
        if (
            not admission_possible and record["state"] != "running"
            and record["artifact_count"] == "0" and record.get("protected", "0") == "0"
        ):
            path.unlink()
            return True
        record.update(state="finished", spawn_failure="exit-confirmed-effects-held")
        _write(path, record)
        return False


def _expire(record: dict[str, str], now: float) -> bool:
    if record["purpose"] == "production":
        return False
    deadline, hard = float(record["deadline"]), float(record["hard_deadline"])
    if not 0 < deadline <= hard:
        raise ValueError("invalid daemon expiry policy")
    return now >= min(deadline, hard)


def admit(
    service, *, runtime_root=None, source=None, purpose=None,
    owner=None, registry_root=None, lease=None,
) -> DaemonInstanceGuard:
    with _local_lock:
        return _admit_owned(
            service, runtime_root=runtime_root, source=source, purpose=purpose,
            owner=owner, registry_root=registry_root, lease=lease,
        )


def _admit_owned(
    service, *, runtime_root=None, source=None, purpose=None,
    owner=None, registry_root=None, lease=None,
) -> DaemonInstanceGuard:
    global _admitted
    with _local_lock:
        if _admitted is not None and _admitted[0] == os.getpid():
            _, receipt, references = _admitted
            _admitted = (os.getpid(), receipt, references + 1)
            return DaemonInstanceGuard(receipt)
    inherited = os.environ.get(INSTANCE_ENV)
    if inherited:
        receipt = DaemonInstanceReceipt(
            inherited, Path(registry_root or os.environ[REGISTRY_ENV]),
        )
    else:
        context = _test_context()
        if context:
            if purpose == "interactive-test" and context["purpose"] != "interactive-test":
                raise ValueError("interactive daemon requires a pause-tolerant lifecycle owner")
            purpose = context["purpose"]
            lease = DaemonLeasePolicy(float(context["ttl"]), float(context["max_lifetime"]))
        elif purpose is None:
            raise ValueError(
                "direct daemon startup requires an explicit purpose or lifecycle reservation"
            )
        if source is None:
            source = getattr(sys.modules.get("__main__"), "__file__", None)
            if source is None:
                raise ValueError("direct daemon startup requires source provenance")
        source = Path(source).absolute()
        runtime = Path(runtime_root) if runtime_root is not None else source_provenance(source)[0].parent
        receipt = reserve(DaemonInstanceSpec(
            service, runtime, source, purpose, owner,
            Path(registry_root) if registry_root is not None else None, lease,
        ))
    me = birth_identity(os.getpid())
    with _locked(receipt, wait_seconds=1) as path:
        record = _read(path)
        if record["state"] == "running":
            if record.get("pid") != str(os.getpid()) or record.get("birth") != me:
                raise ValueError("daemon instance reservation already admitted")
        elif record["state"] not in ("reserved", "bound"):
            raise ValueError("daemon instance admission refused")
        if _expire(record, _wall_clock()):
            raise ValueError("daemon instance admission refused")
        if record.get("pid") and (record["pid"], record["birth"]) != (str(os.getpid()), me):
            raise ValueError("daemon instance admission process differs")
        record.update(state="running", pid=str(os.getpid()), birth=me, updated=str(_wall_clock()))
        _write(path, record)
    # Do not let later unassociated children consume their parent's reservation.
    os.environ.pop(INSTANCE_ENV, None)
    os.environ.pop(REGISTRY_ENV, None)
    if not record.get("supervisor_pid"):
        _register_monitor(receipt, record)
    with _local_lock:
        _admitted = (os.getpid(), receipt, 1)
    return DaemonInstanceGuard(receipt)


def renew(receipt: DaemonInstanceReceipt) -> None:
    with _locked(receipt, wait_seconds=1) as path:
        record = _read(path)
        if record["purpose"] == "production":
            raise ValueError("production daemon leases cannot be renewed")
        if (
            record["owner_pid"] != str(os.getpid())
            or record["owner_birth"] != birth_identity(os.getpid())
            or record["state"] not in ("reserved", "bound", "running")
        ):
            raise ValueError("daemon lease belongs to its test lifecycle owner")
        now = _wall_clock()
        if _expire(record, now):
            raise ValueError("expired daemon lease cannot be revived")
        record.update(deadline=str(min(now + float(record["ttl"]), float(record["hard_deadline"]))),
                      updated=str(now))
        _write(path, record)


def _register_monitor(receipt, record):
    global _monitor, _monitor_pid, _expiry_job
    if record["purpose"] == "production":
        return
    if os.name == "nt" and _expiry_job is None:
        from . import _assign_suspended_to_kill_on_close_job
        _expiry_job = _assign_suspended_to_kill_on_close_job(os.getpid(), allow_breakaway=False)
        if _expiry_job is None:
            raise OSError("disposable daemon descendant custody unavailable")
    with _local_lock:
        if _monitor_pid != os.getpid():
            _guards.clear()
            _monitor = None
            _monitor_pid = os.getpid()
        _guards[receipt.instance_id] = (receipt, threading.Event())
        _ensure_monitor()


def _ensure_monitor():
    global _monitor
    if _monitor is None or not _monitor.is_alive():
        _monitor = threading.Thread(target=_observe_expiry, name="daemon-instance-lifecycle", daemon=True)
        _monitor.start()


@contextlib.contextmanager
def test_lifecycle(owner, root, purpose, policy):
    if not owner or len(owner) > 200 or any(ord(char) < 32 for char in owner):
        raise ValueError("test lifecycle owner must be explicit and printable")
    if purpose == "production":
        raise ValueError("test lifecycle purpose must be explicit")
    if policy is None:
        if purpose != "unattended-test":
            raise ValueError("interactive tests require an explicit pause-tolerant lease")
        policy = DaemonLeasePolicy()
    root = _root(root, create=True)
    context = {
        "run_id": uuid.uuid4().hex, "owner": owner, "pid": os.getpid(),
        "birth": birth_identity(os.getpid()), "registry_root": str(root),
        "purpose": purpose, "ttl": policy.ttl_seconds,
        "max_lifetime": policy.max_lifetime_seconds,
        "next_renew": 0,
    }
    with _local_lock:
        _owners[context["run_id"]] = context
        _ensure_monitor()
    try:
        declaration = b"".join(
            key.encode("ascii") + b"\t" + base64.b64encode(str(value).encode()) + b"\n"
            for key, value in sorted(context.items()) if key != "next_renew"
        )
        yield {TEST_CONTEXT_ENV: base64.b64encode(declaration).decode("ascii")}
    finally:
        with _local_lock:
            _owners.pop(context["run_id"], None)
        # Explicit test teardown is primary; expiry and installer GC are backstops.
        for record in inspect(root):
            if record.get("run_id") != context["run_id"]:
                continue
            receipt = DaemonInstanceReceipt(record["instance_id"], root)
            try:
                with _locked(receipt, wait_seconds=1) as path:
                    current = _read(path)
                    current["deadline"] = str(min(_wall_clock(), float(current["deadline"])))
                    _write(path, current)
                outcome = cleanup(receipt)
                if outcome.status == "held":
                    _log.warning("daemon instance cleanup held: %s %s", receipt.instance_id, outcome.reason)
            except (OSError, ValueError, KeyError) as error:
                # Preserve the exact original entry for explicit reconciliation.
                _log.warning(
                    "daemon instance cleanup held: %s %s", receipt.instance_id, type(error).__name__,
                )
                continue


def _observe_expiry():
    observations = {}
    while True:
        with _local_lock:
            entries = list(_guards.values())
            owners = list(_owners.values())
        if not entries and not owners:
            return
        for context in owners:
            if _monotonic_clock() < context["next_renew"]:
                continue
            context["next_renew"] = _monotonic_clock() + min(30, float(context["ttl"]) / 3)
            try:
                records = inspect(Path(context["registry_root"]))
            except (OSError, ValueError):
                continue
            for record in records:
                if record.get("run_id") != context["run_id"] or record.get("state") not in (
                    "reserved", "bound", "running",
                ):
                    continue
                if _wall_clock() < float(record["deadline"]) - float(record["ttl"]) / 3:
                    continue
                try:
                    renew(DaemonInstanceReceipt(record["instance_id"], Path(context["registry_root"])))
                except (OSError, ValueError, KeyError):
                    continue
        for receipt, stopped in entries:
            if stopped.is_set():
                continue
            try:
                with _locked(receipt) as path:
                    record = _read(path)
                if (record.get("pid"), record.get("birth")) != (
                    str(os.getpid()), birth_identity(os.getpid()),
                ):
                    raise ValueError("daemon monitor instance identity changed")
                previous = observations.get(receipt.instance_id)
                monotonic = _monotonic_clock()
                if previous is None:
                    previous = (
                        monotonic, min(float(record["deadline"]), float(record["hard_deadline"])) - _wall_clock(),
                        record["updated"], monotonic + max(0, float(record["hard_deadline"]) - _wall_clock()),
                    )
                elif record["updated"] != previous[2]:
                    previous = (
                        monotonic, min(float(record["deadline"]), float(record["hard_deadline"])) - _wall_clock(),
                        record["updated"], previous[3],
                    )
                observations[receipt.instance_id] = previous
                if (
                    _expire(record, _wall_clock()) or monotonic - previous[0] >= previous[1]
                    or monotonic >= previous[3]
                ):
                    retire_self()
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    continue
                retire_self()
            except (ValueError, KeyError):
                # A lost/foreign record cannot extend an admitted disposable life.
                retire_self()
        _pause.wait(0.1)


def add_artifact(receipt: DaemonInstanceReceipt, artifact: Path) -> None:
    with _locked(receipt, wait_seconds=1) as path:
        record = _read(path)
        if (record.get("pid"), record.get("birth")) != (str(os.getpid()), birth_identity(os.getpid())):
            raise ValueError("daemon artifact custody belongs to the admitted process")
        root = Path(record["runtime_root"])
        if not artifact.is_absolute() or artifact.parent.resolve(strict=True) != artifact.parent:
            raise ValueError("daemon artifact parent is redirected")
        artifact.relative_to(root)
        if artifact.is_relative_to(receipt.registry_root):
            raise ValueError("daemon inventory is not an application lifecycle artifact")
        if artifact == Path(record["source"]):
            raise ValueError("daemon source is not a lifecycle artifact")
        _plain(artifact)
        device, inode = path_identity(artifact)
        count = int(record["artifact_count"])
        if count >= 64:
            raise ValueError("daemon lifecycle artifact declaration exceeds its bound")
        record.update({
            f"artifact_{count}_path": str(artifact),
            f"artifact_{count}_device": device,
            f"artifact_{count}_inode": inode,
            "artifact_count": str(count + 1),
        })
        _write(path, record)


def close_guard(receipt):
    global _admitted
    with _local_lock:
        if _admitted is not None and _admitted[:2] == (os.getpid(), receipt):
            if _admitted[2] > 1:
                _admitted = (os.getpid(), receipt, _admitted[2] - 1)
                return
            _admitted = None
    with _locked(receipt, wait_seconds=1) as path:
        record = _read(path)
        if (record.get("pid"), record.get("birth")) != (str(os.getpid()), birth_identity(os.getpid())):
            raise ValueError("daemon instance custody belongs to another process")
        record.update(state="finished", updated=str(_wall_clock()))
        _write(path, record)
    with _local_lock:
        entry = _guards.pop(receipt.instance_id, None)
        if entry:
            entry[1].set()


def _inventory_paths(root):
    if not os.access(root, os.R_OK | os.X_OK):
        raise PermissionError(errno.EACCES, "daemon instance inventory unreadable", str(root))
    # pathlib.glob suppresses PermissionError and would turn denied inventory
    # into a false empty result. Preserve filesystem errors explicitly.
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.name.endswith(".instance"):
                yield root / entry.name


def _inventory_exists(root):
    if not root.is_absolute():
        raise ValueError("daemon registry root must be absolute")
    try:
        root.lstat()
    except FileNotFoundError:
        return False
    return True


def inspect(root=None):
    root = Path(root) if root is not None else registry_root()
    if not _inventory_exists(root):
        return []
    _root(root)
    results = []
    for path in sorted(_inventory_paths(root)):
        try:
            results.append(_read(path))
        except (OSError, ValueError, UnicodeError):
            results.append({"instance_id": path.stem, "state": "held", "reason": "invalid-record"})
    return results


def cleanup(receipt, *, terminate=True, stop_timeout=5):
    removed = []

    def result(status, reason):
        return InstanceCleanupResult(receipt.instance_id, status, reason, tuple(removed))

    try:
        with _locked(receipt) as path:
            try:
                record = _read(path)
            except FileNotFoundError:
                return result("missing", "record-absent")
            if record["state"] not in ("reserved", "bound", "running", "finished", "cleaning"):
                return result("held", "unknown-state")
            if "pid" not in record:
                return result("held", "launch-outcome-unknown")
            if record.get("protected", "0") != "0":
                return result("held", "protected-work")
            if record.get("lifecycle") == "finite-worker" and record["purpose"] == "production":
                if record.get("spawn_failure"):
                    return result("held", "spawn-failure-held")
                observed = identity_state(int(record["pid"]), record["birth"])
                if observed == "matching":
                    return result("live", "finite-worker-running")
                if observed != "dead":
                    return result("held", "process-" + observed)
                path.unlink()
                return result("settled", "finite-worker-exit-observed")
            root = Path(record["runtime_root"])
            _plain(root, directory=True, owned=False)
            if root.resolve(strict=True) != root or path_identity(root) != (
                record["runtime_device"], record["runtime_inode"],
            ):
                return result("held", "runtime-root-changed")
            count = int(record["artifact_count"])
            if not 0 <= count <= 64:
                return result("held", "invalid-artifact-count")
            artifacts = []
            for index in range(count):
                artifact = Path(record[f"artifact_{index}_path"])
                artifact.relative_to(root)
                if artifact.is_relative_to(receipt.registry_root):
                    return result("held", "inventory-artifact-refused")
                if artifact.parent.resolve(strict=True) != artifact.parent:
                    return result("held", "artifact-parent-changed")
                try:
                    _plain(artifact)
                except FileNotFoundError:
                    continue
                if path_identity(artifact) != (
                    record[f"artifact_{index}_device"], record[f"artifact_{index}_inode"],
                ):
                    return result("held", "artifact-identity-changed")
                artifacts.append(artifact)
            state = identity_state(int(record["pid"]), record["birth"])
            if state in ("unknown", "reused"):
                return result("held", "process-" + state)
            supervisor_pid = record.get("supervisor_pid")
            witness = path.with_suffix(".settled")
            if supervisor_pid:
                supervisor_state = identity_state(int(supervisor_pid), record["supervisor_birth"])
                if supervisor_state in ("unknown", "reused"):
                    return result("held", "supervisor-" + supervisor_state)
                if supervisor_state == "matching":
                    if not _expire(record, _wall_clock()):
                        return result("live", "production-or-unexpired")
                    if not terminate or not stop_identity(
                        int(supervisor_pid), record["supervisor_birth"],
                        timeout=stop_timeout, graceful=True,
                    ):
                        return result("held", "descendant-settlement-unconfirmed")
                if record.get("tree_settled") != "1":
                    try:
                        proof = _read(witness)
                    except (OSError, ValueError):
                        return result("held", "descendant-settlement-unconfirmed")
                    if proof.get("tree_settled") != "1" or any(
                        proof.get(key) != record[key] for key in (
                            "pid", "birth", "supervisor_pid", "supervisor_birth",
                        )
                    ):
                        return result("held", "descendant-settlement-unconfirmed")
                    record["tree_settled"] = "1"
                    _write(path, record)
                state = identity_state(int(record["pid"]), record["birth"])
                if state != "dead":
                    return result("held", "process-" + state)
            if state == "matching":
                if not _expire(record, _wall_clock()):
                    return result("live", "production-or-unexpired")
                if not terminate:
                    return result("held", "stop-required")
                if not stop_identity(int(record["pid"]), record["birth"], timeout=stop_timeout):
                    return result("held", "stop-unconfirmed")
            record["state"] = "cleaning"
            _write(path, record)
            for artifact in artifacts:
                _plain(artifact)
                index = next(i for i in range(count) if record[f"artifact_{i}_path"] == str(artifact))
                if path_identity(artifact) != (
                    record[f"artifact_{index}_device"], record[f"artifact_{index}_inode"],
                ):
                    return result("held", "artifact-identity-changed")
                delete_owned_file(artifact, (
                    record[f"artifact_{index}_device"], record[f"artifact_{index}_inode"],
                ))
                removed.append(str(artifact))
            if supervisor_pid and witness.exists():
                _plain(witness)
                witness.unlink()
                removed.append(str(witness))
            path.unlink()
            # The canonical lock entry stays: unlinking a lock permits split custody.
            return result("settled", "process-exit-observed")
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        return result("held", type(error).__name__)


def _read_cursor(root):
    path = root / ".reconcile.cursor"
    try:
        info = _plain(path)
    except FileNotFoundError:
        return ""
    with path.open("rb") as stream:
        if not os.path.samestat(info, os.fstat(stream.fileno())):
            raise OSError("inventory cursor replaced")
        raw = stream.read(97)
    if not raw.startswith(_CURSOR_HEADER) or not raw.endswith(b"\n"):
        raise ValueError("invalid inventory cursor")
    key = raw[len(_CURSOR_HEADER):-1].decode("ascii")
    if key and not _ID.fullmatch(key):
        raise ValueError("invalid inventory cursor")
    return key


def _write_cursor(root, key):
    path = root / ".reconcile.cursor"
    if os.path.lexists(path):
        _plain(path)
    pending = root / (".reconcile." + uuid.uuid4().hex + ".pending")
    try:
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(_CURSOR_HEADER + key.encode("ascii") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(path):
            _plain(path)
        os.replace(pending, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            pending.unlink()


def reconcile(root, limit, max_seconds=5):
    started = _monotonic_clock()
    if type(limit) is not int or not 1 <= limit <= 4096:
        raise ValueError("daemon reconciliation limit must be between 1 and 4096")
    if isinstance(max_seconds, bool) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("daemon reconciliation deadline must be finite and positive")
    root = Path(root) if root is not None else registry_root()
    if not _inventory_exists(root):
        return []
    _root(root)
    paths = sorted(_inventory_paths(root))
    # A bad marker remains a visible error even when there is no work.
    cursor = _read_cursor(root)
    if not paths:
        return []
    if any(not _ID.fullmatch(path.stem) for path in paths):
        raise ValueError("noncanonical inventory instance key")
    results = []
    deadline = started + max_seconds
    with _file_lock(
        root / ".reconcile.lock", guard=_cursor_guard, nonblocking=True,
    ):
        cursor = _read_cursor(root)
        ordered = [path for path in paths if path.stem > cursor]
        ordered += [path for path in paths if path.stem <= cursor]
        visited = None
        try:
            for count, path in enumerate(ordered):
                if count >= limit:
                    break
                remaining = deadline - _monotonic_clock()
                if remaining <= 0:
                    break
                visited = path.stem
                try:
                    candidate = _read(path)
                    finite_production = (
                        candidate["purpose"] == "production"
                        and candidate.get("lifecycle") == "finite-worker"
                    )
                    if not finite_production and (
                        candidate["purpose"] == "production" or not _expire(candidate, _wall_clock())
                    ):
                        continue
                except (OSError, ValueError, KeyError):
                    results.append(InstanceCleanupResult(path.stem, "held", "invalid-record"))
                    continue
                results.append(cleanup(
                    DaemonInstanceReceipt(path.stem, root), stop_timeout=min(5, remaining),
                ))
        finally:
            if visited is not None and visited != cursor:
                _write_cursor(root, visited)
    return results
