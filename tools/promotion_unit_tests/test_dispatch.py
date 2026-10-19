"""Deferred-promotion decisions against a closed in-memory provider double."""

from __future__ import annotations

import base64
import copy
import json
from urllib.parse import parse_qs

import pytest

import promotion_dispatch as dispatch

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
        "created_at": "2026-10-18T00:00:00Z",
        **changes,
    }


def retirement_row(pr: dict, *, state: str = "CLOSED") -> dict:
    return {
        "number": pr["number"], "state": state, "merged": False, "baseRefName": "main",
        "headRefName": pr["head"]["ref"], "headRefOid": pr["head"]["sha"],
        "headRepository": {"nameWithOwner": pr["head"]["repo"]["full_name"]},
        "author": pr["user"],
    }


def retirement_page(rows: list[dict], *, total: int | None = None, cursor: str | None = None) -> dict:
    return {"data": {"repository": {"pullRequests": {
        "nodes": copy.deepcopy(rows), "totalCount": len(rows) if total is None else total,
        "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
    }}}}


class Backend:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.active: list[dict] = []
        self.main = state()
        self.main_sha = CONTROL
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
        self.admissions: list[dict] = []
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
        if relative in (
            "contents/.github/release-pipeline-state.json?ref=main",
            f"contents/.github/release-pipeline-state.json?ref={self.main_sha}",
        ):
            return copy.deepcopy(self.main)
        if relative == f"contents/.github/release-pipeline-state.json?ref={HEAD}":
            return copy.deepcopy(self.candidate)
        if relative == "git/ref/heads/dev":
            return {"object": {"type": "commit", "sha": self.source}}
        if relative == "git/ref/heads/main":
            return {"object": {"type": "commit", "sha": self.main_sha}}
        if relative == "actions/runs/500":
            return copy.deepcopy(self.promotion)
        if relative == "actions/runs/600":
            return copy.deepcopy(self.source_ci)
        if path == "graphql":
            assert body is not None
            if "states:OPEN" in body["query"]:
                return retirement_page([retirement_row(row, state="OPEN") for row in self.active])
            assert "states:CLOSED" in body["query"]
            return retirement_page([retirement_row(self.pr)] if self.closed and not self.pr["merged"] else [])
        if relative.startswith("pulls?state=closed&base=main&head="):
            return copy.deepcopy(self.closed)
        if relative == "pulls/123":
            return copy.deepcopy(self.pr)
        if relative.startswith("actions/workflows/ci.yml/runs?head_sha="):
            return {"total_count": len(self.cis), "workflow_runs": copy.deepcopy(self.cis)}
        if relative.startswith("actions/artifacts?name="):
            name = parse_qs(relative.split("?", 1)[1])["name"][0]
            rows = [row for row in self.admissions if row["name"] == name]
            return {"total_count": len(rows), "artifacts": copy.deepcopy(rows)}
        if relative.startswith("actions/workflows/validate-and-promote.yml/runs?created="):
            return {"total_count": len(self.generations), "workflow_runs": copy.deepcopy(self.generations)}
        if relative.startswith("actions/workflows/validate-and-promote.yml/runs?status=in_progress&"):
            return {"total_count": len(self.running), "workflow_runs": copy.deepcopy(self.running)}
        if relative.startswith("actions/runs/") and "/jobs?filter=all" in relative:
            identifier = int(relative.split("/")[2])
            jobs = self.jobs.get(identifier, [])
            return {"total_count": len(jobs), "jobs": copy.deepcopy(jobs)}
        if relative.startswith("actions/runs/"):
            identifier = int(relative.split("/")[2])
            return copy.deepcopy(next(row for row in self.generations if row["id"] == identifier))
        raise AssertionError(path)


def test_current_source_ci_uses_direct_run_evidence_without_index_lag() -> None:
    backend = Backend()
    backend.cis = []
    result = dispatch.pending_source(REPO, source_ci_run=600, api=backend)
    assert result.source_sha == DEV and result.reason == "current-source-ci-succeeded"
    assert any("actions/workflows/ci.yml/runs?head_sha=" in path for path in backend.calls)


def test_successful_noop_without_a_merged_pr_cannot_create_a_dispatch_loop() -> None:
    backend = Backend()
    backend.closed = []
    assert dispatch.pending_source(REPO, completed_promotion_run=500, api=backend).source_sha is None
    assert not any("contents/" in path for path in backend.calls)


@pytest.mark.parametrize("paused,source", [(True, RELEASED), (False, DEV)])
def test_paused_or_already_current_source_has_no_duplicate_generation(paused: bool, source: str) -> None:
    backend = Backend()
    backend.main = state(source, paused=paused)
    assert dispatch.pending_source(REPO, source_ci_run=600, api=backend).source_sha is None
