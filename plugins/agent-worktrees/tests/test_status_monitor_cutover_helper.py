"""Rehearsals for the installer-driven status-monitor cutover helper."""

from __future__ import annotations

import threading
import time
import subprocess
import importlib.util
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from single_instance_lease import AlreadyRunningError, SingleInstance
from zdd import breadcrumb, routing

from agent_worktrees import status_monitor_cutover as smc


@pytest.mark.parametrize(("summary", "expected"), [
    ({"enabled": False, "action": "noop", "reason": "disabled"}, 0),
    ({"enabled": True, "action": "noop", "reason": "no-live-monitor"}, 0),
    ({"enabled": True, "action": "noop", "reason": "no-routed-monitor"}, 1),
    ({"enabled": True, "action": "noop", "reason": "cutover-busy:123"}, 1),
    ({"enabled": True, "action": "refused", "result": {"ok": False}}, 1),
    ({"enabled": True, "action": "cutover", "result": {"ok": False, "rolled_back": True}}, 1),
    ({"enabled": True, "action": "cutover", "result": {"ok": False}}, 1),
    ({"enabled": True, "action": "cutover", "result": {"ok": True}}, 0),
    ({"enabled": True, "action": "cutover", "result": {"ok": "true"}}, 1),
    ({"enabled": True, "action": "restart", "restart": {"spawned": False}}, 1),
    ({"enabled": True, "action": "restart"}, 1),
    ({"enabled": True, "action": "restart", "restart": {"already_current": True}}, 0),
    ({"enabled": True, "action": "restart", "restart": {"spawned": True}}, 0),
    ({"enabled": True, "action": "unknown"}, 1),
])
def test_installer_wrapper_preserves_unsuccessful_monitor_outcomes(
    monkeypatch, capsys, summary, expected,
):
    monkeypatch.setattr(smc, "activate_after_update", lambda **kwargs: summary)
    assert smc.installer_after_update() == expected
    output = capsys.readouterr().out
    assert "rollback kept serving state" not in output


@pytest.fixture(autouse=True)
def legacy_or_posix_cutover_contract(monkeypatch):
    monkeypatch.setattr(smc.guarded_recovery, "enabled", lambda: False)


@pytest.fixture
def replacement_retention(tmp_path, monkeypatch):
    from agent_worktrees import runtime_monitor_retirements as retirements

    helper = Path(__file__).resolve().parents[3] / "libs/versioned-runtime/versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("replacement_test_runtime", helper)
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    for version in ("legacy", "current"):
        (tmp_path / "versions" / version).mkdir(parents=True)
        runtime.mark_complete(tmp_path, version, payload_hash=version)
    runtime.activate(tmp_path, "current", link_free=True)
    monkeypatch.setattr(retirements, "_runtime", lambda: runtime)
    return runtime


class _Handle:
    def __init__(self, daemon):
        self.daemon = daemon
        self.pid = daemon.pid

    def terminate(self) -> None:
        self.daemon.force_terminate()

    def poll(self) -> int | None:
        return None if self.daemon.alive else 0


@pytest.mark.parametrize("boundary_change", ["start-reused", "third-owner"])
def test_r1a_guarded_rollback_revalidates_owner_at_undrain_boundary(monkeypatch, boundary_change):
    from pathlib import Path
    from zdd.cutover import CutoverOrchestrator, CutoverResult

    old = routing.Endpoint("127.0.0.1", 61001, 101, "old", 7, "1010")
    new = routing.Endpoint("127.0.0.1", 61002, 202, "new", 8, "2020")
    third = routing.Endpoint("127.0.0.1", 61003, 303, "third", 9, "3030")
    state = {"active": new, "start": old.process_start_time}
    undrains, checks = [], []

    class Routing:
        def read_table(self, root):
            return {"active": state["active"].to_dict()}

        def publish_active_with_previous_guarded(self, root, **kwargs):
            assert kwargs["expected_active"] == new
            state["active"] = old
            if boundary_change == "start-reused":
                state["start"] = "9999"
            else:
                state["active"] = third

    def health(host, port):
        checks.append((host, port))
        return state["start"] == old.process_start_time

    def request(self, action, payload=None, **kwargs):
        assert action == "undrain"
        undrains.append((state["active"], state["start"]))
        return {"draining": False}

    monkeypatch.setattr(smc.ControlClient, "_request", request)
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda *a: pytest.fail("minted token"))
    orch = CutoverOrchestrator(
        Path("not-opened"), bind="127.0.0.1", version="new",
        spawn_passive=lambda port: pytest.fail("spawned native fixture"),
        health_check=health, predecessor_health_check=health,
        make_client=lambda url: smc.ControlClient(url, token="retained-fixture-token"),
        pick_free_port=lambda: 61002, routing_mod=Routing(), expected_old=old,
        refuse_old=lambda active: (
            None if state["start"] == old.process_start_time else "old identity changed"
        ),
    )
    monkeypatch.setattr(orch, "_await_exit", lambda handle: True)
    result = CutoverResult(ok=False)
    orch._rollback(
        old, SimpleNamespace(terminate=lambda: None), new.port, result,
        flipped=True, expected_old=old, expected_new=new, drain_started=True,
    )
    assert checks, "owner health was not checked before restoration"
    assert undrains == [], "guarded rollback sent undrain after retained owner changed"
    assert not result.rolled_back


@pytest.mark.parametrize("failure", [None, "health", "promotion", "route-race"])
def test_source_start_publishes_own_generation_without_stopping_the_predecessor(
    tmp_path, monkeypatch, failure,
):
    from zdd import diagnostics
    from agent_worktrees import runtime_identity

    slot = tmp_path / "application" / "versions" / "1.2.3"
    events = []
    previous = routing.Endpoint(bind="127.0.0.1", port=12001, pid=101, generation=1)
    table = {"active": previous.to_dict()}
    following = routing.Endpoint(bind="127.0.0.1", port=12002, pid=202, generation=2)
    monkeypatch.setattr(smc.sys, "prefix", str(slot / ".venv"))
    monkeypatch.setattr(runtime_identity, "PROCESS_GENERATION", "a" * 32)
    monkeypatch.setattr(smc, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr("agent_worktrees.status_monitor_runtime._status_monitor_enabled", lambda: True)
    monkeypatch.setattr(smc, "_acquire_cutover_lock", lambda *a, **k: SimpleNamespace(
        release=lambda: events.append("release"),
    ))
    monkeypatch.setattr(routing, "read_table", lambda *a, **k: table)
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda: "fixture-token")
    monkeypatch.setattr(smc, "pick_free_port", lambda: 12002)
    child = SimpleNamespace(pid=202, poll=lambda: None)
    monkeypatch.setattr(smc, "spawn_passive", lambda python, **k: events.append("spawn") or child)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "123")
    monkeypatch.setattr(smc, "active_generation_for_pid", lambda pid: 2)

    class Client:
        def __init__(self, url, **kwargs):
            self.prior = url.endswith(":12001")
            assert kwargs["token"] == "fixture-token"

        def health(self):
            if self.prior:
                events.append("prior-health")
                raise ConnectionError("prior generation is unavailable")
            if failure == "health":
                raise ConnectionError("new generation is unavailable")
            events.append("new-health")
            return {
                "status": "ready", "published": "promote" in events, "pid": 202,
                "runtime_slot": slot.name, "runtime_generation": "a" * 32,
            }

        def promote(self):
            events.append("promote")
            return {"adopted": failure != "promotion"}

    def publish(*args, **kwargs):
        events.append("publish")
        assert kwargs["expected_active"] == previous
        assert kwargs["pid"] == 202 and kwargs["process_start_time"] == "123"
        if failure == "route-race":
            raise routing.ActivePublicationRefused("route changed")
        return following, previous

    def cleanup(pid, birth, **kwargs):
        assert pid == 202 and birth == "123"
        events.append("cleanup-new")
        return {"exit_confirmed": True}

    monkeypatch.setattr(smc, "ControlClient", Client)
    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", publish)
    monkeypatch.setattr(routing, "restore_previous_if_owner", lambda *a, **k: (
        events.append("restore-own-route") or True
    ))
    monkeypatch.setattr(diagnostics, "terminate_pid_if_identity", cleanup)
    monkeypatch.setattr(smc, "activate_after_update", lambda **k: pytest.fail("legacy cutover entered"))
    if failure is None:
        assert smc.start_application(health_timeout=0.01) == 0
        assert events == ["prior-health", "spawn", "new-health", "publish", "promote", "new-health", "release"]
    else:
        with pytest.raises(RuntimeError if failure != "route-race" else routing.ActivePublicationRefused):
            smc.start_application(health_timeout=0.01)
        assert events[-2:] == ["cleanup-new", "release"]
        assert ("restore-own-route" in events) == (failure == "promotion")


def test_source_start_reuses_an_authenticated_current_generation(monkeypatch, tmp_path):
    from agent_worktrees import runtime_identity
    from zdd import diagnostics

    slot = tmp_path / "versions" / "1.2.3"
    endpoint = routing.Endpoint(
        bind="127.0.0.1", port=12002, pid=202, generation=2, process_start_time="123",
    )
    table = {"active": endpoint.to_dict()}
    released = []
    monkeypatch.setattr(smc.sys, "prefix", str(slot / ".venv"))
    monkeypatch.setattr(runtime_identity, "PROCESS_GENERATION", "a" * 32)
    monkeypatch.setattr("agent_worktrees.status_monitor_runtime._status_monitor_enabled", lambda: True)
    monkeypatch.setattr(smc, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(smc, "_acquire_cutover_lock", lambda *a, **k: SimpleNamespace(
        release=lambda: released.append(True),
    ))
    monkeypatch.setattr(routing, "read_table", lambda *a, **k: table)
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda: "fixture-token")
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "123")
    monkeypatch.setattr(smc, "ControlClient", lambda *a, **k: SimpleNamespace(health=lambda: {
        "status": "ready", "published": True, "pid": 202,
        "runtime_slot": slot.name, "runtime_generation": "a" * 32,
    }))
    monkeypatch.setattr(smc, "spawn_passive", lambda *a, **k: pytest.fail("current daemon replaced"))
    assert smc.start_application() == 0
    assert released == [True]


def test_failed_monitor_census_is_not_an_empty_inventory(monkeypatch):
    monkeypatch.setattr(smc, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        smc.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="fixture census failure",
        ),
    )
    with pytest.raises(OSError, match="native monitor process census failed"):
        smc._iter_status_monitor_pids()


@pytest.mark.parametrize("phase", ["recovery", "passive_reap"])
def test_activation_preserves_refusal_before_starting_cutover(tmp_path, monkeypatch, phase):
    events = []
    refusal = {"refused": True, "reason": "fixture native custody refusal"}
    record = breadcrumb.write_breadcrumb(tmp_path, state="started", new_pid=202)
    monkeypatch.setattr(smc, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr("agent_worktrees.status_monitor_runtime._status_monitor_enabled", lambda: True)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(smc, "_monitor_control_url_from_route", lambda: "http://127.0.0.1:1234")
    monkeypatch.setattr(
        smc, "_acquire_cutover_lock",
        lambda _: SimpleNamespace(release=lambda: events.append("release")),
    )

    def recover(*args, **kwargs):
        events.append("recovery")
        if phase == "recovery":
            return refusal
        breadcrumb.write_breadcrumb(tmp_path, state="rolled_back", new_pid=202)
        return {"recovered": True}

    def reap(original):
        assert original == record and phase == "passive_reap"
        events.append("passive_reap")
        return refusal

    monkeypatch.setattr(breadcrumb, "recover_stale_cutover", recover)
    monkeypatch.setattr(smc, "_reap_abandoned_passive", reap)
    monkeypatch.setattr(
        smc, "CutoverOrchestrator",
        lambda *args, **kwargs: pytest.fail("cutover started after native refusal"),
    )
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_reap_stale.schedule_delayed_daemon_health_reap",
        lambda: events.append("backstop"),
    )
    result = smc.activate_after_update(runtime_python="python")
    assert result["action"] == "refused"
    assert result["result"] == {"ok": False, "error": refusal["reason"]}
    assert events == (
        ["recovery", "release", "backstop"] if phase == "recovery"
        else ["recovery", "passive_reap", "release", "backstop"]
    )
    assert breadcrumb.read_breadcrumb(tmp_path)["new_pid"] == 202


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("content", ["{", "{}", '{"state":"unknown"}',
                                     '{"state":"conditional-started"}'])
def test_activation_refuses_unverifiable_custody_before_restart(
    tmp_path, monkeypatch, live, content,
):
    path = breadcrumb.breadcrumb_path(tmp_path)
    path.write_text(content)
    monkeypatch.setattr(smc, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr("agent_worktrees.status_monitor_runtime._status_monitor_enabled", lambda: True)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: live)
    monkeypatch.setattr(smc, "_monitor_control_url_from_route", lambda: None)
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_runtime._restart_status_monitor",
        lambda: pytest.fail("restart reached before custody refusal"),
    )
    result = smc.activate_after_update(runtime_python="python", monitor_was_live=True)
    assert result["action"] == "refused" and result["result"]["ok"] is False
    assert "owner-led recovery" in result["reason"]
    assert path.read_text() == content


class _FakeDaemon:
    def __init__(self, pid: int, *, port: int | None = None, token: str = "test-token"):
        self.pid = pid
        self.port = port
        self.alive = True
        self.draining = False
        self.promoted = False
        self.shutdown_requested = False
        self.shutdown_completed = threading.Event()
        self.busy = threading.Event()
        self.control = None
        self.promote_calls = 0
        self.token = token
        self.async_retirement = True
        self.health_pid = pid
        self.health_start = "1001"
        if port is not None:
            self.start(port)

    def start(self, port: int | None = None) -> None:
        self.control = smc.ControlServer(self._handle, port=port, token=self.token)
        self.control.start()
        self.port = self.control.port

    def _handle(self, action: str, payload: dict) -> dict:
        if action == "health":
            from agent_worktrees.runtime_monitor_retirements import CAPABILITY
            from agent_worktrees.status_monitor_recovery_contract import CAPABILITY as RECOVERY

            return {
                "status": "draining" if self.draining else "ready",
                "capabilities": [CAPABILITY, RECOVERY] if self.async_retirement else [],
                "runtime_generation": "a" * 32,
                "pid": self.health_pid, "process_start_time": self.health_start, "published": self.promoted,
                "worktrees": {"status": "ready" if self.promoted else "unready"},
                "repositories": {"admitting": self.promoted},
            }
        if action == "promote":
            self.promoted = True
            self.promote_calls += 1
            return {"adopted": True}
        if action == "drain":
            self.draining = True
            timeout = float(payload["timeout"])
            poll = max(0.01, float(payload["poll"]))
            deadline = time.time() + timeout
            while self.busy.is_set() and time.time() < deadline:
                time.sleep(poll)
            return {
                "drained": not self.busy.is_set(),
                "clean": not self.busy.is_set(),
                "forced": False,
                "busy_sessions": ["busy"] if self.busy.is_set() else [],
            }
        if action == "undrain":
            self.draining = False
            return {"draining": False}
        if action == "shutdown":
            self.shutdown_requested = True

            def _finish() -> None:
                time.sleep(0.2)
                self.alive = False
                self.shutdown_completed.set()
            if self.control is not None:
                self.control.close()

            threading.Thread(target=_finish, daemon=True).start()
            return {"shutdown": True}
        return {"ok": False}

    def force_terminate(self) -> None:
        if self.alive:
            self.alive = False
            self.shutdown_completed.set()
            if self.control is not None:
                self.control.close()


def _wait_for(predicate, *, timeout: float = 10.0, interval: float = 0.05, message: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(message)


@pytest.mark.parametrize("guarded_enabled", [False, True])
@pytest.mark.parametrize("mismatch", [None, "pid", "birth"])
@pytest.mark.parametrize("outgoing", ["qualified", "unobservable", "uncertain", "corrupt"])
def test_installation_promotes_successor_without_draining_busy_predecessor(
    tmp_path, monkeypatch, replacement_retention, guarded_enabled, mismatch, outgoing,
):
    original_routed_identity = smc.routed_monitor_identity
    monkeypatch.setattr(smc.guarded_recovery, "enabled", lambda: guarded_enabled)
    monkeypatch.setattr(
        smc.guarded_recovery, "negotiate",
        lambda *_args, **_kwargs: pytest.fail("asynchronous installation negotiated predecessor drain"),
    )
    old = _FakeDaemon(101, port=0)
    new = _FakeDaemon(202)
    if mismatch == "pid":
        new.health_pid = 303
    elif mismatch == "birth":
        new.health_start = "3003"
    old.busy.set()
    monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: "1001")
    routing.publish_active(
        tmp_path / "status-monitor-routing", bind="127.0.0.1", port=old.port,
        pid=old.pid, process_start_time="1001",
    )
    monkeypatch.setattr(smc, "routing_dir", lambda runtime_home=None: tmp_path / "status-monitor-routing")
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr("agent_worktrees.status_monitor_runtime._aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(
        smc, "routed_monitor_identity", lambda *args, **kwargs: (101, "1001", None),
    )
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda runtime_home=None: "test-token")
    monkeypatch.setattr(smc.locks, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: "1001")
    monkeypatch.setattr("agent_worktrees.status_monitor_runtime._status_monitor_enabled", lambda: True)
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_reap_stale.schedule_delayed_daemon_health_reap",
        lambda: None,
    )

    def spawn(_python, *, port):
        new.start(port)
        return _Handle(new)

    monkeypatch.setattr(smc, "spawn_passive", spawn)
    recovery_path = breadcrumb.breadcrumb_path(tmp_path / "status-monitor-routing")
    recovery_bytes = None
    if outgoing != "qualified":
        monkeypatch.setattr(smc, "routed_monitor_identity", original_routed_identity)
        monkeypatch.setattr("zdd.diagnostics._pid_alive", lambda pid: True)
        monkeypatch.setattr("zdd.process_exit.observe_original_exit", lambda pid, start: False)
        monkeypatch.setattr(smc.locks, "process_start_time", lambda pid: None if pid == 101 else "1001")
        monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: None if pid == 101 else "1001")
        if outgoing in {"uncertain", "corrupt"}:
            recovery_bytes = (
                b'{"state":"publication-uncertain","self_retirement":true,"new_pid":303}'
                if outgoing == "uncertain" else b'{"state":'
            )
            recovery_path.write_bytes(recovery_bytes)
        monkeypatch.setattr(
            breadcrumb, "recover_stale_cutover", lambda *a, **k: pytest.fail("recovered outgoing custody"),
        )
        monkeypatch.setattr(
            smc, "_reap_abandoned_passive", lambda *a: pytest.fail("reaped outgoing custody"),
        )
    try:
        result = smc.activate_after_update(
            runtime_python="python", asynchronous_retirement=True, health_timeout=0.01 if mismatch else 60,
            retirement_predecessor=(
                {"pid": 101, "start": "1001", "slot": "legacy", "release_claim": None}
                if outgoing == "unobservable" else None
            ),
        )
        if mismatch:
            assert result["result"]["ok"] is False and result["result"]["committed"] is False
            assert not new.promoted and not new.alive
            assert routing.read_table(tmp_path / "status-monitor-routing")["active"]["pid"] == old.pid
            assert old.alive and old.busy.is_set() and not old.draining
            return
        assert result["result"]["ok"] is True, result
        assert result["result"]["retirement_pending"] is True
        assert new.promoted and new.alive
        assert old.alive and old.busy.is_set()
        assert not old.draining and not old.shutdown_requested
        assert routing.read_table(tmp_path / "status-monitor-routing")["active"]["pid"] == new.pid
        if outgoing != "qualified":
            import base64
            import json

            archives = [
                json.loads(path.read_text())
                for path in (tmp_path / "status-monitor-observations").glob("*.json")
            ]
            assert archives
            if outgoing == "unobservable":
                from agent_worktrees import runtime_monitor_retirements as retirements

                records = retirements._records(tmp_path)
                assert len(records) == 1 and records[0][1]["state"] == "retiring"
            if recovery_bytes is not None:
                assert any(
                    base64.b64decode(record["breadcrumb"]) == recovery_bytes
                    for record in archives if record["breadcrumb"] is not None
                )
            replacement_retention.gc(tmp_path, min_age_days=0)
            assert (tmp_path / "versions" / "legacy").is_dir()
    finally:
        old.force_terminate()
        new.force_terminate()


def test_async_installation_replaces_unrouted_predecessor_without_termination(
    tmp_path, monkeypatch, replacement_retention,
):
    from agent_worktrees import status_monitor_runtime as runtime

    new = _FakeDaemon(202)
    directory = tmp_path / "status-monitor-routing"
    monkeypatch.setattr(smc, "routing_dir", lambda runtime_home=None: directory)
    monkeypatch.setattr(runtime, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(smc, "_monitor_control_url_from_route", lambda: None)
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda runtime_home=None: "test-token")
    monkeypatch.setattr(smc.locks, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: "1001")
    monkeypatch.setattr(runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(
        runtime, "_restart_status_monitor",
        lambda **_kwargs: pytest.fail("asynchronous installation used old-process termination"),
    )
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_reap_stale.schedule_delayed_daemon_health_reap",
        lambda: pytest.fail("asynchronous installation scheduled a second retirement owner"),
    )

    def spawn(_python, *, port):
        new.start(port)
        return _Handle(new)

    monkeypatch.setattr(smc, "spawn_passive", spawn)
    try:
        result = smc.activate_after_update(
            runtime_python="python", asynchronous_retirement=True,
            retirement_predecessor={"pid": 101, "start": "1001", "slot": None, "release_claim": None},
        )
        assert result["action"] == "cutover" and result["result"]["ok"], result
        assert result["result"]["retirement_pending"]
        assert new.promoted and new.alive
        assert routing.read_table(directory)["active"]["pid"] == new.pid
    finally:
        new.force_terminate()


def test_async_installation_rejects_an_unsupported_target_before_opening_admission(tmp_path, monkeypatch):
    old, new = _FakeDaemon(101, port=0), _FakeDaemon(202)
    new.async_retirement = False
    monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: "1001")
    monkeypatch.setattr(smc.locks, "process_start_time", lambda pid: "1001")
    routing.publish_active(
        tmp_path / "status-monitor-routing", bind="127.0.0.1", port=old.port,
        pid=old.pid, process_start_time="1001",
    )
    monkeypatch.setattr(smc, "routing_dir", lambda runtime_home=None: tmp_path / "status-monitor-routing")
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr("agent_worktrees.status_monitor_runtime._aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(
        smc, "routed_monitor_identity", lambda *args, **kwargs: (101, "1001", None),
    )
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda runtime_home=None: "test-token")
    monkeypatch.setattr("agent_worktrees.status_monitor_runtime._status_monitor_enabled", lambda: True)

    def spawn(_python, *, port):
        new.start(port)
        return _Handle(new)

    monkeypatch.setattr(smc, "spawn_passive", spawn)
    try:
        result = smc.activate_after_update(
            runtime_python="python", asynchronous_retirement=True, health_timeout=0.01,
        )
        assert "committed" in result["result"], result
        assert not result["result"]["ok"] and not result["result"]["committed"]
        assert "does not support asynchronous" in result["result"]["error"]
        assert old.alive and not old.draining and not old.shutdown_requested
        assert not new.promoted and not new.alive
        from agent_worktrees import runtime_monitor_retirements as retirements

        assert not retirements._records(tmp_path)
        assert routing.read_table(tmp_path / "status-monitor-routing")["active"]["pid"] == old.pid
    finally:
        old.force_terminate()
        new.force_terminate()


def test_async_route_requires_recorded_native_birth_before_health(tmp_path, monkeypatch):
    from agent_worktrees import runtime_monitor_identity as identity

    route = routing.Endpoint("127.0.0.1", 61001, 101, "old", 1)
    monkeypatch.setattr(identity.routing, "read_table", lambda *a, **k: {"active": route.to_dict()})
    monkeypatch.setattr(identity.diagnostics, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(identity.locks, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr(
        smc, "ControlClient", lambda *a, **k: pytest.fail("unbound route reached health"),
    )
    with pytest.raises(ValueError, match="process identity is unconfirmed"):
        identity.routed_identity(tmp_path, require_async_retirement=True)


@pytest.mark.parametrize("damage", [None, "generation", "empty-generation", "recovery-capability"])
def test_async_admission_keeps_native_custody_without_guarded_preparation_recovery(tmp_path, monkeypatch, damage):
    from agent_worktrees import runtime_monitor_identity as identity
    from agent_worktrees.runtime_monitor_retirements import CAPABILITY
    from agent_worktrees.status_monitor_recovery_contract import CAPABILITY as RECOVERY
    from zdd import process_exit

    directory = tmp_path / "status-monitor-routing"
    monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: "1001")
    monkeypatch.setattr(identity.locks, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr("zdd.diagnostics._pid_alive", lambda pid: pid == 101)
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda pid, start: False)
    routing.publish_active(directory, bind="127.0.0.1", port=41001, pid=101, process_start_time="1001")
    health = {
        "status": "ready", "pid": 101, "runtime_prefix": str(tmp_path),
        "runtime_generation": "a" * 32, "capabilities": [CAPABILITY, RECOVERY],
    }
    if damage == "generation":
        health["runtime_generation"] = None
    elif damage == "empty-generation":
        health["runtime_generation"] = ""
    elif damage == "recovery-capability":
        health["capabilities"] = [CAPABILITY]
    monkeypatch.setattr(identity.cutover, "ControlClient", lambda *a, **k: SimpleNamespace(health=lambda: health))
    monkeypatch.setattr(identity.cutover, "load_or_create_control_token", lambda root: "synthetic-token")
    assert identity.routed_identity(tmp_path, require_async_retirement=True) == (101, "1001", None)


@pytest.mark.parametrize("fallback", ["live", "unknown", "prepared", "retiring", "exited"])
def test_serving_owner_does_not_hide_unretained_live_fallback(tmp_path, monkeypatch, fallback):
    from agent_worktrees import runtime_monitor_identity as identity
    from agent_worktrees import runtime_monitor_retirements as retirements
    from zdd import process_exit

    identities = {101: "1001", 202: "2002"}
    monkeypatch.setattr("zdd.diagnostics.process_start_time", identities.get)
    monkeypatch.setattr(identity.locks, "process_start_time", identities.get)
    monkeypatch.setattr("zdd.diagnostics._pid_alive", lambda pid: True)
    def observe(pid, start):
        if pid == 202 and fallback == "unknown":
            raise PermissionError("native fallback exit is unavailable")
        return pid == 202 and fallback == "exited"

    monkeypatch.setattr(process_exit, "observe_original_exit", observe)
    directory = tmp_path / "status-monitor-routing"
    routing.publish_active(directory, bind="127.0.0.1", port=41002, pid=202, process_start_time="2002")
    routing.publish_active(
        directory, bind="127.0.0.1", port=41001, pid=101, process_start_time="1001", demote_existing=True,
    )
    if fallback in {"prepared", "retiring"}:
        retirements.reserve(
            tmp_path, {"pid": 202, "start": "2002", "slot": None, "release_claim": None},
            committed=fallback == "retiring",
        )
    table = routing.read_table(directory, strict=True)
    monkeypatch.setattr(identity.cutover, "load_or_create_control_token", lambda root: "synthetic-token")
    monkeypatch.setattr(identity.cutover, "ControlClient", lambda *a, **k: SimpleNamespace(health=lambda: {
        "status": "ready", "pid": 101, "runtime_prefix": str(tmp_path), "runtime_generation": "a" * 32,
        "capabilities": [retirements.CAPABILITY, smc.guarded_recovery.CAPABILITY],
    }))
    if fallback in {"retiring", "exited"}:
        assert identity.routed_identity(tmp_path, require_async_retirement=True) == (101, "1001", None)
    else:
        with pytest.raises(ValueError, match="Live fallback"):
            identity.routed_identity(tmp_path, require_async_retirement=True)
    assert routing.read_table(directory, strict=True) == table


@pytest.mark.parametrize("recorded,current,healthy", [
    (None, None, False), (None, "2002", False), ("2002", None, False),
    ("2002", "3003", False), ("2002", "2002", True),
])
def test_serving_successor_requires_recorded_and_live_native_birth(monkeypatch, recorded, current, healthy):
    from agent_worktrees import status_monitor_recovery_contract as recovery

    expected = routing.Endpoint("127.0.0.1", 41002, 202, process_start_time=recorded)
    monkeypatch.setattr(recovery.locks, "process_start_time", lambda pid: current)
    client = SimpleNamespace(health=lambda: {
        "status": "ready", "published": True, "pid": 202,
        "worktrees": {"status": "ready"}, "repositories": {"admitting": True},
    })
    assert recovery.successor_healthy("127.0.0.1", 41002, lambda url: client, expected=expected) is healthy


def test_discovery_cannot_publish_when_both_recorded_and_native_birth_are_missing(tmp_path, monkeypatch):
    from agent_worktrees import runtime_monitor_identity as identity
    import os

    endpoint = routing.Endpoint("127.0.0.1", 41002, os.getpid())
    monkeypatch.setattr(identity.routing, "read_table", lambda *a, **k: {"active": endpoint.to_dict()})
    monkeypatch.setattr(identity.locks, "process_start_time", lambda pid: None)
    monkeypatch.setattr(identity.locks, "write_lock", lambda *a, **k: pytest.fail("unverified discovery became ready"))
    assert not identity.publish_lock(tmp_path / "status-monitor.lock", extra={})


@pytest.mark.parametrize("changed", ["route", "native"])
def test_async_predecessor_binds_publication_to_admitted_native_owner(tmp_path, monkeypatch, changed):
    from agent_worktrees import runtime_monitor_identity as identity
    from agent_worktrees import status_monitor_recovery_contract as recovery

    route = routing.Endpoint("127.0.0.1", 61001, 101, "old", 1, "1001")
    monkeypatch.setattr(recovery, "active_endpoint", lambda path: route)
    monkeypatch.setattr(identity.locks, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr(smc, "ControlClient", lambda *a, **k: SimpleNamespace(health=lambda: {
        "pid": 101, "capabilities": ["status-monitor-async-retirement-v1", recovery.CAPABILITY],
        "runtime_generation": "a" * 32,
    }))
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda root: "fixture-token")
    predecessor, options = identity.async_predecessor(
        tmp_path, None, observed=(101, "1001", None), incumbent_expected=True,
    )
    assert predecessor["pid"] == 101 and options["expected_old"] == route
    if changed == "route":
        with pytest.raises(ValueError, match="route identity changed"):
            identity.async_predecessor(
                tmp_path, None, observed=(101, "9999", None), incumbent_expected=True,
            )
    else:
        monkeypatch.setattr(identity.locks, "process_start_time", lambda pid: "9999")
        assert options["refuse_old"](route.to_dict()) is not None


def test_held_cutover_guard_preserves_prior_witness_before_new_attempt(
    tmp_path, monkeypatch, replacement_retention,
):
    from zdd.cutover import CutoverResult
    from agent_worktrees import runtime_monitor_retirements as retirements
    from agent_worktrees import status_monitor_runtime as runtime

    directory = tmp_path / "status-monitor-routing"
    monkeypatch.setattr(smc, "routing_dir", lambda *a: directory)
    monkeypatch.setattr(runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(runtime, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(smc, "_monitor_control_url_from_route", lambda: "http://127.0.0.1:61001")
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda *a: "fixture-token")

    def acquire(*a, **k):
        breadcrumb.write_breadcrumb(directory, state="started", self_retirement=True, new_pid=202)
        return SimpleNamespace(release=lambda: None)

    monkeypatch.setattr(smc, "_acquire_cutover_lock", acquire)
    monkeypatch.setattr(retirements, "reserve", lambda root, record, **k: (
        pytest.fail("unobserved retirement grant") if record is not None else False
    ))
    monkeypatch.setattr(
        breadcrumb, "recover_stale_cutover", lambda *a, **k: pytest.fail("legacy recovery reached"),
    )
    def run(**_kwargs):
        assert list((tmp_path / "status-monitor-observations").glob("*.json"))
        assert not breadcrumb.breadcrumb_path(directory).exists()
        return CutoverResult(ok=False, error="controlled incoming failure")

    monkeypatch.setattr(smc, "CutoverOrchestrator", lambda *a, **k: SimpleNamespace(run=run))
    result = smc.activate_after_update(
        runtime_python="python", asynchronous_retirement=True, monitor_was_live=True,
    )
    assert result["action"] == "cutover" and not result["result"]["ok"]
    assert not breadcrumb.breadcrumb_path(directory).exists()


@pytest.mark.parametrize("ready", [False, True])
def test_safe_supersession_requires_ready_successor_even_after_generation_reset(
    tmp_path, monkeypatch, ready,
):
    from agent_worktrees import self_retire, status_monitor_recovery_contract as recovery

    endpoint = routing.Endpoint("127.0.0.1", 61002, 202, "new", 1, "2002")
    table = {"active": endpoint.to_dict()}
    monkeypatch.setattr(recovery, "successor_healthy", lambda *args, **kwargs: ready)
    assert self_retire.is_superseded(
        tmp_path, 101, 100, read_table=lambda _root: table,
        is_listening=lambda *args: True, require_ready=True,
    ) is ready


@pytest.mark.parametrize("capabilities", [None, "status-monitor-async-retirement-v1", {}])
def test_async_target_capability_is_not_inferred_from_malformed_metadata(capabilities):
    from agent_worktrees.status_monitor_health import check_successor
    from zdd.cutover import CutoverError

    client = SimpleNamespace(health=lambda: {"status": "ready", "capabilities": capabilities})
    with pytest.raises(CutoverError, match="does not support asynchronous"):
        check_successor("127.0.0.1", 61001, lambda _url: client, require_async_retirement=True)


@pytest.mark.parametrize("capabilities", [None, "status-monitor-async-retirement-v1", {}, [], "timeout"])
def test_async_predecessor_without_contract_retains_native_custody(tmp_path, monkeypatch, capabilities):
    from agent_worktrees import runtime_monitor_identity as identity

    endpoint = routing.Endpoint("127.0.0.1", 61001, 101, "old", 1, "1001")
    monkeypatch.setattr(identity.routing, "read_table", lambda *a, **k: {"active": endpoint.to_dict()})
    monkeypatch.setattr(identity.diagnostics, "_pid_alive", lambda pid: True)
    monkeypatch.setattr("zdd.process_exit.observe_original_exit", lambda pid, start: False)
    monkeypatch.setattr(identity.locks, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda root: "fixture-token")
    def health():
        if capabilities == "timeout":
            raise TimeoutError("outgoing control unavailable")
        return {
            "pid": 101, "status": "ready", "capabilities": capabilities, "runtime_prefix": str(tmp_path),
        }

    monkeypatch.setattr(smc, "ControlClient", lambda *a, **k: SimpleNamespace(health=health))
    assert identity.routed_identity(tmp_path, require_async_retirement=True) == (101, "1001", None)


def test_lock_renewal_is_fenced_by_the_native_route_owner(tmp_path, monkeypatch):
    import os
    from contextlib import contextmanager
    from agent_worktrees import runtime_monitor_identity as identity

    state = {"locked": False, "pid": os.getpid() + 1}
    writes = []

    @contextmanager
    def held(_root, **kwargs):
        state["locked"] = True
        try:
            yield
        finally:
            state["locked"] = False

    def write(path, **kwargs):
        assert state["locked"]
        writes.append(path)
        return True

    monkeypatch.setattr(identity.routing, "routing_lock", held)
    monkeypatch.setattr(identity.routing, "read_table", lambda *args, **kwargs: {
        "active": routing.Endpoint("127.0.0.1", 61001, state["pid"], "new", 2, "1001").to_dict(),
    })
    monkeypatch.setattr(identity.locks, "process_start_time", lambda _pid: "1001")
    monkeypatch.setattr(identity.locks, "write_lock", write)
    path = tmp_path / "status-monitor.lock"
    assert not identity.publish_lock(path, extra={}) and not writes
    state["pid"] = os.getpid()
    assert identity.publish_lock(path, extra={}) and writes == [path]


@pytest.mark.parametrize("phase", ["cold", "promotion"])
def test_darwin_routing_and_discovery_use_the_same_birth_token(tmp_path, monkeypatch, phase):
    import os
    from contextlib import nullcontext
    from agent_worktrees import runtime_monitor_identity as identity
    from zdd import darwin_identity, diagnostics

    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(darwin_identity, "birth_time", lambda pid: "1001:22")
    pid = os.getpid()
    token = diagnostics.process_start_time(pid)
    assert token == identity.locks.process_start_time(pid) == "1001:22"
    endpoint = routing.Endpoint("127.0.0.1", 61001, pid, "new", 1 if phase == "cold" else 2, token)
    monkeypatch.setattr(identity.routing, "routing_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(identity.routing, "read_table", lambda *a, **k: {"active": endpoint.to_dict()})
    writes = []
    monkeypatch.setattr(identity.locks, "write_lock", lambda path, **kwargs: writes.append(path) or True)
    assert identity.publish_lock(tmp_path / "status-monitor.lock", extra={})
    assert len(writes) == 1


def test_activate_after_update_cuts_over_and_converges(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_worktrees.runtime_monitor_demand.retain_transition_demand",
        lambda root, pid: nullcontext(),
    )
    route_dir = tmp_path / "routing"
    lock_path = tmp_path / "status-monitor.lock"
    route_dir.mkdir(parents=True)
    lock_path.write_text("{}", encoding="utf-8")

    old = _FakeDaemon(101, port=0)
    first_new = _FakeDaemon(202)
    second_new = _FakeDaemon(303)
    daemons = {old.pid: old, first_new.pid: first_new, second_new.pid: second_new}
    pending = [first_new, second_new]

    routing.publish_active(route_dir, bind="127.0.0.1", port=old.port, pid=old.pid, version="old")

    def _self_retire(daemon: _FakeDaemon) -> None:
        while daemon.alive:
            active = routing.read_active_endpoint(route_dir, verify_listener=False)
            if active is not None and active.pid != daemon.pid and not daemon.busy.is_set():
                daemon.force_terminate()
                return
            time.sleep(0.05)

    threading.Thread(target=_self_retire, args=(old,), daemon=True).start()
    threading.Thread(target=_self_retire, args=(first_new,), daemon=True).start()

    old.busy.set()
    release_old = threading.Event()
    release_thread = threading.Thread(
        target=lambda: (release_old.wait(10), old.busy.clear()),
        daemon=True,
    )
    release_thread.start()

    monkeypatch.setattr(smc, "routing_dir", lambda runtime_home=None: route_dir)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(smc, "load_or_create_control_token", lambda runtime_home=None: "test-token")
    def _spawn(runtime_python, *, port):
        daemon = pending.pop(0)
        daemon.start(port)
        return _Handle(daemon)

    monkeypatch.setattr(smc, "spawn_passive", _spawn)
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_runtime._status_monitor_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_runtime._restart_status_monitor",
        lambda: {"spawned": False},
    )
    # Never spawn a REAL detached subprocess during this test -- the async
    # daemon-health backstop (copilot-extensions#5453 mitigation) is
    # exercised in its own dedicated unit tests instead.
    schedule_calls: list[float] = []
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_reap_stale.schedule_delayed_daemon_health_reap",
        lambda *a, **k: schedule_calls.append(True),
    )

    first_result: dict[str, object] = {}

    def _run_first() -> None:
        first_result.update(smc.activate_after_update(runtime_python="python"))

    first_thread = threading.Thread(target=_run_first, daemon=True)
    first_thread.start()

    first_pid = _wait_for(
        lambda: (
            (ep := routing.read_active_endpoint(route_dir, verify_listener=False))
            and ep.pid != old.pid
            and ep.pid
        ),
        message="first cutover never published an active route",
    )
    assert first_pid == first_new.pid
    assert old.alive, "the old daemon exited before the new route was active"

    release_old.set()
    release_thread.join(timeout=2)
    first_thread.join(timeout=10)
    assert not first_thread.is_alive(), "first cutover never completed"
    assert first_result["action"] == "cutover"
    assert first_result["result"]["ok"] is True

    second_result = smc.activate_after_update(runtime_python="python")
    assert second_result["action"] == "cutover", second_result
    assert second_result["result"]["ok"] is True
    assert second_new.promoted is True

    final_pid = _wait_for(
        lambda: routing.read_active_endpoint(route_dir, verify_listener=False).pid,
        message="second cutover never published the newest route",
    )
    assert final_pid == second_new.pid

    _wait_for(
        lambda: (not old.alive) and (not first_new.alive) and second_new.alive,
        timeout=10,
        message="superseded generations did not retire to one live daemon",
    )

    # Each cutover attempt schedules the async daemon-health backstop
    # (copilot-extensions#5453 mitigation), regardless of this cutover's own
    # outcome -- never blocking, never a real subprocess in this test.
    assert len(schedule_calls) == 2

    for daemon in daemons.values():
        daemon.force_terminate()


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("owned_settlement", [False, True])
def test_caller_owned_guard_survives_monitor_cutover_settlement(
    tmp_path, monkeypatch, failure, owned_settlement,
):
    from types import SimpleNamespace
    from agent_worktrees import status_monitor_runtime as smr
    from agent_worktrees import status_monitor_reap_stale as reap

    route_dir = tmp_path / "status-monitor-routing"
    scheduled = []
    monkeypatch.setattr(smc, "routing_dir", lambda runtime_home=None: route_dir)
    monkeypatch.setattr(smr, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(smc, "_monitor_control_url_from_route", lambda: "http://127.0.0.1:1234")
    monkeypatch.setattr(smc.breadcrumb, "read_breadcrumb", lambda path: None)
    monkeypatch.setattr(smc.breadcrumb, "recover_stale_cutover", lambda *a, **k: {})
    monkeypatch.setattr(smc, "_reap_abandoned_passive", lambda record: {})
    monkeypatch.setattr(reap, "schedule_delayed_daemon_health_reap", lambda: scheduled.append(True))

    with SingleInstance(
        route_dir, service="status-monitor-cutover", lock_name="cutover.lock",
    ) as guard:
        def run(**kwargs):
            assert guard.held
            with pytest.raises(AlreadyRunningError):
                smc._acquire_cutover_lock(route_dir, timeout_s=0.01, poll_s=0.001)
            if failure:
                raise RuntimeError("cutover failed before settlement")
            return SimpleNamespace(to_dict=lambda: {"ok": True, "committed": True})

        monkeypatch.setattr(smc, "CutoverOrchestrator", lambda *a, **k: SimpleNamespace(run=run))
        arguments = {"spawned_handles": []} if owned_settlement else {}
        if failure:
            with pytest.raises(RuntimeError, match="before settlement"):
                smc.activate_after_update(runtime_python="candidate-python", cutover_guard=guard, **arguments)
        else:
            result = smc.activate_after_update(runtime_python="candidate-python", cutover_guard=guard, **arguments)
            assert result["result"]["committed"]
        assert guard.held, "only the owning lifecycle may release its whole-operation guard"
        with pytest.raises(AlreadyRunningError):
            smc._acquire_cutover_lock(route_dir, timeout_s=0.01, poll_s=0.001)
    assert scheduled == ([] if owned_settlement else [True])
    with SingleInstance(route_dir, lock_name="cutover.lock"):
        pass


@pytest.mark.parametrize("held,matching", [(False, True), (True, False)])
def test_foreign_or_unheld_cutover_guard_refuses_before_side_effects(
    tmp_path, monkeypatch, held, matching,
):
    from agent_worktrees import status_monitor_runtime as smr

    route_dir = tmp_path / "status-monitor-routing"
    monkeypatch.setattr(smc, "routing_dir", lambda runtime_home=None: route_dir)
    monkeypatch.setattr(smr, "_status_monitor_enabled", lambda: pytest.fail("no effects before authority"))
    guard = SingleInstance(
        route_dir if matching else tmp_path / "foreign", lock_name="cutover.lock",
    )
    try:
        if held:
            guard.acquire()
        with pytest.raises(ValueError, match="held guard"):
            smc.activate_after_update(cutover_guard=guard)
        assert guard.held is held
    finally:
        guard.release()


def test_legacy_monitor_restart_uses_the_candidate_not_the_installer_bootstrap(
    tmp_path, monkeypatch,
):
    from agent_worktrees import status_monitor_runtime as smr
    from agent_worktrees import status_monitor_reap_stale as reap

    captured = []
    monkeypatch.setattr(smc, "routing_dir", lambda runtime_home=None: tmp_path / "routing")
    monkeypatch.setattr(smr, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: False)
    monkeypatch.setattr(smr, "_restart_status_monitor", lambda **k: captured.append(k) or {"spawned": True})
    monkeypatch.setattr(reap, "schedule_delayed_daemon_health_reap", lambda: None)
    with SingleInstance(tmp_path / "routing", lock_name="cutover.lock") as guard:
        result = smc.activate_after_update(
            runtime_python="candidate-python", monitor_was_live=True, cutover_guard=guard,
        )
        assert guard.held and result["action"] == "restart"
    assert captured == [{"runtime_python": "candidate-python"}]
