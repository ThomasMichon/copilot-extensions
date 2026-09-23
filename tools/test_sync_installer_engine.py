from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "sync-installer-engine.py"

_SPEC = importlib.util.spec_from_file_location("sync_installer_engine", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
sync_installer_engine = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync_installer_engine)


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    canonical_dir = tmp_path / "libs" / "installer-engine"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (canonical_dir / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    monkeypatch.setattr(sync_installer_engine, "REPO", tmp_path)
    monkeypatch.setattr(sync_installer_engine, "CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(sync_installer_engine, "ADOPTERS", ("agent-registered",))
    return tmp_path


def _mk_plugin_scripts(repo: Path, name: str) -> Path:
    scripts = repo / "plugins" / name / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    return scripts


def test_verify_passes_when_adopter_is_byte_identical(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    assert sync_installer_engine.verify() == []


def test_verify_flags_drifted_registered_adopter(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "installer-engine.ps1").write_text("drifted content\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("differs from" in p and "installer-engine.ps1" in p for p in problems)


def test_verify_flags_missing_registered_adopter(fake_repo):
    # agent-registered is declared in ADOPTERS but never vendored the files.
    assert any("is missing" in p for p in sync_installer_engine.verify())


def test_verify_flags_unregistered_adopter_with_a_stray_copy(fake_repo):
    # The registered adopter is in sync...
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    # ...but a second plugin has its own copy without being added to ADOPTERS.
    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    (stray_scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_ignores_plugin_with_no_installer_engine_file(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    _mk_plugin_scripts(fake_repo, "agent-unrelated")

    assert sync_installer_engine.verify() == []
