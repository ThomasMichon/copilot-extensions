"""Supervisor finalize-first sweep: evidence acceptance and settle/release routing."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from agent_dispatch import supervisor_relevance_finalize_first as sweep_mod
from agent_dispatch.client_errors import DispatchUpgradeRequired
from agent_dispatch.queue_relevance_finalize_first import FINALIZE_RUNNER


def _done(rc: int, payload: Any, stderr: str = "") -> subprocess.CompletedProcess[str]:
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(["agent-worktrees"], rc, stdout, stderr)


def _evidence(result: Any) -> tuple[str, dict[str, Any] | None, str]:
    def runner(project: str, worktree_id: str) -> subprocess.CompletedProcess[str] | None:
        if isinstance(result, Exception):
            raise result
        completed: subprocess.CompletedProcess[str] | None = result
        return completed

    return sweep_mod.finalize_evidence(runner, "proj", "wt-1")


def test_finalize_evidence_accepts_only_a_clean_finalize_of_the_same_worktree() -> None:
    verdict, evidence, detail = _evidence(_done(0, {"worktree_id": "wt-1", "success": True, "status": "finalized"}))
    assert (verdict, detail) == (sweep_mod.ACCEPTED, "")
    assert evidence == {
        "runner": FINALIZE_RUNNER, "status": "finalized", "worktree_id": "wt-1",
        "project": "proj", "returncode": 0,
    }


def test_finalize_evidence_refuses_only_a_parseable_failure() -> None:
    verdict, evidence, detail = _evidence(
        _done(1, {"worktree_id": "wt-1", "success": False, "status": "blocked", "error": "dirty"}),
    )
    assert (verdict, evidence) == (sweep_mod.REFUSED, None)
    assert "dirty" in detail


@pytest.mark.parametrize("result", [
    _done(0, {"worktree_id": "wt-1", "success": True, "status": "parked"}),
    _done(0, {"worktree_id": "other", "success": True, "status": "finalized"}),
    _done(0, "not json"),
    _done(1, ""),
    None,
    OSError("missing binary"),
    subprocess.TimeoutExpired(["agent-worktrees"], 1),
])
def test_finalize_evidence_keeps_a_timeout_or_malformed_result_unknown(result: Any) -> None:
    verdict, evidence, detail = _evidence(result)
    assert (verdict, evidence) == (sweep_mod.UNKNOWN, None)
    assert detail


class _Client:
    def __init__(self, candidates: list[dict[str, Any]], unknown: list[dict[str, Any]] | None = None) -> None:
        self.candidates = candidates
        self.unknown = unknown or []
        self.calls: list[tuple[str, Any]] = []
        self.pages: list[Any] = []

    def relevance_finalize_candidates(
        self, supervisor_id: str, *, after: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.pages.append(after)
        rows = self.candidates
        if after is not None:
            rows = rows[[c["task_id"] for c in rows].index(after["task_id"]) + 1:]
        return rows[:4]

    def relevance_finalize_unknown(self, supervisor_id: str) -> list[dict[str, Any]]:
        return self.unknown

    def fence_relevance_finalize(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("fence", task_id))
        return {"token": "tok", "task_id": task_id}

    def settle_relevance_finalize(self, task_id: str, *, token: str, evidence: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("settle", evidence))
        return {"status": "abandoned"}

    def release_relevance_finalize(self, task_id: str, *, token: str, detail: str) -> dict[str, Any]:
        self.calls.append(("release", detail))
        return {"task_id": task_id, "state": "refused", "rearm": None}

    def retain_relevance_finalize(self, task_id: str, *, token: str, detail: str) -> dict[str, Any]:
        self.calls.append(("retain", detail))
        return {"task_id": task_id, "state": "unknown"}


def _candidate(task_id: str = "t1", machine: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id, "generation": 3, "phase": "unstarted",
        "cursor": {"updated_at": 1.0, "task_id": task_id},
        "reservation": {
            "key": f"r-{task_id}", "worktree": f"wt-{task_id}", "allocation_project": "proj",
            "execution_machine": machine,
        },
    }


def _owner(
    client: _Client, *, live: frozenset[str] = frozenset(), unknown: frozenset[str] = frozenset(),
    runner: Any = None, status: Any = None,
) -> SimpleNamespace:
    def liveness(worktree: str, machine: str | None) -> str:
        return "unknown" if worktree in unknown else "alive" if worktree in live else "absent"

    return SimpleNamespace(
        client=client, supervisor_id="sup", finalize_liveness_fn=liveness,
        _recheck_governance=lambda reason: None, finalize_runner=runner, finalize_status_fn=status,
    )


def _sweep(owner: SimpleNamespace) -> dict[str, Any] | None:
    """Start a job, let it finish, then record it on the following cycle."""
    started = sweep_mod.sweep(owner)  # type: ignore[arg-type]
    job = getattr(owner, "_relevance_finalize_job", None)
    if job is None:
        return started
    assert started is not None and started["action"] == "started"
    assert job.thread is not None
    job.thread.join(5)
    return sweep_mod.sweep(owner)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _local_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_dispatch import remote_dispatch

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(sweep_mod, "action_inhibited", lambda *a, **k: False)


def _accepting(project: str, worktree_id: str) -> subprocess.CompletedProcess[str]:
    return _done(0, {"worktree_id": worktree_id, "success": True, "status": "finalized"})


def test_sweep_settles_on_accepted_finalize_and_skips_hot_or_remote() -> None:
    client = _Client([_candidate("hot"), _candidate("far", "host-b"), _candidate("cold")])
    result = _sweep(_owner(client, live=frozenset({"wt-hot"}), runner=_accepting))
    assert result is not None and result["action"] == "settled" and result["task_id"] == "cold"
    assert client.calls[0] == ("fence", "cold")
    assert client.calls[1][0] == "settle" and client.calls[1][1]["worktree_id"] == "wt-cold"


def test_sweep_releases_the_fence_when_finalize_is_refused() -> None:
    def refusing(project: str, worktree_id: str) -> subprocess.CompletedProcess[str]:
        return _done(1, {"worktree_id": worktree_id, "success": False, "error": "unmerged work"})

    client = _Client([_candidate()])
    result = _sweep(_owner(client, runner=refusing))
    assert result is not None and result["action"] == "released"
    assert [name for name, _ in client.calls] == ["fence", "release"]
    assert "unmerged work" in client.calls[1][1]


def test_sweep_retains_custody_when_finalize_times_out_then_reconciles_from_status() -> None:
    client = _Client([_candidate()])
    owner = _owner(client, runner=lambda project, worktree_id: None)
    result = _sweep(owner)
    assert result is not None and result["action"] == "retained"
    assert [name for name, _ in client.calls] == ["fence", "retain"]

    # A retained fence leaves the candidate cohort and is reconciled instead.
    client.candidates = []
    client.unknown = [{"task_id": "t1", "token": "tok", "reservation": _candidate()["reservation"]}]
    owner.finalize_status_fn = lambda worktree_id: None
    assert _sweep(owner)["action"] == "retained"  # type: ignore[index]
    # Each unknown fence is retried once per rotation, so fresh candidates still get a turn.
    assert _sweep(owner) is None
    owner.finalize_status_fn = lambda worktree_id: {"status": "absent"}
    result = _sweep(owner)
    assert result is not None and result["action"] == "settled"
    assert client.calls[-1][1]["reconciled"] == "absent"
    assert [name for name, _ in client.calls].count("release") == 0


def test_sweep_reconciles_a_still_tracked_worktree_as_refused() -> None:
    client = _Client([], [{"task_id": "t1", "token": "tok", "reservation": _candidate()["reservation"]}])
    result = _sweep(_owner(client, status=lambda worktree_id: {"status": "active"}))
    assert result is not None and result["action"] == "released"


@pytest.mark.parametrize("liveness", ["unknown", "alive"])
def test_sweep_never_finalizes_unless_the_worker_is_confirmed_absent(liveness: str) -> None:
    ran: list[str] = []
    client = _Client([_candidate()])
    owner = _owner(client, runner=lambda p, w: ran.append(w))
    owner.finalize_liveness_fn = lambda worktree, machine: liveness
    assert _sweep(owner) is None
    assert client.calls == [] and ran == []


def test_default_liveness_keeps_custody_when_the_bridge_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_dispatch import tracking

    monkeypatch.setattr(tracking, "_resolve_local_owner", lambda worktree, timeout: None)
    owner = _owner(_Client([_candidate()]))
    del owner.finalize_liveness_fn
    # No native identity is needed for the occupancy read, and a failed read is unknown.
    assert sweep_mod._liveness(owner, _candidate()) == sweep_mod.UNKNOWN  # type: ignore[arg-type]
    monkeypatch.setattr(tracking, "_resolve_local_owner", lambda worktree, timeout: {})
    assert sweep_mod._liveness(owner, _candidate()) == sweep_mod.ABSENT  # type: ignore[arg-type]
    monkeypatch.setattr(tracking, "_resolve_local_owner", lambda worktree, timeout: {"session_id": "s"})
    assert sweep_mod._liveness(owner, _candidate()) == sweep_mod.ALIVE  # type: ignore[arg-type]


def test_sweep_re_checks_liveness_after_fencing_and_releases_without_finalizing() -> None:
    ran: list[str] = []
    client = _Client([_candidate()])
    owner = _owner(client, runner=lambda p, w: ran.append(w))
    answers = iter(["absent", "unknown"])
    owner.finalize_liveness_fn = lambda worktree, machine: next(answers)
    result = _sweep(owner)
    assert result is not None and result["action"] == "released" and ran == []


def test_sweep_pages_past_a_full_page_of_skipped_candidates() -> None:
    rows = [_candidate(f"hot{i}") for i in range(4)] + [_candidate("cold")]
    client = _Client(rows)
    live = frozenset(f"wt-hot{i}" for i in range(4))
    result = _sweep(_owner(client, live=live, runner=_accepting))
    assert result is not None and result["task_id"] == "cold"
    assert client.pages[:2] == [None, {"updated_at": 1.0, "task_id": "hot3"}]


def test_a_slow_finalizer_does_not_block_the_sweep() -> None:
    gate = threading.Event()

    def slow(project: str, worktree_id: str) -> subprocess.CompletedProcess[str]:
        gate.wait(5)
        return _accepting(project, worktree_id)

    client = _Client([_candidate()])
    owner = _owner(client, runner=slow)
    started = time.monotonic()
    assert sweep_mod.sweep(owner)["action"] == "started"  # type: ignore[arg-type,index]
    assert sweep_mod.sweep(owner)["action"] == "running"  # type: ignore[arg-type,index]
    assert time.monotonic() - started < 1
    assert [name for name, _ in client.calls] == ["fence"]
    gate.set()
    owner._relevance_finalize_job.thread.join(5)
    assert sweep_mod.sweep(owner)["action"] == "settled"  # type: ignore[arg-type,index]


def test_sweep_is_a_no_op_against_a_coordinator_without_finalize_first() -> None:
    class Old(_Client):
        def relevance_finalize_unknown(self, supervisor_id: str) -> list[dict[str, Any]]:
            raise DispatchUpgradeRequired("old")

    client = Old([_candidate()])
    assert sweep_mod.sweep(_owner(client, runner=_accepting)) is None  # type: ignore[arg-type]
    assert client.calls == []
