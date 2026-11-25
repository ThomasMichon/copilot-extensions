"""Supervisor finalize-first sweep: evidence acceptance and settle/release routing."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from agent_dispatch import supervisor_relevance_finalize_first as sweep_mod
from agent_dispatch.client_errors import DispatchUpgradeRequired
from agent_dispatch.queue_relevance_finalize_first import FINALIZE_RUNNER


def _done(rc: int, payload: Any, stderr: str = "") -> subprocess.CompletedProcess[str]:
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(["agent-worktrees"], rc, stdout, stderr)


def _evidence(result: Any) -> tuple[dict[str, Any] | None, str]:
    def runner(project: str, worktree_id: str) -> subprocess.CompletedProcess[str] | None:
        if isinstance(result, Exception):
            raise result
        completed: subprocess.CompletedProcess[str] | None = result
        return completed

    return sweep_mod.finalize_evidence(runner, "proj", "wt-1")


def test_finalize_evidence_accepts_only_a_clean_finalize_of_the_same_worktree() -> None:
    evidence, detail = _evidence(_done(0, {"worktree_id": "wt-1", "success": True, "status": "finalized"}))
    assert detail == ""
    assert evidence == {
        "runner": FINALIZE_RUNNER, "status": "finalized", "worktree_id": "wt-1",
        "project": "proj", "returncode": 0,
    }


@pytest.mark.parametrize("result", [
    _done(1, {"worktree_id": "wt-1", "success": False, "status": "blocked", "error": "dirty"}),
    _done(0, {"worktree_id": "wt-1", "success": True, "status": "parked"}),
    _done(0, {"worktree_id": "other", "success": True, "status": "finalized"}),
    _done(0, "not json"),
    None,
    OSError("missing binary"),
    subprocess.TimeoutExpired(["agent-worktrees"], 1),
])
def test_finalize_evidence_never_treats_refusal_or_error_as_acceptance(result: Any) -> None:
    evidence, detail = _evidence(result)
    assert evidence is None
    assert detail


class _Client:
    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        self.candidates = candidates
        self.calls: list[tuple[str, Any]] = []

    def relevance_finalize_candidates(self, supervisor_id: str) -> list[dict[str, Any]]:
        return self.candidates

    def fence_relevance_finalize(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("fence", task_id))
        return {"token": "tok", "task_id": task_id}

    def settle_relevance_finalize(self, task_id: str, *, token: str, evidence: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("settle", evidence))
        return {"status": "abandoned"}

    def release_relevance_finalize(self, task_id: str, *, token: str, detail: str) -> dict[str, Any]:
        self.calls.append(("release", detail))
        return {"task_id": task_id, "state": "refused", "rearm": None}


def _candidate(task_id: str = "t1", machine: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id, "generation": 3, "phase": "unstarted",
        "reservation": {
            "key": f"r-{task_id}", "worktree": f"wt-{task_id}", "allocation_project": "proj",
            "execution_machine": machine,
        },
    }


def _owner(client: _Client, *, live: frozenset[str] = frozenset(), runner: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        client=client, supervisor_id="sup",
        liveness_fn=lambda worktree, machine: "native" if worktree in live else None,
        _recheck_governance=lambda reason: None,
        finalize_runner=runner,
    )


@pytest.fixture(autouse=True)
def _local_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_dispatch import remote_dispatch

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(sweep_mod, "action_inhibited", lambda *a, **k: False)


def _accepting(project: str, worktree_id: str) -> subprocess.CompletedProcess[str]:
    return _done(0, {"worktree_id": worktree_id, "success": True, "status": "finalized"})


def test_sweep_settles_on_accepted_finalize_and_skips_hot_or_remote() -> None:
    client = _Client([_candidate("hot"), _candidate("far", "host-b"), _candidate("cold")])
    result = sweep_mod.sweep(_owner(client, live={"wt-hot"}, runner=_accepting))  # type: ignore[arg-type]
    assert result is not None and result["action"] == "settled" and result["task_id"] == "cold"
    assert client.calls[0] == ("fence", "cold")
    assert client.calls[1][0] == "settle" and client.calls[1][1]["worktree_id"] == "wt-cold"


def test_sweep_releases_the_fence_when_finalize_is_refused() -> None:
    def refusing(project: str, worktree_id: str) -> subprocess.CompletedProcess[str]:
        return _done(1, {"worktree_id": worktree_id, "success": False, "error": "unmerged work"})

    client = _Client([_candidate()])
    result = sweep_mod.sweep(_owner(client, runner=refusing))  # type: ignore[arg-type]
    assert result is not None and result["action"] == "released"
    assert [name for name, _ in client.calls] == ["fence", "release"]
    assert "unmerged work" in client.calls[1][1]


def test_sweep_is_a_no_op_against_a_coordinator_without_finalize_first() -> None:
    class Old(_Client):
        def relevance_finalize_candidates(self, supervisor_id: str) -> list[dict[str, Any]]:
            raise DispatchUpgradeRequired("old")

    client = Old([_candidate()])
    assert sweep_mod.sweep(_owner(client, runner=_accepting)) is None  # type: ignore[arg-type]
    assert client.calls == []
