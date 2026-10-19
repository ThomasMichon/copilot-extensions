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
        self.jobs: dict[int, list[dict]] = {}
        self.closed = [{"number": 123}]
        self.pr = {
            "number": 123, "state": "closed", "merged": True, "merge_commit_sha": MERGED,
            "merged_at": None,
            "base": {"ref": "main"}, "head": {
                "ref": "release/promote-500", "sha": HEAD, "repo": {"full_name": REPO},
            }, "user": {"login": "example"},
        }

    def __call__(self, path: str, *, timeout: float, body: dict | None = None) -> object:
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
        if path == "graphql":
            assert body is not None and "states:CLOSED" in body["query"]
            return retirement_page([retirement_row(self.pr)] if self.closed and not self.pr["merged"] else [])
        if relative.startswith("pulls?state=closed&base=main&head="):
            return copy.deepcopy(self.closed)
        if relative == "pulls/123":
            return copy.deepcopy(self.pr)
        if relative.startswith("actions/workflows/ci.yml/runs?event=push&head_sha="):
            return {"total_count": len(self.cis), "workflow_runs": copy.deepcopy(self.cis)}
        if relative.startswith("actions/workflows/validate-and-promote.yml/runs?created="):
            return {"total_count": len(self.generations), "workflow_runs": copy.deepcopy(self.generations)}
        if relative.startswith("actions/workflows/validate-and-promote.yml/runs?status=in_progress&"):
            return {"total_count": len(self.running), "workflow_runs": copy.deepcopy(self.running)}
        if relative.startswith("actions/runs/") and "/jobs?filter=all" in relative:
            identifier = int(relative.split("/")[2])
            jobs = self.jobs.get(identifier, [])
            return {"total_count": len(jobs), "jobs": copy.deepcopy(jobs)}
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
    backend.jobs[501] = admitted_jobs()
    result = dispatch.pending_source(REPO, completed_promotion_run=500, api=backend)
    assert result.source_sha is None and result.reason == "newer-generation-owns-progress"


def test_resume_control_merge_drains_previously_paused_source_without_a_new_push() -> None:
    backend = Backend()
    backend.pr["head"]["ref"] = "release-pipeline/state-123"
    assert dispatch.pending_source(REPO, closed_main_pr=123, api=backend).source_sha == DEV


def test_closed_event_does_not_duplicate_another_generation_but_can_drain_its_own_retirement() -> None:
    backend = Backend()
    backend.generations.append({
        **backend.promotion, "id": 501, "status": "in_progress",
        "created_at": "2026-10-18T00:01:00Z",
    })
    backend.jobs[501] = admitted_jobs()
    assert dispatch.pending_source(REPO, closed_main_pr=123, api=backend).source_sha is None
    backend.generations = [backend.promotion]
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


def admitted_jobs() -> list[dict]:
    return [
        {"id": 10, "run_attempt": 1, "name": "gate (confirm this is a dev commit)",
         "status": "completed", "conclusion": "success"},
        {"id": 11, "run_attempt": 1, "name": "full - example",
         "status": "completed", "conclusion": "failure", "started_at": "2026-10-18T00:01:01Z"},
    ]


def retirement_row(pr: dict) -> dict:
    return {
        "number": pr["number"], "state": "CLOSED", "merged": False,
        "headRefName": pr["head"]["ref"], "headRefOid": pr["head"]["sha"],
        "headRepository": {"nameWithOwner": pr["head"]["repo"]["full_name"]},
        "author": pr["user"],
    }


def retirement_page(rows: list[dict], *, total: int | None = None, cursor: str | None = None) -> dict:
    return {"data": {"repository": {"pullRequests": {
        "nodes": copy.deepcopy(rows), "totalCount": len(rows) if total is None else total,
        "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
    }}}}


@pytest.mark.parametrize("event", ["completion", "closure"])
@pytest.mark.parametrize("admitted", [True, False])
def test_only_a_newer_admitted_generation_suppresses_a_delayed_terminal_wake(
    event: str, admitted: bool,
) -> None:
    backend = Backend()
    backend.generations.append({
        **backend.promotion, "id": 501, "conclusion": "failure",
        "created_at": "2026-10-18T00:01:00Z",
    })
    # B was queued during A, then failed admission; A's merge-only retry succeeds.
    backend.jobs[501] = admitted_jobs()
    if not admitted:
        backend.jobs[501][0]["conclusion"] = "failure"
        backend.jobs[501][1]["conclusion"] = "skipped"
    kwargs = {"completed_promotion_run": 500} if event == "completion" else {"closed_main_pr": 123}
    result = dispatch.pending_source(REPO, api=backend, **kwargs)
    assert (result.source_sha is None) is admitted
    assert result.reason == (
        "newer-generation-owns-progress" if admitted else "deferred-source-ci-succeeded"
    )


def test_resume_merge_queues_behind_a_finishing_paused_noop_without_recursing() -> None:
    backend = Backend()
    backend.pr["head"]["ref"] = "release-pipeline/state-123"
    backend.running = [{**backend.promotion, "status": "in_progress"}]
    backend.jobs[500] = admitted_jobs()
    backend.jobs[500][1]["conclusion"] = "skipped"
    assert dispatch.pending_source(REPO, closed_main_pr=123, api=backend).source_sha == DEV
    backend.closed = []
    assert dispatch.pending_source(REPO, completed_promotion_run=500, api=backend).source_sha is None


@pytest.mark.parametrize("event", ["ci", "closure", "admission"])
def test_retirement_history_applies_to_every_automatic_wake_and_queued_admission(event: str) -> None:
    backend = Backend()
    backend.pr["merged"] = False
    backend.candidate = state(DEV)
    if event == "admission":
        with pytest.raises(dispatch.PromotionDispatchError, match="retired source"):
            dispatch.require_automatic_source(REPO, DEV, api=backend)
    else:
        kwargs = {"source_ci_run": 600} if event == "ci" else {"closed_main_pr": 123}
        result = dispatch.pending_source(REPO, api=backend, **kwargs)
        assert result.source_sha is None and result.reason == "retired-source-unchanged"


def test_admission_requires_gate_and_source_work_from_the_same_attempt() -> None:
    backend = Backend()
    backend.jobs[501] = admitted_jobs()
    backend.jobs[501][1]["run_attempt"] = 2
    assert not dispatch._admitted(lambda path: backend(f"repos/{REPO}/{path}", timeout=30), {"id": 501})


@pytest.mark.parametrize("status,conclusion,started,expected", [
    ("queued", None, None, False),
    ("completed", "skipped", "2026-10-18T00:01:01Z", False),
    ("completed", "cancelled", "2026-10-18T00:01:01Z", True),
    ("in_progress", None, "2026-10-18T00:01:01Z", True),
])
def test_started_source_work_distinguishes_admission_from_waiting_or_paused_noops(
    status: str, conclusion: str | None, started: str | None, expected: bool,
) -> None:
    backend = Backend()
    backend.jobs[501] = admitted_jobs()
    backend.jobs[501][1].update(status=status, conclusion=conclusion, started_at=started)
    assert dispatch._admitted(
        lambda path: backend(f"repos/{REPO}/{path}", timeout=30), {"id": 501},
    ) is expected


def test_admission_reads_all_job_attempt_pages_before_deciding() -> None:
    jobs = [
        {"id": number, "run_attempt": 1, "name": "unrelated", "status": "completed",
         "conclusion": "skipped"}
        for number in range(100, 199)
    ] + [admitted_jobs()[0]]
    calls = []

    def request(path: str) -> object:
        calls.append(path)
        return {"total_count": 101, "jobs": jobs if path.endswith("page=1") else [admitted_jobs()[1]]}

    assert dispatch._admitted(request, {"id": 501})
    assert calls == [
        "actions/runs/501/jobs?filter=all&per_page=100&page=1",
        "actions/runs/501/jobs?filter=all&per_page=100&page=2",
    ]


@pytest.mark.parametrize("malformed", [
    {"total_count": 2, "jobs": []},
    {"total_count": 2, "jobs": [admitted_jobs()[0], admitted_jobs()[0]]},
    {"total_count": 1, "jobs": [{**admitted_jobs()[0], "run_attempt": True}]},
    {"total_count": 1, "jobs": [{**admitted_jobs()[0], "conclusion": None}]},
    {"total_count": 1, "jobs": [{**admitted_jobs()[1], "started_at": "invalid"}]},
])
def test_unavailable_admission_evidence_refuses_instead_of_guessing(malformed: dict) -> None:
    with pytest.raises(dispatch.PromotionDispatchError):
        dispatch._admitted(lambda path: malformed, {"id": 501})


def test_retirement_paginates_closed_history_and_binds_candidate_bytes() -> None:
    backend = Backend()
    backend.pr["merged"] = False
    backend.candidate = state(DEV)
    ordinary = [retirement_row({**backend.pr, "number": number, "head": {
        **backend.pr["head"], "ref": f"control/{number}",
    }}) for number in range(200, 300)]
    calls = []

    def api(path: str, *, timeout: float, body: dict | None = None) -> object:
        if path == "graphql":
            calls.append(body["variables"]["cursor"])
            if body["variables"]["cursor"] is None:
                return retirement_page(ordinary, total=101, cursor="next")
            return retirement_page([retirement_row(backend.pr)], total=101)
        return backend(path, timeout=timeout)

    result = dispatch.pending_source(REPO, source_ci_run=600, api=api)
    assert result.reason == "retired-source-unchanged"
    assert calls == [None, "next"]
    assert f"repos/{REPO}/contents/.github/release-pipeline-state.json?ref={HEAD}" in backend.calls


def test_retirement_record_readback_mismatch_is_an_error_not_permission_to_dispatch() -> None:
    backend = Backend()
    backend.pr["merged"] = False

    def api(path: str, *, timeout: float, body: dict | None = None) -> object:
        value = backend(path, timeout=timeout, body=body)
        if path.endswith("/pulls/123"):
            value["head"]["sha"] = DEV
        return value

    with pytest.raises(dispatch.PromotionDispatchError, match="history changed"):
        dispatch.pending_source(REPO, source_ci_run=600, api=api)


def test_cli_rechecks_queued_automatic_source_without_writing_success_on_retirement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    backend = Backend()
    backend.pr["merged"] = False
    backend.candidate = state(DEV)
    monkeypatch.setattr(lifecycle, "_api", backend)
    output = tmp_path / "output"
    assert lifecycle.main([
        "--repository", REPO, "--require-idle", "--automatic-source", DEV,
        "--github-output", str(output),
    ]) == 1
    assert "retired source is unchanged" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("change", [
    "errors", "missing", "truncated", "wrong-state", "merged", "duplicate",
    "invalid-cursor", "repeated-cursor",
])
def test_retirement_connection_cannot_hide_incomplete_or_malformed_history(change: str) -> None:
    backend = Backend()
    row = retirement_row(backend.pr)
    value = retirement_page([row])
    connection = value["data"]["repository"]["pullRequests"]
    if change == "errors":
        value["errors"] = [{"message": "incomplete read"}]
    elif change == "missing":
        value = {"data": {"repository": None}}
    elif change == "truncated":
        connection["totalCount"] = 2
    elif change == "wrong-state":
        connection["nodes"][0]["state"] = "MERGED"
    elif change == "merged":
        connection["nodes"][0]["merged"] = True
    elif change == "duplicate":
        connection["nodes"].append(copy.deepcopy(row))
        connection["totalCount"] = 2
    elif change == "invalid-cursor":
        connection["pageInfo"]["hasNextPage"] = True
    else:
        connection["pageInfo"] = {"hasNextPage": True, "endCursor": "same"}
    with pytest.raises(dispatch.PromotionDispatchError):
        dispatch._retirements(lambda *args, **kwargs: copy.deepcopy(value), REPO)


def test_retirement_inventory_is_bounded_and_never_accepts_a_truncated_prefix() -> None:
    calls = []

    def request(path: str, *, body: dict) -> object:
        calls.append(body["variables"]["cursor"])
        row = retirement_row(Backend().pr)
        row["number"] += len(calls)
        return retirement_page([row], total=1001, cursor=f"cursor-{len(calls)}")

    with pytest.raises(dispatch.PromotionDispatchError, match="pagination limit"):
        dispatch._retirements(request, REPO)
    assert len(calls) == 10


@pytest.mark.parametrize("repository,source,wait", [
    ("example/..", DEV, 120), (REPO, "invalid", 120), (REPO, DEV, True), (REPO, DEV, 0),
])
def test_automatic_source_rejects_invalid_input_without_api_calls(
    repository: str, source: str, wait: float,
) -> None:
    def api(*args: object, **kwargs: object) -> object:
        pytest.fail("invalid automatic-source admission reached the API")

    with pytest.raises(dispatch.PromotionDispatchError):
        dispatch.require_automatic_source(repository, source, api=api, wait_seconds=wait)
