"""Validated, public-safe model-routing assignment provenance."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, fields

ROUTING_SCHEMA_VERSION = 1
ELIGIBILITY_STATES = frozenset({"demonstrated", "candidate"})
ASSIGNMENT_STATES = frozenset(
    {"assigned", "admitted", "launched", "running", "terminal"}
)
TERMINAL_DISPOSITIONS = frozenset(
    {"accepted", "rejected", "retried", "abandoned", "superseded", "denied"}
)
ACTOR_ROLES = frozenset(
    {"coordinator", "worker", "supervisor", "reviewer", "evaluator"}
)
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+~=-]*$")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_ASSIGNMENT_KEYS = frozenset(
    {
        "purpose",
        "selected_model",
        "eligibility_state",
        "selection_reason",
        "execution_surface",
        "decision_ref",
        "parent_assignment_id",
        "containment_profile_ref",
        "trial_ref",
        "coordinator_session_ref",
    }
)


class RoutingProvenanceError(ValueError):
    """Routing assignment provenance is malformed or conflicts with history."""


def token(
    value: object,
    *,
    field: str,
    limit: int = 240,
    optional: bool = False,
) -> str | None:
    """Return one bounded opaque token, rejecting paths and free-form payloads."""
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise RoutingProvenanceError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized and optional:
        return None
    if not normalized:
        raise RoutingProvenanceError(f"{field} must be non-empty")
    if (
        normalized.startswith(("/", "\\\\", "//"))
        or _WINDOWS_ABSOLUTE.match(normalized)
    ):
        raise RoutingProvenanceError(f"{field} must not be an absolute path")
    if len(normalized) > limit or _TOKEN.fullmatch(normalized) is None:
        raise RoutingProvenanceError(
            f"{field} must be a bounded identifier, not free-form content"
        )
    return normalized


def normalize_assignment(value: Mapping[str, object]) -> dict[str, str | None]:
    """Validate the immutable portion of one routing assignment."""
    unknown = set(value) - _ASSIGNMENT_KEYS
    if unknown:
        raise RoutingProvenanceError(
            "routing assignment contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    eligibility = token(
        value.get("eligibility_state"),
        field="eligibility_state",
        limit=32,
    )
    if eligibility not in ELIGIBILITY_STATES:
        raise RoutingProvenanceError("eligibility_state is not supported")
    trial_ref = token(
        value.get("trial_ref"),
        field="trial_ref",
        optional=True,
    )
    if eligibility == "candidate" and trial_ref is None:
        raise RoutingProvenanceError("candidate assignments require trial_ref")
    return {
        "purpose": token(value.get("purpose"), field="purpose", limit=64),
        "selected_model": token(
            value.get("selected_model"),
            field="selected_model",
            limit=128,
        ),
        "eligibility_state": eligibility,
        "selection_reason": token(
            value.get("selection_reason"),
            field="selection_reason",
            limit=128,
        ),
        "execution_surface": token(
            value.get("execution_surface"),
            field="execution_surface",
            limit=64,
        ),
        "decision_ref": token(
            value.get("decision_ref"),
            field="decision_ref",
        ),
        "parent_assignment_id": token(
            value.get("parent_assignment_id"),
            field="parent_assignment_id",
            optional=True,
        ),
        "containment_profile_ref": token(
            value.get("containment_profile_ref"),
            field="containment_profile_ref",
            optional=True,
        ),
        "trial_ref": trial_ref,
        "coordinator_session_ref": token(
            value.get("coordinator_session_ref"),
            field="coordinator_session_ref",
            optional=True,
        ),
    }


@dataclass(frozen=True)
class RoutingAssignment:
    """Read-only snapshot of one dispatch-owned routing assignment."""

    schema_version: int
    assignment_id: str
    task_id: str
    attempt: int
    parent_assignment_id: str | None
    purpose: str
    selected_model: str
    eligibility_state: str
    selection_reason: str
    execution_surface: str
    containment_profile_ref: str | None
    trial_ref: str | None
    decision_ref: str
    coordinator_session_ref: str | None
    worker_session_ref: str | None
    state: str
    terminal_disposition: str | None
    reason_code: str | None
    created_at: float
    updated_at: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RoutingAssignment:
        return cls(
            **{
                field.name: row[field.name]
                for field in fields(cls)
            }
        )
