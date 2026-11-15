"""Real private authenticated control sockets for the optional observation receiver."""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from test_owner_observation_provider import request, row

from agent_worktrees import owner_observation_contract as c
from agent_worktrees import owner_observation_transport as wire
from agent_worktrees import status_monitor_cutover as control
from agent_worktrees.worktrees_runtime import WorktreesRuntime


@pytest.fixture
def served(tmp_path, monkeypatch):
    owner = WorktreesRuntime(tmp_path / "resident.sqlite3", wake=threading.Event())
    monkeypatch.setattr(owner.agendas, "roster_changed", lambda project: None)
    owner.activate()
    owner.observations.runtime.heartbeat = 0.01
    owner.observation_read(request())
    owner.publish("project", [row()], observed_at=1)
    actions, streams = [], []

    def handle(action, payload):
        actions.append(action)
        if action == "health":
            return owner.health()
        if action == wire.READ_ACTION:
            return owner.observation_read(payload)
        if action == wire.HISTORY_ACTION:
            return owner.observation_history(payload)
        pytest.fail(f"unexpected point action {action}")

    def stream(action, payload, disconnected):
        streams.append(action)
        return owner.stream(action, payload, disconnected)

    server = control.ControlServer(handle, token="fixture-token", stream_handle=stream)
    server.start()
    try:
        yield owner, server, actions, streams
    finally:
        owner.close_admission("closed")
        server.close()
        owner.shutdown()


def client(server, token="fixture-token"):
    return control.ControlClient(f"http://127.0.0.1:{server.port}", token=token, timeout=1.0)


def rpc(server, action, payload):
    value = client(server)._request(
        action,
        payload,
        max_response_bytes=wire.POINT_BYTES,
        total_timeout=2,
    )
    c.validate_frame(value)
    return value


@contextmanager
def held(server, payload, token="fixture-token"):
    with socket.create_connection(("127.0.0.1", server.port), timeout=1) as sock:
        sock.settimeout(2)
        sock.sendall(
            c.utf8(
                c.encode(
                    {
                        "version": 1,
                        "token": token,
                        "action": wire.FEED_ACTION,
                        "payload": payload,
                    }
                )
            )
            + b"\n"
        )
        with sock.makefile("rb") as reader:
            yield sock, reader


def frame(reader):
    raw = reader.readline(c.FRAME_BYTES + 1)
    assert raw and raw.endswith(b"\n") and len(raw) <= c.FRAME_BYTES
    value = c.decode(raw)
    c.validate_frame(value)
    return value


def event_frame(reader):
    deadline = time.monotonic() + 2
    while True:
        value = frame(reader)
        if value["type"] != "heartbeat":
            return value
        assert time.monotonic() < deadline, "provider did not deliver the expected change"


def wait_handlers(server):
    deadline = time.monotonic() + 2
    while server.active_handler_count() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.active_handler_count() == 0


def test_actual_control_health_point_utf8_history_and_held_updates(served):
    owner, server, actions, streams = served
    assert set(wire.CAPABILITIES) <= set(client(server).health()["capabilities"])
    baseline = rpc(server, wire.READ_ACTION, request())
    with held(server, request()) as (_sock, reader):
        assert frame(reader)["cursor"] == baseline["cursor"]
        owner.publish("project", [row(title="\u6f22\U0001f680", count=2)], observed_at=2)
        update = event_frame(reader)
        assert update["type"] == "delta" and update["base_cursor"] == baseline["cursor"]
        assert update["facts"]["row"]["title"] == "\u6f22\U0001f680"
        assert update["entries"][0]["kind"] == "observed-change"
        page = request()
        page.pop("after_cursor")
        page.update(source_generation=update["source_generation"], before_cursor=None, limit=64)
        result = rpc(server, wire.HISTORY_ACTION, page)
        assert result["type"] == "history-page" and len(result["entries"]) == 1
        assert streams == [wire.FEED_ACTION]
        assert wire.HISTORY_ACTION in actions
        owner.close_admission("draining")
        assert event_frame(reader)["reason"] == "draining"
    wait_handlers(server)
    owner.observation_wait_closed(1)
    assert not owner.observation_busy(server)


def test_unknown_auth_cannot_invoke_provider_or_enroll_demand(served):
    owner, server, actions, _streams = served
    before = len(actions)
    with pytest.raises(ValueError):
        client(server, token="wrong")._request(wire.READ_ACTION, request())
    assert len(actions) == before
    assert owner.observation_status()["status"] == "ready"
    wait_handlers(server)


@pytest.mark.parametrize(
    "fault", ["duplicate", "nonfinite", "unknown", "boolean-version", "oversized"]
)
@pytest.mark.parametrize("action", [wire.READ_ACTION, wire.HISTORY_ACTION, wire.FEED_ACTION])
def test_authenticated_malformed_new_schema_is_explicit_and_never_legacy_fallback(
    served, fault, action
):
    _owner, server, actions, streams = served
    payload = request()
    if action == wire.HISTORY_ACTION:
        payload.pop("after_cursor")
        payload.update(source_generation="a" * 32, before_cursor=None, limit=64)
    encoded = json.dumps(payload, separators=(",", ":"))
    if fault == "duplicate":
        encoded = encoded.replace(
            '"contract_version":1', '"contract_version":1,"contract_version":1'
        )
    elif fault == "nonfinite":
        encoded = encoded[:-1] + ',"unknown":NaN}'
    elif fault == "unknown":
        encoded = encoded[:-1] + ',"unknown":true}'
    elif fault == "boolean-version":
        encoded = encoded.replace('"contract_version":1', '"contract_version":true')
    else:
        encoded = encoded[:-1] + ',"unknown":"' + ("x" * c.FRAME_BYTES) + '"}'
    raw = (
        '{"version":1,"token":"fixture-token","action":'
        + json.dumps(action)
        + ',"payload":'
        + encoded
        + "}\n"
    ).encode()
    before_actions, before_streams = len(actions), len(streams)
    with socket.create_connection(("127.0.0.1", server.port), timeout=1) as sock:
        sock.settimeout(2)
        sock.sendall(raw)
        with sock.makefile("rb") as reader:
            result = reader.readline(wire.POINT_BYTES + 1)
    value = c.decode(result) if action == wire.FEED_ACTION else wire.decode_result(result)
    assert value["type"] == "error"
    assert value["error"]["code"] == ("oversized" if fault == "oversized" else "invalid-request")
    assert len(actions) == before_actions and len(streams) == before_streams
    wait_handlers(server)


def test_point_accounting_includes_admitted_handlers_during_drain(served, monkeypatch):
    owner, server, _actions, _streams = served
    entered, release = threading.Event(), threading.Event()
    original = server._handle

    def slow(action, payload):
        if action == wire.READ_ACTION:
            entered.set()
            assert release.wait(2)
        return original(action, payload)

    monkeypatch.setattr(server, "_handle", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rpc, server, wire.READ_ACTION, request())
        try:
            assert entered.wait(1)
            assert owner.observation_counts(server)[wire.READ_ACTION] == 1
            owner.close_admission("draining")
        finally:
            release.set()
        assert future.result(timeout=3)["error"]["code"] == "draining"
    wait_handlers(server)
    owner.observation_wait_closed(1)


def test_socket_cancellation_releases_the_single_subscription(served):
    owner, server, _actions, _streams = served
    with held(server, request()) as (sock, reader):
        assert frame(reader)["type"] == "snapshot"
        assert owner.observations.counts()["streams"] == 1
        sock.shutdown(socket.SHUT_RDWR)
    wait_handlers(server)
    owner.observation_wait_closed(1)
    assert owner.observations.counts()["streams"] == 0


def test_large_utf8_frame_uses_bytes_and_every_emitted_frame_is_bounded(served):
    owner, server, _actions, _streams = served
    with held(server, request()) as (_sock, reader):
        frame(reader)
        owner.publish(
            "project",
            [row(title="\u6f22" * 1000, active_effort={"items": ["\u6f22" * 1000] * 6})],
            observed_at=2,
        )
        update = event_frame(reader)
        assert update["type"] == "delta"
        assert update["facts"]["row"]["title"] == "\u6f22" * 1000
        assert len(wire.encode_frame(update)) <= c.FRAME_BYTES
        point = rpc(server, wire.READ_ACTION, request())
        assert len(wire.encode_result(point)) <= wire.POINT_BYTES
        assert point["facts"] == update["facts"]


def test_bad_parent_point_result_is_not_forwarded_as_success(served, monkeypatch):
    _owner, server, _actions, _streams = served
    monkeypatch.setattr(server, "_handle", lambda action, payload: {"ok": True, "worktrees": []})
    result = rpc(server, wire.READ_ACTION, request())
    assert result["type"] == "error" and result["error"]["code"] == "invalid-response"


@pytest.mark.parametrize("action", [wire.READ_ACTION, wire.HISTORY_ACTION])
def test_actual_monitor_dispatch_preserves_reads_until_handoff_revokes_admission(
    tmp_path, monkeypatch, action,
):
    from agent_worktrees import __main__ as main
    from agent_worktrees import worktrees_runtime
    from agent_worktrees.status_monitor_handover import MonitorHandover

    election = {"writer": True, "reader": True}
    owners = []
    monkeypatch.setattr(main, "_monitor_lock_path", lambda: tmp_path / "monitor.lock")
    monkeypatch.setattr(main, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(main, "_load_hook_client_module", lambda: None)
    monkeypatch.setattr(main.locks, "write_lock", lambda _path, extra=None: True)
    monkeypatch.setattr(
        control, "active_generation_for_pid", lambda pid: 42 if election["writer"] else None,
    )
    monkeypatch.setattr(control, "clear_route_if_owner", lambda pid: False)
    monkeypatch.setattr("agent_worktrees.runtime_monitor_identity.publish_lock", lambda *a, **k: True)
    monkeypatch.setattr(MonitorHandover, "read_admitted", lambda self: election["reader"])
    monkeypatch.setattr(
        main, "_monitor_sweep", lambda *a, **k: pytest.fail("fixture must shut down before sweep"),
    )

    factory = WorktreesRuntime.for_monitor

    def owner_factory(*args, **kwargs):
        owner = factory(*args, **kwargs)
        assert owner.store.db_path.parent == tmp_path
        monkeypatch.setattr(owner.agendas, "roster_changed", lambda project: None)
        owners.append(owner)
        return owner

    monkeypatch.setattr(worktrees_runtime.WorktreesRuntime, "for_monitor", staticmethod(owner_factory))
    results = []

    class Control:
        port = 12345

        def __init__(self, handle, **kwargs):
            self.handle = handle

        def start(self):
            assert self.handle("promote", {}) == {"adopted": True}
            owner = owners[0]
            assert owner.observation_read(request())["error"]["code"] == "unready"
            owner.publish("project", [row()], observed_at=1)
            current = owner.observation_read(request())
            payload = request()
            if action == wire.HISTORY_ACTION:
                payload.pop("after_cursor")
                payload.update(
                    source_generation=current["source_generation"], before_cursor=None, limit=64,
                )
            election["writer"] = False
            result = self.handle(action, payload)
            assert result["type"] == ("history-page" if action == wire.HISTORY_ACTION else "snapshot")
            assert self.handle("worktrees-read", {"contract_version": 1, "project": "project"})["ok"]
            assert owner.observation_status()["status"] == "ready"
            election["reader"] = False
            assert self.handle(action, payload)["error"]["code"] == "superseded"
            results.append(result)
            self.handle("shutdown", {})

        def current_read_count(self, action="worktrees-read"):
            return 0

        def close(self):
            pass

    monkeypatch.setattr(control, "ControlServer", Control)
    assert main.cmd_status_monitor(argparse.Namespace(interval=1, passive=True)) == 0
    assert len(results) == 1
