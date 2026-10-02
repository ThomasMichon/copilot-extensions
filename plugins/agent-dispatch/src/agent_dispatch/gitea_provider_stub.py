"""Structural stub for a Gitea backlog-provider adapter.

Split out of ``repository_issue_loops.py`` (which implements the shipped
GitHub and Azure DevOps adapters) purely to stay under this repo's
module-size cap -- :class:`GiteaProvider` has no behavior of its own yet, so
it does not belong alongside the real adapters' substantial implementations.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .repository_issue_loops import Issue

#: Tracked follow-up for the real Gitea backlog-provider adapter. Update this
#: if the tracking issue moves.
GITEA_PROVIDER_TRACKING_ISSUE = "ThomasMichon/copilot-extensions#4825"


class GiteaProvider:
    """Structural stub for a Gitea backlog-provider adapter.

    ``validate_config`` deliberately still **rejects**
    ``forge.provider: gitea`` (see ``repository_issue_loops._SUPPORTED_FORGE_PROVIDERS``)
    -- this class only scaffolds direct provider selection
    (``_forge_provider_for`` can construct it), not a usable declaration.
    Every operation is genuinely unimplemented: no integration approach
    (Gitea's REST API vs. its official ``tea`` CLI, by analogy with
    ``repository_issue_loops``'s ``gh``/``az`` adapters) has been decided,
    and no live Gitea instance was available to validate an implementation
    against -- see :data:`GITEA_PROVIDER_TRACKING_ISSUE`. Do not implement
    this ad-hoc without that decision and a real instance to validate
    against; that would repeat exactly the class of mistake this repo's own
    "validate beyond unit tests before landing a fix" policy exists to
    prevent.
    """

    def __init__(self, expected_login: str, runner: Callable[..., Any] = subprocess.run):
        if not expected_login:
            raise ValueError("expected_login must be non-empty")
        self.expected_login = expected_login
        self.runner = runner

    def _not_implemented(self, operation: str) -> RuntimeError:
        return NotImplementedError(
            f"GiteaProvider.{operation} is not yet implemented -- see "
            f"{GITEA_PROVIDER_TRACKING_ISSUE} (integration approach and a live "
            "Gitea instance to validate against are both still needed)"
        )

    def list_open_issues(self, repo: str) -> list["Issue"]:
        raise self._not_implemented("list_open_issues")

    def reserve(
        self, repo: str, issue: "Issue", reservation: dict[str, Any]
    ) -> None:
        raise self._not_implemented("reserve")

    def claim(
        self, repo: str, issue: "Issue", reservation: dict[str, Any], task_id: str
    ) -> None:
        raise self._not_implemented("claim")

    def release(
        self,
        repo: str,
        issue: "Issue",
        reservation: dict[str, Any],
        reason: str,
    ) -> None:
        raise self._not_implemented("release")
