"""Unified diff acquisition through the existing Gitea curl owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import ProviderError
from ..pr_contract import PRDiff

if TYPE_CHECKING:
    from .gitea import GiteaProvider


def get_diff(
    provider: GiteaProvider, repo: str, number: int, *, api_base: str = "",
    token: str | None = None, preserve_line_endings: bool = False,
) -> PRDiff:
    """Keep legacy text capture unless the explicit reader selects exact text."""
    if not token:
        return PRDiff(supported=False, error="Gitea provider needs a token to read a PR diff.")
    try:
        status, body = provider._curl(
            "GET", provider._api(api_base, f"/repos/{repo}/pulls/{number}.diff"), token,
            preserve_line_endings=preserve_line_endings,
        )
    except ProviderError as exc:
        return PRDiff(supported=True, error=str(exc))
    if status != 200:
        return PRDiff(supported=True, error=f"gitea diff GET returned HTTP {status}")
    return PRDiff(diff=body)
