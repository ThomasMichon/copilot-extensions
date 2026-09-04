"""Lease broker -- exclusive borrowing of fleet containers.

State of record is a host-side JSON file (``~/.agent-containers/leases.json``)
guarded by an exclusive lock file for race-safety across parallel worktree
agents on the same machine. Leases are *advisory*: the ``container:`` resolver
does not hard-block dispatch, but ``borrow`` will not hand out a container that
is already leased to a live holder.

A lease can be reclaimed during acquisition when its exact same-host holder PID
is definitively gone and no provider lifecycle or session admission protects
the container. Remote and indeterminate holders remain leased until the TTL.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from ssh_manager.locks import pid_alive

from .config import LEASE_FILE, STATE_DIR, ContainersConfig, ensure_state_dir
from .lifecycle import list_containers
from .private_state import atomic_write_json

log = logging.getLogger("agent-containers")

_LOCK_FILE = STATE_DIR / "leases.lock"
_DEPLOY_HOLDS_FILE = STATE_DIR / "deploy-holds.json"
_SESSION_ADMISSIONS_FILE = STATE_DIR / "session-admissions.json"
# Leases are held by an *effort* (a logical entity), not by the short-lived
# CLI process that created them, so reclamation is TTL-based. A long-running
# holder can refresh via ``heartbeat``; otherwise a forgotten lease expires
# after the TTL. ``release`` is the normal way to free a lease.
DEFAULT_TTL = 24 * 3600.0
DEPLOY_HOLD_TTL = 15 * 60.0
SESSION_ADMISSION_TTL = 5 * 60.0
_RECORD_HEARTBEAT_INTERVAL = 30.0


@dataclass
class Lease:
    """An exclusive hold on a container by an effort."""

    container: str
    effort: str
    pid: int
    host: str
    acquired_at: float
    heartbeat_at: float
    reclaim_reason: str | None = None
    reclaimed_from_effort: str | None = None
    reclaimed_from_pid: int | None = None
    reclaimed_at: float | None = None

    def age(self) -> float:
        return time.time() - self.heartbeat_at


@dataclass
class DeployHold:
    """Provider-owned admission hold around destructive lifecycle work."""

    container: str
    operation: str
    token: str
    pid: int
    host: str
    environment: str
    acquired_at: float
    heartbeat_at: float
    expires_at: float
    uncertain: bool = False


@dataclass
class SessionAdmission:
    """Host-wrapper admission record held for one provider-launched session."""

    container: str
    token: str
    pid: int
    host: str
    environment: str
    acquired_at: float
    heartbeat_at: float


class ProviderAdmissionError(RuntimeError):
    """Provider lifecycle admission state is busy or indeterminate."""


class DeployHoldError(ProviderAdmissionError):
    """A provider lifecycle hold could not be acquired."""


def _this_host() -> str:
    return platform.node()


def _this_environment() -> str:
    if sys.platform == "win32":
        return "windows"
    release = platform.release().lower()
    if "microsoft" in release or "wsl" in release:
        return "wsl"
    return "posix"


@contextmanager
def _lease_lock(timeout: float = 10.0, poll: float = 0.05) -> Iterator[None]:
    """Cross-platform exclusive lock via O_CREAT|O_EXCL lock file."""
    ensure_state_dir()
    deadline = time.monotonic() + timeout
    fd = None
    owner_token = uuid.uuid4().hex
    while True:
        try:
            fd = os.open(
                str(_LOCK_FILE),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):
                pass
            os.write(fd, owner_token.encode("ascii"))
            os.fsync(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                # Stale lock recovery: if older than timeout*3, steal it.
                try:
                    age = time.time() - _LOCK_FILE.stat().st_mtime
                    if age > timeout * 3:
                        _LOCK_FILE.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                raise RuntimeError(
                    "Could not acquire lease lock (held by another process)"
                ) from None
            time.sleep(poll)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if _LOCK_FILE.read_text(encoding="ascii") == owner_token:
                _LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def _read_leases() -> dict[str, Lease]:
    """Read leases.json -> {container: Lease}. Returns {} if absent/corrupt."""
    if not LEASE_FILE.exists():
        return {}
    try:
        raw = json.loads(LEASE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("leases.json unreadable; treating as empty")
        return {}
    leases: dict[str, Lease] = {}
    for container, rec in (raw or {}).items():
        try:
            leases[container] = Lease(**rec)
        except TypeError:
            continue
    return leases


def _write_leases(leases: dict[str, Lease]) -> None:
    """Atomically write leases.json."""
    payload = {c: asdict(lease) for c, lease in leases.items()}
    _write_private_json(LEASE_FILE, payload)


def _read_records(path, record_type, *, fail_closed: bool = False):
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if fail_closed:
            raise ProviderAdmissionError(
                f"{path.name} is unreadable; refusing provider admission"
            ) from exc
        log.warning("%s unreadable; treating as empty", path.name)
        return {}
    if not isinstance(raw, dict):
        if fail_closed:
            raise ProviderAdmissionError(
                f"{path.name} does not contain a provider admission mapping"
            )
        return {}
    records = {}
    for key, rec in (raw or {}).items():
        try:
            records[key] = record_type(**rec)
        except TypeError as exc:
            if fail_closed:
                raise ProviderAdmissionError(
                    f"{path.name} contains an invalid provider admission record"
                ) from exc
            continue
    return records


def _write_records(path, records) -> None:
    _write_private_json(
        path,
        {key: asdict(value) for key, value in records.items()},
    )


def _write_private_json(path: Path, payload: dict) -> None:
    """Atomically publish owner-only coordination JSON."""
    atomic_write_json(path, payload, indent=2)


def _record_live(record, ttl: float) -> bool:
    expires_at = getattr(record, "expires_at", None)
    if expires_at is not None and time.time() >= expires_at:
        return False
    if getattr(record, "uncertain", False):
        return True
    if time.time() - record.heartbeat_at > ttl:
        return False
    if (
        record.host != _this_host()
        or record.environment != _this_environment()
    ):
        # Windows and WSL cannot safely inspect each other's process IDs.
        # Preserve the shared-Docker record until its bounded heartbeat TTL.
        return True
    return _pid_alive(record.pid)


def _pid_alive(pid: int) -> bool:
    """Use ssh-manager's OpenProcess-safe cross-platform liveness primitive."""
    return pid_alive(pid)


def _read_live_records(path, record_type, ttl: float):
    records = _read_records(path, record_type, fail_closed=True)
    try:
        live = {
            key: value
            for key, value in records.items()
            if _record_live(value, ttl)
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProviderAdmissionError(
            f"{path.name} contains invalid provider admission values"
        ) from exc
    if len(live) != len(records):
        _write_records(path, live)
    return live


def _heartbeat_record(
    path,
    record_type,
    key: str,
    token: str,
    ttl: float,
    stop: threading.Event,
) -> None:
    interval = min(_RECORD_HEARTBEAT_INTERVAL, max(1.0, ttl / 3))
    while not stop.wait(interval):
        try:
            with _lease_lock():
                records = _read_live_records(path, record_type, ttl)
                record = records.get(key)
                if record is None or record.token != token:
                    return
                record.heartbeat_at = time.time()
                _write_records(path, records)
        except RuntimeError:
            log.exception("Could not heartbeat provider admission record")


def _cleanup_record_silent(
    path: Path,
    record_type,
    ttl: float,
    key: str,
    token: str,
    *,
    preserve_uncertain: bool = False,
) -> None:
    """Best-effort cleanup that never masks the protected operation result."""
    try:
        with _lease_lock():
            records = _read_live_records(path, record_type, ttl)
            current = records.get(key)
            if current is None or current.token != token:
                return
            if preserve_uncertain and getattr(current, "uncertain", False):
                return
            del records[key]
            _write_records(path, records)
    except (OSError, RuntimeError) as exc:
        log.warning(
            "Could not clean provider admission record %s; leaving it "
            "fail-closed for TTL/lifecycle-clear: %s",
            path.name,
            exc,
        )


def _is_stale(lease: Lease, ttl: float) -> bool:
    """A lease is stale once it exceeds the TTL since its last heartbeat.

    Liveness is intentionally NOT tied to the borrowing process: a lease is
    held by an *effort* and persists across CLI invocations and agent
    dispatches until explicitly released or the TTL elapses.
    """
    return lease.age() > ttl


def _prune(leases: dict[str, Lease], ttl: float) -> dict[str, Lease]:
    """Drop stale leases in-place and return the cleaned dict."""
    live = {}
    for container, lease in leases.items():
        if _is_stale(lease, ttl):
            log.info(
                "Reclaiming stale lease: %s (effort=%s, host=%s, pid=%s)",
                container, lease.effort, lease.host, lease.pid,
            )
            continue
        live[container] = lease
    return live


def _lease_holder_liveness(lease: Lease) -> bool | None:
    """Return local holder liveness, or None when it cannot be established."""
    if lease.host != _this_host():
        return None
    try:
        alive = _pid_alive(lease.pid)
    except (OSError, RuntimeError) as exc:
        log.warning(
            "Could not determine lease holder liveness for %s "
            "(host=%s, pid=%s): %s",
            lease.container,
            lease.host,
            lease.pid,
            exc,
        )
        return None
    if alive is True:
        return True
    if alive is False:
        return False
    return None


def list_leases(ttl: float = DEFAULT_TTL, prune: bool = True) -> list[Lease]:
    """Return current (optionally pruned) leases."""
    with _lease_lock():
        leases = _read_leases()
        if prune:
            cleaned = _prune(leases, ttl)
            if len(cleaned) != len(leases):
                _write_leases(cleaned)
            leases = cleaned
        return list(leases.values())


@contextmanager
def provider_lease_guard(
    ttl: float = DEFAULT_TTL,
) -> Iterator[tuple[Lease, ...]]:
    """Hold lease state stable while a provider registry mutation completes."""
    with _lease_lock():
        leases = _read_leases()
        cleaned = _prune(leases, ttl)
        if len(cleaned) != len(leases):
            _write_leases(cleaned)
        yield tuple(cleaned.values())


def borrow(
    config: ContainersConfig,
    effort: str,
    container: str | None = None,
    fleet: str | None = None,
    ttl: float = DEFAULT_TTL,
) -> Lease:
    """Acquire an exclusive lease on a free fleet container for ``effort``.

    If ``container`` is given, lease that specific one (error if held by a
    different live effort). Otherwise pick the first free fleet member,
    preferring already-running containers.

    Re-borrowing the same container for the same effort is idempotent
    (refreshes the heartbeat).
    """
    with _lease_lock():
        admissions = _read_live_records(
            _SESSION_ADMISSIONS_FILE,
            SessionAdmission,
            SESSION_ADMISSION_TTL,
        )
        admitted = {
            admission.container
            for admission in admissions.values()
        }
        leases = _prune(_read_leases(), ttl)
        holds = _read_live_records(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
        )
        members = list_containers(config)
        if fleet:
            members = [c for c in members if c.fleet == fleet]
        if not members:
            raise RuntimeError(
                "No fleet containers found. Run `agent-containers up` first."
            )

        by_name = {c.name: c for c in members}
        reclaimed: Lease | None = None

        if container:
            if container not in by_name:
                raise RuntimeError(
                    f"Container '{container}' is not a known fleet member"
                )
            hold = holds.get(container)
            if hold:
                raise ProviderAdmissionError(
                    f"Container '{container}' is unavailable while provider "
                    f"{hold.operation} is in progress"
                )
            held = leases.get(container)
            if container in admitted and (
                held is None or held.effort != effort
            ):
                raise ProviderAdmissionError(
                    f"Container '{container}' has an active provider session"
                )
            if held and held.effort != effort:
                if _lease_holder_liveness(held) is False:
                    reclaimed = held
                else:
                    raise RuntimeError(
                        f"Container '{container}' is leased by effort "
                        f"'{held.effort}' (host={held.host}, pid={held.pid})"
                    )
            chosen = container
        else:
            # Effort-level idempotency: a normal fleet-scoped re-borrow should
            # refresh the effort's existing member, not report the fleet full.
            held_by_effort = sorted(
                lease.container
                for lease in leases.values()
                if lease.effort == effort and lease.container in by_name
            )
            if held_by_effort:
                chosen = held_by_effort[0]
                hold = holds.get(chosen)
                if hold:
                    raise ProviderAdmissionError(
                        f"Container '{chosen}' is unavailable while provider "
                        f"{hold.operation} is in progress"
                    )
            else:
                # Treat a definitively dead same-host holder as available, but
                # only when no lifecycle or connection admission protects it.
                available = []
                for member in members:
                    if member.name in holds or member.name in admitted:
                        continue
                    held = leases.get(member.name)
                    if held is None:
                        available.append((member, None))
                    elif _lease_holder_liveness(held) is False:
                        available.append((member, held))
                if not available:
                    raise RuntimeError(
                        "All fleet containers are currently leased or under "
                        "provider lifecycle hold. Release one or grow the fleet."
                    )
                available.sort(
                    key=lambda candidate: (
                        not candidate[0].is_running,
                        candidate[0].name,
                    )
                )
                chosen, reclaimed = (
                    available[0][0].name,
                    available[0][1],
                )

        now = time.time()
        previous = leases.get(chosen)
        same_effort = previous is not None and previous.effort == effort
        lease = Lease(
            container=chosen,
            effort=effort,
            pid=os.getpid(),
            host=_this_host(),
            acquired_at=previous.acquired_at if same_effort else now,
            heartbeat_at=now,
            reclaim_reason=(
                previous.reclaim_reason
                if same_effort
                else "dead-local-holder-pid" if reclaimed else None
            ),
            reclaimed_from_effort=(
                previous.reclaimed_from_effort
                if same_effort
                else reclaimed.effort if reclaimed else None
            ),
            reclaimed_from_pid=(
                previous.reclaimed_from_pid
                if same_effort
                else reclaimed.pid if reclaimed else None
            ),
            reclaimed_at=(
                previous.reclaimed_at
                if same_effort
                else now if reclaimed else None
            ),
        )
        leases[chosen] = lease
        _write_leases(leases)
        if reclaimed is not None:
            log.info(
                "Reclaimed lease on '%s' from effort '%s': "
                "dead-local-holder-pid (host=%s, pid=%s)",
                chosen,
                reclaimed.effort,
                reclaimed.host,
                reclaimed.pid,
            )
        log.info("Leased container '%s' to effort '%s'", chosen, effort)
        return lease


def release(target: str, ttl: float = DEFAULT_TTL) -> bool:
    """Release a lease by container name or effort name.

    Returns True if a lease was removed.
    """
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        to_remove = [
            c for c, lease in leases.items()
            if c == target or lease.effort == target
        ]
        if not to_remove:
            return False
        admissions = _read_live_records(
            _SESSION_ADMISSIONS_FILE,
            SessionAdmission,
            SESSION_ADMISSION_TTL,
        )
        active = sorted({
            admission.container
            for admission in admissions.values()
            if admission.container in to_remove
        })
        if active:
            raise ProviderAdmissionError(
                "Cannot release active provider session lease(s): "
                + ", ".join(active)
            )
        for c in to_remove:
            del leases[c]
            log.info("Released lease on '%s'", c)
        _write_leases(leases)
        return True


def heartbeat(container: str, ttl: float = DEFAULT_TTL) -> bool:
    """Refresh the heartbeat on a held lease. Returns True if updated."""
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        lease = leases.get(container)
        if not lease:
            return False
        lease.heartbeat_at = time.time()
        _write_leases(leases)
        return True


def get_lease(container: str, ttl: float = DEFAULT_TTL) -> Lease | None:
    """Return the lease for a container, or None if free."""
    for lease in list_leases(ttl=ttl):
        if lease.container == container:
            return lease
    return None


def get_deploy_hold(container: str) -> DeployHold | None:
    """Return a live provider lifecycle hold for ``container``, if any."""
    with _lease_lock():
        holds = _read_live_records(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
        )
        return holds.get(container)


def deploy_hold_status(container: str) -> dict:
    """Return observable hold state without weakening strict admission reads."""
    try:
        hold = get_deploy_hold(container)
    except ProviderAdmissionError as exc:
        return {
            "state": "unknown",
            "operation": None,
            "reason": str(exc),
        }
    if hold is None:
        return {"state": "none", "operation": None, "reason": None}
    return {
        "state": "active",
        "operation": hold.operation,
        "reason": None,
        "owner_environment": hold.environment,
        "heartbeat_age_seconds": max(0.0, time.time() - hold.heartbeat_at),
        "uncertain": hold.uncertain,
    }


def verify_deploy_hold(container: str, token: str) -> DeployHold:
    """Prove that the current live hold still belongs to this operation."""
    hold = get_deploy_hold(container)
    if hold is None or hold.token != token:
        raise ProviderAdmissionError(
            f"Provider lifecycle hold for '{container}' is no longer owned "
            "by this operation"
        )
    return hold


def mark_deploy_hold_uncertain(container: str, token: str) -> None:
    """Keep an unconfirmed Docker action fail-closed until hold expiry."""
    with _lease_lock():
        holds = _read_live_records(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
        )
        hold = holds.get(container)
        if hold is None or hold.token != token:
            raise ProviderAdmissionError(
                f"Provider lifecycle hold for '{container}' cannot record "
                "an unconfirmed action"
            )
        hold.uncertain = True
        hold.heartbeat_at = time.time()
        _write_records(_DEPLOY_HOLDS_FILE, holds)


def active_session_admissions(container: str) -> list[SessionAdmission]:
    """Return live provider wrapper admissions for one container."""
    with _lease_lock():
        admissions = _read_live_records(
            _SESSION_ADMISSIONS_FILE,
            SessionAdmission,
            SESSION_ADMISSION_TTL,
        )
        return [
            admission
            for admission in admissions.values()
            if admission.container == container
        ]


def clear_stale_provider_records(container: str | None = None) -> dict[str, int]:
    """Safely clear only expired/dead holds and session admissions.

    Fresh records owned by another OS environment remain fail-closed because
    Windows and WSL cannot safely inspect each other's process IDs.
    """
    cleared = {"deploy_holds": 0, "session_admissions": 0}
    with _lease_lock():
        for path, record_type, ttl, counter in (
            (
                _DEPLOY_HOLDS_FILE,
                DeployHold,
                DEPLOY_HOLD_TTL,
                "deploy_holds",
            ),
            (
                _SESSION_ADMISSIONS_FILE,
                SessionAdmission,
                SESSION_ADMISSION_TTL,
                "session_admissions",
            ),
        ):
            try:
                records = _read_records(path, record_type, fail_closed=True)
            except ProviderAdmissionError:
                try:
                    old_enough = time.time() - path.stat().st_mtime > ttl
                except OSError:
                    old_enough = False
                if not old_enough:
                    raise
                path.unlink(missing_ok=True)
                cleared[counter] += 1
                continue
            kept = {}
            for key, record in records.items():
                selected = container is None or record.container == container
                if selected and not _record_live(record, ttl):
                    cleared[counter] += 1
                    continue
                kept[key] = record
            if len(kept) != len(records):
                _write_records(path, kept)
    return cleared


@contextmanager
def deploy_hold(
    container: str,
    operation: str,
    *,
    max_lifetime: float = DEPLOY_HOLD_TTL,
) -> Iterator[DeployHold]:
    """Block new provider borrow/session admission during a destructive check."""
    if max_lifetime <= 0:
        raise DeployHoldError("Provider lifecycle hold lifetime must be positive")
    token = uuid.uuid4().hex
    now = time.time()
    hold = DeployHold(
        container=container,
        operation=operation,
        token=token,
        pid=os.getpid(),
        host=_this_host(),
        environment=_this_environment(),
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now + max_lifetime,
    )
    with _lease_lock():
        holds = _read_live_records(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
        )
        existing = holds.get(container)
        if existing:
            raise DeployHoldError(
                f"Container '{container}' already has a provider "
                f"{existing.operation} hold"
            )
        holds[container] = hold
        _write_records(_DEPLOY_HOLDS_FILE, holds)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_record,
        args=(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            container,
            token,
            DEPLOY_HOLD_TTL,
            heartbeat_stop,
        ),
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        yield hold
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2)
        _cleanup_record_silent(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
            container,
            token,
            preserve_uncertain=True,
        )


@contextmanager
def session_admission(
    container: str,
    *,
    expected_assignment: dict | None = None,
) -> Iterator[SessionAdmission]:
    """Admit one provider launch only when no destructive hold is present."""
    token = uuid.uuid4().hex
    key = f"{container}:{token}"
    admission = SessionAdmission(
        container=container,
        token=token,
        pid=os.getpid(),
        host=_this_host(),
        environment=_this_environment(),
        acquired_at=time.time(),
        heartbeat_at=time.time(),
    )
    with _lease_lock():
        if expected_assignment is not None:
            leases = _prune(_read_leases(), DEFAULT_TTL)
            lease = leases.get(container)
            actual_assignment = (
                {
                    "kind": "lease",
                    "effort": lease.effort,
                    "acquired_at": lease.acquired_at,
                }
                if lease is not None
                else None
            )
            if actual_assignment != expected_assignment:
                raise ProviderAdmissionError(
                    f"Container '{container}' lease assignment changed"
                )
        holds = _read_live_records(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
        )
        hold = holds.get(container)
        if hold:
            raise ProviderAdmissionError(
                f"Container '{container}' is unavailable while provider "
                f"{hold.operation} is in progress"
            )
        admissions = _read_live_records(
            _SESSION_ADMISSIONS_FILE,
            SessionAdmission,
            SESSION_ADMISSION_TTL,
        )
        admissions[key] = admission
        _write_records(_SESSION_ADMISSIONS_FILE, admissions)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_record,
        args=(
            _SESSION_ADMISSIONS_FILE,
            SessionAdmission,
            key,
            token,
            SESSION_ADMISSION_TTL,
            heartbeat_stop,
        ),
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        yield admission
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2)
        _cleanup_record_silent(
            _SESSION_ADMISSIONS_FILE,
            SessionAdmission,
            SESSION_ADMISSION_TTL,
            key,
            token,
        )
