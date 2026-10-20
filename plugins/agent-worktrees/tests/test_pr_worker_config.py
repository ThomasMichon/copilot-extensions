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
    "provider": "azure-devops",
    "repository": "project/repo",
    "api_base": "https://dev.azure.com/example",
    "token_command": "do-not-export",
    "branch_update_strategy": "merge",
    "merge_strategy": "squash",
    "review_blocking": True,
    "dismiss_stale_reviews": False,
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


def export(host, destination, workspace, base="origin/main"):
    return pr_cli.cmd_pr_watch_dispatch([
        "checks", "project/repo", "export-config", "--config", str(host),
        "--workspace", workspace, "--base", base, "--output", str(destination), "--json",
    ])


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


def test_export_does_not_replace_existing_file(host, tmp_path, capsys):
    output = tmp_path / "worker.json"
    output.write_text("previous", "utf-8")
    assert export(host, output, "/worker/repo") == 2
    assert output.read_text("utf-8") == "previous"
    assert not json.loads(capsys.readouterr().out)["ok"]


def test_missing_publication_policy_does_not_export_a_default(host, tmp_path, capsys):
    data = json.loads(host.read_text("utf-8"))
    del data["repos"]["target"]["pr"]["publication_checks"]
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


def test_exported_config_runs_all_checks_from_fresh_worker_context(host, tmp_path, worker, capsys):
    output = tmp_path / "worker.json"
    assert export(host, output, str(worker)) == 0
    capsys.readouterr()
    home = tmp_path / "worker-home"
    home.mkdir()
    env = dict(os.environ, AGENT_HOME=str(home))
    command = [
        sys.executable, "-m", "agent_worktrees", "pr-watch", "checks", "project/repo",
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


@pytest.mark.parametrize("mode", ["different", "additional"])
def test_worker_rejects_a_different_push_destination(host, tmp_path, worker, capsys, mode):
    git(worker, "remote", "set-url", "origin", "https://dev.azure.com/example/project/_git/repo")
    args = ["remote", "set-url", *(["--add"] if mode == "additional" else []), "--push"]
    git(worker, *args, "origin", "https://dev.azure.com/example/project/_git/other")
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
