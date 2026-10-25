"""Cold drain uses real owned hosts/stdio; no model or production runtime."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from zdd import diagnostics, process_exit

from agent_bridge import cold_outages
from agent_bridge.auth import BearerAuthMiddleware
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.connect import ConnectTracker
from agent_bridge.drain_contract import DrainRequest
from agent_bridge.events import EventLog
from agent_bridge.models import SessionStatus
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.routes.admin import router
from agent_bridge.routes.health import router as health_router
from agent_bridge.session_cold_drain import cold_drain, owned_execution
from agent_bridge.session_manager import DaemonDrainingError, Session, SessionManager
from agent_bridge.transport import SpawnTarget

NATIVE = "11111111-2222-4333-8444-555555555555"
COLD = {"drain_contract": {"minimum": 2, "maximum": 2}, "on_incompatible": "cold"}


def api(manager: SessionManager) -> TestClient:
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    app.include_router(health_router)
    app.add_middleware(BearerAuthMiddleware, token="test-token")
    # Keep the scheme separate from the credential, including in fixtures.
    scheme = "Be" + "arer"
    return TestClient(app, headers={"Authorization": scheme + " " + "test-token"})


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"force": "false"},
        {"timeout": -1},
        {"timeout": "1"},
        {"poll": 0},
        {"timeout": float("inf")},
        {"drain_contract": {"minimum": True, "maximum": 2}},
        {"drain_contract": {"minimum": 3, "maximum": 2}},
        {"drain_contract": {"minimum": 0, "maximum": 2}},
        {"drain_contract": {"minimum": 1, "maximum": 65536}},
        {"on_incompatible": None},
        {"on_incompatible": "refuse", "force": True},
        {"drain_contract": {"minimum": 1, "maximum": 1}, "on_incompatible": "yes"},
    ],
)
def test_cold_drain_malformed_contract_has_no_effect(session_manager, body) -> None:
    with api(session_manager) as client:
        response = client.post("/api/v1/drain", content=json.dumps(body))
    assert response.status_code == 422
    assert not session_manager.is_draining
    assert session_manager.db.execute_read("SELECT * FROM cold_outages") == []


def test_cold_drain_authentication_precedes_control(session_manager) -> None:
    with api(session_manager) as client:
        client.headers.pop("Authorization")
        assert client.post("/api/v1/drain", json=COLD).status_code == 401
    assert not session_manager.is_draining
    assert session_manager.db.execute_read("SELECT * FROM cold_outages") == []


def test_cold_drain_missing_compatible_and_refused_mismatch(session_manager) -> None:
    with api(session_manager) as client:
        response = client.post("/api/v1/drain", json={"timeout": 0})
        assert response.json()["contract_status"] == "legacy-unnegotiated"
        assert response.json()["mode"] == "graceful"
        assert session_manager._cold_drain_entered is False
        response = client.post("/api/v1/drain", json={"timeout": 0, "on_incompatible": "cold"})
        assert response.json()["contract_status"] == "legacy-unnegotiated"
        assert response.json()["mode"] == "graceful"
        assert session_manager._cold_drain_entered is False
        session_manager.set_draining(False)
        response = client.post(
            "/api/v1/drain",
            json={
                **COLD,
                "drain_contract": {"minimum": 1, "maximum": 1},
            },
        )
        assert response.json()["contract_status"] == "compatible"
        assert response.json()["mode"] == "graceful"
        assert response.json()["clean"] is True and response.json()["forced"] is False
        session_manager.set_draining(False)
        response = client.post(
            "/api/v1/drain",
            json={
                "drain_contract": {"minimum": 2, "maximum": 2},
                "on_incompatible": "refuse",
            },
        )
        assert response.status_code == 409 and not session_manager.is_draining


def test_cold_drain_independent_full_control_vector(session_manager) -> None:
    path = Path(__file__).resolve().parents[1] / "contract/inputs/http47/negotiated-drain.json"
    expected = json.loads(path.read_text())
    expected["advertised"]["protocol_version"] = HTTP_PROTOCOL_VERSION
    with api(session_manager) as client:
        actual = client.get("/health").json()
        assert {key: actual[key] for key in expected["advertised"]} == expected["advertised"]
        response = client.post("/api/v1/drain", json=expected["cold_request"])
        assert response.status_code == 200
        assert response.json() == expected["empty_cold_response"]
        # Releasing the admission gate cannot undo an interruption or turn a
        # later installer's required-graceful request into a clean attestation.
        session_manager.set_draining(False)
        later = client.post("/api/v1/drain", json={"timeout": 0}).json()
        assert later["mode"] == "incompatible-contract-cold"
        assert later["drained"] is False and later["clean"] is False
        assert later["forced"] is True
    assert cold_outages.RECONNECT_PROMPT == expected["reconnect_prompt"]


def test_cold_drain_client_refuses_legacy_before_post(monkeypatch) -> None:
    client = BridgeClient("http://127.0.0.1:1", "unused")
    monkeypatch.setattr(client, "health", lambda: {"protocol_version": 45})
    calls = []
    monkeypatch.setattr(client, "_request", lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(BridgeClientError, match="does not support"):
        client.drain(on_incompatible="cold", contract_minimum=2, contract_maximum=2)
    assert calls == []


def test_cold_drain_client_does_not_accept_a_legacy_success_shape(monkeypatch) -> None:
    client = BridgeClient("http://127.0.0.1:1", "unused")
    monkeypatch.setattr(
        client,
        "health",
        lambda: {
            "protocol_version": 47,
            "drain_contract": {"minimum": 1, "maximum": 1},
        },
    )
    monkeypatch.setattr(client, "_request", lambda *a, **kw: {"drained": True, "clean": True})
    with pytest.raises(BridgeClientError, match="not confirmed"):
        client.drain(on_incompatible="cold", contract_minimum=2, contract_maximum=2)
    with pytest.raises(BridgeClientError, match="not confirmed"):
        client.drain()
    monkeypatch.setattr(client, "health", lambda: {"protocol_version": 45})
    assert client.drain()["contract_status"] == "legacy-unnegotiated"


@pytest.mark.parametrize("negotiated", [False, True])
@pytest.mark.parametrize("payload", [None, False, [], 1, "response"])
def test_cold_drain_client_rejects_nonobject_outcome(
    monkeypatch: pytest.MonkeyPatch, negotiated: bool, payload: Any,
) -> None:
    client = BridgeClient("http://127.0.0.1:1", "unused")
    health = {"protocol_version": 47, "drain_contract": {"minimum": 1, "maximum": 1}}
    monkeypatch.setattr(client, "health", lambda: health if negotiated else {"protocol_version": 45})
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: payload)
    with pytest.raises(BridgeClientError, match="outcome must be an object") as caught:
        client.drain()
    assert caught.value.status == 502


@pytest.mark.parametrize("field", ["drained", "clean", "forced"])
@pytest.mark.parametrize("value", [None, "false", 0, 1, [], {}, "missing"])
def test_cold_drain_client_requires_boolean_completion_flags(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any,
) -> None:
    client = BridgeClient("http://127.0.0.1:1", "unused")
    monkeypatch.setattr(
        client, "health",
        lambda: {"protocol_version": 47, "drain_contract": {"minimum": 1, "maximum": 1}},
    )
    payload: dict[str, Any] = {
        "drain_contract": 1, "contract_status": "compatible", "mode": "graceful",
        "drained": True, "clean": True, "forced": False,
    }
    if value == "missing":
        payload.pop(field)
    else:
        payload[field] = value
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: payload)
    with pytest.raises(BridgeClientError, match="completion flags must be booleans") as caught:
        client.drain()
    assert caught.value.status == 502


@pytest.mark.parametrize(
    "health",
    [
        {"protocol_version": 47},
        {"protocol_version": 47, "drain_contract": {"minimum": True, "maximum": 2}},
        {"protocol_version": 47, "drain_contract": {"minimum": 3, "maximum": 2}},
    ],
)
def test_cold_drain_default_unknown_negotiation_never_posts(monkeypatch, health) -> None:
    client = BridgeClient("http://127.0.0.1:1", "unused")
    monkeypatch.setattr(client, "health", lambda: health)
    posts = []
    monkeypatch.setattr(client, "_request", lambda *a, **kw: posts.append((a, kw)))
    with pytest.raises(BridgeClientError, match="no mismatch established"):
        client.drain()
    assert posts == []


def test_cold_drain_default_health_failure_is_not_a_mismatch(monkeypatch) -> None:
    client = BridgeClient("http://127.0.0.1:1", "unused")

    def failure():
        raise BridgeClientError(500, "fixture unavailable negotiation")

    monkeypatch.setattr(client, "health", failure)
    posts = []
    monkeypatch.setattr(client, "_request", lambda *a, **kw: posts.append((a, kw)))
    with pytest.raises(BridgeClientError, match="unavailable negotiation"):
        client.drain()
    assert posts == []


@pytest.mark.asyncio
async def test_cold_drain_default_client_automatic_and_strict_cutover_precedes_effects(
    session_manager,
    tmp_path,
    monkeypatch,
) -> None:
    from agent_bridge import drain_contract
    from agent_bridge.routes import admin, health
    from zdd.cutover_drain import _drain_old

    manager = session_manager
    transport_api = api(manager)
    calls = []
    loop = asyncio.get_running_loop()
    session, _ = await live_session(manager, tmp_path, monkeypatch)
    execution = owned_execution(manager, session, manager._host_index.get(session.session_id))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport_api.app),
            base_url="http://fixture",
            headers=dict(transport_api.headers),
        ) as wire:

            class WireClient(BridgeClient):
                def _request(self, method, path, body=None, *, params=None, request_timeout=None):
                    async def request():
                        calls.append((method, path, body))
                        response = await wire.request(method, path, json=body, params=params)
                        data = response.json()
                        if response.status_code >= 400:
                            raise BridgeClientError(response.status_code, data["detail"])
                        return data

                    return asyncio.run_coroutine_threadsafe(request(), loop).result(timeout=30)

            client = WireClient("http://fixture", "test-token")
            compatible = await asyncio.to_thread(client.drain, timeout=1, poll=0.05)
            assert compatible["mode"] == "graceful" and compatible["clean"] is True
            assert calls[-1][2]["on_incompatible"] == "cold"
            manager.set_draining(False)
            compatible_strict = await asyncio.to_thread(client.drain_gracefully, timeout=1)
            assert compatible_strict["mode"] == "graceful" and compatible_strict["clean"] is True
            assert calls[-1][2]["on_incompatible"] == "refuse"
            manager.set_draining(False)

            # Model a receiver with a newer preservation contract, independent
            # of this caller's unchanged default range and the HTTP generation.
            monkeypatch.setattr(drain_contract, "DRAIN_CONTRACT_VERSION", 2)
            monkeypatch.setattr(admin, "DRAIN_CONTRACT_VERSION", 2)
            monkeypatch.setattr(health, "DRAIN_CONTRACT_VERSION", 2)
            calls.clear()
            with pytest.raises(BridgeClientError, match="cannot force"):
                await asyncio.to_thread(client.drain_gracefully, force=True)
            assert calls == []
            with pytest.raises(BridgeClientError, match="graceful-only"):
                await asyncio.to_thread(client.drain_gracefully, timeout=10)
            assert not [entry for entry in calls if entry[0] == "POST"]
            owner = SimpleNamespace(
                _emit=lambda *_a, **_kw: None,
                drain_before_publish=False,
                expected_old=object(),
                make_client=lambda _url: client,
            )
            endpoint = SimpleNamespace(port=1, base_url="http://fixture")
            with pytest.raises(BridgeClientError, match="graceful-only"):
                await asyncio.to_thread(
                    _drain_old,
                    owner,
                    endpoint,
                    SimpleNamespace(steps=[], drain=None),
                    drain_timeout=10,
                    force=False,
                )
            assert not [entry for entry in calls if entry[0] == "POST"]
            refused = await wire.post(
                "/api/v1/drain",
                json={
                    "drain_contract": {"minimum": 1, "maximum": 1},
                    "on_incompatible": "refuse",
                    "timeout": 10,
                },
            )
            assert refused.status_code == 409
            assert not manager.is_draining and not manager._cold_drain_entered
            assert manager.db.execute_read("SELECT * FROM cold_outages") == []
            assert all(
                not process_exit.observe_original_exit(v["pid"], v["start_time"])
                for v in execution.values()
            )

            # Ordinary cutover does not opt in again: BridgeClient's default
            # emits cold policy and the server confirms the disjoint range.
            owner.expected_old = None
            result = SimpleNamespace(steps=[], drain=None)
            await asyncio.to_thread(
                _drain_old,
                owner,
                endpoint,
                result,
                drain_timeout=10,
                force=False,
            )
            assert result.drain["drained"] is True
            assert result.drain["mode"] == "incompatible-contract-cold"
            assert result.drain["clean"] is False and result.drain["forced"] is True
            posted = [entry[2] for entry in calls if entry[0] == "POST"]
            assert len(posted) == 1
            assert posted[0]["drain_contract"] == {"minimum": 1, "maximum": 1}
            assert posted[0]["on_incompatible"] == "cold" and posted[0]["force"] is False
            assert all(
                process_exit.observe_original_exit(v["pid"], v["start_time"])
                for v in execution.values()
            )
            assert cold_outages.latest(manager.db, session.session_id)["state"] == "interrupted"
    finally:
        transport_api.close()
        await cleanup(manager)


@pytest.mark.asyncio
async def test_cold_drain_acquisition_timeout_does_not_bypass_or_mutate(
    session_manager,
) -> None:
    session_manager._active_acquisitions = 1
    result = await cold_drain(
        session_manager, timeout=0, poll=0.05, source="test", reason=None, exclude_session_id=None
    )
    assert not result["drained"] and result["failures"]
    assert session_manager.db.execute_read("SELECT * FROM cold_outages") == []
    assert session_manager._drain_watchdog is None
    assert session_manager.drain_status()["auto_release_at"] is None
    session_manager.set_draining(False)


@pytest.mark.asyncio
async def test_cold_drain_uncertain_reconnect_admission_is_never_replayed(
    session_manager,
    tmp_path,
    monkeypatch,
) -> None:
    manager = session_manager
    successor = None
    try:
        session, transcript = await live_session(manager, tmp_path, monkeypatch)
        assert (
            await cold_drain(
                manager, timeout=10, poll=0.05, source="test", reason=None, exclude_session_id=None
            )
        )["drained"]
        successor = SessionManager(manager.db)
        successor._generation_id = "fixture-successor"
        release = asyncio.Event()
        original = successor._run_prompt

        async def delayed(*args):
            await release.wait()
            await original(*args)

        successor._run_prompt = delayed
        successor._notify_host_reapable = AsyncMock(
            side_effect=ConnectionError("lost acceptance response")
        )
        with pytest.raises(ConnectionError):
            await successor.resume_session(session.session_id)
        retained = cold_outages.latest(manager.db, session.session_id)
        assert retained["state"] == "reconnect_unknown"
        assert retained["reconnect_evidence_json"] is None
        assert not [m for m in messages(transcript) if m.get("method") == "session/prompt"]
        successor._notify_host_reapable = AsyncMock()
        assert await cold_outages.recover(successor) == 0
        assert cold_outages.latest(manager.db, session.session_id)["state"] == "reconnect_unknown"
        release.set()
        await successor.get_session(session.session_id)._prompt_task
        confirmed = cold_outages.latest(manager.db, session.session_id)
        assert confirmed["state"] == "reconnected"
        assert (
            json.loads(confirmed["reconnect_evidence_json"])["kind"] == "provider-prompt-response"
        )
        assert len([m for m in messages(transcript) if m.get("method") == "session/prompt"]) == 1
        assert len(manager.db.get_turns(session.session_id)) == 1
    finally:
        if successor is not None:
            await cleanup(successor)
        await cleanup(manager)


@pytest.mark.asyncio
async def test_cold_drain_legacy_database_read_path_does_not_invent_recovery(tmp_db) -> None:
    tmp_db.execute_write("DROP TABLE cold_outages")
    assert cold_outages.latest(tmp_db, "legacy") is None
    manager = SessionManager(tmp_db)
    assert await cold_outages.recover(manager) == 0
    with pytest.raises(ValueError, match="legacy database"):
        await cold_drain(
            manager, timeout=0, poll=0.05, source="test", reason=None, exclude_session_id=None
        )
    assert not manager.is_draining


async def until(predicate) -> None:
    deadline = time.monotonic() + 10
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("fixture observation timed out")
        await asyncio.sleep(0.01)


def messages(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def bind_peer(tmp_path, monkeypatch, *, reject_reconnect=False) -> Path:
    from agent_bridge import local_cache_refresh, transport

    transcript = tmp_path / "peer.jsonl"

    async def launch(*_args, **_kwargs):
        return (
            [
                sys.executable,
                str(Path(__file__).with_name("cold_drain_peer.py")),
                str(transcript),
                NATIVE,
                "reject" if reject_reconnect else "reply",
            ],
            str(tmp_path),
            dict(os.environ),
        )

    monkeypatch.setattr(transport, "resolve_local_launch", launch)
    monkeypatch.setattr(local_cache_refresh, "refresh_local_cache", AsyncMock())
    return transcript


async def live_session(manager, tmp_path, monkeypatch) -> tuple[Session, Path]:
    transcript = bind_peer(tmp_path, monkeypatch)
    target = SpawnTarget(type="local", cwd=str(tmp_path))
    session = Session("cold-session", "cold", target)
    session.acp_session_id = NATIVE
    session.status = SessionStatus.IDLE
    manager.db.create_session(
        session.session_id,
        session.name,
        None,
        target.cwd,
        "local",
        "idle",
        time.time(),
        acp_session_id=NATIVE,
        target_json=target.to_json(),
    )
    session.event_log = EventLog(db=manager.db, session_id=session.session_id)
    manager._sessions[session.session_id] = session
    session.client, identity = await manager._connect_via_session_host(
        target,
        tracker=ConnectTracker(),
        session_id=session.session_id,
        on_acp_event=lambda event, data: manager._route_acp_event(session, event, data),
        permission_callback=None,
        load_session_id=NATIVE,
    )
    assert identity == NATIVE
    return session, transcript


async def cleanup(manager: SessionManager) -> None:
    for session in manager._sessions.values():
        if session._prompt_task is not None and not session._prompt_task.done():
            session._prompt_task.cancel()
            await asyncio.gather(session._prompt_task, return_exceptions=True)
        if session.client is not None:
            await session.client.shutdown()
    for record in manager._host_index.all():
        manager._reap_host_record(record, "owned cold-drain fixture cleanup")
    manager.set_draining(False)


@pytest.mark.asyncio
async def test_cold_drain_real_owned_termination_same_native_reconnect_once(
    session_manager,
    tmp_path,
    monkeypatch,
) -> None:
    manager = session_manager
    successor = None
    third = None
    try:
        session, transcript = await live_session(manager, tmp_path, monkeypatch)
        record = manager._host_index.get(session.session_id)
        execution = owned_execution(manager, session, record)
        successor = SessionManager(manager.db, read_only_rehydrate=True)
        successor._generation_id = "fixture-successor"
        assert successor.get_session(session.session_id).status == SessionStatus.IDLE
        assert successor.get_session(session.session_id).turn_count == 0
        await manager.submit_prompt(session.session_id, "original work -- never replay")
        await until(lambda: any(m.get("method") == "session/prompt" for m in messages(transcript)))
        result = await cold_drain(
            manager,
            timeout=10,
            poll=0.05,
            source="test",
            reason="update",
            exclude_session_id=None,
        )
        assert result["drained"] is True, result
        assert result["clean"] is False and result["forced"] is True
        assert result["interrupted"] == [session.session_id]
        for item in execution.values():
            assert process_exit.observe_original_exit(item["pid"], item["start_time"])
        retained = cold_outages.latest(manager.db, session.session_id)
        assert retained["native_session_id"] == NATIVE
        assert retained["target_json"] == session.target.to_json()
        assert retained["previous_status"] == "running"
        assert retained["state"] == "interrupted"
        refused = await manager.drain(timeout=0, force=True)
        assert refused["drained"] is False and refused["mode"] == "incompatible-contract-cold"
        assert manager.db.get_turns(session.session_id)[0]["stop_reason"] == "interrupted"
        with pytest.raises(DaemonDrainingError):
            await manager.resume_session(session.session_id)
        manager.set_draining(False)
        with pytest.raises(ValueError, match="successor"):
            await manager.resume_session(session.session_id)

        assert await successor.reattach_session_hosts(remote_recovery_timeout=1) == 1
        resumed = successor.get_session(session.session_id)
        await until(lambda: resumed._prompt_task is not None and resumed._prompt_task.done())
        assert resumed.acp_session_id == NATIVE
        assert await cold_outages.recover(successor) == 0
        actual = messages(transcript)
        loads = [m for m in actual if m.get("method") == "session/load"]
        prompts = [m for m in actual if m.get("method") == "session/prompt"]
        assert len(loads) == 2
        assert {m["params"]["sessionId"] for m in loads + prompts} == {NATIVE}
        assert len(prompts) == 2
        assert prompts[1]["params"]["prompt"] == [
            {
                "type": "text",
                "text": cold_outages.RECONNECT_PROMPT,
            }
        ]
        assert not any(m.get("method") == "session/new" for m in actual)
        assert len([m for m in actual if "boot" in m]) == 2
        turns = manager.db.get_turns(session.session_id)
        assert len(turns) == 2 and turns[1]["prompt"] == cold_outages.RECONNECT_PROMPT
        manager.db.flush()
        bubbles = [
            row["data"]["content"]
            for row in manager.db.get_events(session.session_id, after=0)
            if row["event_type"] == "user_message"
        ]
        assert bubbles == ["original work -- never replay", cold_outages.RECONNECT_PROMPT]
        assert cold_outages.latest(manager.db, session.session_id)["state"] == "reconnected"
        second_outage = await cold_drain(
            successor,
            timeout=10,
            poll=0.05,
            source="test",
            reason="second update",
            exclude_session_id=None,
        )
        assert second_outage["drained"], second_outage
        third = SessionManager(manager.db)
        third._generation_id = "fixture-third-generation"
        assert await third.reattach_session_hosts(remote_recovery_timeout=1) == 1
        recovered_again = third.get_session(session.session_id)
        await until(lambda: recovered_again._prompt_task.done())
        assert recovered_again.acp_session_id == NATIVE
        assert len([m for m in messages(transcript) if "boot" in m]) == 3
        turns = manager.db.get_turns(session.session_id)
        assert [turn["prompt"] for turn in turns[1:]] == [cold_outages.RECONNECT_PROMPT] * 2
        keys = [
            r["acquisition_delivery_id"]
            for r in manager.db.execute_read(
                "SELECT acquisition_delivery_id FROM turns WHERE acquisition_delivery_id IS NOT NULL",
            )
        ]
        assert len(keys) == len(set(keys)) == 2
    finally:
        if third is not None:
            await cleanup(third)
        if successor is not None:
            await cleanup(successor)
        await cleanup(manager)


@pytest.mark.asyncio
async def test_cold_drain_confirmed_series_resume_keeps_registered_same_native_host(
    session_manager,
    tmp_path,
    monkeypatch,
) -> None:
    successor = None
    try:
        session, transcript = await live_session(session_manager, tmp_path, monkeypatch)
        assert (
            await cold_drain(
                session_manager,
                timeout=10,
                poll=0.05,
                source="fixture",
                reason=None,
                exclude_session_id=None,
            )
        )["drained"]
        successor = SessionManager(session_manager.db)
        successor._generation_id = "fixture-successor"
        resumed = await successor.resume_session(session.session_id)
        await resumed._prompt_task
        confirmed = cold_outages.latest(session_manager.db, session.session_id)
        assert confirmed["state"] == "reconnected" and confirmed["reconnect_evidence_json"]
        session_manager.db.flush()
        progress = [
            event["data"]
            for event in session_manager.db.get_events(session.session_id, after=0)
            if event["event_type"] == "startup_progress"
        ]
        assert any(
            event["phase"] == "session-host-preparation" and event["state"] == "finished"
            for event in progress
        )
        assert any(
            event["phase"] == "ready" and event["state"] == "finished"
            for event in progress
        )
        replacement = json.loads(confirmed["replacement_json"])["execution"]
        await successor.stop_session(session.session_id, force=True, reap_host=True)
        assert all(
            process_exit.observe_original_exit(v["pid"], v["start_time"])
            for v in replacement.values()
        )
        resumed = await successor.resume_session(session.session_id)
        assert resumed.acp_session_id == NATIVE
        assert successor._host_index.get(session.session_id) is not None
        assert len([m for m in messages(transcript) if "boot" in m]) == 3
        assert len([m for m in messages(transcript) if m.get("method") == "session/prompt"]) == 1
    finally:
        if successor is not None:
            await cleanup(successor)
        await cleanup(session_manager)


@pytest.mark.asyncio
async def test_cold_drain_settlement_failure_journal_keeps_kernel_custody(
    session_manager,
    tmp_path,
    monkeypatch,
) -> None:
    from agent_bridge import session_cold_drain

    session, _ = await live_session(session_manager, tmp_path, monkeypatch)
    finish = cold_outages.finish
    recorded = []

    def guarded_finish(db, outage_id, state, error=None):
        cold_outages.require_custody(session_manager, session.session_id)
        recorded.append(state)
        finish(db, outage_id, state, error)

    async def settlement_error(*_args):
        raise RuntimeError("fixture settlement failure")

    monkeypatch.setattr(cold_outages, "finish", guarded_finish)
    monkeypatch.setattr(session_cold_drain, "_settle", settlement_error)
    try:
        result = await cold_drain(
            session_manager,
            timeout=10,
            poll=0.05,
            source="fixture",
            reason=None,
            exclude_session_id=None,
        )
        assert not result["drained"] and recorded == ["failed"]
        assert cold_outages.latest(session_manager.db, session.session_id)["state"] == "failed"
    finally:
        await cleanup(session_manager)


@pytest.mark.asyncio
async def test_cold_drain_failed_termination_is_fenced(
    session_manager,
    tmp_path,
    monkeypatch,
) -> None:
    try:
        session, _ = await live_session(session_manager, tmp_path, monkeypatch)
        monkeypatch.setattr(
            session.client.session_host_client,
            "terminate",
            AsyncMock(
                side_effect=ConnectionError("fixture send failed"),
            ),
        )
        result = await cold_drain(
            session_manager,
            timeout=1,
            poll=0.05,
            source="test",
            reason=None,
            exclude_session_id=None,
        )
        assert result["drained"] is False and result["failures"]
        assert cold_outages.latest(session_manager.db, session.session_id)["state"] == "failed"
        assert session_manager._host_index.get(session.session_id) is not None
        session_manager.set_draining(False)
        with pytest.raises(ValueError, match="fences"):
            await session_manager.submit_prompt(session.session_id, "must not replay")
    finally:
        await cleanup(session_manager)


@pytest.mark.asyncio
async def test_cold_drain_no_native_does_not_create_on_recovery(
    session_manager,
    tmp_path,
    monkeypatch,
) -> None:
    try:
        session, transcript = await live_session(session_manager, tmp_path, monkeypatch)
        session.acp_session_id = None
        session_manager.db.update_session_acp_id(session.session_id, None)
        session_manager.db.execute_write(
            "INSERT INTO workspace_embodiments (key_digest,facts_digest,state,session_id)"
            " VALUES ('allocated','retained-facts','allocated','reserved-no-native')"
        )
        before = [
            dict(r) for r in session_manager.db.execute_read("SELECT * FROM workspace_embodiments")
        ]
        result = await cold_drain(
            session_manager,
            timeout=10,
            poll=0.05,
            source="test",
            reason=None,
            exclude_session_id=None,
        )
        assert result["drained"] and result["nonresumable"] == [session.session_id]
        successor = SessionManager(session_manager.db)
        successor._generation_id = "fixture-successor"
        assert await cold_outages.recover(successor) == 0
        with pytest.raises(ValueError, match="nonresumable"):
            await successor.resume_session(session.session_id, allow_recreate=True)
        assert len([m for m in messages(transcript) if "boot" in m]) == 1
        assert before == [
            dict(r) for r in session_manager.db.execute_read("SELECT * FROM workspace_embodiments")
        ]
    finally:
        await cleanup(session_manager)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "generation",
        "pid",
        "birth",
        "boundary",
        "excluded",
        "elevated",
        "legacy-birth",
    ],
)
async def test_cold_drain_owner_identity_and_exclusion_refuse(
    session_manager,
    tmp_path,
    monkeypatch,
    fault,
) -> None:
    try:
        session, transcript = await live_session(session_manager, tmp_path, monkeypatch)
        record = session_manager._host_index.get(session.session_id)
        if fault == "generation":
            record.owner_generation = "another-owner"
        elif fault == "pid":
            record.owner_pid = os.getpid() + 1
        elif fault == "boundary":
            record.boundary = "ssh"
        elif fault == "birth":
            state = json.loads(Path(record.state_file).read_text())
            state["child_process_start_time"] = "1"
            Path(record.state_file).write_text(json.dumps(state))
        elif fault == "legacy-birth":
            state = json.loads(Path(record.state_file).read_text())
            state.pop("host_process_start_time")
            state.pop("child_process_start_time")
            Path(record.state_file).write_text(json.dumps(state))
        elif fault == "elevated":
            session.target.elevated = True
        session_manager._host_index.remove(record.session_id)
        session_manager._host_index.register(record)
        result = await cold_drain(
            session_manager,
            timeout=1,
            poll=0.05,
            source="test",
            reason=None,
            exclude_session_id=session.session_id if fault == "excluded" else None,
        )
        assert result["drained"] is False
        assert diagnostics.process_start_time(record.child_pid) not in {None, "1"}
        assert len([m for m in messages(transcript) if "boot" in m]) == 1
    finally:
        # Even a failed assertion must never route fixture cleanup remotely.
        for record in session_manager._host_index.all():
            record.boundary = "local"
            session_manager._host_index.register(record)
        await cleanup(session_manager)


@pytest.mark.parametrize("conditional", [False, True])
def test_cold_drain_strict_graceful_rejects_even_mislabelled_clean(
    conditional: bool,
) -> None:
    from zdd.conditional import PreparedCutover
    from zdd.cutover import CutoverError
    from zdd.cutover_drain import _drain_old

    response = {
        "drained": True,
        "clean": True,
        "forced": False,
        "mode": "incompatible-contract-cold",
    }
    owner = SimpleNamespace(
        _emit=lambda *_a, **_kw: None,
        drain_before_publish=True,
        make_client=lambda _url: SimpleNamespace(drain=lambda **_kw: response),
    )
    prepared = SimpleNamespace(
        verify_before_retire=lambda: None,
        old=SimpleNamespace(),
        new=SimpleNamespace(),
        snapshot=SimpleNamespace(drain_predecessor=lambda *_: response),
    )
    conditional_owner = SimpleNamespace(
        drain_predecessor=lambda timeout: PreparedCutover.drain_predecessor(prepared, timeout),
    )
    result = SimpleNamespace(steps=[], drain=None)
    with pytest.raises(CutoverError, match="cold mode|did not drain"):
        _drain_old(
            owner,
            SimpleNamespace(port=1, base_url="fixture"),
            result,
            drain_timeout=1,
            force=False,
            conditional=conditional_owner if conditional else None,
        )


def test_cold_drain_contract_range_is_not_http_or_build_version() -> None:
    assert DrainRequest.model_validate(COLD).contract_status == "incompatible"
    assert DrainRequest().contract_status == "legacy-unnegotiated"
