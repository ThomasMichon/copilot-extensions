"""Tests for agent_dispatch.doctor (Boundary I / #2577)."""

from __future__ import annotations

import json

from agent_dispatch import doctor
from agent_dispatch.__main__ import _cmd_doctor, build_parser


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


# -- CLI ---------------------------------------------------------------


class _ListClient:
    def __init__(self, tasks):
        self._tasks = tasks
        self.list_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list(self, **kw):
        self.list_calls.append(kw)
        return self._tasks


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
