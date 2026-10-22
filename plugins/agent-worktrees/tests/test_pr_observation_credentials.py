"""Credential renewal at the native transport boundary, never once per poll."""

from __future__ import annotations

import base64
import json
import subprocess
import time
from dataclasses import dataclass, replace
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from agent_worktrees import config, pr_watch
from agent_worktrees.pr_observation import configured_snapshot
from agent_worktrees.pr_observation_config import ObservationConfig
from agent_worktrees.providers import github
from agent_worktrees.providers.azure_devops import AzureDevOpsProvider
from agent_worktrees.providers.azure_observation_reads import AdoReader
from agent_worktrees.providers import azure_observation_reads
from agent_worktrees.providers.base import ProviderError
from agent_worktrees.providers.observation_credentials import (
    ObservationCredentialError, ObservationCredentials,
)


@dataclass(frozen=True)
class Binding:
    provider: str = "github"
    api_base: str = "https://api.github.com"
    repository: str = "owner/repository"
    permission: str = "observe"
    authentication: str = "bearer"


@dataclass(frozen=True)
class Material:
    token: str
    expires_at: int
    forge: Binding | None


class Source:
    def __init__(self, binding=None, *, fail_at=None):
        self.binding = binding or Binding()
        self.fail_at = fail_at
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise ConnectionError("private-provider-detail")
        return Material("synthetic-" + str(self.calls), int(time.time()) + 60, self.binding)


def credentials(source):
    return ObservationCredentials(
        source, provider="github", api_base="https://api.github.com",
        repository="owner/repository",
    )


@pytest.mark.parametrize("field,value", [
    ("provider", "azure-devops"), ("api_base", "https://other.example"),
    ("repository", "other/repository"), ("permission", "write"),
    ("authentication", "basic"), ("authentication", "unknown"),
])
def test_wrong_binding_is_refused(field, value):
    source = Source(replace(Binding(), **{field: value}))
    with pytest.raises(ObservationCredentialError, match="binding mismatch"):
        credentials(source).acquire()


@pytest.mark.parametrize("changes", [
    {"forge": None}, {"token": ""}, {"token": "line\nbreak"},
    {"token": "has space"}, {"token": "\u00e9"}, {"token": "x" * 16385},
    {"expires_at": True}, {"expires_at": float("inf")}, {"expires_at": 0},
])
def test_invalid_or_expired_material_is_refused(changes):
    material = replace(Source()(), **changes)
    with pytest.raises(ObservationCredentialError):
        credentials(lambda: material).acquire()


def test_source_disconnect_is_sanitized_and_retryable_without_caching():
    source = Source(fail_at=2)
    consumer = credentials(source)
    assert consumer.acquire().token == "synthetic-1"
    with pytest.raises(ObservationCredentialError) as error:
        consumer.acquire()
    assert error.value.transient
    assert "private-provider-detail" not in str(error.value)
    assert error.value.__cause__ is None
    assert consumer.acquire().token == "synthetic-3"


def review(number):
    return {
        "id": number, "state": "APPROVED", "user": {"login": "reviewer"},
        "submitted_at": "2026-01-01T00:00:00Z", "commit_id": "head",
    }


def github_transport(monkeypatch):
    calls = []

    def run(argv, *, env):
        calls.append((argv, env))
        endpoint = argv[-1]
        if endpoint.endswith("/pulls/7"):
            body = {
                "head": {"sha": "head"}, "state": "open", "title": "Example",
                "base": {"ref": "main"}, "mergeable": True,
            }
        elif endpoint.endswith("/status"):
            body = {"total_count": 0, "statuses": []}
        elif "/check-runs?" in endpoint:
            offset = 0 if endpoint.endswith("page=1") else 100
            body = {"total_count": 101, "check_runs": [
                {"id": number, "name": "check", "status": "completed", "conclusion": "success"}
                for number in range(offset, min(offset + 100, 101))
            ]}
        elif "/reviews?" in endpoint:
            body = [review(number) for number in range(1, 101)] if endpoint.endswith("page=1") else []
        else:
            raise AssertionError(argv)
        assert "--paginate" not in argv
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    monkeypatch.setattr(github, "run_cli", run)
    return calls


def test_github_renews_metadata_checks_and_each_review_and_check_page(monkeypatch):
    calls = github_transport(monkeypatch)
    source = Source()
    # Selected mode must not consult these otherwise-valid CLI credentials.
    monkeypatch.setenv("GH_TOKEN", "ambient-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-secret")
    monkeypatch.setenv("GH_HOST", "unrelated.example")
    result = github.GitHubProvider().get_snapshot(
        "owner/repository", 7, credential_source=source,
    )
    assert result.checks_complete and len(result.reviews) == 100
    assert source.calls == len(calls) == 6
    for index, (argv, env) in enumerate(calls, 1):
        assert argv[argv.index("--hostname") + 1] == "github.com"
        assert argv[-1].startswith("https://api.github.com/repos/owner/repository/")
        assert set(env.values()) == {"synthetic-" + str(index)}
        assert "synthetic-" not in " ".join(argv)
    assert "synthetic-" not in repr(result)


@pytest.mark.parametrize("failure", range(1, 7))
def test_github_source_loss_stops_before_the_next_cli_call(monkeypatch, failure):
    calls = github_transport(monkeypatch)
    source = Source(fail_at=failure)
    with pytest.raises(ObservationCredentialError):
        github.GitHubProvider().get_snapshot("owner/repository", 7, credential_source=source)
    assert source.calls == failure
    assert len(calls) == failure - 1


@pytest.mark.parametrize("host,api_base", [
    ("github.com", "https://api.github.com"),
    ("github.com:443", "https://api.github.com"),
    ("tenant.ghe.com", "https://api.tenant.ghe.com"),
    ("tenant.ghe.com:443", "https://api.tenant.ghe.com"),
    ("forge.example", "https://forge.example/api/v3"),
    ("forge.example:443", "https://forge.example/api/v3"),
    ("forge.example:8443", "https://forge.example:8443/api/v3"),
    ("garage.github.com", "https://garage.github.com/api/v3"),
])
def test_github_uses_supported_https_api_endpoint(monkeypatch, host, api_base):
    calls = github_transport(monkeypatch)
    source = Source(replace(Binding(), api_base=api_base))
    github.GitHubProvider().get_snapshot(
        "owner/repository", 7, api_base="https://" + host, credential_source=source,
    )
    assert all(argv[argv.index("--hostname") + 1] == host.split(":")[0] for argv, _ in calls)
    assert all(argv[-1].startswith(api_base + "/repos/owner/repository/") for argv, _ in calls)


@pytest.mark.parametrize("api_base", [
    "github.localhost", "https://github.localhost", "https://github.localhost:443",
])
def test_github_http_only_host_is_refused_before_acquiring_or_emitting(monkeypatch, api_base):
    calls = github_transport(monkeypatch)
    source = Source()
    with pytest.raises(ObservationCredentialError, match="HTTPS API endpoint"):
        github.GitHubProvider().get_snapshot(
            "owner/repository", 7, api_base=api_base, credential_source=source,
        )
    assert source.calls == 0 and not calls


def test_selected_github_auth_loss_is_retryable_and_never_echoes_provider_secret(monkeypatch):
    monkeypatch.setattr(
        github, "run_cli",
        lambda argv, **_: subprocess.CompletedProcess(argv, 1, "", "HTTP 401 synthetic-1"),
    )
    with pytest.raises(ProviderError) as error:
        github.GitHubProvider().get_snapshot("owner/repository", 7, credential_source=Source())
    assert error.value.transient
    assert "synthetic-1" not in str(error.value)


@pytest.mark.parametrize("failure", [2, 3, 4])
def test_github_check_authorization_loss_aborts_later_reads(monkeypatch, failure):
    calls = github_transport(monkeypatch)
    run = github.run_cli

    def fail(argv, **options):
        if len(calls) == failure - 1:
            return subprocess.CompletedProcess(argv, 1, "", "HTTP 403 synthetic-secret")
        return run(argv, **options)

    monkeypatch.setattr(github, "run_cli", fail)
    source = Source()
    with pytest.raises(ObservationCredentialError) as error:
        github.GitHubProvider().get_snapshot("owner/repository", 7, credential_source=source)
    assert error.value.transient and "synthetic-secret" not in str(error.value)
    assert source.calls == failure and len(calls) == failure - 1


def ado_binding(authentication="bearer"):
    return Binding("azure-devops", "https://dev.azure.test/example",
                   "project/repository", "observe", authentication)


class Response(BytesIO):
    status = 200

    def __init__(self, body, continuation=""):
        super().__init__(json.dumps(body).encode())
        self.headers = {"x-ms-continuationtoken": continuation}


class Opener:
    def __init__(self):
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append(request)
        page = len(self.calls)
        return Response({"value": [{"id": page}], "count": 1}, "next" if page == 1 else "")


def ado_reader(source, monkeypatch):
    provider = AzureDevOpsProvider()
    monkeypatch.setattr(provider, "_auth_header", lambda *_: pytest.fail("ambient ADO auth"))
    opener = Opener()
    return AdoReader(
        provider, "project/repository", 7, "https://dev.azure.test/example", None,
        credential_source=source, opener=opener,
    ), opener


@pytest.mark.parametrize("authentication", ["basic", "bearer"])
def test_ado_renews_each_continuation_page_and_preserves_authentication(monkeypatch, authentication):
    source = Source(ado_binding(authentication))
    reader, opener = ado_reader(source, monkeypatch)
    assert source.calls == 0
    result = reader.collection(reader.base, continuation_paging=True)
    assert result.complete and len(result.records) == 2
    assert source.calls == len(opener.calls) == 2
    for index, request in enumerate(opener.calls, 1):
        scheme, value = request.get_header("Authorization").split(" ", 1)
        if authentication == "basic":
            assert scheme == "Bas" + "ic"
            assert base64.b64decode(value).decode() == ":synthetic-" + str(index)
        else:
            assert scheme == "Bear" + "er"
            assert value == "synthetic-" + str(index)


def test_ado_source_loss_aborts_best_effort_collection(monkeypatch):
    source = Source(ado_binding(), fail_at=2)
    reader, opener = ado_reader(source, monkeypatch)
    with pytest.raises(ObservationCredentialError):
        reader.collection(reader.base, continuation_paging=True)
    assert len(opener.calls) == 1 and source.calls == 2


def test_ado_refuses_noncanonical_authentication_before_emitting(monkeypatch):
    reader, opener = ado_reader(Source(ado_binding("basic-pat")), monkeypatch)
    with pytest.raises(ObservationCredentialError, match="binding mismatch"):
        reader.pr()
    assert not opener.calls


def test_ado_refuses_other_endpoint_before_acquiring_or_emitting(monkeypatch):
    source = Source(ado_binding())
    reader, opener = ado_reader(source, monkeypatch)
    with pytest.raises(ObservationCredentialError):
        reader.json("https://other.example/data")
    assert source.calls == 0 and not opener.calls


def test_selected_ado_auth_loss_is_retryable_and_sanitized(monkeypatch):
    reader, opener = ado_reader(Source(ado_binding()), monkeypatch)

    def refused(request, **_):
        raise HTTPError(request.full_url, 401, "synthetic-1", {}, None)

    monkeypatch.setattr(opener, "open", refused)
    with pytest.raises(ObservationCredentialError) as error:
        reader.collection(reader.base)
    assert error.value.transient
    assert "synthetic-1" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize("provider", ["github", "azure-devops"])
def test_selected_fetch_never_resolves_configured_or_ambient_account(monkeypatch, provider):
    source = Source()
    captured = []
    monkeypatch.setattr(pr_watch, "account_token_for_slug", lambda *_: pytest.fail("account fallback"))
    monkeypatch.setattr(
        pr_watch, "configured_snapshot",
        lambda *args, **kwargs: captured.append(kwargs),
    )
    prcfg = config.PRConfig(provider=provider, token_command="must-not-run")
    fetch = pr_watch.build_fetch(prcfg, "owner/repository", 7, credential_source=source)
    fetch()
    fetch()
    assert len(captured) == 2
    assert all(row["credential_source"] is source and "token" not in row for row in captured)
    assert source.calls == 0


def test_unsupported_provider_and_explicit_token_conflicts_refuse_before_effects():
    with pytest.raises(ProviderError):
        pr_watch.build_fetch(config.PRConfig(provider="gitea"), "owner/repository", 7,
                             credential_source=Source())
    with pytest.raises(ProviderError):
        pr_watch.build_fetch(config.PRConfig(provider="github"), "owner/repository", 7,
                             token="explicit", credential_source=Source())
    with pytest.raises(ProviderError):
        configured_snapshot(SimpleNamespace(name="gitea"), "owner/repository", 7,
                            prcfg=config.PRConfig(), credential_source=Source())


def test_disabled_native_ado_cannot_fall_back_to_ambient_az(monkeypatch):
    provider = AzureDevOpsProvider()
    monkeypatch.setattr(provider, "get_snapshot", lambda *_a, **_kw: pytest.fail("ambient az"))
    with pytest.raises(ProviderError, match="native ADO observation"):
        provider.get_configured_snapshot(
            "project/repository", 7, api_base="https://dev.azure.test/example",
            observation=ObservationConfig(enabled=False), credential_source=Source(ado_binding()),
        )


def test_configured_native_ado_carries_the_source_to_the_actual_request(monkeypatch):
    source = Source(ado_binding())
    provider = AzureDevOpsProvider()
    calls = []
    monkeypatch.setattr(provider, "_auth_header", lambda *_: pytest.fail("ambient ADO auth"))

    def open_request(request, **_):
        calls.append(request)
        return Response({
            "status": "completed", "lastMergeSourceCommit": {"commitId": "head"},
            "repository": {"name": "repository", "project": {"name": "project", "id": "project-id"}},
        })

    monkeypatch.setattr(
        azure_observation_reads, "build_opener",
        lambda *_: SimpleNamespace(open=open_request),
    )
    snapshot = configured_snapshot(
        provider, "project/repository", 7, prcfg=config.PRConfig(provider="azure-devops"),
        api_base="https://dev.azure.test/example", credential_source=source,
    )
    assert snapshot.merged and snapshot.head_sha == "head"
    assert source.calls == len(calls) == 1
    scheme, material = calls[0].get_header("Authorization").split(" ", 1)
    assert scheme == "Bear" + "er" and material == "synthetic-1"


def test_ado_build_diagnostic_catch_cannot_swallow_credential_loss(monkeypatch):
    from test_azure_pr_observation import Reader, policy, read
    from agent_worktrees.providers.azure_observation_reads import Collection

    reader = Reader()
    failed = policy(state="rejected")
    failed["context"]["buildId"] = 42
    reader.policy_rows = Collection((failed,))

    def lost(_):
        raise ObservationCredentialError("source disconnected", transient=True)

    monkeypatch.setattr(reader, "build", lost)
    with pytest.raises(ObservationCredentialError):
        read(reader)
    assert reader.calls.count("pr") == 1
