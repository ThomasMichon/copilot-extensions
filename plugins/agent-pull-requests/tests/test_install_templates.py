from __future__ import annotations

import shutil
from pathlib import Path

import pytest


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


def _assert_installer_engine_wiring(plugin: Path) -> None:
    import installer_engine_ref as ier

    scripts = plugin / "scripts"
    materialized = any(
        (scripts / name).exists() or (scripts / name).is_symlink()
        for name in ier.FILES
    )
    for ext in ("ps1", "sh"):
        ref = ier.find_engine_ref(scripts / f"install.{ext}", ext)
        assert ref is not None, f"expected one engine reference in install.{ext}"
        if materialized:
            assert ier.is_local_ref(ref, plugin), f"expected local reference in install.{ext}"
        else:
            assert ier.is_canonical_ref(ref, plugin, repo_root=REPO), (
                f"expected canonical reference in install.{ext}"
            )
            assert not ref.local_path.exists(), ref.file_name
    if materialized:
        for name in (*ier.FILES, *ier.support_files(CANONICAL_ENGINE)):
            asset = scripts / name
            assert asset.is_file() and not asset.is_symlink(), f"missing local asset: {name}"
            assert asset.read_bytes() == (CANONICAL_ENGINE / name).read_bytes(), (
                f"changed local asset: {name}"
            )


def test_installers_source_the_engine_for_the_current_layout(monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    _assert_installer_engine_wiring(PLUGIN)


@pytest.fixture
def materialized_payload(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    import materialize_installer_engine

    payload = tmp_path / "release" / "plugins" / PLUGIN.name
    originals = {
        path: path.read_bytes()
        for path in (
            PLUGIN / "scripts" / "install.ps1",
            PLUGIN / "scripts" / "install.sh",
            *CANONICAL_ENGINE.glob("installer*"),
        )
        if path.is_file()
    }
    shutil.copytree(PLUGIN / "scripts", payload / "scripts")
    log = materialize_installer_engine.materialize_ref_into(
        source_consumer_dir=PLUGIN,
        dest_consumer_dir=payload,
        canonical_root=REPO,
        dest_root=tmp_path / "release",
    )
    assert not any(line.startswith("SKIP") for line in log), "\n".join(log)
    assert {path: path.read_bytes() for path in originals} == originals
    return payload


def test_installer_engine_wiring_accepts_real_materialized_payload(materialized_payload):
    _assert_installer_engine_wiring(materialized_payload)


@pytest.mark.parametrize("name", (
    "installer-engine.ps1",
    "installer-engine.sh",
    "installer_dependency_policy.py",
    "installer_dependency_config.py",
))
@pytest.mark.parametrize("damage", ("missing", "modified"))
def test_installer_engine_wiring_refuses_damaged_assets(materialized_payload, name, damage):
    asset = materialized_payload / "scripts" / name
    if damage == "missing":
        asset.unlink()
    else:
        asset.write_bytes(asset.read_bytes() + b"\n# changed fixture\n")
    with pytest.raises(AssertionError, match=name.replace(".", r"\.")):
        _assert_installer_engine_wiring(materialized_payload)


@pytest.mark.parametrize("ext", ("ps1", "sh"))
def test_installer_engine_wiring_refuses_mixed_layouts(materialized_payload, ext):
    import installer_engine_ref as ier

    script = materialized_payload / "scripts" / f"install.{ext}"
    text = script.read_text(encoding="utf-8")
    assert ier.local_line(ext) in text
    script.write_text(
        text.replace(ier.local_line(ext), ier.canonical_line(ext)),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="expected local reference"):
        _assert_installer_engine_wiring(materialized_payload)
