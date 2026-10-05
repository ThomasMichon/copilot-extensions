"""Tests for BridgeClient.from_config() routing-table integration."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from zdd import routing

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


def test_falls_back_to_config_port_when_no_table(cfg_dir: Path):
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9281"


def test_prefers_routing_table_over_config(cfg_dir: Path, monkeypatch):
    # Pretend the active endpoint moved to a new port (skip listener probe so
    # the test needs no real socket).
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9290, version="v")
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9290"


def test_no_routing_table_env_forces_config_port(cfg_dir: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9290, version="v")
    monkeypatch.setenv("AGENT_BRIDGE_NO_ROUTING_TABLE", "1")
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9281"


def test_end_stop_thread_force_query_param(cfg_dir: Path, monkeypatch):
    """`--force` on end/stop maps to the route's ?force=true (#191)."""
    client = BridgeClient.from_config()
    calls: list[tuple] = []
    monkeypatch.setattr(
        client, "_request",
        lambda method, path, **kw: calls.append((method, path, kw)) or None,
    )
    client.end_session("s1")
    client.end_session("s2", force=True)
    client.stop_session("s3")
    client.stop_session("s4", force=True)
    client.stop_session("s5", force=True, reap_host=True)
    assert calls == [
        ("DELETE", "/api/v1/sessions/s1", {"params": None}),
        ("DELETE", "/api/v1/sessions/s2", {"params": {"force": "true"}}),
        ("POST", "/api/v1/sessions/s3/stop", {"params": None}),
        ("POST", "/api/v1/sessions/s4/stop", {"params": {"force": "true"}}),
        (
            "POST",
            "/api/v1/sessions/s5/stop",
            {"params": {"force": "true", "reap_host": "true"}},
        ),
    ]


def test_restart_worktree_force_query_param(cfg_dir: Path, monkeypatch):
    """`restart_worktree` maps `force` to POST .../restart?force=true (#6744
    Phase 3 -- the reclaim sequence's stop half)."""
    client = BridgeClient.from_config()
    calls: list[tuple] = []
    monkeypatch.setattr(
        client, "_request",
        lambda method, path, **kw: calls.append((method, path, kw)) or None,
    )
    monkeypatch.setattr(client, "daemon_supports", lambda min_version: True)
    client.restart_worktree("wt-1")
    client.restart_worktree("wt-2", force=True)
    client.restart_worktree("wt-3", expected_holder="sess-a")
    client.restart_worktree("wt-4", force=True, expected_holder="sess-b")
    assert calls == [
        ("POST", "/api/v1/worktrees/wt-1/restart", {"params": None, "request_timeout": None}),
        (
            "POST", "/api/v1/worktrees/wt-2/restart",
            {"params": {"force": "true"}, "request_timeout": None},
        ),
        (
            "POST", "/api/v1/worktrees/wt-3/restart",
            {"params": {"expected_holder": "sess-a"}, "request_timeout": None},
        ),
        (
            "POST", "/api/v1/worktrees/wt-4/restart",
            {
                "params": {"force": "true", "expected_holder": "sess-b"},
                "request_timeout": None,
            },
        ),
    ]


def test_restart_worktree_expected_holder_fails_closed_on_old_daemon(
    cfg_dir: Path, monkeypatch,
):
    """``expected_holder`` is protocol-16 behavior -- a daemon that doesn't
    advertise it would silently ignore the query param and run its old
    unfenced restart, so this refuses the call outright rather than sending
    an unfenced restart the daemon can't honor (#2906 race hardening)."""
    client = BridgeClient.from_config()
    calls: list[tuple] = []
    monkeypatch.setattr(
        client, "_request",
        lambda method, path, **kw: calls.append((method, path, kw)) or None,
    )
    monkeypatch.setattr(client, "daemon_supports", lambda min_version: False)
    result = client.restart_worktree("wt-1", expected_holder="sess-a")
    assert result["ok"] is False
    assert calls == []  # never even sent


def test_explicit_base_url_env_wins(cfg_dir: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9290, version="v")
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", "http://127.0.0.1:9299/")
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9299"


def test_stale_table_falls_back_to_config(cfg_dir: Path, monkeypatch):
    # Active points at a dead port (no listener, no pid) -> resolver returns
    # None -> client uses config fallback.
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: False)
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9290)
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9281"


def test_list_agents_preserves_topology_diagnostics(cfg_dir: Path, monkeypatch):
    client = BridgeClient.from_config()
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {
            "agents": [{"name": "valid"}],
            "topology_errors": ["stale: machines.yaml not found"],
        },
    )
    agents, errors = client.list_agents_with_diagnostics()
    assert agents == [{"name": "valid"}]
    assert errors == ["stale: machines.yaml not found"]


def test_list_agents_with_incomplete_reports_incomplete_namespaces(
    cfg_dir: Path, monkeypatch,
):
    """A `--stream`/`--subscribe` consumer needs to know which namespace(s)
    silently dropped their agents on THIS call, so it never reports a false
    `removed` for them."""
    client = BridgeClient.from_config()
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {
            "agents": [{"name": "local-agent"}],
            "topology_errors": [],
            "incomplete_namespaces": ["codespace"],
        },
    )
    agents, errors, incomplete, known = client.list_agents_with_incomplete()
    assert agents == [{"name": "local-agent"}]
    assert errors == []
    assert incomplete == ["codespace"]
    assert known is True


def test_list_agents_with_incomplete_unknown_capability_never_trusts_empty(
    cfg_dir: Path, monkeypatch,
):
    """A daemon whose response omits ``incomplete_namespaces`` entirely must
    report ``capability_known=False`` -- an empty `incomplete_namespaces`
    read from a MISSING key is NOT proof the scan was complete; its
    namespace resolvers may be silently dropping agents with no signal
    whatsoever. Distinguish this from a daemon that explicitly reports zero
    incomplete namespaces (the key present, just an empty list)."""
    client = BridgeClient.from_config()
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {"agents": [{"name": "a"}], "topology_errors": []},
    )
    agents, errors, incomplete, known = client.list_agents_with_incomplete()
    assert agents == [{"name": "a"}]
    assert errors == []
    assert incomplete == []
    assert known is False


def test_list_agents_with_incomplete_true_when_key_present_but_empty(
    cfg_dir: Path, monkeypatch,
):
    """The converse of the above: a capability-aware daemon that explicitly
    reports zero incomplete namespaces (key present, empty list) must be
    trusted -- `capability_known` is keyed on presence, not truthiness."""
    client = BridgeClient.from_config()
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {
            "agents": [{"name": "a"}],
            "topology_errors": [],
            "incomplete_namespaces": [],
        },
    )
    agents, errors, incomplete, known = client.list_agents_with_incomplete()
    assert agents == [{"name": "a"}]
    assert incomplete == []
    assert known is True


def test_list_agents_with_incomplete_defaults_empty_on_older_daemon(
    cfg_dir: Path, monkeypatch,
):
    """An older daemon's response predates `incomplete_namespaces` -- must
    default to empty, not raise/KeyError."""
    client = BridgeClient.from_config()
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {"agents": [{"name": "a"}], "topology_errors": []},
    )
    agents, errors, incomplete, known = client.list_agents_with_incomplete()
    assert agents == [{"name": "a"}]
    assert errors == []
    assert incomplete == []
    assert known is False


def test_list_agents_with_incomplete_omits_params_for_old_daemon(
    cfg_dir: Path, monkeypatch,
):
    """`force_refresh`/`require_complete` (Phase 3b, generation 22) must
    never be sent to a daemon that doesn't advertise
    `AGENT_ROSTER_CACHE_PROTOCOL_VERSION` -- an old daemon would otherwise
    silently ignore them while returning its own old-shape response, but
    the gate keeps intent explicit and avoids a gating regression that
    could someday send a param an old daemon mishandles."""
    client = BridgeClient.from_config()
    calls: list[tuple] = []
    monkeypatch.setattr(
        client, "_request",
        lambda method, path, **kw: calls.append((method, path, kw)) or {
            "agents": [], "topology_errors": [],
        },
    )
    monkeypatch.setattr(client, "daemon_supports", lambda min_version: False)
    client.list_agents_with_incomplete(force_refresh=True, require_complete=True)
    assert calls == [("GET", "/api/v1/agents", {"params": None})]


def test_list_agents_with_incomplete_sends_params_for_generation_22_daemon(
    cfg_dir: Path, monkeypatch,
):
    """Against a daemon that does advertise the capability, both params are
    serialized as the literal string `"true"` (the wire contract's own
    boolean-as-string convention, matching every other query-param flag in
    this client)."""
    client = BridgeClient.from_config()
    calls: list[tuple] = []
    monkeypatch.setattr(
        client, "_request",
        lambda method, path, **kw: calls.append((method, path, kw)) or {
            "agents": [], "topology_errors": [],
        },
    )
    monkeypatch.setattr(client, "daemon_supports", lambda min_version: True)
    client.list_agents_with_incomplete(force_refresh=True, require_complete=True)
    assert calls == [
        (
            "GET", "/api/v1/agents",
            {"params": {"force_refresh": "true", "require_complete": "true"}},
        ),
    ]


def test_list_agents_with_incomplete_omits_params_when_neither_requested(
    cfg_dir: Path, monkeypatch,
):
    """Even against a capability-aware daemon, neither param is sent unless
    the caller actually asked for one -- the plain default call stays a
    plain `GET` with no query string at all."""
    client = BridgeClient.from_config()
    calls: list[tuple] = []
    monkeypatch.setattr(
        client, "_request",
        lambda method, path, **kw: calls.append((method, path, kw)) or {
            "agents": [], "topology_errors": [],
        },
    )
    monkeypatch.setattr(client, "daemon_supports", lambda min_version: True)
    client.list_agents_with_incomplete()
    assert calls == [("GET", "/api/v1/agents", {"params": None})]


def test_live_message_payload_includes_expected_session(cfg_dir: Path, monkeypatch):
    client = BridgeClient.from_config()
    calls = []
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, payload, **kwargs:
            calls.append((method, path, payload, kwargs)) or {},
    )
    client.send_live_message(
        "session-1",
        sender="dispatch",
        body="wake",
        idempotency_key="wake-1",
        expected_session_id="session-1",
    )
    assert calls[0][2]["idempotency_key"] == "wake-1"
    assert calls[0][2]["expected_session_id"] == "session-1"


def test_live_message_payload_includes_non_default_delivery(cfg_dir: Path, monkeypatch):
    client = BridgeClient.from_config()
    calls = []
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, path, payload, **kwargs:
            calls.append((method, path, payload, kwargs)) or {},
    )
    client.send_live_message(
        "session-1",
        sender="dispatch",
        body="wake",
        delivery="queue",
    )
    assert calls[0][2]["delivery"] == "queue"
