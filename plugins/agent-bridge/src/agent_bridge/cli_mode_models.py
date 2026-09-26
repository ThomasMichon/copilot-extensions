"""CLI-mode Session Host reservation + remote-venue descriptor models.

Split out of ``models.py`` (module-size guard); re-exported from there, so
``from agent_bridge.models import LiveSessionVenue`` keeps working.
"""

from __future__ import annotations

from pydantic import BaseModel


class LiveSessionVenue(BaseModel):
    """Where a remote-venue CLI-mode session lives and how to reattach to it.

    Absent (``None``) for the ordinary local case. Populated by a venue's own
    CLI-mode launch verb (agent-bridge-cli-mode-sessions Phase 4) -- never
    inferred or guessed by the bridge itself.
    """

    #: Venue provider boundary ("codespace", "container", "ssh", or another
    #: provider-owned kind). Local sessions carry no venue at all rather than
    #: a "local" kind here.
    kind: str
    #: The venue's own name/identifier (CodeSpace name, container name).
    target: str
    #: The multiplexer session name to attach to on that venue
    #: (``embody``'s own ``wt-<worktree_id>`` convention).
    mux_session_name: str
    #: The worktree that launched and supervises this session, as a qualified
    #: ``machine/project/worktree_id`` ref (no ``#session``), so a successor
    #: session in that worktree -- e.g. after a context handoff -- can find the
    #: workers it supervises. Optional; set by the venue's launch verb.
    supervisor_ref: str | None = None


class CreateCliModeReservationRequest(BaseModel):
    """Request to reserve a worktree for an upcoming CLI-mode session.

    Created by the operator (or a control surface acting on their explicit
    request) *before* the muxed, interactive CLI process starts -- never
    ambiently. See ``visions/remote-interactive-sessions``
    §opt-in-not-ambient-default.
    """

    worktree_id: str
    ttl_seconds: float = 300.0
    venue: LiveSessionVenue | None = None  # inherited by the claiming live session


class CliModeReservationInfo(BaseModel):
    """Public view of a CLI-mode Session Host reservation."""

    worktree_id: str
    reservation_id: str
    created_at: float
    expires_at: float
    #: The live session that claimed this reservation, once one has -- None
    #: while still awaiting its CLI process (§allocate-before-launch).
    claimed_by_session_id: str | None = None
    venue: LiveSessionVenue | None = None
