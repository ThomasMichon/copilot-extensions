"""The shared owner can read target evidence without owning a caller checkout."""

from types import SimpleNamespace
import json
from pathlib import Path
import sys
import textwrap

import pytest

from agent_worktrees import config, pr_cli, pr_config, pr_read_context, pr_related_binding, pr_watch, related, repos
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
    if argv[:1] == ["pr-watch"]:
        monkeypatch.setattr(main, "_ensure_cluster_loaded",
                            lambda: pytest.fail("explicit read loaded lifecycle commands"))
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
    monkeypatch.setattr(main, "_ensure_cluster_loaded",
                        lambda: pytest.fail("explicit threads read loaded lifecycle commands"))
    assert main.main(["pr-watch", "threads", "owner/repository", "7", "--json"]) == 0
    assert '"threads": []' in capsys.readouterr().out


@pytest.mark.parametrize("arguments", [
    ["snapshot", "7", "--json"],
    ["status", "7", "--json"],
    ["threads", "7", "--json"],
    ["cursor", "owner/repository", "7"],
    ["wait", "owner/repository", "7", "--timeout", "1"],
    ["reply", "owner/repository", "7"],
    ["subscribe", "owner/repository", "7"],
])
def test_other_watch_routes_retain_the_deferred_cluster(monkeypatch, arguments):
    from agent_worktrees import __main__ as main

    calls = []
    monkeypatch.setattr(main.front_door_cli, "resolve_invocation_project", lambda *args: True)
    monkeypatch.setattr(main, "_ensure_cluster_loaded", lambda: calls.append("cluster"))

    def dispatch(argv):
        assert argv == arguments
        assert calls == ["cluster"]
        return 37

    monkeypatch.setattr(pr_cli, "cmd_pr_watch_dispatch", dispatch)
    assert main.main(["pr-watch", *arguments]) == 37


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


@pytest.mark.parametrize("verb", ["snapshot", "status", "threads", "diff"])
def test_one_shot_read_reuses_discovery_but_next_read_reacquires(monkeypatch, capsys, verb):
    from agent_worktrees import config_cache, pr_diff_report, pr_thread_report, providers

    discoveries = []
    endpoints = []
    endpoint = "https://forge.example/first"

    def discover():
        discoveries.append(endpoint)
        return {"target": {"provider": "azure-devops", "api_base": endpoint,
                           "repository": "project/repository"}}

    def load(path=None):
        pr_related_binding.control_plane_pr_map()
        return SimpleNamespace(repo_name="caller", default_repo=object(), repos={})

    def fetch(prcfg, *args, **kwargs):
        endpoints.append(prcfg.api_base)
        return lambda: PRSnapshot(head_sha="head")

    monkeypatch.setattr(config, "_control_plane_related_pr_map", discover)
    monkeypatch.setattr(pr_read_context, "load_read_config", load)
    monkeypatch.setattr(pr_config, "_resolve_repo_remote", lambda *args: "")
    monkeypatch.setattr(repos, "list_repos", lambda: [])
    monkeypatch.setattr(pr_watch, "build_fetch", fetch)
    monkeypatch.setattr(providers, "get_provider", lambda name: object())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *args: None)

    def threads(*args, api_base="", **kwargs):
        endpoints.append(api_base)
        return {"supported": True, "threads": []}

    monkeypatch.setattr(pr_thread_report, "thread_report", threads)
    monkeypatch.setattr(pr_diff_report, "diff_report", threads)
    # An enclosing resident cache must not supply a prior read's policy.
    with config_cache.ConfigCacheSession().scope():
        pr_related_binding.control_plane_pr_map()
        discoveries.clear()
        for endpoint in ("https://forge.example/second", "https://forge.example/third"):
            assert pr_cli.cmd_pr_watch_dispatch([verb, "project/repository", "7", "--json"]) == 0
    assert discoveries == endpoints == ["https://forge.example/second", "https://forge.example/third"]
    capsys.readouterr()


def test_read_discovery_scope_is_released_after_failure(monkeypatch):
    from agent_worktrees import config_cache

    calls = []

    def discover():
        calls.append(True)
        return {}

    def fail(path=None):
        pr_related_binding.control_plane_pr_map()
        raise RuntimeError("read failed")

    monkeypatch.setattr(config, "_control_plane_related_pr_map", discover)
    monkeypatch.setattr(pr_read_context, "load_read_config", fail)
    with config_cache.ConfigCacheSession().scope():
        pr_related_binding.control_plane_pr_map()
        with pytest.raises(RuntimeError, match="read failed"):
            pr_cli.cmd_pr_watch_dispatch(["snapshot", "project/repository", "7"])
        pr_related_binding.control_plane_pr_map()
    assert len(calls) == 2
    pr_related_binding.control_plane_pr_map()
    assert len(calls) == 3


def test_wait_does_not_acquire_a_read_cache(monkeypatch):
    calls = []

    def discover():
        calls.append(True)
        return {}

    def wait(argv):
        pr_related_binding.control_plane_pr_map()
        pr_related_binding.control_plane_pr_map()
        return 124

    monkeypatch.setattr(config, "_control_plane_related_pr_map", discover)
    assert pr_read_context.scoped_read_dispatch(wait)(["wait"]) == 124
    assert len(calls) == 2


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


@pytest.mark.parametrize("provider_case", ["github", "gitea", "azure-devops", "ado-changed", "ado-partial"])
@pytest.mark.parametrize("new_frontend", [False, True])
def test_real_thread_read_process_uses_actual_target_provider_and_preserves_evidence(
    monkeypatch, tmp_path, capsys, provider_case, new_frontend,
):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import __main__ as frontend, native_command
    from agent_pull_requests.watch_invocation import WatchInvocation

    script = textwrap.dedent("""
        import json, sys
        from types import SimpleNamespace
        from unittest.mock import patch
        from contextlib import ExitStack
        sys.path.insert(0, sys.argv.pop(1))
        from agent_worktrees import __main__ as cli, config, pr_config, pr_read_context, pr_cli, providers
        from agent_worktrees.providers.github import GitHubProvider
        from agent_worktrees.providers.gitea import GiteaProvider
        from agent_worktrees.providers.azure_devops import AzureDevOpsProvider
        from agent_worktrees.providers.azure_observation_reads import AdoReader, Collection
        case = sys.argv.pop(1)
        provider_name = "azure-devops" if case.startswith("ado-") else case
        endpoint = "https://forge.example/target"
        repo = config.RepoConfig(anchor="", worktree_root="",
                                 pr=config.PRConfig(provider=provider_name, api_base=endpoint))
        caller = SimpleNamespace(default_repo=config.RepoConfig(
            anchor="", worktree_root="", pr=config.PRConfig(provider="mock")))
        reads = []
        def forbidden(*args, **kwargs):
            raise AssertionError("lifecycle, snapshot or mutation route")
        def resolve(cfg, slug):
            assert cfg is caller and slug == "example/repository"
            return pr_config.ForeignRepoResolution(repo, "target")
        def account(slug, prcfg):
            assert slug == "example/repository" and prcfg is repo.pr
            return "synthetic-fixture"
        def graphql(self, query, token, **values):
            assert "mutation" not in query
            assert values == {"owner": "example", "name": "repository", "number": 7}
            reads.append("github")
            return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
                {"isResolved": False, "path": "example.py", "comments": {"nodes": [
                    {"author": {"login": "reviewer"}, "body": "Body --untrusted λ"}
                ]}}
            ]}}}}}, ""
        def curl(self, method, url, token, **kwargs):
            assert method == "GET" and "/repos/example/repository/pulls/7/reviews" in url
            assert url.startswith(endpoint)
            reads.append("gitea")
            return 200, json.dumps([{"body": "Body --untrusted λ", "user": {"login": "reviewer"},
                                    "path": "example.py"}] if url.endswith("/comments") else [{"id": 5}])
        def pr(self):
            reads.append("pr")
            return {"createdBy": {"id": "author"}, "repository": {
                "name": "repository", "project": {"name": "example"}},
                "lastMergeSourceCommit": {"commitId": (
                    "b" * 40 if case == "ado-changed" and reads.count("pr") > 1 else "a" * 40)}}
        def threads(self):
            reads.append("threads")
            return Collection(({
                "id": 5, "status": "active", "lastUpdatedDate": "2026-01-01T10:01:00Z",
                "comments": [{"id": 1, "commentType": "text", "content": "Body --untrusted λ",
                              "author": {"id": "reviewer", "displayName": "reviewer"},
                              "publishedDate": "2026-01-01T10:01:00Z",
                              "lastUpdatedDate": "2026-01-01T10:01:00Z"}],
                "pullRequestThreadContext": {"iterationContext": {"secondComparingIteration": 2}},
                "threadContext": {"filePath": "example.py", "rightFileStart": {"line": 5}},
            },), error="partial page" if case == "ado-partial" else "")
        with ExitStack() as stack:
            for target, name, value in [
                (config, "active_project", lambda: None),
                (cli, "_resolve_active_project", lambda explicit: (None, None)),
                (cli, "_ensure_cluster_loaded", forbidden),
                (pr_read_context, "load_read_config", lambda path=None: caller),
                (pr_config, "resolve_repo_config_for_slug", resolve),
                (pr_cli, "require_claimant_worktree", forbidden),
                (providers, "account_token_for_slug", account),
                (GitHubProvider, "_graphql", graphql), (GiteaProvider, "_curl", curl),
                (AdoReader, "__init__", lambda self, *args, **kwargs: None),
                (AdoReader, "pr", pr), (AdoReader, "threads", threads),
            ]:
                stack.enter_context(patch.object(target, name, value))
            code = cli.main(sys.argv[1:])
            assert reads
            if provider_name == "azure-devops":
                assert reads == (["pr", "threads"] if case == "ado-partial"
                                 else ["pr", "threads", "pr"])
            sys.exit(code)
    """)
    prefix = [sys.executable, "-c", script, str(Path(__file__).parents[1] / "src"), provider_case]
    monkeypatch.chdir(tmp_path)
    if new_frontend:
        monkeypatch.setattr(WatchInvocation, "capture", lambda **kwargs: object())
        monkeypatch.setattr(frontend, "_agent_worktrees_prefix", lambda: prefix)
        code = frontend.main([
            "threads", "--repo", "example/repository", "--number", "7",
            "--worktrees-command", "selected-managed-peer", "--json",
        ])
        body = json.loads(capsys.readouterr().out)
    else:
        result = native_command.run_command(
            [*prefix, "pr-watch", "threads", "example/repository", "7", "--json"], timeout=30,
        )
        code, body = result.returncode, json.loads(result.stdout)
    assert code == (1 if provider_case == "ado-partial" else 0)
    assert body["repo"] == "example/repository" and body["pr"] == 7
    assert body["provider"] == ("azure-devops" if provider_case.startswith("ado-") else provider_case)
    if provider_case == "ado-partial":
        assert body["complete"] is False and body["error"] == "partial page"
    else:
        assert body["threads"][0]["comments"][0]["text"] == "Body --untrusted λ"
        assert body["complete"] is (True if body["provider"] == "azure-devops" else None)
    if provider_case == "ado-changed":
        assert body["candidate_changed"] is True
        assert body["observed_head_sha"] == "a" * 40 and body["head_sha"] == "b" * 40
        assert body["threads"][0]["iteration_context"] == {"secondComparingIteration": 2}
    assert not list(tmp_path.iterdir())
