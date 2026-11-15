"""Opt-in native transport routing, renewal and legacy compatibility."""

import json
import subprocess
import sys

import pytest

from agent_worktrees import pr_cli, pr_inventory_cli, pr_credential_command
from agent_worktrees.providers import gitea, github, observation_inventory
from agent_worktrees.providers.observation_credentials import (
    ObservationCredentialError, ObservationCredentials, with_http_profile,
)
from agent_worktrees.providers.observation_http import read_get
from test_pr_observation_credentials import Binding, Source, review


@pytest.mark.parametrize("kind", ["github", "gitea"])
def test_direct_inventory_renews_and_never_uses_ambient_cli_auth(monkeypatch, kind):
    endpoint = "https://api.github.com" if kind == "github" else "https://forge.example"
    source = Source(Binding(provider=kind, api_base=endpoint))
    replies = iter(['[{"number":7,"state":"open"}]', "[]"])
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[0:2] == ["curl", "-q"]
        assert "env" not in kwargs
        assert "synthetic-" not in " ".join(argv)
        scheme = "Bear" + "er "
        assert kwargs["input_text"] == "Authorization: " + scheme + f"synthetic-{len(calls)}\n"
        return subprocess.CompletedProcess(argv, 0, next(replies) + "\n200", "")

    monkeypatch.setattr(observation_inventory, "run_cli", run)
    monkeypatch.setattr(gitea, "run_cli", run)
    provider = github.GitHubProvider() if kind == "github" else gitea.GiteaProvider()
    result = observation_inventory.inventory(
        provider, "owner/repository", api_base="" if kind == "github" else endpoint,
        credential_source=with_http_profile(source, "direct-v1"),
    )
    assert result["numbers"] == [7] and result["outcome"] == "complete"
    assert source.calls == len(calls) == 2
    urls = [next(arg for arg in argv if arg.startswith("https://")) for argv, _ in calls]
    assert all(url.startswith(endpoint + "/") for url in urls)
    assert all(("per_page=50" if kind == "github" else "limit=50") in url for url in urls)


@pytest.mark.parametrize("fail_at", [None, 2, 5])
def test_direct_github_snapshot_renews_each_manual_page(monkeypatch, fail_at):
    source = Source(fail_at=fail_at)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[:2] == ["curl", "-q"] and "--paginate" not in argv
        assert "env" not in kwargs and "synthetic-" not in " ".join(argv)
        scheme = "Bear" + "er "
        assert kwargs["input_text"] == "Authorization: " + scheme + f"synthetic-{len(calls)}\n"
        url = next(arg for arg in argv if arg.startswith("https://"))
        if url.endswith("/pulls/7"):
            body = {"state": "open", "head": {"sha": "head"}, "base": {"ref": "dev"}}
        elif url.endswith("/status"):
            body = {"total_count": 0, "statuses": []}
        elif "/check-runs?" in url:
            offset = 0 if url.endswith("page=1") else 100
            body = {"total_count": 101, "check_runs": [
                {"id": number, "name": "check", "status": "completed", "conclusion": "success"}
                for number in range(offset, min(offset + 100, 101))
            ]}
        else:
            body = [review(number) for number in range(1, 101)] if url.endswith("page=1") else []
        return subprocess.CompletedProcess(argv, 0, json.dumps(body) + "\n200", "")

    monkeypatch.setattr(github, "run_cli", run)
    selected = with_http_profile(source, "direct-v1")
    if fail_at:
        with pytest.raises(ObservationCredentialError):
            github.GitHubProvider().get_snapshot("owner/repository", 7, credential_source=selected)
        assert source.calls == fail_at and len(calls) == fail_at - 1
    else:
        snapshot = github.GitHubProvider().get_snapshot(
            "owner/repository", 7, credential_source=selected,
        )
        assert snapshot.checks_complete and len(snapshot.reviews) == 100
        assert source.calls == len(calls) == 6


@pytest.mark.parametrize("lost", [False, True])
def test_direct_gitea_snapshot_preserves_auth_loss_stop(monkeypatch, lost):
    source = Source(Binding(provider="gitea", api_base="https://forge.example"))
    calls = []
    replies = iter([
        '{"state":"open","head":{"sha":"head"},"base":{}}',
        "[]", '{"statuses":[],"state":"pending"}',
    ])

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[:2] == ["curl", "-q"]
        status = 403 if lost and len(calls) == 2 else 200
        return subprocess.CompletedProcess(argv, 0, next(replies) + f"\n{status}", "")

    monkeypatch.setattr(gitea, "run_cli", run)
    selected = with_http_profile(source, "direct-v1")
    if lost:
        with pytest.raises(ObservationCredentialError, match="authorization lost"):
            gitea.GiteaProvider().get_snapshot(
                "owner/repository", 7, api_base="https://forge.example", credential_source=selected,
            )
        assert source.calls == len(calls) == 2
    else:
        snapshot = gitea.GiteaProvider().get_snapshot(
            "owner/repository", 7, api_base="https://forge.example", credential_source=selected,
        )
        assert snapshot.head_sha == "head" and snapshot.checks_state == "none"
        assert source.calls == len(calls) == 3


@pytest.mark.parametrize("stdout,returncode", [
    ("{}\n401", 0), ("{}\n403", 0), ("{}\nnot-status", 0),
    ("no-status", 0), ("{}\n000", 0), ("x" * 11 + "\n200", 0), ("private-detail", 1),
])
def test_direct_read_failure_is_sanitized_without_retry(stdout, returncode):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, "private-detail")

    with pytest.raises(ObservationCredentialError) as failure:
        read_get("https://forge.example/api", "synthetic-authorization", invoke=run,
                 max_body_bytes=10)
    assert len(calls) == 1
    assert "private-detail" not in str(failure.value)
    assert "synthetic-" not in str(failure.value)


def test_profile_is_optional_and_unsupported_selection_precedes_acquisition():
    source = Source()
    assert with_http_profile(source, "legacy") is source
    with pytest.raises(ValueError, match="HTTP profile"):
        with_http_profile(source, "unknown")
    with pytest.raises(ValueError, match="HTTP profile"):
        ObservationCredentials(
            with_http_profile(source, "direct-v1"), provider="azure-devops",
            api_base="https://dev.azure.test", repository="owner/repository",
        )
    assert source.calls == 0


def test_direct_parser_requires_typed_source_before_loading_config(monkeypatch, capsys):
    from agent_worktrees import pr_read_context
    monkeypatch.setattr(pr_read_context, "load_read_config",
                        lambda *_: pytest.fail("configuration accessed"))
    assert pr_cli.cmd_pr_watch_dispatch([
        "snapshot", "owner/repository", "7", "--http-profile", "direct-v1", "--json",
    ]) == 2
    captured = capsys.readouterr()
    assert "requires --credential-command" in captured.out + captured.err
    assert pr_inventory_cli.dispatch(["--http-profile", "unknown"]) == 2


def test_passive_contract_and_command_source_expose_opt_in_without_acquisition(monkeypatch, capsys):
    monkeypatch.setattr(pr_credential_command, "run_caller_owned",
                        lambda *_a, **_kw: pytest.fail("discovery acquired credentials"))
    assert pr_inventory_cli.dispatch(["--contract", "--json"]) == 0
    declaration = json.loads(capsys.readouterr().out)["observation_http_profiles"]
    assert declaration["direct-v1"]["option"] == "--http-profile"
    assert declaration["direct-v1"]["requires_binary_qualification"] is True
    selected = pr_credential_command.command_source(
        json.dumps([sys.executable]), http_profile="direct-v1",
    )
    assert selected.http_profile == "direct-v1"
