from __future__ import annotations

import pytest

from agent_bridge import __main__ as cli
from agent_bridge import worktree_head
from agent_bridge.caller_project import CallerProjectContextError, owning_project
from agent_bridge.worktree_head import HeadInfo


@pytest.fixture(autouse=True)
def isolate_caller(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", None)
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: None)
    monkeypatch.setattr(worktree_head, "resolve_head", lambda wid: HeadInfo(
        tracked=True, active=True, head_session="child",
    ))


def test_detached_child_uses_cwd_head_despite_stale_inherited_parent(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "host/other/worktree#parent")
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "child")
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: {
        "owner-ref": "host/harness/worktree#child", "project": "knowledge",
    }.get(key))
    assert cli._sender_repo() == "harness"


@pytest.mark.parametrize("qualifier", ["", "#parent"])
def test_only_current_head_not_resolved_reference_session_selects_caller(monkeypatch, qualifier):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "child")
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: "space/harness/worktree" + qualifier)
    assert owning_project() == "harness"
    assert cli._sender_repo() == "harness"


def test_explicit_project_override_wins(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", "host/harness/worktree#session")
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "session")
    monkeypatch.setattr(cli, "_PROJECT_OVERRIDE", "product")
    assert cli._sender_repo() == "product"


@pytest.mark.parametrize("head", [
    HeadInfo(tracked=True, active=True, head_session="parent"),
    HeadInfo(tracked=True, active=False, head_session="child"),
    HeadInfo(tracked=True, active=True),
    HeadInfo(tracked=False, active=False),
])
def test_stale_or_unknown_head_refuses_dispatch(monkeypatch, capsys, head):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "child")
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: "host/harness/worktree#child")
    monkeypatch.setattr(worktree_head, "resolve_head", lambda wid: head)
    with pytest.raises(CallerProjectContextError):
        owning_project()
    with pytest.raises(SystemExit) as error:
        cli._sender_repo()
    assert error.value.code == 2
    assert "rebind the worktree" in capsys.readouterr().err


@pytest.mark.parametrize("reference", [
    "bare", "host/harness/worktree#", "host/ /worktree#child",
    "host/harness/extra/worktree", "host/harness/worktree#child#extra",
])
def test_invalid_resolved_owner_refuses_dispatch(monkeypatch, reference):
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: reference)
    with pytest.raises(CallerProjectContextError):
        owning_project()


def test_managed_cwd_with_missing_owner_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: {
        "worktree-dir": "managed", "project": "knowledge",
    }.get(key))
    with pytest.raises(CallerProjectContextError):
        owning_project()


@pytest.mark.parametrize("session", ["unsafe!session", "", "s" * 129])
def test_invalid_native_session_does_not_silently_drop_management(monkeypatch, session):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", session)
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: "host/harness/worktree")
    with pytest.raises(CallerProjectContextError):
        owning_project()


def test_plain_terminal_can_resolve_a_worktree_without_native_session(monkeypatch):
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: "host/harness/worktree")
    assert owning_project() == "harness"


def test_singleton_owner_has_no_worktree_head(monkeypatch):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "child")
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: "host/harness/@anchor#child")
    monkeypatch.setattr(worktree_head, "resolve_head", lambda wid: pytest.fail("not a worktree"))
    assert owning_project() == "harness"


def test_unmanaged_cli_uses_existing_cwd_resolution(monkeypatch):
    monkeypatch.setattr(cli, "_worktrees_get", lambda key: "standalone" if key == "project" else None)
    assert cli._sender_repo() == "standalone"
