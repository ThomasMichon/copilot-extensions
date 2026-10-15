"""Cold native-type bootstrap stays bounded and safe to re-enter."""

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "installation-context.ps1"
HOSTS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell")))))
pytestmark = pytest.mark.installation_context_smoke


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("preloaded", [False, True], ids=["cold", "partly-loaded"])
def test_native_types_compile_once_and_preserve_existing_types(tmp_path, host, preloaded):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    source = SCRIPT.read_text(encoding="utf-8")
    prefix = source[source.index("$ErrorActionPreference = 'Stop'"):source.index("function Fail(")]
    preload = ""
    if preloaded:
        atomic = re.search(
            r"(using System\.IO;\n\npublic static class CeAtomicFile \{.*?\n\})",
            prefix, re.S,
        )
        assert atomic is not None
        preload = "Microsoft.PowerShell.Utility\\Add-Type -TypeDefinition @'\n" + atomic[1] + "\n'@\n"
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        + preload
        + """
$script:CompileCount = 0
function Add-Type {
    param([string]$TypeDefinition)
    $script:CompileCount++
    Microsoft.PowerShell.Utility\\Add-Type -TypeDefinition $TypeDefinition
}
"""
        + prefix
        + """
if ($script:CompileCount -ne 1) { throw 'Cold bootstrap must compile exactly once' }
$required = @('CeSafeFile', 'CeStrictJson', 'CeAtomicFile', 'CeAtomicDirectory')
if ($env:OS -eq 'Windows_NT') { $required += @('CeFinalPath', 'CeDirectoryState') }
else { $required += @('CePosixPath', 'CePosixAccount') }
foreach ($name in $required) {
    if (-not ($name -as [type])) { throw "Native type was not loaded: $name" }
}
$original = 'CeAtomicFile' -as [type]
"""
        + prefix
        + """
if ($script:CompileCount -ne 1) { throw 'Reentry unexpectedly compiled native types' }
if (('CeAtomicFile' -as [type]) -ne $original) { throw 'An existing type was replaced' }
Write-Output 'compiled-once'
"""
    )
    harness = tmp_path / "bootstrap.ps1"
    harness.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "compiled-once"
