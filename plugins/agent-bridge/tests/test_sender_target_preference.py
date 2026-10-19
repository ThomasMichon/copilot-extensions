from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_bridge import __main__ as cli
from agent_bridge.client import BridgeClientError
from agent_bridge.models import PhasedTimeouts
from agent_bridge.session_start_cli import _sender_repo_for_target, _start_agent_session
from agent_bridge.session_targeting_cli import _resolve_target


@pytest.fixture(autouse=True)
def invalid_owner(monkeypatch):
    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", None)
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "host/harness/wt#old")
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "new")


@pytest.mark.parametrize("target", [
    "product@machine", "product@codespace:box", "product@container:box",
])
def test_explicit_repo_targets_skip_sender_derivation(target):
    assert _sender_repo_for_target(target) is None


@pytest.mark.parametrize("target", ["codespace:box", "container:box", "custom:resource"])
def test_unlisted_namespace_does_not_consume_sender(target):
    assert _sender_repo_for_target(target, agent_inventory=[]) is None


def test_named_nonmachine_agent_does_not_consume_sender():
    inventory = [
        {"name": "worker", "derived": False, "host": "machine"},
    ]
    assert _sender_repo_for_target("worker", agent_inventory=inventory) is None


def test_bare_namespace_friendly_alias_uses_existing_matcher_without_sender():
    inventory = [
        {"name": "custom:raw", "aliases": ["custom:friendly"], "host": "", "provider": "custom"},
    ]
    assert _sender_repo_for_target("friendly", agent_inventory=inventory) is None


def test_bare_machine_retains_invalid_owner_refusal():
    inventory = [
        {"name": "machine", "derived": True, "host": "machine"},
    ]
    with pytest.raises(SystemExit) as error:
        _sender_repo_for_target("machine", agent_inventory=inventory)
    assert error.value.code == 2


def test_known_colon_machine_alias_still_consumes_preference():
    inventory = [
        {"name": "machine", "aliases": ["space:windows"], "derived": True, "host": "machine"},
    ]
    with pytest.raises(SystemExit):
        _sender_repo_for_target("space:windows", agent_inventory=inventory)


def test_valid_bare_machine_preserves_harness_preference(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "host/harness/wt")
    inventory = [
        {"name": "machine", "derived": True, "host": "machine"},
    ]
    assert _sender_repo_for_target("machine", agent_inventory=inventory) == "harness"


@pytest.mark.parametrize("target", ["product@machine", "custom:resource", "machine"])
def test_actual_start_request_scopes_owner_validation_to_preference_consumers(monkeypatch, target):
    requests = []
    monkeypatch.setattr(cli, "_get_caller_id", lambda: None)
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: None)
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: PhasedTimeouts())
    monkeypatch.setattr(cli, "_wait_for_idle", lambda *args, **kwargs: None)
    inventory = [{"name": "machine", "derived": True, "host": "machine"}]
    def forbidden():
        raise AssertionError("start must reuse the supplied roster, not query again")
    client = SimpleNamespace(
        list_agents=forbidden,
        start_session=lambda **kwargs: requests.append(kwargs) or {
            "session_id": "created", "name": "test", "status": "idle",
        },
    )
    if target == "machine":
        with pytest.raises(SystemExit):
            _start_agent_session(client, target, force_new=True, agent_inventory=inventory)
        assert requests == []
    else:
        assert _start_agent_session(client, target, force_new=True, agent_inventory=inventory) == "created"
        assert requests[0]["sender_repo"] is None


def test_degraded_target_resolution_survives_one_failed_roster_read(monkeypatch, capsys):
    requests = []
    roster_reads = []
    monkeypatch.setattr(cli, "_get_caller_id", lambda: None)
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: None)
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: PhasedTimeouts())
    monkeypatch.setattr(cli, "_wait_for_idle", lambda *args, **kwargs: None)
    def unavailable():
        roster_reads.append(True)
        raise BridgeClientError(503, "roster temporarily unavailable")
    client = SimpleNamespace(
        get_session=lambda target: None,
        list_sessions=lambda: [],
        list_agents=unavailable,
        start_session=lambda **kwargs: requests.append(kwargs) or {
            "session_id": "created", "name": "test", "status": "idle",
        },
    )
    assert _resolve_target(client, "custom:resource", force_new=True) == "created"
    assert len(roster_reads) == 1
    assert requests[0]["sender_repo"] is None
    assert "Agent roster unavailable (HTTP 503)" in capsys.readouterr().err


@pytest.mark.parametrize("machine", [False, True])
def test_known_session_restart_preflights_before_end(monkeypatch, machine):
    events = []
    agent = "machine" if machine else "worker"
    monkeypatch.setattr(cli, "_get_caller_id", lambda: None)
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: None)
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: PhasedTimeouts())
    monkeypatch.setattr(cli, "_wait_for_idle", lambda *args, **kwargs: None)
    client = SimpleNamespace(
        get_session=lambda target: {
            "session_id": "busy", "agent_name": agent, "status": "running",
        },
        list_sessions=lambda: [],
        list_agents=lambda: events.append("roster") or [
            {"name": agent, "derived": machine, "host": "host"},
        ],
        end_session=lambda sid: events.append("end"),
        start_session=lambda **kwargs: events.append(("start", kwargs["sender_repo"])) or {
            "session_id": "new", "name": "test", "status": "idle",
        },
    )
    if machine:
        with pytest.raises(SystemExit):
            _resolve_target(client, "busy", force=True)
        assert events == ["roster"]
    else:
        assert _resolve_target(client, "busy", force=True) == "new"
        assert events == ["roster", "end", ("start", None)]


def test_force_takeover_keeps_existing_session_when_preflight_fails(monkeypatch):
    events = []
    monkeypatch.setattr(cli, "_get_caller_id", lambda: "caller")
    client = SimpleNamespace(
        list_sessions=lambda: [{
            "session_id": "busy", "agent_name": "machine", "caller_id": "caller",
            "status": "running",
        }],
        end_session=lambda sid: events.append("end"),
        start_session=lambda **kwargs: events.append("start"),
    )
    with pytest.raises(SystemExit):
        _start_agent_session(
            client, "machine", force=True,
            agent_inventory=[{"name": "machine", "derived": True, "host": "host"}],
        )
    assert events == []


@pytest.mark.parametrize("target", [
    "remote-acp:assistant", "https://example.com/acp", "wss://example.com/acp",
    "https://example.com/acp@agent",
])
def test_remote_cli_start_supplies_explicit_project_to_request(monkeypatch, target):
    from agent_bridge.client import BridgeClient

    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", "demo")
    monkeypatch.setattr(cli, "_get_caller_id", lambda: None)
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: None)
    monkeypatch.setattr(cli, "_wait_for_idle", lambda *args, **kwargs: None)
    requests = []
    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda version: True
    client._request = lambda *args, **kwargs: requests.append(args) or {
        "session_id": "created", "name": "remote", "status": "idle",
    }
    assert _start_agent_session(
        client, target, force_new=True, agent_inventory=[],
    ) == "created"
    assert requests[0][0:2] == ("POST", "/api/v1/sessions")
    assert requests[0][2]["sender_repo"] == "demo"


@pytest.mark.parametrize("target", ["remote-acp:example", "https://example.com/acp"])
@pytest.mark.parametrize("force", [False, True])
def test_remote_cli_reuse_and_takeover_require_daemon_binding_resolution(monkeypatch, target, force):
    from agent_bridge.client import BridgeClient

    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", "selected")
    monkeypatch.setattr(cli, "_get_caller_id", lambda: "caller")
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: None)
    monkeypatch.setattr(cli, "_wait_for_idle", lambda *args, **kwargs: None)
    requests = []
    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda version: True

    def request(method, path, *args, **kwargs):
        requests.append((method, path, args))
        assert (method, path) == ("POST", "/api/v1/sessions")
        return {"session_id": "validated", "name": "example", "status": "idle"}

    client._request = request
    assert _start_agent_session(client, target, force=force) == "validated"
    assert requests[0][2][0]["sender_repo"] == "selected"
    assert requests[0][2][0]["caller_id"] == "caller"


@pytest.mark.parametrize("version", [38, 39, 40, 41])
def test_named_remote_reuse_keeps_legacy_daemon_continuity(monkeypatch, version):
    from agent_bridge.client import BridgeClient

    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", "selected")
    monkeypatch.setattr(cli, "_get_caller_id", lambda: "caller")
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: None)
    monkeypatch.setattr(cli, "_wait_for_idle", lambda *args, **kwargs: None)
    requests = []
    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda required: version >= required

    def request(method, path, *args, **kwargs):
        requests.append((method, path))
        if (method, path) == ("GET", "/api/v1/sessions"):
            return {"sessions": [{
                "session_id": "legacy", "agent_name": "remote-acp:example",
                "name": "example", "caller_id": "caller", "status": "idle",
            }]}
        assert (method, path) == ("POST", "/api/v1/sessions")
        return {"session_id": "validated", "name": "example", "status": "idle"}

    client._request = request
    assert _start_agent_session(client, "remote-acp:example") == (
        "validated" if version == 41 else "legacy"
    )
    assert requests == [(
        "POST" if version == 41 else "GET", "/api/v1/sessions",
    )]
