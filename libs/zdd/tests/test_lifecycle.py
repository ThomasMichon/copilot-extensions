"""Tests for the durable lifecycle event log and its cutover instrumentation."""

from __future__ import annotations

import json
from pathlib import Path

from zdd import lifecycle, routing
from zdd.cutover import CutoverOrchestrator


# -- the log module ----------------------------------------------------------


def test_record_and_read_roundtrip(tmp_path: Path):
    rec = lifecycle.record(
        tmp_path, lifecycle.CUTOVER_FLIP, service="agent-bridge",
        outcome=lifecycle.OK, version="0.4.0-dev316", port=50000,
        node="host-1", detail={"old_port": 49000},
    )
    assert rec is not None
    assert rec["service"] == "agent-bridge"
    assert rec["action"] == "cutover-flip"
    assert rec["outcome"] == "ok"
    assert rec["port"] == 50000
    assert rec["version"] == "0.4.0-dev316"
    assert rec["node"] == "host-1"
    assert rec["detail"] == {"old_port": 49000}
    assert "ts" in rec and "pid" in rec

    events = lifecycle.read_events(tmp_path)
    assert len(events) == 1
    assert events[0] == rec
    # It really is JSON-Lines on disk.
    line = lifecycle.log_path(tmp_path).read_text(encoding="utf-8").strip()
    assert json.loads(line)["action"] == "cutover-flip"


def test_append_is_additive(tmp_path: Path):
    lifecycle.record(tmp_path, lifecycle.START, service="svc")
    lifecycle.record(tmp_path, lifecycle.STOP, service="svc")
    lifecycle.record(tmp_path, lifecycle.START, service="svc")
    events = lifecycle.read_events(tmp_path)
    assert [e["action"] for e in events] == ["start", "stop", "start"]
    assert lifecycle.read_events(tmp_path, limit=1)[0]["action"] == "start"
    assert len(lifecycle.read_events(tmp_path, limit=2)) == 2
    # limit=0 means "none", not "all" (guard against events[-0:] == all).
    assert lifecycle.read_events(tmp_path, limit=0) == []


def test_service_inferred_from_config_dir(tmp_path: Path):
    cfg = tmp_path / ".agent-dispatch"
    cfg.mkdir()
    assert lifecycle.service_from_config_dir(cfg) == "agent-dispatch"
    # service omitted (defaults to None) -> inferred from the config-dir name.
    rec = lifecycle.record(cfg, lifecycle.UPDATE)
    assert rec is not None and rec["service"] == "agent-dispatch"


def test_empty_detail_is_preserved(tmp_path: Path):
    # An explicitly-passed empty dict is kept; None means "not provided".
    rec = lifecycle.record(tmp_path, lifecycle.START, service="svc", detail={})
    assert rec is not None and rec.get("detail") == {}
    rec2 = lifecycle.record(tmp_path, lifecycle.STOP, service="svc")
    assert "detail" not in rec2


def test_record_is_fail_open(tmp_path: Path):
    # config_dir whose parent is a file -> mkdir(parents=True) fails; must not raise.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    assert lifecycle.record(blocker / "under-a-file", lifecycle.START,
                            service="svc") is None


def test_missing_log_reads_empty(tmp_path: Path):
    assert lifecycle.read_events(tmp_path) == []


def test_malformed_lines_skipped(tmp_path: Path):
    p = lifecycle.log_path(tmp_path)
    p.write_text('{"action":"start"}\nnot json\n{"action":"stop"}\n', encoding="utf-8")
    assert [e["action"] for e in lifecycle.read_events(tmp_path)] == ["start", "stop"]


def test_rotation_when_oversized(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(lifecycle, "_MAX_BYTES", 200)
    for _ in range(50):
        lifecycle.record(tmp_path, lifecycle.START, service="svc",
                         detail={"pad": "x" * 40})
    rotated = lifecycle.log_path(tmp_path).with_name(lifecycle._LOG_FILENAME + ".1")
    assert rotated.exists()


def test_read_events_spans_rotation(tmp_path: Path):
    # read_events must read the retained older generation (.log.1) together with
    # the current log, so recent history is not lost -- and a limit slice is not
    # short-changed -- immediately after a rotation.
    for i in range(3):
        lifecycle.record(tmp_path, lifecycle.START, service="svc", detail={"i": i})
    # Simulate a rotation: the current log becomes the retained generation.
    log = lifecycle.log_path(tmp_path)
    log.replace(log.with_name(log.name + ".1"))
    for i in range(3, 5):
        lifecycle.record(tmp_path, lifecycle.START, service="svc", detail={"i": i})
    events = lifecycle.read_events(tmp_path)
    # Older generation first, then current: 0..4 in order.
    assert [e["detail"]["i"] for e in events] == [0, 1, 2, 3, 4]
    # A limit slice spans both files (last 4 across the rotation boundary).
    assert [e["detail"]["i"] for e in lifecycle.read_events(tmp_path, limit=4)] == \
        [1, 2, 3, 4]


def test_unknown_action_still_recorded(tmp_path: Path):
    rec = lifecycle.record(tmp_path, "some-future-action", service="svc",
                           outcome="ok")
    assert rec is not None and rec["action"] == "some-future-action"


def test_cli_record_and_show(tmp_path: Path, capsys):
    rc = lifecycle.main([
        "record", "--config-dir", str(tmp_path), "--service", "agent-bridge",
        "--action", "update", "--outcome", "ok", "--version", "0.4.0-dev316",
        "--port", "50000", "--detail", '{"note":"cli"}',
    ])
    assert rc == 0
    events = lifecycle.read_events(tmp_path)
    assert len(events) == 1
    assert events[0]["service"] == "agent-bridge"
    assert events[0]["detail"] == {"note": "cli"}

    rc = lifecycle.main(["show", "--config-dir", str(tmp_path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert json.loads(out)["action"] == "update"


# -- cutover instrumentation -------------------------------------------------


class _Handle:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def poll(self):
        return None


class _Client:
    def __init__(self, base_url, registry, drain_result=None):
        self.base_url = base_url
        registry[base_url] = self
        self.calls: list[str] = []
        self.drain_result = drain_result or {
            "drained": True, "clean": True, "forced": False, "busy_sessions": [],
        }

    def health(self):
        return {"status": "ok"}

    def drain(self, *, timeout, poll, force):
        self.calls.append("drain")
        return self.drain_result

    def undrain(self):
        self.calls.append("undrain")
        return {"draining": False}

    def shutdown(self):
        self.calls.append("shutdown")
        return {"shutting_down": True}

    def adopt_relay(self):
        return {"adopted": True}


def _clock():
    t = {"v": 0.0}

    def c():
        t["v"] += 0.01
        return t["v"]

    return c


def _orch(cfg, *, healthy_ports, registry, new_port=9290):
    handle = _Handle()
    return (
        CutoverOrchestrator(
            cfg, bind="127.0.0.1", version="1.0.0",
            spawn_passive=lambda p: handle,
            health_check=lambda host, p: p in healthy_ports,
            make_client=lambda url: registry.get(url) or _Client(url, registry),
            pick_free_port=lambda: new_port,
            sleep=lambda _s: None, clock=_clock(),
        ),
        handle,
    )


def _actions(cfg):
    return [(e["action"], e["outcome"]) for e in lifecycle.read_events(cfg)]


def test_cutover_happy_path_emits_full_trail(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111,
                           version="0.9")
    registry: dict = {}
    orch, _ = _orch(tmp_path, healthy_ports={9290, 9281}, registry=registry)
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert res.ok is True
    trail = _actions(tmp_path)
    assert ("cutover-begin", "begin") in trail
    assert ("cutover-new-bound", "ok") in trail
    assert ("cutover-flip", "ok") in trail
    assert ("drain", "ok") in trail
    assert ("cutover-verify", "ok") in trail
    assert ("cutover-retire", "ok") in trail
    # The retire record logs the OLD daemon's port (the one retired), matching
    # `drain`; the new port rides in detail.
    retire = next(e for e in lifecycle.read_events(tmp_path)
                  if e["action"] == "cutover-retire")
    assert retire["port"] == 9281
    assert retire["detail"]["new_port"] == 9290
    drain = next(e for e in lifecycle.read_events(tmp_path)
                 if e["action"] == "drain" and e["outcome"] == "ok")
    assert drain["port"] == 9281


def test_cutover_new_never_bound_is_recorded(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    registry: dict = {}
    # 9290 never becomes healthy -> the incident's "new daemon never bound".
    # The old daemon (9281) is still healthy, so rollback recovers service.
    orch, _ = _orch(tmp_path, healthy_ports={9281}, registry=registry)
    res = orch.run(health_timeout=0.1, drain_timeout=1, poll=0.01)
    assert res.ok is False
    trail = _actions(tmp_path)
    assert ("cutover-new-bound", "fail") in trail
    # Service recovered to the still-healthy old daemon -> rollback is ok.
    assert ("rollback", "ok") in trail
    # never flipped or retired
    assert ("cutover-flip", "ok") not in trail
    assert not any(a == "cutover-retire" for a, _ in trail)


def test_rollback_records_fail_when_nothing_serves(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    registry: dict = {}
    # Neither the new (9290) nor the old (9281) daemon is healthy: the new never
    # binds and the old is already gone -- the true incident shape, where a
    # rollback leaves nothing serving and must record an alertable fail.
    orch, _ = _orch(tmp_path, healthy_ports=set(), registry=registry)
    res = orch.run(health_timeout=0.1, drain_timeout=1, poll=0.01)
    assert res.ok is False
    trail = _actions(tmp_path)
    assert ("cutover-new-bound", "fail") in trail
    assert ("rollback", "fail") in trail


def test_cold_start_records_retire(tmp_path: Path):
    registry: dict = {}
    orch, _ = _orch(tmp_path, healthy_ports={9290}, registry=registry)
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert res.ok is True
    trail = _actions(tmp_path)
    assert ("cutover-begin", "begin") in trail
    assert ("cutover-new-bound", "ok") in trail
    assert ("cutover-retire", "ok") in trail


def test_verify_before_retire_gate_rolls_back(tmp_path: Path, monkeypatch):
    # New daemon is healthy at bind/flip but dies before the verify-before-retire
    # gate (e.g. it crashes during the old daemon's drain). The gate must refuse
    # to retire the old daemon and roll back to keep it serving (#5322).
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    registry: dict = {}
    state = {"new_alive": True}

    def health_check(host, p):
        if p == 9290:
            return state["new_alive"]
        return p == 9281  # old stays alive

    class DrainKillsNew(_Client):
        def drain(self, *, timeout, poll, force):
            self.calls.append("drain")
            state["new_alive"] = False  # new dies during the drain window
            return self.drain_result

    def make_client(url):
        if url.endswith(":9281"):
            return registry.get(url) or DrainKillsNew(url, registry)
        return registry.get(url) or _Client(url, registry)

    handle = _Handle()
    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="1.0.0",
        spawn_passive=lambda p: handle, health_check=health_check,
        make_client=make_client, pick_free_port=lambda: 9290,
        sleep=lambda _s: None, clock=_clock(),
    )
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert res.ok is False
    assert res.committed is False
    old_client = registry["http://127.0.0.1:9281"]
    assert "shutdown" not in old_client.calls   # old daemon was NOT retired
    assert "undrain" in old_client.calls          # its drain gate was released
    assert routing.read_table(tmp_path)["active"]["port"] == 9281  # old restored
    trail = _actions(tmp_path)
    assert ("cutover-verify", "fail") in trail
    assert not any(a == "cutover-retire" for a, _ in trail)
    assert ("rollback", "ok") in trail            # service recovered to old


# -- dead-port watchdog ------------------------------------------------------


def test_watchdog_reaps_dead_active_promotes_live_previous(tmp_path: Path):
    # active=9290 (dead), previous=9281 (live) -> promote previous, log a reap.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9290, pid=222,
                           demote_existing=True)
    diag = routing.reap_stale_active(
        tmp_path, service="agent-bridge",
        listening=lambda h, p: p == 9281,        # only the old port serves
        pid_alive=lambda pid: pid != 222,        # the dead active's pid is gone
    )
    assert diag["reaped"] is True
    assert diag["dead_port"] == 9290
    assert diag["promoted_port"] == 9281
    assert routing.read_table(tmp_path)["active"]["port"] == 9281
    trail = _actions(tmp_path)
    assert ("watchdog-reap", "ok") in trail


def test_watchdog_clears_table_when_no_live_previous(tmp_path: Path):
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9290, pid=222)
    diag = routing.reap_stale_active(
        tmp_path, listening=lambda h, p: False, pid_alive=lambda pid: False,
    )
    assert diag["reaped"] is True and diag["promoted_port"] is None
    # Table cleared: no active endpoint -> readers fall back to config.
    assert routing.read_active_endpoint(tmp_path) is None
    assert ("watchdog-reap", "ok") in _actions(tmp_path)


def test_watchdog_leaves_live_active(tmp_path: Path):
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9290, pid=222)
    diag = routing.reap_stale_active(
        tmp_path, listening=lambda h, p: True, pid_alive=lambda pid: True,
    )
    assert diag["reaped"] is False
    assert _actions(tmp_path) == []   # nothing logged when nothing reaped


def test_watchdog_leaves_starting_active(tmp_path: Path):
    # No listener yet, but the pid is alive (mid-startup) -> do not reap.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9290, pid=222)
    diag = routing.reap_stale_active(
        tmp_path, listening=lambda h, p: False, pid_alive=lambda pid: True,
    )
    assert diag["reaped"] is False
    assert "starting" in diag["reason"]


def test_cutover_watchdog_uses_injected_routing(tmp_path: Path):
    # The startup dead-port sweep must go through the injected routing_mod, not
    # the module global, so an orchestrator with a custom routing implementation
    # sweeps the same table it reads/publishes to.
    calls: list = []

    class SpyRouting:
        def read_active_endpoint(self, *a, **k):
            return routing.read_active_endpoint(*a, **k)

        def publish_active(self, *a, **k):
            return routing.publish_active(*a, **k)

        def reap_stale_active(self, config_dir, **k):
            calls.append(config_dir)
            return routing.reap_stale_active(config_dir, **k)

    handle = _Handle()
    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="1.0.0",
        spawn_passive=lambda p: handle,
        health_check=lambda host, p: p == 9290,
        make_client=lambda url: _Client(url, {}),
        pick_free_port=lambda: 9290, sleep=lambda _s: None, clock=_clock(),
        routing_mod=SpyRouting(),
    )
    orch.run(health_timeout=1, drain_timeout=1)
    assert calls == [tmp_path]   # reaped via the injected routing_mod


def test_watchdog_leaves_active_with_unknown_pid(tmp_path: Path):
    # No listener, but the endpoint was published without a pid: we cannot prove
    # it is dead, so the watchdog must NOT reap it (conservative).
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9290, pid=None)
    diag = routing.reap_stale_active(
        tmp_path, listening=lambda h, p: False, pid_alive=lambda pid: False,
    )
    assert diag["reaped"] is False
    assert "unconfirmed" in diag["reason"]
    assert _actions(tmp_path) == []
