"""Explicit account failures must not turn into another identity's execution."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import config, fork_pr, git_ops, git_push_transport, repos
from agent_worktrees.providers import base
from agent_worktrees.providers.github import GitHubProvider


@pytest.fixture
def registry(monkeypatch):
    state = repos.ReposRegistry()
    monkeypatch.setattr(repos, "read_registry", lambda **kwargs: state)
    monkeypatch.setattr(repos, "find_repo", lambda name: state.repos.get(name))
    monkeypatch.setattr(git_ops, "gh_token_for_account", lambda account: None)
    monkeypatch.setattr(git_ops, "_active_gh_account", lambda: "ambient-reader")
    monkeypatch.setenv("GH_TOKEN", "ambient-reader-token")
    monkeypatch.setenv("GITHUB_TOKEN", "another-ambient-token")
    return state


def pin(registry, source):
    registry.repos["sample"] = repos.RepoEntry(
        name="sample", remote="https://github.com/example-org/sample.git",
        account="selected-writer" if source == "repo" else "",
    )
    if source == "map":
        registry.account_map["example-org"] = "selected-writer"


@pytest.mark.parametrize("source", ["repo", "map"])
@pytest.mark.parametrize("target", ["sample", "example-org/sample"])
def test_dispatcher_refuses_missing_selected_credential(
    registry, monkeypatch, capfd, source, target,
):
    pin(registry, source)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "gh")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))

    assert cli.cmd_repos_dispatch(["gh", target, "--", "issue", "create"]) == 1
    assert calls == []
    text = capfd.readouterr().out
    assert "selected-writer" in text
    assert "refusing ambient" in text
    assert "ambient-reader-token" not in text


def test_derived_owner_missing_credential_keeps_legacy_execution(
    registry, monkeypatch, capfd,
):
    pin(registry, "derived")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "gh")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert cli.cmd_repos_dispatch(["gh", "sample", "--", "issue", "list"]) == 0
    assert calls[0][1]["env"]["GH_TOKEN"] == "ambient-reader-token"
    assert "using ambient auth" in capfd.readouterr().out


def test_successful_scoped_dispatch_overrides_inherited_token(registry, monkeypatch):
    pin(registry, "repo")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(git_ops, "gh_token_for_account", lambda account: "selected-token")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert cli.cmd_repos_dispatch(["gh", "sample", "--", "api", "user"]) == 0
    assert calls[0][1]["env"]["GH_TOKEN"] == "selected-token"
    assert calls[0][0] == ["gh", "api", "user"]


@pytest.mark.parametrize("source", ["repo", "map"])
def test_provider_and_fork_refuse_before_transport(registry, monkeypatch, source):
    pin(registry, source)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("must not execute"),
    )
    prcfg = config.PRConfig(provider="github")
    with pytest.raises(base.ProviderError, match="selected-writer"):
        base.account_token_for_slug("example-org/sample", prcfg)
    with pytest.raises(base.ProviderError, match="selected-writer"):
        fork_pr._resolve_fork_credential("example-org/sample", prcfg)


@pytest.mark.parametrize("operation", ["push", "fetch", "clone"])
def test_git_missing_selected_credential_never_starts_network(
    registry, monkeypatch, operation,
):
    pin(registry, "repo")
    url = "https://github.com/example-org/sample.git"
    monkeypatch.setattr(git_ops, "_remote_url", lambda *args, **kwargs: url)
    monkeypatch.setattr(
        git_ops, "git", lambda *args, **kwargs: pytest.fail("must not execute"),
    )
    if operation == "push":
        result = git_ops.push("origin", "topic", cwd=".")
        assert not result.ok
        assert "selected-writer" in result.stderr
    else:
        with pytest.raises(git_ops.GitError, match="selected-writer"):
            if operation == "fetch":
                git_ops.fetch("origin", cwd=".")
            else:
                git_ops._auth_config_args_for_url(url)


def test_git_exact_repo_does_not_borrow_sibling_identity(registry, monkeypatch):
    pin(registry, "repo")
    registry.repos["sibling"] = repos.RepoEntry(
        name="sibling", remote="https://github.com/example-org/sibling.git",
        account="sibling-writer",
    )
    seen = []
    monkeypatch.setattr(
        git_ops, "gh_token_for_account", lambda account: seen.append(account) or "token",
    )
    git_ops._auth_config_args_for_url("https://github.com/example-org/sibling.git")
    assert seen == ["sibling-writer"]


def test_clone_entrypoint_refuses_instead_of_starting_plain_git(
    registry, monkeypatch, tmp_path, capfd,
):
    pin(registry, "repo")
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("must not clone"),
    )
    assert repos.clone_repo(
        "https://github.com/example-org/sample.git",
        name="sample", target=str(tmp_path / "new-clone"),
    ) is None
    assert "selected-writer" in capfd.readouterr().out
    assert not (tmp_path / "new-clone").exists()


@pytest.mark.parametrize("store_kind", ["lease", "history"])
def test_coordination_store_refuses_before_any_network(
    registry, monkeypatch, tmp_path, store_kind,
):
    from agent_worktrees import claim_history_mirror, lease_store
    from agent_worktrees.lease_config import LeaseSettings

    pin(registry, "repo")
    monkeypatch.setattr(
        git_ops, "_remote_url",
        lambda *args, **kwargs: "https://github.com/example-org/sample.git",
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("must not contact store"),
    )
    settings = LeaseSettings(
        origin="https://github.com/example-org/sample.git",
        auth_remote="origin", auth_cwd=str(tmp_path),
    )
    constructor, error = (
        (lease_store.GitLeaseStore, lease_store.GitError)
        if store_kind == "lease"
        else (claim_history_mirror.ClaimHistoryMirror,
              claim_history_mirror.ClaimHistoryMirrorError)
    )
    with pytest.raises(error, match="selected-writer"):
        constructor(settings)


def test_prune_lookup_refuses_before_provider_request(registry, monkeypatch, capfd):
    pin(registry, "repo")
    monkeypatch.setattr(
        base, "get_provider", lambda name: pytest.fail("must not select a provider"),
    )
    config_value = SimpleNamespace(
        default_repo=SimpleNamespace(pr=config.PRConfig(provider="github")),
    )
    lookup = cli._make_pr_lookup(config_value)
    assert lookup("example-org/sample", 42) is None
    assert "selected-writer" in capfd.readouterr().out


def test_lease_git_transport_uses_shared_headless_launch(registry, monkeypatch):
    from agent_worktrees import lease_store
    from agent_worktrees.lease_config import LeaseSettings

    calls = []

    def run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0, "synthetic output", "")

    monkeypatch.setattr(subprocess, "run", run)
    store = lease_store.GitLeaseStore(LeaseSettings(origin="synthetic-origin"))
    assert store._git(["--version"]).stdout == "synthetic output"
    if sys.platform == "win32":
        assert calls[0]["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_same_active_git_and_provider_paths_are_preserved(registry, monkeypatch):
    pin(registry, "repo")
    monkeypatch.setattr(git_ops, "_active_gh_account", lambda: "SELECTED-WRITER")
    monkeypatch.setattr(
        git_ops, "gh_token_for_account",
        lambda account: pytest.fail("same-active path must not mint a stale user token"),
    )
    assert git_ops._auth_config_args_for_url(
        "https://github.com/example-org/sample.git",
    ) == []
    assert base.account_token_for_slug(
        "example-org/sample", config.PRConfig(provider="github"),
    ) is None


@pytest.mark.parametrize("operation", ["push", "observe"])
def test_rejected_git_credential_never_retries_ambient(
    registry, monkeypatch, operation,
):
    pin(registry, "repo")
    monkeypatch.setattr(git_ops, "gh_token_for_account", lambda account: "selected-token")
    monkeypatch.setattr(
        git_ops, "_remote_url",
        lambda *args, **kwargs: "https://github.com/example-org/sample.git",
    )
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 128, "", "authentication rejected")

    monkeypatch.setattr(git_ops, "git", run)
    if operation == "push":
        result = git_ops.push("origin", "topic", cwd=".")
        assert not result.ok
        assert "authentication rejected" in result.stderr
    else:
        with pytest.raises(OSError, match="Cannot observe"):
            git_push_transport.observed_ref(
                "origin", ("https://github.com/example-org/sample.git",),
                "refs/heads/topic", ".",
            )
    assert len(calls) == 1
    assert any(arg.startswith("http.extraheader=") for arg in calls[0])


@pytest.mark.parametrize("source", ["repo", "map"])
@pytest.mark.parametrize("minted", [None, "selected-token"])
@pytest.mark.parametrize("operation", ["gh", "provider", "fork", "push", "observe"])
def test_pin_removed_during_lookup_does_not_change_selected_policy(
    registry, monkeypatch, capfd, source, minted, operation,
):
    pin(registry, source)
    calls = []
    url = "https://github.com/example-org/sample.git"

    def mint(account):
        assert account == "selected-writer"
        if source == "repo":
            registry.repos["sample"].account = ""
        else:
            registry.account_map.clear()
        return minted

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 1, "", "HTTP 401: Bad credentials")

    monkeypatch.setattr(git_ops, "gh_token_for_account", mint)
    monkeypatch.setattr(git_ops, "_remote_url", lambda *args, **kwargs: url)
    monkeypatch.setattr(git_ops, "git", run)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "gh")
    prcfg = config.PRConfig(provider="github")
    if operation == "gh":
        assert cli.cmd_repos_dispatch(
            ["gh", "example-org/sample", "--", "issue", "create"],
        ) == 1
        if minted:
            assert calls[0][1]["env"]["GH_TOKEN"] == minted
        else:
            assert "selected-writer" in capfd.readouterr().out
    elif operation in ("provider", "fork"):
        resolve = (
            lambda: base.account_token_for_slug("example-org/sample", prcfg)
            if operation == "provider"
            else fork_pr._resolve_fork_credential("example-org/sample", prcfg)
        )
        if minted:
            result = resolve()
            assert result == (minted if operation == "provider" else (minted, "selected-writer"))
        else:
            with pytest.raises(base.ProviderError, match="selected-writer"):
                resolve()
    elif operation == "push":
        assert not git_ops.push("origin", "topic", cwd=".").ok
    else:
        error = OSError if minted else git_ops.GitError
        with pytest.raises(error):
            git_push_transport.observed_ref("origin", (url,), "refs/heads/topic", ".")
    assert repos.account_for_github_slug(
        "example-org/sample", configured_only=True,
    ) is None
    network = operation in ("gh", "push", "observe")
    assert len(calls) == (1 if minted and network else 0)


def test_account_selection_uses_one_registry_snapshot(registry, monkeypatch):
    from agent_worktrees.github_credentials import account_selection

    pin(registry, "repo")
    reads = []

    def read(**kwargs):
        reads.append(kwargs)
        return registry

    monkeypatch.setattr(repos, "read_registry", read)
    selection = account_selection("example-org/sample")
    assert (selection.login, selection.configured) == ("selected-writer", True)
    assert reads == [{"strict": True}]


@pytest.mark.parametrize("kind", ["syntax", "mapping", "unreadable"])
@pytest.mark.parametrize(
    "operation", ["gh", "push", "fetch", "clone", "provider", "fork", "prune", "lease"],
)
def test_registry_failure_uses_existing_error_channel(
    tmp_path, monkeypatch, capfd, kind, operation,
):
    from agent_worktrees import lease_store, repos_registry_reader
    from agent_worktrees.lease_config import LeaseSettings

    path = tmp_path / "repos.yaml"
    path.write_text("repos: [\n" if kind == "syntax" else "[]\n", encoding="utf-8")
    if kind == "unreadable":
        def denied(path):
            raise PermissionError("synthetic registry access denied")

        monkeypatch.setattr("agent_worktrees.repos_registry_transaction.read_bytes", denied)
    monkeypatch.setattr(
        repos, "read_registry", lambda **kwargs: repos_registry_reader.read_registry(path, **kwargs),
    )
    url = "https://github.com/example-org/sample.git"
    monkeypatch.setattr(git_ops, "_remote_url", lambda *args, **kwargs: url)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(
        git_ops, "gh_token_for_account", lambda *args: pytest.fail("must not acquire credentials"),
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch transport"),
    )
    prcfg = config.PRConfig(provider="github")
    if operation == "gh":
        assert cli.cmd_repos_dispatch(
            ["gh", "example-org/sample", "--", "issue", "create"],
        ) == 1
        assert "repository registry" in capfd.readouterr().out
    elif operation == "push":
        result = git_ops.push("origin", "topic", cwd=tmp_path)
        assert not result.ok
        assert "repository registry" in result.stderr
    elif operation == "fetch":
        with pytest.raises(git_ops.GitError, match="repository registry"):
            git_ops.fetch("origin", cwd=tmp_path)
    elif operation == "clone":
        assert repos.clone_repo(url, name="sample", target=str(tmp_path / "clone")) is None
        assert "repository registry" in capfd.readouterr().out
    elif operation in ("provider", "fork"):
        with pytest.raises(base.ProviderError, match="repository registry"):
            if operation == "provider":
                base.account_token_for_slug("example-org/sample", prcfg)
            else:
                fork_pr._resolve_fork_credential("example-org/sample", prcfg)
    elif operation == "prune":
        lookup = cli._make_pr_lookup(
            SimpleNamespace(default_repo=SimpleNamespace(pr=prcfg)),
        )
        assert lookup("example-org/sample", 42) is None
        assert "repository registry" in capfd.readouterr().out
    else:
        with pytest.raises(lease_store.GitError, match="repository registry"):
            lease_store.GitLeaseStore(
                LeaseSettings(origin=url, auth_remote="origin", auth_cwd=str(tmp_path)),
            )


def test_stale_provider_token_fails_without_ambient_retry(registry, monkeypatch):
    pin(registry, "repo")
    monkeypatch.setattr(git_ops, "gh_token_for_account", lambda account: "stale-token")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 1, "", "HTTP 401: Bad credentials")

    monkeypatch.setattr("agent_worktrees.providers.github.run_cli", run)
    token = base.account_token_for_slug(
        "example-org/sample", config.PRConfig(provider="github"),
    )
    with pytest.raises(base.ProviderError, match="HTTP 401"):
        GitHubProvider().create_pull(
            base.PRScope("example-org/sample", "topic", "dev", "Example"), token=token,
        )
    assert len(calls) == 1
    assert calls[0][1]["env"]["GH_TOKEN"] == "stale-token"


@pytest.mark.parametrize("failure", ["timeout", "missing", "nonzero", "empty"])
def test_token_lookup_has_bounded_headless_failure(monkeypatch, failure):
    git_ops._gh_token_for_owner.cache_clear()
    monkeypatch.setattr(git_ops.shutil, "which", lambda name: "gh")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if failure == "missing":
            raise FileNotFoundError("gh")
        return subprocess.CompletedProcess(args, 1 if failure == "nonzero" else 0, "")

    monkeypatch.setattr(subprocess, "run", run)
    try:
        assert git_ops._gh_token_for_owner("selected-writer") is None
        assert calls[0][1]["timeout"] == 60
        assert calls[0][0] == ["gh", "auth", "token", "--user", "selected-writer"]
        if git_ops.sys.platform == "win32":
            assert calls[0][1]["creationflags"] & subprocess.CREATE_NO_WINDOW
    finally:
        git_ops._gh_token_for_owner.cache_clear()


def test_token_lookup_waits_for_slow_local_credential_store(monkeypatch):
    git_ops._gh_token_for_owner.cache_clear()
    monkeypatch.setattr(git_ops.shutil, "which", lambda name: "gh")
    real_run = subprocess.run

    def run(args, **kwargs):
        return real_run(
            [sys.executable, "-I", "-c",
             "import time; time.sleep(11); print('synthetic-credential')"],
            **kwargs,
        )

    monkeypatch.setattr(subprocess, "run", run)
    try:
        assert git_ops._gh_token_for_owner("selected-writer") == "synthetic-credential"
    finally:
        git_ops._gh_token_for_owner.cache_clear()


def test_configured_only_resolution_preserves_exact_repo_and_index(registry):
    pin(registry, "repo")
    registry.repos["derived"] = repos.RepoEntry(
        name="derived", remote="https://github.com/example-org/derived.git",
    )
    registry.account_map["mapped-org"] = "mapped-writer"
    index = repos.AccountLookupIndex.from_registry(registry)
    for selected_index in (None, index):
        for target, expected in (
            ("sample", "selected-writer"),
            ("example-org/sample", "selected-writer"),
            ("example-org/derived", None),
            ("mapped-org/unregistered", "mapped-writer"),
            ("unconfigured-org/unregistered", None),
            ("bare-owner", None),
        ):
            assert repos.account_for_github_slug(
                target, registry=registry, index=selected_index, configured_only=True,
            ) == expected
