from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_bridge import __main__ as cli
from agent_bridge.models import PhasedTimeouts
from agent_bridge.session_start_cli import _sender_repo_for_target, _start_agent_session


@pytest.fixture(autouse=True)
def invalid_owner(monkeypatch):
    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", None)
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "host/harness/wt#old")
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "new")


@pytest.mark.parametrize("target", [
    "product@machine", "product@codespace:box", "product@container:box",
])
def test_explicit_repo_targets_skip_sender_derivation(target):
    def forbidden():
        raise AssertionError("explicit target should not require an inventory lookup")
    client = SimpleNamespace(list_agents=forbidden)
    assert _sender_repo_for_target(client, target) is None


@pytest.mark.parametrize("target", ["codespace:box", "container:box", "custom:resource"])
def test_unlisted_namespace_does_not_consume_sender(target):
    client = SimpleNamespace(list_agents=lambda: [])
    assert _sender_repo_for_target(client, target) is None


def test_named_nonmachine_agent_does_not_consume_sender():
    client = SimpleNamespace(list_agents=lambda: [
        {"name": "worker", "derived": False, "host": "machine"},
    ])
    assert _sender_repo_for_target(client, "worker") is None


def test_bare_namespace_friendly_alias_uses_existing_matcher_without_sender():
    client = SimpleNamespace(list_agents=lambda: [
        {"name": "custom:raw", "aliases": ["custom:friendly"], "host": "", "provider": "custom"},
    ])
    assert _sender_repo_for_target(client, "friendly") is None


def test_bare_machine_retains_invalid_owner_refusal():
    client = SimpleNamespace(list_agents=lambda: [
        {"name": "machine", "derived": True, "host": "machine"},
    ])
    with pytest.raises(SystemExit) as error:
        _sender_repo_for_target(client, "machine")
    assert error.value.code == 2


def test_known_colon_machine_alias_still_consumes_preference():
    client = SimpleNamespace(list_agents=lambda: [
        {"name": "machine", "aliases": ["space:windows"], "derived": True, "host": "machine"},
    ])
    with pytest.raises(SystemExit):
        _sender_repo_for_target(client, "space:windows")


def test_valid_bare_machine_preserves_harness_preference(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "host/harness/wt")
    client = SimpleNamespace(list_agents=lambda: [
        {"name": "machine", "derived": True, "host": "machine"},
    ])
    assert _sender_repo_for_target(client, "machine") == "harness"


@pytest.mark.parametrize("target", ["product@machine", "custom:resource", "machine"])
def test_actual_start_request_scopes_owner_validation_to_preference_consumers(monkeypatch, target):
    requests = []
    monkeypatch.setattr(cli, "_get_caller_id", lambda: None)
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: None)
    monkeypatch.setattr(cli, "_phased_timeouts", lambda: PhasedTimeouts())
    monkeypatch.setattr(cli, "_wait_for_idle", lambda *args, **kwargs: None)
    client = SimpleNamespace(
        list_agents=lambda: [{"name": "machine", "derived": True, "host": "machine"}],
        start_session=lambda **kwargs: requests.append(kwargs) or {
            "session_id": "created", "name": "test", "status": "idle",
        },
    )
    if target == "machine":
        with pytest.raises(SystemExit):
            _start_agent_session(client, target, force_new=True)
        assert requests == []
    else:
        assert _start_agent_session(client, target, force_new=True) == "created"
        assert requests[0]["sender_repo"] is None
