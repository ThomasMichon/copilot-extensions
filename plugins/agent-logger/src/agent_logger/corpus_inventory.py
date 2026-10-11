"""Explicit, read-only inventory of primary session stores in a synced corpus."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from agent_logger.source_roots import iter_archive_sources


class SessionObservation(TypedDict):
    session_id: str
    kind: str


class SourceInventory(TypedDict):
    source_key: str
    live: int
    archived: int
    sessions: list[SessionObservation] | None
    error: str | None


class CorpusInventory(TypedDict):
    schema_version: int
    root: str
    scope: str
    complete: bool
    source_count: int
    session_count: int
    sources: list[SourceInventory]
    errors: list[str]


def inventory_corpus(root: Path, *, include_sessions: bool = False) -> CorpusInventory:
    """Count source-qualified refs without journal/readiness or repository filters.

    Counts represent primary live/archive observations, not logical deduplication,
    rescue capture versions, archive-content verification, or accounting coverage.
    Preserve the shared reader's live precedence and explicit overlap failures.
    """
    root = root.expanduser().absolute()
    result: CorpusInventory = {
        "schema_version": 1,
        "root": str(root),
        "scope": "primary-session-stores",
        "complete": True,
        "source_count": 0,
        "session_count": 0,
        "sources": [],
        "errors": [],
    }
    try:
        for source in iter_archive_sources(root):
            row: SourceInventory = {
                "source_key": source.key,
                "live": 0,
                "archived": 0,
                "sessions": [] if include_sessions else None,
                "error": None,
            }
            try:
                for ref in source.iter_sessions():
                    if ref.is_archive:
                        row["archived"] += 1
                    else:
                        row["live"] += 1
                    if row["sessions"] is not None:
                        row["sessions"].append({"session_id": ref.id, "kind": ref.kind})
            except (OSError, ValueError) as exc:
                row["error"] = str(exc)
                result["complete"] = False
                result["errors"].append(f"{source.key}: {exc}")
            result["sources"].append(row)
            result["source_count"] += 1
            result["session_count"] += row["live"] + row["archived"]
    except (OSError, ValueError) as exc:
        result["complete"] = False
        result["errors"].append(str(exc))
    return result
