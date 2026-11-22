"""Resolve a qualified GitHub head from its actual repository network."""

from __future__ import annotations

from collections.abc import Callable
import json
import re
import time
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .. import pr_creation_budget
from .base import ProviderError
from .observation_credentials import github_endpoint

if TYPE_CHECKING:
    from .github import GitHubProvider


def _slug(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) is None:
        raise ProviderError("GitHub fork resolution returned an invalid repository identity.")
    if any(part in (".", "..") for part in value.split("/")):
        raise ProviderError("GitHub fork resolution returned an invalid repository identity.")
    return value


def resolve(
    provider: GitHubProvider, repository: str, owner: str, *, api_base: str = "",
    token: str | None = None, run: Callable[..., CompletedProcess[str]],
) -> str:
    repository = _slug(repository)
    if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?", owner) is None:
        raise ProviderError("GitHub fork resolution needs an exact head owner.")
    host, endpoint = github_endpoint(provider.authority_endpoint(api_base))
    env = provider._env(token, host=host)
    if token:
        env.update({name: token for name in (
            "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        )})
    deadline = time.monotonic() + 120

    def read(slug: str, *, forks: bool = False) -> Any:
        path = "/repos/" + "/".join(quote(part, safe="") for part in slug.split("/"))
        args = ["gh", "api", "--hostname", host, "--method", "GET",
                endpoint + path + ("/forks?per_page=100" if forks else "")]
        if forks:
            args += ["--paginate", "--slurp"]
        remaining = pr_creation_budget.remaining(deadline - time.monotonic(), provider=True)
        if remaining <= 0:
            raise ProviderError("GitHub fork resolution exceeded its observation budget; pass --source-repo.")
        result = run(args, env=env, timeout=remaining, max_stdout_bytes=1024 * 1024)
        if result.returncode:
            raise ProviderError(
                f"GitHub fork resolution could not observe the complete network: {result.stderr.strip()}. "
                "Pass --source-repo owner/repository."
            )
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ProviderError("GitHub fork resolution returned malformed data.") from exc

    target = read(repository)
    if (
        not isinstance(target, dict) or _slug(target.get("full_name")).casefold() != repository.casefold()
        or type(target.get("fork")) is not bool
    ):
        raise ProviderError("GitHub fork resolution did not identify the selected target.")
    source = target.get("source") if target["fork"] else target
    if not isinstance(source, dict):
        raise ProviderError("GitHub did not expose the target's fork network; pass --source-repo.")
    network = _slug(source.get("full_name"))
    pages = read(network, forks=True)
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ProviderError("GitHub fork resolution did not return complete fork pages.")
    candidates = {
        slug.casefold(): slug for slug in (_slug(target["full_name"]), network)
        if slug.partition("/")[0].casefold() == owner.casefold()
    }
    for page in pages:
        for fork in page:
            if not isinstance(fork, dict) or fork.get("fork") is not True:
                raise ProviderError("GitHub fork resolution returned an invalid network member.")
            slug = _slug(fork.get("full_name"))
            identity = fork.get("owner")
            if (
                not isinstance(identity, dict) or not isinstance(identity.get("login"), str)
                or identity["login"].casefold() != slug.partition("/")[0].casefold()
            ):
                raise ProviderError("GitHub fork resolution returned a mismatched repository owner.")
            if identity["login"].casefold() == owner.casefold():
                candidates[slug.casefold()] = slug
    if len(candidates) != 1:
        raise ProviderError("GitHub head repository is unavailable or ambiguous; pass --source-repo owner/repository.")
    return next(iter(candidates.values()))
