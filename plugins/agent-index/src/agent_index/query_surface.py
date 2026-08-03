"""Shared JSON + text helpers for the agent-index query surface.

The ``*_to_dict`` helpers normalize engine result objects (or mappings) into the
public JSON shapes; the ``format_*`` helpers render those shapes into the plain
text the MCP tools return. Both agent-index's own tools and VEI's ``vei_*``
tool-shim import these so hit/cluster rendering lives in exactly one place.
"""

from __future__ import annotations

from typing import Any


def clip(text: str, limit: int = 500) -> str:
    """Truncate ``text`` to ``limit`` chars, appending an ellipsis when clipped."""
    return text if len(text) <= limit else text[:limit] + "..."


def format_hits(
    hits: list[dict[str, Any]], header: str, *, show_ids: bool = False
) -> str:
    """Render search/find-similar hits as plain text.

    ``show_ids`` appends ``id=`` / ``src=`` fields (agent-index's richer form);
    VEI's tools leave it off to preserve their historical output byte-for-byte.
    The caller supplies ``header`` (e.g. "Found N results for: ..."), which is
    followed by a blank line before the hit list.
    """
    lines = [header, ""]
    for i, hit in enumerate(hits, 1):
        loc = ""
        if hit.get("line_start") is not None:
            loc = f" (L{hit.get('line_start')}-{hit.get('line_end')})"
        suffix = ""
        if show_ids:
            suffix = (
                f"  id={hit.get('chunk_id') or hit.get('id', '')}  "
                f"src={hit.get('source', '')}"
            )
        lines.append(
            f"[{i}] {hit.get('file_path', '')}{loc} "
            f"[{hit.get('language', '')}/{hit.get('chunk_type', '')}] "
            f"score={float(hit.get('score', 0.0)):.3f}{suffix}"
        )
        lines.append(clip(hit.get("content", "")))
        lines.append("")
    return "\n".join(lines)


def format_clusters(clusters: list[dict[str, Any]], count: int) -> str:
    """Render near-duplicate clusters as plain text (largest/tightest first)."""
    lines = [f"Found {count} cluster(s)", ""]
    for i, cluster in enumerate(clusters, 1):
        rep = cluster.get("representative") or {}
        dupe = " [has exact dupes]" if cluster.get("has_exact_dupes") else ""
        lines.append(
            f"[{i}] {cluster.get('bucket', '')} / {cluster.get('model_id', '')} -- "
            f"{cluster.get('size', 0)} items, "
            f"avg={float(cluster.get('avg_score', 0.0)):.3f}{dupe}"
        )
        lines.append(f"    rep: {rep.get('source', '')} :: {rep.get('file_path', '')}")
        for member in cluster.get("members", []):
            tag = " (exact)" if member.get("is_exact_dupe") else ""
            lines.append(
                f"      - {member.get('source', '')} :: {member.get('file_path', '')} "
                f"(score={float(member.get('score', 0.0)):.3f}){tag}"
            )
        lines.append("")
    return "\n".join(lines)


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
