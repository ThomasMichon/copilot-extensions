"""Verify raw creation IDs and returned URLs before settling publication."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

from .base import ProviderError, PullResult


def number(data: Any, field: str) -> int:
    value = data.get(field) if isinstance(data, dict) else None
    if type(value) is not int or value <= 0:
        raise ProviderError("Provider creation did not return a positive integer pull-request identity.")
    return value


def validate(pull: PullResult, provider: str, repository: str, endpoint: str) -> None:
    if provider == "github":
        from .github import GitHubProvider
        from .github_pull_source import validate_url

        validate_url(pull.url, pull.number, repository, GitHubProvider().authority_endpoint(endpoint))
        return
    if type(pull.number) is not int or pull.number <= 0:
        raise ProviderError("Provider creation did not return a positive integer pull-request identity.")
    try:
        base, url = urlsplit(endpoint), urlsplit(pull.url)
        parts = repository.split("/")
        if len(parts) != 2 or any(not part or part in (".", "..") for part in parts):
            raise ValueError
        owner, repo = (quote(part, safe="") for part in parts)
        if provider == "gitea":
            path = f"/{owner}/{repo}/pulls/{pull.number}"
        elif provider == "azure-devops":
            path = f"/{owner}/_git/{repo}/pullrequest/{pull.number}"
        else:
            raise ValueError
        default_port = 443 if base.scheme == "https" else 80
        base_port, url_port = base.port, url.port
        valid = (
            base.scheme in ("https", "http") and url.scheme == base.scheme
            and bool(base.hostname) and url.hostname == base.hostname
            and (default_port if url_port is None else url_port) == (default_port if base_port is None else base_port)
            and not any(parsed.username or parsed.password or parsed.query or parsed.fragment for parsed in (base, url))
            and not any(character.isspace() or ord(character) < 32 for character in endpoint + pull.url)
            and url.path.removesuffix("/").casefold() == (base.path.rstrip("/") + path).casefold()
        )
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ProviderError("Provider creation returned an unrelated or invalid pull-request URL.")
