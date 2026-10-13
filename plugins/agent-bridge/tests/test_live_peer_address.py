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
        return version <= 35

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
    "live:host:project:wt", "live:host", "live:host:project:wt:extra",
])
def test_exact_live_namespace_session_ids_keep_precedence_through_http_and_cli(
    tmp_db, monkeypatch, capsys, handle,
):
    _register(tmp_db, handle, "other-host", project="other-project", worktree="exact-wt")
    _register(tmp_db, "qualified-peer", "host", project="project", worktree="wt")
    assert tmp_db.resolve_live_session(handle, now=time.time())["session_id"] == handle
    _app, client = _bridge(tmp_db)
    assert client.resolve_live_session(handle)["session_id"] == handle
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    monkeypatch.setattr(cli, "_live_reply_to", lambda _: None)
    monkeypatch.setattr(cli, "_live_sender_label", lambda _: "sender")
    monkeypatch.setattr(cli, "_resolve_target", lambda *_a, **_k: pytest.fail("agent fallback"))
    cli._cmd_send(argparse.Namespace(target=handle, prompt="exact recipient", no_wait=True, json=True))
    assert json.loads(capsys.readouterr().out)["target"] == handle
    assert tmp_db.list_pending_live_messages(handle)[0]["body"] == "exact recipient"
    assert tmp_db.list_pending_live_messages("qualified-peer") == []


@pytest.mark.parametrize("handle", [
    "live:", "live:a:b", "live:a:b:c:d", "live:a:b:../c", "live:a/b:c:d",
    "live:a:b:c/d", "live:a:/b:c", "live:a:b/:c", "live:a:b//c:d", "live:a:b/../c:d",
])
def test_invalid_explicit_address_never_spawns(monkeypatch, capsys, handle):
    monkeypatch.setattr(cli, "_get_client", lambda: SimpleNamespace(resolve_live_session=lambda _: None))
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
@pytest.mark.parametrize("logical_siblings", [False, True])
@pytest.mark.parametrize("caller_qualified", [True, False])
def test_two_bridge_call_and_reverse_reply_preserve_addresses(
    tmp_path, monkeypatch, capsys, idempotency_key, project, logical_siblings, caller_qualified,
):
    machine_a, machine_b = "host-a", "host-b"
    if logical_siblings:
        from agent_bridge.topology import parse_machines_yaml

        machines = parse_machines_yaml({"machines": {"desk": {
            "ssh": {"environments": [
                {"name": "windows", "alias": "desk-win", "shell": "pwsh"},
                {"name": "wsl", "alias": "desk-ubuntu", "shell": "bash"},
            ]},
        }}}, normalize=True)
        machine_a, machine_b = machines
        assert machines[machine_a].physical_host == machines[machine_b].physical_host
    a_db, b_db = Database(tmp_path / "a.db"), Database(tmp_path / "b.db")
    try:
        a_app, a_client = _bridge(a_db)
        b_app, b_client = _bridge(b_db)
        _register(a_db, "a-old", machine_a if caller_qualified else None,
                  project=project if caller_qualified else None,
                  worktree="wt-a" if caller_qualified else None)
        _register(b_db, "b-current", machine_b, project=project, worktree="wt-b")
        _register(a_db, "a-other-machine", "host-c", project=project, now=time.time() + 3)
        _register(b_db, "b-other-project", machine_b, project="project-b",
                  worktree="wt-b", now=time.time() + 3)
        _register(b_db, "b-colliding-source-handle", machine_b, project=project,
                  worktree="wt-a", now=time.time() + 3)

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
            (a_app, a_client, machine_b, b_client), (b_app, b_client, machine_a, a_client),
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
            target=f"live:{machine_b}:{project}:wt-b", prompt="call", json=True,
            no_wait=False, reply_timeout=2.0, idempotency_key=idempotency_key,
        )
        assert a_client.resolve_remote_live_session(machine_b, args.target)["session_id"] == "b-current"
        cli._cmd_send(args)
        output = capsys.readouterr().out
        assert '"replied": true' in output and '"reply": "peer response"' in output
        fixture_path = (Path(__file__).parents[1] / "contract" / "fixtures" / "http"
                        / "current" / "remote-peer-generation-35.json")
        expected = json.loads(fixture_path.read_text(encoding="utf-8"))["response"]
        observed = json.loads(output)
        assert {key: observed[key] for key in expected} == expected
        received = b_db.list_pending_live_messages("b-current")
        if not caller_qualified:
            assert received[0]["reply_to"] is None
            assert b_db.list_pending_live_messages("b-colliding-source-handle") == []
            return
        assert received[0]["reply_to"] == f"live:{machine_a}:{project}:wt-a"
        assert b_db.list_pending_live_messages("b-other-project") == []
        _register(a_db, "a-new", machine_a, project=project, now=time.time() + 1)
        monkeypatch.setattr(cli, "_get_client", lambda: b_client)
        monkeypatch.setattr(cli, "_live_sender_label", lambda _: "wt-b")
        monkeypatch.setattr(cli, "_worktrees_get", lambda _: "/fixture/wt-b")
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "b-current")
        args.target, args.prompt = received[0]["reply_to"], "reverse reply"
        cli._cmd_send(args)
        output = capsys.readouterr().out
        assert '"replied": true' in output and '"reply": "peer response"' in output
        assert a_db.list_pending_live_messages("a-old") == []
        assert a_db.list_pending_live_messages("a-other-machine") == []
        assert a_db.list_pending_live_messages("a-new")[0]["body"] == "reverse reply"
        if idempotency_key:
            cli._cmd_send(args)
            assert len(a_db.list_pending_live_messages("a-new")) == 1
        args.expected_session_id, args.idempotency_key = "a-old", None
        with pytest.raises(SystemExit) as refused:
            cli._cmd_send(args)
        assert refused.value.code == 69
        assert len(a_db.list_pending_live_messages("a-new")) == 1
        args.expected_session_id = None
        args.no_wait, args.reply_timeout, args.prompt = True, 0, "no-wait reply"
        monkeypatch.setattr(live_sessions, "await_turn_reply", lambda *_a, **_k: pytest.fail("no-wait"))
        cli._cmd_send(args)
        received = a_db.list_pending_live_messages("a-new")
        assert len(received) == 2 and received[-1]["body"] == "no-wait reply"
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
        "qualified_peer": True,
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
            "qualified_peer": True,
        }),
    ))
    assert response.type is EnvelopeType.ERROR
    assert response.payload["code"] == "unsupported_version"
    assert "HTTP protocol 35" in response.payload["message"]


def test_old_local_bridge_refuses_qualified_lookup_before_http():
    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda _: False
    client._request = lambda *_a, **_k: pytest.fail("HTTP must not be reached")
    with pytest.raises(BridgeClientError) as refused:
        client.resolve_live_session("live:host-a:project-a:wt-a", qualified_peer=True)
    assert refused.value.status == 426
    assert "HTTP protocol 35" in str(refused.value)


@pytest.mark.parametrize("handle", ["live:host", "live:host:project:wt", "live:host:project:wt:extra"])
def test_old_http_bridge_exact_lookup_precedes_capability_check(handle):
    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda _: pytest.fail("an exact lookup needs no new capability")
    client._request = lambda *_a, **_k: {"session_id": handle}
    assert client.resolve_live_session(handle)["session_id"] == handle


def test_missing_live_handle_checks_old_http_capability_only_after_404():
    client = BridgeClient.__new__(BridgeClient)
    queried = []

    def request(*_a, **_k):
        queried.append(True)
        raise BridgeClientError(404, "not found")

    client._request = request
    client.daemon_supports = lambda _: False if queried else pytest.fail("must query first")
    with pytest.raises(BridgeClientError) as refused:
        client.resolve_live_session("live:host:project:wt")
    assert refused.value.status == 426 and queried == [True]
    assert "HTTP protocol 35" in str(refused.value)


@pytest.mark.parametrize("operation", ["resolve", "send"])
def test_remote_peer_capability_diagnostic_matches_current_floor(operation):
    client = BridgeClient.__new__(BridgeClient)
    client._require_remote_operations = lambda: None
    client._require_remote_commands = lambda: None
    client.daemon_supports = lambda _: False
    client._request = lambda *_a, **_k: pytest.fail("HTTP must not be reached")
    with pytest.raises(BridgeClientError) as refused:
        if operation == "resolve":
            client.resolve_remote_live_session(
                "host", "live:host:project:wt", qualified_peer=True,
            )
        else:
            client.send_remote_live_message(
                "host", "live:host:project:wt", sender="caller",
                message="hello", qualified_peer=True,
            )
    assert refused.value.status == 426
    assert "HTTP protocol 35" in str(refused.value)


def _route_through_versioned_carrier(app, hosting_client, carrier_version):
    packets = []

    class Carrier:
        async def request(self, payload, **_kwargs):
            packets.append(dict(payload))
            if payload["version"] > carrier_version:
                raise CarrierRemoteError({
                    "code": "unsupported_version", "message": "carrier version unsupported",
                })
            result = await CarrierRequestRouter(lambda: hosting_client)(
                Envelope(EnvelopeType.REQUEST, payload=payload),
            )
            if result.type is EnvelopeType.ERROR:
                raise CarrierRemoteError(result.payload)
            return result

    service = RemoteOperationService(SimpleNamespace())

    async def lease(_host):
        return SimpleNamespace(carrier=Carrier(), release=_release)

    service._lease = lease
    app.state.remote_operations = service
    return packets


@pytest.mark.parametrize("handle", ["live:host", "live:host:project:wt", "live:host:project:wt:extra"])
@pytest.mark.parametrize("carrier_version", [3, 4])
@pytest.mark.parametrize("hosting_version", [32, 35])
def test_remote_exact_ids_keep_legacy_carrier_and_http_compatibility(
    tmp_path, handle, carrier_version, hosting_version,
):
    source, hosting = Database(tmp_path / "source.db"), Database(tmp_path / "hosting.db")
    try:
        _register(hosting, handle, "host", project="other-project", worktree="exact-wt")
        _register(hosting, "qualified-peer", "host", project="project", worktree="wt")
        source_app, source_client = _bridge(source)
        _hosting_app, hosting_client = _bridge(hosting)
        hosting_client.daemon_supports = lambda version: version <= hosting_version
        packets = _route_through_versioned_carrier(source_app, hosting_client, carrier_version)
        assert source_client.resolve_remote_live_session("host", handle)["session_id"] == handle
        result = source_client.send_remote_live_message(
            "host", handle, sender="peer", message="legacy exact",
        )
        assert result["session_id"] == handle
        assert all(packet["version"] <= 3 for packet in packets)
        assert hosting.list_pending_live_messages(handle)[0]["body"] == "legacy exact"
        assert hosting.list_pending_live_messages("qualified-peer") == []
    finally:
        source.close()
        hosting.close()


@pytest.mark.parametrize("carrier_version,hosting_version", [(3, 35), (4, 32), (4, 35)])
def test_explicit_remote_qualified_mode_is_fenced_and_cannot_hit_a_colliding_exact_id(
    tmp_path, carrier_version, hosting_version,
):
    source, hosting = Database(tmp_path / "source.db"), Database(tmp_path / "hosting.db")
    address = "live:host:project:wt"
    try:
        _register(hosting, address, "host", project="other-project", worktree="exact-wt")
        _register(hosting, "qualified-peer", "host", project="project", worktree="wt")
        source_app, source_client = _bridge(source)
        _hosting_app, hosting_client = _bridge(hosting)
        hosting_client.daemon_supports = lambda version: version <= hosting_version
        packets = _route_through_versioned_carrier(source_app, hosting_client, carrier_version)
        if carrier_version == 4 and hosting_version == 35:
            result = source_client.send_remote_live_message(
                "host", address, sender="peer", message="qualified", qualified_peer=True,
            )
            assert result["session_id"] == "qualified-peer"
            assert hosting.list_pending_live_messages("qualified-peer")[0]["body"] == "qualified"
        else:
            with pytest.raises(BridgeClientError) as refused:
                source_client.send_remote_live_message(
                    "host", address, sender="peer", message="qualified", qualified_peer=True,
                )
            assert refused.value.status == 426
            assert hosting.list_pending_live_messages("qualified-peer") == []
        assert packets and all(packet["version"] == 4 for packet in packets)
        assert hosting.list_pending_live_messages(address) == []
    finally:
        source.close()
        hosting.close()


def test_address_metadata_is_not_a_session_id():
    assert str(LivePeerAddress.from_registration({
        "machine": "host-a", "repo": "project-a", "worktree_id": "wt-a", "session_id": "temporary",
    })) == "live:host-a:project-a:wt-a"


def test_registered_caller_address_does_not_require_worktrees_sibling(monkeypatch):
    monkeypatch.setattr(cli, "_worktrees_get", lambda _: None)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "caller")
    client = SimpleNamespace(resolve_live_session=lambda _: {
        "machine": "host-a", "repo": "project-a", "worktree_id": "wt-a",
    }, daemon_supports=lambda _: True)
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    assert cli._live_reply_to(argparse.Namespace()) == "live:host-a:project-a:wt-a"


def test_unrepresented_hosted_caller_keeps_legacy_bridge_handle(monkeypatch):
    monkeypatch.setattr(cli, "_worktrees_get", lambda _: None)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "conversation")
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "bridge-session")
    monkeypatch.setattr(cli, "_get_client", lambda: SimpleNamespace(
        resolve_live_session=lambda _: {}, daemon_supports=lambda _: True,
    ))
    assert cli._live_reply_to(argparse.Namespace()) == "bridge-session"


@pytest.mark.parametrize("metadata", [
    {"machine": "host name", "repo": "project-a", "worktree_id": "wt-a"},
    {"machine": "host-a", "repo": "project name", "worktree_id": "wt-a"},
    {"machine": "host-a", "repo": "project-a", "worktree_id": "work tree"},
])
def test_unencodable_registration_keeps_legacy_reply_with_warning(monkeypatch, capsys, metadata):
    monkeypatch.setattr(cli, "_worktrees_get", lambda _: None)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "caller")
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "legacy-bridge-session")
    monkeypatch.setattr(cli, "_get_client", lambda: SimpleNamespace(
        resolve_live_session=lambda _: metadata, daemon_supports=lambda _: True,
    ))
    assert LivePeerAddress.from_registration(metadata) is None
    assert cli._live_reply_to(argparse.Namespace()) == "legacy-bridge-session"
    assert "local-only" in capsys.readouterr().err


@pytest.mark.parametrize("version", [32, 34])
@pytest.mark.parametrize("worktree", [None, "wt-caller"])
def test_new_payload_old_daemon_cli_call_and_reverse_reply_use_legacy_handle(
    tmp_db, monkeypatch, capsys, version, worktree,
):
    _register(tmp_db, "caller-session", "host-a", worktree="wt-caller")
    _register(tmp_db, "recipient", "host-a", worktree="wt-recipient")
    collision = "live:host-a:project-a:wt-caller"
    _register(tmp_db, collision, "other-host", worktree="wt-collision")
    _app, client = _bridge(tmp_db)
    client.daemon_supports = lambda floor: version >= floor
    monkeypatch.setattr(cli, "_get_client", lambda: client)
    monkeypatch.setattr(cli, "_live_sender_label", lambda _: "caller")
    monkeypatch.setattr(
        cli, "_worktrees_get",
        lambda key: f"C:/worktrees/{worktree}" if key == "worktree-dir" and worktree else None,
    )
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "caller-session")
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "caller-session")
    cli._cmd_send(argparse.Namespace(
        target="recipient", prompt="legacy call", no_wait=True, json=True,
    ))
    envelope = tmp_db.list_pending_live_messages("recipient")[0]
    assert envelope["reply_to"] == (worktree or "caller-session")
    cli._cmd_send(argparse.Namespace(
        target=envelope["reply_to"], prompt="legacy response", no_wait=True, json=True,
        reply_to="recipient",
    ))
    assert tmp_db.list_pending_live_messages("caller-session")[0]["body"] == "legacy response"
    assert tmp_db.list_pending_live_messages(collision) == []
    assert "local-only" in capsys.readouterr().err


@pytest.mark.parametrize("version", [32, 34])
@pytest.mark.parametrize("worktree", [None, "wt-caller"])
def test_new_payload_old_daemon_keeps_legacy_automatic_reply(
    monkeypatch, capsys, version, worktree,
):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "caller-conversation")
    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "caller-session")
    monkeypatch.setattr(
        cli, "_worktrees_get",
        lambda key: f"C:/worktrees/{worktree}" if key == "worktree-dir" and worktree else None,
    )
    checked = []

    def supports(floor):
        checked.append(floor)
        return version >= floor

    monkeypatch.setattr(cli, "_get_client", lambda: SimpleNamespace(
        daemon_supports=supports,
        resolve_live_session=lambda _: pytest.fail("old daemon metadata must not advertise a peer"),
    ))
    assert cli._live_reply_to(argparse.Namespace()) == (worktree or "caller-session")
    assert checked == [35]
    assert "local-only" in capsys.readouterr().err


@pytest.mark.parametrize("wait_timeout,timeout", [(120.0, 135.0), (600.0, 615.0), (1.0, 20.0)])
def test_waited_client_defaults_cover_operation_and_read_deadlines(monkeypatch, wait_timeout, timeout):
    captured = {}
    client = BridgeClient.__new__(BridgeClient)
    monkeypatch.setattr(client, "daemon_supports", lambda _: True)

    def request(method, path, data, *, request_timeout):
        captured.update(data=data, read_timeout=request_timeout)
        return {"message_id": 1}

    monkeypatch.setattr(client, "_request", request)
    client.send_remote_live_message(
        "host-b", "live:host-b:project-a:wt-b", sender="peer",
        message="call", wait=True, wait_timeout=wait_timeout,
    )
    assert captured["data"]["timeout"] == timeout
    assert captured["read_timeout"] == timeout + 15.0


@pytest.mark.parametrize("wait_timeout,timeout", [(None, 135.0), (600.0, 615.0), (1.0, 20.0)])
def test_waited_http_defaults_cover_carrier_deadline(tmp_path, wait_timeout, timeout):
    db = Database(tmp_path / "http.db")
    try:
        app, client = _bridge(db)
        service = RemoteOperationService(SimpleNamespace())
        app.state.remote_operations = service
        captured = {}

        async def request(host, payload, *, timeout):
            captured.update(timeout=timeout, payload=payload)
            return {"result": {"message_id": 1}}

        service._request = request
        body = {"sender": "peer", "message": "call", "wait": True}
        if wait_timeout is not None:
            body["wait_timeout"] = wait_timeout
        response = client.http.post(
            "/api/v1/remote/host-b/live-sessions/live:host-b:project-a:wt-b/messages", json=body,
        )
        assert response.status_code == 200, response.text
        assert captured["timeout"] == timeout
        assert captured["payload"]["wait_timeout"] == (wait_timeout or 120.0)
    finally:
        db.close()
