"""Represented aliases, deduplication and reconnect cursors cross only quiescently."""

from __future__ import annotations

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from zdd.private_protocol import Deadline, PrivateIPCError

from agent_bridge import telemetry
from agent_bridge.app import create_app
from agent_bridge.effective_runtime import ServingRuntimeCapture
from agent_bridge.live_representation import (
    LiveEventStore, MergedIds, merged_cursor, translate_reconnect_cursor,
)
from agent_bridge.live_runtime import capture_live_events, restore_live_events
from agent_bridge.runtime_restore import restore_runtime
from agent_bridge.runtime_transfer import _self, receive_runtime
from test_runtime_restore import captured as captured
from test_runtime_transfer import MemoryChannel, request
from test_worktree_runtime import full_capture


def event(key, text="text", timestamp=100.0):
    return {"id": key, "type": "assistant.message", "data": {"content": text}, "timestamp": timestamp}


def represented_graph():
    store = LiveEventStore()
    store.ingest("a", [event("shared"), event("a")], worktree_id="synthetic-venue")
    store.ingest("b", [event("b"), event("shared")], worktree_id="synthetic-venue")
    store.ingest("c", [event("c", timestamp=101.0)])
    continuities = {sid: store.get(sid).continuity_id for sid in ("a", "b", "c")}
    store.alias("b", "c")
    store.alias("a", "c")
    store.get_or_create("empty", worktree_id="empty-venue")
    return store, continuities


def test_private_roundtrip_preserves_shared_graph_and_reconnect_continuity(monkeypatch):
    store, continuities = represented_graph()
    payload = capture_live_events(store)
    emitted = []
    monkeypatch.setattr(telemetry, "emit", emitted.append)
    restored = restore_live_events(payload)
    assert emitted == []
    assert restored is not store and capture_live_events(restored) == payload
    assert restored.get("a") is restored.get("b") is restored.get("c")
    assert restored.ids_of("c") == store.ids_of("c")
    assert restored._seen_ids["a"] is restored._seen_ids["b"] is restored._seen_ids["c"]
    assert restored._seen_order["a"] is restored._seen_order["b"] is restored._seen_order["c"]
    assert restored._sdk_events["a"] is restored._sdk_events["b"] is restored._sdk_events["c"]
    for sid, continuity in continuities.items():
        for cursor in range(3):
            assert translate_reconnect_cursor(restored, restored.get("c"), continuity, cursor) == (
                translate_reconnect_cursor(store, store.get("c"), continuity, cursor)
            )
    for _target, ids in restored.merged_history().values():
        assert type(ids) is MergedIds
        assert all(merged_cursor(ids, i) <= merged_cursor(ids, i + 1) for i in range(3))
    assert restored.ingest("b", [event("shared"), event("c")]) == 0
    head = restored.get("a").latest_id
    assert restored.ingest("c", [event("new", timestamp=102.0)]) == 1
    assert restored.get("b").latest_id == head + 1
    assert store.get("a").latest_id == head
    assert restored.get("empty")._next_id == 1
    assert restored.get("empty")._telemetry.log_epoch == store.get("empty")._telemetry.log_epoch


def test_open_tool_and_telemetry_state_survive_without_sink_or_event_replay(monkeypatch):
    store = LiveEventStore()
    store.ingest("live", [{
        "id": "tool", "type": "tool.execution_start", "timestamp": 100.0,
        "data": {"toolCallId": "call", "toolName": "shell", "arguments": {"opaque": [1]}},
    }], worktree_id="venue")
    source = store.get("live")
    monkeypatch.setattr(telemetry, "emit", lambda record: pytest.fail("restore emitted telemetry"))
    restored = restore_live_events(capture_live_events(store))
    target = restored.get("live")
    assert vars(source._telemetry) == vars(target._telemetry)
    assert target._open_tool_calls["call"] is target._events[0]
    target._events[0].data["raw_input"]["opaque"].append(2)
    assert source._events[0].data["raw_input"]["opaque"] == [1]


@pytest.mark.parametrize("pause", ["dedup", "append", "record", "alias"])
def test_capture_refuses_every_inflight_gap_without_waiting_or_copying(monkeypatch, pause):
    store = LiveEventStore()
    store.ingest("old", [event("old")])
    store.ingest("new", [event("new", timestamp=101.0)])
    entered, release = threading.Event(), threading.Event()
    owner = store.get("old") if pause == "append" else store
    name = {"dedup": "_mark_seen", "append": "append", "record": "_record_sdk_event",
            "alias": "_copy_merged"}[pause]
    original = getattr(owner, name)

    def paused(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.set()
        assert release.wait(5), "test failed to release operation"
        return value

    monkeypatch.setattr(owner, name, paused)
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(store.alias, "old", "new") if pause == "alias" else (
            worker.submit(store.ingest, "old", [event("late", timestamp=102.0)])
        )
        try:
            assert entered.wait(5)
            assert store._operations > 0
            with pytest.raises(PrivateIPCError):
                capture_live_events(store)
        finally:
            release.set()
        future.result(timeout=5)
    assert store._operations == 0
    assert capture_live_events(restore_live_events(capture_live_events(store))) == capture_live_events(store)


def test_failed_ingestion_releases_operation_custody(monkeypatch):
    store = LiveEventStore()

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(store, "get_or_create", broken)
    with pytest.raises(RuntimeError, match="synthetic"):
        store.ingest("live", [])
    assert store._operations == 0
    assert capture_live_events(store)["bindings"] == {}


@pytest.mark.parametrize("mutation", [
    "waiter", "log-lock", "store-lock", "custody-lock", "merging", "db",
    "telemetry", "open-tool", "fifo", "fifo-identity", "index", "oversize", "subclass",
])
def test_capture_rejects_unsupported_or_nonquiescent_graph(mutation):
    store, _continuities = represented_graph()
    log = store.get("a")
    lock = None
    if mutation == "waiter":
        log._waiters.append((None, None))
    elif mutation.endswith("-lock"):
        lock = {"log-lock": log._lock, "store-lock": store._lock,
                "custody-lock": store._operation_lock}[mutation]
        lock.acquire()
    elif mutation == "merging":
        store._merging[id(log)] = [threading.Event()]
    elif mutation == "db":
        log._db = object()
    elif mutation == "telemetry":
        log._telemetry.conversation_state = "invented"
    elif mutation == "open-tool":
        log._open_tool_calls["call"] = copy.deepcopy(log._events[0])
    elif mutation == "fifo":
        store._seen_order["a"].append("invented")
    elif mutation == "fifo-identity":
        store._seen_order["b"] = copy.copy(store._seen_order["b"])
    elif mutation == "index":
        next(iter(store._merged_history.values()))[1].prefix_max.clear()
    elif mutation == "oversize":
        log._events[0].data["private"] = "x" * 262145
    else:
        class Other(LiveEventStore):
            pass
        store = Other()
    try:
        with pytest.raises(PrivateIPCError):
            capture_live_events(store)
    finally:
        if lock is not None:
            lock.release()


@pytest.mark.parametrize("mutation", [
    "schema", "extra", "cursor", "id", "source", "epoch", "orphan",
    "reference", "seen", "sdk", "history", "cycle", "boolean-id", "nan",
])
def test_restore_refuses_torn_coerced_or_incomplete_private_data(mutation):
    store, _continuities = represented_graph()
    payload = capture_live_events(store)
    if mutation == "schema":
        payload["schema"] = True
    elif mutation == "extra":
        payload["private-value"] = "private-value"
    elif mutation == "cursor":
        payload["logs"][0]["next_id"] += 1
    elif mutation == "id":
        payload["logs"][0]["events"][0]["id"] = 2
    elif mutation == "source":
        payload["logs"][0]["telemetry"]["_identity"]["source"] = "owned"
    elif mutation == "epoch":
        payload["logs"][0]["telemetry"]["log_epoch"] = "private-value"
    elif mutation == "orphan":
        payload["logs"].append(copy.deepcopy(payload["logs"][-1]))
    elif mutation == "reference":
        payload["bindings"]["a"]["log"] = 999
    elif mutation == "seen":
        payload["seen"][0].append(payload["seen"][0][0])
    elif mutation == "sdk":
        payload["sdk"][0]["shared"] = [999]
    elif mutation == "history":
        next(iter(payload["history"].values()))["ids"][0][1] = 999
    elif mutation == "cycle":
        key = next(iter(payload["history"]))
        payload["history"][key]["target"] = key
    elif mutation == "boolean-id":
        payload["logs"][0]["events"][0]["id"] = True
    else:
        payload["logs"][0]["events"][0]["timestamp"] = float("nan")
    with pytest.raises(PrivateIPCError) as caught:
        restore_live_events(payload)
    assert "private-value" not in str(caught.value)


def test_fifo_eviction_continues_at_exact_captured_boundary():
    store = LiveEventStore()
    store.ingest("live", [
        {"id": str(i), "type": "unknown", "data": {}} for i in range(4096)
    ])
    restored = restore_live_events(capture_live_events(store))
    restored.ingest("live", [{"id": "4096", "type": "unknown"}])
    assert "0" not in restored._seen_ids["live"]
    assert list(restored._seen_order["live"]) == [str(i) for i in range(1, 4097)]
    assert "0" in store._seen_ids["live"]


@pytest.mark.asyncio
async def test_actual_async_waiter_is_not_transferred():
    store = LiveEventStore()
    log = store.get_or_create("live")
    waiter = asyncio.create_task(log.wait_for_events(0, timeout=0.05))
    await asyncio.sleep(0)
    with pytest.raises(PrivateIPCError):
        capture_live_events(store)
    await waiter
    assert not log._waiters
    assert capture_live_events(store)["logs"][0]["events"] == []


@pytest.mark.asyncio
async def test_runtime_and_pending_graph_keep_actual_store_without_blank_fallback(captured, monkeypatch):
    source, origins, _capture = captured
    full_capture(captured)
    source.state.live_event_store, _continuities = represented_graph()
    capture = ServingRuntimeCapture(
        source, origins, include_resolver=True, include_relay=True, include_roster=True,
        include_sessions=True, include_worktrees=True, include_live_events=True,
        session_deadline=Deadline(10),
    )
    private = capture.capture().private_payload()
    runtime = restore_runtime(private)
    assert capture_live_events(runtime.live_events) == private["runtime"]["live_events"]
    assert capture.matches(capture._last)
    source.state.live_event_store.ingest("a", [event("changed", timestamp=103.0)])
    assert not capture.matches(capture._last)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(origins.config_root))
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(origins.install_root))
    channel = MemoryChannel(_self())
    request(capture, channel, "d" * 64)
    pending = receive_runtime(channel, channel.peer, namespace="d" * 64, deadline=Deadline(10))
    app = create_app(restored_runtime=pending.runtime)
    async with app.router.lifespan_context(app):
        pending.acknowledge(app)
        assert app.state.live_event_store is pending.runtime.live_events
        pending.verify_resources(app)
        app.state.live_event_store = LiveEventStore()
        with pytest.raises(PrivateIPCError):
            pending.verify_resources(app)
        app.state.live_event_store = pending.runtime.live_events
        pending.runtime.live_events.ingest("a", [event("late", timestamp=104.0)])
        with pytest.raises(PrivateIPCError):
            pending.verify_resources(app)
        assert app.state.ready is False and pending.serving is False


def test_optin_requires_previous_coverage(captured):
    app, origins, _capture = captured
    with pytest.raises(PrivateIPCError):
        ServingRuntimeCapture(app, origins, include_live_events=True)
