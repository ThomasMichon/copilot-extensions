from __future__ import annotations

import pytest

from agent_bridge import __main__ as cli
from agent_bridge.caller_project import owning_project


@pytest.fixture(autouse=True)
def isolate_caller(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", None)


def test_owning_harness_preferred_over_knowledge_checkout_and_probe_failure(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "host/harness/worktree#session")
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session")
    for result in ("knowledge", None):
        monkeypatch.setattr(cli, "_worktrees_get", lambda key: result)
        assert cli._sender_repo() == "harness"


def test_explicit_project_override_wins(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "host/harness/worktree#session")
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session")
    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", "product")
    assert cli._sender_repo() == "product"


@pytest.mark.parametrize("reference,session", [
    ("host/harness/worktree#old-session", "new-session"),
    ("host/harness/worktree", "session"),
    ("host/harness/worktree#session", ""),
    ("host/harness/extra/worktree#session", "session"),
    ("host/ /worktree#session", "session"),
    ("host/harness/worktree#session#extra", "session"),
])
def test_unproven_owner_does_not_rebind_project(monkeypatch, reference, session):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", reference)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", session)
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: "cwd-project")
    assert owning_project() is None
    assert cli._sender_repo() == "cwd-project"


def test_unmanaged_cli_uses_existing_cwd_resolution(monkeypatch):
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: "standalone")
    assert cli._sender_repo() == "standalone"
