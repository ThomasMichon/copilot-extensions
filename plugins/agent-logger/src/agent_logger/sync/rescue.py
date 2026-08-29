"""Validate and publish provider-owned rescued session evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_logger.config import Config
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.origin import classify_source_repo
from agent_logger.sync.rescue_projection import push_venue
from agent_logger.sync.rescue_validation import (
    SUPPORTED_PROVIDER,
    RescuedSession,
    RescueSourceError,
    parse_capture,
    read_regular,
    require_directory,
    venue_id,
)
from agent_logger.sync.targets import build_target

CHECKPOINT_SCHEMA_VERSION = 2
_MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
_MAX_CHECKPOINT_RECORDS = 100_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class RescuePushSummary:
    """Observable outcome of one rescue-source push."""

    captures_seen: int = 0
    accepted: int = 0
    skipped: int = 0
    rejected_captures: int = 0
    rejected_sessions: int = 0
    venues_pushed: int = 0
    target_failures: int = 0
    details: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> int:
        return self.rejected_captures + self.rejected_sessions


def _repository_allowed(
    session: RescuedSession,
    allowlist: list[str],
    denylist: list[str],
    *,
    fail_closed: bool,
) -> bool:
    if allowlist and session.source_repo is None:
        return False
    return classify_source_repo(
        session.source_repo,
        has_recorded_paths=session.source_repo is not None,
        allowlist=allowlist,
        denylist=denylist,
        fail_closed=fail_closed,
    )


def _checkpoint_path(cfg: Config) -> Path:
    return cfg.home / "rescue-sync" / "checkpoint.json"


def _validate_home(home: Path) -> None:
    """Validate only the mutable rescue-sync subtree, not dotfiles ancestors."""
    state_root = home.expanduser().absolute() / "rescue-sync"
    existing = state_root
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    require_directory(existing, "agent-logger home ancestor")
    if existing.is_symlink():
        raise RescueSourceError(f"agent-logger home must not traverse a symlink: {home}")
    if state_root.exists():
        require_directory(state_root, "rescue sync state root")
        if (state_root / ".git").exists():
            raise RescueSourceError(
                f"rescue sync state root must not be a repository: {state_root}"
            )


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise RescueSourceError(f"rescue checkpoint must not be a symlink: {path}")
    if not path.exists():
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "captures": {},
            "sessions": {},
        }
    try:
        payload = json.loads(read_regular(path, max_bytes=_MAX_CHECKPOINT_BYTES))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RescueSourceError(f"invalid rescue checkpoint: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2}:
        raise RescueSourceError(f"unsupported rescue checkpoint: {path}")
    records = payload.get("sessions")
    if not isinstance(records, dict):
        raise RescueSourceError(f"invalid rescue checkpoint sessions: {path}")
    captures = payload.get("captures", {})
    if not isinstance(captures, dict):
        raise RescueSourceError(f"invalid rescue checkpoint captures: {path}")
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "captures": captures,
        "sessions": records,
    }


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    require_directory(path.parent, "rescue checkpoint directory")
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    compacted = _compact_checkpoint(payload)
    encoded = (json.dumps(compacted, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > _MAX_CHECKPOINT_BYTES:
        raise RescueSourceError(
            f"rescue checkpoint would exceed {_MAX_CHECKPOINT_BYTES} bytes"
        )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)
        os.replace(temporary, path)
    except OSError as exc:
        raise RescueSourceError(f"cannot write rescue checkpoint {path}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _compact_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only fixed-size high-water and capture-identity records."""
    captures = payload.get("captures", {})
    sessions = payload.get("sessions", {})
    if not isinstance(captures, dict) or not isinstance(sessions, dict):
        raise RescueSourceError("rescue checkpoint records must be mappings")
    if len(captures) + len(sessions) > _MAX_CHECKPOINT_RECORDS:
        raise RescueSourceError(
            f"rescue checkpoint exceeds {_MAX_CHECKPOINT_RECORDS} records"
        )
    compact_captures = {}
    for key, value in captures.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        compact_captures[key] = {
            "capture_id": value.get("capture_id"),
            "fingerprint": value.get("fingerprint"),
        }
    compact_sessions = {}
    for key, value in sessions.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        compact_sessions[key] = {
            field: value.get(field)
            for field in (
                "capture_id",
                "capture_key",
                "capture_fingerprint",
                "capture_order",
                "member_fingerprint",
                "source_repo",
                "target_fingerprint",
            )
        }
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "captures": compact_captures,
        "sessions": compact_sessions,
    }


def _target_fingerprint(cfg: Config) -> str:
    payload = {
        "target": cfg.sync_target,
        "options": cfg.target_options(cfg.sync_target),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _entries(path: Path, label: str) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except OSError as exc:
        raise RescueSourceError(f"cannot enumerate {label} {path}: {exc}") from exc


def _checkpoint_decision(
    session: RescuedSession,
    checkpoint: dict[str, Any],
    target_fingerprint: str,
) -> str:
    saved_capture_record = checkpoint["captures"].get(session.capture_key)
    if saved_capture_record is not None and (
        not isinstance(saved_capture_record, dict)
        or saved_capture_record.get("capture_id") != session.capture_id
        or saved_capture_record.get("fingerprint")
        != session.capture_fingerprint
    ):
        return "mutated-capture"
    record = checkpoint["sessions"].get(session.checkpoint_key)
    if not isinstance(record, dict):
        return "push"
    saved_capture = record.get("capture_id")
    saved_capture_fingerprint = record.get("capture_fingerprint")
    saved_order = record.get("capture_order")
    saved_fingerprint = record.get("member_fingerprint")
    saved_source_repo = record.get("source_repo")
    saved_target = record.get("target_fingerprint")
    if (
        not isinstance(saved_capture, str)
        or not isinstance(saved_order, list)
        or len(saved_order) != 2
        or isinstance(saved_order[0], bool)
        or not isinstance(saved_order[0], (int, float))
        or not isinstance(saved_order[1], str)
        or saved_capture != saved_order[1]
        or not isinstance(saved_fingerprint, str)
        or not _SHA256_RE.fullmatch(saved_fingerprint)
        or not isinstance(saved_target, str)
        or not _SHA256_RE.fullmatch(saved_target)
    ):
        raise RescueSourceError(
            f"invalid checkpoint record for {session.checkpoint_key}"
        )
    current_order = [session.capture_order[0], session.capture_order[1]]
    if current_order < saved_order:
        return "older"
    if saved_capture_fingerprint is None:
        return "push"
    if (
        not isinstance(saved_capture_fingerprint, str)
        or not _SHA256_RE.fullmatch(saved_capture_fingerprint)
    ):
        raise RescueSourceError(
            f"invalid capture fingerprint for {session.checkpoint_key}"
        )
    if (
        saved_capture == session.capture_id
        and saved_capture_fingerprint != session.capture_fingerprint
    ):
        return "mutated-capture"
    if "source_repo" not in record:
        return "push"
    if saved_source_repo != session.source_repo:
        return "reassigned"
    if current_order == saved_order:
        if saved_capture_record is None:
            return "push"
        if saved_fingerprint != session.member_fingerprint:
            return "mutated"
        if saved_target == target_fingerprint:
            return "verify"
    return "push"


def _checkpoint_record(
    session: RescuedSession,
    target_fingerprint: str,
) -> dict[str, Any]:
    return {
        "capture_id": session.capture_id,
        "capture_key": session.capture_key,
        "capture_fingerprint": session.capture_fingerprint,
        "capture_order": [session.capture_order[0], session.capture_order[1]],
        "member_fingerprint": session.member_fingerprint,
        "source_repo": session.source_repo,
        "target_fingerprint": target_fingerprint,
    }


def _capture_checkpoint_record(session: RescuedSession) -> dict[str, str]:
    return {
        "capture_id": session.capture_id,
        "fingerprint": session.capture_fingerprint,
    }


def _normalize_checkpoint(checkpoint: dict[str, Any]) -> None:
    """Drop malformed/obsolete session records while preserving tombstones."""
    # Capture fingerprints are durable tombstones: source retention must not
    # permit provider+venue+capture_id reuse with a different manifest.
    checkpoint["captures"] = {
        key: value
        for key, value in checkpoint["captures"].items()
        if isinstance(key, str) and isinstance(value, dict)
    }
    checkpoint["sessions"] = {
        key: value
        for key, value in checkpoint["sessions"].items()
        if (
            isinstance(value, dict)
            and isinstance(value.get("capture_key"), str)
        )
    }


def _discover(
    rescue_roots: list[Path],
    *,
    target_prefix: str,
    summary: RescuePushSummary,
) -> dict[tuple[str, str], RescuedSession]:
    newest: dict[tuple[str, str], RescuedSession] = {}
    capture_fingerprints: dict[str, str] = {}
    invalid_captures: set[str] = set()
    for raw_root in rescue_roots:
        root = raw_root.expanduser()
        require_directory(root, "rescue root")
        for container_dir in _entries(root, "rescue root"):
            try:
                mode = container_dir.lstat().st_mode
            except OSError as exc:
                summary.rejected_captures += 1
                summary.details.append(f"cannot inspect {container_dir}: {exc}")
                continue
            if container_dir.name.startswith(".") or stat.S_ISREG(mode):
                continue
            try:
                require_directory(container_dir, "container rescue root")
                venue_id(target_prefix, container_dir.name)
            except RescueSourceError as exc:
                summary.rejected_captures += 1
                summary.details.append(str(exc))
                continue
            try:
                capture_entries = _entries(container_dir, "container rescue root")
            except RescueSourceError as exc:
                summary.rejected_captures += 1
                summary.details.append(str(exc))
                continue
            for capture_dir in capture_entries:
                try:
                    mode = capture_dir.lstat().st_mode
                except OSError as exc:
                    summary.rejected_captures += 1
                    summary.details.append(f"cannot inspect {capture_dir}: {exc}")
                    continue
                if capture_dir.name.startswith(".") or stat.S_ISREG(mode):
                    continue
                summary.captures_seen += 1
                try:
                    result = parse_capture(
                        container_dir, capture_dir, target_prefix=target_prefix
                    )
                except RescueSourceError as exc:
                    summary.rejected_captures += 1
                    summary.details.append(f"{capture_dir}: {exc}")
                    continue
                previous_fingerprint = capture_fingerprints.get(result.capture_key)
                if (
                    previous_fingerprint is not None
                    and previous_fingerprint != result.capture_fingerprint
                ):
                    invalid_captures.add(result.capture_key)
                    summary.rejected_captures += 1
                    summary.details.append(
                        f"{capture_dir}: capture metadata/manifest mutated for "
                        f"{result.capture_key}"
                    )
                    continue
                capture_fingerprints[result.capture_key] = result.capture_fingerprint
                summary.rejected_sessions += len(result.rejected_sessions)
                summary.details.extend(
                    f"{capture_dir}: {reason}"
                    for reason in result.rejected_sessions
                )
                summary.details.extend(
                    f"{capture_dir}: {warning}" for warning in result.warnings
                )
                for candidate in result.sessions:
                    key = (candidate.venue_id, candidate.session_id)
                    previous = newest.get(key)
                    if previous is None or candidate.capture_order > previous.capture_order:
                        if previous is not None:
                            summary.skipped += 1
                        newest[key] = candidate
                    else:
                        summary.skipped += 1
    if invalid_captures:
        newest = {
            key: candidate
            for key, candidate in newest.items()
            if candidate.capture_key not in invalid_captures
        }
    return newest


def _select_pending(
    newest: dict[tuple[str, str], RescuedSession],
    cfg: Config,
    checkpoint: dict[str, Any],
    target_fingerprint: str,
    summary: RescuePushSummary,
) -> dict[str, list[RescuedSession]]:
    pending: dict[str, list[RescuedSession]] = {}
    rejected_capture_keys: set[str] = set()
    for candidate in newest.values():
        if not _repository_allowed(
            candidate,
            cfg.sync_repo_allowlist,
            cfg.sync_repo_denylist,
            fail_closed=cfg.sync_repo_allowlist_fail_closed,
        ):
            summary.rejected_sessions += 1
            summary.details.append(
                f"{candidate.venue_id}/{candidate.session_id}: repository not allowed"
            )
            continue
        decision = _checkpoint_decision(candidate, checkpoint, target_fingerprint)
        if decision == "older":
            summary.skipped += 1
            summary.details.append(
                f"{candidate.venue_id}/{candidate.session_id}: {decision} "
                f"capture {candidate.capture_id}"
            )
            continue
        if decision == "verify":
            summary.details.append(
                f"{candidate.venue_id}/{candidate.session_id}: revalidating "
                f"capture {candidate.capture_id}"
            )
        if decision in {"mutated", "mutated-capture", "reassigned"}:
            if candidate.capture_key not in rejected_capture_keys:
                rejected_capture_keys.add(candidate.capture_key)
                summary.rejected_captures += 1
                summary.details.append(
                    f"{candidate.venue_id}/{candidate.capture_id}: "
                    f"{'provider assignment changed' if decision == 'reassigned' else 'capture mutated'}"
                )
            continue
        pending.setdefault(candidate.venue_id, []).append(candidate)
    summary.accepted = sum(len(items) for items in pending.values())
    return pending


def _print_verbose(
    summary: RescuePushSummary,
    pending: dict[str, list[RescuedSession]],
) -> None:
    for detail in summary.details:
        print(f"session-sync rescue-push: {detail}")
    for venue, selected in sorted(pending.items()):
        for session in sorted(selected, key=lambda item: item.session_id):
            print(
                "session-sync rescue-push: "
                f"{venue}/{session.session_id} <- {session.capture_id}"
            )


def push_rescues(
    cfg: Config,
    *,
    rescue_roots: list[Path],
    provider: str = SUPPORTED_PROVIDER,
    target_prefix: str = "container",
    dry_run: bool = False,
    verbose: bool = False,
) -> RescuePushSummary:
    """Validate provider captures and publish the newest session lineage."""
    if provider != SUPPORTED_PROVIDER:
        raise RescueSourceError(f"unsupported rescue provider: {provider}")
    venue_id(target_prefix, "probe")
    _validate_home(cfg.home)
    summary = RescuePushSummary()
    newest = _discover(rescue_roots, target_prefix=target_prefix, summary=summary)
    checkpoint_path = _checkpoint_path(cfg)
    target_fingerprint = _target_fingerprint(cfg)
    if dry_run:
        checkpoint = _load_checkpoint(checkpoint_path)
        _normalize_checkpoint(checkpoint)
        pending = _select_pending(
            newest,
            cfg,
            checkpoint,
            target_fingerprint,
            summary,
        )
        if verbose:
            _print_verbose(summary, pending)
        return summary

    lock_file = cfg.home / "rescue-sync.lock"
    if lock_file.is_symlink():
        raise RescueSourceError(f"rescue sync lock must not be a symlink: {lock_file}")
    with sync_lock(lock_file, timeout=cfg.sync_lock_timeout) as acquired:
        if not acquired:
            raise RescueSourceError("another rescue sync holds the lock")
        checkpoint = _load_checkpoint(checkpoint_path)
        checkpoint_before_prune = json.dumps(
            checkpoint, sort_keys=True, separators=(",", ":")
        )
        _normalize_checkpoint(checkpoint)
        checkpoint_pruned = checkpoint_before_prune != json.dumps(
            checkpoint, sort_keys=True, separators=(",", ":")
        )
        pending = _select_pending(
            newest, cfg, checkpoint, target_fingerprint, summary
        )
        if verbose:
            _print_verbose(summary, pending)
        if not pending:
            if checkpoint_pruned:
                _write_checkpoint(checkpoint_path, checkpoint)
            return summary
        target = build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))
        if not target.rescue_compare_and_set:
            raise RescueSourceError(
                f"sync target {target.name!r} does not support rescue "
                "compare-and-set publication"
            )
        for venue, selected in sorted(pending.items()):
            try:
                result = push_venue(target, cfg, venue, selected)
            except (OSError, RescueSourceError) as exc:
                result = None
                detail = str(exc)
            else:
                detail = result.detail
            if result is None or not result.ok:
                summary.rejected_sessions += len(selected)
                summary.accepted -= len(selected)
                summary.target_failures += 1
                summary.details.append(
                    f"target push failed for {venue}: {detail}"
                )
                print(
                    f"session-sync rescue-push: target push failed for {venue}: "
                    f"{detail}",
                    file=sys.stderr,
                )
                continue
            summary.venues_pushed += 1
            for session in selected:
                checkpoint["captures"][session.capture_key] = (
                    _capture_checkpoint_record(session)
                )
                checkpoint["sessions"][session.checkpoint_key] = _checkpoint_record(
                    session, target_fingerprint
                )
            _write_checkpoint(checkpoint_path, checkpoint)
    return summary


def run_rescue_push(
    cfg: Config,
    *,
    rescue_roots: list[str],
    provider: str,
    target_prefix: str,
    dry_run: bool,
    verbose: bool,
) -> int:
    """CLI wrapper for :func:`push_rescues`."""
    if os.environ.get("AGENT_LOGGER_SYNC_DISABLED") == "1":
        print("session-sync: disabled via AGENT_LOGGER_SYNC_DISABLED")
        return 0
    try:
        summary = push_rescues(
            cfg,
            rescue_roots=[Path(root) for root in rescue_roots],
            provider=provider,
            target_prefix=target_prefix,
            dry_run=dry_run,
            verbose=verbose,
        )
    except (OSError, RescueSourceError) as exc:
        print(f"session-sync rescue-push: {exc}", file=sys.stderr)
        return 1
    action = "would-accept" if dry_run else "accepted"
    print(
        f"session-sync rescue-push: {action}={summary.accepted} "
        f"skipped={summary.skipped} rejected={summary.rejected} "
        f"captures={summary.captures_seen} venues={summary.venues_pushed} "
        f"target_failures={summary.target_failures}"
    )
    if summary.target_failures:
        return 1
    if summary.accepted == 0 and summary.rejected > 0:
        return 1
    return 0
