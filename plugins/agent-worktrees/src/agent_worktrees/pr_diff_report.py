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
        result = provider.get_diff(
            repo, number, api_base=api_base, token=token, preserve_line_endings=True,
        )
        supported, text, error = result.supported, result.diff, result.error
        if type(supported) is not bool or not isinstance(text, str) or not isinstance(error, str):
            raise ValueError("provider returned an invalid diff result")
    except Exception as exc:
        return {**report, "error": f"provider diff acquisition failed ({type(exc).__name__})"}
    return {
        **report, "supported": supported, "diff": text, "error": error,
        "complete": False if error or not supported else None,
    }
