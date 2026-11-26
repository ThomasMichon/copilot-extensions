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
from agent_dispatch.client_errors import DispatchError, DispatchUpgradeRequired
from agent_dispatch.queue_relevance_finalize_first import FINALIZE_RUNNER
from tests import test_pool_placement_client as controls

q = controls.q
api = controls.api


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
    """Run cycles, letting each background job finish, until one records a result."""
    result = sweep_mod.sweep(owner)  # type: ignore[arg-type]
    for _ in range(4):
        job = getattr(owner, "_relevance_finalize_job", None)
        if job is None or result is None or result["action"] not in ("selecting", "started"):
            return result
        assert job.thread is not None
        job.thread.join(5)
        result = sweep_mod.sweep(owner)  # type: ignore[arg-type]
    return result


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
    assert client.calls[1] == ("retain", sweep_mod.IN_FLIGHT_DETAIL)
    assert client.calls[2][0] == "settle" and client.calls[2][1]["worktree_id"] == "wt-cold"


def test_sweep_releases_the_fence_when_finalize_is_refused() -> None:
    def refusing(project: str, worktree_id: str) -> subprocess.CompletedProcess[str]:
        return _done(1, {"worktree_id": worktree_id, "success": False, "error": "unmerged work"})

    client = _Client([_candidate()])
    result = _sweep(_owner(client, runner=refusing))
    assert result is not None and result["action"] == "released"
    assert [name for name, _ in client.calls] == ["fence", "retain", "release"]
    assert "unmerged work" in client.calls[2][1]


def test_sweep_retains_custody_when_finalize_times_out_then_reconciles_from_status() -> None:
    client = _Client([_candidate()])
    owner = _owner(client, runner=lambda project, worktree_id: None)
    result = _sweep(owner)
    assert result is not None and result["action"] == "retained"
    assert [name for name, _ in client.calls] == ["fence", "retain", "retain"]

    # A retained fence leaves the candidate cohort and is reconciled instead.
    client.candidates = []
    client.unknown = [{"task_id": "t1", "token": "tok", "reservation": _candidate()["reservation"]}]
    probed: list[tuple[str, str]] = []

    def missing(project: str, worktree_id: str) -> None:
        probed.append((project, worktree_id))

    owner.finalize_status_fn = missing
    assert _sweep(owner)["action"] == "retained"  # type: ignore[index]
    assert probed == [("proj", "wt-t1")]
    # Each unknown fence is retried once per rotation, so fresh candidates still get a turn.
    assert _sweep(owner) is None
    owner.finalize_status_fn = lambda project, worktree_id: {"status": "finalized"}
    result = _sweep(owner)
    assert result is not None and result["action"] == "settled"
    assert client.calls[-1][1]["reconciled"] == "finalized"
    assert [name for name, _ in client.calls].count("release") == 0


@pytest.mark.parametrize("record", [None, {"status": "finalizing"}, {"status": "archived"}, {}])
def test_reconcile_keeps_custody_unless_the_exact_record_proves_the_outcome(record: Any) -> None:
    verdict, evidence, _ = sweep_mod.reconcile_evidence(lambda project, worktree_id: record, "proj", "wt-1")
    assert verdict == sweep_mod.UNKNOWN and evidence is None


def test_default_status_probe_reads_the_exact_project_record(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_dispatch import procutil, worktree_record

    seen: dict[str, Any] = {}

    def exact(prefix: list[str], worktree_id: str, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs, prefix=prefix, worktree_id=worktree_id)
        return {"id": worktree_id, "status": "finalized"}

    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: ["aw"])
    monkeypatch.setattr(worktree_record, "read_exact_row", exact)
    assert sweep_mod._status_probe("proj", "wt-1") == {"id": "wt-1", "status": "finalized"}
    assert seen["project"] == "proj" and seen["worktree_id"] == "wt-1" and seen["task_scoped"] is False


def test_sweep_reconciles_a_still_tracked_worktree_as_refused() -> None:
    client = _Client([], [{"task_id": "t1", "token": "tok", "reservation": _candidate()["reservation"]}])
    result = _sweep(_owner(client, status=lambda project, worktree_id: {"status": "active"}))
    assert result is not None and result["action"] == "released"


def test_in_flight_custody_is_durable_before_finalize_runs() -> None:
    client = _Client([_candidate()])
    at_run: list[list[str]] = []

    def runner(project: str, worktree_id: str) -> subprocess.CompletedProcess[str]:
        at_run.append([name for name, _ in client.calls])
        return _accepting(project, worktree_id)

    assert _sweep(_owner(client, runner=runner))["action"] == "settled"  # type: ignore[index]
    assert at_run == [["fence", "retain"]]


def test_a_failed_in_flight_retain_never_finalizes() -> None:
    class NoRetain(_Client):
        def retain_relevance_finalize(self, task_id: str, *, token: str, detail: str) -> dict[str, Any]:
            raise DispatchError(503, "unavailable")

    ran: list[str] = []
    client = NoRetain([_candidate()])
    result = _sweep(_owner(client, runner=lambda p, w: ran.append(w)))
    assert result is not None and result["action"] == "not_started" and ran == []
    assert [name for name, _ in client.calls] == ["fence", "release"]


def test_a_failed_outcome_publication_is_kept_and_retried() -> None:
    failures = [DispatchError(503, "down"), None]

    class Flaky(_Client):
        def settle_relevance_finalize(self, task_id: str, *, token: str, evidence: dict[str, Any]) -> dict[str, Any]:
            error = failures.pop(0)
            if error is not None:
                raise error
            return super().settle_relevance_finalize(task_id, token=token, evidence=evidence)

    ran: list[str] = []
    client = Flaky([_candidate()])

    def runner(project: str, worktree_id: str) -> subprocess.CompletedProcess[str]:
        ran.append(worktree_id)
        return _accepting(project, worktree_id)

    owner = _owner(client, runner=runner)
    result = _sweep(owner)
    assert result is not None and result["action"] == "unrecorded"
    assert owner._relevance_finalize_job is not None  # retained for retry, not rerun
    assert sweep_mod.sweep(owner)["action"] == "settled"  # type: ignore[arg-type,index]
    assert ran == ["wt-t1"] and owner._relevance_finalize_job is None


def test_a_rejected_outcome_publication_falls_back_to_reconciliation() -> None:
    class Rejecting(_Client):
        def settle_relevance_finalize(self, task_id: str, *, token: str, evidence: dict[str, Any]) -> dict[str, Any]:
            raise DispatchError(409, "incarnation changed")

    owner = _owner(Rejecting([_candidate()]), runner=_accepting)
    assert _sweep(owner)["action"] == "unrecorded"  # type: ignore[index]
    assert owner._relevance_finalize_job is None


@pytest.mark.parametrize(("status", "action"), [("finalizing", "retained"), ("finalized", "settled")])
def test_a_restarted_supervisor_reconciles_in_flight_custody(status: str, action: str) -> None:
    # A fresh owner has no job; the in-flight unknown fence is its only memory.
    ran: list[str] = []
    client = _Client([], [{"task_id": "t1", "token": "tok", "reservation": _candidate()["reservation"]}])
    owner = _owner(client, runner=lambda p, w: ran.append(w), status=lambda project, worktree_id: {"status": status})
    result = _sweep(owner)
    assert result is not None and result["action"] == action and ran == []
    assert "release" not in [name for name, _ in client.calls]


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
    assert sweep_mod.sweep(owner)["action"] == "selecting"  # type: ignore[arg-type,index]
    owner._relevance_finalize_job.thread.join(5)
    assert sweep_mod.sweep(owner)["action"] == "started"  # type: ignore[arg-type,index]
    assert sweep_mod.sweep(owner)["action"] == "running"  # type: ignore[arg-type,index]
    assert time.monotonic() - started < 1
    assert [name for name, _ in client.calls] == ["fence", "retain"]
    gate.set()
    owner._relevance_finalize_job.thread.join(5)
    assert sweep_mod.sweep(owner)["action"] == "settled"  # type: ignore[arg-type,index]


def test_slow_liveness_reads_never_block_the_sweep() -> None:
    gate = threading.Event()
    client = _Client([_candidate(f"t{i}") for i in range(4)])
    owner = _owner(client, runner=_accepting)

    def slow(worktree: str, machine: str | None) -> str:
        gate.wait(5)
        return "absent"

    owner.finalize_liveness_fn = slow
    started = time.monotonic()
    assert sweep_mod.sweep(owner)["action"] == "selecting"  # type: ignore[arg-type,index]
    assert sweep_mod.sweep(owner)["action"] == "running"  # type: ignore[arg-type,index]
    assert time.monotonic() - started < 1 and client.calls == []
    gate.set()
    owner._relevance_finalize_job.thread.join(5)
    assert sweep_mod.sweep(owner)["action"] == "started"  # type: ignore[arg-type,index]


def test_a_selection_past_its_budget_resumes_after_the_last_probed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client([_candidate(f"t{i}") for i in range(3)])
    owner = _owner(client, live=frozenset({"wt-t0", "wt-t1", "wt-t2"}))
    clock = iter([0.0, 0.0, 0.0, 100.0])
    monkeypatch.setattr(sweep_mod, "_clock", lambda: next(clock))
    assert _sweep(owner) is None
    assert owner._relevance_finalize_cursor == {"updated_at": 1.0, "task_id": "t1"}


def test_sweep_is_a_no_op_against_a_coordinator_without_finalize_first() -> None:
    class Old(_Client):
        def relevance_finalize_unknown(self, supervisor_id: str) -> list[dict[str, Any]]:
            raise DispatchUpgradeRequired("old")

    client = Old([_candidate()])
    assert sweep_mod.sweep(_owner(client, runner=_accepting)) is None  # type: ignore[arg-type]
    assert client.calls == []


def test_one_shot_drain_carries_a_started_selection_through_settlement() -> None:
    client = _Client([_candidate()])
    owner = _owner(client, runner=_accepting)
    # The single poll's sweep only starts background selection.
    assert sweep_mod.sweep(owner)["action"] == "selecting"  # type: ignore[arg-type,index]
    results = sweep_mod.drain(owner, budget=10)  # type: ignore[arg-type]
    assert [r["action"] for r in results] == ["started", "settled"]
    assert [name for name, _ in client.calls] == ["fence", "retain", "settle"]


def test_one_shot_drain_stops_at_its_budget_with_in_flight_custody_retained() -> None:
    release = threading.Event()

    def slow(project: str, worktree_id: str) -> subprocess.CompletedProcess[str]:
        release.wait(10)
        return _accepting(project, worktree_id)

    client = _Client([_candidate()])
    owner = _owner(client, runner=slow)
    sweep_mod.sweep(owner)  # type: ignore[arg-type]
    started = time.monotonic()
    try:
        results = sweep_mod.drain(owner, budget=0.5)  # type: ignore[arg-type]
    finally:
        release.set()
    assert time.monotonic() - started < 5
    assert results[-1] == {"task_id": "t1", "action": "running", "kind": "finalize"}
    assert [name for name, _ in client.calls] == ["fence", "retain"]


def test_supervise_once_finalizes_an_eligible_closeout_in_one_invocation(
    monkeypatch: pytest.MonkeyPatch, q: Any, api: Any, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_dispatch import __main__ as cli
    from agent_dispatch.supervisor import Supervisor

    monkeypatch.setattr("agent_dispatch.supervise_cli.configure_file_logging", lambda *_: None)
    fake = _Client([_candidate()])

    def poll_once(self: Supervisor) -> list[str]:
        stub = _owner(fake, runner=_accepting)
        for name in ("client", "finalize_liveness_fn", "finalize_runner", "_recheck_governance"):
            setattr(self, name, getattr(stub, name))
        sweep_mod.sweep(self)
        return []

    with controls.connected(api, []) as client:
        monkeypatch.setattr(cli, "_client", lambda *args, **kwargs: client)
        monkeypatch.setattr(Supervisor, "poll_once", poll_once)
        args = cli.build_parser().parse_args(["supervise", "--all-repos", "--once"])
        assert args.func(args) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert [r["action"] for r in emitted["finalize_first"]] == ["started", "settled"]
    assert [name for name, _ in fake.calls] == ["fence", "retain", "settle"]
