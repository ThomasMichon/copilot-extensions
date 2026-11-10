"""Unified diff acquisition through the existing Gitea curl owner."""

from __future__ import annotations

from .base import ProviderError
from ..pr_contract import PRDiff


def get_diff(provider, repo, number, *, api_base="", token=None, preserve_line_endings=False):
    """Keep legacy text capture unless the explicit reader selects exact text."""
    if not token:
        return PRDiff(supported=False, error="Gitea provider needs a token to read a PR diff.")
    try:
        status, body = provider._curl(
            "GET", provider._api(api_base, f"/repos/{repo}/pulls/{number}.diff"), token,
            **({"preserve_line_endings": True} if preserve_line_endings else {}),
        )
    except ProviderError as exc:
        return PRDiff(supported=True, error=str(exc))
    if status != 200:
        return PRDiff(supported=True, error=f"gitea diff GET returned HTTP {status}")
    return PRDiff(diff=body)
