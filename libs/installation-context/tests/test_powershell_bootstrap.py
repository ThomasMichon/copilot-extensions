"""Cold native-type bootstrap stays bounded and safe to re-enter."""

import json
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


@pytest.mark.parametrize("host", HOSTS or [None])
def test_canonical_existence_native_hits_and_provider_failures(tmp_path, host):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "existence.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Canonical source failed to parse' }
$function = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'Test-CanonicalPathExists'
}, $true)
if (-not $function) { throw 'Canonical existence helper is missing' }
. ([scriptblock]::Create($function.Extent.Text))
$script:ProviderCalls = 0
$script:ProviderFailure = $false
function Test-Path {
    param([string]$LiteralPath)
    $script:ProviderCalls++
    if ($script:ProviderFailure) { throw 'provider-denied' }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath
}
$file = [IO.Path]::Combine($Root, 'literal[1].txt')
$directory = [IO.Path]::Combine($Root, 'directory[1]')
[IO.File]::WriteAllText($file, 'data')
[void][IO.Directory]::CreateDirectory($directory)
foreach ($path in @($file, $directory, [IO.Path]::GetPathRoot($Root))) {
    if (-not (Test-CanonicalPathExists $path)) { throw 'Native existing path was rejected' }
}
if ($script:ProviderCalls -ne 0) { throw 'A native positive probe invoked the provider' }
[IO.File]::Delete($file)
[IO.Directory]::Delete($directory)
foreach ($path in @($file, $directory)) {
    if (Test-CanonicalPathExists $path) { throw 'A deleted path was cached as existing' }
}
if ($script:ProviderCalls -ne 2) { throw 'Negative native probes did not retain provider checks' }
$script:ProviderFailure = $true
$failed = $false
try { $null = Test-CanonicalPathExists $file }
catch {
    if ($_.Exception.Message -cne 'provider-denied') { throw }
    $failed = $true
}
if (-not $failed) { throw 'Provider failure was swallowed' }
Write-Output 'native-hits-fresh-negative-errors'
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "native-hits-fresh-negative-errors"


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize(
    ("action", "message"),
    [("resolve", "valid only for status"), ("status", "requires -ExpectedPluginId and -ExpectedPayloadRoot")],
)
def test_composed_status_requires_explicit_expectations_and_action(host, action, message):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT),
         action, "-WithContextValidation"],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 1
    assert not result.stdout.strip()
    assert message in result.stderr


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("action", ["source-id", "status"])
def test_false_composed_status_switch_preserves_unselected_action(tmp_path, host, action):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "false-switch.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root, [string]$Action)
$ErrorActionPreference = 'Stop'
$parameters = @{
    Action = $Action
    SourceJson = '{"source":"github","repo":"example/marketplace"}'
    MarketplaceKey = 'example'
    PluginId = 'agent-example'
    PayloadRoot = $Root
    CopilotHome = Join-Path $Root 'copilot'
    DurableHome = Join-Path $Root 'durable'
    LegacyRoot = Join-Path $Root 'legacy'
}
$baseline = & $Source @parameters | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Unselected action failed' }
$parameters.WithContextValidation = $false
$disabled = & $Source @parameters | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Explicit false switch changed the action' }
@{ baseline = $baseline; disabled = $disabled } | ConvertTo-Json -Depth 100 -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path), "-Action", action],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["disabled"] == value["baseline"]
    assert "contextValidation" not in value["disabled"]
