"""Functional tests: agent-bridge's `uninstall --dry-run`/`-DryRun` must never
touch the filesystem -- only report what WOULD be removed/preserved.

Exercises the real installer scripts (not a text-assertion proxy) against a
throwaway --install-dir/-InstallDir so a bug in the dry-run guard (e.g. a
destructive call left unguarded) is caught before it ships.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"


def _resolve_bash() -> str | None:
    """Resolve a REAL bash, not Windows' WSL-launcher `bash.exe` shim.

    On Windows, ``shutil.which("bash")`` can resolve to
    ``C:\\Windows\\system32\\bash.exe`` -- a WSL launcher stub that mangles
    Windows paths passed as arguments (strips backslashes/colons), which
    would silently corrupt ``--install-dir <tmp_path>`` here. Prefer the real
    Git Bash location when present.
    """
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    return shutil.which("bash")


def _clean_env() -> dict[str, str]:
    """Strip a WindowsApps python3 Store-alias stub that can shadow the real
    interpreter and silently no-op (known Windows PATH-shadowing gotcha)."""
    env = dict(os.environ)
    if "PATH" in env:
        parts = env["PATH"].split(os.pathsep)
        env["PATH"] = os.pathsep.join(p for p in parts if "WindowsApps" not in p)
    return env


def _seed_scoped_install(base: Path, version: str) -> None:
    (base / "versions" / version).mkdir(parents=True, exist_ok=True)
    (base / "versions" / version / "marker.txt").write_text("marker", encoding="utf-8")
    (base / "config.yaml").write_text("config", encoding="utf-8")


_BASH = _resolve_bash()


@pytest.mark.skipif(_BASH is None, reason="requires bash")
def test_install_sh_dry_run_preserves_everything(tmp_path: Path):
    install_dir = tmp_path / "scoped-install"
    _seed_scoped_install(install_dir, "0.0.0-test")

    proc = subprocess.run(
        [
            _BASH, str(_INSTALL_SH), "uninstall",
            "--install-dir", str(install_dir), "--dry-run", "--purge",
        ],
        capture_output=True, text=True, env=_clean_env(),
    )
    assert "dry run" in proc.stdout.lower()
    assert (install_dir / "versions" / "0.0.0-test" / "marker.txt").is_file(), (
        "dry-run must never delete the versioned runtime dir"
    )
    assert (install_dir / "config.yaml").is_file(), (
        "dry-run must never delete config, even with --purge"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="pwsh -File install.ps1 -- Windows only in CI")
@pytest.mark.skipif(shutil.which("pwsh") is None, reason="requires pwsh")
def test_install_ps1_dry_run_preserves_everything(tmp_path: Path):
    install_dir = tmp_path / "scoped-install"
    _seed_scoped_install(install_dir, "0.0.0-test")

    proc = subprocess.run(
        [
            "pwsh", "-File", str(_INSTALL_PS1), "uninstall",
            "-InstallDir", str(install_dir), "-DryRun", "-Purge",
        ],
        capture_output=True, text=True,
    )
    assert "dry run" in proc.stdout.lower()
    assert (install_dir / "versions" / "0.0.0-test" / "marker.txt").is_file(), (
        "dry-run must never delete the versioned runtime dir"
    )
    assert (install_dir / "config.yaml").is_file(), (
        "dry-run must never delete config, even with -Purge"
    )
