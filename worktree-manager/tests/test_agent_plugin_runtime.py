"""Tests for the generic agent-* installed-runtime resolver.

Extracted from ``engine_client.py``'s agent-worktrees-specific resolution
(Phase 3b/4 follow-on, worktree-manager-control-plane effort). These tests
prove: (1) the legacy marker-selected-slot behavior is preserved for any
plugin id, (2) the namespaced root is only ever considered when the SAME
vendored installation-mode policy every agent-* plugin's own bootstrap reads
says marketplace cells are enabled, and (3) an absent/disabled policy or a
foreign/invalid explicit context always falls back to legacy -- exactly the
resolver's own "absent policy selects legacy" default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktree_manager import agent_plugin_runtime as apr


def _python_path(slot: Path) -> Path:
    return slot / ("Scripts/python.exe" if apr.os.name == "nt" else "bin/python")


def _write_slot(slot: Path) -> Path:
    python = _python_path(slot)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    return python


def _write_policy(profile: Path, *, enabled: bool) -> None:
    policy_dir = profile / ".copilot-extensions"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "installation-mode.json").write_text(
        json.dumps({
            "schema": "copilot-extensions.installation-mode",
            "version": 1,
            "installationMode": {"enabled": enabled},
        }),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clean_context(monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_HOME", raising=False)


def test_legacy_resolution_is_generic_over_plugin_id(monkeypatch, tmp_path):
    root = tmp_path / ".agent-bridge"
    slot = root / "versions" / "9.9.9"
    python = _write_slot(slot)
    (root / "current-version").write_text("9.9.9", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-bridge",
            "source": {"plugin": "agent-bridge", "version": "9.9.9"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    assert apr.resolve_installed_plugin_command("agent-bridge") == [
        str(python), "-m", "agent_bridge"
    ]


def test_legacy_resolution_skips_a_complete_slot_missing_its_interpreter(
    monkeypatch, tmp_path
):
    """current-version can name a slot that is marked complete but whose
    interpreter is missing/damaged (e.g. a partially-cleaned venv). The
    resolver must keep trying last-known-good / the newest remaining slot,
    not fail the whole lookup the moment the first candidate's marker exists."""
    root = tmp_path / ".agent-bridge"
    damaged_slot = root / "versions" / "9.9.9"
    damaged_slot.mkdir(parents=True)
    (damaged_slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    # No python executable under damaged_slot.
    good_slot = root / "versions" / "9.9.8"
    good_python = _write_slot(good_slot)
    (root / "current-version").write_text("9.9.9", encoding="utf-8")
    (root / "last-known-good").write_text("9.9.8", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-bridge",
            "source": {"plugin": "agent-bridge", "version": "9.9.9"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    assert apr.resolve_installed_plugin_command("agent-bridge") == [
        str(good_python), "-m", "agent_bridge"
    ]


def test_marketplace_cells_enabled_defaults_false_without_policy(tmp_path):
    assert apr.marketplace_cells_enabled(os_profile=tmp_path) is False


def test_marketplace_cells_enabled_reads_global_policy_bit(tmp_path):
    _write_policy(tmp_path, enabled=True)
    assert apr.marketplace_cells_enabled(os_profile=tmp_path) is True
    _write_policy(tmp_path, enabled=False)
    assert apr.marketplace_cells_enabled(os_profile=tmp_path) is False


def _namespaced_fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = (
        tmp_path / "durable" / "marketplaces" / "cell-a" / "plugins" / "agent-worktrees"
    )
    slot = root / "versions" / "1.2.3"
    python = _write_slot(slot)
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    (root / "install.json").write_text(
        json.dumps({"pluginId": "agent-worktrees"}), encoding="utf-8"
    )
    return root, python


def test_namespaced_root_used_when_policy_enabled_and_context_matches(
    monkeypatch, tmp_path
):
    root, python = _namespaced_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    _write_policy(home, enabled=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    # No legacy root exists at all -- proves namespaced resolution, not a
    # coincidental legacy hit.
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "no-legacy-here"))
    assert apr.resolve_installed_plugin_command("agent-worktrees") == [
        str(python), "-m", "agent_worktrees"
    ]


def test_namespaced_root_ignored_when_policy_disabled(monkeypatch, tmp_path):
    """The core 'same config' guarantee: an explicit context alone is never
    enough. Absent (or explicitly false) policy always falls back to legacy,
    exactly like a plugin's own bootstrap would for the same file."""
    root, _python = _namespaced_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "no-legacy-here"))
    assert apr.resolve_installed_plugin_command("agent-worktrees") is None


def test_namespaced_root_ignored_when_context_names_a_different_plugin(
    monkeypatch, tmp_path
):
    root, _python = _namespaced_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    _write_policy(home, enabled=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "no-legacy-here"))
    assert apr.resolve_installed_plugin_command("agent-bridge") is None


def test_namespaced_context_falls_back_to_legacy_when_both_present(
    monkeypatch, tmp_path
):
    """When policy is enabled and context matches, namespaced wins over an
    ALSO-present legacy install (namespaced is tried first)."""
    root, namespaced_python = _namespaced_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    _write_policy(home, enabled=True)
    legacy_root = tmp_path / "legacy" / ".agent-worktrees"
    legacy_slot = legacy_root / "versions" / "0.0.1"
    _write_slot(legacy_slot)
    (legacy_root / "current-version").write_text("0.0.1", encoding="utf-8")
    (legacy_root / "deploy-manifest.json").write_text(
        json.dumps({
            "service": "agent-worktrees",
            "source": {"plugin": "agent-worktrees", "version": "0.0.1"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "legacy"))
    assert apr.resolve_installed_plugin_command("agent-worktrees") == [
        str(namespaced_python), "-m", "agent_worktrees"
    ]


def test_canonical_os_profile_never_substitutes_home_on_posix(monkeypatch):
    """The vendored resolver deliberately selects the real passwd-database
    home on POSIX when ``os_profile`` is omitted, specifically to avoid
    trusting a possibly-unset or spoofed ``HOME``. Substituting ``HOME``
    ourselves would let Worktree Manager read a different policy file than
    an agent-* plugin's own bootstrap would for the same account -- so this
    must return ``None`` (never a HOME-derived path) on POSIX, regardless of
    what HOME is set to."""
    monkeypatch.setattr(apr.os, "name", "posix")
    assert apr._canonical_os_profile({"HOME": "/some/spoofed/home"}) is None
    assert apr._canonical_os_profile({}) is None


def test_canonical_os_profile_uses_userprofile_on_windows(monkeypatch):
    monkeypatch.setattr(apr.os, "name", "nt")
    assert apr._canonical_os_profile({"USERPROFILE": "C:\\Users\\someone"}) == Path(
        "C:\\Users\\someone"
    )
    assert apr._canonical_os_profile({}) is None
