"""Azure DevOps work-item discovery scoping for repository-issue-loops.

Extracted from ``repository_issue_loops`` (kept module-size-neutral): Azure
DevOps work-item discovery has no natural per-repo boundary the way GitHub
does -- a flat, unscoped WIQL query can exceed the platform's 20000-item
result cap on any project with real history (confirmed against a live
project during Phase 1 live-proof testing, 2026-09-20). A declaration's
``forge.discovery_scope`` narrows that query by work item type, area path,
and/or max age; this module owns validating that config and rendering it
into WIQL clauses.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from .registrar import RegistrarError

ADO_SCOPE_KEYS = frozenset({"work_item_types", "area_path", "max_age_days"})


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RegistrarError(
            "repository-issue-loop forge.discovery_scope.work_item_types: "
            "expected a list of non-empty strings"
        )
    return tuple(dict.fromkeys(value))


def validate_discovery_scope(
    forge: Mapping[str, Any], *, provider: Any
) -> dict[str, Any] | None:
    """Validate the optional ``forge.discovery_scope`` narrowing config.

    ``discovery_scope`` is azure-devops-only (rejecting it elsewhere keeps a
    declaration from silently no-op-ing a scope meant for a different
    provider).
    """
    scope = forge.get("discovery_scope")
    if scope is None:
        return None
    if provider != "azure-devops":
        raise RegistrarError(
            "repository-issue-loop forge.discovery_scope: only supported "
            "for forge.provider 'azure-devops'"
        )
    if not isinstance(scope, Mapping):
        raise RegistrarError(
            "repository-issue-loop forge.discovery_scope: expected a mapping"
        )
    extra = sorted(set(scope) - ADO_SCOPE_KEYS)
    if extra:
        raise RegistrarError(
            f"repository-issue-loop forge.discovery_scope: unknown key(s) {extra}"
        )
    work_item_types = _strings(scope.get("work_item_types", ()))
    area_path = scope.get("area_path")
    if area_path is not None and (not isinstance(area_path, str) or not area_path):
        raise RegistrarError(
            "repository-issue-loop forge.discovery_scope.area_path: "
            "expected a non-empty string"
        )
    max_age_days = scope.get("max_age_days")
    if max_age_days is not None:
        if (
            isinstance(max_age_days, bool)
            or not isinstance(max_age_days, (int, float))
            or max_age_days <= 0
        ):
            raise RegistrarError(
                "repository-issue-loop forge.discovery_scope.max_age_days: "
                "expected a positive number"
            )
    if not work_item_types and area_path is None and max_age_days is None:
        raise RegistrarError(
            "repository-issue-loop forge.discovery_scope: must narrow by at "
            "least one of work_item_types, area_path, max_age_days"
        )
    return {
        "work_item_types": list(work_item_types),
        "area_path": area_path,
        "max_age_days": max_age_days,
    }


def wiql_literal(value: str) -> str:
    # WIQL string literals escape an embedded single quote by doubling it,
    # the same convention as SQL.
    return value.replace("'", "''")


def wiql_scope_clauses(scope: Mapping[str, Any] | None) -> str:
    """Render a validated ``discovery_scope`` as extra ``And``-joined WIQL
    clauses (empty string when there is no scope to apply)."""
    if not scope:
        return ""
    clauses = []
    work_item_types = scope.get("work_item_types") or []
    if work_item_types:
        values = " Or ".join(
            f"[System.WorkItemType] = '{wiql_literal(t)}'" for t in work_item_types
        )
        clauses.append(f"({values})")
    area_path = scope.get("area_path")
    if area_path:
        clauses.append(f"[System.AreaPath] Under '{wiql_literal(area_path)}'")
    max_age_days = scope.get("max_age_days")
    if max_age_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        clauses.append(f"[System.CreatedDate] >= '{cutoff.strftime('%Y-%m-%d')}'")
    return "".join(f" And {clause}" for clause in clauses)
