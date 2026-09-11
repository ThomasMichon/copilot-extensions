"""Relay policy belongs to the installation, not its passive-start state."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from agent_bridge import app as app_module
from agent_bridge.agent_registry import AgentResolver
from agent_bridge.models import ServiceConfig
from agent_bridge.routes import admin


@pytest.mark.asyncio
async def test_disabled_adoption_never_calls_hook_for_another_root(tmp_path):
    global_root = tmp_path / "global"
    isolated_root = tmp_path / "isolated"
    global_root.mkdir()
    isolated_root.mkdir()
    marker = global_root / "active.json"
    marker.write_text(json.dumps({"pid": 1234, "version": "older", "port": 45123}))
    before = marker.read_bytes()
    adopt = AsyncMock(side_effect=AssertionError("disabled adoption was invoked"))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        config=ServiceConfig(db_path=str(isolated_root / "sessions.db"), enable_credential_relay=False),
        adopt_relay=adopt,
    )))
    assert await admin.relay_adopt(request) == {"adopted": False, "reason": "relay disabled by configuration"}
    adopt.assert_not_awaited()
    assert marker.read_bytes() == before


@pytest.mark.asyncio
async def test_disabled_relay_start_does_not_construct_sources(monkeypatch):
    import credential_relay

    monkeypatch.setattr(
        credential_relay, "RelayBuilder",
        lambda: pytest.fail("disabled relay built sources or attempted a bind"),
    )
    app = SimpleNamespace(state=SimpleNamespace(config=ServiceConfig(enable_credential_relay=False)))
    assert await app_module._start_credential_relay(app) is None


@pytest.mark.parametrize("enabled", [False, True])
def test_passive_start_preserves_declared_policy_until_adoption(tmp_path, monkeypatch, enabled):
    global_root = tmp_path / "global"
    isolated_root = tmp_path / "isolated"
    global_root.mkdir()
    isolated_root.mkdir()
    marker = global_root / "active.json"
    marker.write_text('{"pid":1234,"version":"newer","port":45123}')
    before = marker.read_bytes()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(isolated_root))
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(isolated_root / "missing.yaml"))
    monkeypatch.setattr(app_module, "daemon_resolver", lambda cfg: AgentResolver({}, {}))
    relay = SimpleNamespace(running=True, stop=AsyncMock())
    start = AsyncMock(return_value=relay)
    monkeypatch.setattr(app_module, "_start_credential_relay", start)
    monkeypatch.setattr(
        "agent_bridge.runtime_version.write_running_version",
        lambda: pytest.fail("passive startup published a primary runtime marker"),
    )
    cfg = ServiceConfig(
        port=45124, db_path=str(isolated_root / "sessions.db"),
        enable_credential_relay=enabled, worktree_discovery_interval=0,
    )
    app = app_module.create_app(config=cfg, token="test-token")
    app.state.relay_start_deferred = True
    app.state.publish_on_ready = False
    with TestClient(app) as client:
        start.assert_not_awaited()
        assert cfg.enable_credential_relay is enabled
        result = client.post("/api/v1/relay/adopt", headers={"Authorization": "Bearer test-token"})
        assert result.status_code == 200
        assert result.json()["adopted"] is enabled
        if enabled:
            start.assert_awaited_once_with(app)
        else:
            start.assert_not_awaited()
    assert marker.read_bytes() == before
