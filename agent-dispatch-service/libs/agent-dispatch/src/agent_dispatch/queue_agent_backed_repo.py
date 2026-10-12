"""``TaskQueue`` mixin: the opt-in "agent-backed repo" enforcement check.

Split out of :mod:`agent_dispatch.queue_storage` (module-size cap) -- see
:mod:`agent_dispatch.registrar_discovery` for the policy switch and the
canonical-lane-alias registry this enforces against.
"""

from __future__ import annotations

from . import registrar_discovery
from .queue_records import TaskError


class AgentBackedRepoMixin:
    """Refuses task creation against an unregistered repo lane, opt-in."""

    @staticmethod
    def _require_agent_backed_repo(canonical_repo: str) -> None:
        """Refuse a task whose lane matches no registered ``repo`` pointer
        alias -- see :func:`agent_dispatch.registrar_discovery.
        agent_backed_enforcement_enabled` for the policy this enforces.

        Matches ``canonical_repo`` *exactly* against the registrar's known
        canonical lane aliases (:func:`agent_dispatch.registrar_discovery.
        known_lane_aliases`) -- never against a pointer's free-form ``name``
        or a basename-only comparison, either of which would let an
        unrelated repo that merely shares a trailing path segment pass.
        """
        if not registrar_discovery.agent_backed_enforcement_enabled():
            return
        if canonical_repo in registrar_discovery.known_lane_aliases():
            return
        raise TaskError(
            f"repo lane {canonical_repo!r} is not a registered agent-backed repo "
            "(it matches no registrar 'repo' pointer's canonical lane alias, so "
            "nothing locally watches it). Register one first with "
            f"'agent-dispatch registrar add-pointer <name> <path> --kind repo "
            f"--alias {canonical_repo}'."
        )
