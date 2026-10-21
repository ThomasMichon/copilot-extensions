"""The Git-only fixture never reaches host mux discovery or termination."""

import pytest

import conftest
from agent_worktrees import sessions


@pytest.mark.parametrize("operation", ["has_mux_session", "kill_tmux_session"])
def test_git_only_mux_blocks_host_calls_and_restores_bindings(monkeypatch, operation):
    calls = []

    def host_call(worktree):
        calls.append(worktree)
        raise AssertionError("Git-only test reached host mux")

    monkeypatch.setattr(sessions, operation, host_call)
    with pytest.MonkeyPatch.context() as patch:
        conftest.git_only_mux.__wrapped__(patch)
        assert getattr(sessions, operation)("synthetic-worktree") is False
        assert calls == []
    assert getattr(sessions, operation) is host_call
    with pytest.raises(AssertionError, match="reached host mux"):
        getattr(sessions, operation)("restored-binding")
    assert calls == ["restored-binding"]
