"""Exercise the installer's real PowerShell-to-PowerShell verification boundary."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"


@pytest.mark.skipif(os.name != "nt", reason="Windows native argument marshalling")
@pytest.mark.parametrize("parent", ["powershell.exe", "pwsh.exe"])
def test_psmux_no_profile_verification_preserves_regex(tmp_path, parent):
    executable = shutil.which(parent)
    if not executable or not shutil.which("pwsh.exe"):
        pytest.skip("Both the requested parent shell and PowerShell 7 are required")

    # A native test command supplies help text; the verifier and both shell
    # processes are real. No installed package, profile, or persistent PATH changes.
    bin_dir = tmp_path / "bin with spaces"
    bin_dir.mkdir()
    (bin_dir / "psmux.cmd").write_text(
        "@echo off\r\necho %TEST_PSMUX_HELP%\r\n", encoding="ascii"
    )
    source = INSTALLER.read_text(encoding="utf-8")
    verifier = source.split("function Ensure-PsmuxSshSafe {", 1)[1].split(
        "function Resolve-AwPsmuxBin", 1
    )[0]
    verifier = verifier[verifier.index("    $env:AW_PSMUX_EXPECTED_VERSION"):]
    script = tmp_path / "verify.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "Set-StrictMode -Version Latest\n"
        "function Write-ServiceOk {}\n"
        "function Verify-Psmux {\n"
        "    $selected = [pscustomobject]@{ Version = '3.3.8' }\n"
        + verifier
        + r"""
foreach ($case in @(
    @{ Output = 'psmux 3.3.8'; Accepted = $true },
    @{ Output = 'psmux 3.3.7'; Accepted = $false },
    @{ Output = 'psmux 13.3.8'; Accepted = $false },
    @{ Output = 'psmux 3.3.8.1'; Accepted = $false }
)) {
    $env:TEST_PSMUX_HELP = $case.Output
    $accepted = $true
    try {
        Verify-Psmux
    } catch {
        if ($_.Exception.Message -notlike '*NoProfile/SSH-style verification*') {
            throw
        }
        $accepted = $false
    }
    if ($accepted -ne $case.Accepted) {
        throw "Unexpected verification result for '$($case.Output)': $accepted"
    }
    if (Test-Path Env:AW_PSMUX_EXPECTED_VERSION) {
        throw 'Verification leaked its environment variable'
    }
}
'verified'
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-File", str(script)],
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == "verified"
