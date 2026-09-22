"""Tests for ``finalize._warn_of_codespace_claims_for_worktree`` -- the
"worst case at worktree finalization" safety net (claim-consistency sweep,
agent-bridge-cli-mode-sessions Phase 4 follow-up): finalize's own
``tracking.release_all_resources`` only clears THIS repo's bookkeeping
ledger, never even queries the real ``agent-codespaces`` claim, so this
step reads it and WARNS with the exact release command -- it must never
release anything itself. Only the current session and an active handoff
are ever entitled to self-release a claim at finalize time; every other
live claim is the calling agent's call, made after confirming it's
genuinely no longer needed.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from agent_worktrees import finalize


def _fake_run(stdout: str, returncode: int = 0):
    def fake_run(argv, **kwargs):
        return type("R", (), {"returncode": returncode, "stdout": stdout})()
    return fake_run


def test_warns_and_lists_release_commands_without_releasing(monkeypatch, capsys):
    monkeypatch.setattr(finalize.shutil, "which", lambda name: "/bin/agent-codespaces")
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return type("R", (), {
            "returncode": 0,
            "stdout": json.dumps([
                {"codespace": "cs-a", "owner": "wt-123", "kind": "claim"},
                {"codespace": "cs-b", "owner": "wt-123", "kind": "claim"},
            ]),
        })()

    with patch("subprocess.run", side_effect=fake_run) as run:
        finalize._warn_of_codespace_claims_for_worktree("wt-123")

    run.assert_called_once()
    assert seen["argv"] == [
        "/bin/agent-codespaces", "leases", "--owner", "wt-123", "--json",
    ]
    out = capsys.readouterr().out
    assert "release-claim cs-a --owner wt-123" in out
    assert "release-claim cs-b --owner wt-123" in out
    assert "release-claim --owner wt-123 --all" in out


def test_no_claims_is_silent(monkeypatch, capsys):
    monkeypatch.setattr(finalize.shutil, "which", lambda name: "/bin/agent-codespaces")
    with patch("subprocess.run", side_effect=_fake_run("[]")):
        finalize._warn_of_codespace_claims_for_worktree("wt-123")
    assert capsys.readouterr().out == ""


def test_never_releases_anything(monkeypatch):
    """The function must only ever invoke `leases` (read-only), never
    `release-claim` or `release-claim --all`."""
    monkeypatch.setattr(finalize.shutil, "which", lambda name: "/bin/agent-codespaces")
    payload = json.dumps([{"codespace": "cs-a", "owner": "wt-123", "kind": "claim"}])
    with patch("subprocess.run", side_effect=_fake_run(payload)) as run:
        finalize._warn_of_codespace_claims_for_worktree("wt-123")
    for call in run.call_args_list:
        argv = call.args[0]
        assert "release-claim" not in argv


def test_no_binstub_is_a_silent_noop(monkeypatch):
    monkeypatch.setattr(finalize.shutil, "which", lambda name: None)
    with patch("subprocess.run") as run:
        finalize._warn_of_codespace_claims_for_worktree("wt-123")
    run.assert_not_called()


def test_a_subprocess_failure_never_raises(monkeypatch):
    monkeypatch.setattr(finalize.shutil, "which", lambda name: "/bin/agent-codespaces")
    with patch("subprocess.run", side_effect=RuntimeError("boom")):
        finalize._warn_of_codespace_claims_for_worktree("wt-123")  # must not raise


def test_a_nonzero_exit_is_treated_as_no_claims(monkeypatch, capsys):
    monkeypatch.setattr(finalize.shutil, "which", lambda name: "/bin/agent-codespaces")
    with patch("subprocess.run", side_effect=_fake_run("", returncode=1)):
        finalize._warn_of_codespace_claims_for_worktree("wt-123")
    assert capsys.readouterr().out == ""
