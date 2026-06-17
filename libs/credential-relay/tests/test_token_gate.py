"""Tests for the per-connection token gate (RelayBuilder + server)."""

from __future__ import annotations

import asyncio

import pytest

from credential_relay import RelayBuilder, TokenRegistry
from credential_relay.server import CredentialRelayServer, RelayStats


class _AzStub:
    """Minimal source that answers get-azure-token with a fixed token."""

    name = "az-stub"

    def supports(self, action, fields):
        return action == "get-azure-token"

    async def resolve(self, action, fields, *, timeout=30.0):
        return "protocol=https\nhost=storage.azure.com\ntoken=STUBTOKEN\n\n"


def test_token_registry_mint_add_validate_discard():
    reg = TokenRegistry()
    tok = TokenRegistry.mint()
    assert len(tok) >= 32
    assert reg.validate(tok) is False  # not added yet
    reg.add(tok)
    assert reg.validate(tok) is True
    assert reg.validate("") is False
    assert reg.validate("wrong") is False
    reg.discard(tok)
    assert reg.validate(tok) is False


def test_builder_require_token_wires_server():
    reg = TokenRegistry()
    b = RelayBuilder()
    b.add_source(_AzStub())
    b.require_token(["get-azure-token"], reg.validate)
    srv = b.build()
    assert srv.token_required_actions == frozenset({"get-azure-token"})
    assert srv.token_validator is not None


async def _roundtrip(srv: CredentialRelayServer, request: str) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", srv.port)
    writer.write(request.encode())
    await writer.drain()
    data = b""
    while True:
        chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
        if not chunk:
            break
        data += chunk
        if b"\n\n" in data:
            break
    writer.close()
    return data.decode()


@pytest.mark.asyncio
async def test_server_token_gate_allows_valid_denies_invalid():
    reg = TokenRegistry()
    good = TokenRegistry.mint()
    reg.add(good)
    srv = CredentialRelayServer(
        port=0,
        sources=[_AzStub()],
        token_validator=reg.validate,
        token_required_actions=frozenset({"get-azure-token"}),
    )
    await srv.start()
    # asyncio assigns a real port when 0 is requested
    srv.port = srv._server.sockets[0].getsockname()[1]
    try:
        ok = await _roundtrip(
            srv,
            f"get-azure-token\nauth={good}\nresource=https://storage.azure.com/\n\n",
        )
        assert "token=STUBTOKEN" in ok

        bad = await _roundtrip(
            srv,
            "get-azure-token\nauth=WRONG\nresource=https://storage.azure.com/\n\n",
        )
        assert "STUBTOKEN" not in bad
        assert srv.stats.token_rejections == 1

        missing = await _roundtrip(
            srv,
            "get-azure-token\nresource=https://storage.azure.com/\n\n",
        )
        assert "STUBTOKEN" not in missing
        assert srv.stats.token_rejections == 2
    finally:
        await srv.stop()


@pytest.mark.asyncio
async def test_ungated_action_needs_no_token():
    """Open actions (not in token_required_actions) bypass the gate."""
    reg = TokenRegistry()
    srv = CredentialRelayServer(
        port=0,
        sources=[_AzStub()],
        token_validator=reg.validate,
        token_required_actions=frozenset({"get-azure-token"}),
    )
    # get-github-token is not gated; with no matching source it just resolves to
    # nothing, but must NOT count as a token rejection.
    await srv.start()
    srv.port = srv._server.sockets[0].getsockname()[1]
    try:
        await _roundtrip(srv, "get-github-token\nhost=github.com\n\n")
        assert srv.stats.token_rejections == 0
    finally:
        await srv.stop()


def test_stats_has_token_rejections():
    assert RelayStats().token_rejections == 0


# ---------------------------------------------------------------------------
# Multi-validator + merged Azure allowlist (codespaces + containers share one
# relay; #44 / option A).
# ---------------------------------------------------------------------------
def test_multiple_validators_any_match():
    """Two providers gating the same action -> a token from EITHER is accepted."""
    b = RelayBuilder()
    b.add_source(_AzStub())
    b.require_token(["get-azure-token"], lambda t: t == "alpha")
    b.require_token(["get-azure-token"], lambda t: t == "beta")
    srv = b.build()
    assert srv.token_required_actions == frozenset({"get-azure-token"})
    assert srv.token_validator("alpha") is True   # first provider's token
    assert srv.token_validator("beta") is True    # second provider's token
    assert srv.token_validator("gamma") is False  # neither


def test_allow_azure_resources_builds_single_merged_source():
    """allow_azure_resources from multiple providers -> one merged az-login."""
    b = RelayBuilder()
    b.allow_azure_resources(["https://storage.azure.com/"])  # containers
    b.allow_azure_resources(["*"])                            # codespaces
    srv = b.build()
    az = [s for s in srv.sources if s.name == "az-login"]
    assert len(az) == 1  # single merged source, not two racing add_source
    assert az[0]._is_allowed("https://anything.example.com/.default") is True


def test_allow_azure_enables_nonempty_builder():
    """A builder with only Azure enabled is not 'empty' (relay must start)."""
    b = RelayBuilder()
    assert b.empty is True
    b.allow_azure_resources(["*"])
    assert b.empty is False

