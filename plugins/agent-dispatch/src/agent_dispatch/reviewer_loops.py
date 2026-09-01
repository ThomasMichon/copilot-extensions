"""Repository reviewer-loop declarations expanded onto existing primitives."""

from __future__ import annotations

from collections.abc import Mapping

from .registrar import ProfileDeclaration, RegistrarError, load_declaration

_KNOWN_KEYS = frozenset(
    {
        "name",
        "kind",
        "repo",
        "task_label",
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
    declarations = (
        {
            "name": f"{name}-source",
            "kind": "emitter",
            "spec": {
                **emitter,
                "id": f"{name}-source",
                "evaluator_ref": evaluator_ref,
            },
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
            **common,
        },
        {
            "name": f"{name}-workers",
            "labels": list(dict.fromkeys((task_label, *additional_labels))),
            "repos": repo,
            **pool,
            **common,
        },
    )
    return tuple(load_declaration(declaration) for declaration in declarations)
