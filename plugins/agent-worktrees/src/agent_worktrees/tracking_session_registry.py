from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import tracking


def _tracking():
    from . import tracking as tracking_mod

    return tracking_mod


def _ensure_activation_history(entry: tracking.SessionEntry) -> None:
    tracking = _tracking()
    if entry.activations or not entry.started_at:
        return
    entry.activations.append(
        tracking.SessionActivation(
            ordinal=1,
            started_at=entry.started_at,
            start_recorded_at=entry.started_at,
            start_source="legacy",
            ended_at=entry.ended_at,
            end_recorded_at=entry.ended_at,
            end_source="legacy" if entry.ended_at else None,
        )
    )


def _start_session_activation(
    entry: tracking.SessionEntry,
    *,
    event_at: str,
    recorded_at: str,
    source: str,
) -> bool:
    tracking = _tracking()
    _ensure_activation_history(entry)
    latest = max(entry.activations, key=lambda item: item.ordinal, default=None)
    if latest is not None and latest.ended_at is None:
        if latest.started_at == event_at or source in ("bind", "handoff", "reconciled"):
            entry.ended_at = None
            return False
        inferred_end = event_at
        try:
            if datetime.fromisoformat(event_at) < datetime.fromisoformat(latest.started_at):
                inferred_end = recorded_at
        except (TypeError, ValueError):
            pass
        latest.ended_at = inferred_end
        latest.end_recorded_at = recorded_at
        latest.end_source = "inferred:next-start"
    ordinal = (latest.ordinal if latest is not None else 0) + 1
    entry.activations.append(
        tracking.SessionActivation(
            ordinal=ordinal,
            started_at=event_at,
            start_recorded_at=recorded_at,
            start_source=source,
        )
    )
    if not entry.started_at:
        entry.started_at = event_at
    entry.ended_at = None
    return True


def _end_session_activation(
    entry: tracking.SessionEntry,
    *,
    event_at: str,
    recorded_at: str,
    source: str,
) -> bool:
    _ensure_activation_history(entry)
    latest = max(entry.activations, key=lambda item: item.ordinal, default=None)
    if latest is None:
        entry.ended_at = event_at
        return True
    if latest.ended_at is not None:
        return False
    latest.ended_at = event_at
    latest.end_recorded_at = recorded_at
    latest.end_source = source
    entry.ended_at = event_at
    return True


def seal_worktree_identity(record: tracking.WorktreeRecord | None) -> dict:
    tracking = _tracking()
    from . import sessions as _sessions

    result = {"sessions": 0, "titled": False}
    if record is None or not record.worktree_path:
        return result
    if not record.sessions:
        try:
            ids = _sessions.backfill_sessions([record]).get(record.worktree_id, [])
        except Exception:
            ids = []
        if ids:
            record.sessions = [tracking.SessionEntry(session_id=session_id, started_at="") for session_id in ids]
            result["sessions"] = len(ids)
    if not (record.title and record.title != "null"):
        summary = ""
        try:
            ctx = _sessions.scan_sessions_fast([record])
            summary = ctx.latest_summary.get(
                _sessions._normalize_path(record.worktree_path),
                "",
            )
        except Exception:
            summary = ""
        if summary and summary != "null":
            record.title = summary
            result["titled"] = True
    if result["sessions"] or result["titled"]:
        try:
            tracking.save_record(record)
        except Exception:
            pass
    return result


def register_session(
    worktree_id: str,
    session_id: str,
    pid: int | None = None,
    pane_id: str | None = None,
    *,
    started_at: str | None = None,
    source: str = "hook",
    recorded_at: str | None = None,
    handoff_token: str | None = None,
    candidate_token: str | None = None,
    initial_projection: bool = False,
) -> tracking.SessionHandoff | None:
    """Register (or reactivate) a Copilot session against a worktree.

    Dispatches the whole transaction through the ``session_register`` verb
    (daemon write path when reachable, else the identical logged in-process
    fallback -- ``agent-worktrees-authoritative-daemon`` effort, Phase 3;
    see ``tracking_session_registration_write.py``'s own module docstring
    for the full design rationale). Called from the sessionStart hook, so
    ``boot_wait_s=0``: never spin-wait for a cold daemon boot (that would
    add latency to every session launch) -- an already-warm daemon is
    dialed immediately; a cold/unreachable one falls straight through to
    the same code with no added wait, matching this function's
    pre-migration latency exactly. Re-raises ``tracking.SessionLifecycleError``
    for a lifecycle rejection, matching the pre-migration contract of
    letting it propagate synchronously to every existing caller's own
    broad exception handling; ``tracking_write.AmbiguousWriteOutcome`` is
    left to propagate the same way.
    """
    tracking = _tracking()
    yaml_path = tracking._owning_tracking_dir(worktree_id) / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return None
    from . import locks as _locks
    from . import status_monitor_runtime as _smr
    from . import tracking_write

    result = tracking_write.dispatch(
        "session_register",
        {
            "worktree_id": worktree_id,
            "yaml_path": str(yaml_path),
            "session_id": session_id,
            "pid": pid,
            "pane_id": pane_id,
            "started_at": started_at,
            "source": source,
            "recorded_at": recorded_at,
            "handoff_token": handoff_token,
            "candidate_token": candidate_token,
            "initial_projection": initial_projection,
        },
        read_lock_data=lambda: _locks.read_lock(_smr._monitor_lock_path()),
        ensure_monitor=(
            _smr._ensure_status_monitor if _smr._status_monitor_enabled() else None
        ),
        boot_wait_s=0.0,
    )
    if result.get("error") == "lifecycle":
        raise tracking.SessionLifecycleError(result["message"])
    linked = result.get("linked_handoff")
    return tracking.SessionHandoff(**linked) if linked else None


def _record_has_open_session(record: tracking.WorktreeRecord) -> bool:
    return any(entry.ended_at is None for entry in record.sessions or [])


def stop_fsmonitor_daemon(worktree_path: str) -> None:
    from . import git_ops

    if not worktree_path or not os.path.isdir(worktree_path):
        return
    try:
        git_ops.git(
            "fsmonitor--daemon",
            "stop",
            cwd=worktree_path,
            check=False,
            capture=True,
            timeout=5,
        )
    except Exception:
        pass


_REPO_FRESHNESS_FILENAME = "repo-freshness.json"
REPO_FRESHNESS_MAX_AGE_S = 120.0


def _repo_freshness_path() -> Path:
    from . import registry_paths

    return registry_paths.registry_path(_REPO_FRESHNESS_FILENAME)


def _load_repo_freshness(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def record_repo_fetch_confirmed(repo: str, *, at: str | None = None) -> None:
    tracking = _tracking()
    if not repo:
        return
    path = tracking._repo_freshness_path()
    try:
        with tracking._RecordLock(path, blocking=False) as lock:
            if not lock.acquired:
                return
            data = _load_repo_freshness(path)
            data[repo] = {"confirmed_at": at or tracking._now_iso()}
            tracking._atomic_write(path, json.dumps(data))
    except Exception:
        pass


def repo_fetch_confirmed_at(repo: str) -> str | None:
    tracking = _tracking()
    if not repo:
        return None
    try:
        entry = _load_repo_freshness(tracking._repo_freshness_path()).get(repo)
    except Exception:
        return None
    if not isinstance(entry, dict):
        return None
    value = entry.get("confirmed_at")
    return value if isinstance(value, str) else None


def is_repo_fetch_fresh(
    repo: str,
    *,
    max_age_seconds: float = REPO_FRESHNESS_MAX_AGE_S,
    now: str | None = None,
) -> bool:
    confirmed_at = repo_fetch_confirmed_at(repo)
    if not confirmed_at:
        return False
    try:
        confirmed_dt = datetime.strptime(confirmed_at, "%Y-%m-%dT%H:%M:%S")
        now_dt = datetime.strptime(now, "%Y-%m-%dT%H:%M:%S") if now else datetime.now()
    except ValueError:
        return False
    return (now_dt - confirmed_dt).total_seconds() <= max_age_seconds


def deregister_session(
    worktree_id: str,
    session_id: str,
    *,
    ended_at: str | None = None,
    source: str = "hook",
    recorded_at: str | None = None,
) -> None:
    tracking = _tracking()
    yaml_path = tracking._owning_tracking_dir(worktree_id) / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return
    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        if record.sessions is None:
            return
        for entry in record.sessions:
            if entry.session_id != session_id:
                continue
            changed = _end_session_activation(
                entry,
                event_at=ended_at or tracking._now_iso(),
                recorded_at=recorded_at or tracking._now_iso(),
                source=source,
            )
            if changed:
                tracking._next_lifecycle_revision(record, session_id)
                self_session_ref = tracking.format_claim_ref(
                    record.machine,
                    record.repo,
                    record.worktree_id,
                    session=session_id,
                )
                tracking.release_resource_claim(record, self_session_ref, save=False)
                tracking.save_record(record)
                if not _record_has_open_session(record):
                    stop_fsmonitor_daemon(record.worktree_path)
            return
