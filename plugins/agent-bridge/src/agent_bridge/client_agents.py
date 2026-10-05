"""Query-param helper for ``BridgeClient.list_agents_with_incomplete``'s
``force_refresh``/``require_complete`` capability (pivot-streaming-transport
Phase 3b, the agent-bridge daemon-side agent-roster cache).

Extracted out of ``client.py`` (at its grandfathered module-size ceiling)
into its own module, mirroring this plugin's own precedent for landing new
capability in a fresh same-package module rather than compacting prose to
fit a shrinking ceiling (see ``client_worktree_restart.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol


class _SupportsDaemonProtocol(Protocol):
    def daemon_supports(self, min_version: int) -> bool: ...


if TYPE_CHECKING:
    _Client = _SupportsDaemonProtocol
else:
    _Client = object


def agent_roster_params(
    client: _Client, force_refresh: bool, require_complete: bool,
) -> dict[str, str]:
    """Build the ``force_refresh``/``require_complete`` query params for
    ``GET /api/v1/agents``, gated on ``AGENT_ROSTER_CACHE_PROTOCOL_VERSION``
    -- an old daemon is never sent either param, which it would otherwise
    silently ignore while still returning its own old-shape response."""
    if not (force_refresh or require_complete):
        return {}
    from .protocol import AGENT_ROSTER_CACHE_PROTOCOL_VERSION

    if not client.daemon_supports(AGENT_ROSTER_CACHE_PROTOCOL_VERSION):
        return {}
    return {
        key: "true"
        for key, value in (
            ("force_refresh", force_refresh),
            ("require_complete", require_complete),
        )
        if value
    }
