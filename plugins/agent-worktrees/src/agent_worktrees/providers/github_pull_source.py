"""GitHub pull creation and reuse bound to an exact source repository."""

from __future__ import annotations

from collections.abc import Callable
import json
from subprocess import CompletedProcess
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

from .base import PRScope, ProviderError, PullResult
from .github_fork_source import _slug
from .observation_credentials import github_endpoint
from .pr_source import source_object, source_text

if TYPE_CHECKING:
    from .github import GitHubProvider


class CreatedSourceMismatch(ProviderError):
    """Creation returned an identity that must be reconciled, not claimed."""

    def __init__(self, pull: PullResult) -> None:
        super().__init__("Created PR source or target differs from the selected publication; reconcile it before retrying.")
        self.pull = pull


def matches(pull: PullResult, source: str, branch: str, endpoint: str) -> bool:
    from ..pr_publish import _identity
    expected = _identity("https://" + endpoint + "/" + _slug(source), "")
    return bool(expected) and pull.head_ref == branch and _identity(pull.head_url, "").casefold() == expected.casefold()


def validate_url(raw_url: str, number: int | None, repository: str, endpoint: str) -> None:
    """Require the returned URL to name the selected forge, target and number."""
    if type(number) is not int or number <= 0:
        raise ProviderError("GitHub did not return a valid pull-request identity.")
    try:
        url = urlsplit(raw_url)
        valid_url = (
            not any(character.isspace() or ord(character) < 32 for character in raw_url)
            and url.scheme == "https" and url.netloc.casefold() == endpoint.casefold()
            and not url.username and not url.password and not url.query and not url.fragment
            and url.path.removesuffix("/").casefold() == f"/{_slug(repository)}/pull/{number}".casefold()
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ProviderError("GitHub returned an unrelated or invalid pull-request URL.")


def _pull(data: Any, repository: str, endpoint: str) -> PullResult:
    if not isinstance(data, dict) or type(data.get("number")) is not int or data["number"] <= 0:
        raise ProviderError("GitHub did not return a valid pull-request identity.")
    validate_url(source_text(data, "html_url"), data["number"], repository, endpoint)
    head, base = source_object(data, "head"), source_object(data, "base")
    source_repository = source_object(head, "repo")
    from ..pr_publish import _identity
    slug = _slug(source_repository.get("full_name"))
    if _identity(source_text(source_repository, "clone_url"), "").partition("/")[2].casefold() != slug.casefold():
        raise ProviderError("GitHub returned inconsistent source repository identities.")
    state = source_text(data, "state")
    if state not in ("open", "closed"):
        raise ProviderError("GitHub did not return a valid pull-request state.")
    return PullResult(
        number=data["number"], url=source_text(data, "html_url"), state=state,
        merged=bool(data.get("merged_at")), head_sha=source_text(head, "sha"),
        head_ref=source_text(head, "ref"), head_url=source_text(source_repository, "clone_url"),
        base_ref=source_text(base, "ref"),
        draft=data.get("draft") if type(data.get("draft")) is bool else None,
    )


def _request(
    provider: GitHubProvider, repository: str, *, api_base: str, token: str | None,
    method: str, fields: tuple[tuple[str, str], ...], paginate: bool = False,
    run: Callable[..., CompletedProcess[str]],
) -> Any:
    host, endpoint = github_endpoint(provider.authority_endpoint(api_base))
    path = "/repos/" + "/".join(quote(part, safe="") for part in _slug(repository).split("/")) + "/pulls"
    args = ["gh", "api", "--hostname", host, "--method", method, endpoint + path]
    for key, value in fields:
        args += ["-F" if key == "draft" else "-f", key + "=" + value]
    if paginate:
        args += ["--paginate", "--slurp"]
    env = provider._env(token, host=host)
    if token:
        env.update({name: token for name in (
            "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        )})
    result = run(args, env=env)
    if result.returncode:
        raise ProviderError(f"GitHub source-bound PR request failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise ProviderError("GitHub source-bound PR request returned malformed data.") from exc


def find(
    provider: GitHubProvider, repository: str, source: str, branch: str, *,
    api_base: str = "", token: str | None = None,
    run: Callable[..., CompletedProcess[str]],
) -> PullResult | None:
    source = _slug(source)
    pages = _request(
        provider, repository, api_base=api_base, token=token, method="GET",
        fields=(("state", "all"), ("head", source.partition("/")[0] + ":" + branch), ("per_page", "100")),
        paginate=True, run=run,
    )
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ProviderError("GitHub did not return complete source-bound PR pages.")
    candidates = []
    for page in pages:
        for data in page:
            if isinstance(data, dict) and data.get("state") == "closed":
                head = data.get("head")
                if not isinstance(head, dict) or head.get("repo") is None:
                    continue
            pull = _pull(data, repository, provider.authority_endpoint(api_base))
            if matches(pull, source, branch, provider.authority_endpoint(api_base)):
                candidates.append(pull)
    opened = [pull for pull in candidates if pull.state == "open" and not pull.merged]
    if len(opened) > 1:
        raise ProviderError("More than one open PR matches the exact source repository and branch.")
    return opened[0] if opened else candidates[0] if candidates else None


def create(
    provider: GitHubProvider, scope: PRScope, *, token: str | None,
    run: Callable[..., CompletedProcess[str]],
) -> PullResult:
    source = _slug(scope.head_identity.partition("/")[2])
    owner, separator, branch = scope.head.partition(":")
    if not separator or owner.casefold() != source.partition("/")[0].casefold():
        raise ProviderError("Qualified GitHub creation requires the exact source repository.")
    data = _request(
        provider, scope.repo, api_base=scope.api_base, token=token, method="POST",
        fields=(("head", scope.head), ("head_repo", source.partition("/")[2]),
                ("base", scope.base), ("title", scope.title), ("body", scope.body),
                ("draft", "true" if scope.draft else "false")), run=run,
    )
    if not isinstance(data, dict) or type(data.get("number")) is not int or data["number"] <= 0:
        raise ProviderError("GitHub creation did not return a valid pull-request identity.")
    receipt = PullResult(number=data["number"])
    try:
        receipt.url = source_text(data, "html_url")
        pull = _pull(data, scope.repo, provider.authority_endpoint(scope.api_base))
        base = source_object(data, "base")
        if (
            not matches(pull, source, branch, provider.authority_endpoint(scope.api_base))
            or _slug(source_object(base, "repo").get("full_name")).casefold() != scope.repo.casefold()
            or pull.base_ref != scope.base or pull.state != "open" or pull.merged
            or (scope.expected_head_sha and pull.head_sha != scope.expected_head_sha)
        ):
            raise CreatedSourceMismatch(pull)
    except ProviderError as exc:
        raise CreatedSourceMismatch(receipt) from exc
    if scope.labels:
        try:
            assert pull.number is not None
            provider.add_publication_labels(scope.repo, pull.number, scope.labels, api_base=scope.api_base, token=token)
        except (ProviderError, OSError) as exc:
            pull.label_error = str(exc)
    return pull
