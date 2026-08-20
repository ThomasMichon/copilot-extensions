"""Tests for interpreter resolution in the container exec-wrapper spawn command.

Covers dotfiles #1631 (root cause): ``_venv_python`` must target the ACTIVE
versioned runtime (``versions/<current-version>``), not the legacy
``~/.agent-containers/.venv`` -- which the versioned-runtime migration stopped
updating, so preferring it made the daemon spawn the wrapper from stale code.
"""

from __future__ import annotations

import sys

from agent_containers import _invoke


def _make_runtime(root, version, *, win):
    """Create a fake versions/<version>/{Scripts|bin}/python(.exe) under root."""
    scripts = "Scripts" if win else "bin"
    exe = "python.exe" if win else "python"
    d = root / "versions" / version / scripts
    d.mkdir(parents=True)
    py = d / exe
    py.write_text("", encoding="utf-8")
    (root / "current-version").write_text(version, encoding="utf-8")
    return py


def test_prefers_active_versioned_runtime(tmp_path, monkeypatch):
    win = sys.platform == "win32"
    py = _make_runtime(tmp_path, "0.1.2-dev55", win=win)
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)
    assert _invoke._venv_python() == str(py)
    assert _invoke.module_argv() == [str(py), "-m", "agent_containers"]


def test_does_not_prefer_stale_legacy_venv(tmp_path, monkeypatch):
    # Both a legacy .venv AND an active versioned runtime exist; the versioned
    # runtime must win (the legacy .venv is the stale one).
    win = sys.platform == "win32"
    scripts = "Scripts" if win else "bin"
    exe = "python.exe" if win else "python"
    legacy = tmp_path / ".venv" / scripts
    legacy.mkdir(parents=True)
    (legacy / exe).write_text("", encoding="utf-8")
    py = _make_runtime(tmp_path, "0.1.2-dev55", win=win)
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    assert _invoke._venv_python() == str(py)


def test_falls_back_to_current_interpreter(tmp_path, monkeypatch):
    # No versioned runtime -> use the running interpreter (never stale).
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)  # no current-version / versions
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    assert _invoke._venv_python() == sys.executable


def test_ignores_current_version_pointing_at_missing_dir(tmp_path, monkeypatch):
    # A current-version pointer whose version dir doesn't exist must not be used.
    (tmp_path / "current-version").write_text("9.9.9-dev0", encoding="utf-8")
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    assert _invoke._venv_python() == sys.executable


def test_raises_when_nothing_resolvable(tmp_path, monkeypatch):
    # No versioned runtime, empty sys.executable, no legacy venv -> fail fast.
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    monkeypatch.setattr(sys, "executable", "")
    import pytest

    with pytest.raises(RuntimeError):
        _invoke._venv_python()


def test_last_resort_legacy_venv_when_no_executable(tmp_path, monkeypatch):
    win = sys.platform == "win32"
    scripts = "Scripts" if win else "bin"
    exe = "python.exe" if win else "python"
    legacy = tmp_path / ".venv" / scripts
    legacy.mkdir(parents=True)
    (legacy / exe).write_text("", encoding="utf-8")
    monkeypatch.setattr(_invoke, "_ROOT", tmp_path)  # no versioned runtime
    monkeypatch.setattr(_invoke, "_LEGACY_VENV_DIR", tmp_path / ".venv")
    monkeypatch.setattr(sys, "executable", "")
    assert _invoke._venv_python() == str(legacy / exe)
