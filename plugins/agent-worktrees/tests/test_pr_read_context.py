"""The shared owner can read target evidence without owning a caller checkout."""

from types import SimpleNamespace

import pytest

from agent_worktrees import config, pr_cli, pr_config, pr_read_context, pr_watch, related, repos
from agent_worktrees.pr_snapshot import PRSnapshot


@pytest.mark.parametrize("argv", [
    ["pr-watch", "snapshot", "owner/repository", "7", "--json"],
    ["pr-watch", "status", "owner/repository", "7", "--json"],
    ["pr-status", "--repo", "owner/repository", "--pr", "7", "--json"],
    ["pr-status", "--repo=owner/repository", "--pr=7", "--json"],
])
def test_front_door_explicit_read_from_neutral_cwd(monkeypatch, tmp_path, capsys, argv):
    from agent_worktrees import __main__ as main

    monkeypatch.chdir(tmp_path)
    config.set_active_project(None)
    monkeypatch.setattr(main, "_resolve_active_project", lambda explicit: (None, None))
    repo = config.RepoConfig(anchor="", worktree_root="", pr=config.PRConfig())
    monkeypatch.setattr(pr_read_context, "load_read_config", lambda path=None: object())
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *args: pr_config.ForeignRepoResolution(repo, "target"))
    monkeypatch.setattr(pr_watch, "build_fetch", lambda *args, **kwargs: lambda: PRSnapshot(head_sha="head"))
    monkeypatch.setattr(pr_cli, "require_claimant_worktree",
                        lambda verb: pytest.fail("one-shot read acquired a worktree"))
    assert main.main(argv) == 0
    assert "agent-worktrees.pr-snapshot" in capsys.readouterr().out


def test_front_door_threads_read_from_neutral_cwd(monkeypatch, tmp_path, capsys):
    from agent_worktrees import __main__ as main, providers, pr_thread_report

    monkeypatch.chdir(tmp_path)
    config.set_active_project(None)
    monkeypatch.setattr(main, "_resolve_active_project", lambda explicit: (None, None))
    repo = config.RepoConfig(anchor="", worktree_root="", pr=config.PRConfig())
    monkeypatch.setattr(pr_read_context, "load_read_config", lambda path=None: object())
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *args: pr_config.ForeignRepoResolution(repo, "target"))
    monkeypatch.setattr(providers, "get_provider", lambda name: object())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *args: None)
    monkeypatch.setattr(pr_thread_report, "thread_report",
                        lambda *args, **kwargs: {"supported": True, "threads": []})
    assert main.main(["pr-watch", "threads", "owner/repository", "7", "--json"]) == 0
    assert '"threads": []' in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["pr-watch", "wait", "owner/repository", "7", "--timeout", "1"],
    ["pr-status", "--json"],
    ["create-pr", "--json"],
])
def test_front_door_project_operations_still_require_context(monkeypatch, tmp_path, argv):
    from agent_worktrees import __main__ as main

    monkeypatch.chdir(tmp_path)
    config.set_active_project(None)
    monkeypatch.setattr(main, "_resolve_active_project", lambda explicit: (None, None))
    monkeypatch.setattr(main, "cmd_help_unrouted", lambda **kwargs: 41)
    assert main.main(argv) == 41


def test_context_free_read_uses_the_registered_control_plane_not_an_invented_project(monkeypatch, tmp_path):
    anchor = tmp_path / "control"
    monkeypatch.setattr(config, "active_project", lambda: None)
    monkeypatch.setattr(related, "find_control_plane_anchor", lambda: str(anchor))
    monkeypatch.setattr(repos, "list_repos", lambda: [
        SimpleNamespace(name="control", local_path=lambda: str(anchor)),
    ])
    result = object()
    monkeypatch.setattr(config, "load_project_config", lambda name: result if name == "control" else None)
    assert pr_read_context.load_read_config() is result


def test_context_free_read_refuses_missing_control_plane(monkeypatch):
    monkeypatch.setattr(config, "active_project", lambda: None)
    monkeypatch.setattr(related, "find_control_plane_anchor", lambda: None)
    with pytest.raises(ValueError, match="--project"):
        pr_read_context.load_read_config()


def test_one_shot_snapshot_has_no_mutating_or_durable_claimant_requirement(monkeypatch, capsys):
    repo = config.RepoConfig(anchor="", worktree_root="", pr=config.PRConfig())
    monkeypatch.setattr(config, "active_project", lambda: None)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree",
                        lambda verb: (_ for _ in ()).throw(AssertionError("not a lifecycle operation")))
    monkeypatch.setattr(pr_read_context, "load_read_config", lambda path=None: object())
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *args: pr_config.ForeignRepoResolution(repo, "target"))
    monkeypatch.setattr(pr_watch, "build_fetch", lambda *args, **kwargs: lambda: PRSnapshot(head_sha="head"))
    assert pr_cli.cmd_pr_watch_dispatch(["snapshot", "owner/repository", "7", "--json"]) == 0
    assert "agent-worktrees.pr-snapshot" in capsys.readouterr().out


def test_foreground_only_wait_cannot_be_unbounded(monkeypatch):
    monkeypatch.setattr(pr_cli, "require_claimant_worktree",
                        lambda verb: (_ for _ in ()).throw(AssertionError("finite caller-only wait")))
    assert pr_cli.cmd_pr_watch_dispatch([
        "wait", "owner/repository", "7", "--foreground-only", "--timeout", "0",
    ]) == 2


def test_existing_durable_wait_still_requires_its_claimant(monkeypatch):
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda verb: (None, "claimant required"))
    assert pr_cli.cmd_pr_watch_dispatch(["wait", "owner/repository", "7", "--timeout", "1"]) == 2


def test_foreground_only_wait_uses_the_existing_poll_loop_and_terminal_event(monkeypatch):
    repo = config.RepoConfig(anchor="", worktree_root="", pr=config.PRConfig())
    cfg = SimpleNamespace(default_repo=repo)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree",
                        lambda verb: (_ for _ in ()).throw(AssertionError("caller-owned process")))
    monkeypatch.setattr(config, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *args: pr_config.ForeignRepoResolution(repo, "target"))
    monkeypatch.setattr(pr_cli, "_tracked_pr_head_evidence", lambda *args: ("", ""))
    monkeypatch.setattr(pr_watch, "build_fetch",
                        lambda *args, **kwargs: lambda: PRSnapshot(pr_state="closed", merged=True))
    assert pr_cli.cmd_pr_watch_dispatch([
        "wait", "owner/repository", "7", "--foreground-only", "--timeout", "1", "--json",
    ]) == 0
