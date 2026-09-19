"""Agent-dispatch HTTP client -- task + attachment-history lookup.

*resolve-by-any-origin-reference* (``visions/plugins/agent-bridge``): the
one HTTP surface :mod:`agent_bridge.routes.dispatch_tasks` needs against the
local **agent-dispatch coordinator** (a distinct loopback service, default
``http://127.0.0.1:9847``, config surfaced by ``agent_dispatch_url``/
``agent_dispatch_token`` in :mod:`agent_bridge.config` -- mirroring
``neuron-forge``'s own ``server/core/agent_dispatch_client.py`` client for the
identical optional dependency, so the two consumers agree on the same shape).

Deliberately small: fetch one task, fetch its durable attachment history
(``agent-dispatch-session-worktree-history`` Phase 1). Both raise on a
genuine transport/HTTP error (the caller decides how to degrade -- this
route treats an unreachable coordinator as "cannot resolve", never as
"resolved to nothing"), except a 404 task fetch, which is a legitimate
"task does not exist" answer, not an error.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_TIMEOUT = 5.0


def _headers(token: str) -> dict[str, str]:
    """Bearer header when a token is configured; the loopback coordinator
    commonly runs unauthenticated, in which case no header is sent."""
    return {"Authorization": f"Bearer {token}"} if token else {}


def _base(url: str) -> str:
    return url.rstrip("/")


async def fetch_task(
    url: str, token: str, task_id: str, *, timeout: float = DEFAULT_TIMEOUT
) -> dict[str, Any] | None:
    """Fetch one dispatch task, preserving 404 as an absent task (``None``)."""
    task_url = f"{_base(url)}/tasks/{task_id}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(task_url, headers=_headers(token))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else None


async def fetch_attachments(
    url: str, token: str, task_id: str, *, timeout: float = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Fetch a task's durable attachment history, newest first.

    Returns an empty list for a task with no recorded attachments (never an
    error) -- the caller (``candidate_session_ids``) already degrades
    gracefully to "try what we can" on an empty/malformed history.
    """
    attachments_url = f"{_base(url)}/tasks/{task_id}/attachments"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(attachments_url, headers=_headers(token))
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]
