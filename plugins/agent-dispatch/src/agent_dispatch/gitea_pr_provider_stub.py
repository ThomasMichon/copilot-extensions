"""Structural stub for the reviewer-side Gitea PR adapter.

This mirrors the backlog-side ``GiteaProvider`` precedent: the provider slot
exists and can be selected deliberately, but every operation fails loud with
the tracked reviewer-surface follow-up instead of pretending to work.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from .github_provider_adapter import PRObservation

#: The reviewer-side Gitea work remains explicitly tracked as open in the
#: effort that owns Phase 2's split completion state.
GITEA_REVIEWER_PROVIDER_TRACKING_REFERENCE = (
    "efforts/active/agent-dispatch-recipe-library/README.md"
    "#phase-2--azure-devops--gitea-provider-adapters"
)


class GiteaPRAdapter:
    """Structural stub for the reviewer-side Gitea PR adapter."""

    def __init__(self, expected_login: str, runner: Callable[..., Any] = subprocess.run):
        if not expected_login:
            raise ValueError("expected_login must be non-empty")
        self.expected_login = expected_login
        self.runner = runner

    def _not_implemented(self, operation: str) -> RuntimeError:
        return NotImplementedError(
            f"GiteaPRAdapter.{operation} is not yet implemented -- see "
            f"{GITEA_REVIEWER_PROVIDER_TRACKING_REFERENCE}"
        )

    def fetch_pr(self, repo: str, number: int) -> dict[str, Any]:
        raise self._not_implemented("fetch_pr")

    def observe(self, repo: str, number: int) -> PRObservation:
        raise self._not_implemented("observe")
