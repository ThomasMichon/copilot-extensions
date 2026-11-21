"""Bounded exact-head observations; never infer empty from a failed lookup."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from .base import ProviderError, run_cli
from .observation_credentials import github_endpoint


def confirm(
    provider: Any, repo: str, head: str, *, number: int | None = None,
    head_sha: str = "", api_base: str = "", token: str | None = None,
) -> bool:
    owner, separator, name = repo.partition("/")
    if not owner or not separator or not name or "/" in name or not head:
        raise ProviderError("An exact repository and head are required.")
    host, endpoint = github_endpoint(provider.authority_endpoint(api_base))
    env = provider._env(token, host=host)
    if token is not None:
        env.update({key: token for key in (
            "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        )})
    result = run_cli(
        [
            "gh", "api", "--hostname", host,
            f"{endpoint}/repos/{quote(owner, safe='')}/{quote(name, safe='')}/pulls", "--method", "GET",
            "-f", "state=all", "-f", f"head={owner}:{head}", "-f", "per_page=2",
        ],
        env=env,
    )
    if result.returncode:
        raise ProviderError(f"Cannot observe publication head: {result.stderr.strip()}")
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProviderError("Publication-head response is not JSON.") from exc
    if not isinstance(rows, list):
        raise ProviderError("Publication-head response is not a list.")
    if number is None:
        return rows == []
    # Two results already establish ambiguity. A single row is complete because
    # this page requested two; no best-effort first-match or unbounded pagination.
    if len(rows) != 1 or not isinstance(rows[0], dict) or not head_sha:
        return False
    row = rows[0]
    source, base = row.get("head"), row.get("base")
    if not isinstance(source, dict) or not isinstance(base, dict):
        return False
    source_repo, base_repo = source.get("repo"), base.get("repo")
    source_name = source_repo.get("full_name") if isinstance(source_repo, dict) else None
    base_name = base_repo.get("full_name") if isinstance(base_repo, dict) else None
    return bool(
        type(row.get("number")) is int and row["number"] == number
        and row.get("state") == "closed"
        and isinstance(row.get("merged_at"), str) and row["merged_at"].strip()
        and source.get("ref") == head and source.get("sha") == head_sha
        and isinstance(source_name, str) and source_name.casefold() == repo.casefold()
        and isinstance(base_name, str) and base_name.casefold() == repo.casefold()
    )
