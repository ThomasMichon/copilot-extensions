"""Repository reviewer-loop declarations expanded onto existing primitives."""

from __future__ import annotations

from collections.abc import Mapping

from .registrar import (
    Filters,
    ProfileDeclaration,
    RegistrarError,
    _load_filters,
    load_declaration,
)

_KNOWN_KEYS = frozenset(
    {
        "name",
        "kind",
        "repo",
        "task_label",
        "filters",
        "emitter",
        "evaluator",
        "pool",
        "owner",
        "description",
    }
)


def _mapping(data: Mapping, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise RegistrarError(
            f"reviewer-loop {key}: expected a mapping, got {type(value).__name__}"
        )
    return dict(value)


def _string(data: Mapping, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise RegistrarError(
            f"reviewer-loop {key}: expected a non-empty string, got {value!r}"
        )
    return value


def _strings(data: dict, key: str) -> tuple[str, ...]:
    value = data.pop(key, ())
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RegistrarError(
            f"reviewer-loop {key}: expected a list of non-empty strings"
        )
    return tuple(dict.fromkeys(value))


def _reject_reserved(section: str, data: Mapping, reserved: set[str]) -> None:
    present = sorted(reserved & set(data))
    if present:
        raise RegistrarError(
            f"reviewer-loop {section}: {present} are derived from the loop declaration"
        )


def _filters_payload(filters: Filters) -> dict[str, dict[str, list[str]]]:
    payload = {}
    for side in ("permit", "reject"):
        values = getattr(filters, side)
        if values:
            payload[side] = {
                dimension: sorted(accepted)
                for dimension, accepted in sorted(values.items())
            }
    return payload


def _compose_filters(common: Filters, specific: Filters) -> Filters:
    permit = dict(common.permit)
    for dimension, accepted in specific.permit.items():
        permit[dimension] = (
            permit[dimension] & accepted
            if dimension in permit
            else accepted
        )
    reject = dict(common.reject)
    for dimension, rejected in specific.reject.items():
        reject[dimension] = reject.get(dimension, frozenset()) | rejected
    for dimension, accepted in permit.items():
        if not accepted:
            raise RegistrarError(
                f"reviewer-loop filters: combined filters permit no "
                f"{dimension!r} value"
            )
        if accepted <= reject.get(dimension, frozenset()):
            raise RegistrarError(
                f"reviewer-loop filters: every permitted {dimension!r} value "
                "is rejected"
            )
    return Filters(permit=permit, reject=reject)


def _placement_filters(data: object) -> Filters:
    try:
        filters = _load_filters(data)
    except RegistrarError as exc:
        raise RegistrarError(f"reviewer-loop filters: {exc}") from exc
    dimensions = set(filters.permit) | set(filters.reject)
    unsupported = sorted(dimensions - {"machine"})
    if unsupported:
        raise RegistrarError(
            "reviewer-loop filters: top-level placement supports only the "
            f"'machine' dimension; put worker eligibility in pool.filters: {unsupported}"
        )
    return filters


def expand_reviewer_loop(data: Mapping) -> tuple[ProfileDeclaration, ...]:
    """Expand one high-level reviewer loop into emitter, evaluator, and pool units.

    The child names and evaluator association depend only on the loop name. Mutable
    commands, guidance, and pool settings therefore update the same supervised units
    instead of forking a second producer or worker pool.
    """
    if not isinstance(data, Mapping):
        raise RegistrarError(
            f"reviewer-loop: expected a mapping, got {type(data).__name__}"
        )
    extra = sorted(set(data) - _KNOWN_KEYS)
    if extra:
        raise RegistrarError(
            f"reviewer-loop: unknown key(s) {extra}; known: {sorted(_KNOWN_KEYS)}"
        )
    if data.get("kind") != "reviewer-loop":
        raise RegistrarError("reviewer-loop kind must be 'reviewer-loop'")

    name = _string(data, "name")
    repo = _string(data, "repo")
    task_label = _string(data, "task_label")
    owner = data.get("owner")
    description = data.get("description")
    for key, value in (("owner", owner), ("description", description)):
        if value is not None and not isinstance(value, str):
            raise RegistrarError(
                f"reviewer-loop {key}: expected a string, got {value!r}"
            )

    emitter = _mapping(data, "emitter")
    evaluator = _mapping(data, "evaluator")
    pool = _mapping(data, "pool")
    placement_filters = _placement_filters(data.get("filters"))
    try:
        pool_filters = _load_filters(pool.pop("filters", None))
    except RegistrarError as exc:
        raise RegistrarError(f"reviewer-loop pool.filters: {exc}") from exc
    additional_labels = _strings(pool, "additional_labels")
    _reject_reserved("emitter", emitter, {"id", "evaluator_ref"})
    _reject_reserved("evaluator", evaluator, {"repo", "evaluator_ref"})
    _reject_reserved(
        "pool",
        pool,
        {"name", "labels", "repos", "kind", "spec", "owner", "description"},
    )

    evaluator_ref = f"{name}-lifecycle"
    common = {"owner": owner, "description": description}
    common_filters = _filters_payload(placement_filters)
    worker_filters = _filters_payload(
        _compose_filters(placement_filters, pool_filters)
    )
    declarations = (
        {
            "name": f"{name}-source",
            "kind": "emitter",
            "spec": {
                **emitter,
                "id": f"{name}-source",
                "evaluator_ref": evaluator_ref,
            },
            "filters": common_filters,
            **common,
        },
        {
            "name": f"{name}-evaluator",
            "kind": "evaluator",
            "spec": {
                **evaluator,
                "repo": repo,
                "evaluator_ref": evaluator_ref,
            },
            "filters": common_filters,
            **common,
        },
        {
            "name": f"{name}-workers",
            "labels": list(dict.fromkeys((task_label, *additional_labels))),
            "repos": repo,
            **pool,
            "filters": worker_filters,
            **common,
        },
    )
    return tuple(load_declaration(declaration) for declaration in declarations)
