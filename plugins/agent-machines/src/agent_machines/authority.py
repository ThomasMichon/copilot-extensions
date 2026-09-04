"""Shared authority metadata and deterministic provenance helpers."""

from __future__ import annotations

import json
from typing import Any

AUTHORITY_MIN = -1000
AUTHORITY_MAX = 1000
AUTHORITY_MODE_OPAQUE_ADDITIVE = "opaque-additive"


def effective_authority(package: Any, declaration: dict[str, Any]) -> int:
    """Return a declaration override or its package authority."""
    return int(declaration.get("authority", package.authority))


def contributor(
    package: Any,
    declaration: dict[str, Any],
) -> dict[str, Any]:
    """Return stable public provenance for one declaration."""
    return {
        "package": package.name,
        "source_repo": package.source_repo,
        "authority": effective_authority(package, declaration),
    }


def contributor_sort_key(item: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(item.get("source_repo", "")),
        str(item.get("package", "")),
        int(item.get("authority", 0)),
    )


def unique_contributors(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate and sort contributor provenance."""
    unique = {
        (
            str(item.get("package", "")),
            str(item.get("source_repo", "")),
            int(item.get("authority", 0)),
        ): {
            "package": str(item.get("package", "")),
            "source_repo": str(item.get("source_repo", "")),
            "authority": int(item.get("authority", 0)),
        }
        for item in items
    }
    return sorted(unique.values(), key=contributor_sort_key)


def decision_sort_key(decision: dict[str, Any]) -> tuple[str, str]:
    """Sort decisions independently of package discovery order."""
    return (
        str(decision.get("domain", "")),
        json.dumps(decision.get("identity"), sort_keys=True, separators=(",", ":")),
    )


def sort_decisions(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return stable, duplicate-free authority decisions."""
    unique: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        normalized = {
            "domain": str(decision["domain"]),
            "identity": decision["identity"],
            "selected": unique_contributors(list(decision.get("selected", []))),
            "superseded": unique_contributors(list(decision.get("superseded", []))),
        }
        key = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        unique[key] = normalized
    return sorted(unique.values(), key=decision_sort_key)
