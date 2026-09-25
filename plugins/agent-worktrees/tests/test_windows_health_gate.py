"""Windows regression test for Invoke-VersionedActivate's health gate.

Mirrors the POSIX end-to-end coverage in test_first_install_bootstrap.py's
``test_posix_health_gate_rejects_namespace_package_slot``, but exercises the
real PowerShell probe directly (see rationale below for why this is a
targeted extraction rather than a full ``install.ps1 provision`` run).

Why not spawn ``install.ps1 provision`` with a faked ``uv``/``python`` like
the POSIX test does: on POSIX, ``uv``/``python`` can be trivial ``#!/bin/sh``
shebang scripts that ``exec`` straight through -- cheap to fake. On Windows,
``Get-ApplicationPath`` (``Get-Command -CommandType Application``) and the
subsequent ``&`` call both require a genuine PE executable at
``Scripts\\python.exe`` -- CreateProcess refuses to launch a renamed batch/
text file as ``.exe``. Faking a working Windows ``python.exe`` would need a
compiled stub, which is disproportionate to this regression's scope.

Instead, this test extracts the EXACT PowerShell source of the health-gate
block (the isolated probe, PYTHONPATH clear, and stale-marker invalidation)
straight out of install.ps1 by anchor string (same technique
test_guard_deploy_contract.py already uses for other hunks), so it stays
honestly coupled to the real shipped code rather than a hand-duplicated
copy that could silently drift. It wraps that snippet in a small harness
function and runs it under a REAL, isolated pwsh subprocess against:

- a genuinely broken slot (a bare ``python -m venv`` venv with no
  ``agent_worktrees`` installed at all) -- the gate must reject it, and
- a marker pre-written by ``versioned_runtime.py``'s own ``mark_complete``
  (simulating an older, less-strict installer run) -- the gate must still
  reject the broken payload AND remove that stale marker.

This never touches the real ``~/.agent-worktrees`` and never dot-sources
the full install.ps1 (which would run its own top-level ``switch``/prereq
checks as a side effect of loading).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import venv
from pathlib import Path

import pytest

# Windows-only: exercises install.ps1's PowerShell health gate via a real
# `venv`-created interpreter at the Windows `Scripts\python.exe` layout.
# Guard the WHOLE module (not just individual tests) -- POSIX's `venv.create`
# produces `bin/python` instead, so the `bare_venv_python` fixture itself
# would fail its own assertion before any test got a chance to skip.
pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Windows-only: exercises install.ps1's PowerShell health gate",
)

PLUGIN = Path(__file__).resolve().parents[1]
INSTALL_PS1 = PLUGIN / "scripts" / "install.ps1"

_VR_PATH = PLUGIN / "scripts" / "versioned_runtime.py"
_vr_spec = importlib.util.spec_from_file_location("versioned_runtime", _VR_PATH)
assert _vr_spec and _vr_spec.loader, f"cannot load {_VR_PATH}"
vr = importlib.util.module_from_spec(_vr_spec)
_vr_spec.loader.exec_module(vr)

# Stable anchors bracketing just the health-gate block inside
# Invoke-VersionedActivate: the isolated probe (PYTHONPATH clear + `-I`),
# its failure branch (error log + stale-marker invalidation), ending right
# before the success path hands off to Invoke-VersionedMarkComplete/activate
# (which need a full versioned_runtime.py + venv toolchain this test doesn't
# set up). If either anchor goes missing, the installer's shape changed
# enough that this extraction needs re-pointing -- fail loudly rather than
# silently testing nothing.
_START_ANCHOR = (
    "$prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'\n"
    "    $prevHealthPP = $env:PYTHONPATH"
)
_END_ANCHOR = "\n    Invoke-VersionedMarkComplete"


def _extract_health_gate_snippet() -> str:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    start = text.index(_START_ANCHOR)
    end = text.index(_END_ANCHOR, start)
    return text[start:end]


def _pwsh() -> str:
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        pytest.skip("no PowerShell interpreter (pwsh/powershell) on PATH")
    return exe


def _run_health_gate(tmp_path: Path, *, venv_python: Path, pre_mark_complete: bool):
    """Run the extracted health-gate snippet in an isolated pwsh subprocess.

    Returns (gate_passed: bool, stdout: str, marker_path: Path,
    marker_existed_before: bool) -- the last element is captured BEFORE the
    subprocess runs, since the gate under test may itself delete the marker.
    """
    src_version = "0.0.0-test"
    venv_dir = tmp_path / "versions" / src_version
    # Copy the WHOLE bare venv (not just python.exe): the interpreter needs
    # its sibling pyvenv.cfg to resolve its own base prefix at startup, so a
    # lone copied .exe fails before it even gets to the "no agent_worktrees"
    # error this test wants to reproduce.
    shutil.copytree(venv_python.parent.parent, venv_dir)

    if pre_mark_complete:
        vr.mark_complete(tmp_path, src_version)
    marker = vr.marker_path(tmp_path, src_version)
    marker_existed_before = marker.exists()

    harness = tmp_path / "harness.ps1"
    harness.write_text(
        "param([string]$VenvPython, [string]$SrcVersion, [string]$VenvDir)\n"
        "function Write-ServiceErr($m) { Write-Host \"ERR: $m\" }\n"
        "function Write-ServiceChanged($m) { Write-Host \"CHG: $m\" }\n"
        "function Test-HealthGateSnippet {\n"
        + _extract_health_gate_snippet()
        + "\n    return $true\n"
        "}\n"
        "if (Test-HealthGateSnippet) { exit 0 } else { exit 1 }\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            _pwsh(),
            "-NoProfile",
            "-NoLogo",
            "-File",
            str(harness),
            "-VenvPython",
            str(venv_dir / "Scripts" / "python.exe"),
            "-SrcVersion",
            src_version,
            "-VenvDir",
            str(venv_dir),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    gate_passed = proc.returncode == 0
    return gate_passed, proc.stdout + proc.stderr, marker, marker_existed_before


@pytest.fixture(scope="module")
def bare_venv_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real, isolated venv with NO agent_worktrees installed -- simulates a
    genuinely broken slot without needing a fake/compiled python.exe.
    """
    target = tmp_path_factory.mktemp("bare-venv")
    venv.create(target, with_pip=False)
    py = target / "Scripts" / "python.exe"
    assert py.exists(), f"venv creation did not produce {py}"
    return py


def test_windows_health_gate_rejects_a_broken_slot(
    tmp_path: Path, bare_venv_python: Path
) -> None:
    gate_passed, output, _marker, _existed = _run_health_gate(
        tmp_path, venv_python=bare_venv_python, pre_mark_complete=False
    )

    assert not gate_passed, output
    assert "ERR: Fresh runtime slot failed its health gate" in output, output


def test_windows_health_gate_invalidates_a_stale_marker_on_a_broken_slot(
    tmp_path: Path, bare_venv_python: Path
) -> None:
    """The exact real-world scenario (dotfiles #7561): an OLDER, less-strict
    installer already wrote a valid completion marker for this slot, but the
    payload is still broken. The (now-strict) gate must still reject it AND
    remove the stale marker so a future run stops trusting it.
    """
    gate_passed, output, marker, existed_before = _run_health_gate(
        tmp_path, venv_python=bare_venv_python, pre_mark_complete=True
    )
    assert existed_before, "test setup should have pre-written the marker"

    assert not gate_passed, output
    assert "ERR: Fresh runtime slot failed its health gate" in output, output
    assert "CHG: Invalidated stale completion marker" in output, output
    assert not marker.exists(), "the stale marker must be removed on gate failure"
