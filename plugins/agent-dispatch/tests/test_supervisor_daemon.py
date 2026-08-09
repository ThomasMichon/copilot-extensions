"""Tests for the singleton supervisor daemon (registered-supervision runtime).

Covered:

* **command building** -- a supervised-lane registration reconstructs the
  ``agent-dispatch supervise`` argv; an unsupported kind raises;
* **reconcile** -- start on register, stop on remove/pause, restart on spec
  change, backoff-gated + cap-bounded revive of a crashed unit, skip of an
  unsupported kind; and
* **serve** -- the single-instance election stands a second daemon down
  (pin-not-failover), and shutdown winds every unit down.

Everything the daemon touches outside itself -- the launcher, the clock, the
sleep, the coordinator client -- is injected as a fake, so no real process or
server is started.
"""

from __future__ import annotations

import pytest

from agent_dispatch.supervisor_daemon import (
    SupervisorDaemon,
    UnsupportedKind,
    build_command,
    supervisor_lease_scope,
)
from tests._helpers import TEST_REPO


# -- fakes -------------------------------------------------------------------


class FakeProc:
    def __init__(self):
        self._returncode: int | None = None
        self.terminated = False

    def crash(self, code: int = 1) -> None:
        self._returncode = code

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        if self._returncode is None:
            self._returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode or 0


class FakeLauncher:
    def __init__(self):
        self.launched: list[tuple[str, FakeProc]] = []

    def launch(self, reg: dict, cmd: list[str]) -> FakeProc:
        proc = FakeProc()
        self.launched.append((reg["id"], proc))
        return proc

    def proc_for(self, rid: str) -> FakeProc:
        return [p for (r, p) in self.launched if r == rid][-1]


class FakeClient:
    def __init__(self, regs: list[dict]):
        self._regs = regs
        self.lease_holder: str | None = None
        self.released: list[str] = []

    def set_regs(self, regs: list[dict]) -> None:
        self._regs = regs

    def list_registrations(self, *, machine=None, env=None, include_paused=True):
        out = []
        for r in self._regs:
            if machine is not None and r.get("machine") != machine:
                continue
            if env is not None and r.get("env", "default") != env:
                continue
            if not include_paused and r.get("status") == "paused":
                continue
            out.append(r)
        return out

    def acquire_schedule_lease(self, scope, holder, **kw):
        if self.lease_holder is None:
            self.lease_holder = holder
            return {"granted": True, "lease": {"holder": holder}}
        return {"granted": self.lease_holder == holder,
                "lease": {"holder": self.lease_holder}}

    def release_schedule_lease(self, scope, holder, **kw):
        self.released.append(scope)
        if self.lease_holder == holder:
            self.lease_holder = None
        return {"released": True}

    def get_schedule_lease(self, scope):
        return {"holder": self.lease_holder} if self.lease_holder else None


class Clock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeLock:
    def __init__(self, granted: bool = True):
        self._granted = granted
        self.acquired = False
        self.released = False

    def acquire(self) -> bool:
        if self._granted:
            self.acquired = True
        return self._granted

    def release(self) -> None:
        self.released = True


def _reg(rid, **over) -> dict:
    r = {
        "id": rid,
        "kind": "supervised-lane",
        "spec": {"repo": TEST_REPO, "max_concurrent": 1, "max_attempts": 3},
        "machine": "lambda-core",
        "env": "default",
        "status": "active",
    }
    r.update(over)
    return r


def _daemon(client, launcher, **kw):
    return SupervisorDaemon(
        client, "lambda-core", "default",
        launcher=launcher, sleep=lambda _s: None, **kw,
    )


# -- command building --------------------------------------------------------


def test_build_command_supervised_lane():
    reg = _reg("a", spec={
        "repo": TEST_REPO, "labels": ["x", "y"], "max_concurrent": 2,
        "max_attempts": 5, "headless_labels": ["y"], "headless_agent": "task-worker",
        "interval": 15.0,
    })
    cmd = build_command(reg, python="PY")
    assert cmd[:4] == ["PY", "-m", "agent_dispatch", "supervise"]
    assert "--repo" in cmd and TEST_REPO in cmd
    assert cmd.count("--label") == 2
    assert "--max-concurrent" in cmd and "2" in cmd
    assert "--headless-label" in cmd and "--headless-agent" in cmd
    # a supervised-lane does NOT run an evaluator -- that is the 'evaluator' kind
    assert "--evaluator" not in cmd


def test_build_command_all_repos():
    cmd = build_command(_reg("a", spec={"all_repos": True}), python="PY")
    assert "--all-repos" in cmd
    assert "--repo" not in cmd


def test_build_command_evaluator_inline_spec():
    reg = _reg("e", kind="evaluator", spec={
        "evaluator_spec": {"states": {}}, "all_repos": True, "labels": ["dampener"],
    })
    materialized = {}

    def mat(name, payload):
        materialized[name] = payload
        return f"/run/{name}.json"

    cmd = build_command(reg, python="PY", materialize=mat)
    assert cmd[:4] == ["PY", "-m", "agent_dispatch", "supervise"]
    assert "--evaluator" in cmd and "/run/evaluator.json" in cmd
    assert "--all-repos" in cmd
    assert materialized["evaluator"] == {"states": {}}


def test_build_command_evaluator_path_ref():
    reg = _reg("e", kind="evaluator", spec={"evaluator": "eval.json", "repo": TEST_REPO})
    cmd = build_command(reg, python="PY")  # no materializer needed for a path ref
    assert "--evaluator" in cmd and "eval.json" in cmd


def test_build_command_schedule():
    reg = _reg("s", kind="schedule",
               spec={"id": "nightly", "repo": TEST_REPO, "interval_seconds": 3600})
    captured = {}

    def mat(name, payload):
        captured[name] = payload
        return f"/run/{name}.json"

    cmd = build_command(reg, python="PY", materialize=mat)
    assert cmd[:5] == ["PY", "-m", "agent_dispatch", "schedule", "serve"]
    assert "/run/schedule.json" in cmd
    # wrapped as a one-entry spec the timer producer consumes
    assert captured["schedule"] == {"schedules": [reg["spec"]]}


def test_build_command_emitter():
    reg = _reg("m", kind="emitter", spec={"url": "http://x", "port": 9400})
    cmd = build_command(reg, python="PY", materialize=lambda n, p: f"/run/{n}.json")
    assert cmd[:4] == ["PY", "-m", "agent_dispatch", "webhook"]
    assert "--config" in cmd and "/run/emitter.json" in cmd
    assert "--port" in cmd and "9400" in cmd


def test_build_command_needs_materializer_for_inline_spec():
    reg = _reg("s", kind="schedule", spec={"id": "n", "repo": TEST_REPO})
    with pytest.raises(UnsupportedKind):
        build_command(reg, python="PY")  # no materializer -> refused


def test_build_command_rejects_unsupported_kind():
    with pytest.raises(UnsupportedKind):
        build_command(_reg("a", kind="totally-unknown", spec={"x": 1}))


def test_lease_scope_format():
    assert supervisor_lease_scope("lambda-core", "default") == \
        "supervisor:lambda-core:default"
    assert supervisor_lease_scope(None, "") == "supervisor:local:default"


# -- reconcile ---------------------------------------------------------------


def test_reconcile_starts_a_unit_per_registration():
    client = FakeClient([_reg("a"), _reg("b")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    summary = d.reconcile_once()
    assert set(summary.started) == {"a", "b"}
    assert summary.running == ["a", "b"]
    # idempotent: a second reconcile with no change starts nothing
    summary2 = d.reconcile_once()
    assert summary2.started == []
    assert summary2.running == ["a", "b"]


def test_reconcile_stops_removed_registration():
    client = FakeClient([_reg("a"), _reg("b")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    d.reconcile_once()
    client.set_regs([_reg("a")])  # b removed
    summary = d.reconcile_once()
    assert summary.stopped == ["b"]
    assert summary.running == ["a"]
    assert launcher.proc_for("b").terminated is True


def test_reconcile_stops_paused_registration():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    d.reconcile_once()
    client.set_regs([_reg("a", status="paused")])
    summary = d.reconcile_once()
    assert summary.stopped == ["a"]
    assert summary.running == []


def test_reconcile_restarts_on_spec_change():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    d.reconcile_once()
    first = launcher.proc_for("a")
    client.set_regs([_reg("a", spec={"repo": TEST_REPO, "max_concurrent": 9})])
    summary = d.reconcile_once()
    assert summary.restarted == ["a"]
    assert first.terminated is True
    assert launcher.proc_for("a") is not first


def test_reconcile_revives_crashed_unit_with_backoff_and_cap():
    clock = Clock(0.0)
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher, clock=clock, restart_backoff=10.0, max_restarts=2)
    d.reconcile_once()  # start proc0
    launcher.proc_for("a").crash()

    # t=0: revive (restart_after was 0)
    s1 = d.reconcile_once()
    assert s1.revived == ["a"]
    launcher.proc_for("a").crash()

    # t=5: still in backoff (restart_after == 10) -> no revive
    clock.t = 5.0
    s2 = d.reconcile_once()
    assert s2.revived == []
    assert s2.running == []  # crashed proc is not "running"
    assert "a" in s2.backing_off  # unit retained, awaiting backoff

    # t=15: backoff elapsed -> revive again (2nd restart, hits the cap)
    clock.t = 15.0
    s3 = d.reconcile_once()
    assert s3.revived == ["a"]
    launcher.proc_for("a").crash()

    # t=30: exceeded max_restarts (2) -> left stopped, not revived
    clock.t = 30.0
    s4 = d.reconcile_once()
    assert s4.revived == []
    assert "a" in s4.skipped
    assert s4.running == []


def test_reconcile_skips_unsupported_kind():
    client = FakeClient([_reg("a", kind="totally-unknown", spec={"x": 1})])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    summary = d.reconcile_once()
    assert summary.skipped == ["a"]
    assert summary.started == []
    assert launcher.launched == []


# -- serve / single-instance -------------------------------------------------


def test_serve_stands_down_when_scope_already_held():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher, lock=FakeLock(granted=False))
    rc = d.serve(once=True)
    assert rc == 3
    assert launcher.launched == []  # never ran


def test_serve_runs_once_and_winds_down():
    client = FakeClient([_reg("a"), _reg("b")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    seen = []
    d = _daemon(client, launcher, lock=lock)
    rc = d.serve(once=True, on_cycle=seen.append)
    assert rc == 0
    assert set(seen[0].started) == {"a", "b"}
    # shutdown terminated every unit and released the singleton lock
    assert launcher.proc_for("a").terminated is True
    assert launcher.proc_for("b").terminated is True
    assert lock.released is True


def test_serve_unguarded_skips_election():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher, lock=FakeLock(granted=False))
    rc = d.serve(once=True, single_instance=False)
    assert rc == 0
    assert launcher.proc_for("a")  # ran despite a held lock
