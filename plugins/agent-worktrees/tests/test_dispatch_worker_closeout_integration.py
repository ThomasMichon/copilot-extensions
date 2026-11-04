"""Own-worker closeout traverses the actual finalizer while retaining its checkout."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent_worktrees import dispatch_worker_closeout as closeout
from agent_worktrees import dispatch_closeout_observation as observation
from agent_worktrees import config as cfg, finalize, finalize_open_pr_gate, tracking
from test_dispatch_worker_closeout import NATIVE, SOURCE
from test_resource_producer_finalize import _final_owner_seed as _final_owner_seed
from test_resource_producer_finalize import final_owner as final_owner
from test_transitive_finalize_integration import _git


@pytest.fixture
def own_checkout(final_owner, monkeypatch: pytest.MonkeyPatch) -> tuple:
    root, path, config = final_owner
    workspace = root / "own-worker"
    record = tracking.load_record(path)
    _git(
        "worktree", "add", "-b", record.branch, str(workspace), "origin/base",
        cwd=Path(config.default_repo.anchor),
    )
    record.worktree_path = str(workspace)
    record.creation_nonce = "own-worker-created"
    record.dispatch_attempt = tracking.DispatchAttempt(
        "task", "reservation", 1, "acp", "pool", record.execution_machine,
    )
    record.sessions = [tracking.SessionEntry(
        session_id=NATIVE, started_at="2026-01-01", activations=[tracking.SessionActivation(
            ordinal=1, started_at="2026-01-01", start_recorded_at="2026-01-01",
            source_identity=SOURCE,
        )],
    )]
    record.head_session = NATIVE
    record.resources.append(tracking.ResourceClaim(
        kind="session",
        ref=tracking.format_claim_ref(
            record.execution_machine, record.repo, record.worktree_id, session=NATIVE,
        ),
    ))
    tracking.save_record(record, path)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", NATIVE)
    monkeypatch.setattr(closeout, "_native_source", lambda native: SOURCE)
    monkeypatch.setattr(closeout, "read_workspace_record", lambda cwd: {"worktree": {
        "id": record.worktree_id, "path": str(workspace),
    }})
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
    return root, path, config, workspace


def test_actual_own_finalize_retains_checkout_session_and_dispatch_identity(own_checkout) -> None:
    _, path, _, workspace = own_checkout
    before = tracking.load_record(path)
    result = closeout.finalize_own("task", "reservation", NATIVE)
    current = tracking.load_record(path)
    assert result["status"] == current.status == "finalized"
    assert result["checkout_retained"] is True and workspace.is_dir()
    assert current.dispatch_attempt == before.dispatch_attempt
    assert current.sessions == before.sessions and current.resolved_head_session == NATIVE
    assert current.finalized_checkout is not None
    assert result["task_release"] is False and result["process_retirement"] is False


@pytest.mark.parametrize("inherited_selectors", [False, True])
def test_observer_rechecks_actual_checkout_after_worker_finalizes(
    own_checkout, monkeypatch, inherited_selectors,
) -> None:
    _, path, _, workspace = own_checkout
    record = tracking.load_record(path)
    closeout.finalize_own("task", "reservation", NATIVE)
    monkeypatch.setattr(observation, "read_exact_record", lambda *a: {
        "path": str(workspace), "creation_nonce": record.creation_nonce,
    })
    monkeypatch.setattr(observation, "read_workspace_record", lambda *a: {"worktree": {
        "id": record.worktree_id, "creation_nonce": record.creation_nonce,
    }})
    result = observation.observe(record.worktree_id, "task", "reservation", NATIVE)
    assert result["status"] == "finalized" and workspace.is_dir()
    if inherited_selectors:
        import shutil
        alternate = workspace.parent / "alternate"
        shutil.copytree(workspace, alternate)
        git_dir = _git("rev-parse", "--absolute-git-dir", cwd=workspace)
        index = _git("rev-parse", "--git-path", "index", cwd=workspace)
        alternate_index = alternate / "alternate-index"
        shutil.copyfile(index, alternate_index)
        monkeypatch.setenv("GIT_DIR", git_dir)
        monkeypatch.setenv("GIT_WORK_TREE", str(alternate))
        monkeypatch.setenv("GIT_INDEX_FILE", str(alternate_index))
    (workspace / "late-work.txt").write_text("New work after finalization\n", encoding="utf-8")
    with pytest.raises(ValueError, match="currently clean"):
        observation.observe(record.worktree_id, "task", "reservation", NATIVE)
    assert tracking.load_record(path).status == "finalized"


@pytest.mark.parametrize("fault", ["dirty", "unmerged", "open-pr", "outbound"])
def test_normal_source_pr_and_outbound_gates_still_block_own_closeout(
    own_checkout, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    _, path, _, workspace = own_checkout
    if fault in {"dirty", "unmerged"}:
        (workspace / "unfinished.txt").write_text("unfinished\n", encoding="utf-8")
        if fault == "unmerged":
            _git("add", "unfinished.txt", cwd=workspace)
            _git("commit", "-m", "Unmerged work", cwd=workspace)
    elif fault == "outbound":
        record = tracking.load_record(path)
        record.resources.append(tracking.ResourceClaim(kind="container", ref="owned-box"))
        tracking.save_record(record, path)
    else:
        monkeypatch.setattr(
            finalize_open_pr_gate,
            "assert_no_live_pr", lambda *a, **k: False,
        )
    with pytest.raises(ValueError):
        closeout.finalize_own("task", "reservation", NATIVE)
    current = tracking.load_record(path)
    assert current.status == "active" and workspace.is_dir()
    assert next(claim for claim in current.resources if claim.kind == "session").is_live


@pytest.mark.parametrize("boundary", ["freeze", "cleanup", "commit"])
def test_each_real_finalization_boundary_rechecks_own_worker_before_commit(
    own_checkout, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    _, path, _, workspace = own_checkout
    original = closeout.OwnWorker.require
    checks = 0
    target = {"freeze": 3, "cleanup": 4, "commit": 5}[boundary]

    def raced(self, record, config):
        nonlocal checks
        checks += 1
        if checks == target:
            record.dispatch_attempt = replace(record.dispatch_attempt, task_id="foreign")
        return original(self, record, config)

    monkeypatch.setattr(closeout.OwnWorker, "require", raced)
    with pytest.raises(ValueError):
        closeout.finalize_own("task", "reservation", NATIVE)
    assert checks == target
    assert tracking.load_record(path).status == "active" and workspace.is_dir()


@pytest.mark.parametrize("state", ["merged", "open", "unavailable"])
def test_normal_reconciliation_settles_only_an_authoritatively_merged_pr_claim(
    own_checkout, monkeypatch, state,
) -> None:
    from agent_worktrees import providers
    from agent_worktrees.providers import PullResult

    _, path, config, workspace = own_checkout
    config.repos[config.repo_name] = replace(config.default_repo, pr=cfg.PRConfig(
        enabled=True, provider="gitea", api_base="https://forge.example",
    ))
    record = tracking.load_record(path)
    sha = _git("rev-parse", "HEAD", cwd=workspace)
    url = "https://forge.example/example/project/pulls/17"
    record.prs = [tracking.PRRecord(
        state="open", number=17, branch=record.branch, provider="gitea",
        repo="example/project", url=url, head_sha=sha,
    )]
    record.resources.append(tracking.ResourceClaim(kind="pr", ref=url))
    tracking.save_record(record, path)
    reads = []

    class Provider:
        name = "gitea"

        def authority_endpoint(self, api_base=""):
            return api_base

        def get_pull(self, repo, number, **kwargs):
            reads.append((repo, number))
            if state == "unavailable":
                raise RuntimeError("Provider unavailable")
            return PullResult(
                number=17, state="closed" if state == "merged" else "open",
                merged=state == "merged", head_sha=sha, base_ref="base",
            )

    monkeypatch.setattr(providers, "get_provider", lambda name: Provider())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: None)
    if state == "merged":
        assert closeout.finalize_own("task", "reservation", NATIVE)["status"] == "finalized"
    else:
        with pytest.raises(ValueError):
            closeout.finalize_own("task", "reservation", NATIVE)
    current = tracking.load_record(path)
    claim = next(claim for claim in current.resources if claim.kind == "pr")
    assert reads and current.status == ("finalized" if state == "merged" else "active")
    assert claim.is_unsettled is (state != "merged") and workspace.is_dir()
