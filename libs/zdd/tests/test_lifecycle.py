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
