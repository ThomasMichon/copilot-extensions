"""Explicit diff acquisition stays separate from tracked PR reconciliation."""

from __future__ import annotations


def diff_report(provider, repo, number, *, api_base="", token=None):
    """Read through the existing provider without inventing candidate evidence."""
    report = {
        "schema": "agent-worktrees.pr-diff-read", "version": 1,
        "provider": provider.name, "repo": repo, "pr": number,
        "supported": False, "complete": False, "diff": "", "error": "",
    }
    try:
        result = provider.get_diff(repo, number, api_base=api_base, token=token)
    except Exception as exc:
        return {**report, "error": str(exc) or "provider diff acquisition failed"}
    return {
        **report, "supported": result.supported, "diff": result.diff, "error": result.error,
        "complete": False if result.error or not result.supported else None,
    }
