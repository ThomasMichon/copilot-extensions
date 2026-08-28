"""Tests for the account-conduct guidance, now delivered via the session-conduct
sessionStart hook.

Migrated (dotfiles#1053 / effort instructions-to-hooks) from a per-project
``account-conduct.instructions.md`` loaded via COPILOT_CUSTOM_INSTRUCTIONS_DIRS.
The static guidance text now lives in ``scripts/conduct/account-conduct.md`` and
is emitted as ``additionalContext`` by the cwd-gated session-conduct hook; the
deploy path only retires any stale copy of the old file.
"""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import __main__ as m


def _conduct_file(proj_dir: Path) -> Path:
    return proj_dir / ".github" / "instructions" / "account-conduct.instructions.md"


def _conduct_fragment() -> Path:
    # .../src/agent_worktrees/__main__.py -> the plugin root is parents[2].
    plugin_root = Path(m.__file__).resolve().parents[2]
    return plugin_root / "scripts" / "conduct" / "account-conduct.md"


def test_conduct_fragment_present_and_shaped():
    frag = _conduct_fragment()
    assert frag.exists(), "account-conduct guidance must ship as a conduct fragment"
    text = frag.read_text(encoding="utf-8")
    # Plain guidance -- no ownership marker / frontmatter: it is emitted as
    # additionalContext, not scanned as a *.instructions.md file.
    assert not text.lstrip().startswith(m._INSTRUCTION_MARKER)
    # Requires repo/account discovery before mutation, then keeps ordinary
    # execution race-safe while delegating ambient-account exceptions.
    assert "Before contributing code" in text
    assert "mutating an issue, PR, release, or settings" in text
    assert "identify target `owner/repo`" in text
    assert "never infer its account" in text
    assert "repos account-for" in text
    assert "repos gh" in text
    assert "active account is global/shared/racy" in text
    assert "never switch ordinarily" in text
    assert "Account-scoped transports (for example CodeSpaces) and auth repair" in text
    assert "are\nexceptions" in text
    assert "their owning skill" in text
    assert "restore the prior account" in text
    assert "resolves per process" in text
    assert "ambient fallback, verify identity" in text
    assert "agent-worktrees:agent-worktrees-repos" in text
    assert "GH_TOKEN" not in text
    assert len(text.rstrip()) <= 700


def test_per_project_deploy_retired():
    # The per-project file deploy is gone; only the cleanup helper remains.
    assert not hasattr(m, "_deploy_account_conduct")
    assert hasattr(m, "_remove_managed_instruction")


def _conduct_dir() -> Path:
    return Path(m.__file__).resolve().parents[2] / "scripts" / "conduct"


def test_worktree_conduct_fragment_migrated():
    # worktree-conduct migrated to the conduct/ injector too (dotfiles#1054):
    # the fragment ships and the per-project deploy helper is gone.
    frag = _conduct_dir() / "worktree-conduct.md"
    assert frag.exists(), "worktree-conduct guidance must ship as a conduct fragment"
    text = frag.read_text(encoding="utf-8")
    assert not text.lstrip().startswith(m._INSTRUCTION_MARKER)
    assert "agent-worktrees status" in text
    assert "--follow-up" in text and "--resolved" in text
    assert "--title" in text and "--summary" in text
    assert "Leftover temp state" in text
    assert "unpushed external-repo work" in text
    assert "merged-but-undeployed" in text
    assert "finalized/completed worktree" in text
    assert "resolved and safe to prune" in text
    assert "finalize" in text
    assert "reflective cue" in text
    assert "threshold-based status nudge" in text
    assert "not a mandatory heartbeat" in text
    assert "paired-worktree and other holds may" in text
    assert "delay pruning" in text
    assert "status --history" in text
    assert "agent-worktrees:worktree" in text
    assert len(text.rstrip()) <= 1_800
    assert not hasattr(m, "_deploy_worktree_conduct")


def test_remove_managed_instruction_removes_marked(tmp_path: Path):
    proj = tmp_path / ".proj"
    path = _conduct_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{m._INSTRUCTION_MARKER}\n# stale managed file\n")

    m._remove_managed_instruction(proj, "account-conduct.instructions.md")
    assert not path.exists(), "a stale marked file must be retired on deploy"


def test_remove_managed_instruction_leaves_unmarked_user_file(tmp_path: Path):
    proj = tmp_path / ".proj"
    path = _conduct_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# user's own instructions, not ours\n")

    m._remove_managed_instruction(proj, "account-conduct.instructions.md")
    assert path.exists(), "an unmarked user file must never be deleted"


def test_cleanup_still_sweeps_stale_account_conduct(tmp_path: Path):
    # A stale marked file is also swept by _cleanup_stale_instructions (the
    # machines.yaml-absent path), which still lists it.
    proj = tmp_path / ".proj"
    path = _conduct_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{m._INSTRUCTION_MARKER}\n# stale\n")

    m._cleanup_stale_instructions(proj)
    assert not path.exists()
