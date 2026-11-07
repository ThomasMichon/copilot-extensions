"""Actual author command parsing retains claimant and per-action outcomes."""
import json

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg, output, pr_author_cli, pr_cli, pr_config, pr_metadata


@pytest.fixture
def out():
    with output.capture_json_output() as capture:
        yield capture


@pytest.fixture
def author(monkeypatch, tmp_path):
    repo = cfg.RepoConfig(
        anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
        pr=cfg.PRConfig(enabled=True, provider="azure-devops", api_base="https://dev.azure.com/example"),
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux", repo_name="caller",
        repos={"caller": repo},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *args: config)
    monkeypatch.setattr(cli.front_door_cli, "resolve_invocation_project", lambda *args: True)
    monkeypatch.setattr(
        pr_config, "resolve_repo_config_for_slug",
        lambda *_: pr_config.ForeignRepoResolution(repo, "project/repo"),
    )
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: ("claimant", ""))
    return config


def test_target_capabilities_are_actual_cli_contract(author, out):
    assert cli.main(["pr-author-capabilities", "--repo", "project/repo", "--json"]) == 0
    result = json.loads(out.getvalue())
    assert result["schema"] == pr_author_cli.SCHEMA
    assert result["repo"] == "project/repo"
    assert result["operations"]["metadata-update"] is True
    assert result["operations"]["remote-evidence"] is False
    assert result["operations"]["worker-policy-config"] is True
    assert result["operations"]["worker-policy-export"] is True


@pytest.mark.parametrize("contents", [None, "{}", "[]"])
def test_invalid_explicit_author_config_refuses_before_mutation(
    author, out, monkeypatch, tmp_path, contents,
):
    path = tmp_path / "worker.json"
    if contents is not None:
        path.write_text(contents, "utf-8")
    monkeypatch.setattr(pr_metadata, "update_metadata", lambda *a, **k: pytest.fail("write"))
    assert cli.main([
        "pr-update", "--repo", "project/repo", "--number", "7",
        "--config", str(path), "--title", "new", "--json",
    ]) == 1
    assert "explicit PR configuration" in json.loads(out.getvalue())["error"]


def test_empty_explicit_author_config_is_not_ambient(author, out, monkeypatch):
    monkeypatch.setattr(pr_metadata, "update_metadata", lambda *a, **k: pytest.fail("write"))
    assert cli.main([
        "pr-update", "--repo", "project/repo", "--number", "7",
        "--config=", "--title", "new", "--json",
    ]) == 1
    assert "explicit PR configuration file is unavailable" in json.loads(out.getvalue())["error"]


def test_metadata_command_keeps_exact_body_and_partial_pr_identity(author, monkeypatch, tmp_path, out):
    path = tmp_path / "description.md"
    value = '# Heading\r\n\r\n"quoted" \u03bb\r\n'
    path.write_bytes(value.encode("utf-8"))
    observed = {}
    result = {"ok": False, "repo": "project/repo", "number": 7,
              "url": "https://forge.invalid/7", "actions": {"description": {"status": "unknown"}}}

    def update(config, repo, number, **kwargs):
        observed.update(config=config, repo=repo, number=number, **kwargs)
        return result

    monkeypatch.setattr(pr_metadata, "update_metadata", update)
    assert cli.main([
        "pr-update", "--repo", "project/repo", "--number", "7", "--body-file", str(path),
        "--reviewer", "expert", "--work-item", "other/42", "--json",
    ]) == 1
    assert json.loads(out.getvalue()) == {"version": 1, **result}
    assert observed["body"] == value and observed["config"] is author
    assert observed["reviewers"] == ("expert",) and observed["work_items"] == ("other/42",)


def test_metadata_command_without_claimant_never_resolves_or_writes(author, monkeypatch, out):
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: (None, "no claimant"))
    monkeypatch.setattr(pr_metadata, "update_metadata", lambda *a, **k: pytest.fail("unexpected write"))
    assert cli.main(["pr-update", "--repo", "project/repo", "--number", "7", "--title", "new", "--json"])
    assert "no claimant" in json.loads(out.getvalue())["error"]


def test_foreign_create_reads_large_body_file_exactly_once_at_owner(author, monkeypatch, tmp_path, out):
    from agent_worktrees import pr_foreign_create, worktree_identity

    path = tmp_path / "long-description.md"
    value = '# Heading\r\n\r\n"quoted" \u03bb\r\n' * 4000
    path.write_bytes(value.encode("utf-8"))
    observed = {}
    monkeypatch.setattr(worktree_identity, "_infer_worktree_id", lambda *args: "claimant")
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda *args: "claimant")

    def create(*args, **kwargs):
        observed.update(kwargs)
        return {"repo": "project/repo", "number": 7, "url": "https://forge.invalid/7", "pr_opened": True}

    monkeypatch.setattr(pr_foreign_create, "create_foreign_pr_from_branch", create)
    assert cli.main([
        "pr-create", "--repo", "project/repo", "--from-branch", "topic", "--title", "Fix",
        "--body-file", str(path), "--json",
    ]) == 0
    assert observed["body"] == value
    assert json.loads(out.getvalue())["number"] == 7


@pytest.mark.parametrize("state", ["closed", "merged", ""])
def test_source_origin_never_labels_a_terminal_or_unknown_pr(author, monkeypatch, tmp_path, out, state):
    from types import SimpleNamespace
    from agent_worktrees import pr_source_publication, providers

    monkeypatch.setattr(
        pr_source_publication, "publish_source",
        lambda *args, **kwargs: {"ok": True, "repo": "project/repo", "publication_state": "pushed"},
    )
    backend = SimpleNamespace(
        find_pull_by_head=lambda *args, **kwargs: SimpleNamespace(state=state, number=7, url="https://forge.invalid/7"),
        get_publication_labels=lambda *args, **kwargs: pytest.fail("wrong PR labels"),
    )
    monkeypatch.setattr(providers, "get_provider", lambda *_: backend)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    assert cli.main([
        "pr-push", "--repo", "project/repo", "--workspace", str(tmp_path), "--branch", "topic",
        "--expected-head", "a" * 40, "--publication-origin", "test",
        "--publication-label", "source:test", "--json",
    ]) == 1
    result = json.loads(out.getvalue())
    assert result["publication_state"] == "pushed"
    assert not result["publication_labels"]["ok"]


@pytest.mark.parametrize("outcome", ["changed-after-create", "unknown-after-create"])
def test_human_create_output_warns_about_retained_unqualified_pr(capsys, outcome):
    from agent_worktrees import finalize_cli

    finalize_cli._warn_publication_labels({"publication_checks": {"ok": False, "outcome": outcome}})
    captured = capsys.readouterr()
    assert outcome in captured.out + captured.err
    assert "do not recreate" in captured.out + captured.err
