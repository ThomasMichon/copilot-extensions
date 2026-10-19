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
    project: str | None = None,
) -> dict[str, str]:
    """Build the ``force_refresh``/``require_complete`` query params for
    ``GET /api/v1/agents``, gated on ``AGENT_ROSTER_CACHE_PROTOCOL_VERSION``
    -- an old daemon is never sent either param, which it would otherwise
    silently ignore while still returning its own old-shape response."""
    from .protocol import REMOTE_ACP_TARGETS_PROTOCOL_VERSION

    params = (
        {"project": project}
        if project is not None and client.daemon_supports(REMOTE_ACP_TARGETS_PROTOCOL_VERSION) else {}
    )
    if not (force_refresh or require_complete):
        return params
    from .protocol import AGENT_ROSTER_CACHE_PROTOCOL_VERSION

    if not client.daemon_supports(AGENT_ROSTER_CACHE_PROTOCOL_VERSION):
        return params
    params.update({
        key: "true"
        for key, value in (
            ("force_refresh", force_refresh),
            ("require_complete", require_complete),
        )
        if value
    })
    return params


def agent_detail_path(client: _Client, name: str, include_unaddressable: bool, project: str | None) -> str:
    from urllib.parse import urlencode
    from .protocol import REMOTE_ACP_TARGETS_PROTOCOL_VERSION

    params = {"include_unaddressable": "true"} if include_unaddressable else {}
    if project is not None and client.daemon_supports(REMOTE_ACP_TARGETS_PROTOCOL_VERSION):
        params["project"] = project
    return f"/api/v1/agents/{name}" + ("?" + urlencode(params) if params else "")
