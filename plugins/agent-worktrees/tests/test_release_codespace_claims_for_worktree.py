"""Tests for ``finalize._release_codespace_claims_for_worktree`` -- the
"worst case at worktree finalization" safety net (claim-consistency sweep,
agent-bridge-cli-mode-sessions Phase 4 follow-up): finalize's own
``tracking.release_all_resources`` only clears THIS repo's bookkeeping
ledger, never the real ``agent-codespaces`` claim, so this shells out to
actually release it too. Deliberately over-releases (``--all``) rather than
trying to resolve exactly which CodeSpace(s) the worktree held.
"""
from __future__ import annotations

from unittest.mock import patch

from agent_worktrees import finalize


def test_shells_release_claim_all_for_owner(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(finalize.shutil, "which", lambda name: "/bin/agent-codespaces")
    with patch("subprocess.run", side_effect=fake_run) as run:
        finalize._release_codespace_claims_for_worktree("wt-123")

    run.assert_called_once()
    assert seen["argv"] == [
        "/bin/agent-codespaces", "release-claim", "--owner", "wt-123", "--all",
    ]


def test_no_binstub_is_a_silent_noop(monkeypatch):
    monkeypatch.setattr(finalize.shutil, "which", lambda name: None)
    with patch("subprocess.run") as run:
        finalize._release_codespace_claims_for_worktree("wt-123")
    run.assert_not_called()


def test_a_subprocess_failure_never_raises(monkeypatch):
    monkeypatch.setattr(finalize.shutil, "which", lambda name: "/bin/agent-codespaces")
    with patch("subprocess.run", side_effect=RuntimeError("boom")):
        finalize._release_codespace_claims_for_worktree("wt-123")  # must not raise
