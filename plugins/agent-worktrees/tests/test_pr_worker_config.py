"""Resolved host policy reaches a fresh worker through the public CLI."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from agent_worktrees import config as cfg, git_ops, pr_cli, pr_read_context
from agent_worktrees.pr_observation_config import parse_observation
from agent_worktrees.pr_publication_config import parse_publication_checks
from agent_procutil import no_window_kwargs


POLICY = {
    "publication_origin": {"id": "control-plane", "labels": ["source:control-plane"]},
    "provider": "azure-devops",
    "repository": "project/repo",
    "api_base": "https://dev.azure.com/example",
    "token_command": "do-not-export",
    "branch_update_strategy": "merge",
    "merge_strategy": "squash",
    "review_blocking": True,
    "dismiss_stale_reviews": False,
    "hold_labels": ["paused"],
    "wip_title_prefixes": ["WIP:"],
    "publication_checks": {
        "features": {"self_review": True, "scope_report": True},
        "review_lenses_path": ".checks/lenses",
        "allowed_paths": ["a.txt"],
        "guidance": "Review the exact diff before publication.",
    },
    "observation": {
        "features": {"reviews": True, "comments": True, "policies": False, "builds": True},
        "requirements": {"minimum_approvals": 2, "non_author": True, "approval_after_push": True},
        "policy_rules": [{"name": "Composite", "kind": "composite"}],
        "guidance": "Keep native human requirements separate.",
    },
}


def git(root, *args):
    return git_ops.git(*args, cwd=root, isolated_repository=True, timeout=15, kill_tree=True).stdout.strip()


@pytest.fixture
def host(tmp_path, monkeypatch):
    home = tmp_path / "host-home"
    home.mkdir()
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setattr(cfg, "_control_plane_related_pr_map", lambda: {})
    source = tmp_path / "controller"
    source.mkdir()
    path = tmp_path / "host.json"
    path.write_text(json.dumps({
        "repo_name": "controller",
        "repos": {
            "controller": {"anchor": str(source)},
            "target": {"pr": POLICY},
        },
    }), "utf-8")
    return path


def export(host, destination, workspace, base="origin/main", repository="project/repo"):
    return pr_cli.cmd_pr_watch_dispatch([
        "checks", repository, "export-config", "--config", str(host),
        "--workspace", workspace, "--base", base, "--output", str(destination), "--json",
    ])


def test_explicit_origin_export_needs_no_target_policy_graft(host, tmp_path, capsys):
    data = json.loads(host.read_text("utf-8"))
    del data["repos"]["target"]["pr"]["publication_checks"]
    del data["repos"]["target"]["pr"]["publication_origin"]
    host.write_text(json.dumps(data), "utf-8")
    destination = tmp_path / "origin-worker.json"
    assert pr_cli.cmd_pr_watch_dispatch([
        "checks", "project/repo", "export-config", "--config", str(host),
        "--workspace", "/worker/repo", "--base", "origin/main", "--output", str(destination),
        "--publication-origin", "control-plane", "--publication-label", "source:control-plane", "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    worker = json.loads(destination.read_text("utf-8"))["repos"]["pr-worker"]["pr"]
    assert worker["publication_origin"] == POLICY["publication_origin"]
    assert worker["publication_checks"] is None
    assert "publication_origin_active" not in worker


@pytest.mark.parametrize("workspace", ["/worker/repo", "C:\\worker\\repo"])
def test_export_uses_effective_policy_without_host_paths_or_credentials(host, tmp_path, workspace, capsys):
    output = tmp_path / "worker.json"
    assert export(host, output, workspace) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["repository"] == "project/repo"
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    raw = json.loads(output.read_text("utf-8"))
    assert set(raw) == {"repo_name", "repos"}
    repo = raw["repos"][raw["repo_name"]]
    assert repo["anchor"] == workspace
    assert repo["remote"] == "origin" and repo["default_branch"] == "main"
    assert parse_publication_checks(repo["pr"]["publication_checks"]) == parse_publication_checks(
        POLICY["publication_checks"],
    )
    assert parse_observation(repo["pr"]["observation"]) == parse_observation(POLICY["observation"])
    assert repo["pr"]["provider"] == "azure-devops"
    assert repo["pr"]["repository"] == "project/repo"
    assert repo["pr"]["api_base"] == POLICY["api_base"]
    assert repo["pr"]["review_blocking"] is True
    assert repo["pr"]["dismiss_stale_reviews"] is False
    assert repo["pr"]["hold_labels"] == POLICY["hold_labels"]
    assert repo["pr"]["wip_title_prefixes"] == POLICY["wip_title_prefixes"]
    assert repo["pr"]["publication_origin"] == POLICY["publication_origin"]
    assert "publication_origin_active" not in repo["pr"]
    text = output.read_text("utf-8")
    assert "do-not-export" not in text and str(host.parent) not in text
    assert "machine" not in raw and "token_command" not in repo["pr"]


@pytest.mark.parametrize("workspace", ["relative", "../worker", "/worker/../other"])
def test_export_refuses_nonportable_or_ambiguous_workspace(host, tmp_path, workspace, capsys):
    output = tmp_path / "worker.json"
    assert export(host, output, workspace) == 2
    assert not output.exists()
    assert not json.loads(capsys.readouterr().out)["ok"]


@pytest.mark.parametrize("base", ["main", "another/main", "origin/", "origin/main\nother"])
def test_export_requires_the_selected_remote_base(host, tmp_path, base, capsys):
    output = tmp_path / "worker.json"
    assert export(host, output, "/worker/repo", base=base) == 2
    assert not output.exists()
    assert not json.loads(capsys.readouterr().out)["ok"]


@pytest.mark.parametrize("endpoint", [
    "https://example:placeholder@example.invalid",
    "https://example.invalid/?private=value",
    "https://example.invalid/#private",
])
def test_export_refuses_endpoint_credentials_or_query_data(host, tmp_path, endpoint, capsys):
    data = json.loads(host.read_text("utf-8"))
    data["repos"]["target"]["pr"]["api_base"] = endpoint
    host.write_text(json.dumps(data), "utf-8")
    output = tmp_path / "worker.json"
    assert export(host, output, "/worker/repo") == 2
    assert not output.exists()
    assert not json.loads(capsys.readouterr().out)["ok"]


@pytest.mark.parametrize("endpoint", [
    "reader:fixture-value@forge.invalid", "//reader:fixture-value@forge.invalid",
    "forge.invalid?private=value", "forge.invalid#private", "forge.invalid:invalid",
])
def test_github_export_rejects_schemeless_endpoint_data(host, tmp_path, capsys, endpoint):
    data = json.loads(host.read_text("utf-8"))
    data["repos"]["target"]["pr"].update(provider="github", api_base=endpoint)
    host.write_text(json.dumps(data), "utf-8")
    output = tmp_path / "worker.json"
    assert export(host, output, "/worker/repo") == 2
    assert not output.exists()
    assert not json.loads(capsys.readouterr().out)["ok"]


@pytest.mark.parametrize(("argv", "allowed"), [
    (["pr-watch", "checks", "project/repo", "lenses", "--config", "policy", "--workspace", "repo"], True),
    (["pr-watch", "checks", "project/repo", "lenses", "--config=policy", "--workspace=repo"], True),
    (["pr-watch", "checks", "project/repo", "lenses", "--workspace", "repo"], False),
    (["pr-watch", "checks", "project/repo", "lenses", "--config", "policy"], False),
    (["pr-watch", "wait", "project/repo", "1", "--config", "policy", "--workspace", "repo"], False),
    (["pr-watch", "subscribe", "project/repo", "1", "--config", "policy", "--workspace", "repo"], False),
])
def test_explicit_worker_context_does_not_admit_stateful_operations(argv, allowed):
    from agent_worktrees.front_door_cli import _is_no_project_invocation

    assert _is_no_project_invocation(argv) is allowed


@pytest.mark.parametrize("contents", [None, "{", "[]", "{}", "null", "\x80"])
def test_invalid_explicit_config_cannot_use_an_ambient_policy(tmp_path, monkeypatch, contents):
    path = tmp_path / "explicit.json"
    if contents is not None:
        path.write_bytes(contents.encode("latin-1"))
    calls = []
    monkeypatch.setattr(cfg, "load_config", lambda path: calls.append(path))
    with pytest.raises(ValueError, match="explicit PR configuration"):
        pr_read_context.load_read_config(path)
    assert not calls


@pytest.mark.parametrize("repos", [None, [], {}, {"target": []}, {"target": None}, {"target": "bad"}])
def test_malformed_explicit_repos_cannot_fall_back_to_registered_policy(
    tmp_path, monkeypatch, repos,
):
    path = tmp_path / "explicit.json"
    path.write_text(json.dumps({"repo_name": "target", "repos": repos}), "utf-8")
    calls = []
    monkeypatch.setattr(cfg, "_resolve_anchor_from_registry",
                        lambda *args: calls.append(args) or str(tmp_path))
    with pytest.raises(ValueError, match="explicit PR configuration repos"):
        pr_read_context.load_read_config(path)
    assert not calls


@pytest.mark.parametrize(("title", "labels", "blocked"), [
    ("Ready", (), False), ("Ready", ("PAUSED",), True), ("wip: change", (), True),
])
def test_worker_preserves_publication_holds(host, tmp_path, capsys, title, labels, blocked):
    from agent_worktrees.pr_observation import Observation, configured_snapshot
    from agent_worktrees.pr_snapshot import PRSnapshot

    output = tmp_path / "worker.json"
    assert export(host, output, "/worker/repo") == 0
    capsys.readouterr()
    loaded = pr_read_context.load_read_config(output).default_repo.pr

    class Driver:
        def get_configured_snapshot(self, *args, **kwargs):
            evidence = Observation("azure-devops", "project/repo", 1, POLICY["api_base"], "abc")
            return PRSnapshot(title=title, labels=labels, observation=evidence)

    snapshot = configured_snapshot(Driver(), "project/repo", 1, prcfg=loaded)
    gate = next(g for g in snapshot.observation.gates if g.id == "publication_ready")
    assert (gate.state == "blocked") is blocked


def test_export_does_not_replace_existing_file(host, tmp_path, capsys):
    output = tmp_path / "worker.json"
    output.write_text("previous", "utf-8")
    assert export(host, output, "/worker/repo") == 2
    assert output.read_text("utf-8") == "previous"
    assert not json.loads(capsys.readouterr().out)["ok"]


def test_missing_publication_policy_does_not_export_a_default(host, tmp_path, capsys):
    data = json.loads(host.read_text("utf-8"))
    del data["repos"]["target"]["pr"]["publication_checks"]
    del data["repos"]["target"]["pr"]["publication_origin"]
    host.write_text(json.dumps(data), "utf-8")
    output = tmp_path / "worker.json"
    assert export(host, output, "/worker/repo") == 2
    assert not output.exists()
    assert not json.loads(capsys.readouterr().out)["ok"]


@pytest.fixture
def worker(tmp_path):
    worker = tmp_path / "worker"
    worker.mkdir()
    git(worker, "init", "-q", "-b", "main")
    git(worker, "config", "user.name", "Test")
    git(worker, "config", "user.email", "test@example.invalid")
    git(worker, "config", "core.autocrlf", "false")
    git(worker, "remote", "add", "origin", "https://dev.azure.com/example/project/_git/_optimized/repo")
    git(worker, "remote", "set-url", "--push", "origin", "https://dev.azure.com/example/project/_git/repo")
    lens = worker / ".checks" / "lenses" / "runtime"
    lens.mkdir(parents=True)
    (lens / "SKILL.md").write_text("Review runtime changes.\n", "utf-8")
    (worker / "a.txt").write_text("one\n", "utf-8")
    git(worker, "add", ".")
    git(worker, "commit", "-qm", "base")
    git(worker, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(worker, "checkout", "-qb", "change")
    (worker / "a.txt").write_text("two\n", "utf-8")
    git(worker, "commit", "-qam", "change")
    return worker


@pytest.mark.parametrize("provider", ["azure-devops", "gitea", "azure-encoded", "github-ssh443"])
def test_exported_config_runs_all_checks_from_fresh_worker_context(host, tmp_path, worker, capsys, provider):
    repository = "project/repo"
    if provider == "gitea":
        data = json.loads(host.read_text("utf-8"))
        policy = data["repos"]["target"]["pr"]
        policy.update(provider="gitea", api_base="https://forge.invalid/gitea")
        policy["publication_checks"]["ssh_url"] = "git@forge.invalid:project/repo.git"
        host.write_text(json.dumps(data), "utf-8")
        git(worker, "remote", "set-url", "--push", "origin", policy["publication_checks"]["ssh_url"])
    elif provider == "azure-encoded":
        repository = "My Project/My Repo"
        data = json.loads(host.read_text("utf-8"))
        data["repos"]["target"]["pr"]["repository"] = repository
        host.write_text(json.dumps(data), "utf-8")
        git(worker, "remote", "set-url", "--push", "origin",
            "https://dev.azure.com/example/My%20Project/_git/My%20Repo")
    elif provider == "github-ssh443":
        data = json.loads(host.read_text("utf-8"))
        data["repos"]["target"]["pr"].update(provider="github", api_base="github.com")
        host.write_text(json.dumps(data), "utf-8")
        git(worker, "remote", "set-url", "--push", "origin",
            "ssh://git@ssh.github.com:443/project/repo.git")
    output = tmp_path / "worker.json"
    assert export(host, output, str(worker), repository=repository) == 0
    capsys.readouterr()
    home = tmp_path / "worker-home"
    home.mkdir()
    env = dict(os.environ, AGENT_HOME=str(home))
    command = [
        sys.executable, "-m", "agent_worktrees", "pr-watch", "checks", repository,
    ]
    missing = subprocess.run(
        [*command, "lenses", "--workspace", str(worker), "--json"],
        cwd=worker, env=env, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert missing.returncode != 0
    for operation, options in (
        ("lenses", []),
        ("record-self-review", ["--lenses", "runtime", "--summary", "reviewed"]),
        ("verify-self-review", []),
        ("scope-report", []),
    ):
        result = subprocess.run(
            [*command, operation, "--workspace", str(worker), "--config", str(output), "--json", *options],
            cwd=worker, env=env, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["ok"]


def test_explicit_worker_policy_precedes_a_conflicting_registered_target(
    host, tmp_path, worker, monkeypatch, capsys,
):
    from types import SimpleNamespace
    from agent_worktrees import pr_watch, repos
    from agent_worktrees.pr_snapshot import PRSnapshot

    output = tmp_path / "worker.json"
    assert export(host, output, str(worker)) == 0
    capsys.readouterr()
    ambient = cfg.RepoConfig(
        anchor=str(worker), worktree_root="",
        pr=cfg._parse_pr({"provider": "azure-devops", "repository": "project/repo",
                         "api_base": POLICY["api_base"], "publication_checks": {
                             "features": {"self_review": False, "scope_report": False},
                         }}),
    )
    monkeypatch.setattr(repos, "find_repo", lambda name: None)
    monkeypatch.setattr(repos, "list_repos", lambda: [
        SimpleNamespace(name="ambient", remote="https://dev.azure.com/example/project/_git/repo"),
    ])
    calls = []
    monkeypatch.setattr(cfg, "load_project_config",
                        lambda name: calls.append(name) or SimpleNamespace(repos={"ambient": ambient}))
    for operation, options in (
        ("lenses", []),
        ("record-self-review", ["--lenses", "runtime", "--summary", "reviewed"]),
        ("verify-self-review", []),
        ("scope-report", []),
    ):
        assert pr_cli.cmd_pr_watch_dispatch([
            "checks", "project/repo", operation, "--workspace", str(worker),
            "--config", str(output), "--json", *options,
        ]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["ok"]
        if operation == "lenses":
            assert result["lenses"] == ["runtime"]
    observed = []
    monkeypatch.setattr(pr_watch, "build_fetch",
                        lambda policy, *args, **kwargs: observed.append(policy) or (lambda: PRSnapshot()))
    assert pr_cli.cmd_pr_watch_dispatch([
        "snapshot", "project/repo", "1", "--config", str(output), "--json",
    ]) == 0
    capsys.readouterr()
    assert observed[0].publication_checks == parse_publication_checks(POLICY["publication_checks"])
    assert not calls


def test_export_pins_github_host_across_fresh_worker_environments(
    host, tmp_path, worker, monkeypatch, capsys,
):
    data = json.loads(host.read_text("utf-8"))
    data["repos"]["target"]["pr"].update(provider="github", api_base="")
    host.write_text(json.dumps(data), "utf-8")
    monkeypatch.setenv("GH_HOST", "forge.invalid")
    output = tmp_path / "worker.json"
    assert export(host, output, str(worker)) == 0
    capsys.readouterr()
    assert json.loads(output.read_text("utf-8"))["repos"]["pr-worker"]["pr"]["api_base"] == "forge.invalid"
    home = tmp_path / "isolated-home"
    home.mkdir()
    for endpoint, expected in (("forge.invalid", 0), ("github.com", 2)):
        git(worker, "remote", "set-url", "--push", "origin", f"https://{endpoint}/project/repo.git")
        result = subprocess.run(
            [sys.executable, "-m", "agent_worktrees", "pr-watch", "checks", "project/repo",
             "lenses", "--workspace", str(worker), "--config", str(output), "--json"],
            cwd=worker, env=dict(os.environ, AGENT_HOME=str(home), GH_HOST="github.com"),
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert result.returncode == expected, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["different", "additional", "organization", "host"])
def test_worker_rejects_a_different_push_destination(host, tmp_path, worker, capsys, mode):
    git(worker, "remote", "set-url", "origin", "https://dev.azure.com/example/project/_git/repo")
    args = ["remote", "set-url", *(["--add"] if mode == "additional" else []), "--push"]
    url = {
        "organization": "https://dev.azure.com/another/project/_git/repo",
        "host": "https://elsewhere.invalid/example/project/_git/repo",
    }.get(mode, "https://dev.azure.com/example/project/_git/other")
    git(worker, *args, "origin", url)
    output = tmp_path / "worker.json"
    assert export(host, output, str(worker)) == 0
    capsys.readouterr()
    home = tmp_path / "isolated-home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "pr-watch", "checks", "project/repo",
         "lenses", "--workspace", str(worker), "--config", str(output), "--json"],
        cwd=worker, env=dict(os.environ, AGENT_HOME=str(home)), capture_output=True,
        text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 2
    expected = "exactly one push URL" if mode == "additional" else "workspace remote does not match"
    assert expected in json.loads(result.stdout)["error"]


@pytest.mark.parametrize(("provider", "endpoint", "repository", "remote", "matches"), [
    ("azure-devops", "https://dev.azure.com/example", "project/repo",
     "git@ssh.dev.azure.com:v3/example/project/repo", True),
    ("azure-devops", "https://dev.azure.com/example", "project/repo",
     "git@ssh.dev.azure.com:v3/another/project/repo", False),
    ("azure-devops", "https://example.visualstudio.com", "project/repo",
     "git@ssh.dev.azure.com:v3/example/project/repo", True),
    ("azure-devops", "https://dev.azure.com/example", "project/repo",
     "https://example.visualstudio.com/project/_git/repo", True),
    ("azure-devops", "https://dev.azure.com/example", "project/repo",
     "https://example.visualstudio.com/DefaultCollection/project/_git/repo", True),
    ("azure-devops", "https://example.visualstudio.com", "project/repo",
     "https://dev.azure.com/another/project/_git/repo", False),
    ("azure-devops", "https://dev.azure.com/example", "project/repo",
     "http://example.visualstudio.com/project/_git/repo", False),
    ("azure-devops", "https://dev.azure.com/example", "My Project/My Repo",
     "https://dev.azure.com/example/My%20Project/_git/My%20Repo", True),
    ("azure-devops", "https://example.visualstudio.com", "My Project/My Repo",
     "git@ssh.dev.azure.com:v3/example/My Project/My%20Repo", True),
    ("azure-devops", "https://dev.azure.com/example", "My%20Project/repo",
     "https://dev.azure.com/example/My%2520Project/_git/repo", True),
    ("azure-devops", "https://dev.azure.com/example", "My%20Project/repo",
     "https://dev.azure.com/example/My%20Project/_git/repo", False),
    ("azure-devops", "https://dev.azure.com/example", "project/repo",
     "git@ssh.dev.azure.com:v3/example/project%2Frepo", False),
    ("azure-devops", "https://dev.azure.com/example", "project/a%2Fb",
     "https://dev.azure.com/example/project/_git/a%252Fb", True),
    ("azure-devops", "https://dev.azure.com/example", "project/a%2Fb",
     "https://dev.azure.com/example/project/_git/a%2Fb", False),
    ("github", "", "example/repo", "git@github.com:example/repo.git", True),
    ("github", "github.com", "example/repo", "ssh://git@ssh.github.com:443/example/repo.git", True),
    ("github", "github.com:443", "example/repo", "ssh://git@ssh.github.com:443/example/repo.git", True),
    ("github", "github.com", "example/repo", "ssh://git@ssh.github.com:22/example/repo.git", False),
    ("github", "github.com", "example/repo", "https://ssh.github.com:443/example/repo.git", False),
    ("github", "forge.invalid", "example/repo", "ssh://git@ssh.github.com:443/example/repo.git", False),
    ("github", "github.com", "example/repo", "ssh://git@other.invalid:443/example/repo.git", False),
    ("github", "github.com", "example/repo", "ssh://git@ssh.github.com:443/example/other.git", False),
    ("github", "", "example/repo", "https://other.invalid/example/repo.git", False),
    ("github", "https://forge.invalid", "example/repo", "ssh://git@forge.invalid/example/repo", True),
    ("gitea", "https://forge.invalid/prefix", "example/repo",
     "ssh://git@forge.invalid/prefix/example/repo.git", True),
    ("gitea", "https://forge.invalid/prefix", "example/repo",
     "https://forge.invalid/another/example/repo", False),
])
def test_remote_binding_includes_provider_endpoint(provider, endpoint, repository, remote, matches):
    from agent_worktrees.pr_worker_config import matches_remote

    policy = cfg._parse_pr({"provider": provider, "api_base": endpoint})
    assert matches_remote(remote, repository, policy, "/worker/repo") is matches


@pytest.mark.parametrize("remote", [
    "git@forge.invalid:project/repo.git",
    "ssh://git@forge.invalid/project/repo",
])
def test_gitea_ssh_subpath_binding_survives_export(host, tmp_path, capsys, remote):
    from agent_worktrees.pr_worker_config import matches_remote

    data = json.loads(host.read_text("utf-8"))
    policy = data["repos"]["target"]["pr"]
    policy.update(provider="gitea", api_base="https://forge.invalid/gitea")
    host.write_text(json.dumps(data), "utf-8")
    without_binding = tmp_path / "unbound.json"
    assert export(host, without_binding, "/worker/repo") == 0
    capsys.readouterr()
    unbound = pr_read_context.load_read_config(without_binding).default_repo.pr
    assert not matches_remote(remote, "project/repo", unbound, "/worker/repo")
    policy["publication_checks"]["ssh_url"] = "git@forge.invalid:project/repo.git"
    host.write_text(json.dumps(data), "utf-8")
    output = tmp_path / "worker.json"
    assert export(host, output, "/worker/repo") == 0
    capsys.readouterr()
    loaded = pr_read_context.load_read_config(output).default_repo.pr
    assert loaded.publication_checks.ssh_url == policy["publication_checks"]["ssh_url"]
    assert matches_remote(remote, "project/repo", loaded, "/worker/repo")
    assert not matches_remote(
        "git@another.invalid:project/repo.git", "project/repo", loaded, "/worker/repo",
    )
    assert not matches_remote(
        "git@forge.invalid:project/other.git", "project/repo", loaded, "/worker/repo",
    )
    assert not matches_remote(
        "https://forge.invalid/project/repo.git", "project/repo", loaded, "/worker/repo",
    )
    assert not matches_remote(
        "ftp://forge.invalid/project/repo.git", "project/repo", loaded, "/worker/repo",
    )
