"""Authoritative same-repository, all-state head absence for local cancellation."""

from __future__ import annotations

import json

from .base import ProviderError, run_cli


def confirm(provider, repo: str, head: str, *, api_base: str = "", token=None) -> bool:
    """Return true only for an explicitly empty successful all-state response."""
    owner, separator, name = repo.partition("/")
    if not owner or not separator or not name or "/" in name or not head:
        raise ProviderError("An exact repository and head are required to confirm absence.")
    host = provider.authority_endpoint(api_base)
    result = run_cli(
        [
            "gh", "api", "--hostname", host, f"repos/{repo}/pulls", "--method", "GET",
            "-f", "state=all", "-f", f"head={owner}:{head}", "-f", "per_page=1",
        ],
        env=provider._env(token, host=host),
    )
    if result.returncode:
        raise ProviderError(f"Cannot confirm PR-head absence: {result.stderr.strip()}")
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProviderError("PR-head absence response is not JSON.") from exc
    if not isinstance(rows, list):
        raise ProviderError("PR-head absence response is not a list.")
    return rows == []
