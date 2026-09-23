"""Lease broker for CodeSpace borrows, worktree claims, and reclaim holds."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from agent_procutil import no_window_flags
from ssh_manager.locks import pid_alive

from . import coordination
from .config import RUNTIME_DIR, ensure_runtime_dir

log = logging.getLogger("agent-codespaces")

LEASE_FILE = RUNTIME_DIR / "leases.json"
_LOCK_FILE = RUNTIME_DIR / "leases.lock"
_DEPLOY_HOLDS_FILE = RUNTIME_DIR / "deploy-holds.json"
# Leases are held by an *effort*, not by the CLI process, so reclamation is
# TTL-based. A long-running holder can refresh via ``heartbeat``; otherwise a
# forgotten lease expires after the TTL. ``release`` is the normal way to free.
DEFAULT_TTL = 24 * 3600.0
DEPLOY_HOLD_TTL = 15 * 60.0
_RECORD_HEARTBEAT_INTERVAL = 30.0


@dataclass
class Lease:
    """An advisory effort lease or an exclusive worktree claim."""

    codespace: str
    effort: str
    pid: int
    host: str
    acquired_at: float
    heartbeat_at: float
    worktree: str = ""
    # L2 cross-machine fencing token; empty for legacy/L1-only records.
    lease_token: str = ""

    def age(self) -> float:
        return time.time() - self.heartbeat_at


@dataclass
class DeployHold:
    """Provider-owned admission hold around destructive lifecycle work."""

    codespace: str
    operation: str
    token: str
    pid: int
    host: str
    environment: str
    acquired_at: float
    heartbeat_at: float
    expires_at: float
    uncertain: bool = False


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
    ensure_runtime_dir()
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
    """Read leases.json -> {codespace: Lease}. Returns {} if absent/corrupt."""
    if not LEASE_FILE.exists():
        return {}
    try:
        raw = json.loads(LEASE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("leases.json unreadable; treating as empty")
        return {}
    leases: dict[str, Lease] = {}
    for codespace, rec in (raw or {}).items():
        try:
            leases[codespace] = Lease(**rec)
        except TypeError:
            continue
    return leases


def _write_leases(leases: dict[str, Lease]) -> None:
    """Atomically write leases.json."""
    _write_private_json(
        LEASE_FILE,
        {codespace: asdict(lease) for codespace, lease in leases.items()},
    )


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
    ensure_runtime_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(payload, indent=2).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


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
        # Preserve the shared record until its bounded heartbeat TTL.
        return True
    return pid_alive(record.pid)


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
    """A lease is stale once its heartbeat exceeds the TTL."""
    return lease.age() > ttl


def _prune(leases: dict[str, Lease], ttl: float) -> dict[str, Lease]:
    """Drop stale leases in-place and return the cleaned dict."""
    live = {}
    for codespace, lease in leases.items():
        if _is_stale(lease, ttl):
            log.info(
                "Reclaiming stale lease: %s (effort=%s, host=%s, pid=%s)",
                codespace, lease.effort, lease.host, lease.pid,
            )
            continue
        live[codespace] = lease
    return live


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


def borrow(
    effort: str,
    codespace: str,
    force: bool = False,
    ttl: float = DEFAULT_TTL,
) -> Lease:
    """Acquire or refresh an advisory lease on ``codespace`` for ``effort``."""
    if not codespace:
        raise RuntimeError("borrow requires a CodeSpace name")
    with _lease_lock():
        holds = _read_live_records(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
        )
        hold = holds.get(codespace)
        if hold:
            raise ProviderAdmissionError(
                f"CodeSpace '{codespace}' is unavailable while provider "
                f"{hold.operation} is in progress"
            )
        leases = _prune(_read_leases(), ttl)
        held = leases.get(codespace)
        if held and held.effort != effort and not force:
            raise RuntimeError(
                f"CodeSpace '{codespace}' is leased by effort "
                f"'{held.effort}' (host={held.host}, pid={held.pid}). "
                f"Use --force to take it over."
            )
        now = time.time()
        # Preserve acquired_at only when the same effort re-borrows; a forced
        # takeover by a new effort starts a fresh acquisition.
        keep_acquired = (
            held.acquired_at
            if held and held.effort == effort
            else now
        )
        lease = Lease(
            codespace=codespace,
            effort=effort,
            pid=os.getpid(),
            host=_this_host(),
            acquired_at=keep_acquired,
            heartbeat_at=now,
        )
        leases[codespace] = lease
        _write_leases(leases)
        if held and held.effort != effort:
            log.info(
                "Force-took CodeSpace '%s' from effort '%s' for effort '%s'",
                codespace, held.effort, effort,
            )
        else:
            log.info("Leased CodeSpace '%s' to effort '%s'", codespace, effort)
        return lease


def release(target: str, ttl: float = DEFAULT_TTL) -> bool:
    """Release a lease by CodeSpace name or effort name."""
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        to_remove = [
            c for c, lease in leases.items()
            if c == target or lease.effort == target
        ]
        if not to_remove:
            return False
        for c in to_remove:
            del leases[c]
            log.info("Released lease on '%s'", c)
        _write_leases(leases)
        return True


def heartbeat(codespace: str, ttl: float = DEFAULT_TTL) -> bool:
    """Refresh the local heartbeat and best-effort renew any L2 token."""
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        lease = leases.get(codespace)
        if not lease:
            return False
        token = lease.lease_token
    new_token = ""
    if token:
        res = coordination.renew(codespace, token)
        if res.ok:
            new_token = res.token
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        lease = leases.get(codespace)
        if not lease:
            return False
        lease.heartbeat_at = time.time()
        if new_token:
            lease.lease_token = new_token
        _write_leases(leases)
        return True


def get_lease(codespace: str, ttl: float = DEFAULT_TTL) -> Lease | None:
    """Return the lease for a CodeSpace, or None if free."""
    for lease in list_leases(ttl=ttl):
        if lease.codespace == codespace:
            return lease
    return None


def get_deploy_hold(codespace: str) -> DeployHold | None:
    """Return a live provider lifecycle hold for ``codespace``, if any."""
    with _lease_lock():
        holds = _read_live_records(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
        )
        return holds.get(codespace)


def deploy_hold_status(codespace: str) -> dict:
    """Return observable hold state without weakening strict admission reads."""
    try:
        hold = get_deploy_hold(codespace)
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


def verify_deploy_hold(codespace: str, token: str) -> DeployHold:
    """Prove that the current live hold still belongs to this operation."""
    hold = get_deploy_hold(codespace)
    if hold is None or hold.token != token:
        raise ProviderAdmissionError(
            f"Provider lifecycle hold for '{codespace}' is no longer owned "
            "by this operation"
        )
    return hold


def mark_deploy_hold_uncertain(codespace: str, token: str) -> None:
    """Keep an unconfirmed destructive action fail-closed until hold expiry."""
    with _lease_lock():
        holds = _read_live_records(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
        )
        hold = holds.get(codespace)
        if hold is None or hold.token != token:
            raise ProviderAdmissionError(
                f"Provider lifecycle hold for '{codespace}' cannot record "
                "an unconfirmed action"
            )
        hold.uncertain = True
        hold.heartbeat_at = time.time()
        _write_records(_DEPLOY_HOLDS_FILE, holds)


def clear_stale_provider_records(codespace: str | None = None) -> dict[str, int]:
    """Clear only expired/dead deploy holds; cross-environment records stay closed."""
    cleared = {"deploy_holds": 0}
    with _lease_lock():
        try:
            records = _read_records(
                _DEPLOY_HOLDS_FILE,
                DeployHold,
                fail_closed=True,
            )
        except ProviderAdmissionError:
            try:
                old_enough = (
                    time.time() - _DEPLOY_HOLDS_FILE.stat().st_mtime
                    > DEPLOY_HOLD_TTL
                )
            except OSError:
                old_enough = False
            if not old_enough:
                raise
            _DEPLOY_HOLDS_FILE.unlink(missing_ok=True)
            cleared["deploy_holds"] += 1
            return cleared
        kept = {}
        for key, record in records.items():
            selected = codespace is None or record.codespace == codespace
            if selected and not _record_live(record, DEPLOY_HOLD_TTL):
                cleared["deploy_holds"] += 1
                continue
            kept[key] = record
        if len(kept) != len(records):
            _write_records(_DEPLOY_HOLDS_FILE, kept)
    return cleared


@contextmanager
def deploy_hold(
    codespace: str,
    operation: str,
    *,
    max_lifetime: float = DEPLOY_HOLD_TTL,
) -> Iterator[DeployHold]:
    """Block new provider borrow admission during a destructive check."""
    if max_lifetime <= 0:
        raise DeployHoldError("Provider lifecycle hold lifetime must be positive")
    token = uuid.uuid4().hex
    now = time.time()
    hold = DeployHold(
        codespace=codespace,
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
        existing = holds.get(codespace)
        if existing:
            raise DeployHoldError(
                f"CodeSpace '{codespace}' already has a provider "
                f"{existing.operation} hold"
            )
        holds[codespace] = hold
        _write_records(_DEPLOY_HOLDS_FILE, holds)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_record,
        args=(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            codespace,
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
            codespace,
            token,
            preserve_uncertain=True,
        )


# Exclusive, worktree-keyed claims (#897).


def _creation_flags() -> int:
    return no_window_flags()


def _agent_worktrees_bin() -> str | None:
    return shutil.which("agent-worktrees")  # marketplace-isolation: allow no-context legacy lease lookup; explicit context uses worktrees.run


class ClaimConflict(RuntimeError):
    """A CodeSpace is exclusively claimed by a *different, still-live* worktree."""

    def __init__(self, codespace: str, holder: str, host: str, pid: int) -> None:
        self.codespace = codespace
        self.holder = holder
        self.host = host
        self.pid = pid
        super().__init__(
            f"CodeSpace '{codespace}' is exclusively claimed by worktree "
            f"'{holder}' (host={host}, pid={pid})."
        )


class CoordinationRejected(RuntimeError):
    """The owning worktree cannot create durable coordination state."""


def _claim_owner(lease: Lease) -> str:
    """Return the worktree owner for a claim, else the effort owner."""
    return lease.worktree or lease.effort


def _same_holder_ref(a: str | None, b: str | None) -> bool:
    """True when two ClaimRefs name the same worktree id."""
    if not a or not b:
        return False
    aw = a.split("/")[-1].split("#")[0].strip()
    bw = b.split("/")[-1].split("#")[0].strip()
    return bool(aw) and aw == bw


def resolve_owner_worktree(
    explicit: str | None = None, session_id: str | None = None
) -> str | None:
    """Resolve the explicit owner or the calling worktree dir."""
    from . import worktrees

    worktrees.validate_context()
    if explicit:
        return explicit.strip() or None
    if worktrees.explicit_context():
        args = ["get", "worktree-dir"]
        if session_id:
            args += ["--session-id", session_id]
        r = worktrees.run(*args, timeout=10)
        if r is None or r.returncode != 0:
            return None
        return r.stdout.strip() or None
    aw = _agent_worktrees_bin()
    if not aw:
        return None
    args = [aw, "get", "worktree-dir"]
    if session_id:
        args += ["--session-id", session_id]
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=10,
            creationflags=_creation_flags(),
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def active_worktree_ids() -> set[str] | None:
    """Return active worktree paths, or ``None`` when they cannot be listed."""
    from . import worktrees

    if worktrees.explicit_context():
        r = worktrees.run("list", "--json", timeout=15)
        if r is None:
            return None
    else:
        aw = _agent_worktrees_bin()
        if not aw:
            return None
        try:
            r = subprocess.run(
                [aw, "list", "--json"], capture_output=True, text=True,
                timeout=15, creationflags=_creation_flags(),
            )
        except Exception:
            return None
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except Exception:
        return None
    items = data.get("worktrees", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    ids: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        status = str(it.get("status", "")).lower()
        if status in ("finalized", "completed", "removed", "pruned"):
            continue
        path = it.get("path")
        if path:
            ids.add(str(path))
    return ids


def _worktree_alive(owner: str, active: set[str] | None) -> bool:
    """Treat a holder as dead only when absence is positively confirmed."""
    if not owner:
        return True
    if active is not None and owner in active:
        return True
    is_path = os.path.isabs(owner)
    if is_path:
        try:
            if os.path.exists(owner):
                return True
        except OSError:
            return True
        return False
    return True


def sweep_dead(
    active: set[str] | None = None, ttl: float = DEFAULT_TTL
) -> list[str]:
    """Release claims whose owning worktree is positively gone."""
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        released: list[str] = []
        for cs, lease in list(leases.items()):
            owner = _claim_owner(lease)
            if lease.worktree and not _worktree_alive(owner, active):
                log.info(
                    "Auto-releasing claim on '%s' -- owner worktree '%s' is gone",
                    cs, owner,
                )
                del leases[cs]
                released.append(cs)
        if released:
            _write_leases(leases)
        return released


def claim(
    codespace: str,
    owner: str,
    *,
    force: bool = False,
    ttl: float = DEFAULT_TTL,
    active: set[str] | None = None,
    holder_ref: str | None = None,
    coordinate: bool = True,
    preflight_result: coordination.PreflightResult | None = None,
) -> Lease:
    """Acquire or refresh an exclusive worktree claim on ``codespace``.

    Live different owners raise :class:`ClaimConflict` unless ``force``; gone
    owners are reclaimed. When ``coordinate`` and ``holder_ref`` are present, a
    best-effort cross-machine L2 lease is acquired before the local write.
    """
    from .worktrees import validate_context

    validate_context()
    if not codespace:
        raise RuntimeError("claim requires a CodeSpace name")
    if not owner:
        raise RuntimeError("claim requires an owner worktree")

    lease_token = ""
    if coordinate and holder_ref:
        peek = _read_leases().get(codespace)
        same_owner = bool(peek and _claim_owner(peek) == owner)
        prior_token = peek.lease_token if (same_owner and peek) else ""
        lease_token = prior_token
        if same_owner and prior_token:
            res = coordination.renew(codespace, prior_token)
        else:
            readiness = preflight_result or coordination.preflight(holder_ref)
            if readiness.rejected:
                raise CoordinationRejected(
                    f"{readiness.code}: {readiness.detail}"
                )
            res = coordination.acquire(codespace, holder_ref)
        if res.ok:
            lease_token = res.token
        elif res.rejected:
            raise CoordinationRejected(res.detail)
        elif res.conflict:
            self_conflict = same_owner or _same_holder_ref(res.holder, holder_ref)
            if self_conflict:
                log.info(
                    "Re-entrant claim on '%s' by its own owner '%s'; adopting "
                    "the existing lease (L1-only; token heals on next renew).",
                    codespace, owner,
                )
                lease_token = prior_token
            elif not force:
                held_local = _read_leases().get(codespace)
                if held_local is not None:
                    raise ClaimConflict(
                        codespace, _claim_owner(held_local),
                        held_local.host, held_local.pid,
                    )
                raise ClaimConflict(
                    codespace, res.holder or "(cross-machine)",
                    "(cross-machine)", 0,
                )
            else:
                log.warning(
                    "Forced claim on '%s' over a live cross-machine lease held "
                    "by '%s'; proceeding without the L2 lease.",
                    codespace, res.holder or "?",
                )
                lease_token = ""

    release_after_reject = ""
    hold_operation = ""
    with _lease_lock():
        holds = _read_live_records(
            _DEPLOY_HOLDS_FILE,
            DeployHold,
            DEPLOY_HOLD_TTL,
        )
        hold = holds.get(codespace)
        if hold:
            release_after_reject = lease_token
            hold_operation = hold.operation
        else:
            leases = _prune(_read_leases(), ttl)
            held = leases.get(codespace)
            if held and _claim_owner(held) != owner:
                holder = _claim_owner(held)
                if _worktree_alive(holder, active) and not force:
                    raise ClaimConflict(codespace, holder, held.host, held.pid)
                log.info(
                    "Taking CodeSpace '%s' claim from '%s' for '%s' (%s)",
                    codespace, holder, owner,
                    "forced" if force else "prior owner gone",
                )
            now = time.time()
            keep_acquired = (
                held.acquired_at if held and _claim_owner(held) == owner else now
            )
            lease = Lease(
                codespace=codespace,
                effort="",
                pid=os.getpid(),
                host=_this_host(),
                acquired_at=keep_acquired,
                heartbeat_at=now,
                worktree=owner,
                lease_token=lease_token,
            )
            leases[codespace] = lease
            _write_leases(leases)
            return lease
    if release_after_reject:
        try:
            coordination.release(codespace, release_after_reject)
        except Exception:
            log.warning(
                "Could not release cross-machine token for '%s' "
                "after deploy-hold rejection",
                codespace,
                exc_info=True,
            )
    raise ProviderAdmissionError(
        f"CodeSpace '{codespace}' is unavailable while provider "
        f"{hold_operation} is in progress"
    )


def claim_for_connect(
    codespace: str,
    *,
    force: bool = False,
    effort: str | None = None,
    session_id: str | None = None,
) -> str | None:
    """Resolve the caller's worktree, take the claim, and journal obligation."""
    from . import coordination

    fence_holder_ref = coordination.owner_ref(session_id=session_id)
    if os.environ.get("AGENT_CODESPACES_DISABLE_CLAIM"):
        return fence_holder_ref
    claim_owner = resolve_owner_worktree(explicit=effort, session_id=session_id)
    if not claim_owner:
        if fence_holder_ref:
            readiness = coordination.preflight(fence_holder_ref)
            if readiness.rejected:
                raise CoordinationRejected(
                    f"{readiness.code}: {readiness.detail}"
                )
        return fence_holder_ref
    claim(
        codespace, claim_owner, force=force,
        active=active_worktree_ids(), holder_ref=fence_holder_ref,
    )
    if fence_holder_ref and coordination.journal_obligation(
        codespace, fence_holder_ref,
    ):
        log.info(
            "Journaled CodeSpace %s as an obligation on %s",
            codespace, fence_holder_ref,
        )
    return fence_holder_ref


def lease_token_for(codespace: str, ttl: float = DEFAULT_TTL) -> str | None:
    """Return the live L2 fencing token for ``codespace``, if any."""
    try:
        with _lease_lock():
            held = _prune(_read_leases(), ttl).get(codespace)
        token = held.lease_token if held else ""
        return token or None
    except Exception:
        return None


def release_claim(codespace: str, owner: str, ttl: float = DEFAULT_TTL) -> bool:
    """Release ``codespace``'s claim when ``owner`` still holds it."""
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        held = leases.get(codespace)
        if not held or _claim_owner(held) != owner:
            return False
        token = held.lease_token
        del leases[codespace]
        _write_leases(leases)
        log.info("Released claim on '%s' (owner '%s')", codespace, owner)
    if token:
        coordination.release(codespace, token)
    return True


def release_worktree_claims(owner: str, ttl: float = DEFAULT_TTL) -> list[str]:
    """Release every claim owned by ``owner`` and tombstone any L2 leases."""
    with _lease_lock():
        leases = _prune(_read_leases(), ttl)
        released_tokens = {
            cs: lease.lease_token
            for cs, lease in leases.items()
            if _claim_owner(lease) == owner
        }
        for cs in released_tokens:
            del leases[cs]
        if released_tokens:
            _write_leases(leases)
            log.info(
                "Released %d claim(s) for worktree '%s'", len(released_tokens), owner
            )
    for cs, token in released_tokens.items():
        if token:
            coordination.release(cs, token)
    return list(released_tokens)
