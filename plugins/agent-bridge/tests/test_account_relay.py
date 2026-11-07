import asyncio
import json
import socket

from credential_relay.account_relay import AccountRelay
from credential_relay.registry import RelayBuilder

from agent_bridge.relay_runtime import capture_relay, restore_relay
from agent_bridge.agent_registry_relay import register_credential_sources


def test_optional_policy_is_attached_to_bridge_not_a_provider_daemon(tmp_path, monkeypatch):
    from agent_bridge import config, agent_registry_relay, http_provider_sources

    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_registry_relay, "_register_provider_relay", lambda *a: None)
    monkeypatch.setattr(http_provider_sources, "register_http_credential_sources", lambda *a: None)
    builder = RelayBuilder()
    register_credential_sources(builder)
    assert builder.empty and builder.account_relay is None
    (tmp_path / "account-relay.json").write_text("{}")
    register_credential_sources(builder)
    assert not builder.empty
    assert type(builder.account_relay) is AccountRelay
    assert builder.build().account_relay is builder.account_relay


def test_account_policy_reference_survives_private_cutover_without_reading_tokens(tmp_path):
    builder = RelayBuilder()
    builder.account_relay = AccountRelay(str(tmp_path / "not-yet-readable.json"))
    server = builder.build()
    payload = capture_relay(server)
    assert payload["account_policy"] == str(tmp_path / "not-yet-readable.json")
    restored = restore_relay(payload)
    assert restored.account_relay.policy_path == server.account_relay.policy_path
    assert restored.account_relay._server is None


def test_legacy_capture_has_no_new_required_field():
    server = RelayBuilder().build()
    payload = capture_relay(server)
    assert "account_policy" not in payload
    assert capture_relay(restore_relay(payload)) == payload


def test_private_stage_prepares_account_listener_without_admission(tmp_path, monkeypatch):
    from agent_bridge.staged_relay import StagedRelay
    from zdd.private_protocol import Deadline
    from credential_relay.server import CredentialRelayServer

    async def scenario():
        calls = []
        account = AccountRelay(str(tmp_path / "account-relay.json"))

        async def resolve(fields, capability):
            calls.append((fields, capability))
            return "token=isolated-stage-result\n\n"

        monkeypatch.setattr(account, "resolve", resolve)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        relay = CredentialRelayServer(port=port, account_relay=account)
        stage = StagedRelay(relay, Deadline(5))
        publication = tmp_path / "account-relay-port.json"
        predecessor = AccountRelay(account.policy_path)
        await predecessor.start()
        old_publication = json.loads(publication.read_text())

        async def request(port):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                writer.write(b"get-account-token\nsource=fixture\n\n")
                await writer.drain()
                try:
                    return await asyncio.wait_for(reader.read(), 1)
                except (ConnectionResetError, BrokenPipeError):
                    return b""
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionResetError, BrokenPipeError):
                    pass

        try:
            await stage.prepare()
            account_port = account._server.sockets[0].getsockname()[1]
            assert json.loads(publication.read_text()) == old_publication
            assert await request(account_port) == b""
            assert not calls
            stage.activate(Deadline(1))
            assert json.loads(publication.read_text())["port"] == account_port
            await predecessor.stop()
            assert json.loads(publication.read_text())["port"] == account_port
            assert await request(account_port) == b"token=isolated-stage-result\n\n"
            assert len(calls) == 1
        finally:
            await predecessor.stop()
            await stage.wait_closed(Deadline(2))
        assert not publication.exists()

    asyncio.run(scenario())
