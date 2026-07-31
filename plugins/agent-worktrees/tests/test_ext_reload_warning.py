"""Tests for the temporary extension-reload hang warning deploy.

Covers the machine-wide ``~/.copilot/instructions/`` warning injection that
warns agents about the CAR extension-reload generation-race hang
(github/copilot-agent-runtime#13492; fix #13494). Remove this test when the
whole feature is retired after the fix ships everywhere.
"""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import __main__ as m


def _warning_file(home: Path) -> Path:
    return home / ".copilot" / "instructions" / "agent-worktrees-ext-reload-hang.instructions.md"


def test_deploys_marked_warning_when_fix_unreleased(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(m.cfg, "_home", lambda: home)
    monkeypatch.setattr(m, "_EXT_RELOAD_FIX_VERSION", None)

    m._deploy_ext_reload_warning()

    path = _warning_file(home)
    assert path.exists(), "warning should deploy when the fix is unreleased"
    text = path.read_text(encoding="utf-8")
    assert m._INSTRUCTION_MARKER in text
    assert "Bare resume" in text
    assert "github/copilot-agent-runtime#13492" in text


def test_deploy_is_idempotent(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(m.cfg, "_home", lambda: home)
    monkeypatch.setattr(m, "_EXT_RELOAD_FIX_VERSION", None)

    m._deploy_ext_reload_warning()
    path = _warning_file(home)
    first = path.read_text(encoding="utf-8")
    mtime = path.stat().st_mtime_ns

    m._deploy_ext_reload_warning()
    assert path.read_text(encoding="utf-8") == first
    assert path.stat().st_mtime_ns == mtime, "in-sync file must not be rewritten"


def test_removes_marked_warning_when_cli_fixed(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(m.cfg, "_home", lambda: home)

    # First deploy while unfixed.
    monkeypatch.setattr(m, "_EXT_RELOAD_FIX_VERSION", None)
    m._deploy_ext_reload_warning()
    assert _warning_file(home).exists()

    # Now the running CLI carries the fix -> the marked file self-cleans.
    monkeypatch.setattr(m, "_EXT_RELOAD_FIX_VERSION", "1.0.80")
    monkeypatch.setattr(m, "_installed_copilot_version", lambda: "1.0.80")
    m._deploy_ext_reload_warning()
    assert not _warning_file(home).exists()


def test_keeps_warning_when_cli_predates_fix(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(m.cfg, "_home", lambda: home)
    monkeypatch.setattr(m, "_EXT_RELOAD_FIX_VERSION", "1.0.80")
    monkeypatch.setattr(m, "_installed_copilot_version", lambda: "1.0.79")

    m._deploy_ext_reload_warning()
    assert _warning_file(home).exists(), "older CLI is still affected -> warn"


def test_remove_leaves_unmarked_user_file_alone(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(m.cfg, "_home", lambda: home)

    path = _warning_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# user's own instructions, not ours\n", encoding="utf-8")

    m._remove_ext_reload_warning()
    assert path.exists(), "an unmarked user file must never be deleted"
