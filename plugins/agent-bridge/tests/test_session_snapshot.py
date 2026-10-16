"""Private metadata staging never rediscovers or activates a surviving session."""

from __future__ import annotations

import copy
import asyncio
import time
from dataclasses import asdict

import pytest
from fastapi import FastAPI
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError
from zdd.routing import Endpoint

from agent_bridge.acp_client import AcpClient
from agent_bridge.effective_runtime import ServingRuntimeCapture
from agent_bridge.events import EventLog
from agent_bridge.models import SessionStatus
from agent_bridge.runtime_restore import restore_runtime
from agent_bridge.serving_runtime import CandidateServing
from agent_bridge.session_host import private_receipt as receipt_module
from agent_bridge.session_host.host_index import HostIndex, HostRecord
from agent_bridge.session_host.private_receipt import VerifiedHostReceipt, decode_host_receipt
from agent_bridge.session_manager import Session
from agent_bridge.session_snapshot import capture_sessions, decode_sessions
from agent_bridge.transport import SpawnTarget
from test_predecessor_runtime import NativeChannel
from test_roster_runtime import warm_roster
from test_runtime_restore import captured as captured


def session_fixture(app: FastAPI) -> Session:
    session = Session(
        "actual-session", "actual-name",
        SpawnTarget(
            cwd="/captured/workspace", env={"SESSION_SECRET": "fixture-sensitive"},
            container={"name": "captured-container"},
            venue={"instance_id": "captured-instance"},
        ),
        agent_name="captured-agent", caller_id="actual-caller",
    )
    # Prove the private path preserves the actual target instead of migrating it.
    session.target.container.pop("instance_id")
    session.status = SessionStatus.RUNNING
    session.acp_session_id = "actual-acp-session"
    session.model_override, session.effort_override = "selected-model", "selected-effort"
    session.mcp_servers = [{"name": "actual-mcp", "env": {"SECRET": "fixture-sensitive"}}]
    session.progress = {"build": "ok"}
    session._crossed_thresholds = {"warning"}
    session._handoff_pending = True
    session._stop_requested = True
    session._stop_notices = 2
    session.last_output_at = 17.25
    session.last_heartbeat_at = 18.25
    session.turn_count = 5
    session.event_log = EventLog(session_id=session.session_id)
    session.event_log.append("agent_message", {"text": "actual-private-tail"}, timestamp=16.5)
    session.event_log._next_id = 7
    session.subscriber_count = 2
    app.state.session_manager._sessions[session.session_id] = session
    return session


def composite(captured: tuple) -> ServingRuntimeCapture:
    app, origins, _capture = captured
    warm_roster(app)
    return ServingRuntimeCapture(
        app, origins, include_resolver=True, include_relay=True, include_roster=True,
        include_sessions=True, session_deadline=Deadline(10),
    )


def native_fixture(
    monkeypatch: pytest.MonkeyPatch, *, session_id: str = "actual-session",
) -> tuple[dict, NativeChannel, VerifiedHostReceipt]:
    host = PeerIdentity(12345, "23456", "synthetic-principal")
    frontend = Endpoint("127.0.0.1", 54321, 34567, "fixture-version", 4, "45678")
    payload = {
        "schema": 1, "host": asdict(host), "instance": "a" * 64,
        "session_id": session_id, "config_identity": "b" * 64,
        "attachment_generation": 2, "frontend": frontend.to_dict(),
        "snapshot": "c" * 64, "ack_cursor": 5, "max_seq": 7,
        "child_pid": 56789, "protocol_version": 1, "host_version": "fixture-host",
        "data_endpoint": {"bind": "127.0.0.1", "port": 1234}, "acp_wire": None,
        "frontend_data_endpoint": None,
    }
    channel = NativeChannel(host)
    monkeypatch.setattr(receipt_module, "_native_channel_type", lambda: NativeChannel)
    verified = VerifiedHostReceipt(
        payload, channel, host, frontend, session_id, "b" * 64, Deadline(2),
    )
    return payload, channel, verified


def test_actual_metadata_events_mcp_and_selected_target_hydrate_without_rediscovery(
    captured: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _origins, _capture = captured
    original = session_fixture(app)
    payload = composite(captured).capture().private_payload()

    def forbidden(*args, **kwargs):
        pytest.fail("private hydration rediscovered or mutated external state")

    monkeypatch.setattr(HostIndex, "_load", forbidden)
    monkeypatch.setattr(app.state.db, "list_sessions", forbidden)
    monkeypatch.setattr(app.state.db, "get_turns", forbidden)
    monkeypatch.setattr(app.state.db, "get_events", forbidden)
    monkeypatch.setattr(app.state.db, "flush", forbidden)
    monkeypatch.setattr("agent_bridge.container_preference_launch.migrate_selected_instance", forbidden)
    restored = restore_runtime(payload)
    manager = restored.build_manager(app.state.db)
    prepared = manager._sessions[original.session_id]
    assert prepared.target == original.target and prepared.target is not original.target
    assert prepared.mcp_servers == original.mcp_servers
    assert prepared.mcp_servers is not original.mcp_servers
    assert (prepared.model_override, prepared.effort_override) == ("selected-model", "selected-effort")
    assert prepared.acp_session_id == original.acp_session_id
    assert prepared.status is SessionStatus.RUNNING
    assert prepared.last_output_at == 17.25 and prepared.last_heartbeat_at == 18.25
    assert prepared.progress == {"build": "ok"} and prepared.turn_count == 5
    assert prepared._crossed_thresholds == {"warning"}
    assert prepared._handoff_pending and prepared._stop_requested and prepared._stop_notices == 2
    assert prepared.client is None and prepared._prompt_task is None
    assert prepared.subscriber_count == 0
    assert not prepared._lifecycle_lock.locked() and not prepared._turn_start_lock.locked()
    assert prepared.event_log.get_events(0) == original.event_log.get_events(0)
    assert prepared.event_log._next_id == 7
    assert prepared.event_log._waiters == [] and manager._host_index.all() == []
    assert manager._private_session_snapshot.sessions[0].observation.subscriber_count == 2
    assert "fixture-sensitive" not in repr(restored)
    prepared.mcp_servers[0]["env"]["SECRET"] = "new-recipient-only"
    assert original.mcp_servers[0]["env"]["SECRET"] == "fixture-sensitive"


def test_metadata_identity_changes_without_changing_stable_host_context(
    captured: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, origins, _capture = captured
    session = session_fixture(app)
    raw, channel, verified = native_fixture(monkeypatch)
    manager = app.state.session_manager
    manager._host_index._records[session.session_id] = HostRecord(
        session.session_id, 1234, raw["host"]["pid"], raw["child_pid"],
    )
    warm_roster(app)
    capture = ServingRuntimeCapture(
        app, origins, include_resolver=True, include_relay=True, include_roster=True,
        include_sessions=True, host_receipts={session.session_id: verified},
        session_deadline=Deadline(2),
    )
    try:
        first = capture.capture()
        session.model_override = "new-selected-model"
        second = capture.capture()
        assert first.identity != second.identity
        assert not capture.matches(first)
        assert second.private_payload()["runtime"]["sessions"]["sessions"][0]["host"]["config_identity"] == "b" * 64
        assert channel.retained == 1
        channel.close()
        assert verified.private_payload(Deadline(1)) == raw
        channel.api.signalled = True
        with pytest.raises(PrivateIPCError):
            capture.capture()
    finally:
        verified.close()
    assert channel.api.closed == 1


def test_existing_host_or_client_without_native_evidence_refuses(captured: tuple) -> None:
    app, _origins, _capture = captured
    session = session_fixture(app)
    session.client = AcpClient(on_event=lambda *_args: None)
    with pytest.raises(PrivateIPCError):
        composite(captured).capture()
    session.client = None
    app.state.session_manager._host_index._records["uncovered-host"] = HostRecord(
        "uncovered-host", 1234, 12345, 23456,
    )
    with pytest.raises(PrivateIPCError):
        composite(captured).capture()


def test_nonempty_metadata_cannot_enable_foreground_serving(captured: tuple) -> None:
    app, _origins, _capture = captured
    session_fixture(app)
    runtime = restore_runtime(composite(captured).capture().private_payload())
    with pytest.raises(PrivateIPCError):
        CandidateServing(app, runtime)


def test_mutated_detached_session_config_refuses_before_manager_construction(captured: tuple) -> None:
    app, _origins, _capture = captured
    session_fixture(app)
    runtime = restore_runtime(composite(captured).capture().private_payload())
    runtime.sessions.sessions[0].target.env["SESSION_SECRET"] = "changed"
    with pytest.raises(PrivateIPCError):
        runtime.build_manager(app.state.db)


@pytest.mark.parametrize("case", [
    "schema-bool", "extra", "missing", "duplicate", "cursor", "events-order",
    "subscriber-bool", "mcp-object", "live-no-receipt", "host-session", "host-bool",
])
def test_typed_session_coverage_refuses_malformed_fields(captured: tuple, case: str) -> None:
    app, _origins, _capture = captured
    session_fixture(app)
    raw = capture_sessions(app.state.session_manager, {}, Deadline(1))
    session = raw["sessions"][0]
    if case == "schema-bool":
        raw["schema"] = True
    elif case == "extra":
        session["unknown"] = "fixture-sensitive"
    elif case == "missing":
        del session["model_override"]
    elif case == "duplicate":
        raw["sessions"].append(copy.deepcopy(session))
    elif case == "cursor":
        session["next_event_id"] = 1
    elif case == "events-order":
        session["events"].append(copy.deepcopy(session["events"][0]))
    elif case == "subscriber-bool":
        session["observation"]["subscriber_count"] = True
    elif case == "mcp-object":
        session["mcp_servers"][0]["env"]["SECRET"] = object()
    elif case == "live-no-receipt":
        session["observation"]["client_present"] = True
    else:
        session["host"] = {
            "schema": 1, "host": asdict(PeerIdentity(12345, "23456", "principal")),
            "instance": "a" * 64, "session_id": "wrong-session",
            "config_identity": "b" * 64, "attachment_generation": 1,
            "frontend": Endpoint("127.0.0.1", 1234, 23456, "fixture", 1, "34567").to_dict(),
            "snapshot": "c" * 64, "ack_cursor": 0, "max_seq": 0,
            "child_pid": 34567, "protocol_version": 1, "host_version": "fixture",
            "data_endpoint": {"bind": "127.0.0.1", "port": 4567},
        }
        if case == "host-bool":
            session["host"]["schema"] = True
    with pytest.raises(PrivateIPCError) as caught:
        decode_sessions(raw)
    assert "fixture-sensitive" not in str(caught.value)


@pytest.mark.parametrize("case", ["copied", "wrong-host", "wrong-session", "wrong-context", "wrong-front"])
def test_native_receipt_requires_independent_channel_birth_and_context(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    raw, channel, verified = native_fixture(monkeypatch)
    verified.close()
    host = channel.peer
    front = Endpoint(**raw["frontend"])
    if case == "copied":
        channel = object()
    elif case == "wrong-host":
        host = PeerIdentity(host.pid + 1, host.start_time, host.principal)
    elif case == "wrong-front":
        front = Endpoint(front.bind, front.port, front.pid, front.version, 99, front.process_start_time)
    with pytest.raises(PrivateIPCError):
        VerifiedHostReceipt(
            raw, channel, host, front,
            "wrong" if case == "wrong-session" else raw["session_id"],
            "d" * 64 if case == "wrong-context" else raw["config_identity"], Deadline(1),
        )


def test_decoded_receipt_is_only_an_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    raw, channel, verified = native_fixture(monkeypatch)
    try:
        decoded = decode_host_receipt(raw)
        assert asdict(decoded) == raw and decoded.host == channel.peer
        assert "c" * 64 not in repr(decoded)
        assert not hasattr(decoded, "retain_peer")
    finally:
        verified.close()


def test_older_receipt_without_frontend_data_endpoint_keeps_original_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, channel, original = native_fixture(monkeypatch)
    older = None
    try:
        raw.pop("frontend_data_endpoint")
        receipt = decode_host_receipt(raw)
        assert receipt.frontend_data_endpoint is None
        older = VerifiedHostReceipt(
            raw, channel, receipt.host, receipt.frontend, receipt.session_id,
            receipt.config_identity, Deadline(2),
        )
        assert older.private_payload(Deadline(2)) == raw
    finally:
        original.close()
        if older is not None:
            older.close()


def test_original_native_receipt_deadline_cannot_be_extended(monkeypatch: pytest.MonkeyPatch) -> None:
    _raw, _channel, verified = native_fixture(monkeypatch)
    try:
        verified._deadline._expires = time.monotonic() - 1
        with pytest.raises(PrivateIPCError):
            verified.private_payload(Deadline(30))
    finally:
        verified.close()


def test_changed_native_receipt_wire_refuses_before_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    _raw, _channel, verified = native_fixture(monkeypatch)
    try:
        verified._wire = verified._wire.replace(b"actual-session", b"forged-session")
        with pytest.raises(PrivateIPCError):
            verified.private_payload(Deadline(1))
    finally:
        verified.close()


def test_pending_prompt_and_locks_are_observed_not_recreated(captured: tuple) -> None:
    async def scenario() -> None:
        app, _origins, _capture = captured
        session = session_fixture(app)
        settled = asyncio.Event()
        task = session._prompt_task = asyncio.create_task(settled.wait())
        await session._lifecycle_lock.acquire()
        await session._turn_start_lock.acquire()
        try:
            runtime = restore_runtime(composite(captured).capture().private_payload())
            prepared = runtime.build_manager(app.state.db)
            observation = prepared._private_session_snapshot.sessions[0].observation
            assert observation.prompt_pending and observation.lifecycle_locked
            assert observation.turn_start_locked and not task.done()
            successor = prepared._sessions[session.session_id]
            assert successor._prompt_task is None
            assert not successor._lifecycle_lock.locked()
            assert not successor._turn_start_lock.locked()
        finally:
            session._lifecycle_lock.release()
            session._turn_start_lock.release()
            settled.set()
            await task
    asyncio.run(scenario())
