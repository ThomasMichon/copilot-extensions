"""Mechanical extraction from ``tracking.py`` for controller-relation helpers.

This module exists only to keep ``agent_worktrees.tracking`` under its
grandfathered module-size baseline. The functions below were moved verbatim,
with no behavior change, from ``tracking.py``.
"""

from __future__ import annotations

from pathlib import Path

from .tracking import (
    _MAX_CONTROLLER_RELATIONS,
    MAX_PERSISTED_COUNTER,
    ClaimRef,
    ControllerRelation,
    ControllerRelationError,
    ControllerRelationSource,
    WorktreeRecord,
    format_claim_ref,
    parse_claim_ref,
)


def _RecordLock(*args, **kwargs):
    from .tracking import _RecordLock as impl

    return impl(*args, **kwargs)


def _flush_session_projections(record: WorktreeRecord) -> None:
    from .tracking import _flush_session_projections as impl

    impl(record)


def _now_iso() -> str:
    from .tracking import _now_iso as impl

    return impl()


def _save_record_unlocked(record: WorktreeRecord, path: Path) -> None:
    from .tracking import _save_record_unlocked as impl

    impl(record, path)


def load_record(path: Path) -> WorktreeRecord:
    from .tracking import load_record as impl

    return impl(path)


def save_record(record: WorktreeRecord, path: Path | None = None) -> None:
    from .tracking import save_record as impl

    impl(record, path)


def _valid_relation_session_id(session_id: str) -> bool:
    return bool(
        session_id
        and session_id not in {".", ".."}
        and "/" not in session_id
        and "\\" not in session_id
        and "\x00" not in session_id
        and Path(session_id).name == session_id
    )


def _normalize_controller_ref(
    controller_ref: str,
    *,
    session_id: str | None = None,
) -> tuple[str, ClaimRef]:
    """Validate and canonicalize one controller ClaimRef."""
    if not controller_ref or controller_ref.count("#") > 1:
        raise ControllerRelationError("controller_ref must be a worktree ClaimRef")
    body, _, ref_session = controller_ref.partition("#")
    parts = body.split("/")
    if len(parts) not in (1, 3) or any(not part for part in parts):
        raise ControllerRelationError(
            "controller_ref must be worktree_id or "
            "machine/project/worktree_id[#session]"
        )
    parsed = parse_claim_ref(controller_ref)
    if parsed is None or not parsed.worktree_id:
        raise ControllerRelationError("controller_ref must identify a worktree")
    if any(token in parsed.worktree_id for token in ("/", "\\", "\x00")):
        raise ControllerRelationError(
            "controller_ref worktree_id must be one path-safe identifier"
        )
    exact_session = session_id or ref_session or None
    if session_id and ref_session and session_id != ref_session:
        raise ControllerRelationError(
            "controller_ref session does not match controller_session_id"
        )
    if exact_session and not _valid_relation_session_id(exact_session):
        raise ControllerRelationError(f"invalid controller session id {exact_session!r}")
    normalized = format_claim_ref(
        parsed.machine,
        parsed.project,
        parsed.worktree_id,
        exact_session,
    )
    return normalized, ClaimRef(
        worktree_id=parsed.worktree_id,
        machine=parsed.machine,
        project=parsed.project,
        session=exact_session,
    )


def _controller_ref_key(controller_ref: str | None) -> tuple[
    str | None, str | None, str
] | None:
    if not controller_ref:
        return None
    _normalized, parsed = _normalize_controller_ref(controller_ref)
    return parsed.machine, parsed.project, parsed.worktree_id


def _controller_refs_overlap(
    left: str | None,
    right: str | None,
) -> bool:
    left_key = _controller_ref_key(left)
    right_key = _controller_ref_key(right)
    if left_key is None or right_key is None:
        return False
    if left_key[2] != right_key[2]:
        return False
    left_qualified = bool(left_key[0] and left_key[1])
    right_qualified = bool(right_key[0] and right_key[1])
    return (
        left_key == right_key
        if left_qualified and right_qualified
        else True
    )


def controller_relation_to_dict(
    relation: ControllerRelation,
) -> dict[str, object]:
    """Render one normalized controller relation for JSON surfaces."""
    return {
        "kind": relation.kind,
        "source": relation.source,
        "controller_ref": relation.controller_ref,
        "controller_session_id": relation.controller_session_id,
        "state": relation.state,
        "relation_revision": relation.relation_revision,
        "created_at": relation.created_at,
        "ended_at": relation.ended_at,
    }


def _mark_controller_projection_dirty(
    record: WorktreeRecord,
    *session_ids: str | None,
) -> None:
    dirty = set(getattr(record, "_controller_projection_dirty", set()))
    dirty.update(
        session_id for session_id in session_ids
        if session_id and _valid_relation_session_id(session_id)
    )
    record._controller_projection_dirty = dirty


def _next_controller_revision(record: WorktreeRecord) -> int:
    highest = max(
        (relation.relation_revision for relation in record.controllers),
        default=0,
    )
    revision = max(record.controller_revision, highest) + 1
    if revision > MAX_PERSISTED_COUNTER:
        raise ControllerRelationError("controller revision counter is exhausted")
    record.controller_revision = revision
    return revision


def _limit_controller_relations(
    relations: list[ControllerRelation],
) -> tuple[list[ControllerRelation], list[ControllerRelation]]:
    if len(relations) <= _MAX_CONTROLLER_RELATIONS:
        return relations, []
    active = sorted(
        (relation for relation in relations if relation.state == "active"),
        key=lambda relation: relation.relation_revision,
    )
    if len(active) > _MAX_CONTROLLER_RELATIONS:
        raise ControllerRelationError(
            f"at most {_MAX_CONTROLLER_RELATIONS} active controller relations "
            "may be recorded"
        )
    ended = sorted(
        (relation for relation in relations if relation.state == "ended"),
        key=lambda relation: relation.relation_revision,
    )
    keep_ended = _MAX_CONTROLLER_RELATIONS - len(active)
    retained = active + ended[-keep_ended:] if keep_ended else active
    retained_ids = {id(relation) for relation in retained}
    removed = [
        relation for relation in relations if id(relation) not in retained_ids
    ]
    retained.sort(key=lambda relation: relation.relation_revision)
    return retained, removed


def _validate_controller_relation_set(
    relations: list[ControllerRelation],
) -> None:
    for index, relation in enumerate(relations):
        for prior in relations[:index]:
            if (
                relation.controller_session_id
                and relation.controller_session_id
                == prior.controller_session_id
            ):
                raise ControllerRelationError(
                    "controller session is assigned to multiple relations"
                )
            if _controller_refs_overlap(
                relation.controller_ref, prior.controller_ref
            ):
                raise ControllerRelationError(
                    "controller worktree is assigned to multiple relations"
                )


def _derive_initial_controller_relations(
    *,
    machine: str,
    project: str,
    owner_ref: str | None,
    caller_worktree: str | None,
    parent_session: str | None,
    created_at: str,
) -> tuple[list[ControllerRelation], int]:
    """Derive deterministic initial control from legacy creation metadata."""
    if parent_session and not _valid_relation_session_id(parent_session):
        raise ControllerRelationError(
            f"invalid controller session id {parent_session!r}"
        )
    relations: list[ControllerRelation] = []

    def add_reference(
        source: ControllerRelationSource,
        raw_ref: str,
    ) -> None:
        normalized, parsed = _normalize_controller_ref(raw_ref)
        for relation in relations:
            if _controller_refs_overlap(relation.controller_ref, normalized):
                if parsed.session and not relation.controller_session_id:
                    relation.controller_session_id = parsed.session
                    relation.controller_ref = normalized
                elif (
                    parsed.is_qualified
                    and relation.controller_ref
                    and not all(
                        _controller_ref_key(relation.controller_ref)[:2]
                    )
                ):
                    relation.controller_ref = normalized
                return
            if (parsed.session and
                    relation.controller_session_id == parsed.session):
                if relation.controller_ref is None:
                    relation.controller_ref = normalized
                    relation.kind = "worktree"
                return
        relations.append(ControllerRelation(
            kind="worktree",
            source=source,
            controller_ref=normalized,
            controller_session_id=parsed.session,
            relation_revision=len(relations) + 1,
            created_at=created_at,
        ))

    if owner_ref:
        add_reference("owner-ref", owner_ref)
    if caller_worktree:
        caller_ref = caller_worktree
        parsed_caller = parse_claim_ref(caller_worktree)
        matches_richer_owner = bool(
            parsed_caller is not None
            and not parsed_caller.is_qualified
            and any(
                (
                    key := _controller_ref_key(
                        relation.controller_ref
                    )
                ) is not None
                and key[2] == parsed_caller.worktree_id
                for relation in relations
            )
        )
        if (
            parsed_caller is not None
            and not parsed_caller.is_qualified
            and machine
            and project
            and not matches_richer_owner
        ):
            caller_ref = format_claim_ref(
                machine,
                project,
                parsed_caller.worktree_id,
                parsed_caller.session,
            )
        add_reference("caller-worktree", caller_ref)
    if parent_session:
        matching = next((relation for relation in relations
                         if relation.source == "caller-worktree"), None)
        if matching is None:
            matching = next((relation for relation in relations
                             if relation.controller_session_id == parent_session), None)
        if matching is None:
            unbound_refs = [
                relation for relation in relations
                if relation.controller_session_id is None
            ]
            if len(unbound_refs) == 1:
                matching = unbound_refs[0]
                matching.controller_session_id = parent_session
                normalized, _parsed = _normalize_controller_ref(
                    matching.controller_ref or "",
                    session_id=parent_session,
                )
                matching.controller_ref = normalized
            else:
                relations.append(ControllerRelation(
                    kind="session",
                    source="parent-session",
                    controller_session_id=parent_session,
                    relation_revision=len(relations) + 1,
                    created_at=created_at,
                ))
    return relations, len(relations)


def derive_legacy_controller_relations(
    record: WorktreeRecord,
) -> list[ControllerRelation]:
    """Derive explicit controller state from one legacy creation record.

    Ordinary loads and saves deliberately leave legacy creation fields alone.
    This helper is reserved for explicit doctor/backfill flows.
    """
    if record.controller_metadata_opaque:
        raise ControllerRelationError(
            "controller metadata contains unsupported entries"
        )
    if record.controller_revision or record.controllers:
        return []
    if (
        record.controller_raw_revision_present
        or record.controller_raw_entries_present
    ):
        return []
    if not (record.owner_ref or record.caller_worktree or record.parent_session):
        return []
    relations, _revision = _derive_initial_controller_relations(
        machine=record.machine,
        project=record.repo,
        owner_ref=record.owner_ref,
        caller_worktree=record.caller_worktree,
        parent_session=record.parent_session,
        created_at=record.started_at,
    )
    return relations


def backfill_legacy_controller_relations(
    record: WorktreeRecord,
    *,
    path: Path | None = None,
) -> list[ControllerRelation]:
    """Persist controller relations derived from legacy creation metadata."""
    target = path or record.yaml_path
    with _RecordLock(target, require_sidecar=True):
        authoritative = load_record(target) if target.exists() else record
        relations = derive_legacy_controller_relations(authoritative)
        if not relations:
            _sync_record_instance(record, authoritative)
            return []
        authoritative.controllers = relations
        authoritative.controller_revision = max(
            relation.relation_revision for relation in relations
        )
        _mark_controller_projection_dirty(
            authoritative,
            *(
                relation.controller_session_id
                for relation in relations
                if relation.controller_session_id
            ),
        )
        _save_record_unlocked(authoritative, target)
    _flush_session_projections(authoritative)
    _sync_record_instance(record, authoritative)
    return relations


def _controller_relation_matches(
    relation: ControllerRelation,
    *,
    controller_ref: str | None,
    controller_session_id: str | None,
) -> bool:
    selector_session = controller_session_id
    if controller_ref is not None:
        normalized, parsed = _normalize_controller_ref(
            controller_ref,
            session_id=controller_session_id,
        )
        selector_session = parsed.session
        if not _controller_refs_overlap(
            relation.controller_ref, normalized
        ):
            return False
    if (selector_session is not None and
            relation.controller_session_id != selector_session):
        return False
    return True


def _sync_record_instance(
    target: WorktreeRecord,
    source: WorktreeRecord,
) -> None:
    """Refresh a caller-held record after a relation-only transaction."""
    if target is source:
        return
    target.controller_revision = source.controller_revision
    target.controllers = source.controllers
    target.controller_metadata_opaque = source.controller_metadata_opaque
    target.controller_raw_revision = source.controller_raw_revision
    target.controller_raw_entries = source.controller_raw_entries
    target.controller_raw_revision_present = (
        source.controller_raw_revision_present)
    target.controller_raw_entries_present = (
        source.controller_raw_entries_present)
    target._controller_projection_dirty = (
        set(getattr(target, "_controller_projection_dirty", set()))
        | set(getattr(source, "_controller_projection_dirty", set()))
    )


def set_controller_relation(
    record: WorktreeRecord,
    *,
    controller_ref: str | None = None,
    controller_session_id: str | None = None,
    source: ControllerRelationSource = "explicit",
    created_at: str | None = None,
    save: bool = True,
    path: Path | None = None,
) -> ControllerRelation:
    """Add or refresh a controller without changing session binding or head.

    The default write path is a locked reload-mutate-save transaction, so two
    processes cannot allocate the same revision from stale snapshots. Callers
    using ``save=False`` must already hold the record lock through their final
    :func:`save_record`.
    """
    if save:
        target = path or record.yaml_path
        with _RecordLock(target, require_sidecar=True):
            authoritative = load_record(target) if target.exists() else record
            relation = set_controller_relation(
                authoritative,
                controller_ref=controller_ref,
                controller_session_id=controller_session_id,
                source=source,
                created_at=created_at,
                save=False,
                path=target,
            )
            _save_record_unlocked(authoritative, target)
        _flush_session_projections(authoritative)
        _sync_record_instance(record, authoritative)
        return relation
    if source not in (
        "explicit", "owner-ref", "caller-worktree", "parent-session"
    ):
        raise ControllerRelationError(f"unsupported controller source {source!r}")
    if record.controller_metadata_opaque:
        raise ControllerRelationError(
            "controller metadata contains unsupported entries; explicit repair "
            "is required before mutation"
        )
    normalized_ref = None
    parsed = None
    if controller_ref:
        normalized_ref, parsed = _normalize_controller_ref(
            controller_ref,
            session_id=controller_session_id,
        )
        controller_session_id = parsed.session
    elif controller_session_id:
        if not _valid_relation_session_id(controller_session_id):
            raise ControllerRelationError(
                f"invalid controller session id {controller_session_id!r}"
            )
    else:
        raise ControllerRelationError(
            "controller_ref or controller_session_id is required"
        )

    ref_matches = [
        candidate for candidate in record.controllers
        if normalized_ref is not None and _controller_refs_overlap(
            candidate.controller_ref, normalized_ref
        )
    ]
    session_matches = [
        candidate for candidate in record.controllers
        if (
            controller_session_id is not None
            and candidate.controller_session_id == controller_session_id
        )
    ]
    if len(ref_matches) > 1 or len(session_matches) > 1:
        raise ControllerRelationError(
            "existing controller identity is ambiguous"
        )
    if (
        ref_matches
        and session_matches
        and ref_matches[0] is not session_matches[0]
    ):
        raise ControllerRelationError(
            "controller_ref and controller_session_id identify "
            "different relations"
        )
    relation = (
        ref_matches[0]
        if ref_matches
        else session_matches[0] if session_matches else None
    )
    prior_session = relation.controller_session_id if relation else None
    if (
        relation is None
        and len(record.active_controllers) >= _MAX_CONTROLLER_RELATIONS
    ):
        raise ControllerRelationError(
            f"at most {_MAX_CONTROLLER_RELATIONS} active controller relations "
            "may be recorded"
        )
    revision = _next_controller_revision(record)
    if relation is None:
        relation = ControllerRelation(
            kind="worktree" if normalized_ref else "session",
            source=source,
            controller_ref=normalized_ref,
            controller_session_id=controller_session_id,
            relation_revision=revision,
            created_at=created_at or _now_iso(),
        )
        record.controllers.append(relation)
    else:
        if normalized_ref:
            relation.controller_ref = normalized_ref
            relation.kind = "worktree"
        if controller_session_id:
            relation.controller_session_id = controller_session_id
        relation.source = source
        relation.state = "active"
        relation.ended_at = None
        relation.relation_revision = revision
        if created_at:
            relation.created_at = created_at

    _validate_controller_relation_set(record.controllers)
    record.controllers, removed = _limit_controller_relations(record.controllers)
    _mark_controller_projection_dirty(
        record,
        prior_session,
        relation.controller_session_id,
        *(item.controller_session_id for item in removed),
    )
    if save:
        save_record(record)
    return relation


def end_controller_relation(
    record: WorktreeRecord,
    *,
    controller_ref: str | None = None,
    controller_session_id: str | None = None,
    ended_at: str | None = None,
    save: bool = True,
    path: Path | None = None,
) -> ControllerRelation:
    """End one exact controller relation without altering the bound head."""
    if save:
        target = path or record.yaml_path
        with _RecordLock(target, require_sidecar=True):
            authoritative = load_record(target) if target.exists() else record
            relation = end_controller_relation(
                authoritative,
                controller_ref=controller_ref,
                controller_session_id=controller_session_id,
                ended_at=ended_at,
                save=False,
                path=target,
            )
            _save_record_unlocked(authoritative, target)
        _flush_session_projections(authoritative)
        _sync_record_instance(record, authoritative)
        return relation
    if controller_ref is None and controller_session_id is None:
        raise ControllerRelationError(
            "controller_ref or controller_session_id is required"
        )
    if record.controller_metadata_opaque:
        raise ControllerRelationError(
            "controller metadata contains unsupported entries; explicit repair "
            "is required before mutation"
        )
    if (controller_session_id is not None and
            not _valid_relation_session_id(controller_session_id)):
        raise ControllerRelationError(
            f"invalid controller session id {controller_session_id!r}"
        )
    matching = [
        relation for relation in record.controllers
        if _controller_relation_matches(
            relation,
            controller_ref=controller_ref,
            controller_session_id=controller_session_id,
        )
    ]
    if len(matching) != 1:
        raise ControllerRelationError(
            "controller relation was not found"
            if not matching else "controller relation selector is ambiguous"
        )
    relation = matching[0]
    revision = _next_controller_revision(record)
    relation.state = "ended"
    relation.ended_at = ended_at or _now_iso()
    relation.relation_revision = revision
    _mark_controller_projection_dirty(
        record, relation.controller_session_id
    )
    if save:
        save_record(record)
    return relation


def remove_controller_relation(
    record: WorktreeRecord,
    *,
    controller_ref: str | None = None,
    controller_session_id: str | None = None,
    save: bool = True,
    path: Path | None = None,
) -> None:
    """Remove one relation for explicit repair and retract its projection."""
    if save:
        target = path or record.yaml_path
        with _RecordLock(target, require_sidecar=True):
            authoritative = load_record(target) if target.exists() else record
            remove_controller_relation(
                authoritative,
                controller_ref=controller_ref,
                controller_session_id=controller_session_id,
                save=False,
                path=target,
            )
            _save_record_unlocked(authoritative, target)
        _flush_session_projections(authoritative)
        _sync_record_instance(record, authoritative)
        return
    if controller_ref is None and controller_session_id is None:
        raise ControllerRelationError(
            "controller_ref or controller_session_id is required"
        )
    if record.controller_metadata_opaque:
        raise ControllerRelationError(
            "controller metadata contains unsupported entries; explicit repair "
            "is required before mutation"
        )
    if (controller_session_id is not None and
            not _valid_relation_session_id(controller_session_id)):
        raise ControllerRelationError(
            f"invalid controller session id {controller_session_id!r}"
        )
    matching = [
        relation for relation in record.controllers
        if _controller_relation_matches(
            relation,
            controller_ref=controller_ref,
            controller_session_id=controller_session_id,
        )
    ]
    if len(matching) != 1:
        raise ControllerRelationError(
            "controller relation was not found"
            if not matching else "controller relation selector is ambiguous"
        )
    relation = matching[0]
    _next_controller_revision(record)
    record.controllers.remove(relation)
    _mark_controller_projection_dirty(
        record, relation.controller_session_id
    )
    if save:
        save_record(record)
