"""Tests for the temporary extension-reload hang warning, now delivered via the
``session-ext-reload`` sessionStart hook.

Migrated (dotfiles#1055 / effort instructions-to-hooks) from a per-project
``ext-reload-hang.instructions.md`` loaded via COPILOT_CUSTOM_INSTRUCTIONS_DIRS.
The canonical warning text now lives in ``scripts/ext-reload-hang.md`` (deployed
to ~/.agent-worktrees/bin/ and emitted as ``additionalContext`` by the
session-ext-reload hook); the deploy path only retires any stale copy of the old
file. Unlike the account/worktree-conduct fragments the injector is NOT strictly
cwd-gated -- it also fires at cwd=~/ so it reaches a Bare resume session, the
exact scenario this warning covers. Remove this whole test when the feature is
retired after github/copilot-agent-runtime#13494 ships everywhere.
"""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import __main__ as m


def _warning_file(proj_dir: Path) -> Path:
    return proj_dir / ".github" / "instructions" / "ext-reload-hang.instructions.md"


def _plugin_root() -> Path:
    # .../src/agent_worktrees/__main__.py -> the plugin root is parents[2].
    return Path(m.__file__).resolve().parents[2]


def _fragment() -> Path:
    return _plugin_root() / "scripts" / "ext-reload-hang.md"


def test_fragment_present_and_shaped():
    frag = _fragment()
    assert frag.exists(), "ext-reload-hang warning must ship as a bin fragment"
    text = frag.read_text(encoding="utf-8")
    # Plain guidance emitted as additionalContext -- NO ownership marker /
    # frontmatter (it is not scanned as a *.instructions.md file).
    assert not text.lstrip().startswith(m._INSTRUCTION_MARKER)
    assert "github/copilot-agent-runtime#13492" in text
    assert "github/copilot-agent-runtime#13494" in text
    assert "waiting for that fix to reach the installed Copilot CLI" in text
    assert "Bare resume" in text


def test_injector_scripts_present_and_not_strictly_cwd_gated():
    scripts = _plugin_root() / "scripts"
    ps1 = scripts / "session-ext-reload.ps1"
    sh = scripts / "session-ext-reload.sh"
    assert ps1.exists() and sh.exists(), "both session-ext-reload injectors must ship"
    # The whole point of this migration: the injector still fires at cwd=~/
    # (Bare resume), so it must consult HOME, not only the get-project gate.
    assert "USERPROFILE" in ps1.read_text(encoding="utf-8")
    assert "HOME" in sh.read_text(encoding="utf-8")


def test_per_project_deploy_retired():
    # The per-project file deploy is gone; only the cleanup/retire helpers remain.
    assert not hasattr(m, "_deploy_ext_reload_warning")
    assert not hasattr(m, "_EXT_RELOAD_WARNING")
    assert hasattr(m, "_remove_managed_instruction")


def test_remove_managed_instruction_retires_marked(tmp_path: Path):
    proj = tmp_path / ".proj"
    path = _warning_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{m._INSTRUCTION_MARKER}\n# stale managed warning\n")

    m._remove_managed_instruction(proj, "ext-reload-hang.instructions.md")
    assert not path.exists(), "a stale marked file must be retired on deploy"


def test_remove_managed_instruction_leaves_unmarked_user_file(tmp_path: Path):
    proj = tmp_path / ".proj"
    path = _warning_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# user's own instructions, not ours\n")

    m._remove_managed_instruction(proj, "ext-reload-hang.instructions.md")
    assert path.exists(), "an unmarked user file must never be deleted"


def test_cleanup_still_sweeps_stale_warning(tmp_path: Path):
    # A stale marked file is also swept by _cleanup_stale_instructions (the
    # machines.yaml-absent path), which still lists it.
    proj = tmp_path / ".proj"
    path = _warning_file(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{m._INSTRUCTION_MARKER}\n# stale\n")

    m._cleanup_stale_instructions(proj)
    assert not path.exists()
