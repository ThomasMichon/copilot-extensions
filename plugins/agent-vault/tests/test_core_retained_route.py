"""A health-qualified core route must not discover another credential recipient."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from agent_vault import cli, config, core_ext, extensions
from agent_vault.command_transport import CommandRoute
from agent_vault.extensions import ExtensionRegistry, TransportContext
from agent_vault.rendezvous import Endpoint

if TYPE_CHECKING:
    from test_core_delegation import _EchoServer
else:
    from .test_core_delegation import _EchoServer


def test_core_route_keeps_qualified_endpoint_and_token_after_discovery_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, replacement = _EchoServer({"ok": True}), _EchoServer({"ok": True})
    registry = ExtensionRegistry()
    registry._loaded = True
    core_ext.register(registry)
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    monkeypatch.setattr(cli, "_discover_endpoint", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "_send_socket", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "_send_tcp", lambda *_a, **_kw: None)
    monkeypatch.setenv("AGENT_VAULT_CORE_RUN_DIR", str(tmp_path / "core"))
    monkeypatch.setenv(core_ext.CORE_ENDPOINT_ENV, f"tcp:{first.host}:{first.port}")
    monkeypatch.setenv(core_ext.CORE_TOKEN_ENV, "fixture-token-before")
    context = config.ResolvedVault(None, None, "Example", 19371, {"port": "flag"})
    route = CommandRoute()
    try:
        health = cli.send_command({"action": "ping"}, _context=context, _route=route)
        assert health is not None and health["ok"] and route.sender is not None
        monkeypatch.setenv(core_ext.CORE_ENDPOINT_ENV, f"tcp:{replacement.host}:{replacement.port}")
        monkeypatch.setenv(core_ext.CORE_TOKEN_ENV, "fixture-token-after")
        result = cli.send_command(
            {"action": "unlock", "password": "fixture-password"}, _context=context, _route=route,
        )
        assert result is not None and result["ok"]
        assert first.received is not None and first.received["action"] == "unlock"
        assert first.received["password"] == "fixture-password"
        assert first.received["_token"] == "fixture-token-before"
        assert replacement.received is None
    finally:
        first.close()
        replacement.close()


@pytest.mark.parametrize("configured", ["12", "nan", "inf"])
def test_core_bound_connector_keeps_its_qualified_timeout_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured: str,
) -> None:
    monkeypatch.setenv("AGENT_VAULT_CORE_RUN_DIR", str(tmp_path))
    monkeypatch.setenv(core_ext.CORE_TIMEOUT_ENV, configured)
    monkeypatch.setattr(core_ext, "resolve", lambda *_a, **_kw: Endpoint("tcp", "127.0.0.1:12345"))
    observed: list[dict[str, Any]] = []

    def delegate(_app: str, _request: dict[str, Any], **options: Any) -> dict[str, Any]:
        observed.append(options)
        return {"ok": True}

    monkeypatch.setattr(core_ext, "delegate", delegate)
    context = TransportContext(None, None, None, 19371)
    selected = core_ext.bind_core_transport(context)
    assert selected is not None
    monkeypatch.setenv(core_ext.CORE_TIMEOUT_ENV, "99")
    first, second = selected({"action": "ping"}, None, context), selected({"action": "unlock"}, 3.0, context)
    assert first is not None and second is not None and first["ok"] and second["ok"]
    cap = 12.0 if configured == "12" else core_ext.DEFAULT_CORE_TIMEOUT
    assert [options["timeout"] for options in observed] == [cap, 3.0]
    assert all(isinstance(options["override"], Endpoint) for options in observed)
    assert all(options["probe"] is None for options in observed)
