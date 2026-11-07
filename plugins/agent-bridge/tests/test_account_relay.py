import asyncio
import json
import socket
import time
from types import SimpleNamespace

import pytest

from credential_relay.account_relay import AccountRelay, SOURCE_LEASE_ACTION, SOURCE_LEASE_CONTRACT
from credential_relay.registry import RelayBuilder
from credential_relay.sources.gh_auth import GhAuthSource

from agent_bridge.relay_runtime import capture_relay, restore_relay
from agent_bridge.agent_registry_relay import register_credential_sources


@pytest.fixture
def source_policy(tmp_path):
    capability = tmp_path / "capability"
    capability.write_text("capability-for-isolated-test-only-123456")
    fields = {
        "source": "github:example/repo", "account": "account-a", "recipient": "worker-a",
        "host": "github.com", "action": "get-github-token",
    }
    policy = {
        "schema": "credential-relay.account-policy", "version": 1,
        "providers": {"selected": {"kind": "gh-auth", "account": "account-a", "host": "github.com"}},
        "grants": [dict(fields, provider="selected", capability_file=str(capability),
                       expires_at=time.time() + 3600, source_lease_seconds=3600)],
    }
    filename = tmp_path / "account-relay.json"
    filename.write_text(json.dumps(policy))
    return filename, policy, fields, capability


@pytest.mark.asyncio
async def test_live_source_lease_contract_renewal_and_legacy_compatibility(source_policy, monkeypatch):
    filename, policy, fields, capability = source_policy
    calls = 0

    async def resolve(self, action, request, *, timeout):
        nonlocal calls
        calls += 1
        if calls > 3:
            return None
        return (
            f"protocol=https\nhost=github.com\nusername={self._account}\n"
            f"token=isolated-credential-{calls}\n\n"
        )

    monkeypatch.setattr(GhAuthSource, "resolve", resolve)
    relay = AccountRelay(str(filename))
    await relay.start()

    async def exchange(operation, values):
        reader, writer = await asyncio.open_connection("127.0.0.1", relay._publication["port"])
        try:
            writer.write((operation + "\n" + "".join(
                f"{key}={value}\n" for key, value in values.items()
            ) + "\n").encode())
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(), 2)
            return dict(line.split("=", 1) for line in raw.decode().splitlines() if line)
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        assert await exchange("describe-account-relay", {}) == {
            "contract": SOURCE_LEASE_CONTRACT, "operation": SOURCE_LEASE_ACTION,
        }
        assert calls == 0
        request = dict(fields, auth=capability.read_text())
        old = await exchange("get-account-token", request)
        assert set(old) == {"protocol", "host", "username", "token"}
        leases = [await exchange(SOURCE_LEASE_ACTION, request) for _ in range(2)]
        assert [value["token"] for value in leases] == [
            "isolated-credential-2", "isolated-credential-3",
        ]
        assert leases[0]["lease_id"] != leases[1]["lease_id"]
        for value in leases:
            assert value["lease_contract"] == SOURCE_LEASE_CONTRACT
            issued, expires = float(value["lease_issued_at"]), float(value["lease_expires_at"])
            assert 0 < expires - issued <= 3600
            assert expires <= policy["grants"][0]["expires_at"]
        assert await exchange(SOURCE_LEASE_ACTION, request) == {}
        assert calls == 4
    finally:
        await relay.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [None, True, 0, 3601])
async def test_source_lease_requires_explicit_valid_grant(source_policy, monkeypatch, seconds):
    filename, policy, fields, capability = source_policy
    if seconds is None:
        del policy["grants"][0]["source_lease_seconds"]
    else:
        policy["grants"][0]["source_lease_seconds"] = seconds
    filename.write_text(json.dumps(policy))

    async def forbidden(*args, **kwargs):
        pytest.fail("Unapproved lease attempted source acquisition")

    monkeypatch.setattr(GhAuthSource, "resolve", forbidden)
    assert await AccountRelay(str(filename)).resolve(
        fields, capability.read_text(), source_lease=True,
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "policy", "capability", "expiry", "lease-expiry", "clock-backwards",
    "grant-monotonic-expiry", "binding",
])
async def test_source_lease_fences_acquisition_before_delivery(source_policy, monkeypatch, change):
    filename, policy, fields, capability = source_policy
    policy["grants"][0]["source_lease_seconds"] = 60
    now = time.time()
    elapsed = 0.0
    if change == "grant-monotonic-expiry":
        policy["grants"][0]["expires_at"] = now + 5
    filename.write_text(json.dumps(policy))
    monkeypatch.setattr("credential_relay.account_relay.time", SimpleNamespace(
        time=lambda: now, monotonic=lambda: elapsed,
    ))
    secret = capability.read_text()

    async def resolve(self, action, request, *, timeout):
        nonlocal now, elapsed
        if change == "policy":
            filename.write_text("{}")
        elif change == "capability":
            capability.write_text("rotated-capability-for-isolated-test-only")
        elif change == "expiry":
            now = policy["grants"][0]["expires_at"]
        elif change == "lease-expiry":
            now += 60
        elif change == "clock-backwards":
            now -= 1
        elif change == "grant-monotonic-expiry":
            now += 1
            elapsed += 6
        account = "other-account" if change == "binding" else self._account
        return f"protocol=https\nhost=github.com\nusername={account}\ntoken=isolated-credential\n\n"

    monkeypatch.setattr(GhAuthSource, "resolve", resolve)
    assert await AccountRelay(str(filename)).resolve(fields, secret, source_lease=True) is None


@pytest.mark.asyncio
async def test_source_lease_shutdown_cancels_acquisition_without_delivery(source_policy, monkeypatch):
    filename, _, fields, capability = source_policy
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def resolve(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(GhAuthSource, "resolve", resolve)
    relay = AccountRelay(str(filename))
    await relay.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", relay._publication["port"])
    try:
        values = dict(fields, auth=capability.read_text())
        writer.write((SOURCE_LEASE_ACTION + "\n" + "".join(
            f"{key}={value}\n" for key, value in values.items()
        ) + "\n").encode())
        await writer.drain()
        await asyncio.wait_for(entered.wait(), 2)
        await relay.stop()
        assert cancelled.is_set()
        assert await asyncio.wait_for(reader.read(), 1) == b""
    finally:
        writer.close()
        await writer.wait_closed()
        await relay.stop()


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
