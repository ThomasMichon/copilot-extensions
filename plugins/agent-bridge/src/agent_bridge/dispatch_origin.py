"""Original Dispatch assignment metadata, not current placement or effect authority."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

CAPABILITY = "dispatch_acp_session_origin"
Text = Annotated[str, Field(strict=True, min_length=1, max_length=512)]
Counter = Annotated[int, Field(strict=True, ge=0, le=(1 << 63) - 1)]


class _Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def require_text(self):
        if any(
            not value.strip() or "\0" in value
            for value in self.__dict__.values() if isinstance(value, str)
        ):
            raise ValueError("dispatch origin references require nonblank, NUL-free text")
        return self


class EmitterReference(_Reference):
    repo: Text
    source: Text
    request_id: Text
    producer_id: Text
    task_id: Text
    generation: Counter


class PoolReference(_Reference):
    task_id: Text
    registration_id: Text
    machine: Text
    env: Text
    reservation_key: Text
    task_generation: Counter
    catalog_revision: Counter


class AssignmentCohort(_Reference):
    schema_name: Literal["dispatch-assignment-cohort"] = Field(alias="schema")
    version: Annotated[int, Field(strict=True, ge=1, le=1)]
    emitter: EmitterReference | None
    pool: PoolReference | None

    @model_serializer(mode="wrap")
    def serialize_cohort(self, handler):
        fields = handler(self)
        if "schema_name" in fields:
            fields["schema"] = fields.pop("schema_name")
        return fields


class DispatchSessionOrigin(_Reference):
    """The assignment that created this session, separate from workspace birth."""

    task_id: Text
    reservation_key: Text
    cohort: AssignmentCohort

    @model_validator(mode="after")
    def require_assignment(self):
        if any(value != value.strip() for value in (self.task_id, self.reservation_key)):
            raise ValueError("dispatch origin requires canonical task and reservation IDs")
        emitter, pool = self.cohort.emitter, self.cohort.pool
        if emitter is not None and emitter.task_id != self.task_id:
            raise ValueError("dispatch origin emitter names another task")
        if pool is not None and (
            pool.task_id != self.task_id or pool.reservation_key != self.reservation_key
        ):
            raise ValueError("dispatch origin pool names another task or reservation")
        return self


def capture(value: object, target) -> dict | None:
    """Detach and validate before any session-start preparation or native effects."""
    if value is None:
        return None
    origin = DispatchSessionOrigin.model_validate(value)
    if (
        target.type != "local" or not target.project or not target.worktree_id
        or target.codespace or target.container or target.spawn_command
        or target.remote_acp or target.host or target.elevated
    ):
        raise ValueError("dispatch origin requires a prepared local ACP workspace")
    return origin.model_dump(mode="json")


def inspect(row: dict | None) -> dict | None:
    """Read first-save config only; mutable targets and current task labels are not evidence."""
    try:
        config = json.loads(row["config_json"]) if row and row.get("config_json") else {}
        value = config.get("dispatch_origin")
        return DispatchSessionOrigin.model_validate(value).model_dump(mode="json") if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None
