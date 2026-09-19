"""Rank candidate session IDs for a delegated (agent-dispatch) task.

*resolve-by-any-origin-reference* (``visions/plugins/agent-bridge``): a
caller with only a dispatch-task reference -- not a session ID directly --
should resolve to the same session the bridge would give a caller who
already had the ID, through the same any-session-any-registered-worktree
primitive. This module is the pure, dependency-free half of that: given a
task's JSON (as returned by agent-dispatch's ``GET /tasks/{id}``) and its
durable attachment history (``GET /tasks/{id}/attachments``, newest first --
see the ``agent-dispatch-session-worktree-history`` effort's Phase 1), it
ranks which session ID to try first, second, and so on. The caller (a route
handler) does the actual resolution per candidate through the existing
live-then-cold-store primitive (``SessionManager.get_session`` /
``fetch_cold_store_session``), stopping at the first one that answers.

Kept deliberately free of any I/O, HTTP client, or SessionManager import so
it can be unit-tested as pure data-in/data-out ranking logic.
"""

from __future__ import annotations

from typing import Any


def candidate_session_ids(
    task: dict[str, Any], attachments: list[dict[str, Any]]
) -> list[str]:
    """Order the session IDs worth trying for this dispatch task.

    Precedence:
      1. The task's *current* owner session (``owner_session_id``), if set --
         this is the freshest, most likely-live candidate.
      2. Every attachment history entry, in the order given (the API already
         returns newest-first), skipping the current owner session if it
         already appeared there (avoids a duplicate resolve attempt).

    A detached entry is still tried -- its worktree may be gone, but a
    cold-store provider can still answer for that *exact* session
    individually (this is precisely what makes the ranking useful: a task
    resumed under a new owner session still resolves callers asking about
    its *prior* work, not only its current one).

    Never raises on malformed input: a missing/non-string field is treated
    as absent rather than an error, so a partial or unexpected task/
    attachment shape degrades to "try what we can" instead of failing the
    whole resolution.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(candidate: object) -> None:
        if isinstance(candidate, str) and candidate and candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)

    _add(task.get("owner_session_id"))
    for entry in attachments:
        if isinstance(entry, dict):
            _add(entry.get("session_id"))
    return ordered


def task_worktree_id(task: dict[str, Any]) -> str | None:
    """The worktree a dispatch task targets, if any -- ``target_worktree``
    on the task's own record. A resolver that has no resolvable session for
    any candidate (see :func:`candidate_session_ids`) can still fall back to
    "the worktree's latest session" using this, mirroring
    ``neuron-forge``'s own ``task_worktree()`` helper (the same field, same
    convention, kept independent here rather than importing across plugins).
    """
    value = task.get("target_worktree")
    return value if isinstance(value, str) and value else None
