"""Tests for zero-downtime cutover breadcrumb + rollback undrain (#1756).

Exercises the CutoverOrchestrator with injected fakes (no real subprocesses or
sockets) plus the durable breadcrumb and stale-cutover recovery helper.
"""

from __future__ import annotations

from zdd import breadcrumb
from zdd.cutover import CutoverOrchestrator
from zdd.routing import Endpoint


class FakeHandle:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def poll(self):
        return None


class FakeClient:
    """Records every orchestrator call; drain outcome is configurable."""

    def __init__(self, *, drain_result: dict | None = None) -> None:
        self.calls: list[str] = []
        self._drain_result = drain_result or {
            "drained": True, "clean": True, "forced": False, "busy_sessions": [],
        }

    def health(self) -> dict:
        self.calls.append("health")
        return {"status": "ok"}

    def drain(self, *, timeout, poll, force) -> dict:
        self.calls.append("drain")
        return self._drain_result

    def undrain(self) -> dict:
        self.calls.append("undrain")
        return {"draining": False}

    def shutdown(self) -> dict:
        self.calls.append("shutdown")
        return {"shutting_down": True}

    def adopt_relay(self) -> dict:
        self.calls.append("adopt_relay")
        return {"adopted": True}


class FakeRouting:
    """Minimal routing stand-in: no socket probing, records publishes."""

    def __init__(self, active: Endpoint | None) -> None:
        self.active = active
        self.publishes: list[dict] = []

    def read_active_endpoint(self, config_dir, *, verify_listener=True):
        return self.active

    def publish_active(self, config_dir, *, bind, port, pid=None, version=None,
                       generation=None, demote_existing=False):
        self.publishes.append({"bind": bind, "port": port})
        self.active = Endpoint(bind=bind, port=port, pid=pid, version=version)
        return self.active


def _orch(tmp_path, *, routing, client, health_ok=True):
    return CutoverOrchestrator(
        tmp_path,
        bind="127.0.0.1",
        version="9.9.9",
        spawn_passive=lambda port: FakeHandle(),
        health_check=lambda h, p: health_ok,
        make_client=lambda base_url: client,
        pick_free_port=lambda: 9391,
        sleep=lambda s: None,
        routing_mod=routing,
    )


def test_successful_cutover_clears_breadcrumb(tmp_path):
    old = Endpoint(bind="127.0.0.1", port=9281, pid=1234, version="9.9.8")
    routing = FakeRouting(old)
    client = FakeClient()
    res = _orch(tmp_path, routing=routing, client=client).run(
        health_timeout=1.0, drain_timeout=1.0,
    )
    assert res.ok is True
    assert res.committed is True
    assert "drain" in client.calls and "shutdown" in client.calls
    # Clean cutover leaves no stale breadcrumb.
    assert breadcrumb.read_breadcrumb(tmp_path) is None


def test_drain_failure_rolls_back_and_undrains(tmp_path):
    old = Endpoint(bind="127.0.0.1", port=9281, pid=1234, version="9.9.8")
    routing = FakeRouting(old)
    client = FakeClient(drain_result={
        "drained": False, "clean": False, "forced": False,
        "busy_sessions": ["busy-1"],
    })
    res = _orch(tmp_path, routing=routing, client=client).run(
        health_timeout=1.0, drain_timeout=1.0,
    )
    assert res.ok is False
    assert res.rolled_back is True
    # The survivor's drain gate is released on rollback (#1756).
    assert "undrain" in client.calls
    # A durable trace of the aborted cutover remains, marked resolved.
    record = breadcrumb.read_breadcrumb(tmp_path)
    assert record is not None
    assert record["state"] == "rolled_back"
    assert record["error"]


def test_new_daemon_unhealthy_rolls_back(tmp_path):
    old = Endpoint(bind="127.0.0.1", port=9281, pid=1234, version="9.9.8")
    routing = FakeRouting(old)
    client = FakeClient()
    # Health probe fails for the *new* daemon; old is restored via health_check
    # too, so use a health_check that fails only for the new port.
    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="9.9.9",
        spawn_passive=lambda port: FakeHandle(),
        health_check=lambda h, p: p != 9391,  # new port unhealthy
        make_client=lambda base_url: client,
        pick_free_port=lambda: 9391,
        sleep=lambda s: None,
        routing_mod=routing,
    )
    res = orch.run(health_timeout=0.2, drain_timeout=1.0, poll=0.05)
    assert res.ok is False
    assert res.rolled_back is True
    record = breadcrumb.read_breadcrumb(tmp_path)
    assert record is not None and record["state"] == "rolled_back"


def test_breadcrumb_written_before_drain(tmp_path):
    # A cutover that raises *inside* drain (client.drain raising) still leaves a
    # breadcrumb whose durable state proves the gate was opened.
    old = Endpoint(bind="127.0.0.1", port=9281, pid=1234, version="9.9.8")
    routing = FakeRouting(old)

    class ExplodingClient(FakeClient):
        def drain(self, *, timeout, poll, force):
            self.calls.append("drain")
            raise RuntimeError("orchestrator died mid-drain")

    client = ExplodingClient()
    res = _orch(tmp_path, routing=routing, client=client).run(
        health_timeout=1.0, drain_timeout=1.0,
    )
    assert res.ok is False
    record = breadcrumb.read_breadcrumb(tmp_path)
    assert record is not None
    assert record["state"] == "rolled_back"


# -- recover_stale_cutover ---------------------------------------------------


def test_recover_stale_cutover_undrains_survivor(tmp_path):
    # Simulate an aborted cutover: a non-terminal breadcrumb naming a survivor.
    breadcrumb.write_breadcrumb(
        tmp_path, state="draining",
        old={"bind": "127.0.0.1", "port": 9281}, new_port=9391,
    )
    client = FakeClient()
    summary = breadcrumb.recover_stale_cutover(
        tmp_path, lambda base_url: client, health_check=lambda h, p: True,
    )
    assert summary["recovered"] is True
    assert "undrain" in client.calls
    record = breadcrumb.read_breadcrumb(tmp_path)
    assert record["state"] == "rolled_back"


def test_recover_stale_cutover_noop_when_terminal(tmp_path):
    breadcrumb.write_breadcrumb(
        tmp_path, state="committed",
        old={"bind": "127.0.0.1", "port": 9281}, new_port=9391,
    )
    client = FakeClient()
    summary = breadcrumb.recover_stale_cutover(
        tmp_path, lambda base_url: client, health_check=lambda h, p: True,
    )
    assert summary["recovered"] is False
    assert "undrain" not in client.calls


def test_recover_stale_cutover_noop_when_absent(tmp_path):
    client = FakeClient()
    summary = breadcrumb.recover_stale_cutover(
        tmp_path, lambda base_url: client,
    )
    assert summary["recovered"] is False
    assert client.calls == []


def test_recover_survivor_unreachable_retires_breadcrumb(tmp_path):
    breadcrumb.write_breadcrumb(
        tmp_path, state="draining",
        old={"bind": "127.0.0.1", "port": 9281}, new_port=9391,
    )
    client = FakeClient()
    summary = breadcrumb.recover_stale_cutover(
        tmp_path, lambda base_url: client, health_check=lambda h, p: False,
    )
    assert summary["recovered"] is False
    assert "undrain" not in client.calls
    # Breadcrumb retired so we do not keep retrying a dead endpoint.
    assert breadcrumb.read_breadcrumb(tmp_path)["state"] == "rolled_back"
