"""Complete, read-only repository inventories through the selected provider."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Protocol
from urllib.parse import quote, urlsplit

from .base import ProviderError, run_cli
from .observation_credentials import github_endpoint


class GitHubInventoryOwner(Protocol):
    def authority_endpoint(self, api_base: str = "") -> str: ...

    def _env(self, token: str | None, *, host: str = "") -> dict[str, str]: ...


def github_open_pulls(
    owner: GitHubInventoryOwner, repo: str, *, api_base: str = "",
    token: str | None = None, expected_login: str | None = None,
) -> tuple[dict, ...]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(
        part in (".", "..") for part in repo.split("/")
    ):
        raise ProviderError("Open-PR inventory requires an exact owner/repository")
    authority = owner.authority_endpoint(api_base)
    host, endpoint = github_endpoint(authority)
    expected_address = urlsplit("https://" + authority)
    env = owner._env(token, host=host)
    if token:
        env.update({name: token for name in (
            "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        )})
    if expected_login:
        identity = run_cli(["gh", "api", "--hostname", host, endpoint + "/user", "--jq", ".login"], env=env)
        if identity.returncode or identity.stdout.strip().casefold() != expected_login.casefold():
            raise ProviderError("Repository-scoped GitHub identity could not be verified")
    records: dict[int, dict] = {}
    projection = (
        "[.[] | {number,url:.html_url,state,title,created_at,"
        "author:.user.login,head_sha:.head.sha,base_repo:.base.repo.full_name}]"
    )
    response = run_cli([
        "gh", "api", "--hostname", host,
        f"{endpoint}/repos/{quote(repo, safe='/')}/pulls?state=open&per_page=100",
        "--paginate", "--jq", projection,
    ], env=env)
    if response.returncode:
        raise ProviderError(f"Open-PR inventory did not complete: {response.stderr.strip()}")
    decoder = json.JSONDecoder()
    remaining = response.stdout.lstrip("\ufeff").strip()
    if not remaining:
        raise ProviderError("Open-PR inventory returned no page")
    for page in range(1, 10001):
        try:
            rows, end = decoder.raw_decode(remaining)
        except (ValueError, TypeError) as error:
            raise ProviderError(f"Open-PR inventory page {page} is not JSON") from error
        if not isinstance(rows, list) or len(rows) > 100:
            raise ProviderError(f"Open-PR inventory page {page} is not a bounded array")
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderError(f"Open-PR inventory page {page} contains a non-object")
            number = row.get("number")
            required = ("url", "created_at", "base_repo")
            optional = ("title", "author", "head_sha")
            if (
                type(number) is not int or number <= 0
                or row.get("state") != "open"
                or any(not isinstance(row.get(key), str) or not row[key] for key in required)
                or any(key not in row or row[key] is not None and (
                    not isinstance(row[key], str) or not row[key]
                ) for key in optional)
                or row["base_repo"].lower() != repo.lower()
            ):
                raise ProviderError(f"Open-PR inventory page {page} contains invalid PR data")
            try:
                created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                address = urlsplit(row["url"])
                same_authority = (
                    address.hostname == expected_address.hostname
                    and (address.port or 443) == (expected_address.port or 443)
                )
            except ValueError as error:
                raise ProviderError("Open-PR inventory has an invalid address or creation timestamp") from error
            if (
                created.tzinfo is None or address.scheme != "https"
                or not same_authority
                or address.username is not None or address.password is not None
                or address.query or address.fragment
                or address.path.rstrip("/").lower() != f"/{repo}/pull/{number}".lower()
            ):
                raise ProviderError("Open-PR inventory has an invalid PR address or timestamp")
            record = {key: value for key, value in row.items() if key not in ("state", "base_repo")}
            record["metadata_unknown"] = [key for key in optional if row[key] is None]
            if number in records:
                raise ProviderError("Open-PR pagination repeated a PR; inventory is incomplete")
            records[number] = record
        remaining = remaining[end:].strip()
        if not remaining:
            return tuple(records.values())
    raise ProviderError("Open-PR inventory exceeded its page bound; inventory is incomplete")
