"""Installed Manager commands retain their app root without ambient selectors."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest
import worktree_manager
from worktree_manager import source_config


@pytest.fixture
def application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home" / ".copilot-extensions" / "apps" / "worktree-manager"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    (slot / "application.json").write_text('{"name":"worktree-manager"}', encoding="utf-8")
    environment = slot / ".venv"
    environment.mkdir()
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setattr(worktree_manager, "__file__", str(environment / "worktree_manager" / "__init__.py"))
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return slot


def test_installed_application_reads_only_its_common_root_configuration(application: Path) -> None:
    root = application.parent.parent
    source_config.set_source(repo="https://example.invalid/incoming.git", root=root)
    legacy = root.parents[2] / ".worktree-manager"
    source_config.set_source(repo="https://example.invalid/legacy.git", root=legacy)
    assert source_config.default_root() == root
    assert source_config.config_path() == root / "config.toml"
    assert source_config.resolved_repo() == "https://example.invalid/incoming.git"
    assert source_config.resolved_repo(legacy) == "https://example.invalid/legacy.git"


def test_explicit_root_override_remains_authoritative(
    application: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "explicit"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(selected))
    assert source_config.default_root() == selected


def test_source_import_outside_installed_environment_does_not_infer_app_root(
    application: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worktree_manager, "__file__", str(tmp_path / "source" / "__init__.py"))
    assert source_config.default_root() == tmp_path / "home" / ".worktree-manager"


def test_legacy_version_slot_without_application_keeps_legacy_default(
    application: Path, tmp_path: Path,
) -> None:
    (application / "application.json").unlink()
    assert source_config.default_root() == tmp_path / "home" / ".worktree-manager"
