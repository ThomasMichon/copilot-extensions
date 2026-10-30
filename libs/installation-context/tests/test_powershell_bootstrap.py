"""The PowerShell SDK delegates without compiling or acquiring an interpreter."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "installation-context.ps1"
HOSTS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell")))))
pytestmark = pytest.mark.installation_context_smoke


@pytest.mark.parametrize("host", HOSTS or [None])
def test_sdk_delegates_without_compilation_and_can_reenter(tmp_path, host):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("Add-Type", "Reflection.Emit", "DllImport", "public static class"):
        assert forbidden not in source
    script = r"""
param([string]$Source, [string]$Root)
$ErrorActionPreference = 'Stop'
function Add-Type {
    throw 'The SDK attempted native compilation'
}
$parameters = @{
    Action = 'source-id'
    SourceJson = '{"source":"github","repo":"example/marketplace"}'
    CopilotHome = Join-Path $Root 'copilot'
    DurableHome = Join-Path $Root 'durable'
}
1..2 | ForEach-Object {
    $result = & $Source @parameters | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'The SDK failed without a compiler' }
    if ($result.canonical -cne 'github:example/marketplace') { throw 'Source identity changed' }
}
Write-Output 'compiler-free-reentry'
"""
    harness = tmp_path / "bootstrap.ps1"
    harness.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "compiler-free-reentry"


@pytest.mark.parametrize("host", HOSTS or [None])
def test_sdk_expands_raw_percent_paths_once_not_each_canonicalization(tmp_path, host):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "literal-percent.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root)
$ErrorActionPreference = 'Stop'
$env:COPILOT_EXTENSIONS_CONTEXT = $null
$env:COPILOT_PLUGIN_ROOT = $null
$env:SDK_OUTER = '%SDK_INNER%'
$env:SDK_INNER = 'wrong-directory'
$literal = Join-Path $Root '%SDK_INNER%'
$raw = Join-Path $Root '%SDK_OUTER%'
[void][IO.Directory]::CreateDirectory($literal)
$parameters = @{
    Action = 'resolve'
    SourceJson = (@{ source = 'directory'; path = $raw } | ConvertTo-Json -Compress)
    PluginId = 'agent-example'
    PayloadRoot = $raw
    ProjectRoot = $raw
    CopilotHome = Join-Path $raw 'copilot'
    DurableHome = Join-Path $raw 'durable'
    ExpectedPayloadRoot = $raw
}
$result = & $Source @parameters | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Literal environment tokens were expanded again' }
@{ result = $result; literal = $literal } | ConvertTo-Json -Depth 20 -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["result"]["payloadRoot"] == value["literal"]
    assert value["result"]["source"]["canonical"] == "directory:" + value["literal"]
    assert Path(value["result"]["cellRoot"]).is_relative_to(Path(value["literal"]) / "durable")
    assert not (Path(value["literal"]) / "durable").exists()
    assert not (tmp_path / "wrong-directory").exists()


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("action,pointer", [
    ("resolve", "explicit"), ("resolve", "inherited"), ("resolve", "absent"),
    ("validate", "explicit"), ("validate", "inherited"),
])
def test_sdk_context_accepts_once_expanded_inherited_plugin_root(tmp_path, host, action, pointer):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "inherited-root.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root, [string]$Action, [string]$Pointer)
$ErrorActionPreference = 'Stop'
$env:COPILOT_EXTENSIONS_CONTEXT = $null
$env:COPILOT_PLUGIN_ROOT = $null
$env:SDK_OUTER = '%SDK_INNER%'
$env:SDK_INNER = 'wrong-directory'
$raw = Join-Path $Root '%SDK_OUTER%'
$literal = Join-Path $Root '%SDK_INNER%'
[void][IO.Directory]::CreateDirectory($literal)
$common = @{
    PayloadRoot = $raw
    PluginId = 'agent-example'
    SourceJson = '{"source":"github","repo":"example/marketplace"}'
    CopilotHome = Join-Path $Root 'copilot'
    DurableHome = Join-Path $Root 'durable'
}
$stamp = & $Source -Action stamp @common -PayloadVersion '1.0.0' -PayloadOrigin explicit `
    -ExpectedNamespaceGeneration 0 -ExpectedInstallGeneration 0 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Fixture receipt could not be stamped' }
$env:COPILOT_PLUGIN_ROOT = $raw
$parameters = @{
    Action = $Action
    DurableHome = $common.DurableHome
    ExpectedPluginId = 'agent-example'
    ExpectedPayloadRoot = $raw
}
if ($Pointer -ceq 'explicit') { $parameters.Context = $stamp.installReceipt }
elseif ($Pointer -ceq 'inherited') { $env:COPILOT_EXTENSIONS_CONTEXT = $stamp.installReceipt }
else {
    foreach ($name in $common.Keys) { $parameters[$name] = $common[$name] }
}
$result = & $Source @parameters | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Matching inherited plugin root was rejected' }
@{ result = $result; literal = $literal } | ConvertTo-Json -Depth 20 -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path), "-Action", action, "-Pointer", pointer],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["result"]["payloadRoot"] == value["literal"]
    assert not (tmp_path / "wrong-directory").exists()


@pytest.mark.parametrize("host", HOSTS or [None])
def test_sdk_physical_paths_and_missing_suffix_are_not_cached(tmp_path, host):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "existence.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root)
$ErrorActionPreference = 'Stop'
$payload = [IO.Path]::Combine($Root, 'directory[1]')
$durable = [IO.Path]::Combine($Root, 'missing[1]', 'durable')
[void][IO.Directory]::CreateDirectory($payload)
$parameters = @{
    Action = 'resolve'
    SourceJson = '{"source":"github","repo":"example/marketplace"}'
    PluginId = 'agent-example'
    PayloadRoot = $payload
    CopilotHome = Join-Path $Root 'copilot'
    DurableHome = $durable
}
$first = & $Source @parameters | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'An existing literal payload was rejected' }
if ($first.payloadRoot -cne $payload) { throw 'The physical payload changed' }
if (-not $first.cellRoot.StartsWith($durable)) { throw 'The missing suffix changed' }
if (Test-Path -LiteralPath $durable) { throw 'Read-only resolution created a durable root' }
[IO.Directory]::Delete($payload)
$second = & $Source @parameters
if ($LASTEXITCODE -ne 1 -or $second) { throw 'A deleted payload was cached as existing' }
Write-Output 'fresh-path-observation'
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
    assert result.stdout.strip() == "fresh-path-observation"


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


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("condition", ["absent", "lookup-error"])
def test_missing_python_and_selection_errors_are_explicit_without_acquisition(tmp_path, host, condition):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "prerequisite.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root, [string]$Condition)
$ErrorActionPreference = 'Stop'
function Add-Type { throw 'Unexpected compiler invocation' }
$env:PATH = $Root
if ($Condition -ceq 'lookup-error') {
    function Get-Command { throw 'controlled-lookup-error' }
}
& $Source source-id -SourceJson '{"source":"opaque","id":"example"}' `
    -CopilotHome (Join-Path $Root 'copilot') -DurableHome (Join-Path $Root 'durable')
exit $LASTEXITCODE
""",
        encoding="utf-8",
    )
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path), "-Condition", condition],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 1
    assert not result.stdout.strip()
    message = "Python 3.10+ must be available as python on PATH" if condition == "absent" else "controlled-lookup-error"
    assert message in result.stderr
    assert "Unexpected compiler" not in result.stderr
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("host", HOSTS or [None])
def test_sdk_encoded_packet_preserves_literal_unicode_and_case_insensitive_binding(tmp_path, host):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "literal.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root)
$value = "space 'quoted' `"double`" \slash &|<>()`$?; " + [char]0x3bb + "`nsecond"
$parameters = @{
    action = 'source-id'
    sourcejson = (@{ source = 'opaque'; id = $value } | ConvertTo-Json -Compress)
    copilothome = Join-Path $Root 'copilot'
    durablehome = Join-Path $Root 'durable'
}
$result = & $Source @parameters | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Literal SDK invocation failed' }
$result | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "opaque:space 'quoted' \"double\" \\slash &|<>()$?; \u03bb\nsecond"
    value = json.loads(result.stdout)
    assert value["canonical"] == expected
    assert value["record"] == (
        "version:1:1\nkind:6:opaque\n"
        f"source:{len(expected.encode('utf-8'))}:{expected}\nref:0:\n"
    )


@pytest.mark.parametrize("host", HOSTS or [None])
def test_sdk_accepts_in_memory_packets_larger_than_native_argv(tmp_path, host):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "large-request.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root)
$ErrorActionPreference = 'Stop'
$value = 'x' * 120000
$result = & $Source source-id `
    -SourceJson (@{ source = 'opaque'; id = $value } | ConvertTo-Json -Compress) `
    -CopilotHome (Join-Path $Root 'copilot') -DurableHome (Join-Path $Root 'durable') |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $result.canonical -cne ('opaque:' + $value)) {
    throw 'The SDK imposed the native argv limit on an in-memory request'
}
@{ canonical_length = $result.canonical.Length } | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"canonical_length": 120007}


@pytest.mark.parametrize("host", HOSTS or [None])
def test_unsupported_python_retains_diagnostic_when_request_pipe_closes(tmp_path, host):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    transport = SCRIPT.with_name("shell-caller-owned.ps1")
    source = transport.read_text(encoding="utf-8")
    guard = "if sys.version_info < (3,10):"
    assert source.count(guard) == 1
    copied = tmp_path / SCRIPT.name
    shutil.copyfile(SCRIPT, copied)
    (tmp_path / transport.name).write_text(source.replace(guard, "if True:"), encoding="utf-8")
    (tmp_path / "installation_context.py").write_text(
        "raise AssertionError('unsupported Python reached application work')\n", encoding="utf-8",
    )
    harness = tmp_path / "unsupported.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root)
$value = 'x' * 120000
& $Source source-id `
    -SourceJson (@{ source = 'opaque'; id = $value } | ConvertTo-Json -Compress) `
    -CopilotHome (Join-Path $Root 'copilot') -DurableHome (Join-Path $Root 'durable')
exit $LASTEXITCODE
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(copied), "-Root", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 1
    assert not result.stdout.strip()
    assert "Python 3.10+ is required on PATH" in result.stderr
    assert "unsupported Python reached application work" not in result.stderr
    assert not (tmp_path / "copilot").exists()
    assert not (tmp_path / "durable").exists()


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("failure", [False, True], ids=["stdout", "stderr"])
def test_sdk_drains_large_captured_output_before_waiting(tmp_path, host, failure):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "capture.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root, [string]$Failure)
$longValue = 'x' * 18000
$descriptor = if ($Failure -ceq 'true') {
    @{ source = $longValue; id = 'example' }
} else {
    @{ source = 'opaque'; id = $longValue }
}
& $Source source-id -SourceJson ($descriptor | ConvertTo-Json -Compress) `
    -CopilotHome (Join-Path $Root 'copilot') -DurableHome (Join-Path $Root 'durable')
exit $LASTEXITCODE
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path), "-Failure", str(failure).lower()],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if failure:
        assert result.returncode == 1
        assert not result.stdout.strip()
        assert "Unsupported source kind" in result.stderr
        assert len(result.stderr) > 18000
    else:
        assert result.returncode == 0, result.stderr
        assert not result.stderr.strip()
        assert json.loads(result.stdout)["canonical"] == "opaque:" + "x" * 18000
        assert len(result.stdout) > 36000


@pytest.mark.skipif(os.name != "nt", reason="Native Windows rooted-path semantics")
@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("path_kind", ["absolute", "percent", "root-relative", "drive-relative"])
def test_sdk_preserves_native_paths_and_os_process_directory(tmp_path, host, path_kind):
    if host is None:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "native-paths.ps1"
    harness.write_text(
        r"""
param([string]$Source, [string]$Root, [string]$Kind)
$ErrorActionPreference = 'Stop'
$env:COPILOT_EXTENSIONS_CONTEXT = $null
$env:COPILOT_PLUGIN_ROOT = $null
$env:SDK_COMPONENT = 'environment'
$processDirectory = Join-Path $Root 'process directory'
$location = Join-Path $Root 'PowerShell location'
$payload = Join-Path $processDirectory 'payload [1]'
$project = Join-Path $processDirectory 'project [1]'
$sourceRoot = Join-Path $Root 'environment'
foreach ($directory in @($processDirectory, $location, $payload, $project, $sourceRoot)) {
    [void][IO.Directory]::CreateDirectory($directory)
}
[Environment]::CurrentDirectory = $processDirectory
Set-Location -LiteralPath $location
$raw = $payload
if ($Kind -ceq 'percent') {
    $env:SDK_PAYLOAD = 'payload [1]'
    $raw = Join-Path $processDirectory '%SDK_PAYLOAD%'
} elseif ($Kind -ceq 'root-relative') {
    $raw = $payload.Substring(2)
} elseif ($Kind -ceq 'drive-relative') {
    $raw = [IO.Path]::GetPathRoot($payload).Substring(0, 2) + 'payload [1]'
}
$sourcePath = Join-Path $Root '%SDK_COMPONENT%'
$parameters = @{
    Action = 'resolve'
    SourceJson = (@{ source = 'directory'; path = $sourcePath } | ConvertTo-Json -Compress)
    PluginId = 'agent-example'
    PayloadRoot = $raw
    CopilotHome = Join-Path $processDirectory 'copilot'
    DurableHome = [IO.Path]::GetPathRoot($payload).Substring(0, 2) + 'durable [1]'
    ProjectRoot = 'project [1]'
}
$result = & $Source @parameters | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Native path invocation failed' }
if ([Environment]::CurrentDirectory -cne $processDirectory -or
    (Get-Location).Path -cne $location) { throw 'The SDK changed its caller directory' }
@{ result = $result; payload = $payload; sourceRoot = $sourceRoot;
   durable = Join-Path $processDirectory 'durable [1]' } | ConvertTo-Json -Depth 20 -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness),
         "-Source", str(SCRIPT), "-Root", str(tmp_path), "-Kind", path_kind],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["result"]["payloadRoot"] == value["payload"]
    assert value["result"]["source"]["canonical"] == "directory:" + value["sourceRoot"]
    assert Path(value["result"]["cellRoot"]).is_relative_to(Path(value["durable"]))
    assert not Path(value["durable"]).exists()
