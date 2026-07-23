"""Tests for the parent-session context hint (Fix B: mux/path alignment).

A worktree with no session of its own must NOT auto-resume its originating
``parent_session`` -- that session belongs to a different worktree, and
Copilot's resume-auto-cd would adopt its persisted cwd, launching this tab in
the parent's directory (worktree id/path mismatch). The resume path now surfaces
the parent only as a *hint*; ``_emit_parent_context_hint`` renders it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from conftest import make_session_dir

from agent_worktrees.__main__ import _emit_parent_context_hint


def _patch_state_dir(tmp_session_state_dir: Path):
    return patch(
        "agent_worktrees.sessions._session_state_dir",
        return_value=tmp_session_state_dir,
    )


class TestEmitParentContextHint:
    def test_hint_printed_to_stdout_for_valid_parent(
        self, tmp_session_state_dir: Path, capsys
    ):
        make_session_dir(tmp_session_state_dir, "parent-sess", "/tmp/parent-wt",
                         summary="origin work")
        record = SimpleNamespace(parent_session="parent-sess")
        with _patch_state_dir(tmp_session_state_dir):
            _emit_parent_context_hint(record)
        out = capsys.readouterr()
        assert "parent-sess" in out.out
        assert "/resume" in out.out
        assert out.err == ""

    def test_hint_routed_to_stderr_when_requested(
        self, tmp_session_state_dir: Path, capsys
    ):
        # The JSON-emitting launch path must not corrupt stdout, so the hint
        # goes to stderr there.
        make_session_dir(tmp_session_state_dir, "parent-sess", "/tmp/parent-wt",
                         summary="origin work")
        record = SimpleNamespace(parent_session="parent-sess")
        with _patch_state_dir(tmp_session_state_dir):
            _emit_parent_context_hint(record, to_stderr=True)
        out = capsys.readouterr()
        assert out.out == ""
        assert "parent-sess" in out.err

    def test_no_output_for_missing_parent(
        self, tmp_session_state_dir: Path, capsys
    ):
        record = SimpleNamespace(parent_session=None)
        with _patch_state_dir(tmp_session_state_dir):
            _emit_parent_context_hint(record)
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == ""

    def test_no_output_for_stale_parent_pointer(
        self, tmp_session_state_dir: Path, capsys
    ):
        # A parent_session whose state dir is gone/pruned is not resumable and
        # must not be surfaced as a live pointer.
        record = SimpleNamespace(parent_session="pruned-sess")
        with _patch_state_dir(tmp_session_state_dir):
            _emit_parent_context_hint(record)
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == ""
