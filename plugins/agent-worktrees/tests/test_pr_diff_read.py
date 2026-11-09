"""The named-target diff owner is independent of tracked lifecycle and workspaces."""

import json
from pathlib import Path
import sys
import textwrap

import pytest

from agent_worktrees import pr_cli
from agent_worktrees.pr_contract import PRDiff
from agent_worktrees.pr_diff_report import diff_report


@pytest.mark.parametrize("result,complete", [
    (PRDiff(diff=""), None), (PRDiff(diff="partial", error="lost tail"), False),
    (PRDiff(supported=False, error="unsupported"), False),
])
def test_provider_result_does_not_invent_complete_or_candidate_evidence(result, complete):
    class Provider:
        name = "fixture"

        def get_diff(self, repo, number, **kwargs):
            assert (repo, number, kwargs) == ("example/repository", 7,
                                              {"api_base": "https://forge.example", "token": None,
                                               "preserve_line_endings": True})
            return result

    report = diff_report(Provider(), "example/repository", 7, api_base="https://forge.example")
    assert report["diff"] == result.diff and report["supported"] is result.supported
    assert report["error"] == result.error and report["complete"] is complete
    assert not any(key in report for key in ("head_sha", "base_sha", "candidate_changed"))


def test_provider_exception_is_valid_failed_report():
    class Provider:
        name = "fixture"

        def get_diff(self, *args, **kwargs):
            raise RuntimeError("synthetic-secret-in-unexpected-exception")

    report = diff_report(Provider(), "example/repository", 7)
    assert report["error"] == "provider diff acquisition failed (RuntimeError)"
    assert "synthetic-secret" not in json.dumps(report)
    assert report["supported"] is report["complete"] is False


@pytest.mark.parametrize("result", [
    None, {}, PRDiff(supported=1), PRDiff(diff=None), PRDiff(error=None),
])
def test_malformed_provider_results_stay_valid_failed_reports(result):
    class Provider:
        name = "fixture"

        def get_diff(self, *args, **kwargs):
            return result

    report = diff_report(Provider(), "example/repository", 7)
    assert report["error"] and report["diff"] == ""
    assert report["supported"] is report["complete"] is False


def test_unknown_report_verb_refuses_before_provider_or_credential_acquisition(monkeypatch):
    from agent_worktrees import pr_read_context, providers

    monkeypatch.setattr(providers, "get_provider", lambda *args: pytest.fail("provider acquisition"))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *args: pytest.fail("credential acquisition"))
    with pytest.raises(ValueError, match="Unknown on-demand report verb"):
        pr_read_context.read_report("difference", object(), "example/repository", 7)


@pytest.mark.parametrize("supported", [None, 0, "true"])
def test_dispatch_requires_positive_report_support(monkeypatch, capsys, supported):
    from agent_worktrees import config, pr_config, pr_read_context

    repo = config.RepoConfig(anchor="", worktree_root="", pr=config.PRConfig())
    monkeypatch.setattr(pr_read_context, "load_read_config", lambda *args: object())
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *args: pr_config.ForeignRepoResolution(repo, "target"))
    monkeypatch.setattr(pr_read_context, "read_report",
                        lambda *a, **k: {"supported": supported, "error": "", "diff": ""})
    assert pr_cli.cmd_pr_watch_dispatch(["diff", "example/repository", "7", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["supported"] == supported


@pytest.mark.parametrize("argv", [
    ["diff", "7"], ["diff", "example/repository", "0"],
    ["diff", "example/repository", "-7"], ["diff", "example/repository", "true"],
    ["diff", "example/repository", "7", "--resolve"],
    ["diff", "example/repository", "7", "--con", "file"],
])
def test_invalid_new_syntax_refuses_before_binding(monkeypatch, argv):
    from agent_worktrees import pr_read_context

    monkeypatch.setattr(pr_read_context, "load_read_config", lambda *a: pytest.fail("binding acquisition"))
    assert pr_cli.cmd_pr_watch_dispatch(argv) == 2


@pytest.mark.parametrize("case", ["github", "github-default", "github-error", "gitea", "gitea-no-token", "azure-devops"])
@pytest.mark.parametrize("new_frontend", [False, True])
def test_actual_provider_and_frontdoor_cross_process_without_checkout(
    tmp_path, monkeypatch, capsys, case, new_frontend,
):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import __main__ as frontend, native_command
    from agent_pull_requests.watch_invocation import WatchInvocation

    script = textwrap.dedent("""
        import json, os, sys
        from types import SimpleNamespace
        from contextlib import ExitStack
        from unittest.mock import patch
        sys.path.insert(0, sys.argv.pop(1))
        from agent_worktrees import __main__ as cli, config, pr_config, pr_read_context, pr_cli, providers
        from agent_worktrees.providers import github
        from agent_worktrees.providers.gitea import GiteaProvider
        case = sys.argv.pop(1)
        provider_name = "github" if case.startswith("github") else "gitea" if case.startswith("gitea") else case
        endpoint = "" if case == "github-default" else "https://target.example"
        os.environ["GH_HOST"] = "ambient.example"
        repo = config.RepoConfig(anchor="", worktree_root="",
                                 pr=config.PRConfig(provider=provider_name, api_base=endpoint))
        caller = SimpleNamespace(default_repo=config.RepoConfig(
            anchor="", worktree_root="", pr=config.PRConfig(provider="mock")))
        reads = []
        text = "diff --git a/x b/x\\r\\n+--untrusted λ\\t\\n"
        def forbidden(*args, **kwargs):
            raise AssertionError("lifecycle, snapshot or mutation route")
        def resolve(cfg, slug):
            assert cfg is caller and slug == "example/repository"
            return pr_config.ForeignRepoResolution(repo, "target")
        def account(slug, prcfg):
            assert slug == "example/repository" and prcfg is repo.pr
            return None if case == "gitea-no-token" else "synthetic-fixture"
        def run(argv, **kwargs):
            assert argv == ["gh", "pr", "diff", "7", "--repo", "example/repository"]
            assert kwargs["env"]["GH_HOST"] == ("ambient.example" if not endpoint else "target.example")
            reads.append("github")
            return SimpleNamespace(returncode=1 if case == "github-error" else 0,
                                   stdout="" if case == "github-error" else text, stderr="native refusal")
        def curl(self, method, url, token, **kwargs):
            assert method == "GET" and url == endpoint + "/api/v1/repos/example/repository/pulls/7.diff"
            reads.append("gitea")
            return 200, text
        with ExitStack() as stack:
            for target, name, value in [
                (config, "active_project", lambda: None),
                (cli, "_resolve_active_project", lambda explicit: (None, None)),
                (cli, "_ensure_cluster_loaded", forbidden),
                (pr_read_context, "load_read_config", lambda path=None: caller),
                (pr_config, "resolve_repo_config_for_slug", resolve),
                (pr_cli, "require_claimant_worktree", forbidden),
                (providers, "account_token_for_slug", account),
                (github, "run_cli", run), (GiteaProvider, "_curl", curl),
            ]:
                stack.enter_context(patch.object(target, name, value))
            code = cli.main(sys.argv[1:])
            assert len(reads) == (0 if case in ("gitea-no-token", "azure-devops") else 1)
            sys.exit(code)
    """)
    prefix = [sys.executable, "-c", script, str(Path(__file__).parents[1] / "src"), case]
    monkeypatch.chdir(tmp_path)
    if new_frontend:
        monkeypatch.setattr(WatchInvocation, "capture", lambda **kwargs: object())
        monkeypatch.setattr(frontend, "_agent_worktrees_prefix", lambda: prefix)
        code = frontend.main([
            "diff", "--repo", "example/repository", "--number", "7",
            "--worktrees-command", "selected-managed-peer", "--json",
        ])
        body = json.loads(capsys.readouterr().out)
    else:
        result = native_command.run_command(
            [*prefix, "pr-watch", "diff", "example/repository", "7", "--json"], timeout=30,
        )
        code, body = result.returncode, json.loads(result.stdout)
    failed = case in ("gitea-no-token", "azure-devops", "github-error")
    assert code == int(failed)
    assert body["schema"] == "agent-worktrees.pr-diff-read" and body["version"] == 1
    assert body["repo"] == "example/repository" and body["pr"] == 7
    assert body["complete"] is (False if failed else None)
    if failed:
        assert body["error"] and body["diff"] == ""
    else:
        assert body["diff"] == "diff --git a/x b/x\r\n+--untrusted λ\t\n"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("json_mode", [False, True])
def test_prior_tracked_parser_process_keeps_reconciliation_and_legacy_output(tmp_path, monkeypatch, json_mode):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests.native_command import run_command

    script = textwrap.dedent("""
        import argparse, sys
        from types import SimpleNamespace
        from contextlib import ExitStack
        from unittest.mock import patch
        sys.path.insert(0, sys.argv.pop(1))
        from agent_worktrees import config, pr_ops, pr_state_cli, providers, worktree_identity
        from agent_worktrees.pr_contract import PRDiff
        active = SimpleNamespace(provider="gitea", number=7, repo="example/repository", state="open")
        record = SimpleNamespace(repo="example/repository", active_pr=lambda: active)
        cfg = SimpleNamespace(default_repo=SimpleNamespace(pr=config.PRConfig(provider="gitea")))
        calls = []
        class Provider:
            def get_diff(self, repo, number, **kwargs):
                assert calls == ["reconcile"] and (repo, number) == ("example/repository", 7)
                calls.append("diff")
                return PRDiff(diff="retained tracked diff")
        parser = argparse.ArgumentParser()
        pr_state_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(sys.argv[1:])
        with ExitStack() as stack:
            for target, name, value in [
                (config, "load_config", lambda *args: cfg),
                (worktree_identity, "_infer_worktree_id", lambda *args: "selected"),
                (worktree_identity, "_resolve_worktree_id", lambda value: value),
                (pr_ops, "_load_record_or_none", lambda value: record),
                (pr_ops, "_reconcile_active_pr", lambda *args: calls.append("reconcile")),
                (providers, "get_provider", lambda name: Provider()),
                (providers, "account_token_for_slug", lambda *args: None),
            ]:
                stack.enter_context(patch.object(target, name, value))
            code = pr_state_cli.cmd_pr_diff(args)
            assert calls == ["reconcile", "diff"]
            sys.exit(code)
    """)
    argv = [sys.executable, "-c", script, str(Path(__file__).parents[1] / "src"), "pr-diff", "selected"]
    if json_mode:
        argv.append("--json")
    monkeypatch.chdir(tmp_path)
    result = run_command(argv, timeout=30)
    assert result.returncode == 0, result.stderr
    if json_mode:
        assert json.loads(result.stdout) == {
            "version": 1, "worktree_id": "selected", "has_pr": True, "number": 7,
            "repo": "example/repository", "supported": True, "diff": "retained tracked diff",
        }
    else:
        assert result.stdout == "retained tracked diff\n"
    assert not list(tmp_path.iterdir())
