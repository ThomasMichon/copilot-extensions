"""Resolve registered relevance scope under the task decision's SQLite lock."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import sqlite3

from .queue_common import Task
from .queue_records import TaskError
from .registrations import RegistrationKind, RegistrationStatus
from .supervisor_registration import registration_override_ids


@dataclass(frozen=True)
class RelevanceRegistration:
    registration_id: str
    machine: str
    env: str
    revision: int | None = None
    effective_revision: str | None = None


@dataclass(frozen=True)
class EffectiveRelevanceSources:
    registrations: dict[str, dict]
    revision: str


@dataclass(frozen=True)
class ResolvedRelevanceScope:
    scope: tuple[str, str, str]
    effective_revision: str | None = None


RelevanceAdmission = Callable[[sqlite3.Connection, RelevanceRegistration], EffectiveRelevanceSources]


def registration_revision(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT registration_revision FROM pool_catalog_clock WHERE singleton=1",
    ).fetchone()["registration_revision"]


def resolve_registration(
    conn: sqlite3.Connection, task: Task, selector: RelevanceRegistration, *,
    disabled_ids: frozenset[str], require_revision: bool,
    admission: RelevanceAdmission | None = None,
) -> ResolvedRelevanceScope:
    """Resolve source ownership under the same queue transaction as the decision."""
    for value in (selector.registration_id, selector.machine, selector.env):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise TaskError("relevance registration needs an explicit id, machine and environment")
    current_revision = registration_revision(conn)
    if require_revision and (
        type(selector.revision) is not int or selector.revision != current_revision
    ):
        raise TaskError("relevance registration revision changed; refresh the source check")
    if admission is not None:
        effective = admission(conn, selector)
        registrations, effective_revision = effective.registrations, effective.revision
    else:
        if selector.registration_id.startswith("declared:"):
            raise TaskError("declared relevance scope requires effective registrar admission")
        registrations = {}
        for row in conn.execute("SELECT * FROM registrations").fetchall():
            reg = dict(row)
            reg["spec"] = json.loads(row["spec"])
            registrations[reg["id"]] = reg
        effective_revision = None
    reg = registrations.get(selector.registration_id)
    if reg is None or not _local_active(reg, selector):
        raise TaskError("relevance needs an active registration in the executing local scope")
    if registration_override_ids(reg) & disabled_ids:
        raise TaskError("relevance registration is disabled by operator override")
    extended = reg["id"].startswith("declared:") or reg["kind"] == RegistrationKind.EVALUATOR
    if extended and effective_revision is None:
        raise TaskError("relevance scope requires effective registrar admission")
    if require_revision and (
        (extended and selector.effective_revision is None)
        or (selector.effective_revision is not None and selector.effective_revision != effective_revision)
    ):
        raise TaskError("relevance effective registration revision changed; refresh the source check")
    emitters = [
        item for item in registrations.values()
        if item["kind"] == RegistrationKind.EMITTER
        and ((spec := item["spec"]).get("source") or "emitter", spec.get("id"))
        == (task.source, task.origin_ref)
    ]
    if len(emitters) > 1:
        raise TaskError("relevance emitter attribution is ambiguous across registrations")
    if task.kind != "finite" or len(emitters) != 1:
        raise TaskError("relevance registration does not match this finite task's source")
    emitter = emitters[0]
    spec = emitter["spec"]
    if (
        not _local_active(emitter, selector)
        or registration_override_ids(emitter) & disabled_ids
    ):
        raise TaskError("relevance companion emitter is unavailable or disabled by operator override")
    if spec.get("task_output") != "json" or not spec.get("command"):
        raise TaskError("relevance requires a task-authoring command emitter")
    if reg["kind"] == RegistrationKind.EVALUATOR:
        from .verification import _evaluator_for_task

        candidates = [
            (item["spec"], item) for item in registrations.values()
            if item["kind"] == RegistrationKind.EVALUATOR
            and _local_active(item, selector)
            and not registration_override_ids(item) & disabled_ids
        ]
        selected = _evaluator_for_task(
            candidates, repo=task.repo, evaluator_ref=task.evaluator_ref,
        ) if task.evaluator_ref else None
        if (
            selected is not reg or not task.evaluator_ref
            or spec.get("evaluator_ref") != task.evaluator_ref
        ):
            raise TaskError("relevance evaluator does not match this emitter's repo/evaluator scope")
    elif reg["kind"] != RegistrationKind.EMITTER or reg["id"] != emitter["id"]:
        raise TaskError("relevance registration does not match this finite task's source")
    return ResolvedRelevanceScope((task.repo, task.source, task.origin_ref), effective_revision)


def _local_active(reg: dict, selector: RelevanceRegistration) -> bool:
    return (
        reg["status"] == RegistrationStatus.ACTIVE
        and reg["machine"] == selector.machine and reg["env"] == selector.env
    )
