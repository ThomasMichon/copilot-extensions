"""Push delivery contracts: barriers, bounded wakes, poll recovery and ownership."""

from __future__ import annotations

import asyncio
import os
import queue
import threading
import time

import pytest
from fastapi import Request

from agent_dispatch.assignment_intake import AssignmentIntake, assignment_event_matches
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator_loops import DrainGate
from agent_dispatch.federation_runner import FederationRunner
from agent_dispatch.satellite_work_intake import SatelliteWorkIntake
from agent_dispatch.satellites import FleetDirectory
from tests import test_coordinator as coordinator_fixtures
from tests._helpers import OTHER_REPO, TEST_REPO

app = coordinator_fixtures.app
server_url = coordinator_fixtures.server_url


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "assignment contract deadline exceeded"


def task_event(kind="task.created", *, machine="host-a", repo=TEST_REPO):
    return {"type": kind, "task": {"target_machine": machine, "repo": repo}}


@pytest.mark.parametrize(
    "event,expected",
    [
        (task_event(), True),
        (task_event(machine="HOST-A"), True),
        (task_event("task.completed"), True),
        (task_event("task.claimed"), True),
        (task_event("task.heartbeat"), False),
        (task_event("task.activity_updated"), False),
        (task_event(machine="host-b"), False),
        (task_event(repo=OTHER_REPO), False),
        (task_event(machine=None), False),
        ({"type": "task.reconciled"}, True),
        ({"type": "task.reaped"}, True),
        ({"type": "task.steer_taken"}, False),
        ({"type": "ready"}, False),
        ({"type": "spawn.spawned"}, False),
    ],
)
def test_assignment_affinity_and_capacity_wakes(event, expected):
    assert assignment_event_matches(event, "host-a", TEST_REPO) is expected


class ScriptedEvents:
    def __init__(self):
        self.frames = queue.Queue()
        self.allow_ready = threading.Event()
        self.allow_ready.set()
        self.connections = 0
        self.closed = 0

    async def async_stream_events(self):
        self.connections += 1
        try:
            while not self.allow_ready.is_set():
                await asyncio.sleep(0.005)
            yield {"type": "ready"}
            while True:
                try:
                    item = self.frames.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.005)
                    continue
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self.closed += 1


class CountingIntake:
    def __init__(self):
        self.count = 0
        self.block = threading.Event()
        self.block.set()
        self.entered = threading.Event()

    def tick(self):
        self.count += 1
        self.entered.set()
        assert self.block.wait(3)
        return {"spawned": []}


def build_loop(client, intake, **kwargs):
    return AssignmentIntake(
        client, intake, machine="host-a", repo=TEST_REPO,
        enabled=lambda: True, min_reconcile_seconds=0.02, **kwargs,
    )


@pytest.mark.parametrize("role", ["satellite", "peer", "coordinator"])
def test_assignment_runner_passive_promotion_and_demotion(monkeypatch, tmp_path, role):
    from zdd import routing

    from agent_dispatch import config, server

    monkeypatch.setattr(server, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_wake_route_owned", False)
    monkeypatch.setattr(config, "satellite_gate_open", lambda: True)
    monkeypatch.setattr(config, "shared_url", lambda: None)
    directory = FleetDirectory()
    gate = DrainGate()
    runner = FederationRunner(
        directory, "host-a", role=role,
        is_active=server._owns_active_route, drain_gate=gate,
    )
    monkeypatch.setattr(runner, "_satellite_kwargs", lambda: {})
    runner.tick()
    assert directory.list() == []
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9999, pid=os.getpid())
    runner.tick()
    assert len(directory.list()) == 1
    gate.set_draining(True)
    monkeypatch.setattr(runner, "_tick", lambda: pytest.fail("draining generation admitted"))
    runner.tick()
    gate.set_draining(False)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9998, pid=os.getpid() + 1)
    runner.tick()
    runner.stop()
    assert len(directory.list()) == 1  # Do not withdraw the successor's stable identity.
    assert gate.claims == 0


def test_assignment_launch_keeps_http_drain_busy(monkeypatch, app, server_url):
    from agent_dispatch import config, satellite_work_intake

    monkeypatch.setattr(config, "satellite_gate_open", lambda: True)
    monkeypatch.setattr(config, "shared_url", lambda: server_url)
    monkeypatch.setattr(config, "shared_token", lambda: None)
    intake = CountingIntake()
    intake.block.clear()
    monkeypatch.setattr(satellite_work_intake, "SatelliteWorkIntake", lambda *a, **kw: intake)
    gate = app.state.drain_gate
    runner = FederationRunner(
        FleetDirectory(), "host-a", role="satellite", drain_gate=gate,
    )
    monkeypatch.setattr(runner, "_satellite_kwargs", lambda: {})
    runner.start(interval=0.02)
    with DispatchClient(server_url) as client:
        try:
            assert intake.entered.wait(1)
            result = client.drain(timeout=0.02, poll=0.05, force=False)
            assert result["drained"] is False
            assert result["busy_claims"] >= 1
            count = intake.count
            intake.block.set()
            result = client.drain(timeout=2, poll=0.05, force=False)
            assert result["clean"] is True
            time.sleep(0.1)
            assert intake.count == count
            assert runner._reconcile_work_intake() == {}
            assert intake.count == count
        finally:
            intake.block.set()
            runner.stop(timeout=2)
            client.undrain()


def test_assignment_admission_rechecks_drain_inside_tracked_boundary(app, monkeypatch):
    from fastapi.testclient import TestClient

    class RacingGate(DrainGate):
        def track_claim(self):
            self.set_draining(True)
            return super().track_claim()

    runner = FederationRunner(FleetDirectory(), "host-a", drain_gate=RacingGate())
    runner._work_intake = CountingIntake()
    assert runner._reconcile_work_intake() == {}
    assert runner._work_intake.count == 0
    app.state.drain_gate = RacingGate()
    monkeypatch.setattr(
        app.state.queue, "claim_outcome",
        lambda *a, **kw: pytest.fail("claim admitted after draining"),
    )
    with TestClient(app) as client:
        response = client.post("/claim", json={"worker_id": "host-a:worker", "repo": TEST_REPO})
        assert response.status_code == 200
        assert response.json() is None


def test_assignment_credential_command_runs_only_on_owner_tick(
    monkeypatch, server_url, tmp_path,
):
    from zdd import routing

    from agent_dispatch import client as client_module
    from agent_dispatch import config, satellite_work_intake, server

    monkeypatch.setattr(config, "satellite_gate_open", lambda: True)
    monkeypatch.setattr(config, "shared_url", lambda: server_url)
    monkeypatch.delenv("AGENT_DISPATCH_SHARED_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN_COMMAND", "fake-credential-fetch")
    owner = threading.get_ident()
    calls = []
    credential = ["first"]

    def fetch(command):
        calls.append(threading.get_ident())
        return credential[0]

    monkeypatch.setattr(config, "run_token_command", fetch)
    events = ScriptedEvents()
    clients = []

    def make_client(*args, **kwargs):
        result = DispatchClient(*args, **kwargs)
        result.async_stream_events = events.async_stream_events
        clients.append(result)
        return result

    monkeypatch.setattr(client_module, "DispatchClient", make_client)
    monkeypatch.setattr(
        satellite_work_intake, "SatelliteWorkIntake", lambda *a, **kw: CountingIntake(),
    )
    monkeypatch.setattr(server, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_wake_route_owned", False)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9999, pid=os.getpid())
    route_reads = []
    read_table = routing.read_table

    def read_route(path, **kwargs):
        route_reads.append(threading.current_thread().name)
        return read_table(path, **kwargs)

    monkeypatch.setattr(routing, "read_table", read_route)
    runner = FederationRunner(
        FleetDirectory(), "host-a", role="satellite", is_active=server._owns_active_route,
    )
    monkeypatch.setattr(runner, "_satellite_kwargs", lambda: {})
    runner._running = True
    try:
        runner.tick()
        wait_for(lambda: runner.assignment_status()["state"] == "connected")
        for _ in range(10_000):
            events.frames.put(task_event())
        wait_for(events.frames.empty)
        assert calls == [owner]
        assert 1 <= len(route_reads) < 20
        assert not any("_read" in name for name in route_reads)
        old = runner._assignment_intake
        credential[0] = "rotated"
        runner.tick()
        wait_for(lambda: not (old._worker.is_alive() or old._reader.is_alive()))
        runner.tick()
        wait_for(lambda: runner.assignment_status()["state"] == "connected")
        assert clients[0]._http.is_closed
        assert runner._work_intake_token == "rotated"
        assert calls == [owner, owner, owner]
        routing.publish_active(
            tmp_path, bind="127.0.0.1", port=9998, pid=os.getpid() + 1,
        )
        before = runner._work_intake.count
        assert runner._reconcile_work_intake() == {}
        assert runner._work_intake.count == before
        assert runner._cached_admitting() is False
    finally:
        runner.stop(timeout=2)


def settled_count(loop, intake):
    wait_for(lambda: loop.status()["state"] == "connected" and intake.count >= 1)
    time.sleep(0.06)
    return intake.count


def test_ready_barrier_reconciles_work_created_after_initial_read():
    client, intake = ScriptedEvents(), CountingIntake()
    client.allow_ready.clear()
    loop = build_loop(client, intake)
    loop.start()
    try:
        wait_for(lambda: intake.count == 1)
        time.sleep(0.04)
        assert intake.count == 1
        client.allow_ready.set()
        wait_for(lambda: intake.count == 2)
        assert loop.status()["state"] == "connected"
    finally:
        assert loop.stop()
    assert client.closed == 1


def test_assignment_reconnect_reconciles_gap_without_an_event():
    client, intake = ScriptedEvents(), CountingIntake()
    loop = build_loop(client, intake)
    loop.start()
    try:
        settled_count(loop, intake)
        client.frames.put(ConnectionError("synthetic disconnect"))
        wait_for(lambda: loop.status()["state"] == "degraded")
        count = intake.count
        wait_for(lambda: client.connections == 2)
        wait_for(lambda: intake.count > count)
        assert loop.status()["state"] == "connected"
    finally:
        assert loop.stop()
    assert client.closed == 2


def test_assignment_burst_coalesces_and_keeps_trailing_dirty_read():
    client, intake = ScriptedEvents(), CountingIntake()
    loop = build_loop(client, intake)
    loop.start()
    try:
        baseline = settled_count(loop, intake)
        intake.block.clear()
        client.frames.put(task_event())
        wait_for(lambda: intake.count == baseline + 1)
        for _ in range(10000):
            client.frames.put(task_event())
        wait_for(lambda: client.frames.empty())
        intake.block.set()
        wait_for(lambda: intake.count == baseline + 2)
        time.sleep(0.06)
        assert intake.count == baseline + 2
    finally:
        intake.block.set()
        assert loop.stop()


def test_assignment_other_affinity_does_not_reconcile():
    client, intake = ScriptedEvents(), CountingIntake()
    loop = build_loop(client, intake)
    loop.start()
    try:
        baseline = settled_count(loop, intake)
        client.frames.put(task_event(machine="host-b"))
        client.frames.put(task_event(repo=OTHER_REPO))
        client.frames.put(task_event("task.heartbeat"))
        wait_for(lambda: client.frames.empty())
        time.sleep(0.05)
        assert intake.count == baseline
    finally:
        assert loop.stop()


def test_assignment_poll_recovers_silent_missed_event():
    client, intake = ScriptedEvents(), CountingIntake()
    loop = build_loop(client, intake, poll_seconds=0.15)
    loop.start()
    try:
        baseline = settled_count(loop, intake)
        wait_for(lambda: intake.count > baseline)
        assert loop.status()["state"] == "connected"
    finally:
        assert loop.stop()


def test_assignment_missing_ready_degrades_and_poll_continues():
    client, intake = ScriptedEvents(), CountingIntake()
    client.allow_ready.clear()
    loop = build_loop(client, intake, poll_seconds=0.15, ready_timeout=0.05)
    loop.start()
    try:
        wait_for(lambda: loop.status()["state"] == "degraded")
        assert loop.status()["error"] == "TimeoutError"
        wait_for(lambda: intake.count >= 2)
    finally:
        assert loop.stop()
    assert client.closed == 1


def test_assignment_ready_then_eof_does_not_reset_reconnect_backoff(monkeypatch):
    class EndingEvents:
        async def async_stream_events(self):
            yield {"type": "ready"}

    delays = []

    async def record_delay(seconds):
        delays.append(seconds)
        if len(delays) == 8:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", record_delay)
    loop = build_loop(EndingEvents(), CountingIntake())
    asyncio.run(loop._subscribe())
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]
    assert loop.stop()


def test_assignment_partial_start_failure_retires_started_thread(monkeypatch):
    loop = build_loop(ScriptedEvents(), CountingIntake())

    def fail_start():
        raise RuntimeError("synthetic thread admission failure")

    monkeypatch.setattr(loop._reader, "start", fail_start)
    with pytest.raises(RuntimeError, match="thread admission"):
        loop.start()
    assert loop.stop()
    assert not loop._worker.is_alive()
    assert loop.status()["state"] == "stopped"


def test_assignment_ready_does_not_require_python311_asyncio_timeout(monkeypatch):
    monkeypatch.delattr(asyncio, "timeout", raising=False)
    loop = build_loop(ScriptedEvents(), CountingIntake())
    loop.start()
    try:
        wait_for(lambda: loop.status()["state"] == "connected")
    finally:
        assert loop.stop()


def test_assignment_stop_retains_inflight_intake_and_cancels_quiet_reader():
    client, intake = ScriptedEvents(), CountingIntake()
    intake.block.clear()
    loop = build_loop(client, intake)
    loop.start()
    try:
        assert intake.entered.wait(1)
        wait_for(lambda: loop.status()["state"] == "connected")
        assert not loop.stop(timeout=0.05)
        wait_for(lambda: client.closed == 1)
        assert loop._worker.is_alive()
        intake.block.set()
        assert loop.stop(timeout=1)
        assert not loop._reader.is_alive()
        assert not loop._worker.is_alive()
    finally:
        intake.block.set()
        loop.stop()


def test_assignment_repeated_stop_does_not_cancel_transport_cleanup():
    entered, closed = threading.Event(), threading.Event()

    class ClosingEvents:
        async def async_stream_events(self):
            yield {"type": "ready"}
            try:
                await asyncio.sleep(600)
            finally:
                entered.set()
                await asyncio.sleep(0.05)
                closed.set()

    loop = build_loop(ClosingEvents(), CountingIntake())
    loop.start()
    try:
        wait_for(lambda: loop.status()["state"] == "connected")
        loop.stop(timeout=0)
        assert entered.wait(1)
        loop.stop(timeout=0)
        assert loop.stop(timeout=1)
        assert closed.is_set()
    finally:
        loop.stop()


def test_assignment_tcp_enqueue_latency_capacity_release_and_affinity(server_url, app):
    """Real /events -> dirty wake -> /tasks -> existing bounded spawn contract."""
    spawned = []
    with DispatchClient(server_url) as client:
        first = client.create(
            "occupied", repo=TEST_REPO, target_machine="host-a",
        )
        client.claim("worker", repo=TEST_REPO, machine="host-a", task_id=first["id"])
        intake = SatelliteWorkIntake(
            client, machine="host-a", repo=TEST_REPO, project="widget",
            spawn_fn=lambda task_id, **kwargs: spawned.append((task_id, kwargs)),
        )
        loop = build_loop(client, intake)
        loop.start()
        try:
            wait_for(lambda: (
                loop.status()["reconciles"] >= 1 and loop.status()["state"] == "connected"
            ))
            client.create("other host", repo=TEST_REPO, target_machine="host-b")
            client.create("other repo", repo=OTHER_REPO, target_machine="host-a")
            task = client.create("queued", repo=TEST_REPO, target_machine="host-a")
            time.sleep(0.1)
            assert spawned == []  # authoritative occupancy, not event authority
            before = time.monotonic()
            client.abandon(first["id"], worker_id="worker", permitted=True, reason="test capacity")
            wait_for(lambda: len(spawned) == 1, timeout=1.5)
            assert time.monotonic() - before < 1.5
            assert spawned[0][0] == task["id"]
            assert spawned[0][1]["route"] == " --shared"
            assert client.get(task["id"])["status"] == "queued"
        finally:
            before = time.monotonic()
            assert loop.stop(timeout=1)
            assert time.monotonic() - before < 1
        wait_for(lambda: app.state.bus.subscriber_count == 0)


@pytest.mark.guard
def test_assignment_tcp_startup_and_reconnect_recover_unannounced_queue(server_url, app):
    bus = app.state.bus
    original = bus.subscribe
    allow_initial_ready = threading.Event()
    reconnect_waiting = threading.Event()
    allow_reconnect = threading.Event()
    connections = 0

    async def disconnectable(**kwargs):
        nonlocal connections
        connections += 1
        if connections == 1:
            while not allow_initial_ready.is_set():
                await asyncio.sleep(0.005)
        else:
            reconnect_waiting.set()
            while not allow_reconnect.is_set():
                await asyncio.sleep(0.005)
        stream = original(**kwargs)
        try:
            async for event in stream:
                if event.get("type") == "test.disconnect":
                    return
                yield event
        finally:
            await stream.aclose()

    bus.subscribe = disconnectable
    spawned = []
    with DispatchClient(server_url) as client:
        first = client.create("before ready", repo=TEST_REPO, target_machine="host-a")
        intake = SatelliteWorkIntake(
            client, machine="host-a", repo=TEST_REPO, project="widget",
            max_concurrent=3, spawn_fn=lambda task_id, **kw: spawned.append(task_id),
        )
        loop = build_loop(client, intake)
        loop.start()
        try:
            wait_for(lambda: spawned == [first["id"]])
            wait_for(lambda: loop.status()["reconciles"] >= 1)
            assert loop.status()["reconciles"] == 1
            allow_initial_ready.set()
            wait_for(lambda: loop.status()["state"] == "connected")
            wait_for(lambda: loop.status()["reconciles"] >= 2)
            bus.publish({"type": "test.disconnect"})
            assert reconnect_waiting.wait(3)
            assert loop.status()["state"] != "connected"
            assert bus.subscriber_count == 0
            second = client.create("in gap", repo=TEST_REPO, target_machine="host-a")
            assert spawned == [first["id"]]
            allow_reconnect.set()
            wait_for(lambda: second["id"] in spawned)
            wait_for(lambda: loop.status()["state"] == "connected")
            assert spawned == [first["id"], second["id"]]
        finally:
            allow_initial_ready.set()
            allow_reconnect.set()
            assert loop.stop()
    wait_for(lambda: bus.subscriber_count == 0)


def test_assignment_health_reports_local_subscription_without_directory_read(
    server_url, app,
):
    class LocalRunner:
        def assignment_status(self):
            return {"state": "degraded", "error": "ReadTimeout", "reconciles": 3}

    app.state.federation_runner = LocalRunner()
    with DispatchClient(server_url) as client:
        health = client.health()
        assert health["assignment_intake"] == LocalRunner().assignment_status()
        assert health["events_keepalive_seconds"] == 10.0


@pytest.mark.guard
def test_assignment_runner_presence_not_blocked_by_spawn(monkeypatch, server_url):
    from agent_dispatch import config, satellite_work_intake

    monkeypatch.setattr(config, "satellite_gate_open", lambda: True)
    monkeypatch.setattr(config, "shared_url", lambda: server_url)
    monkeypatch.setattr(config, "shared_token", lambda: None)
    intake = CountingIntake()
    intake.block.clear()
    monkeypatch.setattr(satellite_work_intake, "SatelliteWorkIntake", lambda *a, **kw: intake)
    directory = FleetDirectory()
    runner = FederationRunner(directory, "host-a", role="satellite")
    monkeypatch.setattr(runner, "_satellite_kwargs", lambda: {})
    beats = []
    original = runner._heartbeat
    monkeypatch.setattr(runner, "_heartbeat", lambda: (beats.append(1), original())[1])
    runner.start(interval=0.02)
    try:
        assert intake.entered.wait(1)
        wait_for(lambda: len(beats) >= 4)
        wait_for(lambda: runner.status()["assignment_intake"]["state"] == "connected")
        assert intake.count == 1
    finally:
        intake.block.set()
        runner.stop(timeout=2)
    assert runner._assignment_intake is None


@pytest.fixture
def quiet_server_url(app, request):
    from fastapi.responses import StreamingResponse

    app.router.routes[:] = [
        route for route in app.router.routes if getattr(route, "path", None) != "/events"
    ]

    @app.get("/events")
    async def quiet_stream(request: Request):
        app.state.stream_headers = request.headers
        async def frames():
            yield 'data: {"type": "ready"}\n\n'
            await asyncio.sleep(600)

        return StreamingResponse(frames(), media_type="text/event-stream")

    return request.getfixturevalue("server_url")


def test_assignment_tcp_read_timeout_is_bounded(quiet_server_url):
    import httpx

    async def scenario(client):
        stream = client.async_stream_events(read_timeout=0.5)
        try:
            assert await anext(stream) == {"type": "ready"}
            before = time.monotonic()
            with pytest.raises(httpx.ReadTimeout):
                await anext(stream)
            assert time.monotonic() - before < 2
        finally:
            await stream.aclose()

    with DispatchClient(quiet_server_url) as client:
        asyncio.run(scenario(client))


def test_assignment_tcp_forwards_pinned_client_credentials(quiet_server_url, app):
    async def scenario(client):
        stream = client.async_stream_events()
        try:
            assert await anext(stream) == {"type": "ready"}
            assert app.state.stream_headers.get("authorization") is not None
            assert (
                app.state.stream_headers.get("authorization")
                == client._http.headers.get("authorization")
            )
        finally:
            await stream.aclose()

    with DispatchClient(quiet_server_url, token="synthetic-events") as client:
        asyncio.run(scenario(client))


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_assignment_stream_refuses_unbounded_timing(timeout):
    async def scenario(client):
        stream = client.async_stream_events(read_timeout=timeout)
        with pytest.raises(ValueError, match="finite and positive"):
            await anext(stream)

    with DispatchClient("http://127.0.0.1:9") as client:
        asyncio.run(scenario(client))


@pytest.mark.parametrize("change", ["gate", "url", "token"])
def test_assignment_runner_retires_config_change_without_closing_inuse_client(
    monkeypatch, server_url, change,
):
    from agent_dispatch import config, satellite_work_intake

    settings = {"gate": True, "url": server_url, "token": None}
    monkeypatch.setattr(config, "satellite_gate_open", lambda: settings["gate"])
    monkeypatch.setattr(config, "shared_url", lambda: settings["url"])
    monkeypatch.setattr(config, "shared_token", lambda: settings["token"])
    intake = CountingIntake()
    intake.block.clear()
    monkeypatch.setattr(satellite_work_intake, "SatelliteWorkIntake", lambda *a, **kw: intake)
    runner = FederationRunner(FleetDirectory(), "host-a", role="satellite")
    monkeypatch.setattr(runner, "_satellite_kwargs", lambda: {})
    runner._running = True
    runner.tick()
    try:
        assert intake.entered.wait(1)
        client = runner._work_intake_client
        settings[change] = {"gate": False, "url": None, "token": "rotated"}[change]
        runner.tick()
        assert runner._work_intake_client is client
        assert not client._http.is_closed
        intake.block.set()
        wait_for(lambda: not (
            runner._assignment_intake._worker.is_alive()
            or runner._assignment_intake._reader.is_alive()
        ))
        runner.tick()
        assert client._http.is_closed
    finally:
        intake.block.set()
        runner.stop(timeout=2)
