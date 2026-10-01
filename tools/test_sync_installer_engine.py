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


def _write_canonical_refs(scripts: Path) -> None:
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )


def _write_local_refs(scripts: Path) -> None:
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/installer-engine.sh"\n',
        encoding="utf-8",
    )


def test_verify_passes_when_adopter_is_byte_identical(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    assert sync_installer_engine.verify() == []


def test_verify_accepts_a_canonical_reference_with_no_local_copy(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(scripts)

    assert sync_installer_engine.verify() == []


def test_verify_flags_drifted_registered_adopter(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
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
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    # ...but a second plugin has its own copy without being added to ADOPTERS.
    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    (stray_scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_flags_unregistered_adopter_with_local_ref_and_no_copy(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    _write_local_refs(stray_scripts)

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_flags_unregistered_adopter_with_trailing_comment_ref_and_no_copy(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    (stray_scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/installer-engine.sh" # load helpers\n',
        encoding="utf-8",
    )
    (stray_scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1') # load helpers\n",
        encoding="utf-8",
    )

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_ignores_plugin_with_no_installer_engine_file(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    _mk_plugin_scripts(fake_repo, "agent-unrelated")

    assert sync_installer_engine.verify() == []


def test_verify_flags_an_escaping_noncanonical_reference(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../outside/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    outside = fake_repo / "outside"
    outside.mkdir()
    (outside / "installer-engine.sh").write_text("nope\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("which is not libs/installer-engine/installer-engine.sh" in p for p in problems)


def test_verify_rejects_malformed_local_reference_even_when_copies_exist(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'missing/installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/missing/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("neither" in p and "installer-engine.sh" in p for p in problems)
    assert any("neither" in p and "installer-engine.ps1" in p for p in problems)


def test_verify_rejects_normalized_local_reference_with_missing_intermediate(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/missing/../installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'missing\\..\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("neither" in p and "missing/../installer-engine.sh" in p for p in problems)
    assert any("neither" in p and "missing\\..\\installer-engine.ps1" in p for p in problems)


def test_verify_rejects_mismatched_powershell_quotes(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1\")\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    problems = sync_installer_engine.verify()

    assert any("does not source installer-engine" in p for p in problems)


def test_verify_rejects_mixed_local_and_canonical_forms(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("mixes installer-engine forms" in p for p in problems)


def test_verify_rejects_symlinked_canonical_engine(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(scripts)
    real = fake_repo / "outside"
    real.mkdir()
    (real / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (real / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    for name in ("installer-engine.ps1", "installer-engine.sh"):
        (fake_repo / "libs" / "installer-engine" / name).unlink()
        (fake_repo / "libs" / "installer-engine" / name).symlink_to(real / name)

    problems = sync_installer_engine.verify()

    assert any("is a symlink -- refusing" in p for p in problems)


def test_verify_rejects_symlinked_canonical_engine_in_local_mode(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    real = fake_repo / "outside"
    real.mkdir()
    (real / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (real / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    for name in ("installer-engine.ps1", "installer-engine.sh"):
        (fake_repo / "libs" / "installer-engine" / name).unlink()
        (fake_repo / "libs" / "installer-engine" / name).symlink_to(real / name)

    problems = sync_installer_engine.verify()

    assert any("is a symlink -- refusing" in p for p in problems)


def test_verify_flags_duplicate_engine_source_lines(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )

    problems = sync_installer_engine.verify()

    assert any("contains 2 installer-engine source lines" in p for p in problems)


def test_sync_repairs_missing_local_copy_when_wrappers_are_local(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)

    written = sync_installer_engine.sync()

    assert "plugins/agent-registered/scripts/installer-engine.ps1" in written
    assert "plugins/agent-registered/scripts/installer-engine.sh" in written
    assert (scripts / "installer-engine.ps1").read_text(encoding="utf-8") == "canonical ps1\n"
    assert (scripts / "installer-engine.sh").read_text(encoding="utf-8") == "canonical sh\n"


def test_sync_removes_stale_local_copy_when_wrappers_are_canonical(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("stale\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("stale\n", encoding="utf-8")

    written = sync_installer_engine.sync()

    assert "removed plugins/agent-registered/scripts/installer-engine.ps1" in written
    assert "removed plugins/agent-registered/scripts/installer-engine.sh" in written
    assert not (scripts / "installer-engine.ps1").exists()
    assert not (scripts / "installer-engine.sh").exists()


def test_sync_refuses_symlinked_canonical_engine_in_local_mode(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    real = fake_repo / "outside"
    real.mkdir()
    (real / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (real / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    for name in ("installer-engine.ps1", "installer-engine.sh"):
        (fake_repo / "libs" / "installer-engine" / name).unlink()
        (fake_repo / "libs" / "installer-engine" / name).symlink_to(real / name)

    with pytest.raises(RuntimeError, match="symlink -- refusing"):
        sync_installer_engine.sync()
