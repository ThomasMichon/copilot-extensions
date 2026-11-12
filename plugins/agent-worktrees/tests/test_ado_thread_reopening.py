"""Reopening through the existing native owner, transport and consumed ledger."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest

from agent_worktrees import config as cfg, output, pr_ado_remediation, pr_author_cli, pr_cli, pr_config
from agent_worktrees import __main__ as cli
from agent_worktrees.providers import azure_devops as azure
from agent_worktrees.providers.azure_thread_resolution import resolve
from test_ado_thread_resolution import ACTOR, ORG, REPO, Backend, arguments, configure


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Backend:
    server = Backend(reopening=True)
    configure(monkeypatch, tmp_path, server)
    return server


def execute(backend: Backend, **kwargs: Any) -> dict:
    return resolve(azure.AzureDevOpsProvider(), REPO, 7, 9, api_base=ORG, token="fixture",
                   reopening=True, **kwargs)


def status(result: dict) -> str:
    assert result["schema"] == "agent-worktrees.pr-thread-reopen"
    return result["actions"]["reopening"]["status"]


def argv(expected: dict) -> list[str]:
    return ["pr-thread-reopen", *arguments(expected)[1:]]


@pytest.mark.parametrize("initial", ["fixed", "wontFix", "closed", "byDesign", 2, 3, 4, 5])
def test_actual_owner_reopens_each_resolved_status_only(backend: Backend, initial: Any) -> None:
    backend.rows[0]["status"] = initial
    before, sibling, comments = deepcopy(backend.pull), deepcopy(backend.rows[1]), deepcopy(backend.rows[0]["comments"])
    expected = backend.expectations()
    with output.capture_json_output() as capture:
        assert cli.main(argv(expected)) == 0
    result = json.loads(capture.getvalue())
    assert status(result) == "confirmed" and result["actor"] == ACTOR
    assert backend.pull == before and backend.rows[1] == sibling
    assert backend.rows[0]["comments"] == comments and backend.rows[0]["status"] == "active"
    assert backend.writes[0][2] == {"status": "active"} and len(backend.writes) == 1


@pytest.mark.parametrize("damage", [
    "active", "pending", "numeric-active", "numeric-pending", "boolean", "unknown", "deleted",
    "system", "vote", "count", "continuation", "duplicate", "missing", "no-comments",
    "no-iteration", "iteration", "parent", "principal", "permission", "pr-number",
    "thread", "head", "base", "source-ref", "target-ref", "source-repository",
])
def test_inapplicable_incomplete_or_stale_evidence_refuses_without_effect(backend: Backend, damage: str) -> None:
    expected = backend.expectations()
    row = backend.rows[0]
    if damage in ("active", "pending", "unknown"):
        row["status"] = damage
    elif damage in ("numeric-active", "numeric-pending", "boolean"):
        row["status"] = {"numeric-active": 1, "numeric-pending": 6, "boolean": True}[damage]
    elif damage == "deleted":
        row["isDeleted"] = True
    elif damage == "system":
        row["comments"][0]["commentType"] = 2
    elif damage == "vote":
        row["properties"] = {"CodeReviewThreadType": {"$value": "VoteUpdate"}}
    elif damage == "count":
        backend.count = False
    elif damage == "continuation":
        backend.continuation = True
    elif damage == "duplicate":
        backend.rows.append(deepcopy(row))
    elif damage == "missing":
        backend.rows = backend.rows[1:]
    elif damage == "no-comments":
        row.pop("comments")
    elif damage == "no-iteration":
        row.pop("pullRequestThreadContext")
    elif damage == "iteration":
        row["pullRequestThreadContext"]["iterationContext"]["secondComparingIteration"] = 99
    elif damage == "parent":
        row["comments"][0]["parentCommentId"] = 99
    elif damage == "principal":
        backend.actor = ""
    elif damage == "permission":
        backend.allowed = False
    elif damage == "pr-number":
        backend.pull["pullRequestId"] = 8
    elif damage == "thread":
        row["comments"][0]["content"] = "Concurrent edit"
    elif damage == "head":
        backend.pull["lastMergeSourceCommit"]["commitId"] = "e" * 40
        backend.iterations[0]["sourceRefCommit"]["commitId"] = "e" * 40
    elif damage == "base":
        backend.change_base()
    elif damage.endswith("-ref"):
        backend.pull["sourceRefName" if damage == "source-ref" else "targetRefName"] += "-moved"
    else:
        source = deepcopy(backend.pull["repository"])
        source["id"] = "55555555-5555-5555-5555-555555555555"
        backend.pull["forkSource"] = {"repository": source}
    assert status(execute(backend, **expected)) == "not-attempted" and not backend.writes


@pytest.mark.parametrize("movement", ["candidate", "principal", "permission", "thread"])
def test_final_recheck_refuses_movement(backend: Backend, movement: str) -> None:
    expected = backend.expectations()
    backend.pr_reads = 0
    def change(server: Backend) -> None:
        if server.pr_reads != 4:
            return
        if movement == "candidate":
            server.change_base()
        elif movement == "principal":
            server.actor = "55555555-5555-5555-5555-555555555555"
        elif movement == "permission":
            server.allowed = False
        else:
            server.rows[0]["comments"][0]["content"] = "Late edit"
    backend.on_pr = change
    assert status(execute(backend, **expected)) == "not-attempted" and not backend.writes


def test_stable_target_advance_does_not_require_rewriting_historical_iteration(backend: Backend) -> None:
    backend.change_base()
    assert backend.iterations[0]["targetRefCommit"]["commitId"] == "b" * 40
    assert status(execute(backend, **backend.expectations())) == "confirmed"
    assert len(backend.writes) == 1


@pytest.mark.parametrize("fault", [
    "reject", "timeout", "lost", "malformed", "wrong-response", "edited-comment", "readback",
    "changed-base", "truncated-patch", "truncated-readback", "bool-response", "float-response",
    "bool-readback", "reclosed",
])
def test_rejection_or_unknown_is_never_upgraded_by_later_active_observation(backend: Backend, fault: str) -> None:
    expected = backend.expectations()
    backend.fault = fault
    original = execute(backend, **expected)
    assert status(original) == ("failed" if fault == "reject" else "unknown")
    backend.fault = ""
    backend.rows[0]["status"] = "active"
    recovered = execute(backend, **expected)
    assert status(recovered) == status(original) and recovered["reconciled"]
    assert recovered["error"] == original["error"] and len(backend.writes) == 1
    conflict = execute(backend, **{**expected, "expected_thread": "sha256:" + "0" * 64})
    assert conflict["reason"] == "operation-conflict" and conflict["original_operation"] == original
    assert len(backend.writes) == 1


def test_confirmed_result_remains_original_after_reclosure(backend: Backend) -> None:
    expected = backend.expectations()
    original = execute(backend, **expected)
    backend.rows[0]["status"] = "closed"
    recovered = execute(backend, **expected)
    assert status(recovered) == "confirmed" and recovered["reconciled"]
    assert recovered["observed_thread"] == original["observed_thread"]
    assert recovered["reconciliation_observation"]["thread"]["status"] == "closed"
    assert len(backend.writes) == 1


@pytest.mark.parametrize("after_dispatch", [False, True])
def test_interrupted_key_stays_consumed_and_unknown(backend: Backend, monkeypatch: pytest.MonkeyPatch,
                                                  after_dispatch: bool) -> None:
    expected = backend.expectations()
    if after_dispatch:
        backend.fault = "crash-after"
    else:
        consume = pr_ado_remediation.Ledger.consume
        def crash(*args: Any, **kwargs: Any) -> None:
            consume(*args, **kwargs)
            raise SystemExit(17)
        monkeypatch.setattr(pr_ado_remediation.Ledger, "consume", crash)
    with pytest.raises(SystemExit):
        execute(backend, **expected)
    backend.fault = ""
    backend.rows[0]["status"] = "active"
    recovered = execute(backend, **expected)
    assert status(recovered) == "unknown" and recovered["reconciled"]
    assert len(backend.writes) == int(after_dispatch)


def test_late_response_is_not_confirmation(backend: Backend) -> None:
    from agent_worktrees.providers.azure_remediation import AdoMutator
    expected = backend.expectations()
    backend.fault = "late"
    result = execute(backend, **expected, reader_factory=lambda *a: AdoMutator(*a, clock=lambda: backend.now))
    assert status(result) == "unknown" and len(backend.writes) == 1


def test_numeric_active_response_is_supported_but_boolean_is_not(backend: Backend) -> None:
    expected = backend.expectations()
    backend.fault = "numeric-response"
    assert status(execute(backend, **expected)) == "confirmed" and len(backend.writes) == 1


def test_close_and_reopen_keys_do_not_reinterpret_consumed_operations(backend: Backend) -> None:
    backend.rows[0]["status"] = "active"
    close_expected = backend.expectations()
    backend.desired_status = "closed"
    original = resolve(azure.AzureDevOpsProvider(), REPO, 7, 9, api_base=ORG, token="fixture", **close_expected)
    assert original["actions"]["resolution"]["status"] == "confirmed"
    backend.desired_status = "active"
    reopen_expected = {**backend.expectations(), "operation_id": "intentional-reopen:9"}
    assert status(execute(backend, **reopen_expected)) == "confirmed"
    recovered = resolve(azure.AzureDevOpsProvider(), REPO, 7, 9, api_base=ORG, token="fixture", **close_expected)
    assert recovered["reconciled"] and recovered["observed_thread"] == original["observed_thread"]
    assert len(backend.writes) == 2


@pytest.mark.parametrize("provider", ["github", "gitea"])
def test_unsupported_binding_never_reaches_token_or_legacy_bulk(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    from agent_worktrees import pr_thread_resolution, providers
    target = cfg.RepoConfig(anchor="", worktree_root="", pr=cfg.PRConfig(enabled=True, provider=provider))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *_: pr_config.ForeignRepoResolution(target, REPO))
    monkeypatch.setattr(providers, "get_provider", lambda *_: pytest.fail("legacy bulk fallback"))
    assert status(pr_thread_resolution.resolve(None, REPO, 7, 9, reopening=True, **backend.expectations())) == "not-attempted"
    assert not backend.writes


def test_claimant_and_worker_policy_refuse_before_native_dispatch(
    backend: Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = backend.expectations()
    monkeypatch.setattr(pr_cli, "require_claimant_worktree", lambda *_: (None, "no claimant"))
    monkeypatch.setattr(pr_author_cli, "load_author_config", lambda *a, **k: pytest.fail("policy fallback"))
    with output.capture_json_output() as capture:
        assert cli.main(argv(expected)) == 1
    assert status(json.loads(capture.getvalue())) == "not-attempted" and not backend.writes


@pytest.mark.parametrize("damage", ["none", "wrong-marker", "wrong-target", "disabled", "provider"])
def test_actual_delivered_policy_selects_only_its_enabled_target(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, damage: str,
) -> None:
    from agent_worktrees import providers
    expected = backend.expectations()
    policy = {"enabled": True, "provider": "azure-devops", "repository": REPO, "api_base": ORG}
    body = {"repo_name": "pr-worker", "repos": {"pr-worker": {"pr": policy}}}
    if damage == "wrong-marker":
        body["repo_name"] = "caller"
    elif damage == "wrong-target":
        policy["repository"] = "other/repository"
    elif damage == "disabled":
        policy["enabled"] = False
    elif damage == "provider":
        policy["provider"] = "github"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    def token(repo: str, selected: cfg.PRConfig) -> str:
        assert repo == REPO and selected.repository == REPO and selected.provider == "azure-devops"
        return "fixture"
    monkeypatch.setattr(providers, "account_token_for_slug", token)
    with output.capture_json_output() as capture:
        assert cli.main([*argv(expected), "--config", str(path)]) == (0 if damage == "none" else 1)
    result = json.loads(capture.getvalue())
    assert status(result) == ("confirmed" if damage == "none" else "not-attempted")
    assert len(backend.writes) == (1 if damage == "none" else 0)


def test_missing_project_retains_reopening_refusal_envelope(
    backend: Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = backend.expectations()
    def unavailable(*args: Any) -> None:
        raise ValueError("project registry unavailable")
    monkeypatch.setattr(cli.front_door_cli, "resolve_invocation_project", unavailable)
    with output.capture_json_output() as capture:
        assert cli.main(argv(expected)) == 1
    result = json.loads(capture.getvalue())
    assert status(result) == "not-attempted" and result["reason"] == "project-unavailable"
    assert result["thread"] == 9 and not backend.writes


def test_legacy_status_owner_still_rejects_reopening(backend: Backend) -> None:
    from agent_worktrees.providers.azure_remediation import AdoMutator
    with pytest.raises(pr_ado_remediation.RemediationError, match="Unsupported native thread status"):
        AdoMutator(azure.AzureDevOpsProvider(), REPO, 7, ORG, "fixture").thread_status(9, "active")
    assert not backend.writes


def test_actual_frontend_refuses_prior_receiver_across_process(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    import agent_worktrees
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "agent-pull-requests" / "src"))
    from agent_pull_requests import __main__ as frontend, native_cli
    expected = backend.expectations()
    args = frontend.build_parser().parse_args([
        "reopen-thread", *arguments(expected)[1:], "--worktrees-command", "selected-prior-owner",
    ])
    code = (
        "import sys; from agent_worktrees import pr_author_cli; "
        "from test_ado_thread_resolution import process_main; "
        "original=pr_author_cli.capabilities; "
        "pr_author_cli.capabilities=lambda *a: {**original(*a), "
        "'operations': {k:v for k,v in original(*a)['operations'].items() if k != 'thread-reopen'}}; "
        "assert sys.argv[2] == 'pr-author-capabilities'; "
        "raise SystemExit(process_main(sys.argv[1],sys.argv[2:]))"
    )
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((
        str(Path(agent_worktrees.__file__).parent.parent), str(Path(__file__).parent),
        os.environ.get("PYTHONPATH", ""),
    )))
    assert native_cli.run(args, prefix_factory=lambda: [
        sys.executable, "-c", code, str(tmp_path),
    ]) == 1
    body = json.loads(capsys.readouterr().out)
    assert status(body) == "not-attempted" and not (tmp_path / "writes").exists()


@pytest.mark.parametrize("extra", [
    ["--all"], ["--pr", "7"], ["tracked"], ["--thread", "0"],
    ["--expected-candidate", "head-only"], ["--operation-id", "invalid key"],
])
def test_strict_reopening_parser_does_not_expand_selection(backend: Backend, extra: list[str]) -> None:
    selected = argv(backend.expectations())
    if extra[0] in ("--all", "--pr", "tracked"):
        with pytest.raises(SystemExit) as caught:
            cli.main([*selected, *extra])
        assert caught.value.code == 2
    else:
        with output.capture_json_output() as capture:
            assert cli.main([*selected, *extra]) == 1
        assert status(json.loads(capture.getvalue())) == "not-attempted"
    assert not backend.writes
