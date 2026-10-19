"""Deferred advances wake after verified merge/retirement, never by a no-op loop."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import pytest

import promotion_dispatch as dispatch
import promotion_lifecycle as lifecycle

REPO = "example/project"
RELEASED, DEV, HEAD, MERGED, CONTROL = (letter * 40 for letter in "abcde")


def state(source: str | None = RELEASED, *, paused: bool = False) -> dict:
    value = {
        "schema": "copilot-extensions.release-pipeline-state", "version": 1,
        "paused": paused, "last_promotion": {"dev_head": source} if source else None,
    }
    raw = json.dumps(value).encode()
    return {"type": "file", "encoding": "base64", "size": len(raw),
            "content": base64.b64encode(raw).decode()}


def ci(identifier: int = 600, **changes: object) -> dict:
    return {
        "id": identifier, "run_attempt": 1, "head_sha": DEV, "event": "push",
        "path": ".github/workflows/ci.yml", "status": "completed", "conclusion": "success",
        "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
        **changes,
    }


class Backend:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.active: list[dict] = []
        self.main = state()
        self.candidate = state()
        self.source = DEV
        self.cis = [ci()]
        self.source_ci = ci()
        self.promotion = {
            "id": 500, "run_attempt": 1, "head_sha": CONTROL,
            "created_at": "2026-10-18T00:00:00Z",
            "path": ".github/workflows/validate-and-promote.yml", "head_branch": "main",
            "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
        }
        self.generations = [copy.deepcopy(self.promotion)]
        self.running: list[dict] = []
        self.closed = [{"number": 123}]
        self.pr = {
            "number": 123, "state": "closed", "merged": True, "merge_commit_sha": MERGED,
            "base": {"ref": "main"}, "head": {
                "ref": "release/promote-500", "sha": HEAD, "repo": {"full_name": REPO},
            }, "user": {"login": "example"},
        }

    def __call__(self, path: str, *, timeout: float) -> object:
        self.calls.append(path)
        assert 0 < timeout <= 30
        relative = path.removeprefix(f"repos/{REPO}/")
        if relative.startswith("pulls?state=open&base=main"):
            return copy.deepcopy(self.active)
        if relative == "contents/.github/release-pipeline-state.json?ref=main":
            return copy.deepcopy(self.main)
        if relative == f"contents/.github/release-pipeline-state.json?ref={HEAD}":
            return copy.deepcopy(self.candidate)
        if relative == "git/ref/heads/dev":
            return {"object": {"type": "commit", "sha": self.source}}
        if relative == "actions/runs/500":
            return copy.deepcopy(self.promotion)
        if relative == "actions/runs/600":
            return copy.deepcopy(self.source_ci)
        if relative.startswith("pulls?state=closed&base=main&head="):
            return copy.deepcopy(self.closed)
        if relative == "pulls/123":
            return copy.deepcopy(self.pr)
        if relative.startswith("actions/workflows/ci.yml/runs?event=push&head_sha="):
            return {"total_count": len(self.cis), "workflow_runs": copy.deepcopy(self.cis)}
        if relative.startswith("actions/workflows/validate-and-promote.yml/runs?created="):
            return {"total_count": len(self.generations), "workflow_runs": copy.deepcopy(self.generations)}
        if relative == "actions/workflows/validate-and-promote.yml/runs?status=in_progress&per_page=100":
            return {"total_count": len(self.running), "workflow_runs": copy.deepcopy(self.running)}
        raise AssertionError(path)


def test_current_source_ci_uses_direct_run_evidence_without_index_lag() -> None:
    backend = Backend()
    backend.cis = []
    result = dispatch.pending_source(REPO, source_ci_run=600, api=backend)
    assert result.source_sha == DEV and result.reason == "current-source-ci-succeeded"
    assert not any("actions/workflows" in path for path in backend.calls)


def test_actual_merge_completion_recovers_a_ci_advance_suppressed_while_pr_was_open() -> None:
    backend = Backend()
    result = dispatch.pending_source(REPO, completed_promotion_run=500, api=backend)
    assert result.source_sha == DEV and result.reason == "deferred-source-ci-succeeded"
    assert any("pulls/123" in path for path in backend.calls)


def test_successful_noop_without_a_merged_pr_cannot_create_a_dispatch_loop() -> None:
    backend = Backend()
    backend.closed = []
    assert dispatch.pending_source(REPO, completed_promotion_run=500, api=backend).source_sha is None
    assert not any("contents/" in path for path in backend.calls)


def test_stale_completion_cannot_retry_a_newer_failed_generation() -> None:
    backend = Backend()
    backend.generations.append({
        **backend.promotion, "id": 501, "created_at": "2026-10-18T00:01:00Z",
        "conclusion": "failure",
    })
    result = dispatch.pending_source(REPO, completed_promotion_run=500, api=backend)
    assert result.source_sha is None and result.reason == "newer-generation-owns-progress"


def test_resume_control_merge_drains_previously_paused_source_without_a_new_push() -> None:
    backend = Backend()
    backend.pr["head"]["ref"] = "release-pipeline/state-123"
    assert dispatch.pending_source(REPO, closed_main_pr=123, api=backend).source_sha == DEV


def test_closed_event_does_not_duplicate_another_generation_but_can_drain_its_own_retirement() -> None:
    backend = Backend()
    backend.running = [{**backend.promotion, "id": 501, "status": "in_progress"}]
    assert dispatch.pending_source(REPO, closed_main_pr=123, api=backend).source_sha is None
    backend.running = [{**backend.promotion, "status": "in_progress"}]
    backend.pr["merged"] = False
    assert dispatch.pending_source(REPO, closed_main_pr=123, api=backend).source_sha == DEV


def test_retirement_never_automatically_retries_the_same_source() -> None:
    backend = Backend()
    backend.pr["merged"] = False
    backend.candidate = state(DEV)
    result = dispatch.pending_source(REPO, closed_main_pr=123, api=backend)
    assert result.source_sha is None and result.reason == "retired-source-unchanged"
    assert not any("actions/workflows/ci.yml" in path for path in backend.calls)
    backend.candidate = state(RELEASED)
    assert dispatch.pending_source(REPO, closed_main_pr=123, api=backend).source_sha == DEV


@pytest.mark.parametrize("paused,source", [(True, RELEASED), (False, DEV)])
def test_paused_or_already_current_source_has_no_duplicate_generation(paused: bool, source: str) -> None:
    backend = Backend()
    backend.main = state(source, paused=paused)
    assert dispatch.pending_source(REPO, source_ci_run=600, api=backend).source_sha is None


@pytest.mark.parametrize("status,conclusion", [
    ("queued", None), ("in_progress", None), ("completed", "failure"), ("completed", "cancelled"),
])
def test_pending_or_failed_current_ci_cannot_wake_a_promotion(status: str, conclusion: str | None) -> None:
    backend = Backend()
    backend.cis = [ci(status=status, conclusion=conclusion)]
    assert dispatch.pending_source(REPO, completed_promotion_run=500, api=backend).source_sha is None


def test_older_ci_completion_coalesces_to_the_current_successful_dev_source() -> None:
    backend = Backend()
    backend.source_ci["head_sha"] = RELEASED
    backend.cis = [ci(601)]
    assert dispatch.pending_source(REPO, source_ci_run=600, api=backend).source_sha == DEV


@pytest.mark.parametrize("change", [
    {"event": "pull_request"}, {"head_repository": {"full_name": "outside/fork"}},
    {"path": ".github/workflows/other.yml"},
])
def test_untrusted_or_unrelated_ci_cannot_request_a_generation(change: dict) -> None:
    backend = Backend()
    backend.source_ci.update(change)
    result = dispatch.pending_source(REPO, source_ci_run=600, api=backend)
    assert result.source_sha is None and result.reason == "source-ci-ineligible"


@pytest.mark.parametrize("change", [
    {"head_branch": "dev"}, {"path": ".github/workflows/other.yml"},
    {"head_repository": {"full_name": "outside/fork"}}, {"id": True},
])
def test_completion_must_name_the_canonical_main_promoter(change: dict) -> None:
    backend = Backend()
    backend.promotion.update(change)
    with pytest.raises(dispatch.PromotionDispatchError):
        dispatch.pending_source(REPO, completed_promotion_run=500, api=backend)


@pytest.mark.parametrize("change", [
    {"head": {"ref": "release/promote-501", "sha": HEAD, "repo": {"full_name": REPO}}},
    {"author": {"login": "outside"}, "user": {"login": "outside"}},
    {"head": {"ref": "release/promote-500", "sha": HEAD, "repo": {"full_name": None}}},
    {"merge_commit_sha": None}, {"merged": True, "state": "unknown"},
])
def test_closed_pr_evidence_cannot_drift_or_hide_missing_merge_identity(change: dict) -> None:
    backend = Backend()
    backend.pr.update(change)
    with pytest.raises(dispatch.PromotionDispatchError):
        dispatch.pending_source(REPO, completed_promotion_run=500, api=backend)


@pytest.mark.parametrize("malformed", [
    {}, {"type": "file", "encoding": "base64", "size": True, "content": "e30="},
    {"type": "file", "encoding": "base64", "size": 2, "content": "invalid!"},
])
def test_state_is_validated_before_any_dispatch(malformed: dict) -> None:
    backend = Backend()
    backend.main = malformed
    with pytest.raises((dispatch.PromotionDispatchError, ValueError)):
        dispatch.pending_source(REPO, source_ci_run=600, api=backend)


def test_ambiguous_source_ci_never_selects_an_arbitrary_green_run() -> None:
    backend = Backend()
    backend.cis = [ci(), ci(601)]
    with pytest.raises(dispatch.PromotionDispatchError, match="ambiguous"):
        dispatch.pending_source(REPO, completed_promotion_run=500, api=backend)


def test_cli_exposes_only_a_validated_source_and_active_pr_still_prevents_recovery_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    backend = Backend()
    monkeypatch.setattr(lifecycle, "_api", backend)
    output = tmp_path / "output"
    assert lifecycle.main([
        "--repository", REPO, "--completed-promotion-run", "500", "--github-output", str(output),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["dispatch"] is True
    assert output.read_bytes() == f"promotion_idle=true\ndispatch_sha={DEV}\n".encode()
    backend.active = [{
        "number": 124, "state": "open", "base": {"ref": "main"},
        "head": {"ref": "release/promote-501", "sha": HEAD, "repo": {"full_name": REPO}},
        "user": {"login": "example"},
    }]
    backend.calls.clear()
    held = tmp_path / "held"
    assert lifecycle.main([
        "--repository", REPO, "--completed-promotion-run", "500", "--github-output", str(held),
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dispatch"] is False and result["reason"] == "active-promotion"
    assert len(backend.calls) == 1
    assert held.read_bytes() == b"promotion_idle=false\n"
