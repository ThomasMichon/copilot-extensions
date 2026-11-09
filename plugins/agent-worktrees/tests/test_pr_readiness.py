"""Explicit readiness uses the actual target policy, provider and native client."""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import subprocess
from typing import Any
from urllib.parse import unquote, urlparse

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg, output, pr_cli, pr_config, pr_readiness
from agent_worktrees.providers import azure_devops as azure

REPO = "Source Project/repository"
ORG = "https://dev.azure.com/example"


class Backend:
    def __init__(self) -> None:
        self.pull: dict[str, Any] = {
            "pullRequestId": 7, "status": "active", "isDraft": True,
            "repository": {"name": "repository", "project": {"name": "Source Project"}},
            "title": "WIP: retain title", "description": "unchanged",
            "reviewers": [{"id": "reviewer", "vote": -10}],
            "labels": ["paused", "do-not-merge"],
            "autoCompleteSetBy": {"id": "existing-owner"},
            "completionOptions": {"deleteSourceBranch": False},
        }
        self.methods: list[str] = []
        self.writes: list[dict[str, Any]] = []
        self.fault = ""

    def cli(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert args[0] == "curl", "no create, push, consent or alternate transport"
        method = args[args.index("-X") + 1]
        url = args[args.index("-X") + 2]
        assert unquote(urlparse(url).path) == "/example/Source Project/_apis/git/repositories/repository/pullRequests/7"
        self.methods.append(method)
        if method == "PATCH":
            payload = json.loads(kwargs["input_text"])
            assert args[args.index("--data-binary") + 1] == "@-"
            assert payload == {"isDraft": False}
            self.writes.append(payload)
            if self.fault == "reject":
                return subprocess.CompletedProcess(args, 0, "{}\n403", "")
            if self.fault == "http-timeout":
                return subprocess.CompletedProcess(args, 0, "{}\n408", "")
            self.pull.update(payload)
            if self.fault == "lost":
                return subprocess.CompletedProcess(args, 28, "", "response lost")
            if self.fault == "raised":
                raise subprocess.TimeoutExpired(args, 30)
            if self.fault == "malformed":
                return subprocess.CompletedProcess(args, 0, "[]\n200", "")
            if self.fault == "still-draft":
                self.pull["isDraft"] = True
        if method == "GET" and self.writes:
            if self.fault == "readback":
                return subprocess.CompletedProcess(args, 28, "", "readback unavailable")
            if self.fault == "terminal":
                self.pull["status"] = "completed"
            if self.fault == "identity":
                self.pull["pullRequestId"] = 8
        return subprocess.CompletedProcess(args, 0, json.dumps(self.pull) + "\n200", "")


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Backend:
    server = Backend()
    monkeypatch.setattr(azure, "run_cli", server.cli)
    return server


def ready() -> dict[str, Any]:
    return azure.AzureDevOpsProvider().ready_pull(REPO, 7, api_base=ORG, token="fixture-only")


def test_ready_mutates_only_native_draft_and_preserves_every_independent_gate(backend: Backend) -> None:
    before = deepcopy(backend.pull)
    result = ready()
    assert result == pr_readiness.outcome(REPO, 7, provider="azure-devops", status="confirmed")
    assert backend.pull == {**before, "isDraft": False}
    assert backend.methods == ["GET", "PATCH", "GET"]
    assert ready()["actions"]["draft"]["status"] == "not-attempted"
    assert len(backend.writes) == 1


@pytest.mark.parametrize("damage", [
    {"pullRequestId": True}, {"pullRequestId": 8}, {"repository": []},
    {"repository": {"name": "other", "project": {"name": "Source Project"}}},
    {"repository": {"name": "repository", "project": {"name": "other"}}},
    {"isDraft": None}, {"isDraft": 1}, {"status": None},
    {"status": "completed"}, {"status": "abandoned"},
])
def test_ready_refuses_invalid_or_terminal_evidence_before_write(backend: Backend, damage: dict) -> None:
    backend.pull.update(damage)
    result = ready()
    assert not result["ok"] and result["actions"]["draft"]["status"] == "not-attempted"
    assert result["repo"] == REPO and result["number"] == 7
    assert not backend.writes


@pytest.mark.parametrize(("fault", "status"), [
    ("reject", "failed"), ("http-timeout", "unknown"), ("lost", "unknown"), ("raised", "unknown"),
    ("malformed", "unknown"), ("readback", "unknown"), ("terminal", "unknown"),
    ("identity", "unknown"), ("still-draft", "failed"),
])
def test_ready_never_replays_an_uncertain_or_failed_effect(backend: Backend, fault: str, status: str) -> None:
    backend.fault = fault
    result = ready()
    assert result["actions"]["draft"]["status"] == status and not result["ok"]
    assert result["repo"] == REPO and result["number"] == 7 and result["error"]
    assert len(backend.writes) == 1


@pytest.fixture
def author(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> cfg.Config:
    caller = cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
                           pr=cfg.PRConfig(enabled=True, provider="github"))
    target = cfg.RepoConfig(anchor="", worktree_root="", pr=cfg.PRConfig(
        enabled=True, provider="azure-devops", api_base=ORG, merge_actor="human-only",
    ))
    config = cfg.Config(srcroot=str(tmp_path), machine="test", platform="linux",
                        repo_name="caller", repos={"caller": caller})
    monkeypatch.setattr(cfg, "load_config", lambda *args: config)
    monkeypatch.setattr(cli.front_door_cli, "resolve_invocation_project", lambda *args: True)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: ("claimant", ""))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *_: pr_config.ForeignRepoResolution(target, REPO))
    from agent_worktrees import providers
    monkeypatch.setattr(providers, "account_token_for_slug",
                        lambda repo, policy: "fixture-only" if repo == REPO and policy is target.pr else pytest.fail("wrong auth"))
    return config


def test_ready_actual_cli_preserves_target_policy_and_structured_result(
    backend: Backend, author: cfg.Config,
) -> None:
    with output.capture_json_output() as capture:
        assert cli.main(["pr-author-ready", "--repo", REPO, "--number", "7", "--json"]) == 0
    assert json.loads(capture.getvalue())["actions"]["draft"]["status"] == "confirmed"
    assert len(backend.writes) == 1


def test_ready_actual_explicit_policy_resolution_never_uses_caller_provider(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_worktrees import providers

    caller = cfg.RepoConfig(anchor=str(tmp_path), worktree_root="",
                           pr=cfg.PRConfig(enabled=True, provider="github"))
    target = cfg.RepoConfig(anchor="", worktree_root="", pr=cfg.PRConfig(
        enabled=True, provider="azure-devops", repository=REPO, api_base=ORG,
    ))
    config = cfg.Config(srcroot=str(tmp_path), machine="test", platform="linux",
                        repo_name="caller", repos={"caller": caller}, explicit_pr_target=target)
    monkeypatch.setattr(providers, "account_token_for_slug",
                        lambda repo, policy: "fixture-only" if repo == REPO and policy is target.pr else pytest.fail("wrong auth"))
    assert pr_readiness.ready(config, REPO, 7)["ok"]
    assert len(backend.writes) == 1
    result = pr_readiness.ready(config, "other/repository", 7)
    assert not result["ok"] and "another PR target" in result["error"]
    assert len(backend.writes) == 1


def test_ready_requires_claimant_before_policy_or_provider(
    backend: Backend, author: cfg.Config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: (None, "no claimant"))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: pytest.fail("policy fallback"))
    with output.capture_json_output() as capture:
        assert cli.main(["pr-author-ready", "--repo", REPO, "--number", "7", "--json"]) == 1
    result = json.loads(capture.getvalue())
    assert result["actions"]["draft"]["status"] == "not-attempted" and "no claimant" in result["error"]
    assert not backend.methods


@pytest.mark.parametrize("contents", [
    None, "{}", '{"repo_name":"pr-worker","repos":{"pr-worker":{"pr":{"repository":"other/repo"}}}}',
])
def test_ready_invalid_explicit_policy_never_uses_ambient_target(
    backend: Backend, author: cfg.Config, tmp_path: Path, contents: str | None,
) -> None:
    path = tmp_path / "policy.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    with output.capture_json_output() as capture:
        assert cli.main([
            "pr-author-ready", "--repo", REPO, "--number", "7", "--config", str(path), "--json",
        ]) == 1
    result = json.loads(capture.getvalue())
    assert result["actions"]["draft"]["status"] == "not-attempted" and result["error"]
    assert not backend.methods


@pytest.mark.parametrize("binding", ["unregistered", "disabled", "github"])
def test_ready_refuses_unqualified_target_without_caller_fallback(
    backend: Backend, author: cfg.Config, monkeypatch: pytest.MonkeyPatch, binding: str,
) -> None:
    target = cfg.RepoConfig(anchor="", worktree_root="",
                           pr=cfg.PRConfig(enabled=binding != "disabled", provider=binding))
    resolution = pr_config.ForeignRepoResolution(None, "") if binding == "unregistered" else (
        pr_config.ForeignRepoResolution(target, REPO)
    )
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *_: resolution)
    result = pr_readiness.ready(author, REPO, 7)
    assert result["actions"]["draft"]["status"] == "not-attempted"
    assert not backend.methods


@pytest.mark.parametrize("extra", [
    ["--auto"], ["--publication-label", "source:other"], ["--pr", "7"], ["tracked-worktree"],
])
def test_ready_rejects_legacy_or_effect_expansion_options(author: cfg.Config, extra: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["pr-author-ready", "--repo", REPO, "--number", "7", "--json", *extra])
    assert exc.value.code == 2


@pytest.mark.parametrize("claimant", [True, False])
def test_ready_actual_native_frontend_worktrees_and_provider(
    backend: Backend, author: cfg.Config, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, capsys: pytest.CaptureFixture[str], claimant: bool,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import __main__ as frontend, native_author

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        with output.capture_json_output() as capture, redirect_stdout(StringIO()):
            code = cli.main(argv[1:])
        return subprocess.CompletedProcess(argv, code, capture.getvalue(), "")

    monkeypatch.setattr(native_author, "run_command", run)
    monkeypatch.setattr(frontend, "_WATCH_INVOCATION", None)
    if not claimant:
        monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: (None, "no claimant"))
    selector = tmp_path / "worktrees"
    selector.touch()
    assert frontend.main([
        "ready", "--repo", REPO, "--number", "7",
        "--worktrees-command", str(selector), "--json",
    ]) == (0 if claimant else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == pr_readiness.SCHEMA and result["ok"] is claimant
    if claimant:
        assert backend.methods == ["GET", "PATCH", "GET"]
    else:
        assert result["actions"]["draft"]["status"] == "not-attempted"
        assert result["provider"] is None and result["error"] == "no claimant"
        assert not backend.methods


def test_ready_actual_frontend_without_registered_project_keeps_refusal(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import __main__ as frontend, native_author

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", None)
    policy = tmp_path / "worker.json"
    policy.write_text(json.dumps({
        "repo_name": "pr-worker",
        "repos": {"pr-worker": {"anchor": str(tmp_path), "worktree_root": "",
                              "pr": {"enabled": True, "provider": "azure-devops",
                                     "repository": REPO, "api_base": ORG}}},
    }), encoding="utf-8")
    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv[1])
        with output.capture_json_output() as capture, redirect_stdout(StringIO()):
            code = cli.main(argv[1:])
        return subprocess.CompletedProcess(argv, code, capture.getvalue(), "")

    monkeypatch.setattr(native_author, "run_command", run)
    monkeypatch.setattr(frontend, "_WATCH_INVOCATION", None)
    selector = tmp_path / "worktrees"
    selector.touch()
    assert frontend.main([
        "ready", "--repo", REPO, "--number", "7", "--config", str(policy),
        "--worktrees-command", str(selector), "--json",
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == pr_readiness.SCHEMA and result["version"] == 1
    assert result["actions"]["draft"]["status"] == "not-attempted"
    assert result["provider"] is None and "no registered invocation project" in result["error"]
    assert calls == ["pr-author-capabilities", "pr-author-ready"]
    assert not backend.methods


def test_ready_project_resolution_exception_and_strict_syntax(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any) -> None:
        raise ValueError("project registry unavailable")

    monkeypatch.setattr(cli.front_door_cli, "resolve_invocation_project", fail)
    argv = ["pr-author-ready", "--repo", REPO, "--number", "7", "--json"]
    with output.capture_json_output() as capture:
        assert cli.main(argv) == 1
    result = json.loads(capture.getvalue())
    assert result == pr_readiness.outcome(REPO, 7, error="project registry unavailable")
    with pytest.raises(SystemExit) as exc:
        cli.main([*argv, "--auto"])
    assert exc.value.code == 2
