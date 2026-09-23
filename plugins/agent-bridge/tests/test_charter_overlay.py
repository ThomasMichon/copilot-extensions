"""Regression coverage for the venue/charter split.

``agent`` names the **venue** a session spawns onto (a real, already-
addressable agent-bridge target). ``charter`` is an optional
``.github/agents/<charter>.agent.md`` behavior overlay -- Copilot's own
``--agent <charter>`` flag -- independent of the venue. The client renders
``charter`` as a ``copilot_args`` entry (the existing generic per-session
extra-args passthrough), merged ahead of any explicit ``copilot_args`` the
caller also supplies, rather than inventing a new wire field.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_bridge.client import BridgeClient


@pytest.fixture
def cfg_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config dir with a valid auth token and a config.yaml port."""
    (tmp_path / "auth.yaml").write_text(yaml.dump({"token": "tok-123"}))
    (tmp_path / "config.yaml").write_text(yaml.dump({"port": 9281, "bind": "127.0.0.1"}))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_NO_ROUTING_TABLE", raising=False)
    return tmp_path


def _captured_body(client: BridgeClient, monkeypatch: pytest.MonkeyPatch, **kwargs) -> dict:
    captured: list[dict] = []

    def fake_request(method, path, body=None, **kw):
        captured.append(body or {})
        return {"session_id": "s"}

    monkeypatch.setattr(client, "_request", fake_request)
    client.start_session(**kwargs)
    return captured[0]


def test_start_session_body_omits_copilot_args_when_no_charter(cfg_dir: Path, monkeypatch):
    client = BridgeClient.from_config()
    body = _captured_body(client, monkeypatch, agent="Lambda-Core-wsl")
    assert body["agent"] == "Lambda-Core-wsl"
    assert "copilot_args" not in body


def test_start_session_body_renders_charter_as_agent_flag(cfg_dir: Path, monkeypatch):
    client = BridgeClient.from_config()
    body = _captured_body(
        client, monkeypatch, agent="Lambda-Core-wsl", charter="cab-sweep-reconciler",
    )
    assert body["agent"] == "Lambda-Core-wsl"
    assert body["copilot_args"] == ["--agent", "cab-sweep-reconciler"]
    assert "charter" not in body


def test_start_session_body_charter_precedes_explicit_copilot_args(cfg_dir: Path, monkeypatch):
    client = BridgeClient.from_config()
    body = _captured_body(
        client,
        monkeypatch,
        agent="Lambda-Core-wsl",
        charter="module-decomposer",
        copilot_args=["--additional-mcp-config", "@run.json"],
    )
    assert body["copilot_args"] == [
        "--agent", "module-decomposer", "--additional-mcp-config", "@run.json",
    ]
