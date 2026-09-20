"""Handoff lineage stamping and trace rendering helpers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import activity
from . import config as cfg
from . import handoff_trace
from . import sessions
from . import tracking
from .session_tracking_cli import (
    _all_tracking_dirs,
    _find_tracking_file_exact,
    _project_for_tracking_file,
)

HANDOFF_STAGES: dict[int, str] = {
    1: "worktree_created",
    2: "mux_session_assigned",
    3: "copilot_invoked",
    4: "session_start_bound",
    5: "status_reported",
    6: "handoff_triggered",
    7: "handoff_host_acknowledged",
    8: "handoff_successor_spawn_started",
    9: "handoff_successor_session_start_bound",
    10: "handoff_successor_claimed",
    11: "handoff_pickup_confirmed_predecessor_closing",
    12: "session_end_bound",
    13: "handoff_complete",
}


def _safe_session_segment(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id)[:160] or "unknown"


def session_state_handoff_path(session_id: str | None) -> Path | None:
    """Path to one session's context-handoff session-state record."""
    if not session_id:
        return None
    return (
        sessions._session_state_dir()
        / _safe_session_segment(session_id)
        / "handoff-request.json"
    )


def session_state_trace_path(session_id: str | None) -> Path | None:
    """Path to one session's per-session handoff trace file, when present."""
    if not session_id:
        return None
    return (
        sessions._session_state_dir()
        / _safe_session_segment(session_id)
        / "handoff-trace.jsonl"
    )


def read_session_state_handoff(session_id: str | None) -> dict[str, object] | None:
    """Read one session-state handoff request record, if it exists."""
    path = session_state_handoff_path(session_id)
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def session_state_worktree_binding_path(session_id: str | None) -> Path | None:
    """Path to one session's durable worktree-association record.

    Unconditional counterpart to `session_state_handoff_path` (which only
    exists for a handoff flow): every session that registers against a
    worktree gets this record, regardless of whether a handoff was ever
    involved (gitea aperture-labs#7230, wish 1 -- durable session recording
    must not depend on the handoff mechanism succeeding).
    """
    if not session_id:
        return None
    return (
        sessions._session_state_dir()
        / _safe_session_segment(session_id)
        / "worktree-binding.json"
    )


def stamp_session_state_worktree_binding(
    session_id: str | None,
    worktree_id: str | None,
    *,
    worktree_dir: str | None = None,
    machine: str | None = None,
) -> dict[str, object] | None:
    """Best-effort, unconditional durable record of "this session registered
    against this worktree" -- written from the session-state side (mirrors
    the worktree's own tracking YAML, which already accumulates every
    session in `record.sessions`; this is the session-state-folder half of
    that same fact, so a caller with only a session id in hand -- no
    worktree context -- can still recover which worktree it belongs to, and
    so the association survives even if the worktree's own tracking file is
    ever lost/corrupted).

    Never creates the session-state directory itself -- an active session
    already owns it (mirrors context-handoff's `writeSessionStateHandoff`
    convention); a session-state dir that does not yet exist is a silent
    no-op, not an error. Atomic write via a temp-file rename.
    """
    path = session_state_worktree_binding_path(session_id)
    if path is None or not worktree_id:
        return None
    session_dir = path.parent
    if not session_dir.is_dir():
        return None
    record = {
        "sessionId": session_id,
        "worktreeId": worktree_id,
        "worktreeDir": worktree_dir,
        "machine": machine,
        "registeredAt": tracking._now_iso(),
    }
    try:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return None
    return record


def stamp_session_state_handoff(
    session_id: str | None,
    *,
    handoff_token: str | None = None,
    predecessor_session_id: str | None = None,
    successor_session_id: str | None = None,
) -> dict[str, object] | None:
    """Best-effort lineage-field stamp on an existing handoff request record."""
    path = session_state_handoff_path(session_id)
    if path is None or not path.exists():
        return None
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        with handoff_trace._append_lock(lock_path):
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
            if not isinstance(current, dict):
                return None
            expected = str(handoff_token or "").strip() or None
            current_token = (
                str(current.get("handoffId") or current.get("handoff_token") or "").strip()
                or None
            )
            if expected and current_token and current_token != expected:
                return current
            changed = False
            for key, value in (
                ("predecessor_session_id", predecessor_session_id),
                ("successor_session_id", successor_session_id),
            ):
                if value is None or current.get(key) == value:
                    continue
                current[key] = value
                changed = True
            if changed:
                current["lineageUpdatedAt"] = tracking._now_iso()
                tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
                tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
                tmp.replace(path)
            return current
    except Exception:
        return None


def read_session_state_trace(session_id: str | None) -> list[dict[str, object]]:
    """Best-effort read of a per-session handoff trace JSONL file."""
    path = session_state_trace_path(session_id)
    if path is None or not path.exists():
        return []
    out: list[dict[str, object]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if isinstance(data, dict):
                    out.append(data)
    except OSError:
        return out
    return out


def _event_token(event: dict[str, object]) -> str | None:
    return (
        str(
            event.get("handoff_token")
            or event.get("handoff_id")
            or event.get("handoffId")
            or ""
        ).strip()
        or None
    )


def _event_sort_key(event: dict[str, object]) -> tuple[str, int, str, str]:
    raw_stage = event.get("stage")
    try:
        stage = int(raw_stage) if raw_stage is not None else 0
    except (TypeError, ValueError):
        stage = 0
    return (
        str(event.get("ts") or ""),
        stage,
        str(event.get("event") or ""),
        str(event.get("session_id") or ""),
    )


def _dedupe_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for event in sorted(events, key=_event_sort_key):
        key = json.dumps(
            event, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def _find_tracking_file_by_session_unique(session_id: str) -> Path | None:
    matches: list[Path] = []
    for tracking_dir in _all_tracking_dirs():
        if not tracking_dir.exists():
            continue
        for path in sorted(tracking_dir.glob("*.yaml"), key=lambda item: item.name):
            try:
                if session_id not in path.read_text(encoding="utf-8"):
                    continue
                if tracking.load_record(path).session_entry(session_id):
                    matches.append(path.resolve())
            except Exception:
                continue
    unique = list(dict.fromkeys(matches))
    if len(unique) > 1:
        raise RuntimeError(f"Session id is ambiguous across projects: {session_id}")
    return unique[0] if unique else None


def resolve_trace_target(
    identifier: str,
    *,
    project: str | None = None,
) -> tuple[str, tracking.WorktreeRecord, str]:
    """Resolve a worktree/session selector to one concrete worktree record."""
    target = str(identifier or "").strip()
    if not target:
        raise RuntimeError("handoff-trace requires a worktree id or session id")
    if project:
        cfg.set_active_project(project)
        path = cfg.tracking_dir() / f"{target}.yaml"
        if path.is_file():
            return project, tracking.load_record(path), "worktree"
        for candidate in sorted(
            cfg.tracking_dir().glob("*.yaml"), key=lambda item: item.name
        ):
            try:
                record = tracking.load_record(candidate)
            except Exception:
                continue
            if record.session_entry(target):
                return project, record, "session"
        raise RuntimeError(
            f"No worktree or session found in project {project!r}: {target}"
        )
    active = cfg.active_project()
    if active:
        try:
            return resolve_trace_target(target, project=active)
        except RuntimeError:
            pass
    try:
        path = _find_tracking_file_exact(target)
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc
    selector_kind = "worktree"
    if path is None:
        path = _find_tracking_file_by_session_unique(target)
        selector_kind = "session"
    if path is None:
        raise RuntimeError(f"No tracked worktree or session found: {target}")
    project_name = _project_for_tracking_file(path)
    if not project_name:
        raise RuntimeError(f"Could not resolve owning project for {target}")
    cfg.set_active_project(project_name)
    return project_name, tracking.load_record(path), selector_kind


def _merge_trace_sources(project: str, worktree_id: str) -> list[dict[str, object]]:
    return _dedupe_events(
        handoff_trace.read_trace(project, worktree_id)
        + activity.read_events(worktree_id=worktree_id)
    )


def _select_handoff_token(
    events: list[dict[str, object]],
    record,
    *,
    explicit_token: str | None,
    selector_kind: str,
    selector_value: str,
) -> str:
    if explicit_token:
        return explicit_token
    candidates: list[tuple[tuple[str, int, str, str], str]] = []
    for event in events:
        token = _event_token(event)
        if not token:
            continue
        if selector_kind == "session":
            session_match = selector_value in {
                str(event.get("session_id") or ""),
                str(event.get("predecessor_session_id") or ""),
                str(event.get("successor_session_id") or ""),
            }
            if not session_match:
                continue
        candidates.append((_event_sort_key(event), token))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    if selector_kind == "session":
        for handoff in sorted(record.handoffs, key=lambda item: item.ordinal, reverse=True):
            if selector_value in {
                handoff.predecessor,
                handoff.successor or "",
                handoff.candidate or "",
            }:
                return handoff.token
    if record.handoffs:
        return sorted(record.handoffs, key=lambda item: item.ordinal)[-1].token
    raise RuntimeError(
        "No handoff attempts were recorded for this worktree; pass --token once one exists."
    )


def _pick_latest(
    events: list[dict[str, object]],
    *,
    stage: int,
    before_ts: str | None = None,
    after_ts: str | None = None,
    session_id: str | None = None,
    launch_id: str | None = None,
    token: str | None = None,
) -> list[dict[str, object]]:
    matched = []
    for event in events:
        if event.get("stage") != stage:
            continue
        ts = str(event.get("ts") or "")
        if before_ts and ts and ts > before_ts:
            continue
        if after_ts and ts and ts < after_ts:
            continue
        if session_id and str(event.get("session_id") or "") != session_id:
            continue
        if launch_id and str(event.get("launch_id") or "") != launch_id:
            continue
        if token and _event_token(event) != token:
            continue
        matched.append(event)
    return matched[-1:] if matched else []


def _stage_entries(
    events: list[dict[str, object]],
    *,
    handoff_token: str,
    predecessor_session_id: str | None,
    successor_session_id: str | None,
) -> list[dict[str, object]]:
    token_events = [event for event in events if _event_token(event) == handoff_token]
    stage6 = next((event for event in token_events if event.get("stage") == 6), None)
    stage6_ts = str(stage6.get("ts") or "") if stage6 else None
    predecessor_start = _pick_latest(
        events, stage=4, before_ts=stage6_ts, session_id=predecessor_session_id
    )
    predecessor_launch_id = (
        str(predecessor_start[0].get("launch_id") or "") if predecessor_start else ""
    ) or None
    stage11 = _pick_latest(events, stage=11, token=handoff_token)
    stage11_ts = str(stage11[0].get("ts") or "") if stage11 else None
    stage9_successor = successor_session_id
    stages: list[dict[str, object]] = []
    for stage, stage_name in HANDOFF_STAGES.items():
        matched: list[dict[str, object]]
        if stage == 1:
            matched = _pick_latest(events, stage=stage, before_ts=stage6_ts)
        elif stage in {2, 3}:
            matched = _pick_latest(
                events,
                stage=stage,
                before_ts=stage6_ts,
                launch_id=predecessor_launch_id,
            )
            if not matched:
                matched = _pick_latest(events, stage=stage, before_ts=stage6_ts)
        elif stage == 4:
            matched = predecessor_start
        elif stage == 5:
            matched = _pick_latest(
                events, stage=stage, before_ts=stage6_ts, session_id=predecessor_session_id
            )
            if not matched:
                matched = _pick_latest(events, stage=stage, before_ts=stage6_ts)
        elif stage in {6, 7, 8, 9, 10, 11, 13}:
            matched = [event for event in token_events if event.get("stage") == stage]
            if stage == 9 and stage9_successor:
                success_only = [
                    event
                    for event in matched
                    if str(event.get("session_id") or "") == stage9_successor
                ]
                if success_only:
                    matched = success_only
        elif stage == 12:
            matched = _pick_latest(
                events,
                stage=stage,
                session_id=predecessor_session_id,
                after_ts=stage11_ts or stage6_ts,
            )
        else:
            matched = []
        stages.append(
            {
                "stage": stage,
                "stage_name": stage_name,
                "observed": bool(matched),
                "events": [
                    {
                        "ts": event.get("ts"),
                        "event": event.get("event"),
                        "session_id": event.get("session_id"),
                        "launch_id": event.get("launch_id"),
                        "source": event.get("source"),
                        "handoff_token": _event_token(event),
                        "predecessor_session_id": event.get("predecessor_session_id"),
                        "successor_session_id": event.get("successor_session_id"),
                        "outcome": event.get("outcome"),
                    }
                    for event in matched
                ],
            }
        )
    return stages


def build_trace_report(
    identifier: str,
    *,
    project: str | None = None,
    handoff_token: str | None = None,
) -> dict[str, object]:
    """Resolve, select, and render one handoff attempt's 13-stage trace."""
    selector_value = str(identifier or "").strip()
    project_name, record, selector_kind = resolve_trace_target(
        selector_value, project=project
    )
    events = _merge_trace_sources(project_name, record.worktree_id)
    selected_token = _select_handoff_token(
        events,
        record,
        explicit_token=(str(handoff_token or "").strip() or None),
        selector_kind=selector_kind,
        selector_value=selector_value,
    )
    selected = next(
        (handoff for handoff in record.handoffs if handoff.token == selected_token),
        None,
    )

    def _latest_related_session(
        field: str,
        fallback_to_session: bool,
    ) -> str | None:
        for event in reversed(events):
            if _event_token(event) != selected_token:
                continue
            value = str(event.get(field) or "").strip()
            if value:
                return value
            if fallback_to_session:
                session_value = str(event.get("session_id") or "").strip()
                if session_value:
                    return session_value
        return None

    predecessor_session_id = (
        selected.predecessor
        if selected is not None
        else _latest_related_session("predecessor_session_id", True)
    )
    successor_session_id = (
        (selected.successor or selected.candidate)
        if selected is not None
        else _latest_related_session("successor_session_id", False)
    )
    session_trace_events = read_session_state_trace(
        predecessor_session_id
    ) + read_session_state_trace(successor_session_id)
    events = _dedupe_events(events + session_trace_events)
    return {
        "project": project_name,
        "worktree_id": record.worktree_id,
        "selector_kind": selector_kind,
        "selector_value": selector_value,
        "handoff_token": selected_token,
        "predecessor_session_id": predecessor_session_id,
        "successor_session_id": successor_session_id,
        "session_state": {
            "predecessor": read_session_state_handoff(predecessor_session_id),
            "successor": read_session_state_handoff(successor_session_id),
        },
        "stages": _stage_entries(
            events,
            handoff_token=selected_token,
            predecessor_session_id=predecessor_session_id,
            successor_session_id=successor_session_id,
        ),
    }


def render_trace_report(report: dict[str, object]) -> str:
    """Human-readable handoff trace report."""
    lines = [
        "handoff trace: "
        f"{report['project']} / {report['worktree_id']} / {report['handoff_token']}",
    ]
    predecessor = report.get("predecessor_session_id")
    successor = report.get("successor_session_id")
    if predecessor or successor:
        lines.append(
            f"predecessor={predecessor or '-'} successor={successor or '-'}"
        )
    for stage in report.get("stages", []):
        number = stage["stage"]
        name = stage["stage_name"]
        if not stage["observed"]:
            lines.append(f"stage {number:>2}: {name}: never observed")
            continue
        observed = []
        for event in stage.get("events", []):
            detail = str(event.get("event") or "")
            if event.get("outcome"):
                detail += f" ({event['outcome']})"
            if event.get("ts"):
                detail += f" @ {event['ts']}"
            observed.append(detail)
        lines.append(
            f"stage {number:>2}: {name}: observed via " + ", ".join(observed)
        )
    return "\n".join(lines)


def cmd_handoff_trace(args, *, json_output, json_error) -> int:
    """CLI entrypoint for rendering one handoff attempt's trace."""
    project = getattr(args, "project", None)
    if project:
        cfg.set_active_project(project)
    try:
        report = build_trace_report(
            getattr(args, "trace_target", None),
            project=project,
            handoff_token=getattr(args, "token", None),
        )
    except RuntimeError as exc:
        return json_error(str(exc))
    if getattr(args, "json", False):
        json_output(report)
    else:
        print(render_trace_report(report))
    return 0
