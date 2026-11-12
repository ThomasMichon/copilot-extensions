"""Resolve registered relevance scope under the task decision's SQLite lock."""

from __future__ import annotations

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


def registration_revision(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT registration_revision FROM pool_catalog_clock WHERE singleton=1",
    ).fetchone()["registration_revision"]


def resolve_registration(
    conn: sqlite3.Connection, task: Task, selector: RelevanceRegistration, *,
    disabled_ids: frozenset[str], require_revision: bool,
) -> tuple[str, str, str]:
    """Store-backed local emitters only; declared/evaluator admission follows later."""
    for value in (selector.registration_id, selector.machine, selector.env):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise TaskError("relevance registration needs an explicit id, machine and environment")
    if selector.registration_id.startswith("declared:"):
        raise TaskError("declared relevance scope requires effective registrar admission")
    current_revision = registration_revision(conn)
    if require_revision and (
        type(selector.revision) is not int or selector.revision != current_revision
    ):
        raise TaskError("relevance registration revision changed; refresh the source check")
    row = conn.execute(
        "SELECT * FROM registrations WHERE id=?", (selector.registration_id,),
    ).fetchone()
    if row is None or (
        row["kind"] != RegistrationKind.EMITTER
        or row["status"] != RegistrationStatus.ACTIVE
        or row["machine"] != selector.machine or row["env"] != selector.env
    ):
        raise TaskError("relevance needs an active emitter in the executing local scope")
    reg = dict(row)
    reg["spec"] = spec = json.loads(row["spec"])
    if registration_override_ids(reg) & disabled_ids:
        raise TaskError("relevance registration is disabled by operator override")
    if spec.get("task_output") != "json" or not spec.get("command"):
        raise TaskError("relevance requires a task-authoring command emitter")
    source, origin = spec.get("source") or "emitter", spec.get("id")
    if not source or not origin or task.kind != "finite" or (
        task.source, task.origin_ref,
    ) != (source, origin):
        raise TaskError("relevance registration does not match this finite task's source")
    others = conn.execute(
        "SELECT id, spec FROM registrations WHERE kind=? AND id!=?",
        (RegistrationKind.EMITTER, selector.registration_id),
    ).fetchall()
    if any(
        ((other_spec := json.loads(other["spec"])).get("source") or "emitter",
         other_spec.get("id")) == (source, origin)
        for other in others
    ):
        raise TaskError("relevance emitter attribution is ambiguous across registrations")
    return task.repo, source, origin
