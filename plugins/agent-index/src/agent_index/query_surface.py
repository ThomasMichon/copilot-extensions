"""Shared JSON helpers for the agent-index query surface."""

from __future__ import annotations

from typing import Any


def format_error(exc: BaseException) -> str:
    """Return a short, traceback-free error string for CLI/API responses."""
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def hit_to_dict(hit: Any) -> dict[str, Any]:
    """Convert a SearchResult-like object or mapping to the public hit shape."""
    chunk_id = _field(hit, "chunk_id", _field(hit, "id", ""))
    return {
        "id": chunk_id,
        "chunk_id": chunk_id,
        "score": _field(hit, "score", 0.0),
        "file_path": _field(hit, "file_path", ""),
        "line_start": _field(hit, "line_start", None),
        "line_end": _field(hit, "line_end", None),
        "source": _field(hit, "source", ""),
        "chunk_type": _field(hit, "chunk_type", ""),
        "language": _field(hit, "language", ""),
        "content": _field(hit, "content", ""),
    }


def _field(hit: Any, name: str, default: Any) -> Any:
    if isinstance(hit, dict):
        return hit.get(name, default)
    return getattr(hit, name, default)


def cluster_member_to_dict(member: Any) -> dict[str, Any]:
    """Convert a StoredMember-like object to the public member shape."""
    return {
        "source": _field(member, "source", ""),
        "file_path": _field(member, "file_path", ""),
        "score": _field(member, "score", 0.0),
        "is_exact_dupe": bool(_field(member, "is_exact_dupe", False)),
    }


def stored_cluster_to_dict(cluster: Any) -> dict[str, Any]:
    """Convert a StoredCluster-like object to the public cluster shape.

    Mirrors VEI's ``ClusterItem``: the representative is the member matching the
    stored rep source/path (falling back to the first member).
    """
    members = [cluster_member_to_dict(m) for m in _field(cluster, "members", ())]
    rep_source = _field(cluster, "rep_source", "")
    rep_file_path = _field(cluster, "rep_file_path", "")
    representative = next(
        (
            m
            for m in members
            if m["source"] == rep_source and m["file_path"] == rep_file_path
        ),
        members[0] if members else None,
    )
    return {
        "cluster_id": _field(cluster, "cluster_id", ""),
        "bucket": _field(cluster, "bucket", ""),
        "model_id": _field(cluster, "model_id", ""),
        "size": _field(cluster, "size", 0),
        "representative": representative,
        "avg_score": _field(cluster, "avg_score", 0.0),
        "has_exact_dupes": bool(_field(cluster, "has_exact_dupes", False)),
        "members": members,
    }
