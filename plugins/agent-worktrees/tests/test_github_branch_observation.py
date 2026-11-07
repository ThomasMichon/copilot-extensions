"""Exact GitHub branch observations through the provider's scoped transport."""

import json
import subprocess

import pytest

from agent_worktrees import pr_foreign_checks
from agent_worktrees.providers import ProviderError, github


def response(branch="topic/with#separator", sha="A" * 40):
    return {
        "ref": "refs/heads/" + branch,
        "object": {"type": "commit", "sha": sha},
    }


def test_branch_observation_pins_host_token_and_encoded_exact_ref(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(response()), "")

    monkeypatch.setattr(github, "run_cli", run)
    result = pr_foreign_checks.observe(
        github.GitHubProvider(), "example/project", "topic/with#separator",
        api_base="https://forge.example:8443/api/v3", token="synthetic-token",
    )
    assert result == "a" * 40
    assert calls == [(
        ["gh", "api", "--hostname", "forge.example:8443", "--method", "GET",
         "repos/example/project/git/ref/heads/topic%2Fwith%23separator"],
        {"env": {"GH_TOKEN": "synthetic-token", "GH_HOST": "forge.example:8443"}},
    )]


def test_branch_observation_preserves_ambient_auth_and_normalizes_heads(monkeypatch):
    calls = []
    monkeypatch.setenv("GH_HOST", "forge.example")

    def run(args, **kwargs):
        calls.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, json.dumps(response("topic")), "")

    monkeypatch.setattr(github, "run_cli", run)
    assert github.GitHubProvider().observe_branch("example/project", "refs/heads/topic") == {
        "repository": "example/project", "branch": "topic", "head_sha": "a" * 40,
    }
    assert calls == [{"GH_HOST": "forge.example"}]


@pytest.mark.parametrize("payload", [
    "{", "[]", "null", "{}",
    json.dumps(response("topic-extra")),
    json.dumps({"ref": "refs/heads/topic", "object": None}),
    json.dumps({"ref": "refs/heads/topic", "object": {"type": "tag", "sha": "a" * 40}}),
    json.dumps(response("topic", "abc")),
    json.dumps(response("topic", "0" * 40)),
])
def test_branch_observation_rejects_missing_mismatched_or_malformed_tip(monkeypatch, payload):
    monkeypatch.setattr(
        github, "run_cli",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, payload, ""),
    )
    with pytest.raises(ProviderError, match="exact valid branch tip"):
        github.GitHubProvider().observe_branch("example/project", "topic")


@pytest.mark.parametrize("repo,branch", [
    ("example", "topic"), ("example/project/extra", "topic"),
    ("example/project", ""), ("example/project", "refs/tags/topic"),
    ("example/project", "topic\nother"),
])
def test_invalid_branch_scope_refuses_before_transport(monkeypatch, repo, branch):
    monkeypatch.setattr(github, "run_cli", lambda *a, **k: pytest.fail("invalid scope reached gh"))
    with pytest.raises(ProviderError):
        github.GitHubProvider().observe_branch(repo, branch)


def test_branch_transport_failure_is_not_branch_absence(monkeypatch):
    monkeypatch.setattr(
        github, "run_cli",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "HTTP 404: Not Found"),
    )
    with pytest.raises(ProviderError, match="HTTP 404"):
        github.GitHubProvider().observe_branch("example/project", "topic")
