"""``BridgeClient.restart_worktree`` -- a thin wrapper over ``POST
/api/v1/worktrees/{id}/restart``.

Extracted out of ``client.py`` (at its grandfathered module-size ceiling)
into its own mixin, mirroring this plugin's own precedent for landing new
capability in a fresh same-package module rather than compacting prose to
fit a shrinking ceiling.

This is the missing half of the reclaim sequence (agent-bridge-cold-resume
Phase 3, #6744): stopping a worktree's interactive CLI via
``agent-worktrees restart`` directly (not through this route) never expires
its live-session registration, so the terminated CLI's stale row can keep
the atomic ownership guard (#2879) refusing a later ``resume``/``reclaim``
until its heartbeat naturally lapses. This route already does that
invalidation server-side (see ``routes/worktrees.py``'s own ``#2906``
comment) -- callers that need to stop-then-force-resume a worktree (e.g.
``agent_dispatch.bridge_reclaim``) must go through here, not straight to
``agent-worktrees``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol


class _SupportsRequest(Protocol):
    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = ...,
        *,
        params: dict[str, Any] | list[tuple[str, Any]] | None = ...,
        request_timeout: float | None = ...,
    ) -> dict[str, Any] | None: ...


if TYPE_CHECKING:
    _Base = _SupportsRequest
else:
    _Base = object


class WorktreeRestartMixin(_Base):
    """Mixin supplying ``BridgeClient.restart_worktree``."""

    def restart_worktree(
        self,
        worktree_id: str,
        *,
        force: bool = False,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST /api/v1/worktrees/{id}/restart -- stop the worktree's
        interactive (mux-launched) Copilot in place (graceful, then a hard
        mux kill-session; ``force=true`` skips the graceful quit), keeping
        the worktree on disk. On success this also **expires any
        live-session registration** for it (#2906) -- see the module
        docstring. Returns ``{worktree_id, agent_name, had_session, method,
        ok}`` (``method``: none | graceful | hard | failed).
        """
        params = {"force": "true"} if force else None
        return (
            self._request(
                "POST",
                f"/api/v1/worktrees/{worktree_id}/restart",
                params=params,
                request_timeout=request_timeout,
            )
            or {}
        )
