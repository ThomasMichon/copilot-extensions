"""Tests for the account-conduct managed custom-instruction deploy.

Covers the per-project ``account-conduct.instructions.md`` that reminds agents
to match the active ``gh`` account to the target repo's owner before ad-hoc
``gh`` ops (multi-account gh hygiene). Delivered exactly like the
worktree-conduct guide -- a marked file in the project's
COPILOT_CUSTOM_INSTRUCTIONS_DIRS dir.
"""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import __main__ as m


def _conduct_file(proj_dir: Path) -> Path:
    return proj_dir / ".github" / "instructions" / "account-conduct.instructions.md"


def test_deploys_marked_conduct(tmp_path: Path):
    proj = tmp_path / ".proj"
    m._deploy_account_conduct(proj)

    path = _conduct_file(proj)
    assert path.exists(), "conduct should deploy into the project instructions dir"
    text = path.read_text()
    # Delivered like worktree-conduct: ownership marker first, no frontmatter.
    assert text.startswith(m._INSTRUCTION_MARKER)
    # Names the resolver + injection mechanics agents must follow.
    assert "repos account-for" in text
    assert "repos gh" in text
    assert "GH_TOKEN" in text


def test_deploy_is_idempotent(tmp_path: Path):
    proj = tmp_path / ".proj"
    m._deploy_account_conduct(proj)
    path = _conduct_file(proj)
    first = path.read_text()
    mtime = path.stat().st_mtime_ns

    m._deploy_account_conduct(proj)
    assert path.read_text() == first
    assert path.stat().st_mtime_ns == mtime, "in-sync file must not be rewritten"


def test_matches_worktree_conduct_shape(tmp_path: Path):
    """The conduct rides the exact same delivery as the worktree-conduct guide."""
    proj = tmp_path / ".proj"
    m._deploy_worktree_conduct(proj)
    m._deploy_account_conduct(proj)

    instr_dir = proj / ".github" / "instructions"
    worktree = instr_dir / "worktree-conduct.instructions.md"
    account = instr_dir / "account-conduct.instructions.md"
    assert worktree.exists() and account.exists()
    # Both sit in the same dir and both lead with the ownership marker.
    assert worktree.read_text().startswith(m._INSTRUCTION_MARKER)
    assert account.read_text().startswith(m._INSTRUCTION_MARKER)


def test_cleanup_removes_marked_conduct(tmp_path: Path):
    proj = tmp_path / ".proj"
    m._deploy_account_conduct(proj)
    assert _conduct_file(proj).exists()

    # When machines.yaml is absent, stale marked instruction files are removed.
    m._cleanup_stale_instructions(proj)
    assert not _conduct_file(proj).exists()


def test_cleanup_leaves_unmarked_user_file_alone(tmp_path: Path):
    proj = tmp_path / ".proj"
    path = _conduct_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# user's own instructions, not ours\n")

    m._cleanup_stale_instructions(proj)
    assert path.exists(), "an unmarked user file must never be deleted"
