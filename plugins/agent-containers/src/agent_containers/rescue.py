"""Host-owned publication and retention for restricted session evidence."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import STATE_DIR, ContainersConfig, FleetConfig, ensure_state_dir
from .lifecycle import inspect_container
from .private_state import (
    ensure_private_dir,
    fsync_directory,
    write_json_exclusive,
)
from .rescue_protocol import (
    RescueError,
    _inventory_root,
    _inventory_session,
    _remaining,
    _stream_member,
)
from .restricted_exec import RestrictedExecError, resolve_executable

log = logging.getLogger("agent-containers")
RESCUE_SCHEMA_VERSION = 1
RESCUE_ROOT = STATE_DIR / "rescues"
ALLOWLISTED_MEMBERS = (
    "events.jsonl",
    "workspace.yaml",
    "origin.json",
    "context.json",
    "agent-worktrees.json",
    "checkpoints/index.md",
)
HIGH_GROWTH_ROOTS = {"files", "rewind-file-snapshots", "research"}
AGENT_WORKTREES_PROJECTION = "agent-worktrees.json"
AGENT_WORKTREES_SCHEMA_VERSION = 1
MAX_AGENT_WORKTREES_BYTES = 128 * 1024
MAX_AGENT_WORKTREES_JSON_DEPTH = 64
_SESSION_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_MALFORMED_PIN_MAX_AGE = 3600.0


@dataclass
class RescuePin:
    """Retention pin for a verified capture used by one lifecycle operation."""

    container: str
    container_instance: str
    container_generation: str
    capture_id: str
    token: str
    expires_at: float
    path: Path
    metadata: dict


def _safe_component(value: str) -> str:
    if _SAFE_COMPONENT_RE.fullmatch(value):
        return value
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:24]


def _capture_base(container: str) -> Path:
    return RESCUE_ROOT / _safe_component(container)


def container_generation(inspected: dict) -> str:
    """Return Docker's authoritative execution generation for one instance."""
    started_at = (inspected.get("State") or {}).get("StartedAt")
    if not isinstance(started_at, str) or not started_at.strip():
        raise RescueError("container execution generation is unavailable")
    return started_at.strip()


def _fsync_dir(path: Path) -> None:
    fsync_directory(path)


def _write_json_fsynced(path: Path, payload: dict) -> None:
    write_json_exclusive(path, payload, indent=2, sort_keys=True)


@contextmanager
def _exclusive_lock(
    path: Path,
    *,
    timeout: float = 30.0,
    stale_after: float = 3600.0,
) -> Iterator[None]:
    deadline = time.monotonic() + timeout
    fd = None
    owner_token = uuid.uuid4().hex
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, owner_token.encode("ascii"))
            os.fsync(fd)
            break
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > stale_after
            except OSError:
                stale = False
            if stale:
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise RescueError(f"rescue lock is busy: {path.name}") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if path.read_text(encoding="ascii") == owner_token:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _capture_size(path: Path) -> int:
    try:
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        size = int(metadata["total_bytes"])
        if size >= 0:
            return size
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return 0


def _projection_validation_error(path: Path, session_id: str) -> str | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_AGENT_WORKTREES_BYTES + 1)
    except OSError:
        return "unreadable_projection"
    if len(raw) > MAX_AGENT_WORKTREES_BYTES:
        return "oversize"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return "invalid_projection_json"
    if not isinstance(payload, dict):
        return "invalid_projection_schema"
    version = payload.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
    ):
        return "unsupported_projection_schema"
    if payload.get("session_id") != session_id:
        return "projection_session_id_mismatch"
    if _json_depth_exceeds(payload, MAX_AGENT_WORKTREES_JSON_DEPTH):
        return "invalid_projection_schema"
    if version > AGENT_WORKTREES_SCHEMA_VERSION:
        return None
    relations = payload.get("relations")
    tombstones = payload.get("relation_tombstones", [])
    if (
        not isinstance(relations, list)
        or any(not isinstance(item, dict) for item in relations)
        or not isinstance(tombstones, list)
        or any(not isinstance(item, dict) for item in tombstones)
    ):
        return "invalid_projection_schema"
    return None


def _json_depth_exceeds(value: object, maximum: int) -> bool:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > maximum:
            return True
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return False


def _newest_verified(base: Path) -> dict | None:
    if not base.exists():
        return None
    for capture in sorted(base.iterdir(), key=lambda item: item.name, reverse=True):
        if not capture.is_dir() or capture.name.startswith(".staging-"):
            continue
        try:
            metadata = json.loads((capture / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            metadata.get("status") == "verified"
            and metadata.get("capture_id") == capture.name
        ):
            return {
                key: metadata.get(key)
                for key in (
                    "status",
                    "completeness",
                    "capture_id",
                    "captured_at",
                    "container_instance",
                    "container_generation",
                    "session_count",
                    "session_state",
                    "total_bytes",
                    "excluded",
                )
            }
    return None


def _verified_reference(base: Path, capture_id: object) -> dict | None:
    if not isinstance(capture_id, str) or not _SAFE_COMPONENT_RE.fullmatch(capture_id):
        return None
    try:
        metadata = json.loads(
            (base / capture_id / "metadata.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    if (
        metadata.get("status") != "verified"
        or metadata.get("capture_id") != capture_id
    ):
        return None
    return metadata


def _publish_status(base: Path, payload: dict) -> None:
    staging = base / f".status-{uuid.uuid4().hex}.tmp"
    _write_json_fsynced(staging, payload)
    os.replace(staging, base / "status.json")
    _fsync_dir(base)


def _capture_pinned(capture: Path) -> bool:
    pinned = False
    for temp_pin in capture.glob("..pin-*.tmp"):
        try:
            expired = time.time() - temp_pin.stat().st_mtime > _MALFORMED_PIN_MAX_AGE
        except OSError:
            expired = False
        if expired:
            temp_pin.unlink(missing_ok=True)
    for pin_path in capture.glob(".pin-*.json"):
        try:
            payload = json.loads(pin_path.read_text(encoding="utf-8"))
            expires_at = float(payload["expires_at"])
            token = str(payload["token"])
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            try:
                expired = (
                    time.time() - pin_path.stat().st_mtime
                    > _MALFORMED_PIN_MAX_AGE
                )
            except OSError:
                expired = False
            if expired:
                pin_path.unlink(missing_ok=True)
                continue
            return True
        if not token or expires_at > time.time():
            pinned = True
            continue
        pin_path.unlink(missing_ok=True)
    return pinned


@contextmanager
def pin_verified_capture(
    container: str,
    container_instance: str,
    container_generation: str,
    capture_id: str,
    *,
    expires_at: float,
) -> Iterator[RescuePin]:
    """Pin one verified capture against retention for a lifecycle operation."""
    base = _capture_base(container)
    token = uuid.uuid4().hex
    with _exclusive_lock(RESCUE_ROOT / ".retention.lock"):
        metadata = _verified_reference(base, capture_id)
        if (
            metadata is None
            or metadata.get("container_instance") != container_instance
            or metadata.get("container_generation") != container_generation
        ):
            raise RescueError(
                "verified rescue capture is unavailable for lifecycle pinning"
            )
        capture = base / capture_id
        pin_path = capture / f".pin-{token}.json"
        _write_json_fsynced(
            pin_path,
            {
                "token": token,
                "container_instance": container_instance,
                "container_generation": container_generation,
                "expires_at": expires_at,
            },
        )
        _fsync_dir(capture)
    pin = RescuePin(
        container=container,
        container_instance=container_instance,
        container_generation=container_generation,
        capture_id=capture_id,
        token=token,
        expires_at=expires_at,
        path=pin_path,
        metadata=metadata,
    )
    try:
        yield pin
    finally:
        with _exclusive_lock(RESCUE_ROOT / ".retention.lock"):
            try:
                payload = json.loads(pin_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if payload.get("token") == token:
                pin_path.unlink(missing_ok=True)
                _fsync_dir(pin_path.parent)


def verify_pinned_capture(pin: RescuePin) -> None:
    """Verify the pinned archive and ownership immediately before destruction."""
    try:
        payload = json.loads(pin.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RescueError("verified rescue pin is no longer available") from exc
    if (
        payload.get("token") != pin.token
        or float(payload.get("expires_at", 0)) <= time.time()
        or (
            (metadata := _verified_reference(
                pin.path.parent.parent,
                pin.capture_id,
            ))
            is None
        )
        or metadata.get("container_instance") != pin.container_instance
        or metadata.get("container_generation") != pin.container_generation
    ):
        raise RescueError("verified rescue pin failed validation")


def _enforce_retention(config: ContainersConfig, current: Path) -> None:
    captures = []
    for container_dir in RESCUE_ROOT.iterdir():
        if not container_dir.is_dir():
            continue
        children = sorted(
            (
                child
                for child in container_dir.iterdir()
                if child.is_dir() and not child.name.startswith(".staging-")
            ),
            key=lambda item: item.name,
            reverse=True,
        )
        for stale in children[config.rescue.retain_per_container :]:
            if stale != current and not _capture_pinned(stale):
                shutil.rmtree(stale)
        captures.extend(
            child
            for child in children
            if child.exists()
        )
    sized = sorted(
        ((_capture_size(path), path) for path in captures),
        key=lambda item: item[1].name,
    )
    total = sum(size for size, _path in sized)
    for size, path in sized:
        if total <= config.rescue.max_total_bytes:
            break
        if path == current:
            continue
        if _capture_pinned(path):
            continue
        shutil.rmtree(path)
        total -= size
    for container_dir in RESCUE_ROOT.iterdir():
        if container_dir.is_dir():
            _reconcile_status_reference(container_dir)


def _reconcile_status_reference(base: Path) -> None:
    status_path = base / "status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(status, dict):
        return
    newest = _newest_verified(base)
    if status.get("status") == "verified":
        capture_id = status.get("capture_id")
        if _verified_reference(base, capture_id) is not None:
            return
        if newest is None:
            status_path.unlink(missing_ok=True)
        else:
            _publish_status(base, newest)
        return
    if status.get("status") in {"failed", "abandoned"}:
        fallback = status.get("latest_verified")
        capture_id = fallback.get("capture_id") if isinstance(fallback, dict) else None
        if _verified_reference(base, capture_id) is not None:
            return
        status["latest_verified"] = newest
        _publish_status(base, status)


def capture_restricted_sessions(
    config: ContainersConfig,
    fleet: FleetConfig,
    *,
    container: str,
    container_instance: str,
    user: str,
    deadline: float | None = None,
) -> dict:
    """Capture allowlisted evidence with descriptor-bound, atomic publication."""
    if not fleet.restricted:
        raise RescueError("session evidence rescue is restricted-fleet only")
    config.rescue.validate()
    ensure_state_dir()
    ensure_private_dir(RESCUE_ROOT)
    base = _capture_base(container)
    ensure_private_dir(base)
    if deadline is None:
        deadline = time.monotonic() + config.rescue.operation_timeout_seconds
    capture_lock = RESCUE_ROOT / f".capture-{_safe_component(container)}.lock"
    with _exclusive_lock(
        capture_lock,
        timeout=_remaining(deadline, 30.0),
    ):
        inspected = inspect_container(container_instance)
        inspected_id = str(inspected.get("Id") or "")
        if not inspected_id or inspected_id.lower() != container_instance.lower():
            raise RescueError("container identity changed before rescue")
        generation = container_generation(inspected)
        try:
            node_path, home = resolve_executable(
                container_instance,
                user,
                inspected,
                kind="node",
                deadline=deadline,
            )
        except RestrictedExecError as exc:
            raise RescueError(str(exc)) from exc
        return _capture_locked(
            config,
            fleet,
            container=container,
            container_instance=container_instance,
            user=user,
            base=base,
            node_path=node_path,
            home=home,
            container_generation=generation,
            deadline=deadline,
        )


def _capture_locked(
    config: ContainersConfig,
    fleet: FleetConfig,
    *,
    container: str,
    container_instance: str,
    user: str,
    base: Path,
    node_path: str,
    home: str,
    container_generation: str,
    deadline: float,
) -> dict:
    for abandoned_staging in base.glob(".staging-*"):
        if abandoned_staging.is_dir():
            shutil.rmtree(abandoned_staging, ignore_errors=True)
    for abandoned_status in base.glob(".status-*.tmp"):
        abandoned_status.unlink(missing_ok=True)
    capture_id = f"{time.time_ns()}-{_safe_component(container_instance)[:16]}"
    staging = base / f".staging-{uuid.uuid4().hex}"
    final = base / capture_id
    ensure_private_dir(staging)
    total_bytes = 0
    sessions: dict[str, dict] = {}
    excluded = {
        "unknown_session_entries": 0,
        "unknown_members": 0,
        "high_growth_roots": [],
        "allowlisted": [],
        "missing_events": [],
    }
    try:
        root_inventory = _inventory_root(
            container_instance,
            user,
            node_path,
            home,
            deadline=deadline,
        )
        root_entries = root_inventory.entries
        session_ids = []
        for name, kind in root_entries:
            if kind == "d" and _SESSION_RE.fullmatch(name):
                canonical = str(uuid.UUID(name))
                if canonical == name.lower():
                    session_ids.append(canonical)
                    continue
            excluded["unknown_session_entries"] += 1

        sessions_root = staging / "sessions"
        ensure_private_dir(sessions_root)
        for session_id in sorted(set(session_ids)):
            inventory = _inventory_session(
                container_instance,
                user,
                node_path,
                home,
                session_id,
                deadline=deadline,
            )
            known_growth = sorted(
                {
                    path.split("/", 1)[0]
                    for path, _kind in inventory
                    if path.split("/", 1)[0] in HIGH_GROWTH_ROOTS
                }
            )
            excluded["high_growth_roots"] = sorted(
                set(excluded["high_growth_roots"]) | set(known_growth)
            )
            excluded["unknown_members"] += sum(
                1
                for path, _kind in inventory
                if path
                and path not in ALLOWLISTED_MEMBERS
                and path.split("/", 1)[0] not in HIGH_GROWTH_ROOTS
                and path != "checkpoints"
            )
            session_dir = sessions_root / session_id
            ensure_private_dir(session_dir)
            member_metadata = {}
            for relative in ALLOWLISTED_MEMBERS:
                destination = session_dir / relative
                ensure_private_dir(destination.parent)
                member_limit = (
                    min(config.rescue.max_member_bytes, MAX_AGENT_WORKTREES_BYTES)
                    if relative == AGENT_WORKTREES_PROJECTION
                    else config.rescue.max_member_bytes
                )
                result = _stream_member(
                    container_instance,
                    user,
                    node_path,
                    home,
                    session_id,
                    relative,
                    destination,
                    max_bytes=member_limit,
                    deadline=deadline,
                )
                if result.status == "captured":
                    if relative == AGENT_WORKTREES_PROJECTION:
                        projection_error = _projection_validation_error(
                            destination,
                            session_id,
                        )
                        if projection_error is not None:
                            destination.unlink(missing_ok=True)
                            excluded["allowlisted"].append(
                                _excluded_member(
                                    session_id,
                                    relative,
                                    projection_error,
                                    result.size,
                                )
                            )
                            continue
                    if total_bytes + result.size > config.rescue.max_capture_bytes:
                        destination.unlink(missing_ok=True)
                        excluded["allowlisted"].append(
                            _excluded_member(session_id, relative, "capture_limit", result.size)
                        )
                        continue
                    total_bytes += result.size
                    member_metadata[relative] = {
                        "bytes": result.size,
                        "sha256": result.sha256,
                    }
                elif result.status == "excluded":
                    excluded["allowlisted"].append(
                        _excluded_member(
                            session_id,
                            relative,
                            result.reason or "irregular",
                            result.size,
                        )
                    )
                elif relative == "events.jsonl":
                    excluded["missing_events"].append(session_id)
            sessions[session_id] = {"members": member_metadata}

        completeness = (
            "partial"
            if (
                root_inventory.state == "missing"
                or excluded["allowlisted"]
                or excluded["missing_events"]
            )
            else "complete"
        )
        metadata = _metadata(
            config,
            fleet,
            container=container,
            container_instance=container_instance,
            capture_id=capture_id,
            completeness=completeness,
            sessions=sessions,
            excluded=excluded,
            total_bytes=total_bytes,
            session_state=root_inventory.state,
            container_generation=container_generation,
        )
        _publish_capture(
            config,
            base,
            staging,
            final,
            metadata,
            deadline=deadline,
        )
        return metadata
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        _publish_failed_status(
            base,
            container_instance,
            container_generation,
        )
        raise


def _excluded_member(
    session_id: str,
    member: str,
    reason: str,
    size: int,
) -> dict:
    return {
        "session_id": session_id,
        "member": member,
        "reason": reason,
        "bytes": size,
    }


def _metadata(
    config: ContainersConfig,
    fleet: FleetConfig,
    *,
    container: str,
    container_instance: str,
    capture_id: str,
    completeness: str,
    sessions: dict,
    excluded: dict,
    total_bytes: int,
    session_state: str,
    container_generation: str,
) -> dict:
    return {
        "schema_version": RESCUE_SCHEMA_VERSION,
        "status": "verified",
        "completeness": completeness,
        "capture_id": capture_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "container": container,
        "container_instance": container_instance,
        "container_generation": container_generation,
        "fleet": next(
            (name for name, value in config.fleets.items() if value is fleet),
            None,
        ),
        "source_repo": fleet.repo or None,
        "session_count": len(sessions),
        "session_state": session_state,
        "total_bytes": total_bytes,
        "sessions": sessions,
        "excluded": excluded,
        "restorable": False,
    }


def _publish_capture(
    config: ContainersConfig,
    base: Path,
    staging: Path,
    final: Path,
    metadata: dict,
    *,
    deadline: float,
) -> None:
    _write_json_fsynced(staging / "metadata.json", metadata)
    for directory in sorted(
        (path for path in staging.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _fsync_dir(directory)
    _fsync_dir(staging)
    with _exclusive_lock(
        RESCUE_ROOT / ".retention.lock",
        timeout=_remaining(deadline, 30.0),
    ):
        os.replace(staging, final)
        _fsync_dir(base)
        _publish_status(
            base,
            {
                key: metadata.get(key)
                for key in (
                    "status",
                    "completeness",
                    "capture_id",
                    "captured_at",
                    "container_instance",
                    "container_generation",
                    "session_count",
                    "session_state",
                    "total_bytes",
                    "excluded",
                )
            },
        )
        try:
            _remaining(deadline, config.rescue.operation_timeout_seconds)
            _enforce_retention(config, final)
        except (OSError, RescueError) as exc:
            log.warning("Verified rescue published; retention deferred: %s", exc)


def _publish_failed_status(
    base: Path,
    container_instance: str,
    container_generation: str,
) -> None:
    try:
        with _exclusive_lock(RESCUE_ROOT / ".retention.lock"):
            _publish_status(
                base,
                {
                    "status": "failed",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "container_instance": container_instance,
                    "container_generation": container_generation,
                    "reason": "capture_failed",
                    "latest_verified": _newest_verified(base),
                },
            )
    except (OSError, RescueError):
        pass


def record_telemetry_loss(
    *,
    container: str,
    container_instance: str,
    container_generation: str,
    reason: str,
) -> dict:
    """Record explicit acceptance that tmpfs evidence is unavailable."""
    ensure_state_dir()
    ensure_private_dir(RESCUE_ROOT)
    base = _capture_base(container)
    ensure_private_dir(base)
    capture_lock = RESCUE_ROOT / f".capture-{_safe_component(container)}.lock"
    with _exclusive_lock(capture_lock):
        with _exclusive_lock(RESCUE_ROOT / ".retention.lock"):
            payload = {
                "status": "abandoned",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "container_instance": container_instance,
                "container_generation": container_generation,
                "reason": reason,
                "latest_verified": _newest_verified(base),
            }
            _publish_status(base, payload)
            return payload


def latest_rescue_status(container: str) -> dict | None:
    """Return path-free latest-attempt status plus verified fallback metadata."""
    base = _capture_base(container)
    if not base.exists():
        return None
    try:
        status = json.loads((base / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        status = None
    newest = _newest_verified(base)
    if isinstance(status, dict) and status.get("status") == "verified":
        capture_id = status.get("capture_id")
        if _verified_reference(base, capture_id) is not None:
            return status
        return newest
    if isinstance(status, dict) and status.get("status") in {"failed", "abandoned"}:
        fallback = status.get("latest_verified")
        capture_id = fallback.get("capture_id") if isinstance(fallback, dict) else None
        if _verified_reference(base, capture_id) is None:
            status["latest_verified"] = newest
        return status
    return newest


def verified_capture_for_instance(
    container: str,
    container_instance: str,
    container_generation: str,
) -> dict | None:
    """Return a verified capture for one exact Docker ID and run generation."""
    base = _capture_base(container)
    if not base.exists():
        return None
    for capture in sorted(base.iterdir(), key=lambda item: item.name, reverse=True):
        if not capture.is_dir() or capture.name.startswith(".staging-"):
            continue
        try:
            metadata = json.loads((capture / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            metadata.get("status") == "verified"
            and metadata.get("container_instance") == container_instance
            and metadata.get("container_generation") == container_generation
        ):
            return metadata
    return None
