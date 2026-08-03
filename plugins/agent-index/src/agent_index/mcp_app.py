"""Discoverable MCP toolset over the agent-index HTTP API.

Exposes agent-index's query surface — ``agent_index_search``,
``agent_index_find_similar``, ``agent_index_clusters``, ``agent_index_status``,
``agent_index_reindex`` — as MCP tools so agents find semantic retrieval
directly, without knowing the HTTP shape.

**Toolset vs transport.** The tools are a *transport-agnostic* HTTP client over a
**configurable endpoint**: resolved from ``AGENT_INDEX_ENDPOINT`` if set, else
local endpoint discovery (``config.client_url``). Each consumer wires its own
transport to the backing service — direct local HTTP on-box, an SSH-forwarded
port to a remote host, or a gateway URL — by pointing ``AGENT_INDEX_ENDPOINT`` at
the reachable address. The toolset itself is identical everywhere.

Run as ``agent-index mcp`` (stdio) so an agent-mcp bridge can spawn it with the
right per-machine endpoint in its environment.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from agent_index.config import ENDPOINT_ENV, client_url
from agent_index.query_surface import format_clusters, format_hits

_TIMEOUT = 30.0

mcp = FastMCP(
    "agent-index",
    instructions=(
        "Semantic + lexical search over a harness repo's code, docs, commits, "
        "issues, and pull requests. Use agent_index_search to find content by "
        "meaning, agent_index_find_similar to pivot from a result into 'more like "
        "this', agent_index_clusters to list near-duplicate items, and "
        "agent_index_status for index health."
    ),
)


def _endpoint() -> str:
    """Resolve the agent-index HTTP base URL: explicit override, else local
    discovery. The override is how a consumer points the toolset at a remote
    service (SSH-forwarded port, gateway URL, ...)."""
    endpoint = os.environ.get(ENDPOINT_ENV) or client_url()
    if not endpoint:
        raise RuntimeError(
            f"agent-index endpoint not found; set {ENDPOINT_ENV} to the service URL "
            "(local, SSH-forwarded, or gateway) or start the local service."
        )
    return endpoint.rstrip("/")


async def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{_endpoint()}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{_endpoint()}{path}", json=body)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def agent_index_search(
    query: str,
    limit: int = 10,
    source: str | None = None,
    language: str | None = None,
    repo: str | None = None,
) -> str:
    """Search the index semantically (meaning + lexical hybrid).

    Args:
        query: Natural-language or code query.
        limit: Max results (default 10).
        source: Filter by source name (e.g. "git:my-repo").
        language: Filter by language (e.g. "python", "markdown").
        repo: Filter by repository metadata.
    """
    params: dict[str, Any] = {"q": query, "limit": limit}
    if source:
        params["source"] = source
    if language:
        params["language"] = language
    if repo:
        params["repo"] = repo
    data = await _get("/search", params)
    if not data.get("available", True):
        return f"agent-index search unavailable: {data.get('error', 'unknown error')}"
    hits = data.get("hits", [])
    if not hits:
        return f"No results for: {query}"
    return format_hits(hits, f"Found {len(hits)} result(s) for: {query}", show_ids=True)


@mcp.tool()
async def agent_index_find_similar(
    chunk_id: str,
    limit: int = 10,
    source: str | None = None,
) -> str:
    """Find items similar to an already-indexed chunk (the "more like this" pivot).

    Args:
        chunk_id: Reference chunk id (from an agent_index_search result).
        limit: Max neighbours (default 10).
        source: Filter neighbours by source name.
    """
    params: dict[str, Any] = {"id": chunk_id, "limit": limit}
    if source:
        params["source"] = source
    data = await _get("/similar", params)
    if not data.get("available", True):
        return f"agent-index find-similar unavailable: {data.get('error', 'unknown error')}"
    hits = data.get("hits", [])
    if not hits:
        return f"No similar items for chunk {chunk_id}"
    return format_hits(
        hits, f"Found {len(hits)} similar item(s) for chunk {chunk_id}", show_ids=True
    )


@mcp.tool()
async def agent_index_clusters(
    source: str | None = None,
    bucket: str | None = None,
    model: str | None = None,
    exact_dupes_only: bool = False,
    limit: int = 20,
) -> str:
    """List clusters of near-duplicate indexed items.

    Answers "which items are basically the same?" — filed issues, docs, or
    quips that duplicate each other. Clusters are largest/tightest first.

    Args:
        source: Scope to a source (collapsed to its bucket, e.g.
            "git:my-repo" -> all that repo's files).
        bucket: Explicit bucket (e.g. "git", "gitea:issues").
        model: Embedding space ("code" or "prose").
        exact_dupes_only: Only clusters that contain a byte-identical pair.
        limit: Max clusters (default 20).
    """
    params: dict[str, Any] = {"limit": limit, "exact_dupes_only": exact_dupes_only}
    if source:
        params["source"] = source
    if bucket:
        params["bucket"] = bucket
    if model:
        params["model"] = model
    data = await _get("/clusters", params)
    if not data.get("available", True):
        return f"agent-index clusters unavailable: {data.get('error', 'unknown error')}"
    clusters = data.get("clusters", [])
    if not clusters:
        return "No clusters found for the given filters."
    return format_clusters(clusters, data.get("count", len(clusters)))


@mcp.tool()
async def agent_index_status() -> str:
    """Show index health — plugin/version, chunk counts, sources, indexing state."""
    data = await _get("/status", {})
    index = data.get("index", {}) or {}
    lines = [
        f"Plugin: {data.get('plugin', 'agent-index')} {data.get('version', '')}",
        f"Draining: {data.get('draining', False)}",
        f"Index available: {index.get('available', False)}",
        f"Total chunks: {index.get('chunks', 0)}",
    ]
    sources = index.get("sources") or {}
    if sources:
        lines.append("Sources:")
        for name, info in sources.items():
            count = info.get("chunk_count", info) if isinstance(info, dict) else info
            lines.append(f"  {name}: {count}")
    indexing = data.get("indexing")
    if indexing:
        lines.append(
            f"Indexing: running={indexing.get('running')} "
            f"paused={indexing.get('paused')} "
            f"active_task={indexing.get('active_task_id')}"
        )
    return "\n".join(lines)


@mcp.tool()
async def agent_index_reindex(full: bool = False, source: str | None = None) -> str:
    """Trigger a reindex on the service (runs in the background).

    Args:
        full: If true, reindex from scratch; otherwise incremental.
        source: Reindex only this source (default: all).
    """
    body: dict[str, Any] = {"full": full}
    if source:
        body["source"] = source
    data = await _post("/reindex", body)
    if not data.get("accepted", False):
        return f"Reindex not accepted: {data.get('error', 'unknown error')}"
    task = data.get("task", {})
    task_source = task.get("source", source or "all")
    return f"Reindex accepted (task {task.get('id', '?')}, source={task_source})."


def serve_stdio() -> None:
    """Run the MCP toolset over stdio (the agent-mcp spawn transport)."""
    mcp.run()
