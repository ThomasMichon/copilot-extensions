from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from . import obligations
from . import tracking


def _ensure_activation_history(entry: tracking.SessionEntry) -> None:
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
    yaml_path = tracking._owning_tracking_dir(worktree_id) / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return None
    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        if (
            record.kind in tracking.MANAGED_KINDS
            and record.status in {"complete", "completed", "finalized"}
        ):
            raise tracking.SessionLifecycleError(
                f"worktree {worktree_id} is terminal and managed; refusing new session activation"
            )
        if record.sessions is None:
            record.sessions = []
        event_at = started_at or tracking._now_iso()
        observed_at = recorded_at or tracking._now_iso()
        tracking._ensure_head_ledger(record)
        self_session_ref = tracking.format_claim_ref(
            record.machine, record.repo, record.worktree_id, session=session_id
        )
        tracking.add_resource_claim(
            record,
            tracking.ResourceClaim(
                kind="session",
                ref=self_session_ref,
                created_at=event_at,
                state=obligations.ACTIVE,
                note="live Copilot session",
            ),
            save=False,
        )

        def _link_if_fresh() -> tracking.SessionHandoff | None:
            prior = next((handoff for handoff in record.handoffs if handoff.token == handoff_token), None)
            already_linked = (
                prior is not None
                and prior.state == "linked"
                and prior.successor == session_id
            )
            linked = tracking.link_handoff(
                record,
                handoff_token,
                session_id,
                linked_at=event_at,
                save=False,
            )
            return None if already_linked else linked

        for entry in record.sessions:
            if entry.session_id != session_id:
                continue
            activation_added = _start_session_activation(
                entry,
                event_at=event_at,
                recorded_at=observed_at,
                source=source,
            )
            if pid:
                entry.pid = pid
            if pane_id:
                entry.pane_id = pane_id
            if handoff_token and tracking._handoff_state(record, handoff_token) == "cancelled":
                handoff_token = None
            if handoff_token:
                try:
                    linked_handoff = _link_if_fresh()
                except tracking.SessionLifecycleError:
                    if activation_added:
                        tracking._next_lifecycle_revision(record, session_id)
                    tracking.save_record(record)
                    raise
                tracking.save_record(record)
                return linked_handoff
            if (
                not candidate_token
                and record.resolved_head_session is None
                and entry.state == "active"
                and (
                    source == "bind"
                    or tracking._pending_handoffs_all_from_yielded(record)
                )
            ):
                tracking._cancel_pending_handoffs(record)
                tracking._append_head_transition(
                    record,
                    session_id,
                    reason="rebind",
                    at=event_at,
                )
            elif activation_added:
                tracking._next_lifecycle_revision(record, session_id)
            tracking.save_record(record)
            return None

        had_active_head = record.resolved_head_session is not None
        new_entry = tracking.SessionEntry(
            session_id=session_id,
            started_at=event_at,
            pid=pid,
            pane_id=pane_id,
            activations=[
                tracking.SessionActivation(
                    ordinal=1,
                    started_at=event_at,
                    start_recorded_at=observed_at,
                    start_source=source,
                )
            ],
        )
        record.sessions.append(new_entry)
        tracking._next_lifecycle_revision(record, session_id)
        if initial_projection and record.controller_for_session(session_id) is None:
            initial_sessions = set(
                getattr(record, "_session_projection_initial_registration", set())
            )
            initial_sessions.add(session_id)
            record._session_projection_initial_registration = initial_sessions
        linked_handoff = None
        if handoff_token and tracking._handoff_state(record, handoff_token) == "cancelled":
            handoff_token = None
        if handoff_token:
            try:
                linked_handoff = _link_if_fresh()
            except tracking.SessionLifecycleError:
                tracking.save_record(record)
                raise
        elif not candidate_token and not had_active_head and (
            source == "bind" or tracking._pending_handoffs_all_from_yielded(record)
        ):
            tracking._cancel_pending_handoffs(record)
            tracking._append_head_transition(
                record,
                session_id,
                reason="rebind" if source == "bind" else "initial",
                at=event_at,
            )
        tracking.save_record(record)
        return linked_handoff


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
