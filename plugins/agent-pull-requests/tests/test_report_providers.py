"""Real report builders and provider code under synthetic IO, not live proof."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_pull_requests.report_client import ReportClient
from agent_pull_requests.report_read import ReportReader

from test_report_read import reports as reports


@pytest.mark.parametrize("provider_name", ["github", "gitea", "azure-devops"])
def test_real_provider_reports_cross_optional_client_gateway_seam(reports, monkeypatch, provider_name):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-worktrees" / "src"))
    for source in (root / "libs").glob("*/src"):
        monkeypatch.syspath_prepend(str(source))
    from agent_worktrees.pr_diff_report import diff_report
    from agent_worktrees.pr_thread_report import thread_report
    from agent_worktrees.providers.github import GitHubProvider
    from agent_worktrees.providers.gitea import GiteaProvider
    from agent_worktrees.providers.azure_devops import AzureDevOpsProvider
    from agent_worktrees.providers import github
    from agent_worktrees.providers.azure_observation_reads import AdoReader

    store, gateway, reader, old_client, caller, state, _ = reports
    scope = {**dict(reader.scope), "provider": provider_name}
    text = "diff --git a/x b/x\r\n+λ\rtail"
    reads = []
    if provider_name == "github":
        provider = GitHubProvider()
        def run(argv, **kwargs):
            assert argv == ["gh", "pr", "diff", "7", "--repo", scope["repo"]]
            assert kwargs["env"]["GH_HOST"] == "forge.example"
            reads.append("diff")
            return SimpleNamespace(returncode=0, stdout=text, stderr="")
        monkeypatch.setattr(github, "run_cli", run)
        def graphql(query, token, **kwargs):
            assert kwargs == {"owner": "example", "name": "project", "number": 7}
            reads.append("threads")
            return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [{
                "path": "x", "comments": {"nodes": [{"body": "λ\r\nbody", "author": {"login": "fixture"}}]},
            }]}}}}}, ""
        monkeypatch.setattr(provider, "_graphql", graphql)
    elif provider_name == "gitea":
        provider = GiteaProvider()
        def curl(method, url, token, **kwargs):
            assert method == "GET" and token == "synthetic"
            assert url.startswith(scope["endpoint"] + "/api/v1/repos/example/project/pulls/7")
            if url.endswith(".diff"):
                reads.append("diff")
                return 200, text
            reads.append("threads")
            if url.endswith("/reviews"):
                return 200, '[{"id":1}]'
            assert url.endswith("/reviews/1/comments")
            return 200, json.dumps([{"body": "λ\r\nbody", "user": {"login": "fixture"}, "path": "x"}])
        monkeypatch.setattr(provider, "_curl", curl)
    else:
        provider = AzureDevOpsProvider()
        heads = iter(("before", "after"))
        def native_json(self, url, **kwargs):
            assert self.api_base == scope["endpoint"]
            if "/threads?" in url:
                reads.append("threads")
                return {"value": [], "count": 0}, ""
            reads.append("pr")
            return {
                "repository": {"name": "project", "project": {"name": "example"}},
                "lastMergeSourceCommit": {"commitId": next(heads)},
            }, ""
        monkeypatch.setattr(AdoReader, "json", native_json)

    def collect(operation, number, remaining):
        remaining()
        report = (thread_report if operation == "threads" else diff_report)(
            provider, scope["repo"], number, api_base=scope["endpoint"], token="synthetic",
        )
        remaining()
        return int(bool(report["error"] or not report["supported"])), json.dumps(report)
    state["binding"] = replace(state["binding"], scope=scope, collect=collect)
    reader = ReportReader(reader.consumer_scope, scope, reader.resolve_binding, max_work_seconds=3)
    gateway.reports = {(old_client.binding.principal, old_client.binding.target): reader}
    client = ReportClient(caller, route_prefix="", binding=old_client.binding, scope=scope)
    before = store.path.read_bytes()

    async def scenario():
        await client.capabilities()
        assert reads == []
        threads = await client.read("threads", {"number": 7, "budget_seconds": 2})
        diff = await client.read("diff", {"number": 7, "budget_seconds": 2})
        assert threads["report"]["provider"] == provider_name
        if provider_name == "azure-devops":
            assert threads["report"]["candidate_changed"] is True
            assert threads["report"]["complete"] is True
            assert diff["report"]["supported"] is False and diff["owner_exit"] != 0
            assert reads == ["pr", "threads", "pr"]
        else:
            assert threads["report"]["threads"][0]["comments"][0]["text"] == "λ\r\nbody"
            assert threads["report"]["complete"] is None
            assert diff["report"]["diff"] == text and diff["owner_exit"] == 0
        await gateway.drain(timeout=1)
    asyncio.run(scenario())
    assert store.path.read_bytes() == before
