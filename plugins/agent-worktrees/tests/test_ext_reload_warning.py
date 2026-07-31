"""Tests for the temporary extension-reload hang warning deploy.

Covers the per-project ``ext-reload-hang.instructions.md`` custom-instruction
that warns agents about the CAR extension-reload generation-race hang
(github/copilot-agent-runtime#13492; fix #13494). It is delivered exactly like
the worktree-conduct guide -- a marked file in the project's
COPILOT_CUSTOM_INSTRUCTIONS_DIRS dir. Remove this test when the whole feature is
retired after the fix ships everywhere.
"""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import __main__ as m


def _warning_file(proj_dir: Path) -> Path:
    return proj_dir / ".github" / "instructions" / "ext-reload-hang.instructions.md"


def test_deploys_marked_warning(tmp_path: Path):
    proj = tmp_path / ".proj"
    m._deploy_ext_reload_warning(proj)

    path = _warning_file(proj)
    assert path.exists(), "warning should deploy into the project instructions dir"
    text = path.read_text()
    # Delivered like worktree-conduct: ownership marker first, no frontmatter.
    assert text.startswith(m._INSTRUCTION_MARKER)
    assert "Bare resume" in text
    assert "github/copilot-agent-runtime#13492" in text


def test_deploy_is_idempotent(tmp_path: Path):
    proj = tmp_path / ".proj"
    m._deploy_ext_reload_warning(proj)
    path = _warning_file(proj)
    first = path.read_text()
    mtime = path.stat().st_mtime_ns

    m._deploy_ext_reload_warning(proj)
    assert path.read_text() == first
    assert path.stat().st_mtime_ns == mtime, "in-sync file must not be rewritten"


def test_matches_worktree_conduct_shape(tmp_path: Path):
    """The warning rides the exact same delivery as the worktree-conduct guide."""
    proj = tmp_path / ".proj"
    m._deploy_worktree_conduct(proj)
    m._deploy_ext_reload_warning(proj)

    instr_dir = proj / ".github" / "instructions"
    conduct = instr_dir / "worktree-conduct.instructions.md"
    warning = instr_dir / "ext-reload-hang.instructions.md"
    assert conduct.exists() and warning.exists()
    # Both sit in the same dir and both lead with the ownership marker.
    assert conduct.read_text().startswith(m._INSTRUCTION_MARKER)
    assert warning.read_text().startswith(m._INSTRUCTION_MARKER)


def test_cleanup_removes_marked_warning(tmp_path: Path):
    proj = tmp_path / ".proj"
    m._deploy_ext_reload_warning(proj)
    assert _warning_file(proj).exists()

    # When machines.yaml is absent, stale marked instruction files are removed.
    m._cleanup_stale_instructions(proj)
    assert not _warning_file(proj).exists()


def test_cleanup_leaves_unmarked_user_file_alone(tmp_path: Path):
    proj = tmp_path / ".proj"
    path = _warning_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# user's own instructions, not ours\n")

    m._cleanup_stale_instructions(proj)
    assert path.exists(), "an unmarked user file must never be deleted"
