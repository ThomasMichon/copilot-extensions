"""Derived controller-session lineage findings."""

from __future__ import annotations

from typing import Callable

from . import config as cfg
from . import session_projection
from . import tracking

RecordLoader = Callable[[str, str], tracking.WorktreeRecord | None]
ProjectionReader = Callable[[str], dict | None]
SessionIndex = dict[str, tracking.SessionEntry]
SuccessorIndex = dict[str, set[str]]


def _safe_identity_token(value: str) -> bool:
    return bool(
        value
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )


def _default_record_loader(
    project: str,
    worktree_id: str,
) -> tracking.WorktreeRecord | None:
    if not _safe_identity_token(project) or not _safe_identity_token(worktree_id):
        return None
    try:
        tracking_dir = cfg.project_dir(project) / "worktrees"
        path = tracking_dir / f"{worktree_id}.yaml"
        if path.parent != tracking_dir:
            return None
        if not path.is_file():
            return None
        return tracking.load_record(path)
    except Exception:
        return None


def _controller_target(
    child: tracking.WorktreeRecord,
    relation: tracking.ControllerRelation,
    *,
    record_loader: RecordLoader,
    projection_reader: ProjectionReader,
    local_machine: str,
) -> tuple[str, str, str | None, str | None]:
    ref = (
        tracking.parse_claim_ref(relation.controller_ref)
        if relation.controller_ref
        else None
    )
    if ref is not None:
        if ref.machine and ref.machine != local_machine:
            return "remote", ref.project or child.repo, ref.worktree_id, (
                relation.controller_session_id or ref.session
            )
        return (
            "local",
            ref.project or child.repo,
            ref.worktree_id,
            relation.controller_session_id or ref.session,
        )

    session_id = relation.controller_session_id
    if not session_id:
        return "missing-session", child.repo, None, None
    try:
        restored = session_projection.is_restored(session_id)
        projection = projection_reader(session_id)
    except session_projection.MissingSessionTree:
        return "missing-session-tree", child.repo, None, session_id
    except session_projection.UnsupportedProjectionVersion:
        return "unsupported-projection", child.repo, None, session_id
    except session_projection.ProjectionError:
        return "invalid-projection", child.repo, None, session_id
    if projection is None:
        return "missing-projection", child.repo, None, session_id
    if restored:
        validation = session_projection.validate_restored_hint(
            session_id,
            projection,
            record_loader=record_loader,
        )
        if validation["status"] != "restored-validated":
            return str(validation["status"]), child.repo, None, session_id
        return (
            "local",
            str(validation["project"]),
            str(validation["worktree_id"]),
            session_id,
        )
    bound = [
        item
        for item in projection.get("relations", [])
        if isinstance(item, dict)
        and item.get("role") == "bound"
        and item.get("project") == child.repo
        and isinstance(item.get("worktree_id"), str)
        and item.get("worktree_id")
    ]
    targets = {str(item["worktree_id"]) for item in bound}
    if len(targets) != 1:
        return (
            "ambiguous-projection" if targets else "unbound-projection",
            child.repo,
            None,
            session_id,
        )
    return "local", child.repo, next(iter(targets)), session_id


def lineage_indexes(
    record: tracking.WorktreeRecord,
) -> tuple[SessionIndex, SuccessorIndex]:
    """Index one authoritative record for repeated bounded lineage reads."""
    sessions: SessionIndex = {}
    for entry in record.sessions or ():
        sessions.setdefault(entry.session_id, entry)
    successors: SuccessorIndex = {
        session_id: ({entry.successor} if entry.successor else set())
        for session_id, entry in sessions.items()
    }
    for handoff in record.handoffs:
        if handoff.successor:
            successors.setdefault(handoff.predecessor, set()).add(
                handoff.successor
            )
    return sessions, successors


def resolve_terminal_session(
    record: tracking.WorktreeRecord,
    start_session_id: str,
    *,
    max_steps: int | None = None,
    session_index: SessionIndex | None = None,
    successor_index: SuccessorIndex | None = None,
) -> dict[str, object]:
    """Follow explicit successor links to one terminal session."""
    if session_index is None or successor_index is None:
        session_index, successor_index = lineage_indexes(record)
    current = start_session_id
    visited: list[str] = []
    while True:
        if max_steps is not None and len(visited) >= max(0, max_steps):
            return {
                "status": "overflow",
                "terminal_session_id": None,
                "lineage": visited,
                "next_session_id": current,
            }
        if current in visited:
            return {
                "status": "cycle",
                "terminal_session_id": None,
                "lineage": visited + [current],
            }
        visited.append(current)
        entry = session_index.get(current)
        if entry is None:
            return {
                "status": "missing-session",
                "terminal_session_id": None,
                "lineage": visited,
            }
        successors = successor_index.get(current, set())
        if len(successors) > 1:
            return {
                "status": "ambiguous",
                "terminal_session_id": None,
                "lineage": visited,
                "successors": sorted(successors),
            }
        if not successors:
            if entry.state == "active":
                return {
                    "status": "resolved",
                    "terminal_session_id": current,
                    "lineage": visited,
                }
            return {
                "status": "controller-terminal",
                "terminal_session_id": None,
                "lineage": visited,
            }
        current = next(iter(successors))


def controller_findings(
    child: tracking.WorktreeRecord,
    *,
    record_loader: RecordLoader = _default_record_loader,
    projection_reader: ProjectionReader = session_projection.read,
    local_machine: str | None = None,
    max_lineage_steps: int | None = None,
) -> list[dict[str, object]]:
    """Resolve controller relations without mutating authoritative records."""
    if local_machine is None:
        try:
            local_machine = cfg.load_config().machine
        except Exception:
            local_machine = child.machine

    findings: list[dict[str, object]] = []
    record_cache: dict[
        tuple[str, str], tracking.WorktreeRecord | None
    ] = {}
    lineage_cache: dict[
        tuple[str, str], tuple[SessionIndex, SuccessorIndex]
    ] = {}
    for relation in child.controllers:
        finding: dict[str, object] = {
            "relation_revision": relation.relation_revision,
            "controller_ref": relation.controller_ref,
            "controller_session_id": relation.controller_session_id,
            "relation_state": relation.state,
        }
        try:
            if relation.state != "active":
                finding["status"] = "ended"
                finding["terminal_session_id"] = None
                findings.append(finding)
                continue

            target_status, project, worktree_id, session_id = _controller_target(
                child,
                relation,
                record_loader=record_loader,
                projection_reader=projection_reader,
                local_machine=local_machine,
            )
            finding["controller_project"] = project
            finding["controller_worktree_id"] = worktree_id
            if target_status == "remote":
                finding["status"] = "remote"
                ref = (
                    tracking.parse_claim_ref(relation.controller_ref)
                    if relation.controller_ref
                    else None
                )
                finding["controller_machine"] = ref.machine if ref else None
                finding["terminal_session_id"] = None
                finding["remote_session_id"] = session_id
                findings.append(finding)
                continue
            if target_status != "local" or not worktree_id:
                finding["status"] = (
                    target_status
                    if target_status != "local"
                    else "unresolved-ref"
                )
                finding["terminal_session_id"] = None
                findings.append(finding)
                continue

            cache_key = (project, worktree_id)
            if cache_key not in record_cache:
                record_cache[cache_key] = record_loader(project, worktree_id)
            controller = record_cache[cache_key]
            if controller is None:
                finding["status"] = "missing-record"
                finding["terminal_session_id"] = None
                findings.append(finding)
                continue
            finding["controller_machine"] = controller.machine
            finding["head_revision"] = controller.head_revision
            if session_id is None:
                transition = controller.replayed_head_transition
                if transition is None or transition.session_id is None:
                    finding["status"] = "ambiguous"
                    finding["terminal_session_id"] = None
                    findings.append(finding)
                    continue
                session_id = transition.session_id
            if cache_key not in lineage_cache:
                lineage_cache[cache_key] = lineage_indexes(controller)
            session_index, successor_index = lineage_cache[cache_key]
            finding.update(resolve_terminal_session(
                controller,
                session_id,
                max_steps=max_lineage_steps,
                session_index=session_index,
                successor_index=successor_index,
            ))
            findings.append(finding)
        except Exception:
            finding["status"] = "error"
            finding["terminal_session_id"] = None
            findings.append(finding)
    return findings
