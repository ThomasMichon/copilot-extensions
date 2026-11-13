"""Finite repository inventory through the existing read-only provider owners."""

from __future__ import annotations

import json
import re
import time
from urllib.parse import quote

from .base import ProviderError, run_cli
from .observation_credentials import CredentialSource, ObservationCredentials, github_endpoint

PROTOCOL = "agent-worktrees.pr-inventory/v1"
MAX_PAGES = 20
PAGE_SIZE = 50
MAX_BYTES = 1_048_576


def inventory(
    provider, repo: str, *, api_base: str, credential_source: CredentialSource,
    max_pages: int = 4, max_pulls: int = 200,
    deadline: float | None = None,
) -> dict:
    """Keep accepted pages when a later read fails; only empty pages prove completion."""
    if (
        provider.name not in ("github", "gitea")
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
        or any(part in (".", "..") for part in repo.split("/"))
        or type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES
        or type(max_pulls) is not int or not 1 <= max_pulls <= 1000
    ):
        raise ValueError("invalid bounded observation inventory selection")
    selected_base = api_base or ("https://github.com" if provider.name == "github" else "")
    endpoint = provider.authority_endpoint(selected_base)
    if provider.name == "github":
        host, endpoint = github_endpoint(endpoint)
    credentials = ObservationCredentials(
        credential_source, provider=provider.name, api_base=endpoint, repository=repo,
    )
    numbers: list[int] = []
    deadline = deadline if deadline is not None else time.monotonic() + 60
    pages = 0
    outcome, error = "partial", "page_limit"
    for page in range(1, max_pages + 1):
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError("inventory deadline exhausted")
            path = f"/repos/{quote(repo, safe='/')}/pulls?state=open&page={page}"
            if provider.name == "github" and credentials.http_profile == "direct-v1":
                from .observation_http import read_get
                status, body = read_get(
                    endpoint + path + f"&per_page={PAGE_SIZE}", credentials.authorization(),
                    invoke=run_cli, deadline=deadline, github=True,
                )
                if status != 200:
                    raise ProviderError("inventory read failed")
            elif provider.name == "github":
                material = credentials.acquire()
                if time.monotonic() >= deadline:
                    raise ValueError("inventory deadline exhausted")
                env = provider._env(material.token, host=host)
                env.update({key: material.token for key in (
                    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                )})
                response = run_cli([
                    "gh", "api", "--hostname", host, endpoint + path + f"&per_page={PAGE_SIZE}",
                ], env=env, timeout=max(1, int(deadline - time.monotonic())),
                   max_stdout_bytes=MAX_BYTES, max_stderr_bytes=8192)
                if response.returncode:
                    raise ProviderError("inventory read failed")
                body = response.stdout
            else:
                status, body = provider._curl(
                    "GET", provider._api(endpoint, path + f"&limit={PAGE_SIZE}"), credentials,
                    read_deadline=deadline,
                )
                if status != 200:
                    raise ProviderError("inventory read failed")
            if len(body.encode("utf-8")) > MAX_BYTES:
                raise ValueError("inventory page exceeds byte bound")
            rows = json.loads(body)
            if not isinstance(rows, list) or len(rows) > PAGE_SIZE or any(
                not isinstance(row, dict) or type(row.get("number")) is not int
                or row["number"] < 1 or row.get("state") != "open" for row in rows
            ):
                raise ValueError("invalid inventory page")
            batch = [row["number"] for row in rows]
            if len(set(batch)) != len(batch) or set(batch).intersection(numbers):
                raise ValueError("repeated inventory member")
            pages += 1
            if not rows:
                outcome, error = "complete", ""
                break
            capacity = max_pulls - len(numbers)
            numbers.extend(batch[:capacity])
            if len(batch) > capacity or len(numbers) == max_pulls:
                error = "pull_limit"
                break
        except (ProviderError, OSError, ValueError, UnicodeError):
            outcome, error = ("partial" if pages else "unavailable"), "read_unavailable"
            break
    return {
        "schema": "agent-worktrees.pr-inventory", "version": 1,
        "provider": provider.name, "api_base": selected_base.rstrip("/"), "repo": repo,
        "outcome": outcome, "numbers": numbers, "pages": pages, "error": error,
        "max_pages": max_pages, "max_pulls": max_pulls,
    }
