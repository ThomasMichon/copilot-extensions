"""Publication labels stay on the selected GitHub endpoint and credential."""

from __future__ import annotations

from collections.abc import Callable
import json
from subprocess import CompletedProcess
from typing import TYPE_CHECKING
from urllib.parse import quote

from .base import ProviderError
from .github_fork_source import _slug
from .observation_credentials import github_endpoint

if TYPE_CHECKING:
    from .github import GitHubProvider


def _request(
    provider: GitHubProvider, repository: str, number: int, *, api_base: str,
    token: str | None, labels: tuple[str, ...] | None,
    run: Callable[..., CompletedProcess[str]],
) -> str:
    host, endpoint = github_endpoint(provider.authority_endpoint(api_base))
    if type(number) is not int or number <= 0:
        raise ProviderError("Publication labels need a positive PR number.")
    path = "/repos/" + "/".join(quote(part, safe="") for part in _slug(repository).split("/"))
    args = ["gh", "api", "--hostname", host, "--method",
            "GET" if labels is None else "POST", endpoint + path + f"/issues/{number}/labels"]
    if labels is None:
        args += ["--paginate", "--slurp"]
    else:
        for label in labels:
            args += ["-f", "labels[]=" + label]
    env = provider._env(token, host=host)
    if token:
        env.update({alias: token for alias in (
            "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        )})
    result = run(args, env=env)
    if result.returncode:
        raise ProviderError(f"Publication-label request failed: {result.stderr.strip()}")
    return result.stdout


def read(
    provider: GitHubProvider, repository: str, number: int, *, api_base: str,
    token: str | None, run: Callable[..., CompletedProcess[str]],
) -> tuple[str, ...]:
    try:
        pages = json.loads(_request(
            provider, repository, number, api_base=api_base, token=token, labels=None, run=run,
        ))
    except ValueError as exc:
        raise ProviderError("Malformed publication-label response.") from exc
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ProviderError("Malformed publication-label pages.")
    labels = [item for page in pages for item in page]
    if any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in labels):
        raise ProviderError("Malformed publication-label response.")
    return tuple(item["name"] for item in labels)


def add(
    provider: GitHubProvider, repository: str, number: int, labels: tuple[str, ...], *,
    api_base: str, token: str | None, run: Callable[..., CompletedProcess[str]],
) -> None:
    _request(provider, repository, number, api_base=api_base, token=token, labels=labels, run=run)
