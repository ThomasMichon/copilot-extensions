"""Explicit reviewer requests compose actual parsers, target policy and provider."""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import subprocess

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg, output, pr_cli, pr_reviewer_request, providers
from agent_worktrees.providers import github

REPO = "example/repository"
BOT = "copilot-pull-request-reviewer[bot]"


class Backend:
    def __init__(self):
        self.pull = {
            "number": 7, "state": "open", "merged": False, "draft": False,
            "base": {"repo": {"full_name": REPO}}, "title": "Keep title",
            "labels": ["paused"], "requested_reviewers": [{"login": "another-reviewer"}],
            "auto_merge": {"merge_method": "squash"},
        }
        self.calls = []
        self.requests = []
        self.fault = ""

    def run(self, argv, **kwargs):
        assert argv[:4] == ["gh", "api", "--hostname", "forge.example"]
        assert kwargs["env"]["GH_HOST"] == "forge.example"
        self.calls.append(argv)
        if "-X" not in argv:
            assert argv[4:] == [f"repos/{REPO}/pulls/7"]
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.pull), "")
        assert argv[4:] == [
            "-X", "POST", f"repos/{REPO}/pulls/7/requested_reviewers",
            "-f", f"reviewers[]={BOT}", "--include",
        ]
        self.requests.append(BOT)
        if self.fault == "reject":
            return subprocess.CompletedProcess(argv, 1, "HTTP/2.0 422 Unprocessable\n\n{}", "")
        if self.fault == "timeout":
            raise subprocess.TimeoutExpired(argv, 30)
        if self.fault == "lost":
            return subprocess.CompletedProcess(argv, 1, "", "connection closed")
        status = "503 Service Unavailable" if self.fault == "server" else "200 OK"
        data = {**self.pull, "number": 8} if self.fault == "identity" else self.pull
        body = "[]" if self.fault == "malformed" else json.dumps(data)
        return subprocess.CompletedProcess(argv, 0, f"HTTP/2.0 {status}\r\nX-Test: true\r\n\r\n{body}", "")


@pytest.fixture
def backend(monkeypatch):
    value = Backend()
    monkeypatch.setattr(github, "run_cli", value.run)
    return value


@pytest.fixture
def author(monkeypatch, tmp_path):
    caller = cfg.RepoConfig(anchor=str(tmp_path), worktree_root="",
                           pr=cfg.PRConfig(enabled=True, provider="azure-devops"))
    target = cfg.RepoConfig(anchor="", worktree_root="", pr=cfg.PRConfig(
        enabled=True, provider="github", repository=REPO, api_base="forge.example", reviewer="copilot",
    ))
    config = cfg.Config(srcroot=str(tmp_path), machine="test", platform="linux",
                        repo_name="caller", repos={"caller": caller}, explicit_pr_target=target)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cli.front_door_cli, "resolve_invocation_project", lambda *a: True)
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: ("claimant", ""))

    def token(repo, policy):
        assert repo == REPO and policy == target.pr
        return "fixture-only"

    monkeypatch.setattr(providers, "account_token_for_slug", token)
    return config


def invoke():
    with output.capture_json_output() as capture:
        code = cli.main(["pr-author-nudge", "--repo", REPO, "--number", "7", "--json"])
    return code, json.loads(capture.getvalue())


def test_actual_target_owner_acceptance_is_not_a_verdict_or_other_mutation(author, backend):
    before = deepcopy(backend.pull)
    code, result = invoke()
    assert code == 0 and result["requested"] is True
    assert result["actions"]["review_request"]["status"] == "accepted"
    assert result["provider"] == "github" and result["reviewer"] == BOT
    assert backend.requests == [BOT] and len(backend.calls) == 2
    assert backend.pull == before and "verdict" not in result


@pytest.mark.parametrize("damage", [
    {"number": True}, {"number": 8}, {"base": []},
    {"base": {"repo": {"full_name": "other/repository"}}},
    {"state": None}, {"state": "closed"}, {"merged": None}, {"merged": True},
    {"draft": True}, {"draft": None}, {"draft": 0},
])
def test_preflight_refuses_missing_malformed_terminal_or_draft_without_write(author, backend, damage):
    backend.pull.update(damage)
    code, result = invoke()
    assert code == 1 and result["actions"]["review_request"]["status"] == "not-attempted"
    assert result["error"] and not backend.requests


@pytest.mark.parametrize(("fault", "status"), [
    ("reject", "failed"), ("lost", "unknown"), ("timeout", "unknown"),
    ("server", "unknown"), ("malformed", "unknown"), ("identity", "unknown"),
])
def test_request_outcomes_never_replay_or_turn_transport_errors_into_rejections(
    author, backend, fault, status,
):
    backend.fault = fault
    code, result = invoke()
    assert code == 1 and result["actions"]["review_request"]["status"] == status
    assert result["repo"] == REPO and result["number"] == 7 and result["error"]
    assert result["requested"] is False and backend.requests == [BOT]


@pytest.mark.parametrize("reviewer", ["", "unmapped"])
def test_unbound_or_unmapped_is_normal_noop_without_credential_acquisition(author, monkeypatch, reviewer):
    target = author.explicit_pr_target
    selected = replace(author, explicit_pr_target=replace(target, pr=replace(target.pr, reviewer=reviewer)))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: pytest.fail("credential acquisition"))
    result = pr_reviewer_request.nudge(selected, REPO, 7)
    assert result["ok"] and not result["supported"] and not result["requested"]
    assert result["actions"]["review_request"]["status"] == "no-op"


@pytest.mark.parametrize("provider", ["azure-devops", "gitea"])
def test_unqualified_binding_never_uses_caller_provider_or_credentials(author, monkeypatch, provider):
    target = author.explicit_pr_target
    selected = replace(author, explicit_pr_target=replace(target, pr=replace(target.pr, provider=provider)))
    monkeypatch.setattr(providers, "get_provider", lambda *a: pytest.fail("unsupported provider"))
    assert not pr_reviewer_request.nudge(selected, REPO, 7)["ok"]


def test_missing_or_disabled_target_never_uses_caller_policy(author, monkeypatch):
    monkeypatch.setattr(providers, "get_provider", lambda *a: pytest.fail("unadmitted target"))
    assert not pr_reviewer_request.nudge(author, "other/repository", 7)["ok"]
    target = author.explicit_pr_target
    disabled = replace(author, explicit_pr_target=replace(target, pr=replace(target.pr, enabled=False)))
    assert not pr_reviewer_request.nudge(disabled, REPO, 7)["ok"]


@pytest.mark.parametrize("contents", [None, {}, {"repo_name": "pr-worker", "repos": {
    "pr-worker": {"pr": {"enabled": True, "provider": "github", "repository": "other/repository"}},
}}])
def test_invalid_delivered_policy_refuses_without_ambient_fallback(
    author, backend, tmp_path, contents,
):
    policy = tmp_path / "worker.json"
    if contents is not None:
        policy.write_text(json.dumps(contents), encoding="utf-8")
    with output.capture_json_output() as capture:
        assert cli.main([
            "pr-author-nudge", "--repo", REPO, "--number", "7",
            "--config", str(policy), "--json",
        ]) == 1
    result = json.loads(capture.getvalue())
    assert result["actions"]["review_request"]["status"] == "not-attempted"
    assert result["error"] and not backend.calls


@pytest.mark.parametrize("marker", [None, "caller"])
@pytest.mark.parametrize("command", ["pr-author-capabilities", "pr-author-nudge", "pr-author-ready", "pr-update"])
def test_reserved_worker_policy_requires_its_marker_before_ambient_target_resolution(
    author, backend, tmp_path, marker, command,
):
    policy = tmp_path / "worker.json"
    raw = {"repos": {"pr-worker": {"pr": {
        "enabled": True, "provider": "github", "repository": "other/repository",
    }}}}
    if marker is not None:
        raw["repo_name"] = marker
    policy.write_text(json.dumps(raw), encoding="utf-8")
    argv = [command, "--repo", REPO, "--config", str(policy), "--json"]
    if command != "pr-author-capabilities":
        argv.extend(["--number", "7"])
    with output.capture_json_output() as capture:
        assert cli.main(argv) == 1
    result = json.loads(capture.getvalue())
    assert "must identify its pr-worker repository" in result["error"]
    assert not backend.calls


@pytest.mark.parametrize("extra", [["--auto"], ["--reviewer", "someone"], ["--worktree", "other"]])
def test_explicit_parser_rejects_effect_expansion_and_tracked_selectors(author, extra):
    with pytest.raises(SystemExit) as caught:
        cli.main(["pr-author-nudge", "--repo", REPO, "--number", "7", "--json", *extra])
    assert caught.value.code == 2


@pytest.mark.parametrize("claimant", [True, False])
def test_actual_native_frontend_worktrees_and_github_owner(
    author, backend, monkeypatch, tmp_path, capsys, claimant,
):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import __main__ as frontend, native_author, native_reviewer_request

    def run(argv, **kwargs):
        with output.capture_json_output() as capture, redirect_stdout(StringIO()):
            code = cli.main(argv[1:])
        return subprocess.CompletedProcess(argv, code, capture.getvalue(), "")

    monkeypatch.setattr(native_author, "run_command", run)
    monkeypatch.setattr(native_reviewer_request, "run_command", run)
    monkeypatch.setattr(frontend, "_WATCH_INVOCATION", None)
    if not claimant:
        monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: (None, "no claimant"))
    selector = tmp_path / "worktrees"
    selector.touch()
    assert frontend.main([
        "nudge", "--repo", REPO, "--number", "7", "--worktrees-command", str(selector), "--json",
    ]) == (0 if claimant else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == pr_reviewer_request.SCHEMA
    assert result["actions"]["review_request"]["status"] == ("accepted" if claimant else "not-attempted")
    assert len(backend.requests) == int(claimant)


@pytest.mark.parametrize("failure", [False, True])
def test_projectless_dispatch_preserves_versioned_not_attempted_and_strict_syntax(monkeypatch, failure):
    def resolve(*args):
        if failure:
            raise ValueError("project registry unavailable")
        return False

    monkeypatch.setattr(cli.front_door_cli, "resolve_invocation_project", resolve)
    code, result = invoke()
    assert code == 1 and result["actions"]["review_request"]["status"] == "not-attempted"
    assert result["provider"] is None and result["error"]
    with pytest.raises(SystemExit) as caught:
        cli.main(["pr-author-nudge", "--repo", REPO, "--number", "7", "--json", "--auto"])
    assert caught.value.code == 2
