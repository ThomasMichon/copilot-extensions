"""Tests for Copilot permission / folder-trust seeding (permissions.py).

Focus: folder trust must be written to the camelCase ``trustedFolders`` key
that the Copilot CLI actually reads. An earlier snake_case ``trusted_folders``
was silently ignored, so worktrees were never pre-trusted (leaving the startup
folder-trust reload firing, which feeds the extension-reload hang) and were
never cleaned up on finalize.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import permissions


@pytest.fixture
def copilot_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point permissions.py at an isolated ~/.copilot dir."""
    cop = tmp_path / ".copilot"
    cop.mkdir()
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: cop)
    return cop


def _write_config(cop: Path, data: dict) -> Path:
    p = cop / "config.json"
    p.write_text(json.dumps(data, indent=2))
    return p


# ── folder trust: correct key ────────────────────────────────────────────

def test_add_trusted_folder_uses_camelcase_key(copilot_home: Path) -> None:
    cfg = _write_config(copilot_home, {"trustedFolders": []})

    assert permissions.add_trusted_folder(r"D:\wt\a") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == [r"D:\wt\a"]
    # The dead snake_case key must never be created.
    assert "trusted_folders" not in data


def test_add_trusted_folder_initializes_missing_key(copilot_home: Path) -> None:
    # config.json exists but has no trustedFolders yet (fresh CLI install).
    cfg = _write_config(copilot_home, {"firstLaunchAt": "2026-01-01"})

    assert permissions.add_trusted_folder(r"D:\wt\a") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == [r"D:\wt\a"]
    assert data["firstLaunchAt"] == "2026-01-01"  # unrelated keys preserved


def test_add_trusted_folder_idempotent(copilot_home: Path) -> None:
    _write_config(copilot_home, {"trustedFolders": [r"D:\wt\a"]})

    assert permissions.add_trusted_folder(r"D:\wt\a") is False


def test_add_trusted_folder_preserves_existing_entries(copilot_home: Path) -> None:
    cfg = _write_config(copilot_home, {"trustedFolders": [r"D:\wt\existing"]})

    assert permissions.add_trusted_folder(r"D:\wt\new") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == [r"D:\wt\existing", r"D:\wt\new"]


def test_add_trusted_folder_no_config(copilot_home: Path) -> None:
    # No config.json at all -> no-op (CLI creates it on first run).
    assert permissions.add_trusted_folder(r"D:\wt\a") is False


# ── folder trust: removal (finalize cleanup) ─────────────────────────────

def test_remove_trusted_folder_uses_camelcase_key(copilot_home: Path) -> None:
    cfg = _write_config(
        copilot_home, {"trustedFolders": [r"D:\wt\a", r"D:\wt\b"]}
    )

    assert permissions.remove_trusted_folder(r"D:\wt\a") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == [r"D:\wt\b"]


def test_remove_trusted_folder_absent(copilot_home: Path) -> None:
    _write_config(copilot_home, {"trustedFolders": [r"D:\wt\b"]})

    assert permissions.remove_trusted_folder(r"D:\wt\a") is False


def test_add_then_remove_roundtrip(copilot_home: Path) -> None:
    cfg = _write_config(copilot_home, {"trustedFolders": []})

    assert permissions.add_trusted_folder(r"D:\wt\a") is True
    assert permissions.remove_trusted_folder(r"D:\wt\a") is True

    data = json.loads(cfg.read_text())
    assert data["trustedFolders"] == []
    assert "trusted_folders" not in data
