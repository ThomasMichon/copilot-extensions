from __future__ import annotations

from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
CANONICAL_ENGINE = REPO / "libs" / "installer-engine"


def test_installed_binstub_templates_forward_install_dir_and_use_resilient_hosts() -> None:
    installer_ps1 = (CANONICAL_ENGINE / "installer-engine.ps1").read_text(
        encoding="utf-8"
    )
    installer_sh = (CANONICAL_ENGINE / "installer-engine.sh").read_text(
        encoding="utf-8"
    )

    assert "provision -InstallDir `$_root" in installer_ps1
    assert r'%SystemRoot%\System32\where.exe' in installer_ps1
    assert (
        r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
        in installer_ps1
    )
    assert 'set "_PSHOST="' in installer_ps1

    assert 'provision --install-dir "\\$_root"' in installer_sh
    assert '\\$_root/.provision.lock.pid' in installer_sh
    assert "exec 9>&-" in installer_sh


def test_posix_engine_bootstrap_pins_uv_release_assets() -> None:
    installer_sh = (CANONICAL_ENGINE / "installer-engine.sh").read_text(
        encoding="utf-8"
    )

    assert "bootstrap_version" in installer_sh
    assert "github.com/astral-sh/uv/releases/download" in installer_sh
    assert ".tar.gz" in installer_sh
    assert "expected_sha" in installer_sh
    assert "astral.sh/uv/install.sh" not in installer_sh


def test_installers_source_the_canonical_engine_on_dev() -> None:
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    install_sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert "..\\..\\..\\libs\\installer-engine\\installer-engine.ps1" in install_ps1
    assert "../../../libs/installer-engine/installer-engine.sh" in install_sh
    assert not (PLUGIN / "scripts" / "installer-engine.ps1").exists()
    assert not (PLUGIN / "scripts" / "installer-engine.sh").exists()
