"""Tests for the active/passive cutover orchestrator (deploy.py)."""

from __future__ import annotations

from pathlib import Path

from zdd import routing
from zdd.cutover import CutoverOrchestrator


class FakeHandle:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def poll(self) -> int | None:
        return None


class FakeClient:
    """Records calls; configurable drain outcome."""

    def __init__(self, base_url: str, registry: dict) -> None:
        self.base_url = base_url
        self.registry = registry
        registry[base_url] = self
        self.calls: list[str] = []
        self.drain_result = {"drained": True, "clean": True, "forced": False,
                             "busy_sessions": []}

    def health(self):
        self.calls.append("health")
        return {"status": "ok"}

    def drain(self, *, timeout, poll, force):
        self.calls.append(f"drain(force={force})")
        return self.drain_result

    def undrain(self):
        self.calls.append("undrain")
        return {"draining": False}

    def shutdown(self):
        self.calls.append("shutdown")
        return {"shutting_down": True}

    def adopt_relay(self):
        self.calls.append("adopt_relay")
        return {"adopted": True}


def _make(cfg_dir: Path, *, healthy_ports, registry, spawned=None, port=9290,
          drain_result=None):
    """Build an orchestrator with controllable collaborators."""
    handle = FakeHandle()
    if spawned is None:
        spawned = []

    def spawn_passive(p):
        spawned.append(p)
        # the new daemon "becomes healthy" only if its port is in healthy_ports
        return handle

    def health_check(host, p):
        return p in healthy_ports

    def make_client(base_url):
        c = registry.get(base_url) or FakeClient(base_url, registry)
        if drain_result is not None and base_url.endswith(":9281"):
            c.drain_result = drain_result
        return c

    orch = CutoverOrchestrator(
        cfg_dir, bind="127.0.0.1", version="1.0.0",
        spawn_passive=spawn_passive,
        health_check=health_check,
        make_client=make_client,
        pick_free_port=lambda: port,
        sleep=lambda _s: None,
        clock=_fake_clock(),
    )
    return orch, handle


def _fake_clock():
    t = {"v": 0.0}

    def clock():
        t["v"] += 0.01
        return t["v"]

    return clock


# -- happy path: old daemon present, clean drain -----------------------------


def test_cutover_happy_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    # An old active daemon exists on 9281.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111,
                           version="0.9")
    registry: dict = {}
    orch, handle = _make(
        tmp_path, healthy_ports={9290, 9281}, registry=registry,
        port=9290,
    )
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert res.ok is True
    assert res.committed is True
    assert res.rolled_back is False
    assert res.new_port == 9290
    assert res.old_endpoint.port == 9281
    # Old daemon was drained then shut down; new adopted the relay.
    old_client = registry["http://127.0.0.1:9281"]
    assert "drain(force=False)" in old_client.calls
    assert "shutdown" in old_client.calls
    new_client = registry["http://127.0.0.1:9290"]
    assert "adopt_relay" in new_client.calls
    # Routing table now points at the new port.
    assert routing.read_table(tmp_path)["active"]["port"] == 9290
    assert routing.read_table(tmp_path)["previous"]["port"] == 9281


# -- new daemon never becomes healthy -> rollback ----------------------------


def test_rollback_when_new_unhealthy(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    registry: dict = {}
    # 9290 is NOT in healthy_ports -> health probe never passes.
    orch, handle = _make(
        tmp_path, healthy_ports={9281}, registry=registry,
        port=9290,
    )
    res = orch.run(health_timeout=0.1, drain_timeout=1, poll=0.01)
    assert res.ok is False
    assert res.rolled_back is True
    assert handle.terminated is True
    # Route never moved off the old daemon.
    assert routing.read_table(tmp_path)["active"]["port"] == 9281
    # We never drained the old daemon (only health/undrain during rollback).
    old_client = registry.get("http://127.0.0.1:9281")
    if old_client is not None:
        assert not any(c.startswith("drain(") for c in old_client.calls)


# -- drain times out, no force -> rollback (route restored) ------------------


def test_rollback_when_drain_incomplete(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    registry: dict = {}
    incomplete = {"drained": False, "clean": False, "forced": False,
                  "busy_sessions": ["s1"]}
    orch, handle = _make(
        tmp_path, healthy_ports={9290, 9281}, registry=registry,
        port=9290, drain_result=incomplete,
    )
    res = orch.run(health_timeout=1, drain_timeout=0.1)
    assert res.ok is False
    assert res.committed is False
    assert res.rolled_back is True
    assert handle.terminated is True
    old_client = registry["http://127.0.0.1:9281"]
    assert "shutdown" not in old_client.calls  # never retired the old daemon
    assert "undrain" in old_client.calls        # drain gate released on rollback
    # Route restored to the old daemon.
    assert routing.read_table(tmp_path)["active"]["port"] == 9281


# -- drain times out WITH force -> proceeds ----------------------------------


def test_force_proceeds_through_incomplete_drain(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    registry: dict = {}
    forced = {"drained": True, "clean": False, "forced": True,
              "busy_sessions": ["s1"]}
    orch, handle = _make(
        tmp_path, healthy_ports={9290, 9281}, registry=registry,
        port=9290, drain_result=forced,
    )
    res = orch.run(health_timeout=1, drain_timeout=0.1, force=True)
    assert res.ok is True
    assert res.committed is True
    assert "shutdown" in registry["http://127.0.0.1:9281"].calls
    assert routing.read_table(tmp_path)["active"]["port"] == 9290


# -- cold start: no prior active daemon --------------------------------------


def test_cold_start_no_old_daemon(tmp_path: Path):
    registry: dict = {}
    orch, handle = _make(
        tmp_path, healthy_ports={9290}, registry=registry,
        port=9290,
    )
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert res.ok is True
    assert res.committed is True
    assert res.old_endpoint is None
    assert routing.read_table(tmp_path)["active"]["port"] == 9290


# -- drain RAISES after the flip, old daemon now dead -> commit forward -------


def test_commit_forward_when_old_dies_during_drain(tmp_path: Path, monkeypatch):
    """If the old daemon becomes unreachable after the route flips, rollback must
    NOT kill the healthy new daemon (that would strand all clients). It commits
    forward to the new daemon instead."""
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    registry: dict = {}

    # old (9281) is healthy at spawn/flip time but DEAD by rollback time.
    state = {"old_alive": True}

    def health_check(host, p):
        if p == 9281:
            return state["old_alive"]
        return p == 9290

    class RaisingClient(FakeClient):
        def drain(self, *, timeout, poll, force):
            self.calls.append("drain")
            state["old_alive"] = False  # old crashes mid-drain
            raise ConnectionError("old daemon gone")

    def make_client(base_url):
        if base_url.endswith(":9281"):
            return registry.get(base_url) or RaisingClient(base_url, registry)
        return registry.get(base_url) or FakeClient(base_url, registry)

    handle = FakeHandle()
    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="1.0.0",
        spawn_passive=lambda p: handle,
        health_check=health_check, make_client=make_client,
        pick_free_port=lambda: 9290, sleep=lambda _s: None, clock=_fake_clock(),
    )
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert res.ok is True
    assert res.committed is True
    assert res.rolled_back is False
    assert handle.terminated is False  # the healthy new daemon was NOT killed
    # Route stays on the new daemon.
    assert routing.read_table(tmp_path)["active"]["port"] == 9290


# -- drain RAISES but old still alive -> rollback restores old ---------------


def test_rollback_to_old_when_old_alive_and_drain_raises(tmp_path: Path,
                                                         monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=111)
    registry: dict = {}

    class RaisingClient(FakeClient):
        def drain(self, *, timeout, poll, force):
            self.calls.append("drain")
            raise ConnectionError("transient blip")

    def health_check(host, p):
        return p in (9281, 9290)  # old stays alive

    def make_client(base_url):
        if base_url.endswith(":9281"):
            return registry.get(base_url) or RaisingClient(base_url, registry)
        return registry.get(base_url) or FakeClient(base_url, registry)

    handle = FakeHandle()
    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="1.0.0",
        spawn_passive=lambda p: handle,
        health_check=health_check, make_client=make_client,
        pick_free_port=lambda: 9290, sleep=lambda _s: None, clock=_fake_clock(),
    )
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert res.ok is False
    assert res.rolled_back is True
    assert handle.terminated is True  # new daemon torn down
    # Route restored to the old (still-alive) daemon.
    assert routing.read_table(tmp_path)["active"]["port"] == 9281

