"""Isolated observer effects, faults, bounded evidence and real pull consumers."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import suppress
from datetime import datetime
import json
import os
from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from agent_procutil import no_window_flags
from agent_remote_driver.session_host.bundle import build_session_host_bundle
from agent_remote_driver.session_host.inspection import CONTRACT, probe, request as inspection_request
from agent_remote_driver.session_host import protocol as proto
from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.host import ChildProcess, SessionHost

from agent_bridge import __main__ as cli
from agent_bridge.client import BridgeClient
from agent_bridge.db import Database
from agent_bridge.events import EventLog, SseEvent
from agent_bridge.session_status import SessionStatus
from agent_bridge.routes.sessions import router
from agent_bridge.session_inspection import _age, _iso, _semantic
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget
from test_session_host import _FakeChild, _stub_tree_kill as _stub_tree_kill

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
    pytest.mark.effect("network"),
]


@pytest.fixture(autouse=True)
def _no_observer_reap(_stub_tree_kill: list[tuple[int, bool]]) -> Iterator[None]:
    yield
    assert not _stub_tree_kill, "inspection attempted to retire the child"


def _session(client: SessionHostClient | None = None) -> Any:
    session: Any = Session("inspection", "fixture", SpawnTarget(type="local"))
    session.event_log = EventLog()
    session.status = SessionStatus.RUNNING
    session.client = SimpleNamespace(
        session_host_client=client, is_running=True, pending_ask_user=lambda: [],
        active_background_tasks=[], pid=4242,
    )
    return session


def _custody(host: SessionHost, child: _FakeChild) -> tuple[Any, ...]:
    return (
        host._front, host._attachment_epoch, host.ack_cursor, host.buffered_seqs,
        host._last_reapable, host._graceful_detach, host._reap_timer,
        host._active_reap_timer, bytes(child.stdin.buffer), child.killed,
    )


@pytest.mark.asyncio
async def test_round_trip_is_fixed_size_and_never_touches_front_or_lifetime() -> None:
    child = _FakeChild()
    host = SessionHost(cast(ChildProcess, child), nonce="fixture")
    port = await host.serve(port=0)
    front = await SessionHostClient.connect(port=port)
    try:
        await front.attach(nonce=b"fixture")
        await front.write(b'{"id":7,"method":"session/prompt"}\n')
        child.feed_frame(b'{"method":"session/update","params":{}}')
        await anext(front.frames())
        before = _custody(host, child)
        result = await front.inspect()
        assert result["state"] == "responsive"
        assert len(result["nonce"]) == 32 and 0 <= result["rtt_s"] < 1
        snap = result["snapshot"]
        assert snap == {
            "host_pid": os.getpid(), "host_generation": host._inspection_generation,
            "child_pid": child.pid, "child_alive": True, "turn_in_flight": True,
            "max_seq": 1, "frontend_ack": 0, "buffer_depth": 1,
            "last_child_frame_age_s": snap["last_child_frame_age_s"],
        }
        assert snap["last_child_frame_age_s"] >= 0
        assert len(json.dumps(result)) < 1024
        assert _custody(host, child) == before
        refused = await probe("127.0.0.1", port, "not-the-credential")
        assert refused["state"] == "unsupported"
        assert _custody(host, child) == before
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(
                proto.encode(proto.MsgType.PING, json.dumps({
                    "contract": CONTRACT, "version": 1, "nonce": "0" * 32,
                    "credential": "fixture",
                }).encode())
                + proto.encode(proto.MsgType.WRITE, b"must not reach stdin")
                + proto.encode(proto.MsgType.ACK, proto.pack_u64(1))
                + proto.encode(proto.MsgType.STATUS, proto.pack_flag(True))
                + proto.encode(proto.MsgType.TERMINATE)
            )
            await writer.drain()
            reply = await proto.read_message(reader)
            assert reply is not None and reply[0] == proto.MsgType.PONG
            assert await reader.read() == b""
            assert _custody(host, child) == before
        finally:
            writer.close()
            await writer.wait_closed()
        await front.ack(1)
        await asyncio.sleep(.01)
        before = _custody(host, child)
        again = await front.inspect()
        assert again["snapshot"]["frontend_ack"] == 1
        assert again["snapshot"]["buffer_depth"] == 0
        assert again["snapshot"]["host_generation"] == snap["host_generation"]
        assert again["nonce"] != result["nonce"]
        assert _custody(host, child) == before
        child.feed_frame(b'{"id":7,"result":{}}')
        assert (await anext(front.frames()))[0] == 2
        await front.ack(2)
        await front.send_status(True)
        await front.close()

        async def front_lost() -> None:
            while host._front is not None:
                await asyncio.sleep(.01)

        await asyncio.wait_for(front_lost(), 1)
        assert host._reap_timer is not None
        before = _custody(host, child)
        assert (await probe("127.0.0.1", port, "fixture", child_pid=child.pid))["state"] == "responsive"
        assert _custody(host, child) == before
    finally:
        await front.close()
        await host.close()


@pytest.mark.asyncio
async def test_probe_remains_responsive_while_the_real_front_send_lock_is_backpressured() -> None:
    child = _FakeChild()
    host = SessionHost(cast(ChildProcess, child))
    port = await host.serve(port=0)
    front = await SessionHostClient.connect(port=port)
    flow = None
    try:
        await front.attach()
        active_front = host._front
        assert active_front is not None
        # Drive the transport's flow-control callback, not a platform-specific
        # socket-buffer threshold. The real FRAME write must block in drain().
        flow = cast(asyncio.StreamReaderProtocol, active_front.writer.transport.get_protocol())
        flow.pause_writing()
        child.feed_frame(b"fixture")

        async def backpressured() -> None:
            while not active_front.send_lock.locked():
                await asyncio.sleep(.01)
        await asyncio.wait_for(backpressured(), 2)
        assert active_front.send_lock.locked()
        before = _custody(host, child)
        result = await asyncio.wait_for(front.inspect(), .5)
        assert result["state"] == "responsive"
        assert result["snapshot"]["max_seq"] >= 1
        assert result["snapshot"]["buffer_depth"] >= 1
        assert _custody(host, child) == before
    finally:
        if flow is not None:
            flow.resume_writing()
        await front.close()
        await host.close()


@pytest.mark.asyncio
async def test_dead_child_reconnect_and_turn_silence_are_distinct() -> None:
    child = _FakeChild()
    host = SessionHost(cast(ChildProcess, child))
    port = await host.serve(port=0)
    first = await SessionHostClient.connect(port=port)
    second = None
    try:
        await first.attach()
        await first.write(b'{"id":1,"method":"session/prompt"}\n')
        await asyncio.sleep(.01)
        thinking = await first.inspect()
        assert thinking["snapshot"]["child_alive"]
        assert thinking["snapshot"]["turn_in_flight"]
        assert thinking["snapshot"]["last_child_frame_age_s"] is None
        second = await SessionHostClient.connect(port=port)
        await second.attach()
        before = _custody(host, child)
        assert (await second.inspect())["snapshot"]["turn_in_flight"]
        assert _custody(host, child) == before
        child.finish(5)
        await asyncio.wait_for(host.wait_child(), 1)
        dead = await second.inspect()
        assert dead["snapshot"]["child_alive"] is False
        assert dead["snapshot"]["host_generation"] == thinking["snapshot"]["host_generation"]
        assert dead["state"] == "responsive"
    finally:
        await first.close()
        if second is not None:
            await second.close()
        await host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "blackhole", "legacy", "bad-nonce", "partial", "oversized", "deep-json",
    "bad-child", "bad-generation", "bad-age", "overflow-age", "bad-version", "boolean-pid", "additive",
])
async def test_unknown_legacy_and_invalid_observers_do_not_attach_or_retry(fault: str) -> None:
    requests: list[tuple[proto.MsgType | None, bytes]] = []
    tasks: set[asyncio.Task[Any]] = set()
    release = asyncio.Event()

    async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        try:
            message = await proto.read_message(reader)
            assert message is not None
            requests.append(message)
            if fault == "blackhole":
                await release.wait()
            elif fault == "legacy":
                return
            elif fault == "deep-json":
                payload = b"[" * 1500 + b"0" + b"]" * 1500
                assert inspection_request(payload, "") is None
                await proto.write_message(writer, proto.MsgType.PONG, payload)
            elif fault == "oversized":
                writer.write(struct.pack(">I", 8192) + proto.MsgType.PONG.value)
                await writer.drain()
            else:
                body: dict[str, Any] = {
                    "contract": CONTRACT, "version": 1,
                    "nonce": json.loads(message[1])["nonce"],
                    "host_pid": 1234, "host_generation": "a" * 32,
                    "child_pid": 4242, "child_alive": True, "turn_in_flight": False,
                    "max_seq": 0, "frontend_ack": 0, "buffer_depth": 0,
                    "last_child_frame_age_s": None,
                }
                if fault == "partial":
                    del body["buffer_depth"]
                else:
                    changes: dict[str, dict[str, Any]] = {
                        "bad-nonce": {"nonce": "b" * 32},
                        "bad-child": {"child_pid": 7777},
                        "bad-generation": {"host_generation": ""},
                        "bad-age": {"last_child_frame_age_s": float("nan")},
                        "overflow-age": {"last_child_frame_age_s": 10**400},
                        "bad-version": {"version": 2},
                        "boolean-pid": {"host_pid": True},
                        "additive": {"future": "safe additive field"},
                    }
                    body.update(changes.get(fault, {}))
                await proto.write_message(writer, proto.MsgType.PONG, json.dumps(body).encode())
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(receive, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        result = await probe("127.0.0.1", port, "", child_pid=4242, timeout=.1)
        assert result["state"] == {
            "blackhole": "timed_out", "legacy": "unsupported",
            "bad-version": "unsupported", "additive": "responsive",
        }.get(fault, "invalid")
        assert "future" not in json.dumps(result)
        assert len(requests) == 1 and requests[0][0] == proto.MsgType.PING
    finally:
        release.set()
        server.close()
        await server.wait_closed()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_private_provider_without_inspection_does_not_consume_a_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("unavailable private inspection must not send anything")

    monkeypatch.setattr("agent_remote_driver.session_host.inspection.probe", forbidden)
    private = SessionHostClient(asyncio.StreamReader(), MagicMock())
    assert (await private.inspect()) == {
        "state": "unsupported", "reason": "provider-has-no-read-only-inspection",
    }


def test_semantic_projection_is_bounded_content_free_and_not_ping_progress() -> None:
    session = _session()
    now = time.time()
    log = session.event_log
    log.append("tool_call_start", {"tool_call_id": "build", "title": "Build"}, timestamp=now - 100)
    for i in range(80):
        log.append("agent_thought", {"text": "never disclose fixture thought"}, timestamp=now - 90 + i)
    log.append("agent_message", {"text": "bounded fixture output"}, timestamp=now - 1)
    session.progress = {"phase": "compiling", **{str(i): "x" * 1000 for i in range(30)}}
    result = _semantic(session, "running", now, {"state": "responsive"})
    assert result["freshness"] == "fresh" and result["truncation"] == "truncated"
    assert result["request_state"] == "unknown" and result["terminal_result"] == "unknown"
    assert result["current_phase"] == "compiling"
    assert result["active_tool"]["elapsed_s"] == 100
    assert result["active_tool"]["update_age_s"] is None
    assert result["thought_chunk_count"] == 63 and result["last_thought_age_s"] == 11
    assert result["event_sequence_delta"] == 63
    assert len(result["milestones"]["declared"]) == 16
    assert all(len(item["value"]) <= 160 for item in result["milestones"]["declared"])
    assert result["evidence"]["reference"].startswith("abr1.")
    assert "never disclose" not in json.dumps(result) and "continuity" not in json.dumps(result)
    assert result["proof"] == "observed-events-not-host-round-trip"
    assert result["source"] == "bridge-retained-root-events"
    assert datetime.fromisoformat(result["observed_at"]).timestamp() == pytest.approx(now, rel=0, abs=.000001)
    assert result["scope"] == "bounded-retained-window-not-task-completion"
    assert result["milestones"]["declared"][0]["basis"] == "declared"
    assert result["unknown_result"] == "clipped-pending-input-history"
    assert log.latest_id == 82


@pytest.mark.parametrize("signal,expected", [
    ("absent", ("unknown", "unknown", "no-semantic-signal")),
    ("stale", ("stale", "not-terminal", "stale-semantic-signal")),
    ("success", ("fresh", "success", None)),
    ("failure", ("fresh", "failure", None)),
    ("unknown-end", ("fresh", "unknown", "terminal-outcome-not-reported")),
    ("contradiction", ("fresh", "unknown", "contradictory-sources")),
    ("malformed", ("unknown", "unknown", "malformed-or-partial-projection")),
])
def test_projection_domains_and_reasoned_unknown_results(
    signal: str, expected: tuple[str, str, str | None],
) -> None:
    session = _session()
    now = time.time()
    status = "idle" if signal in {"success", "failure", "unknown-end"} else "running"
    host = {"state": "responsive", "snapshot": {"child_alive": True, "turn_in_flight": False}}
    if signal != "absent":
        data = {"stop_reason": {"success": "end_turn", "failure": "error:fixture"}.get(signal)}
        event = session.event_log.append(
            "turn_complete" if status == "idle" else "agent_message",
            data, timestamp=now - (120 if signal == "stale" else 1),
        )
        if signal == "malformed":
            event.data = None
    if signal == "contradiction":
        host = {"snapshot": {"child_alive": False}}
    result = _semantic(session, status, now, host)
    assert (result["freshness"], result["terminal_result"], result["unknown_result"]) == expected
    assert result["freshness"] in {"fresh", "stale", "unknown"}
    assert result["truncation"] in {"none", "truncated", "unknown"}
    assert result["request_state"] in {"requested", "running", "blocked", "complete", "unknown"}


def test_existing_reference_reader_and_unavailable_redacted_history(tmp_db: Database) -> None:
    from agent_bridge.result_snapshot import expand_owned_result_ref
    from agent_bridge.result_tokens import ResultHistoryChangedError

    session = _session()
    now = time.time()
    tmp_db.create_session("inspection", "fixture", None, ".", "local", "running", now)
    session.event_log = EventLog(db=tmp_db, session_id="inspection")
    log = session.event_log
    log.append("agent_message", {"text": "public fixture output"}, timestamp=now)
    tmp_db.flush()
    result = _semantic(session, "running", now, {})
    token = result["evidence"]["reference"]
    expanded = expand_owned_result_ref(
        db=tmp_db, event_log=log, session_id="inspection", token=token,
    )
    assert expanded["event"]["data"]["text"] == "public fixture output"
    assert tmp_db.get_cursor("observer", "inspection") == 0
    log.rebuild([])
    with pytest.raises(ResultHistoryChangedError):
        expand_owned_result_ref(db=tmp_db, event_log=log, session_id="inspection", token=token)
    unavailable = _semantic(session, "running", now, {})
    assert unavailable["evidence"]["availability"] == "unavailable"
    log.append("agent_thought", {"text": "retained secret-free fixture"}, timestamp=now + 1)
    redacted = _semantic(session, "running", now + 2, {})
    assert redacted["evidence"] == {
        "availability": "unavailable", "reference": None,
        "reason": "redacted-or-unsupported-evidence",
    }
    assert "secret-free fixture" not in json.dumps(redacted)


@pytest.mark.parametrize("status,expected", [("starting", "requested"), ("running", "blocked")])
def test_requested_blocked_and_quiet_tool_metadata(status: str, expected: str) -> None:
    session = _session()
    now = time.time()
    log = session.event_log
    log.append("tool_call_start", {"tool_call_id": "build", "title": "Build"}, timestamp=now - 100)
    log.append("tool_call_update", {"tool_call_id": "build", "status": "in_progress"}, timestamp=now - 90)
    log.append("ask_user_request", {"message": "fixture question"}, timestamp=now - 80)
    result = _semantic(session, status, now, {})
    assert result["request_state"] == expected
    assert result["active_tool"]["elapsed_s"] == 100
    assert result["active_tool"]["update_age_s"] == 90
    assert result["freshness"] == "stale"
    assert result["terminal_result"] == "not-terminal"
    assert result["milestones"]["derived"][0]["basis"] == "derived"


def test_tool_start_is_not_update_evidence() -> None:
    session = _session()
    now = time.time()
    session.event_log.append(
        "tool_call_start", {"tool_call_id": "build", "title": "Build"}, timestamp=now - 100,
    )
    tool = _semantic(session, "running", now, {})["active_tool"]
    assert tool["elapsed_s"] == 100
    assert tool["update_age_s"] is None
    assert tool["update_evidence"] == "no-retained-update"


@pytest.mark.parametrize("boundary", [False, True])
def test_clipped_window_cannot_rule_out_older_blocking_input(boundary: bool) -> None:
    session = _session()
    now = time.time()
    session.event_log.append("ask_user_request", {"tool_call_id": "old"}, timestamp=now - 1)
    for _ in range(64):
        session.event_log.append("agent_message", {"text": "fixture"}, timestamp=now)
    if boundary:
        session.event_log.append("turn_complete", {"stop_reason": "end_turn"}, timestamp=now)
    result = _semantic(session, "idle" if boundary else "running", now, {})
    assert result["truncation"] == "truncated"
    if boundary:
        assert result["request_state"] == "complete" and result["terminal_result"] == "success"
    else:
        assert result["request_state"] == "unknown" and result["terminal_result"] == "unknown"
        assert result["unknown_result"] == "clipped-pending-input-history"


@pytest.mark.parametrize("retained", [list(range(101, 165)), [1, *range(101, 165)]])
def test_captured_tail_references_require_the_existing_detail_reader(
    retained: list[int], tmp_db: Database,
) -> None:
    session = _session()
    now = time.time()
    tmp_db.create_session("inspection", "fixture", None, ".", "local", "running", now)
    events = [SseEvent(index, "agent_message", {"text": "fixture"}, now) for index in retained]
    for event in events:
        tmp_db.append_event("inspection", event.id, event.event, event.data, event.timestamp)
    tmp_db.flush()
    session.event_log = EventLog.from_captured_events(
        events, 165, db=tmp_db, session_id="inspection", acp_session_id=None, worktree_id=None,
    )
    assert session.event_log.snapshot_observation(limit=64)[3][-1].id == 164
    result = _semantic(session, "running", now, {})
    assert result["evidence"] == {
        "availability": "unavailable", "reference": None, "reason": "detail-reader-unavailable",
    }


@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_oversized_numeric_observations_remain_unknown(value: int) -> None:
    session = _session()
    now = time.time()
    event = session.event_log.append("agent_message", {"text": "fixture"}, timestamp=now)
    event.timestamp = value
    result = _semantic(session, "running", now, {})
    assert result["signal_age_s"] is None and result["freshness"] == "unknown"
    assert _age(value, now) is None and _iso(value) is None


@pytest.mark.parametrize("events,expected", [
    ([
        ("ask_user_request", {"tool_call_id": "a"}),
        ("ask_user_request", {"tool_call_id": "b"}),
        ("ask_user_resolved", {"tool_call_id": "b"}),
    ], "blocked"),
    ([
        ("ask_user_request", {"tool_call_id": "a"}),
        ("permission_resolved", {"request_id": "a"}),
    ], "blocked"),
    ([
        ("permission_request", {"request_id": "a"}),
        ("permission_request", {"request_id": "b"}),
        ("permission_resolved", {"request_id": "b"}),
    ], "blocked"),
    ([
        ("ask_user_request", {"request_id": "a", "tool_call_id": "same"}),
        ("ask_user_request", {"request_id": "b", "tool_call_id": "same"}),
        ("ask_user_resolved", {"request_id": "b", "tool_call_id": "same"}),
    ], "blocked"),
    ([
        ("ask_user_request", {"tool_call_id": "a"}),
        ("ask_user_request", {"tool_call_id": "b"}),
        ("ask_user_resolved", {"tool_call_id": "a"}),
        ("ask_user_withdrawn", {"tool_call_id": "b"}),
    ], "running"),
])
def test_pending_input_resolutions_only_clear_their_correlated_family(
    events: list[tuple[str, dict[str, str]]], expected: str,
) -> None:
    session = _session()
    now = time.time()
    for kind, data in events:
        session.event_log.append(kind, data, timestamp=now)
    result = _semantic(session, "running", now, {})
    assert result["request_state"] == expected
    assert result["terminal_result"] == "not-terminal"
    assert session.event_log.latest_id == len(events)


@pytest.mark.asyncio
async def test_http_and_both_cli_aliases_use_copied_core_without_cursor_or_input_effects(
    tmp_path: Path, tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive, digest = build_session_host_bundle(tmp_path / "build")
    copied = tmp_path / "receiving" / archive.name
    copied.parent.mkdir()
    copied.write_bytes(archive.read_bytes())
    # This process has only stdlib + the receiving archive. No source imports,
    # installed suite service, Copilot, mux, credentials or native session.
    code = r'''
import asyncio,json,sys
sys.path.insert(0,sys.argv[1])
from agent_remote_driver.session_host import host as host_module
from agent_remote_driver.session_host.host import SessionHost
retirements=[]
input_frames=[]
host_module._tree_kill=lambda pid,**options: retirements.append(pid)
class Input:
    def write(self,data): input_frames.append(data)
    async def drain(self): pass
class Child:
    pid=4242
    returncode=None
    stdin=Input()
    def __init__(self): self.stdout=asyncio.StreamReader()
    async def wait(self): await asyncio.Event().wait()
async def main():
    host=SessionHost(Child(),nonce="fixture")
    try:
        port=await host.serve(port=0)
        print(json.dumps({"port":port,"module":sys.modules[type(host).__module__].__file__}),flush=True)
        await asyncio.get_running_loop().run_in_executor(None,sys.stdin.readline)
    finally:
        await host.close()
        assert not retirements and not input_frames
        assert host._attachment_epoch==1 and host.ack_cursor==0
asyncio.run(main())
'''
    process = await asyncio.create_subprocess_exec(
        getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-c", code, str(copied),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, creationflags=no_window_flags(),
        cwd=copied.parent,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    front = None
    try:
        ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
        assert ready["module"].startswith(str(copied))
        assert digest in archive.name or digest[:16] in archive.name
        front = await SessionHostClient.connect(port=ready["port"])
        await front.attach(nonce=b"fixture")
        session = _session(front)
        session.last_output_at = session.last_heartbeat_at = time.time()
        tmp_db.create_session("inspection", "fixture", None, ".", "local", "running", time.time())
        session.event_log = EventLog(db=tmp_db, session_id="inspection")
        session.event_log.append("agent_thought", {"text": "private fixture detail"})
        session.event_log.append("agent_message", {"text": "public fixture output"})
        tmp_db.flush()
        app = FastAPI()
        app.include_router(router)
        from agent_bridge.protocol import HTTP_PROTOCOL_MIN_SUPPORTED

        @app.get("/health")
        def health() -> dict[str, int]:
            return {
                "protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
                "min_protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
            }

        app.state.session_manager = SimpleNamespace(
            db=tmp_db, get_session=lambda sid: session if sid == "inspection" else None,
        )
        http = TestClient(app)
        client = BridgeClient("http://fixture", "", connect_grace=0)

        def request(
            method: str, path: str, body: dict[str, Any] | None = None, *,
            params: dict[str, Any] | None = None, **options: Any,
        ) -> dict[str, Any]:
            response = http.request(method, path, json=body, params=params)
            assert response.status_code == 200, response.text
            return cast(dict[str, Any], response.json())

        monkeypatch.setattr(client, "_request", request)
        monkeypatch.setattr(cli, "_get_client", lambda **options: client)
        monkeypatch.setattr(cli, "_caller_id_for", lambda args: "observer")
        before = tmp_db.get_cursor("default", "inspection")
        assert tmp_db.get_controlled_cursor_state("observer", "inspection")["registered"] is False
        for arguments in (
            ["status", "inspection", "--json"],
            ["--json", "inspect", "inspection"],
        ):
            await asyncio.wait_for(asyncio.to_thread(cli.main, [
                *arguments,
            ]), 10)
            result = json.loads(capsys.readouterr().out)
            inspection = result["inspection"]
            layers = inspection["layers"]
            assert layers["daemon_api"]["state"] == "responsive"
            assert layers["session_host"]["state"] == "responsive"
            assert layers["session_host"]["snapshot"]["host_pid"] == process.pid
            assert layers["copilot_child"]["state"] == "alive"
            assert layers["acp_turn"]["state"] == "no-observed-turn"
            assert layers["semantic_progress"]["terminal_result"] == "not-terminal"
            assert result["liveness"] == "active"
            assert result["last_output_at"] and result["last_heartbeat_at"]
            assert "private fixture detail" not in json.dumps(result)
            assert session.event_log.latest_id == 2
            assert tmp_db.get_cursor("default", "inspection") == before
            assert tmp_db.get_cursor("observer", "inspection") == 0
            assert tmp_db.get_controlled_cursor_state("observer", "inspection")["registered"] is False
        await asyncio.to_thread(cli.main, [
            "read", "inspection", "--range", "1:2", "--expand", "thoughts", "--no-color",
        ])
        assert "private fixture detail" in capsys.readouterr().out
        assert tmp_db.get_cursor("observer", "inspection") == 0
        http.close()
    finally:
        if front is not None:
            await front.close()
        if process.returncode is None:
            with suppress(BrokenPipeError, ConnectionResetError):
                process.stdin.write(b"quit\n")
                await process.stdin.drain()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
        stderr = await process.stderr.read()
        assert process.returncode == 0, stderr.decode(errors="replace")


def test_inspect_json_names_older_daemon_inspection_gap_without_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    client = SimpleNamespace(
        get_session_status=lambda sid, caller_id=None: {"session_id": sid, "status": "running"},
    )
    monkeypatch.setattr(cli, "_get_client", lambda **kw: client)
    monkeypatch.setattr(cli, "_caller_id_for", lambda args: "observer")
    cli.main(["inspect", "inspection", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "running"
    assert result["inspection"]["state"] == "unsupported"
    assert result["inspection"]["reason"] == "serving-daemon-has-no-inspection"
