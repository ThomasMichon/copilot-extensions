"""Bounded read credentials and native inventory without ambient account fallback."""

import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import pr_inventory_cli, pr_cli
from agent_worktrees.providers import gitea, github, observation_inventory
from agent_worktrees.providers.observation_credentials import ObservationCredentialError


def source(provider, endpoint, calls):
    def acquire():
        calls.append("credential")
        return SimpleNamespace(
            token=f"synthetic-{len(calls)}", expires_at=time.time() + 60,
            forge=SimpleNamespace(
                provider=provider, api_base=endpoint, repository="owner/repo",
                permission="observe", authentication="bearer",
            ),
        )
    return acquire


@pytest.mark.parametrize("kind", ["github", "gitea"])
def test_paginated_inventory_renews_and_preserves_partial_pages(monkeypatch, kind):
    calls, requests = [], []
    provider = github.GitHubProvider() if kind == "github" else gitea.GiteaProvider()
    endpoint = "https://api.github.com" if kind == "github" else "https://forge.example"
    pages = iter(['[{"number":7,"state":"open"}]', "not JSON"])

    def run(argv, **kwargs):
        requests.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, next(pages) + (
            "\n200" if kind == "gitea" else ""
        ), "")

    monkeypatch.setattr(observation_inventory, "run_cli", run)
    monkeypatch.setattr(gitea, "run_cli", run)
    result = observation_inventory.inventory(
        provider, "owner/repo", api_base="https://github.com" if kind == "github" else endpoint,
        credential_source=source(kind, endpoint, calls),
    )
    assert result["numbers"] == [7]
    assert result["outcome"] == "partial" and result["pages"] == 1
    assert calls == ["credential", "credential"]
    assert all("page=" in " ".join(argv) for argv, _ in requests)
    assert all("--paginate" not in argv for argv, _ in requests)
    if kind == "gitea":
        scheme = "Bear" + "er "
        assert requests[0][1]["input_text"] == "Authorization: " + scheme + "synthetic-1\n"
        assert "synthetic-1" not in " ".join(requests[0][0])
    assert all(kwargs["max_stdout_bytes"] <= 1_048_584 for _, kwargs in requests)


@pytest.mark.parametrize("kind", ["github", "gitea"])
@pytest.mark.parametrize("pages,expected,numbers", [
    (["[]"], "complete", []),
    (["failure"], "unavailable", []),
    (['[{"number":7,"state":"open"}]', "[]"], "complete", [7]),
    (['[{"number":7,"state":"open"}]'] * 2, "partial", [7]),
])
def test_complete_empty_unavailable_and_duplicate_pages(monkeypatch, kind, pages, expected, numbers):
    provider = github.GitHubProvider() if kind == "github" else gitea.GiteaProvider()
    endpoint = "https://api.github.com" if kind == "github" else "https://forge.example"
    replies = iter(pages)

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, next(replies) + ("\n200" if kind == "gitea" else ""), "",
        )

    monkeypatch.setattr(observation_inventory, "run_cli", run)
    monkeypatch.setattr(gitea, "run_cli", run)
    result = observation_inventory.inventory(
        provider, "owner/repo", api_base="https://github.com" if kind == "github" else endpoint,
        credential_source=source(kind, endpoint, []),
    )
    assert result["outcome"] == expected and result["numbers"] == numbers


def test_gitea_snapshot_renews_every_read_and_auth_loss_aborts(monkeypatch):
    calls, requests = [], []
    replies = iter([
        json.dumps({"state": "open", "head": {"sha": "abc"}, "base": {"ref": "dev"}}),
        "[]", json.dumps({"statuses": [], "state": "pending"}),
    ])

    def run(argv, **kwargs):
        requests.append(argv)
        return subprocess.CompletedProcess(argv, 0, next(replies) + "\n200", "")

    monkeypatch.setattr(gitea, "run_cli", run)
    provider = gitea.GiteaProvider()
    snap = provider.get_snapshot(
        "owner/repo", 7, api_base="https://forge.example",
        credential_source=source("gitea", "https://forge.example", calls),
    )
    assert snap.head_sha == "abc" and snap.checks_state == "none"
    assert calls == ["credential"] * 3 and len(requests) == 3
    attempts = 0

    def lost(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise ObservationCredentialError("lost")

    monkeypatch.setattr(provider, "_curl", lost)
    with pytest.raises(ObservationCredentialError):
        provider._curl_with_retry("GET", "https://forge.example", "unused")
    assert attempts == 1


def test_caps_and_wrong_credential_binding_are_not_complete(monkeypatch):
    monkeypatch.setattr(observation_inventory, "run_cli", lambda argv, **_: (
        subprocess.CompletedProcess(argv, 0, '[{"number":7,"state":"open"}]', "")
    ))
    provider = github.GitHubProvider()
    result = observation_inventory.inventory(
        provider, "owner/repo", api_base="", max_pulls=1,
        credential_source=source("github", "https://api.github.com", []),
    )
    assert result["outcome"] == "partial" and result["error"] == "pull_limit"
    result = observation_inventory.inventory(
        provider, "owner/repo", api_base="",
        credential_source=source("gitea", "https://api.github.com", []),
    )
    assert result["outcome"] == "unavailable" and not result["numbers"]


@pytest.mark.parametrize("http_status", [401, 403])
def test_gitea_final_status_authorization_loss_aborts_snapshot(monkeypatch, http_status):
    replies = iter([
        (200, '{"state":"open","head":{"sha":"abc"},"base":{}}'),
        (200, "[]"),
        (http_status, "{}"),
    ])

    def run(argv, **kwargs):
        status, body = next(replies)
        assert kwargs["max_stdout_bytes"] == 1_048_584
        assert kwargs["max_stderr_bytes"] == 8192
        return subprocess.CompletedProcess(argv, 0, body + f"\n{status}", "")

    monkeypatch.setattr(gitea, "run_cli", run)
    with pytest.raises(ObservationCredentialError) as failure:
        gitea.GiteaProvider().get_snapshot(
            "owner/repo", 7, api_base="https://forge.example",
            credential_source=source("gitea", "https://forge.example", []),
        )
    assert failure.value.transient


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_actual_native_capture_refuses_oversized_provider_stream(stream):
    from agent_worktrees.providers.base import run_cli
    result = run_cli(
        [sys.executable, "-c", f"import sys; sys.{stream}.write('x'*1000000)"],
        max_stdout_bytes=128, max_stderr_bytes=128, timeout=5,
    )
    assert result.returncode != 0
    assert len(result.stdout) <= 128 and len(result.stderr) <= 128


def test_native_parser_capability_discovery_is_passive_and_strict(capsys, monkeypatch):
    monkeypatch.setattr(
        observation_inventory, "inventory", lambda *_a, **_kw: pytest.fail("unexpected read"),
    )
    assert pr_cli.cmd_pr_watch_dispatch(["inventory", "--contract", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["providers"] == ["github", "gitea"]
    assert pr_inventory_cli.dispatch(["--unknown"]) == 2
    assert pr_inventory_cli.dispatch(["owner/repo", "--json"]) == 2
    assert pr_inventory_cli.dispatch(["--contract", "--config", "arbitrary"]) == 2


@pytest.mark.parametrize("kind", ["github", "gitea"])
def test_actual_native_entry_and_executing_typed_credential_command(
    tmp_path, monkeypatch, capsys, kind,
):
    from agent_worktrees import __main__ as main

    endpoint = "https://api.github.com" if kind == "github" else "https://forge.example"
    selected = "https://github.com" if kind == "github" else endpoint
    material = {
        "schema": "fleet-manager.forge-credential", "version": 1,
        "token": "synthetic-entry", "expires_at": int(time.time()) + 60,
        "forge": {
            "provider": kind, "api_base": endpoint, "repository": "owner/repo",
            "permission": "observe", "authentication": "bearer",
        },
    }
    helper = tmp_path / "read_credential.py"
    helper.write_text("import json\nprint(json.dumps(" + repr(material) + "))\n")
    (tmp_path / "native.yaml").write_text(json.dumps({"repos": {"target": {"pr": {
        "provider": kind, "api_base": selected, "repository": "owner/repo",
    }}}}))
    monkeypatch.setattr(
        main, "_resolve_active_project", lambda *_args: pytest.fail("native read needs no project"),
    )
    monkeypatch.setattr(
        main, "_ensure_cluster_loaded", lambda: pytest.fail("native read loaded lifecycle"),
    )
    reads = []

    def run(argv, **kwargs):
        reads.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "[]" + ("\n200" if kind == "gitea" else ""), "")

    monkeypatch.setattr(observation_inventory, "run_cli", run)
    monkeypatch.setattr(gitea, "run_cli", run)
    assert main.main([
        "pr-watch", "inventory", "owner/repo", "--config", str(tmp_path / "native.yaml"),
        "--credential-command", json.dumps([sys.executable, str(helper)]), "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "complete"
    assert len(reads) == 1
    if kind == "github":
        assert reads[0][1]["env"]["GH_TOKEN"] == material["token"]
    else:
        scheme = "Bear" + "er "
        assert reads[0][1]["input_text"] == "Authorization: " + scheme + material["token"] + "\n"
        assert material["token"] not in " ".join(reads[0][0])


def test_inventory_real_resolver_refuses_registry_only_target(tmp_path, monkeypatch):
    from agent_worktrees import config, pr_config, pr_related_binding
    path = tmp_path / "native.yaml"
    path.write_text(json.dumps({"repo_name": "ambient", "repos": {"selected": {"pr": {
        "provider": "gitea", "api_base": "https://forge.example", "repository": "owner/selected",
    }}}}))
    monkeypatch.setattr(config, "load_config", lambda *_a, **_kw: pytest.fail("layered inventory load"))
    monkeypatch.setattr(
        pr_config, "resolve_repo_config_for_slug",
        lambda *_args: pytest.fail("selected config fell back to registry"),
    )
    monkeypatch.setattr(
        pr_related_binding, "control_plane_pr_map",
        lambda: pytest.fail("selected config fell back to control plane"),
    )
    assert pr_inventory_cli.dispatch([
        "owner/repo", "--config", str(path),
        "--credential-command", json.dumps([sys.executable]), "--json",
    ]) == 2


@pytest.mark.parametrize("text", [
    'repos: {a: {pr: {provider: github, repository: owner/repo, api_base: https://github.com, api_base: https://other.example}}}',
    '{"repos":{}}',
])
def test_inventory_source_loader_refuses_duplicate_or_empty_binding(tmp_path, text):
    from agent_worktrees.pr_inventory_config import load_binding
    path = tmp_path / "native.yaml"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_binding(path, "owner/repo")


@pytest.mark.parametrize("operation", ["inventory", "snapshot"])
def test_typed_gitea_plaintext_refuses_before_credential_acquisition(monkeypatch, operation):
    calls = []
    credential = source("gitea", "http://forge.example", calls)
    monkeypatch.setattr(gitea, "run_cli", lambda *_a, **_kw: pytest.fail("plaintext transport"))
    if operation == "inventory":
        result = observation_inventory.inventory(
            gitea.GiteaProvider(), "owner/repo", api_base="http://forge.example",
            credential_source=credential,
        )
        assert result["outcome"] == "unavailable"
    else:
        with pytest.raises(ObservationCredentialError):
            gitea.GiteaProvider().get_snapshot(
                "owner/repo", 7, api_base="http://forge.example", credential_source=credential,
            )
    assert calls == []
