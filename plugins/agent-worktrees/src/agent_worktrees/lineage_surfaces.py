"""Bounded worktree and exact-session lineage JSON surfaces."""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from . import controller_lineage
from . import reciprocal_presentation
from . import session_projection
from . import tracking

RecordLoader = Callable[[str, str], tracking.WorktreeRecord | None]
ProjectionReader = Callable[[str], dict[str, Any] | None]
RestoredReader = Callable[[str], bool]
MAX_WORKTREE_SESSIONS = 512
MAX_HEAD_TRANSITIONS = 512
MAX_HANDOFFS = 256
MAX_CONTROLLERS = 32
MAX_LINEAGE_STEPS = 512
MAX_PROJECTION_HEALTH = 128


def _safe_identity_token(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )


def _record_loader(
    project: str,
    worktree_id: str,
) -> tracking.WorktreeRecord | None:
    if not _safe_identity_token(project) or not _safe_identity_token(worktree_id):
        return None
    try:
        from . import config as cfg

        tracking_dir = cfg.project_dir(project) / "worktrees"
        path = tracking_dir / f"{worktree_id}.yaml"
        if path.parent != tracking_dir or not path.is_file():
            return None
        return tracking.load_record(path)
    except Exception:
        return None


def _session_node(
    entry: tracking.SessionEntry,
    *,
    is_head: bool,
) -> dict[str, Any]:
    return {
        "session_id": entry.session_id,
        "authoritative": True,
        "state": entry.state,
        "is_head": is_head,
        "relation_revision": entry.relation_revision,
        "started_at": entry.started_at,
        "ended_at": entry.ended_at,
        "predecessor": entry.predecessor,
        "successor": entry.successor,
        "activations": [
            dataclasses.asdict(activation)
            for activation in entry.activations
        ],
    }


def _bounded_sessions(
    record: tracking.WorktreeRecord,
) -> tuple[list[tracking.SessionEntry], int]:
    sessions = list(record.sessions or ())
    if len(sessions) <= MAX_WORKTREE_SESSIONS:
        return sessions, 0
    retained = sessions[-MAX_WORKTREE_SESSIONS:]
    head = record.resolved_head_session
    if head and all(entry.session_id != head for entry in retained):
        head_entry = record.session_entry(head)
        if head_entry is not None:
            retained = [head_entry, *retained[-(MAX_WORKTREE_SESSIONS - 1):]]
    retained_ids = {entry.session_id for entry in retained}
    return (
        [
            entry
            for entry in sessions
            if entry.session_id in retained_ids
        ],
        len(sessions) - len(retained_ids),
    )


def _session_graph(
    record: tracking.WorktreeRecord,
    sessions: list[tracking.SessionEntry],
    handoffs: list[tracking.SessionHandoff],
) -> dict[str, Any]:
    entries = {
        entry.session_id: entry
        for entry in sessions
    }
    authoritative_ids = {
        entry.session_id
        for entry in record.sessions or ()
    }
    nodes = [
        _session_node(
            entry,
            is_head=record.resolved_head_session == entry.session_id,
        )
        for entry in sessions
    ]
    edges_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    referenced: set[str] = set()
    integrity_findings: list[dict[str, Any]] = []

    def add_edge(
        source: str,
        target: str,
        *,
        evidence: str,
        ordinal: int | None = None,
        state: str | None = None,
    ) -> None:
        key = (source, target)
        edge = edges_by_pair.setdefault(key, {
            "kind": "successor",
            "source": source,
            "target": target,
            "evidence": [],
            "handoffs": [],
        })
        if evidence not in edge["evidence"]:
            edge["evidence"].append(evidence)
        if ordinal is not None:
            edge["handoffs"].append({
                "ordinal": ordinal,
                "state": state,
            })
        referenced.update((source, target))

    for entry in sessions:
        if entry.successor:
            add_edge(
                entry.session_id,
                entry.successor,
                evidence="successor",
            )
            target = entries.get(entry.successor)
            if target is not None and target.predecessor != entry.session_id:
                integrity_findings.append({
                    "status": (
                        "missing-predecessor"
                        if target.predecessor is None
                        else "conflicting-predecessor"
                    ),
                    "source": entry.session_id,
                    "target": entry.successor,
                    "observed": target.predecessor,
                })
        if entry.predecessor:
            add_edge(
                entry.predecessor,
                entry.session_id,
                evidence="predecessor",
            )
            source = entries.get(entry.predecessor)
            if source is not None and source.successor != entry.session_id:
                integrity_findings.append({
                    "status": (
                        "missing-successor"
                        if source.successor is None
                        else "conflicting-successor"
                    ),
                    "source": entry.predecessor,
                    "target": entry.session_id,
                    "observed": source.successor,
                })
    for handoff in handoffs:
        if handoff.successor:
            add_edge(
                handoff.predecessor,
                handoff.successor,
                evidence="handoff",
                ordinal=handoff.ordinal,
                state=handoff.state,
            )

    omitted = sorted((referenced & authoritative_ids) - entries.keys())
    missing = sorted(referenced - authoritative_ids)
    nodes.extend({
        "session_id": session_id,
        "authoritative": True,
        "state": "omitted",
        "is_head": record.resolved_head_session == session_id,
    } for session_id in omitted)
    nodes.extend({
        "session_id": session_id,
        "authoritative": False,
        "state": "missing",
        "is_head": False,
    } for session_id in missing)
    session_index, successor_index = controller_lineage.lineage_indexes(record)
    findings = [
        {
            "session_id": entry.session_id,
            **controller_lineage.resolve_terminal_session(
                record,
                entry.session_id,
                max_steps=MAX_LINEAGE_STEPS,
                session_index=session_index,
                successor_index=successor_index,
            ),
        }
        for entry in sessions
    ]
    return {
        "nodes": nodes,
        "edges": list(edges_by_pair.values()),
        "findings": findings,
        "integrity_findings": integrity_findings,
    }


def _bounded_tail(items: list[Any], limit: int) -> tuple[list[Any], int]:
    if len(items) <= limit:
        return list(items), 0
    return list(items[-limit:]), len(items) - limit


def _bounded_controllers(
    record: tracking.WorktreeRecord,
) -> tuple[list[tracking.ControllerRelation], int]:
    controllers = list(record.controllers)
    if len(controllers) <= MAX_CONTROLLERS:
        return controllers, 0
    active = sorted(
        (item for item in controllers if item.state == "active"),
        key=lambda item: item.relation_revision,
    )
    ended = sorted(
        (item for item in controllers if item.state != "active"),
        key=lambda item: item.relation_revision,
    )
    if len(active) >= MAX_CONTROLLERS:
        retained = active[-MAX_CONTROLLERS:]
    else:
        retained = [
            *active,
            *ended[-(MAX_CONTROLLERS - len(active)):],
        ]
    return retained, len(controllers) - len(retained)


def _bound_info(total: int, returned: int, limit: int) -> dict[str, Any]:
    omitted = max(0, total - returned)
    return {
        "limit": limit,
        "total": total,
        "returned": returned,
        "omitted": omitted,
        "overflow": omitted > 0,
    }


def _projection_health(
    record: tracking.WorktreeRecord,
    session_id: str,
    *,
    role: str,
    record_loader: RecordLoader,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "project": record.repo,
        "worktree_id": record.worktree_id,
        "session_id": session_id,
        "role": role,
        "status": "current",
        "repairable": False,
        "repaired": False,
        "restored": False,
    }
    if role not in {"bound", "controller"}:
        item["status"] = "invalid-role"
        return item
    try:
        restored = session_projection.is_restored(session_id)
        item["restored"] = restored
        projection = session_projection.read(session_id)
    except session_projection.MissingSessionTree:
        item["status"] = "missing-session-tree"
        return item
    except session_projection.UnsupportedProjectionVersion:
        item["status"] = "newer-schema"
        return item
    except session_projection.ProjectionError:
        item["status"] = "restored-invalid" if item["restored"] else "invalid"
        item["repairable"] = not item["restored"]
        return item
    return session_projection._classify_relation(
        record,
        session_id,
        role=role,
        projection=projection,
        restored=restored,
        record_loader=record_loader,
    )


def worktree_lineage(
    record: tracking.WorktreeRecord,
    *,
    record_loader: RecordLoader = _record_loader,
) -> dict[str, Any]:
    """Render one authoritative worktree as a bounded visualization graph."""
    retained_sessions, _ = _bounded_sessions(record)
    retained_transitions, _ = _bounded_tail(
        record.head_transitions,
        MAX_HEAD_TRANSITIONS,
    )
    retained_handoffs, _ = _bounded_tail(
        record.handoffs,
        MAX_HANDOFFS,
    )
    retained_controllers, _ = _bounded_controllers(record)
    surface_record = dataclasses.replace(
        record,
        sessions=retained_sessions,
        head_transitions=retained_transitions,
        handoffs=retained_handoffs,
        controllers=retained_controllers,
    )
    findings = controller_lineage.controller_findings(
        surface_record,
        record_loader=record_loader,
        max_lineage_steps=MAX_LINEAGE_STEPS,
    )
    projection_candidates: list[tuple[str, str]] = []
    head = record.resolved_head_session
    if head:
        projection_candidates.append((head, "bound"))
    projection_candidates.extend(
        (relation.controller_session_id, "controller")
        for relation in sorted(
            retained_controllers,
            key=lambda item: item.relation_revision,
            reverse=True,
        )
        if relation.state == "active" and relation.controller_session_id
    )
    projection_candidates.extend(
        (entry.session_id, "bound")
        for entry in reversed(retained_sessions)
    )
    projection_candidates.extend(
        (relation.controller_session_id, "controller")
        for relation in sorted(
            retained_controllers,
            key=lambda item: item.relation_revision,
            reverse=True,
        )
        if relation.state != "active" and relation.controller_session_id
    )
    projection_candidates = list(dict.fromkeys(projection_candidates))
    retained_projection_candidates = projection_candidates[
        :MAX_PROJECTION_HEALTH
    ]
    projection_health = [
        _projection_health(
            record,
            session_id,
            role=role,
            record_loader=record_loader,
        )
        for session_id, role in retained_projection_candidates
    ]
    return {
        "surface": "worktree-lineage",
        "surface_version": 1,
        "project": record.repo,
        "worktree_id": record.worktree_id,
        "machine": record.machine,
        "platform": record.platform,
        "status": record.status,
        "revisions": {
            "lifecycle": record.lifecycle_revision,
            "head": record.head_revision,
            "controller": record.controller_revision,
        },
        "head_session": record.resolved_head_session,
        "bounds": {
            "sessions": _bound_info(
                len(record.sessions or ()),
                len(retained_sessions),
                MAX_WORKTREE_SESSIONS,
            ),
            "head_transitions": _bound_info(
                len(record.head_transitions),
                len(retained_transitions),
                MAX_HEAD_TRANSITIONS,
            ),
            "handoffs": _bound_info(
                len(record.handoffs),
                len(retained_handoffs),
                MAX_HANDOFFS,
            ),
            "controllers": _bound_info(
                len(record.controllers),
                len(retained_controllers),
                MAX_CONTROLLERS,
            ),
            "projection_health": _bound_info(
                len(projection_candidates),
                len(retained_projection_candidates),
                MAX_PROJECTION_HEALTH,
            ),
        },
        "sessions": [
            _session_node(
                entry,
                is_head=record.resolved_head_session == entry.session_id,
            )
            for entry in retained_sessions
        ],
        "head_transitions": [
            dataclasses.asdict(transition)
            for transition in retained_transitions
        ],
        "handoffs": [
            {
                "ordinal": handoff.ordinal,
                "predecessor": handoff.predecessor,
                "state": handoff.state,
                "opened_at": handoff.opened_at,
                "successor": handoff.successor,
                "linked_at": handoff.linked_at,
                "candidate": handoff.candidate,
                "candidate_at": handoff.candidate_at,
            }
            for handoff in retained_handoffs
        ],
        "controllers": [
            tracking.controller_relation_to_dict(relation)
            for relation in retained_controllers
        ],
        "controller_findings": findings,
        "reciprocal_relation": reciprocal_presentation.derive(
            record,
            findings,
        ),
        "projection_health": projection_health,
        "graph": _session_graph(
            record,
            retained_sessions,
            retained_handoffs,
        ),
    }


def _projection_read(
    session_id: str,
    *,
    projection_reader: ProjectionReader,
    restored_reader: RestoredReader,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    status: dict[str, Any] = {
        "status": "missing-projection",
        "restored": False,
        "schema_version": None,
        "overflow": False,
        "omitted_relations": 0,
        "returned_relations": 0,
        "surface_omitted_relations": 0,
        "returned_tombstones": 0,
        "surface_omitted_tombstones": 0,
    }
    try:
        status["restored"] = restored_reader(session_id)
        projection = projection_reader(session_id)
    except session_projection.MissingSessionTree:
        status["status"] = "missing-session-tree"
        return None, status
    except session_projection.UnsupportedProjectionVersion:
        status["status"] = "unsupported"
        return None, status
    except session_projection.ProjectionError:
        status["status"] = "invalid"
        return None, status
    if projection is None:
        return None, status
    overflow = projection.get("overflow", False)
    omitted = projection.get("omitted_relations", 0)
    if not isinstance(overflow, bool) or type(omitted) is not int or omitted < 0:
        status["status"] = "invalid"
        return None, status
    status.update({
        "status": "incomplete" if overflow or omitted else "available",
        "schema_version": projection.get("version"),
        "overflow": overflow,
        "omitted_relations": omitted,
    })
    return projection, status


def _bounded_projected_relations(
    relations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if len(relations) <= session_projection.MAX_RELATIONS:
        return relations, 0
    bound = [item for item in relations if item.get("role") == "bound"]
    nonterminal = [
        item for item in relations
        if item.get("role") != "bound"
        and session_projection._protected_relation(item)
    ]
    terminal = [
        item for item in relations
        if not session_projection._protected_relation(item)
    ]
    for group in (bound, nonterminal, terminal):
        group.sort(key=session_projection._relation_sort_key)
    retained = bound[-session_projection.MAX_RELATIONS:]
    remaining = session_projection.MAX_RELATIONS - len(retained)
    if remaining:
        retained = [*retained, *nonterminal[-remaining:]]
        remaining = session_projection.MAX_RELATIONS - len(retained)
    if remaining:
        retained = [*retained, *terminal[-remaining:]]
    retained.sort(key=session_projection._relation_sort_key)
    return retained, len(relations) - len(retained)


def session_lineage(
    session_id: str,
    *,
    record_loader: RecordLoader = _record_loader,
    projection_reader: ProjectionReader = session_projection.read,
    restored_reader: RestoredReader = session_projection.is_restored,
) -> dict[str, Any]:
    """Render one exact session projection without scanning session-state."""
    projection, projection_status = _projection_read(
        session_id,
        projection_reader=projection_reader,
        restored_reader=restored_reader,
    )
    result: dict[str, Any] = {
        "surface": "session-lineage",
        "surface_version": 1,
        "session_id": session_id,
        "projection": projection_status,
        "relations": [],
        "relation_tombstones": [],
    }
    if projection is None:
        return result

    relations: list[dict[str, Any]] = []
    projected_relations = projection.get("relations", [])
    retained_relations, omitted_relations = _bounded_projected_relations(
        projected_relations,
    )
    projected_tombstones = [
        item
        for item in projection.get("relation_tombstones", [])
        if isinstance(item, dict)
    ]
    retained_tombstones = projected_tombstones[
        -session_projection.MAX_RELATION_TOMBSTONES:
    ]
    omitted_tombstones = (
        len(projected_tombstones) - len(retained_tombstones)
    )
    if omitted_relations or omitted_tombstones:
        projection_status["status"] = "incomplete"
    projection_status["returned_relations"] = len(retained_relations)
    projection_status["surface_omitted_relations"] = omitted_relations
    projection_status["returned_tombstones"] = len(retained_tombstones)
    projection_status["surface_omitted_tombstones"] = omitted_tombstones
    for projected in retained_relations:
        role = projected.get("role")
        project = projected.get("project")
        worktree_id = projected.get("worktree_id")
        item: dict[str, Any] = {
            "projection": dict(projected),
            "authority": {
                "status": "invalid",
                "project": project,
                "worktree_id": worktree_id,
                "session_id": session_id,
                "role": role,
                "repairable": False,
                "repaired": False,
                "restored": bool(projection_status["restored"]),
            },
        }
        if (
            role not in {"bound", "controller"}
            or not _safe_identity_token(project)
            or not _safe_identity_token(worktree_id)
        ):
            relations.append(item)
            continue
        try:
            record = record_loader(str(project), str(worktree_id))
        except Exception:
            item["authority"]["status"] = "unreadable"
            relations.append(item)
            continue
        if record is None:
            item["authority"]["status"] = "missing-record"
            relations.append(item)
            continue
        classification = session_projection._classify_relation(
            record,
            session_id,
            role=role,
            projection=projection,
            restored=bool(projection_status["restored"]),
            record_loader=record_loader,
        )
        item["authority"] = classification
        item["worktree"] = {
            "project": record.repo,
            "worktree_id": record.worktree_id,
            "machine": record.machine,
            "platform": record.platform,
            "status": record.status,
            "revisions": {
                "lifecycle": record.lifecycle_revision,
                "head": record.head_revision,
                "controller": record.controller_revision,
            },
            "head_session": record.resolved_head_session,
            "presentation": {
                "evaluated": False,
                "reason": "exact-session-scope",
            },
        }
        if role == "bound":
            item["lineage"] = {
                "session_id": session_id,
                **controller_lineage.resolve_terminal_session(
                    record,
                    session_id,
                    max_steps=MAX_LINEAGE_STEPS,
                ),
            }
        else:
            controller = record.controller_for_session(session_id)
            item["controller_relation"] = (
                tracking.controller_relation_to_dict(controller)
                if controller is not None
                else None
            )
        relations.append(item)
    result["relations"] = relations
    result["relation_tombstones"] = [
        dict(item)
        for item in retained_tombstones
        if isinstance(item, dict)
    ]
    return result
