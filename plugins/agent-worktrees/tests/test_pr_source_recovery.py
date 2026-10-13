"""Transport-only regressions for provider-confirmed PR source observations."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy

import pytest

from agent_worktrees.providers import azure_devops, github, gitea
from agent_worktrees.providers.base import ProviderError, PullResult


HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
SOURCE_REF = "feature/recovery"
PROVIDERS = ("github", "gitea", "azure")

pytestmark = pytest.mark.contract("agent_worktrees.pr.operator_recovery.provider_source")


def _payload(provider: str, *, fork: bool = False) -> dict:
    owner = "contributor" if fork else "example"
    if provider == "github":
        return {
            "url": "https://github.example/example/project/pull/17",
            "number": 17,
            "state": "OPEN",
            "headRefOid": HEAD_SHA,
            "baseRefName": "main",
            "headRefName": SOURCE_REF,
            "headRepository": {"nameWithOwner": f"{owner}/project"},
        }
    if provider == "gitea":
        return {
            "html_url": "https://git.example/example/project/pulls/17",
            "number": 17,
            "state": "open",
            "merged": False,
            "head": {
                "sha": HEAD_SHA,
                "ref": SOURCE_REF,
                "repo": {"clone_url": f"https://git.example/{owner}/project.git"},
            },
            "base": {
                "ref": "main",
                "repo": {"clone_url": "https://git.example/example/project.git"},
            },
        }
    data = {
        "status": "active",
        "lastMergeSourceCommit": {"commitId": HEAD_SHA},
        "sourceRefName": f"refs/heads/{SOURCE_REF}",
        "targetRefName": "refs/heads/main",
        "repository": {"remoteUrl": "https://dev.azure.com/example/team/_git/project"},
    }
    if fork:
        data["forkSource"] = {
            "name": f"refs/heads/{SOURCE_REF}",
            "repository": {
                "remoteUrl": "https://dev.azure.com/example/team/_git/project-fork",
            },
        }
    return data


def _get_pull(monkeypatch: pytest.MonkeyPatch, provider: str, data: object) -> PullResult:
    return _get_body(monkeypatch, provider, json.dumps(data))


def _get_body(monkeypatch: pytest.MonkeyPatch, provider: str, body: str) -> PullResult:
    if provider == "gitea":
        def transport(method: str, url: str, token: str) -> tuple[int, str]:
            assert method == "GET"
            assert url == "https://git.example/api/v1/repos/example/project/pulls/17"
            assert token == "test-token"
            return 200, body

        monkeypatch.setattr(gitea.GiteaProvider, "_curl", staticmethod(transport))
        return gitea.GiteaProvider().get_pull(
            "example/project", 17, api_base="https://git.example", token="test-token",
        )

    def transport(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if provider == "github":
            assert args[:4] == ["gh", "pr", "view", "17"]
            fields = args[args.index("--json") + 1].split(",")
            assert {"headRefOid", "headRefName", "headRepository"} <= set(fields)
            assert kwargs["env"] == {"GH_HOST": "github.example"}
        else:
            assert args[:4] == ["az", "repos", "pr", "show"]
            assert args[args.index("--id") + 1] == "17"
        return subprocess.CompletedProcess(args, 0, stdout=body, stderr="")

    module = github if provider == "github" else azure_devops
    monkeypatch.setattr(module, "run_cli", transport)
    if provider == "github":
        return github.GitHubProvider().get_pull(
            "example/project", 17, api_base="https://github.example",
        )
    return azure_devops.AzureDevOpsProvider().get_pull(
        "team/project", 17, api_base="https://dev.azure.com/example",
    )


@pytest.mark.guard
@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("fork", (False, True), ids=("same-repository", "fork"))
def test_get_pull_preserves_full_source(
    monkeypatch: pytest.MonkeyPatch, provider: str, fork: bool,
) -> None:
    result = _get_pull(monkeypatch, provider, _payload(provider, fork=fork))
    owner = "contributor" if fork else "example"
    expected_urls = {
        "github": f"https://github.example/{owner}/project.git",
        "gitea": f"https://git.example/{owner}/project.git",
        "azure": "https://dev.azure.com/example/team/_git/"
        + ("project-fork" if fork else "project"),
    }
    assert result.head_sha == HEAD_SHA
    assert result.head_ref == SOURCE_REF
    assert result.head_url == expected_urls[provider]
    assert result.base_ref == "main"
    assert result.number == 17
    assert result.state == "open"
    assert result.merged is False


@pytest.mark.guard
@pytest.mark.parametrize("owner", ("user_team", "service_account_enterprise"))
def test_github_enterprise_managed_user_preserves_merged_source(
    monkeypatch: pytest.MonkeyPatch, owner: str,
) -> None:
    data = _payload("github")
    data["state"] = "MERGED"
    data["headRepository"]["nameWithOwner"] = f"{owner}/project"
    result = _get_pull(monkeypatch, "github", data)
    assert result.state == "merged"
    assert result.merged is True
    assert result.head_sha == HEAD_SHA
    assert result.head_ref == SOURCE_REF
    assert result.head_url == f"https://github.example/{owner}/project.git"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_get_pull_missing_source_does_not_invent_identity(
    monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    result = _get_pull(monkeypatch, provider, {})
    assert (result.head_sha, result.head_ref, result.head_url, result.base_ref) == (
        "", "", "", "",
    )
    assert result.state == "open"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_get_pull_nullable_source_is_unknown(
    monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    null_fields = {
        "github": ("headRefOid", "headRefName", "headRepository", "baseRefName"),
        "gitea": ("head", "base"),
        "azure": ("lastMergeSourceCommit", "sourceRefName", "repository", "targetRefName"),
    }
    data = _payload(provider)
    data.update(dict.fromkeys(null_fields[provider]))
    result = _get_pull(monkeypatch, provider, data)
    assert (result.head_sha, result.head_ref, result.head_url, result.base_ref) == (
        "", "", "", "",
    )


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("body", ("null", "[]", '"payload"', "17", "true", "{"))
def test_get_pull_invalid_payload_is_provider_error(
    monkeypatch: pytest.MonkeyPatch, provider: str, body: str,
) -> None:
    with pytest.raises(ProviderError, match="Malformed pull-request response"):
        _get_body(monkeypatch, provider, body)


MALFORMED_FIELDS = (
    ("github", ("headRepository",)),
    ("github", ("headRepository", "nameWithOwner")),
    ("github", ("headRefName",)),
    ("github", ("headRefOid",)),
    ("github", ("baseRefName",)),
    ("github", ("url",)),
    ("gitea", ("head",)),
    ("gitea", ("head", "repo")),
    ("gitea", ("head", "repo", "clone_url")),
    ("gitea", ("head", "ref")),
    ("gitea", ("head", "sha")),
    ("gitea", ("base",)),
    ("gitea", ("base", "ref")),
    ("azure", ("forkSource",)),
    ("azure", ("forkSource", "repository")),
    ("azure", ("forkSource", "repository", "remoteUrl")),
    ("azure", ("sourceRefName",)),
    ("azure", ("targetRefName",)),
    ("azure", ("lastMergeSourceCommit",)),
    ("azure", ("lastMergeSourceCommit", "commitId")),
    ("azure", ("repository",)),
    ("azure", ("repository", "remoteUrl")),
)


@pytest.mark.parametrize("provider,path", MALFORMED_FIELDS)
@pytest.mark.parametrize("value", (False, 123, ["unexpected"]))
def test_get_pull_rejects_non_typed_source_fields(
    monkeypatch: pytest.MonkeyPatch, provider: str, path: tuple[str, ...], value: object,
) -> None:
    data = deepcopy(_payload(provider, fork=path[0] == "forkSource"))
    parent = data
    for field in path[:-1]:
        parent = parent[field]
    parent[path[-1]] = value
    with pytest.raises(ProviderError, match="Malformed pull-request source"):
        _get_pull(monkeypatch, provider, data)


@pytest.mark.parametrize("provider,path", MALFORMED_FIELDS)
def test_get_pull_rejects_wrong_container_shape(
    monkeypatch: pytest.MonkeyPatch, provider: str, path: tuple[str, ...],
) -> None:
    object_fields = {"headRepository", "head", "repo", "base", "forkSource",
                     "repository", "lastMergeSourceCommit"}
    value = "unexpected" if path[-1] in object_fields else {}
    data = _payload(provider, fork=path[0] == "forkSource")
    parent = data
    for field in path[:-1]:
        parent = parent[field]
    parent[path[-1]] = value
    with pytest.raises(ProviderError, match="Malformed pull-request source"):
        _get_pull(monkeypatch, provider, data)


@pytest.mark.parametrize("repository", (None, {}, {"nameWithOwner": None}, {"name": "project"}))
def test_github_missing_source_owner_does_not_use_destination(
    monkeypatch: pytest.MonkeyPatch, repository: object,
) -> None:
    data = _payload("github", fork=True)
    data["headRepository"] = repository
    result = _get_pull(monkeypatch, "github", data)
    assert result.head_url == ""
    assert result.head_ref == SOURCE_REF
    assert result.head_sha == HEAD_SHA


@pytest.mark.parametrize("name", (
    "project", "owner/project/extra", "owner /project", "../project",
    "emu_owner/../project", "emu_owner/.", "emu_owner/..",
    "emu_owner/project%2Fextra", "emu_owner/project\\extra",
    "emu_owner /project", "emu_owner@other/project", "emu_owner/project?query",
))
def test_github_malformed_repository_name_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    data = _payload("github")
    data["headRepository"]["nameWithOwner"] = name
    with pytest.raises(ProviderError, match="Malformed pull-request source repository"):
        _get_pull(monkeypatch, "github", data)


@pytest.mark.parametrize("url", ("", "not-a-url", "file:///project", "https://[invalid"))
def test_github_unusable_url_cannot_identify_a_source(
    monkeypatch: pytest.MonkeyPatch, url: str,
) -> None:
    data = _payload("github")
    data["url"] = url
    try:
        result = _get_pull(monkeypatch, "github", data)
    except ProviderError:
        return
    assert result.head_url == ""


@pytest.mark.parametrize("fork_source", ({}, {"repository": None}, {"repository": {}}))
def test_azure_incomplete_fork_never_falls_back_to_destination(
    monkeypatch: pytest.MonkeyPatch, fork_source: dict,
) -> None:
    data = _payload("azure", fork=True)
    data["forkSource"] = fork_source
    result = _get_pull(monkeypatch, "azure", data)
    assert result.head_url == ""
    assert result.head_sha == HEAD_SHA
    assert result.head_ref == SOURCE_REF


def test_azure_null_fork_source_preserves_same_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _payload("azure")
    data["forkSource"] = None
    result = _get_pull(monkeypatch, "azure", data)
    assert result.head_url == data["repository"]["remoteUrl"]


@pytest.mark.parametrize("destination", [
    "git@ssh.dev.azure.com:v3/example/team/project",
    "ssh://git@ssh.dev.azure.com:22/v3/example/team/project",
    "https://dev.azure.com/example/team/_git/project",
])
def test_azure_source_matches_secure_transport_alias(destination: str) -> None:
    from agent_worktrees import pr_publish
    from agent_worktrees.providers.pr_source import same_repository

    source = pr_publish._identity("https://dev.azure.com/example/team/_git/project", ".")
    target = pr_publish._identity(destination, ".")
    assert same_repository(source, target, "azure-devops")


@pytest.mark.parametrize("destination", [
    "git@ssh.dev.azure.com:v3/other/team/project",
    "git@ssh.dev.azure.com:v3/example/other/project",
    "git@ssh.dev.azure.com:v3/example/team/other",
    "ssh://git@ssh.dev.azure.com:2222/v3/example/team/project",
    "http://dev.azure.com/example/team/_git/project",
    "git@other.example:v3/example/team/project",
])
def test_azure_source_refuses_distinct_destination(destination: str) -> None:
    from agent_worktrees import pr_publish
    from agent_worktrees.providers.pr_source import same_repository

    source = pr_publish._identity("https://dev.azure.com/example/team/_git/project", ".")
    target = pr_publish._identity(destination, ".")
    assert not same_repository(source, target, "azure-devops")
    assert not same_repository(source, target, "github")


@pytest.mark.parametrize("repository", (None, {}, {"clone_url": None}))
def test_gitea_missing_head_repository_never_uses_base(
    monkeypatch: pytest.MonkeyPatch, repository: object,
) -> None:
    data = _payload("gitea", fork=True)
    data["head"]["repo"] = repository
    result = _get_pull(monkeypatch, "gitea", data)
    assert result.head_url == ""
    assert result.head_sha == HEAD_SHA
    assert result.head_ref == SOURCE_REF


@pytest.mark.parametrize("provider", PROVIDERS)
def test_get_pull_merged_lifecycle_remains_compatible(
    monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    data = _payload(provider, fork=True)
    if provider == "github":
        data["state"] = "MERGED"
    elif provider == "gitea":
        data.update(state="closed", merged=True)
    else:
        data["status"] = "completed"
    result = _get_pull(monkeypatch, provider, data)
    assert result.state == "merged"
    assert result.merged is True
    assert result.head_sha == HEAD_SHA
