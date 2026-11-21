"""Qualified foreign heads resolve actual, complete repository-network data."""

import json
import subprocess
from copy import deepcopy
from collections.abc import Callable
from typing import Any, cast

import pytest

from agent_worktrees.providers import github
from agent_worktrees.providers.base import PRScope, ProviderError, PullResult
from test_pr_foreign_create import _tracking_setup as _tracking_setup


@pytest.mark.parametrize("target_is_fork", [False, True])
def test_legacy_foreign_head_resolves_a_differently_named_fork_from_all_pages(
    monkeypatch: pytest.MonkeyPatch, target_is_fork: bool,
) -> None:
    calls: list[list[str]] = []
    target: dict[str, Any] = {"full_name": "upstream/target", "fork": target_is_fork}
    if target_is_fork:
        target["source"] = {"full_name": "original/network-root"}

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["env"]["GH_HOST"] == "forge.example.test"
        assert argv[argv.index("--method") + 1] == "GET"
        assert any(value.startswith("https://forge.example.test:8443/") for value in argv)
        data: object = target
        if "--paginate" in argv:
            data = [
                [{"full_name": "other/copy", "fork": True, "owner": {"login": "other"}}],
                [{"full_name": "contributor/renamed-source", "fork": True,
                  "owner": {"login": "contributor"}}],
            ]
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(github, "run_cli", run)
    monkeypatch.setenv("GH_API_HOST", "unrelated.invalid")
    assert github.GitHubProvider().resolve_head_repository(
        "upstream/target", "contributor", api_base="https://forge.example.test:8443",
    ) == "contributor/renamed-source"
    assert len(calls) == 2
    network = "original/network-root" if target_is_fork else "upstream/target"
    assert any(value.endswith("/repos/" + network + "/forks?per_page=100") for value in calls[1])
    assert "--paginate" in calls[1] and "--slurp" in calls[1]


@pytest.mark.parametrize("defect", ["missing", "ambiguous", "incomplete"])
def test_foreign_head_never_guesses_a_fork_when_resolution_is_not_unique(
    monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        data: object = {"full_name": "upstream/target", "fork": False}
        if "--paginate" in argv:
            pages: list[Any] = [[]]
            if defect == "ambiguous":
                pages = [[
                    {"full_name": "contributor/first", "fork": True, "owner": {"login": "contributor"}},
                    {"full_name": "contributor/second", "fork": True, "owner": {"login": "contributor"}},
                ]]
            elif defect == "incomplete":
                pages = [[{"full_name": "contributor/renamed-source", "fork": True,
                           "owner": {"login": "contributor"}}], None]
            data = pages
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(github, "run_cli", run)
    with pytest.raises(ProviderError, match="ambiguous|unavailable|complete"):
        github.GitHubProvider().resolve_head_repository("upstream/target", "contributor")
    assert len(calls) == 2
    assert all(call[call.index("--method") + 1] == "GET" for call in calls)


def _pull(number: int = 7, source: str = "contributor/renamed-source") -> dict[str, Any]:
    return {
        "number": number, "html_url": f"https://forge.example.test/upstream/target/pull/{number}",
        "state": "open", "draft": True,
        "head": {"ref": "topic", "sha": "a" * 40, "repo": {
            "full_name": source, "clone_url": f"https://forge.example.test/{source}.git",
        }},
        "base": {"ref": "dev", "repo": {"full_name": "upstream/target"}},
    }


@pytest.mark.parametrize("operation", ["lookup", "create"])
@pytest.mark.parametrize("port", [443, 8443])
def test_provider_pull_url_compares_effective_https_authority(
    monkeypatch: pytest.MonkeyPatch, operation: str, port: int,
) -> None:
    payload = _pull()

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        data: object = [[payload]] if operation == "lookup" else payload
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(github, "run_cli", run)
    provider = github.GitHubProvider()

    def invoke() -> PullResult | None:
        endpoint = f"https://forge.example.test:{port}"
        if operation == "lookup":
            return provider.find_pull_by_source(
                "upstream/target", "contributor/renamed-source", "topic", api_base=endpoint,
            )
        return provider.create_pull(PRScope(
            repo="upstream/target", head="contributor:topic", base="dev",
            title="Fix", body="", api_base=endpoint,
            head_identity=("forge.example.test" + (f":{port}" if port != 443 else "")
                           + "/contributor/renamed-source"),
        ))

    if port == 443:
        result = invoke()
        assert result and result.number == 7
    else:
        with pytest.raises(ProviderError):
            invoke()


@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("duplicate", [False, True])
def test_source_lookup_uses_supported_query_and_exact_repository_across_pages(
    monkeypatch: pytest.MonkeyPatch, duplicate: bool, qualified: bool,
) -> None:
    calls: list[list[str]] = []
    wrong = _pull(8, "contributor/another-copy")
    old = _pull(3)
    old["state"] = "closed"
    pages = [[wrong, old], [_pull()]]
    if duplicate:
        pages[0].append(_pull(9))

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert argv[:2] == ["gh", "api"]
        assert argv[argv.index("--method") + 1] == "GET"
        assert "head=contributor:topic" in argv and "--head" not in argv
        assert "--paginate" in argv and "--slurp" in argv
        assert kwargs["env"]["GH_HOST"] == "forge.example.test"
        return subprocess.CompletedProcess(argv, 0, json.dumps(pages), "")

    monkeypatch.setattr(github, "run_cli", run)
    provider = github.GitHubProvider()
    monkeypatch.setattr(provider, "resolve_head_repository", lambda *a, **k: "contributor/renamed-source")

    def find() -> PullResult | None:
        if qualified:
            return provider.find_pull_by_head(
                "upstream/target", "contributor:topic", api_base="https://forge.example.test",
            )
        return provider.find_pull_by_source(
            "upstream/target", "contributor/renamed-source", "topic", api_base="https://forge.example.test",
        )

    if duplicate:
        with pytest.raises(ProviderError, match="More than one open PR"):
            find()
    else:
        result = find()
        assert result and result.number == 7 and result.head_ref == "topic"
        assert result.head_url == "https://forge.example.test/contributor/renamed-source.git"
    assert len(calls) == 1


@pytest.mark.parametrize("url", [
    None, "", "not-a-url", "https://other.example.test/upstream/target/pull/7",
    "https://forge.example.test/elsewhere/target/pull/7",
    "https://forge.example.test/upstream/target/pull/8",
    "https://forge.example.test/upstream/target/pull/\n7",
])
def test_source_lookup_rejects_missing_or_unrelated_pull_urls(
    monkeypatch: pytest.MonkeyPatch, url: str | None,
) -> None:
    payload = _pull()
    payload["html_url"] = url
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps([[payload]]), "")

    monkeypatch.setattr(github, "run_cli", run)
    with pytest.raises(ProviderError, match="URL"):
        github.GitHubProvider().find_pull_by_source(
            "upstream/target", "contributor/renamed-source", "topic", api_base="https://forge.example.test",
        )
    assert len(calls) == 1


@pytest.mark.parametrize("defect", [
    "none", "source", "branch", "base", "head", "labels", "url",
    "missing-url", "empty-url", "malformed-url", "other-host", "other-target", "other-number",
])
def test_qualified_creation_sends_exact_repository_and_checks_actual_response(
    monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    from agent_worktrees.providers.github_pull_source import CreatedSourceMismatch

    payload = _pull()
    if defect == "source":
        payload = _pull(source="contributor/another-copy")
    elif defect == "branch":
        payload["head"]["ref"] = "other"
    elif defect == "base":
        payload["base"]["repo"]["full_name"] = "elsewhere/target"
    elif defect == "head":
        payload["head"]["sha"] = "b" * 40
    elif defect == "url":
        payload["html_url"] = 7
    elif defect == "missing-url":
        del payload["html_url"]
    elif defect == "empty-url":
        payload["html_url"] = ""
    elif defect == "malformed-url":
        payload["html_url"] = "not-a-url"
    elif defect == "other-host":
        payload["html_url"] = "https://other.example.test/upstream/target/pull/7"
    elif defect == "other-target":
        payload["html_url"] = "https://forge.example.test/unrelated/target/pull/7"
    elif defect == "other-number":
        payload["html_url"] = "https://forge.example.test/upstream/target/pull/8"
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert argv[:2] == ["gh", "api"] and argv[argv.index("--method") + 1] == "POST"
        assert kwargs["env"]["GH_HOST"] == "forge.example.test"
        if any(value.endswith("/issues/7/labels") for value in argv):
            assert defect in ("none", "labels")
            return subprocess.CompletedProcess(argv, 1 if defect == "labels" else 0, "", "label unavailable")
        assert "head=contributor:topic" in argv and "head_repo=renamed-source" in argv
        assert "base=dev" in argv and "draft=true" in argv
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(github, "run_cli", run)
    provider = github.GitHubProvider()
    monkeypatch.setattr(provider, "observe_branch", lambda *a, **k: {
        "repository": "contributor/renamed-source", "branch": "topic", "head_sha": "a" * 40,
    })
    scope = PRScope(
        repo="upstream/target", head="contributor:topic", base="dev", title="Fix", body="Details",
        api_base="https://forge.example.test", draft=True, labels=("review-needed",),
        head_identity="forge.example.test/contributor/renamed-source", expected_head_sha="a" * 40,
    )
    if defect in ("none", "labels"):
        result = provider.create_pull(scope)
        assert result.number == 7 and result.draft is True and result.head_sha == "a" * 40
        assert bool(result.label_error) is (defect == "labels")
        assert len(calls) == 2
    else:
        with pytest.raises(CreatedSourceMismatch) as error:
            provider.create_pull(scope)
        assert error.value.pull.number == 7
        assert error.value.pull.url == (payload.get("html_url", "") if defect != "url" else "")
        assert len(calls) == 1


@pytest.mark.parametrize("defect", ["none", "source-input", "repository", "branch", "head"])
def test_foreign_reuse_validates_source_before_lookup_and_claim(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any, defect: str,
) -> None:
    from agent_worktrees import pr_config, pr_foreign_checks, pr_foreign_create
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    path = ledger / f"{wid}.yaml"
    before = path.read_bytes()
    foreign_resolution = cast(Callable[..., pr_config.ForeignRepoResolution], _foreign_resolution)
    caller_config = cast(Callable[..., Any], _config)
    resolution = foreign_resolution(provider="github", required_body_sections=("Intent",))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    calls: list[tuple[str, str, str]] = []
    observed = PullResult(
        number=7, url="https://github.com/owner/other-repo/pull/7", state="open", base_ref="dev",
        head_ref="topic", head_url="https://github.com/contributor/renamed-source.git", head_sha="a" * 40,
    )
    if defect == "repository":
        observed.head_url = "https://github.com/contributor/another-copy.git"
    elif defect == "branch":
        observed.head_ref = "elsewhere"
    elif defect == "head":
        observed.head_sha = "b" * 40

    class GitHub(github.GitHubProvider):
        def find_pull_by_source(self, repo: str, source: str, branch: str, *,
                                api_base: str = "", token: str | None = None) -> PullResult | None:
            calls.append((repo, source, branch))
            return deepcopy(observed)

        def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
            pytest.fail("Reuse must not create another PR")

    monkeypatch.setattr(providers, "get_provider", lambda *_: GitHub())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    if defect == "head":
        monkeypatch.setattr(pr_foreign_checks, "qualify", lambda *a, **k: {"head": "a" * 40})
    result = pr_foreign_create.create_foreign_pr_from_branch(
        wid, caller_config(), target_repo="owner/other-repo", from_branch="contributor:topic", title="Fix",
        source_repository="wrong/source" if defect == "source-input" else "contributor/renamed-source",
        source_workspace="reachable-source" if defect == "head" else None,
        expected_head="a" * 40 if defect == "head" else None,
    )
    if defect == "none":
        assert result["claimed"] and result["reused"]
    else:
        assert "error" in result and not result.get("claimed")
        assert path.read_bytes() == before
    assert calls == ([] if defect == "source-input" else [
        ("owner/other-repo", "contributor/renamed-source", "topic"),
    ])


@pytest.mark.parametrize("malformed", [False, True])
def test_foreign_creation_retains_unverified_result_without_claiming_or_recreating(
    monkeypatch: pytest.MonkeyPatch, _tracking_setup: Any, malformed: bool,
) -> None:
    from dataclasses import replace
    from agent_worktrees import pr_config, pr_foreign_create
    from agent_worktrees.providers import base as providers
    from test_pr_foreign_create import _config, _foreign_resolution

    ledger, wid = _tracking_setup
    path = ledger / f"{wid}.yaml"
    before = path.read_bytes()
    foreign_resolution = cast(Callable[..., pr_config.ForeignRepoResolution], _foreign_resolution)
    caller_config = cast(Callable[..., Any], _config)
    resolution = foreign_resolution(provider="github")
    assert resolution.repo_config is not None
    repo = replace(resolution.repo_config, pr=replace(
        resolution.repo_config.pr, api_base="https://forge.example.test",
    ))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: replace(resolution, repo_config=repo))
    backend = github.GitHubProvider()
    monkeypatch.setattr(backend, "find_pull_by_source", lambda *a, **k: None)
    monkeypatch.setattr(providers, "get_provider", lambda *_: backend)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    payload = _pull(source="contributor/another-copy")
    if malformed:
        payload["head"] = None
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(github, "run_cli", run)
    result = pr_foreign_create.create_foreign_pr_from_branch(
        wid, caller_config(), target_repo="upstream/target", from_branch="contributor:topic",
        source_repository="contributor/renamed-source", title="Fix",
    )
    assert result["error"] and result["publication_state"] == "unknown"
    assert result["number"] == 7 and result["url"].endswith("/pull/7") and result["pr_opened"]
    assert not result.get("claimed") and path.read_bytes() == before
    assert len(calls) == 1 and calls[0][calls[0].index("--method") + 1] == "POST"
