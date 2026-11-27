"""The installer prepares the declared command bootstrap Python before binding.

Regression: a fleet update failed in ``Deploy-SelfProvisioningBinstub`` with
``managed-python-not-prepared`` because the installation-context status helper
inspected (never prepared) the declared uv-managed CPython.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

INIT = Path(__file__).resolve().parents[1] / "scripts" / "init.ps1"
PWSH = shutil.which("pwsh") or ""

pytestmark = pytest.mark.skipif(not PWSH, reason="pwsh is required")

_DRIVER = r"""
param([string]$Init, [string]$WheelProfile, [string]$WheelPython, [string]$Action = 'install')
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Init, [ref]$tokens, [ref]$errors)
$fn = $ast.Find({ param($n)
    $n -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $n.Name -eq 'Get-InstallerBootstrapPython' }, $true)
# Define it from a file in this scripts directory so $PSScriptRoot matches.
$definition = Join-Path $PSScriptRoot 'installer-function.ps1'
Set-Content -LiteralPath $definition -Value $fn.Extent.Text
. $definition
$result = Get-InstallerBootstrapPython
@{ python = $result } | ConvertTo-Json -Compress
"""

_SELECTOR = r"""
param([string]$Operation = 'Inspect', [int]$TimeoutMilliseconds = 30000)
Set-Content -LiteralPath (Join-Path $PSScriptRoot 'calls.txt') -Value "$Operation $TimeoutMilliseconds"
'uv progress noise is not selected'
(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'python.txt') -Raw).Trim()
"""


@pytest.fixture
def scripts(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "scripts"
    selector_dir = root / "_bootstrap_python"
    selector_dir.mkdir(parents=True)
    (root / "bootstrap-python-launcher.txt").write_text("uv\n", encoding="utf-8")
    (selector_dir / "select.ps1").write_text(_SELECTOR, encoding="utf-8")
    python = tmp_path / "managed" / "python.exe"
    python.parent.mkdir()
    python.write_text("", encoding="utf-8")
    (selector_dir / "python.txt").write_text(str(python), encoding="utf-8")
    driver = root / "driver.ps1"
    driver.write_text(_DRIVER, encoding="utf-8")
    return root, python


def _run(
    root: Path, *extra: str, env_python: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    if env_python:
        env["COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"] = env_python
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(root / "driver.ps1"),
         "-Init", str(INIT), *extra],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    return proc


def _calls(root: Path) -> list[str] | None:
    calls = root / "_bootstrap_python" / "calls.txt"
    return calls.read_text(encoding="utf-8").split() if calls.exists() else None


def test_installer_prepares_declared_bootstrap_python(scripts: tuple[Path, Path]) -> None:
    root, python = scripts
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["python"] == str(python)
    assert _calls(root) == ["Prepare", "600000"]


def test_stamp_does_not_acquire_command_bootstrap_python(scripts: tuple[Path, Path]) -> None:
    root, python = scripts
    proc = _run(root, "-Action", "stamp")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["python"] == str(python)
    assert _calls(root) == ["Inspect", "600000"]


def test_wheel_profile_uses_wheel_python_without_preparing(
    scripts: tuple[Path, Path], tmp_path: Path,
) -> None:
    root, _ = scripts
    wheel_python = str(tmp_path / "wheel" / "python.exe")
    proc = _run(root, "-WheelProfile", "provider-exec", "-WheelPython", wheel_python)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["python"] == wheel_python
    assert _calls(root) is None


def test_explicit_bootstrap_python_is_preserved(
    scripts: tuple[Path, Path], tmp_path: Path,
) -> None:
    root, _ = scripts
    proc = _run(root, env_python=str(tmp_path / "explicit.exe"))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["python"] is None
    assert _calls(root) is None


def test_source_checkout_without_launcher_declaration_selects_nothing(
    scripts: tuple[Path, Path],
) -> None:
    root, _ = scripts
    (root / "bootstrap-python-launcher.txt").unlink()
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["python"] is None
    assert _calls(root) is None


def test_binding_status_child_receives_prepared_bootstrap_python() -> None:
    text = INIT.read_text(encoding="utf-8")
    start = text.index("function Deploy-SelfProvisioningBinstub")
    body = text[start:text.index("Cannot bind the global command to its installation context", start)]
    assert "Get-InstallerBootstrapPython" in body
    assert "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON = $bootstrapPython" in body
    assert "-Environment $bindingEnvironment" in body
