"""Qualified peer routes preserve host/project ownership and call/response."""

from __future__ import annotations

import argparse
import asyncio
import time
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ssh_manager import CarrierRemoteError, Envelope, EnvelopeType

from agent_bridge import __main__ as cli
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.db import Database
from agent_bridge.live_peer_address import LivePeerAddress
from agent_bridge.live_representation import LiveEventStore
from agent_bridge.remote_operations import CarrierRequestRouter, RemoteOperationService
from agent_bridge.routes import live_sessions, remote


class _HttpClient(BridgeClient):
    def __init__(self, app):
        self.http = TestClient(app)

    def daemon_supports(self, version):
        return version <= 33

    def _request(self, method, path, data=None, *, params=None, request_timeout=None):
        response = self.http.request(method, path, json=data, params=params)
        if response.status_code >= 400:
            raise BridgeClientError(response.status_code, response.json().get("detail"))
        return response.json()


def _bridge(db):
    app = FastAPI()
    app.state.db = db
    app.state.live_event_store = LiveEventStore()
    app.include_router(live_sessions.router)
    app.include_router(remote.router)
    return app, _HttpClient(app)


def _register(db, sid, machine, project="project-a", worktree="wt-a", *, now=None):
    assert db.register_live_session(
        sid, machine=machine, cwd=None, worktree_id=worktree, repo=project,
        branch=None, pid=None, role=None, now=now if now is not None else time.time(),
    ) == "live"


@pytest.mark.parametrize("project", ["project-a", "owner/repo"])
def test_qualified_handle_survives_turnover_and_database_reopen(tmp_path, project):
    path = tmp_path / "bridge.db"
    db = Database(path)
    try:
        _register(db, "old", "host-a", project=project)
        _register(db, "new", "host-a", project=project, now=time.time() + 1)
        _register(db, "other-project", "host-a", project="project-b", now=time.time() + 2)
        _register(db, "other-machine", "host-b", project=project, now=time.time() + 3)
        assert db.resolve_live_session(f"live:host-a:{project}:wt-a", now=time.time())["session_id"] == "new"
    finally:
        db.close()
    db = Database(path)
    try:
        assert db.resolve_live_session(f"live:host-a:{project}:wt-a", now=time.time())["session_id"] == "new"
        assert db.resolve_live_session("live:host-a:missing:wt-a", now=time.time()) is None
        assert db.resolve_live_session(f"live:host-a:{project}:wt-a", now=time.time() + 3600) is None
    finally:
        db.close()


@pytest.mark.parametrize("handle", [
    "live:", "live:a:b", "live:a:b:c:d", "live:a:b:../c", "live:a/b:c:d",
    "live:a:b:c/d", "live:a:/b:c", "live:a:b/:c", "live:a:b//c:d", "live:a:b/../c:d",
])
def test_invalid_explicit_address_never_spawns(monkeypatch, capsys, handle):
    monkeypatch.setattr(cli, "_get_client", lambda: SimpleNamespace())
    monkeypatch.setattr(cli, "_resolve_target", lambda *_a, **_k: pytest.fail("agent fallback"))
    with pytest.raises(SystemExit) as refused:
        cli._cmd_send(argparse.Namespace(target=handle, prompt="hello", json=False))
    assert refused.value.code == 69
    assert "peer address" in capsys.readouterr().err


@pytest.mark.guard
def test_unavailable_explicit_address_never_spawns(monkeypatch):
    def unavailable(*_a, **_k):
        raise BridgeClientError(404, "no live session for handle")

    client = SimpleNamespace(resolve_live_session=lambda _: {}, send_remote_live_message=unavailable)
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    monkeypatch.setattr(cli, "_live_reply_to", lambda _: "live:host-a:project-a:wt-a")
    monkeypatch.setattr(cli, "_live_sender_label", lambda _: "wt-a")
    monkeypatch.setattr(cli, "_resolve_target", lambda *_a, **_k: pytest.fail("agent fallback"))
    with pytest.raises(SystemExit) as refused:
        cli._cmd_send(argparse.Namespace(target="live:host-b:project-a:wt-b", prompt="hello", json=False))
    assert refused.value.code == 69


@pytest.mark.parametrize("idempotency_key", [pytest.param(None, marks=pytest.mark.guard), "peer-call-1"])
@pytest.mark.parametrize("project", ["project-a", "owner/repo"])
def test_two_bridge_call_and_reverse_reply_preserve_addresses(
    tmp_path, monkeypatch, capsys, idempotency_key, project,
):
    a_db, b_db = Database(tmp_path / "a.db"), Database(tmp_path / "b.db")
    try:
        a_app, a_client = _bridge(a_db)
        b_app, b_client = _bridge(b_db)
        _register(a_db, "a-old", "host-a", project=project)
        _register(b_db, "b-current", "host-b", project=project, worktree="wt-b")

        class Carrier:
            def __init__(self, client):
                self.router = CarrierRequestRouter(lambda: client)

            async def request(self, payload, **_kwargs):
                result = await self.router(Envelope(EnvelopeType.REQUEST, payload=payload))
                if result.type is EnvelopeType.ERROR:
                    raise CarrierRemoteError(result.payload)
                assert result.type is EnvelopeType.RESPONSE, result.payload
                return result

        for app, client, host, target_client in (
            (a_app, a_client, "host-b", b_client), (b_app, b_client, "host-a", a_client),
        ):
            service = RemoteOperationService(SimpleNamespace())
            carrier = Carrier(target_client)

            async def lease(requested_host, *, expected_host=host, carrier=carrier):
                assert requested_host == expected_host
                return SimpleNamespace(carrier=carrier, release=_release)

            service._lease = lease
            app.state.remote_operations = service

        original_wait = live_sessions.await_turn_reply

        async def reply(log, *, after, timeout):
            log.append("agent_message", {"text": "peer response"})
            log.append("turn_complete", {"stop_reason": "end_turn"})
            return await original_wait(log, after=after, timeout=timeout)

        monkeypatch.setattr(live_sessions, "await_turn_reply", reply)
        monkeypatch.setattr(cli, "_get_client", lambda: a_client)
        monkeypatch.setattr(cli, "_live_sender_label", lambda _: "wt-a")
        monkeypatch.setattr(cli, "_worktrees_get", lambda _: "/fixture/wt-a")
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "a-old")
        args = argparse.Namespace(
            target=f"live:host-b:{project}:wt-b", prompt="call", json=True,
            no_wait=False, reply_timeout=2.0, idempotency_key=idempotency_key,
        )
        assert a_client.resolve_remote_live_session("host-b", args.target)["session_id"] == "b-current"
        cli._cmd_send(args)
        output = capsys.readouterr().out
        assert '"replied": true' in output and '"reply": "peer response"' in output
        fixture_path = (Path(__file__).parents[1] / "contract" / "fixtures" / "http"
                        / "current" / "remote-peer-generation-33.json")
        expected = json.loads(fixture_path.read_text(encoding="utf-8"))["response"]
        observed = json.loads(output)
        assert {key: observed[key] for key in expected} == expected
        received = b_db.list_pending_live_messages("b-current")
        assert received[0]["reply_to"] == f"live:host-a:{project}:wt-a"
        _register(a_db, "a-new", "host-a", project=project, now=time.time() + 1)
        monkeypatch.setattr(cli, "_get_client", lambda: b_client)
        monkeypatch.setattr(cli, "_live_sender_label", lambda _: "wt-b")
        monkeypatch.setattr(cli, "_worktrees_get", lambda _: "/fixture/wt-b")
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "b-current")
        args.target, args.prompt = received[0]["reply_to"], "reverse reply"
        cli._cmd_send(args)
        output = capsys.readouterr().out
        assert '"replied": true' in output and '"reply": "peer response"' in output
        assert a_db.list_pending_live_messages("a-old") == []
        assert a_db.list_pending_live_messages("a-new")[0]["body"] == "reverse reply"
        if idempotency_key:
            cli._cmd_send(args)
            assert len(a_db.list_pending_live_messages("a-new")) == 1
        args.expected_session_id, args.idempotency_key = "a-old", None
        with pytest.raises(SystemExit) as refused:
            cli._cmd_send(args)
        assert refused.value.code == 69
        assert len(a_db.list_pending_live_messages("a-new")) == 1
    finally:
        a_db.close()
        b_db.close()


async def _release():
    pass


@pytest.mark.guard
def test_old_carrier_rejects_new_contract_before_delivery():
    router = CarrierRequestRouter(lambda: pytest.fail("client must not be reached"))
    response = asyncio.run(router(Envelope(EnvelopeType.REQUEST, payload={
        "version": 3, "operation": "live_session.send",
        "target": "live:host-b:project-a:wt-b", "sender": "peer", "message": "hello",
    })))
    assert response.type is EnvelopeType.ERROR
    assert response.payload["code"] == "unsupported_version"


def test_old_hosting_bridge_rejects_reply_contract_before_delivery():
    client = SimpleNamespace(
        daemon_supports=lambda _: False,
        resolve_live_session=lambda _: pytest.fail("must not resolve on unsupported daemon"),
    )
    response = asyncio.run(CarrierRequestRouter(lambda: client)(
        Envelope(EnvelopeType.REQUEST, payload={
            "version": 4, "operation": "live_session.send", "target": "live:host-b:project-a:wt-b",
            "sender": "peer", "message": "hello",
        }),
    ))
    assert response.type is EnvelopeType.ERROR
    assert response.payload["code"] == "unsupported_version"


def test_old_local_bridge_refuses_qualified_lookup_before_http():
    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda _: False
    client._request = lambda *_a, **_k: pytest.fail("HTTP must not be reached")
    with pytest.raises(BridgeClientError) as refused:
        client.resolve_live_session("live:host-a:project-a:wt-a")
    assert refused.value.status == 426


def test_address_metadata_is_not_a_session_id():
    assert str(LivePeerAddress.from_registration({
        "machine": "host-a", "repo": "project-a", "worktree_id": "wt-a", "session_id": "temporary",
    })) == "live:host-a:project-a:wt-a"


def test_registered_caller_address_does_not_require_worktrees_sibling(monkeypatch):
    monkeypatch.setattr(cli, "_worktrees_get", lambda _: None)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "caller")
    client = SimpleNamespace(resolve_live_session=lambda _: {
        "machine": "host-a", "repo": "project-a", "worktree_id": "wt-a",
    })
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    assert cli._live_reply_to(argparse.Namespace()) == "live:host-a:project-a:wt-a"


def test_unrepresented_hosted_caller_keeps_legacy_bridge_handle(monkeypatch):
    monkeypatch.setattr(cli, "_worktrees_get", lambda _: None)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "conversation")
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "bridge-session")
    monkeypatch.setattr(cli, "_get_client", lambda: SimpleNamespace(resolve_live_session=lambda _: {}))
    assert cli._live_reply_to(argparse.Namespace()) == "bridge-session"
