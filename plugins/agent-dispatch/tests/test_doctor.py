"""Tests for agent_dispatch.doctor (Boundary I / #2577)."""

from __future__ import annotations

import json

import pytest

from agent_dispatch import doctor
from agent_dispatch.__main__ import _cmd_doctor, build_parser
from agent_dispatch.client import DispatchError


def _args(argv):
    return build_parser().parse_args(argv)


def _task(
    *,
    task_id="t-1",
    status="started",
    owner="headless-x",
    worktree_id="wt-1",
    reservation_key="dispatch-task:t-1:1",
    lease_expires_at=None,
    activity=None,
):
    reservation = None
    if worktree_id or reservation_key:
        reservation = {"worktree": worktree_id, "key": reservation_key}
    return {
        "id": task_id,
        "status": status,
        "owner": owner,
        "lease_expires_at": lease_expires_at,
        "activity": activity,
        "spawn_reservation": reservation,
    }


# -- diagnose ------------------------------------------------------------


def test_diagnose_orphaned_when_worktree_finalized():
    task = _task()
    d = doctor.diagnose(task, resolve=lambda wt: {"status": "finalized"})
    assert d.verdict == "orphaned_worktree_gone"
    assert d.worktree_id == "wt-1"
    assert d.reservation_key == "dispatch-task:t-1:1"


def test_diagnose_orphaned_when_worktree_absent_entirely():
    task = _task()
    d = doctor.diagnose(task, resolve=lambda wt: {"status": "absent"})
    assert d.verdict == "orphaned_worktree_gone"


def test_resolve_worktree_none_is_indeterminate_not_gone():
    """A resolver returning None (CLI unavailable / call failed) must never be
    treated as confirmed-gone -- diagnose falls through to other checks."""
    task = _task(status="started", lease_expires_at=1000.0, activity=None)
    d = doctor.diagnose(task, now=1000.0 + 10, resolve=lambda wt: None)
    assert d.verdict != "orphaned_worktree_gone"


def test_diagnose_healthy_when_worktree_active():
    task = _task(status="started", lease_expires_at=1000.0, activity="IDLE")
    d = doctor.diagnose(task, now=1000.0 + 10, resolve=lambda wt: {"status": "active"})
    assert d.verdict == "healthy"


def test_diagnose_stale_lease_when_started_expired_and_no_activity():
    task = _task(status="started", lease_expires_at=1000.0, activity=None)
    now = 1000.0 + doctor.DEFAULT_STALE_LEASE_GRACE_SECONDS + 1
    d = doctor.diagnose(task, now=now, resolve=lambda wt: {"status": "active"})
    assert d.verdict == "stale_lease"


def test_diagnose_started_within_grace_is_healthy():
    task = _task(status="started", lease_expires_at=1000.0, activity=None)
    now = 1000.0 + 10  # well within the default grace window
    d = doctor.diagnose(task, now=now, resolve=lambda wt: {"status": "active"})
    assert d.verdict == "healthy"


def test_diagnose_suspended_with_no_reservation_is_unknown():
    task = _task(status="suspended", worktree_id=None, reservation_key=None)
    d = doctor.diagnose(task, resolve=lambda wt: {"status": "active"})
    assert d.verdict == "unknown"


def test_diagnose_queued_with_reservation_reports_stuck_verdict():
    """A queued task whose latest spawn reservation genuinely FAILED --
    confirmed live (#5209): several review tasks sat `queued, no owner` for
    hours during a facility-wide agent-bridge outage, invisible to a doctor
    sweep that never examined `queued` at all."""
    task = _task(status="queued", worktree_id=None, reservation_key="dispatch-task:t-1:2")
    task["spawn_reservation"] = {
        "key": "dispatch-task:t-1:2",
        "attempt": 2,
        "state": "failed",
        "detail": (
            "'intelligence-dampener-dispatch-reviewer' is not a known agent "
            "name or session ID"
        ),
    }
    d = doctor.diagnose(task, resolve=lambda wt: {"status": "active"})
    assert d.verdict == doctor.QUEUED_STUCK_RESERVATION_VERDICT
    assert "not a known agent name" in d.detail


def test_diagnose_queued_with_active_reservation_is_not_stuck():
    """`reserving`/`spawned`/`cold`/`releasing` are all legitimate ACTIVE
    states for a reservation whose task still shows as `queued` -- only a
    genuinely FAILED reservation is stuck. Misreporting an in-flight spawn
    as stuck would be a false positive on healthy work."""
    for active_state in ("reserving", "spawned", "cold", "releasing"):
        task = _task(status="queued", worktree_id=None, reservation_key="k1")
        task["spawn_reservation"] = {"key": "k1", "attempt": 1, "state": active_state}
        d = doctor.diagnose(task, resolve=lambda wt: {"status": "active"})
        assert d.verdict != doctor.QUEUED_STUCK_RESERVATION_VERDICT, active_state


def test_diagnose_queued_with_no_reservation_is_unknown_not_stuck():
    """diagnose() itself is unaffected for an ordinary, never-yet-attempted
    queued task -- the batch-level skip lives in diagnose_many, not here."""
    task = _task(status="queued", worktree_id=None, reservation_key=None)
    d = doctor.diagnose(task, resolve=lambda wt: {"status": "active"})
    assert d.verdict != doctor.QUEUED_STUCK_RESERVATION_VERDICT
    assert d.verdict == "unknown"


def test_diagnose_custom_grace_seconds():
    task = _task(status="started", lease_expires_at=1000.0, activity=None)
    d = doctor.diagnose(
        task,
        now=1000.0 + 50,
        stale_lease_grace_seconds=30.0,
        resolve=lambda wt: {"status": "active"},
    )
    assert d.verdict == "stale_lease"


# -- repair ----------------------------------------------------------------


class _FakeClient:
    def __init__(self):
        self.fail_spawn_calls = []
        self.release_calls = []
        self.yield_calls = []

    def fail_spawn(self, key, *, detail=None, **kw):
        self.fail_spawn_calls.append((key, detail))
        return {"state": "failed"}

    def release(self, task_id, worker_id, *, reason=None):
        self.release_calls.append((task_id, worker_id, reason))
        return {"status": "queued"}

    def yield_task(self, task_id, worker_id, *, note=None, **kw):
        self.yield_calls.append((task_id, worker_id, note))
        return {"status": "queued"}


def test_repair_skips_non_orphaned_verdicts():
    d = doctor.Diagnosis(
        task_id="t-1", status="started", verdict="healthy", detail="fine",
        worktree_id="wt-1", reservation_key="k", owner="o",
    )
    result = doctor.repair(d, _FakeClient(), reason="test")
    assert result["action"] == "skipped"


def test_repair_started_task_fails_reservation_and_yields():
    d = doctor.Diagnosis(
        task_id="t-1", status="started", verdict="orphaned_worktree_gone",
        detail="gone", worktree_id="wt-1", reservation_key="dispatch-task:t-1:1",
        owner="headless-x",
    )
    client = _FakeClient()
    result = doctor.repair(d, client, reason="doctor: gone")
    assert client.fail_spawn_calls == [("dispatch-task:t-1:1", "doctor: gone")]
    assert client.yield_calls == [("t-1", "headless-x", "doctor: gone")]
    assert client.release_calls == []
    assert result["reservation"] == {"state": "failed"}
    assert result["task"] == {"status": "queued"}


def test_repair_suspended_task_releases_instead_of_yielding():
    d = doctor.Diagnosis(
        task_id="t-1", status="suspended", verdict="orphaned_worktree_gone",
        detail="gone", worktree_id="wt-1", reservation_key="dispatch-task:t-1:2",
        owner="headless-x",
    )
    client = _FakeClient()
    doctor.repair(d, client, reason="doctor: gone")
    assert client.release_calls == [("t-1", "headless-x", "doctor: gone")]
    assert client.yield_calls == []


def test_repair_without_reservation_key_still_releases_task():
    d = doctor.Diagnosis(
        task_id="t-1", status="suspended", verdict="orphaned_worktree_gone",
        detail="gone", worktree_id="wt-1", reservation_key=None, owner="headless-x",
    )
    client = _FakeClient()
    result = doctor.repair(d, client, reason="doctor: gone")
    assert client.fail_spawn_calls == []
    assert "reservation" not in result
    assert result["task"] == {"status": "queued"}


def test_repair_client_errors_are_surfaced_not_raised():
    class _Boom(_FakeClient):
        def fail_spawn(self, key, *, detail=None, **kw):
            raise RuntimeError("coordinator unreachable")

        def yield_task(self, task_id, worker_id, *, note=None, **kw):
            raise RuntimeError("coordinator unreachable")

    d = doctor.Diagnosis(
        task_id="t-1", status="started", verdict="orphaned_worktree_gone",
        detail="gone", worktree_id="wt-1", reservation_key="k", owner="o",
    )
    result = doctor.repair(d, _Boom(), reason="doctor: gone")
    assert "error" in result["reservation"]
    assert "error" in result["task"]


def test_repair_terminal_task_clears_only_the_stale_reservation():
    """Regression (copilot-extensions#3025): a task diagnosed
    `orphaned_worktree_gone` that has already gone terminal must never have
    its task state transitioned, but its stale reservation -- which
    `resolve_worktree` already confirmed gone -- is cleared with
    `force=True, confirmed_absent=True` rather than left fenced forever."""

    class _TerminalClient:
        def __init__(self):
            self.fail_spawn_calls = []

        def fail_spawn(self, key, *, detail=None, force=False, confirmed_absent=False, **kw):
            self.fail_spawn_calls.append((key, detail, force, confirmed_absent))
            return {"state": "failed"}

        def yield_task(self, *a, **k):
            raise AssertionError("must not transition an already-terminal task")

        def release(self, *a, **k):
            raise AssertionError("must not transition an already-terminal task")

    d = doctor.Diagnosis(
        task_id="t-1", status="submitted", verdict="orphaned_worktree_gone",
        detail="gone", worktree_id="wt-1", reservation_key="dispatch-task:t-1:1",
        owner="headless-x",
    )
    client = _TerminalClient()
    result = doctor.repair(d, client, reason="doctor: gone")
    assert client.fail_spawn_calls == [("dispatch-task:t-1:1", "doctor: gone", True, True)]
    assert result["reservation"] == {"state": "failed"}
    assert "skipped" in result["task"]


# -- CLI ---------------------------------------------------------------


class _ListClient:
    def __init__(self, tasks, reservations=None):
        self._tasks = tasks
        self.list_calls = []
        self._reservations = reservations or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list(self, **kw):
        self.list_calls.append(kw)
        return self._tasks

    def list_reservations(self, **kw):
        return self._reservations

    def get(self, task_id):
        for t in self._tasks:
            if t.get("id") == task_id:
                return t
        raise AssertionError(f"no such task: {task_id}")


def test_cli_doctor_reports_diagnoses_without_repair(capsys, monkeypatch):
    tasks = [_task(task_id="t-1", status="started")]
    fake = _ListClient(tasks)
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr("agent_dispatch.__main__._scope_repo", lambda args: "repo")
    monkeypatch.setattr(doctor, "resolve_worktree", lambda wt, **k: {"status": "finalized"})

    rc = _cmd_doctor(_args(["doctor"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["examined"] == 1
    assert out["diagnoses"][0]["verdict"] == "orphaned_worktree_gone"
    assert "repaired" not in out
    assert fake.list_calls[0]["status"] == "claimed,started,suspended"


def test_cli_doctor_honors_patched_resolver_without_real_agent_worktrees(
    capsys, monkeypatch
):
    """Regression test: `_cmd_doctor` must pass `resolve=doctor.resolve_worktree`
    explicitly (a call-time attribute lookup) rather than relying on
    `diagnose`'s own default parameter, which -- like any Python default --
    binds once at def-time and would silently ignore a monkeypatched
    `doctor.resolve_worktree`. Also patches `agent_worktrees_launch_prefix`
    to `None` so this can't coincidentally pass via a real, host-installed
    agent-worktrees CLI (that gap is exactly how this bug first shipped)."""
    monkeypatch.setattr(doctor, "agent_worktrees_launch_prefix", lambda: None)
    tasks = [_task(task_id="t-1", status="started")]
    fake = _ListClient(tasks)
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr("agent_dispatch.__main__._scope_repo", lambda args: "repo")
    monkeypatch.setattr(doctor, "resolve_worktree", lambda wt, **k: {"status": "finalized"})

    rc = _cmd_doctor(_args(["doctor"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["diagnoses"][0]["verdict"] == "orphaned_worktree_gone"


def test_cli_doctor_repair_flag_repairs_orphaned_tasks(capsys, monkeypatch):
    monkeypatch.setattr(doctor, "agent_worktrees_launch_prefix", lambda: None)
    tasks = [_task(task_id="t-1", status="started")]
    fake = _ListClient(tasks)
    fake.fail_spawn = lambda key, **k: {"state": "failed"}
    fake.yield_task = lambda tid, wid, **k: {"status": "queued"}
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr("agent_dispatch.__main__._scope_repo", lambda args: "repo")
    monkeypatch.setattr(doctor, "resolve_worktree", lambda wt, **k: {"status": "finalized"})

    rc = _cmd_doctor(_args(["doctor", "--repair"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["repaired"]) == 1
    assert out["repaired"][0]["task"]["status"] == "queued"


def test_cli_doctor_without_repair_never_mutates(capsys, monkeypatch):
    monkeypatch.setattr(doctor, "agent_worktrees_launch_prefix", lambda: None)
    tasks = [_task(task_id="t-1", status="started")]
    fake = _ListClient(tasks)

    def _boom(*a, **k):
        raise AssertionError("must not mutate without --repair")

    fake.fail_spawn = _boom
    fake.yield_task = _boom
    fake.release = _boom
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr("agent_dispatch.__main__._scope_repo", lambda args: "repo")
    monkeypatch.setattr(doctor, "resolve_worktree", lambda wt, **k: {"status": "finalized"})

    rc = _cmd_doctor(_args(["doctor"]))
    assert rc == 0


def test_cli_doctor_repo_unresolved_errors(capsys, monkeypatch):
    monkeypatch.setattr("agent_dispatch.__main__._scope_repo", lambda args: None)
    rc = _cmd_doctor(_args(["doctor"]))
    assert rc == 2


# -- reservation-history liveness (#2884) ----------------------------------


def _reservation(*, attempt, key, session_handle=None):
    return {"attempt": attempt, "key": key, "session_handle": session_handle}


def test_diagnose_reports_earlier_attempt_live_when_latest_is_gone():
    task = _task(task_id="t-1", status="started", worktree_id=None, reservation_key=None)
    reservations = [
        _reservation(attempt=1, key="k1", session_handle="local-body:sess-old"),
        _reservation(attempt=2, key="k2", session_handle="local-body:sess-new"),
    ]

    def local_verdict(sid):
        return "live" if sid == "sess-old" else "gone"

    d = doctor.diagnose(
        task,
        reservations=reservations,
        local_session_verdict=local_verdict,
        fleet_session_verdict=lambda host, sid: "unknown",
    )
    assert d.verdict == doctor.EARLIER_ATTEMPT_LIVE_VERDICT
    assert d.live_attempt == 1
    assert d.live_session_id == "sess-old"
    assert d.live_host is None
    assert "sess-old" in d.detail


def test_diagnose_earlier_attempt_live_as_dict_includes_live_fields():
    task = _task(task_id="t-1", status="started", worktree_id=None, reservation_key=None)
    reservations = [
        _reservation(attempt=1, key="k1", session_handle="local-body:sess-old"),
        _reservation(attempt=2, key="k2", session_handle="local-body:sess-new"),
    ]
    d = doctor.diagnose(
        task,
        reservations=reservations,
        local_session_verdict=lambda sid: "live" if sid == "sess-old" else "gone",
        fleet_session_verdict=lambda host, sid: "unknown",
    )
    out = d.as_dict()
    assert out["live_attempt"] == 1
    assert out["live_session_id"] == "sess-old"
    assert out["live_host"] is None


def test_diagnose_as_dict_omits_live_fields_for_ordinary_verdicts():
    """The reservation-history keys must not appear at all for a verdict that
    doesn't carry live-attempt data -- preserves the existing JSON shape for
    exact consumers that never opted into --check-live-sessions."""
    task = _task()
    d = doctor.diagnose(task, resolve=lambda wt: {"status": "finalized"})
    assert d.verdict == "orphaned_worktree_gone"
    out = d.as_dict()
    assert "live_attempt" not in out
    assert "live_session_id" not in out
    assert "live_host" not in out


def test_diagnose_fleet_earlier_attempt_live_preserves_host():
    task = _task(task_id="t-1", status="started", worktree_id=None, reservation_key=None)
    reservations = [
        _reservation(attempt=1, key="k1", session_handle="fleet-body:borealis:sess-old"),
        _reservation(attempt=2, key="k2", session_handle="fleet-body:borealis:sess-new"),
    ]

    def fleet_verdict(host, sid):
        return "live" if sid == "sess-old" else "gone"

    d = doctor.diagnose(
        task,
        reservations=reservations,
        local_session_verdict=lambda sid: "unknown",
        fleet_session_verdict=fleet_verdict,
    )
    assert d.verdict == doctor.EARLIER_ATTEMPT_LIVE_VERDICT
    assert d.live_host == "borealis"
    assert "borealis" in d.detail


def test_diagnose_no_history_signal_when_latest_attempt_is_live():
    task = _task(task_id="t-1", status="started", worktree_id=None, reservation_key=None)
    reservations = [
        _reservation(attempt=1, key="k1", session_handle="local-body:sess-old"),
        _reservation(attempt=2, key="k2", session_handle="local-body:sess-new"),
    ]
    d = doctor.diagnose(
        task,
        reservations=reservations,
        local_session_verdict=lambda sid: "live",
        fleet_session_verdict=lambda host, sid: "unknown",
        resolve=lambda wt: {"status": "active"},
    )
    assert d.verdict != doctor.EARLIER_ATTEMPT_LIVE_VERDICT


def test_diagnose_no_history_signal_when_every_attempt_is_gone():
    task = _task(task_id="t-1", status="started", worktree_id=None, reservation_key=None)
    reservations = [
        _reservation(attempt=1, key="k1", session_handle="local-body:sess-old"),
        _reservation(attempt=2, key="k2", session_handle="local-body:sess-new"),
    ]
    d = doctor.diagnose(
        task,
        reservations=reservations,
        local_session_verdict=lambda sid: "gone",
        fleet_session_verdict=lambda host, sid: "unknown",
    )
    assert d.verdict != doctor.EARLIER_ATTEMPT_LIVE_VERDICT
    assert d.verdict == "unknown"  # falls through: no worktree recorded either


def test_diagnose_fleet_session_handle_resolved_by_host_and_id():
    task = _task(task_id="t-1", status="started", worktree_id=None, reservation_key=None)
    reservations = [
        _reservation(attempt=1, key="k1", session_handle="fleet-body:borealis:sess-old"),
        _reservation(attempt=2, key="k2", session_handle="fleet-body:borealis:sess-new"),
    ]
    seen = []

    def fleet_verdict(host, sid):
        seen.append((host, sid))
        return "live" if sid == "sess-old" else "gone"

    d = doctor.diagnose(
        task,
        reservations=reservations,
        local_session_verdict=lambda sid: "unknown",
        fleet_session_verdict=fleet_verdict,
    )
    assert d.verdict == doctor.EARLIER_ATTEMPT_LIVE_VERDICT
    assert d.live_session_id == "sess-old"
    assert ("borealis", "sess-old") in seen


def test_diagnose_reservations_omitted_is_unaffected():
    """Backward compatibility: omitting `reservations` (the default) must not
    change any existing verdict."""
    task = _task()
    d = doctor.diagnose(task, resolve=lambda wt: {"status": "finalized"})
    assert d.verdict == "orphaned_worktree_gone"


def test_cli_doctor_check_live_sessions_fetches_reservations_per_task(
    capsys, monkeypatch
):
    monkeypatch.setattr(doctor, "agent_worktrees_launch_prefix", lambda: None)
    tasks = [_task(task_id="t-1", status="started", worktree_id=None, reservation_key=None)]
    fake = _ListClient(tasks)
    fake.list_reservations_calls = []

    def _list_reservations(
        *, task_id=None, state=None, repo=None, label=None, latest_only=False, limit=1000
    ):
        if task_id is None:
            # The separate stuck-queued-reservation query also calls
            # list_reservations (with no task_id); irrelevant to this
            # test's own assertion below, which tracks only per-task
            # reservation-history calls.
            return []
        fake.list_reservations_calls.append(task_id)
        return [
            _reservation(attempt=1, key="k1", session_handle="local-body:sess-old"),
            _reservation(attempt=2, key="k2", session_handle="local-body:sess-new"),
        ]

    fake.list_reservations = _list_reservations
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr("agent_dispatch.__main__._scope_repo", lambda args: "repo")
    monkeypatch.setattr(
        doctor,
        "_default_local_session_verdict",
        lambda sid: "live" if sid == "sess-old" else "gone",
    )
    monkeypatch.setattr(doctor, "_default_fleet_session_verdict", lambda host, sid: "unknown")

    rc = _cmd_doctor(_args(["doctor", "--check-live-sessions"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert fake.list_reservations_calls == ["t-1"]
    assert out["diagnoses"][0]["verdict"] == doctor.EARLIER_ATTEMPT_LIVE_VERDICT
    assert out["diagnoses"][0]["live_session_id"] == "sess-old"


def test_cli_doctor_task_flag_diagnoses_one_task_via_get(capsys, monkeypatch):
    task = _task(task_id="t-1", status="started")
    fake = _ListClient([])
    fake.get_calls = []

    def _get(task_id):
        fake.get_calls.append(task_id)
        return task

    fake.get = _get
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr(doctor, "resolve_worktree", lambda wt, **k: {"status": "finalized"})

    rc = _cmd_doctor(_args(["doctor", "--task", "t-1"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert fake.get_calls == ["t-1"]
    assert out["examined"] == 1
    assert out["diagnoses"][0]["verdict"] == "orphaned_worktree_gone"


def test_diagnose_many_reports_truncated_when_reservation_page_is_full(monkeypatch):
    """A reservation-history page that comes back exactly at the request
    limit is never silently treated as complete -- report it distinctly
    instead of risking an analysis that missed a live earlier attempt."""
    monkeypatch.setattr(doctor, "_RESERVATION_HISTORY_LIMIT", 2)
    task = _task(task_id="t-1", status="started", worktree_id=None, reservation_key=None)

    class _Client:
        def list_reservations(self, *, task_id, limit):
            assert limit == 2
            return [
                _reservation(attempt=1, key="k1", session_handle="local-body:sess-old"),
                _reservation(attempt=2, key="k2", session_handle="local-body:sess-new"),
            ]

    payload = doctor.diagnose_many(_Client(), [task], check_live_sessions=True)
    assert payload["diagnoses"][0]["verdict"] == doctor.RESERVATION_HISTORY_TRUNCATED_VERDICT
    assert "live_attempt" not in payload["diagnoses"][0]


def test_diagnose_many_untruncated_page_still_diagnoses_normally(monkeypatch):
    monkeypatch.setattr(doctor, "_RESERVATION_HISTORY_LIMIT", 10)
    monkeypatch.setattr(doctor, "_default_local_session_verdict", lambda sid: "gone")
    monkeypatch.setattr(doctor, "_default_fleet_session_verdict", lambda host, sid: "unknown")
    task = _task(task_id="t-1", status="started", worktree_id=None, reservation_key=None)

    class _Client:
        def list_reservations(self, *, task_id, limit):
            return [_reservation(attempt=1, key="k1", session_handle="local-body:sess-old")]

    payload = doctor.diagnose_many(_Client(), [task], check_live_sessions=True)
    assert payload["diagnoses"][0]["verdict"] != doctor.RESERVATION_HISTORY_TRUNCATED_VERDICT


def test_diagnose_many_repairs_only_the_reservation_for_a_terminal_task(monkeypatch):
    """Regression (copilot-extensions#3025): `--task` fetches a task of any
    status (unlike the repo/label sweep, which is pre-filtered to
    EXAMINED_STATUSES). A terminal task must never have its *task state*
    transitioned (`yield_task`/`release` would be an invalid transition on an
    already-finished task) -- but its stale reservation, if its worktree
    resolves as confirmed gone, is now cleared (force + confirmed_absent)
    rather than left permanently fencing its exclusive_key."""
    monkeypatch.setattr(doctor, "resolve_worktree", lambda wt, **k: {"status": "finalized"})
    terminal_task = _task(task_id="t-1", status="submitted")

    class _Client:
        def fail_spawn(self, key, *, detail=None, force=False, confirmed_absent=False, **k):
            assert force is True
            assert confirmed_absent is True
            return {"state": "failed"}

        def yield_task(self, *a, **k):
            raise AssertionError("must not transition a terminal task's status")

        def release(self, *a, **k):
            raise AssertionError("must not transition a terminal task's status")

    payload = doctor.diagnose_many(_Client(), [terminal_task], repair_orphaned=True)
    assert payload["diagnoses"][0]["verdict"] == "orphaned_worktree_gone"
    assert len(payload["repaired"]) == 1
    assert payload["repaired"][0]["reservation"] == {"state": "failed"}
    assert "skipped" in payload["repaired"][0]["task"]


def test_diagnose_many_still_repairs_an_examined_status_task(monkeypatch):
    monkeypatch.setattr(doctor, "resolve_worktree", lambda wt, **k: {"status": "finalized"})
    task = _task(task_id="t-1", status="started")

    class _Client:
        def fail_spawn(self, key, **k):
            return {"state": "failed"}

        def yield_task(self, task_id, worker_id, **k):
            return {"status": "queued"}

    payload = doctor.diagnose_many(_Client(), [task], repair_orphaned=True)
    assert len(payload["repaired"]) == 1
    assert payload["repaired"][0]["task"]["status"] == "queued"


def test_diagnose_many_never_repairs_a_non_examined_non_terminal_status(monkeypatch):
    """A status that is neither EXAMINED_STATUSES nor TERMINAL_STATES (e.g.
    `proposed`, which has no owner/reservation to repair in the first
    place) stays excluded from auto-repair entirely. `queued` is now
    examined too, but a queued task with a reservation always diagnoses as
    :data:`doctor.QUEUED_STUCK_RESERVATION_VERDICT` (never
    `REPAIRABLE_VERDICT`) -- see
    ``test_diagnose_many_never_repairs_a_queued_stuck_reservation`` below."""
    monkeypatch.setattr(doctor, "resolve_worktree", lambda wt, **k: {"status": "finalized"})
    task = _task(task_id="t-1", status="proposed")

    class _Client:
        def fail_spawn(self, *a, **k):
            raise AssertionError("must not repair a proposed task")

        def yield_task(self, *a, **k):
            raise AssertionError("must not repair a proposed task")

        def release(self, *a, **k):
            raise AssertionError("must not repair a proposed task")

    payload = doctor.diagnose_many(_Client(), [task], repair_orphaned=True)
    assert payload["repaired"] == []


def test_find_stuck_queued_reservations_queries_failed_state_separately():
    """The query is independent: list_reservations(state=FAILED) rather than
    expanding the bounded task sweep, so a large queued backlog never
    consumes the sweep's own --limit."""

    class _Client:
        def __init__(self):
            self.list_reservations_calls = []

        def list_reservations(self, **kw):
            self.list_reservations_calls.append(kw)
            return [{"key": "k1", "task_id": "t-1", "state": "failed"}]

        def get(self, task_id):
            assert task_id == "t-1"
            return _task(
                task_id="t-1",
                status="queued",
                worktree_id=None,
                reservation_key="k1",
            ) | {
                "spawn_reservation": {
                    "key": "k1",
                    "attempt": 2,
                    "state": "failed",
                    "detail": "boom",
                }
            }

    client = _Client()
    diagnoses = doctor.find_stuck_queued_reservations(client, repo="r", label="l", limit=50)
    assert len(diagnoses) == 1
    assert diagnoses[0].verdict == doctor.QUEUED_STUCK_RESERVATION_VERDICT
    assert client.list_reservations_calls == [
        {
            "state": doctor.SpawnState.FAILED,
            "repo": "r",
            "label": "l",
            "latest_only": True,
            "limit": 50,
        }
    ]


def test_find_stuck_queued_reservations_skips_task_no_longer_queued():
    """A failed reservation whose task has since progressed past `queued`
    (claimed a fresh attempt, etc.) is not stuck anymore -- must not report
    it as if it still were."""

    class _Client:
        def list_reservations(self, **kw):
            return [{"key": "k1", "task_id": "t-1", "state": "failed"}]

        def get(self, task_id):
            return _task(task_id="t-1", status="claimed")

    assert doctor.find_stuck_queued_reservations(_Client()) == []


def test_find_stuck_queued_reservations_skips_superseded_reservation():
    """The task is still `queued`, but its *current* reservation is a newer
    attempt than the one this FAILED row named -- a fresh attempt already
    superseded it, so it is not stuck."""

    class _Client:
        def list_reservations(self, **kw):
            return [{"key": "k1-old", "task_id": "t-1", "state": "failed"}]

        def get(self, task_id):
            task = _task(task_id="t-1", status="queued", worktree_id=None)
            task["spawn_reservation"] = {"key": "k2-new", "attempt": 2, "state": "reserving"}
            return task

    assert doctor.find_stuck_queued_reservations(_Client()) == []


def test_find_stuck_queued_reservations_skips_rearmed_race():
    """Same reservation key as the FAILED row the initial list query named,
    task still queued -- but an operator's `reservations rearm` raced in
    between (`failed` -> `rearmed`) before this per-task fetch. No longer
    stuck; must not be reported."""

    class _Client:
        def list_reservations(self, **kw):
            return [{"key": "k1", "task_id": "t-1", "state": "failed"}]

        def get(self, task_id):
            task = _task(task_id="t-1", status="queued", worktree_id=None)
            task["spawn_reservation"] = {"key": "k1", "attempt": 1, "state": "rearmed"}
            return task

    assert doctor.find_stuck_queued_reservations(_Client()) == []


def test_find_stuck_queued_reservations_skips_vanished_task():
    """A confirmed 404 (the task no longer exists) is the only failure this
    skips -- see the companion propagation test below."""

    class _Client:
        def list_reservations(self, **kw):
            return [{"key": "k1", "task_id": "t-1", "state": "failed"}]

        def get(self, task_id):
            raise DispatchError(404, "no such task")

    assert doctor.find_stuck_queued_reservations(_Client()) == []


def test_find_stuck_queued_reservations_propagates_non_404_errors():
    """An auth failure, a coordinator 5xx, or a transport error must never
    look like a vanished task -- doctor should fail loudly, not silently
    report an incomplete/empty diagnosis during the exact outage it exists
    to investigate."""

    class _Client:
        def list_reservations(self, **kw):
            return [{"key": "k1", "task_id": "t-1", "state": "failed"}]

        def get(self, task_id):
            raise DispatchError(503, "coordinator unavailable")

    with pytest.raises(DispatchError):
        doctor.find_stuck_queued_reservations(_Client())


def test_cmd_doctor_merges_stuck_queued_reservations_into_sweep(capsys, monkeypatch):
    """CLI-level: the repo/label sweep's own diagnoses and the separately-
    queried stuck-queued-reservation diagnoses land in one combined payload,
    with `examined` reflecting both sources."""
    tasks = [_task(task_id="t-1", status="started")]

    class _Client(_ListClient):
        def __init__(self):
            super().__init__(tasks)

        def list_reservations(self, **kw):
            return [{"key": "k2", "task_id": "t-2", "state": "failed"}]

        def get(self, task_id):
            task = _task(task_id="t-2", status="queued", worktree_id=None)
            task["spawn_reservation"] = {"key": "k2", "attempt": 1, "state": "failed"}
            return task

    fake = _Client()
    monkeypatch.setattr("agent_dispatch.__main__._client", lambda args: fake)
    monkeypatch.setattr("agent_dispatch.__main__._scope_repo", lambda args: "repo")
    monkeypatch.setattr(doctor, "resolve_worktree", lambda wt, **k: {"status": "finalized"})

    rc = _cmd_doctor(_args(["doctor"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["examined"] == 2
    verdicts = {d["verdict"] for d in out["diagnoses"]}
    assert "orphaned_worktree_gone" in verdicts
    assert doctor.QUEUED_STUCK_RESERVATION_VERDICT in verdicts

