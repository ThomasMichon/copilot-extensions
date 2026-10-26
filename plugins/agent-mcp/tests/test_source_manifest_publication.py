"""Actual CLI manifest writers retain admitted source facts after activation."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "cli_manifest_native_fixture", Path(__file__).with_name("test_source_install_transactions.py"),
)
assert SPEC and SPEC.loader
native = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = native
SPEC.loader.exec_module(native)
pytestmark = pytest.mark.guard
PLUGINS = (
    "agent-codespaces", "agent-mcp", "agent-containers", "agent-ssh",
    "agent-machines", "agent-pull-requests", "budget-guidance",
)
SHARED = ("agent-codespaces", "agent-ssh", "agent-pull-requests")


def _text(plugin: str, language: str) -> str:
    entry = "init" if plugin in {"agent-mcp", "agent-containers", "agent-machines"} else "install"
    return (native.ROOT / "plugins" / plugin / "scripts" / f"{entry}.{language}").read_text(
        encoding="utf-8",
    )


def _function(plugin: str, name: str, language: str) -> str:
    pattern = (
        rf"(?ms)^function {re.escape(name)} \{{.*?^\}}"
        if language == "ps1" else rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}(?!\nEOF(?:\n|$))"
    )
    match = re.search(pattern, _text(plugin, language))
    assert match, (plugin, name)
    return match.group().replace("$PSScriptRoot", "$ScriptDir") + "\n"


def _prepare(plugin: str, language: str) -> str:
    text = _text(plugin, language)
    start = text.index("# Source manifest inputs are captured before activation.")
    endings = {
        ("agent-codespaces", "sh"): '    if ! "$py" "$vr"',
        ("agent-codespaces", "ps1"): "    & $py $vr",
        ("agent-mcp", "sh"): '    if ! "$VENV_PYTHON" "$VR_SCRIPT"',
        ("agent-mcp", "ps1"): "    Invoke-Hidden -FilePath $VenvPython -ArgList @($VrScript",
        ("agent-containers", "sh"): "if ! profile_result=",
        ("agent-containers", "ps1"): "$profileJson = Invoke-Hidden",
        ("agent-machines", "sh"): '\n\nif [[ ( "$ACTION"',
        ("agent-machines", "ps1"): "\nif (\n    $Action -in @('cell-provision'",
    }
    ending = endings.get((plugin, language))
    if ending is None:
        ending = "_versioned_activate ||" if language == "sh" else "if (-not (Invoke-VersionedActivate"
    return text[start:text.index(ending, start)].replace("$PSScriptRoot", "$ScriptDir") + "\n"


def _initialize(plugin: str, language: str) -> str:
    text = _text(plugin, language)
    if plugin == "agent-containers":
        if language == "ps1":
            start = text.index("$sourceMetadata = Join-Path")
            end = text.index("\nfunction Assert-SourceRuntimeIdentity", start)
        else:
            start = text.index('if [[ -n "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN+x}"')
            end = text.index("\n_assert_source_runtime_identity()", start)
        return text[start:end] + "\n"
    name = "Initialize-SourceRuntime" if language == "ps1" else "_initialize_source_runtime"
    if plugin in {"agent-mcp", "agent-codespaces"}:
        source = native.ROOT / "plugins" / plugin / "scripts" / f"source-runtime.{language}"
        pattern = (
            rf"(?ms)^function {name} \{{.*?^\}}"
            if language == "ps1" else rf"(?ms)^{name}\(\) \{{.*?^\}}"
        )
        match = re.search(pattern, source.read_text(encoding="utf-8"))
        assert match, (plugin, name)
        function = match.group()
    else:
        function = _function(plugin, name, language)
    return function + "\n" + name + "\n"


def _profile_mapping(language: str) -> str:
    text = _text("agent-containers", language)
    if language == "ps1":
        start = text.index("$VenvDir = [string]$profileResult.venv")
        end = text.index("\nWrite-Ok", start)
    else:
        start = text.index("IFS=$'\\t' read -r RUNTIME_PROFILE")
        end = text.index("\n_ok", start)
    return text[start:end] + "\n"


def _writer(plugin: str, language: str) -> str:
    text = _text(plugin, language)
    if plugin == "agent-codespaces":
        name = "_write_codespaces_deploy_manifest" if language == "sh" else "Write-CodespacesDeployManifest"
        return _function(plugin, name, language) + "\n" + name
    if plugin == "agent-ssh":
        start = (
            text.index("\nSOURCE_PATH=", text.index("# Source manifest inputs"))
            if language == "sh" else
            text.index("\n$sourcePath =", text.index("# Source manifest inputs"))
        )
        end = text.index("\necho ''" if language == "sh" else "\n$prevEAP =", start)
    elif plugin == "agent-pull-requests":
        start = text.index(
            'source_fields="$(_source_manifest_fields)"' if language == "sh" else
            "$sourceFields = Get-SourceManifestFields",
        )
        end = text.index('\necho ""' if language == "sh" else "\n    $prevEAP =", start)
    elif plugin == "budget-guidance":
        start = text.index('KIND="$(_source_kind "$PLUGIN_DIR")"' if language == "sh" else "$kind = Get-SourceKind")
        end = text.index("\necho ''" if language == "sh" else "\n$prevEAP =", start)
    else:
        start = text.index("# -- 5. Deploy manifest" if language == "sh" else "# -- 5. Write deploy manifest")
        end_marker = (
            "# -- Machine-local config" if plugin == "agent-containers" else "# -- 6. Verify"
        )
        end = text.index(end_marker, start)
    return text[start:end].replace("$PSScriptRoot", "$ScriptDir")


def _payload(tmp_path: Path, plugin: str) -> tuple[Path, dict[str, object]]:
    payload = tmp_path / "payload with spaces"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for source in (native.ROOT / "plugins" / plugin / "scripts").glob("versioned_runtime*.*"):
        shutil.copyfile(source, scripts / source.name)
    (payload / "src").mkdir()
    (payload / "src" / "input.py").write_bytes(b"original\n")
    (payload / "libs").mkdir()
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "{plugin}"\nversion = "{native.VERSION}"\n', encoding="utf-8",
    )
    (payload / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": native.VERSION}), encoding="utf-8",
    )
    digest = native.IDENTITY.assembly_hash(payload)
    identity = native.IDENTITY.SourceIdentity(
        plugin, native.VERSION, "a" * 40, "b" * 40, digest,
        native.IDENTITY.runtime_version(native.VERSION, "a" * 40, digest),
    )
    (payload / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    return payload, native.IDENTITY.manifest_fields(identity)


def _setup(tmp_path: Path, plugin: str, language: str, payload: Path, fields: dict[str, object]) -> str:
    def q(value: Path | str) -> str:
        return native._quote(value, language)

    root = tmp_path / "install"
    root.mkdir()
    (root / "deploy-manifest.json").write_bytes(b"previous manifest")
    (root / "current-version").write_bytes(b"previous")
    selected = str(fields["runtime_version"])
    runtime = selected
    if plugin == "agent-containers":
        runtime = native.IDENTITY.runtime_version(
            native.VERSION + "+provider-exec", "a" * 40, fields["runtime_source"]["assembly_hash"],
        )
    slot = root / "versions" / runtime
    slot.mkdir(parents=True)
    pin = json.dumps(fields, sort_keys=True)
    if language == "ps1":
        helpers = [_function(plugin, "Get-SourceKind", language), _function(plugin, "Get-GitInfo", language)]
        if plugin in SHARED:
            helpers.append(_function(plugin, "Get-PayloadHash", language))
        if plugin == "agent-ssh":
            helpers.append(_function(plugin, "Get-SnapshotSourceMarkerPath", language))
        if plugin == "agent-pull-requests":
            helpers.append(_function(plugin, "Get-SourceManifestFields", language))
        return f"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$env:COMPUTERNAME = 'fixture'
$PluginDir = {q(payload)}
$sourceManifestPath = $PluginDir
$ScriptDir = Join-Path $PluginDir 'scripts'
$PSScriptRoot = $ScriptDir
$InstallDir = {q(root)}
$ManifestPath = Join-Path $InstallDir 'deploy-manifest.json'
$VenvDir = Join-Path (Join-Path $InstallDir 'versions') {q(native.VERSION)}
$LinkDir = Join-Path $InstallDir '.venv-legacy'
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PluginDir)
$PkgSrcDir = Join-Path $PluginDir 'src'
$SrcVersion = {q(native.VERSION)}
$PackageVersion = {q(native.VERSION)}
$SourceRuntimeIdentity = {q(pin)}
$SourceSelected = $false
$CellMode = $false
$utf8NoBom = [Text.UTF8Encoding]::new($false)
function Write-Ok($value) {{ }}
function Write-ServiceOk($value) {{ }}
function Write-Fail($value) {{ throw $value }}
function Get-BootstrapPython {{ return {q(sys.executable)} }}
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
. {q(native.ROOT / 'libs' / 'installer-engine' / 'installer-engine.ps1')}
function Get-SourceRuntimeIdentity {{
    param([string]$Payload = $PluginDir)
    $detail = ''
    $observed = Invoke-Hidden -FilePath {q(sys.executable)} -ArgList @(
        '-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $Payload, '--package-version', $PackageVersion,
        '--plugin', {q(plugin)}, '--format', 'json'
    ) -ErrorOutput ([ref]$detail)
    if ($LASTEXITCODE -ne 0) {{
        throw "Real source receipt admission failed: $detail"
    }}
    return $observed.Trim()
}}
function Get-SourceRuntimeVersion {{ param($Format) Get-SourceRuntimeIdentity }}
function Assert-SourceRuntime {{
    param([string]$Payload = $PluginDir)
    if ((Get-SourceRuntimeIdentity -Payload $Payload) -cne $SourceRuntimeIdentity) {{
        throw 'Real source receipt admission changed'
    }}
}}
function Assert-SourceRuntimeIdentity {{ param([string]$Payload = $PluginDir) Assert-SourceRuntime -Payload $Payload }}
function Test-SourceRuntimeUnchanged {{ Assert-SourceRuntime }}
{chr(10).join(helpers)}
{_initialize(plugin, language)}
$profileResult = [pscustomobject]@{{
    runtimeVersion = {q(runtime)}
    profile = 'provider-exec'
    venv = {q(slot)}
    python = {q(slot / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))}
    runtimeSource = ($SourceRuntimeIdentity | ConvertFrom-Json).runtime_source
}}
{_profile_mapping(language) if plugin == 'agent-containers' else ''}
if ($VenvDir -cne {q(slot)}) {{ throw 'Actual source initializer selected the wrong slot' }}
Assert-SourceRuntime
$payloadHash = if ({'$true' if plugin in SHARED else '$false'}) {{ Get-PayloadHash }} else {{ '' }}
"""
    helpers = [_function(plugin, "_source_kind", language), _function(plugin, "_git_info", language)]
    if plugin in SHARED:
        helpers.append(_function(plugin, "_payload_hash", language))
    if plugin == "agent-ssh":
        helpers.append(_function(plugin, "_snapshot_source_marker", language))
    if plugin == "agent-pull-requests":
        helpers.append(_function(plugin, "_source_manifest_fields", language))
    return f"""
set -euo pipefail
PLUGIN_DIR={q(payload)}
SOURCE_MANIFEST_PATH="$PLUGIN_DIR"
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={q(root)}
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"
VENV_DIR="$INSTALL_DIR/versions/{native.VERSION}"
LINK_DIR="$INSTALL_DIR/.venv-legacy"
PKG_SRC_DIR="$PLUGIN_DIR/src"
PACKAGE_VERSION={q(native.VERSION)}
SRC_VERSION={q(native.VERSION)}
SOURCE_RUNTIME_IDENTITY={q(pin)}
SOURCE_SELECTED=0
CELL_MODE=0
PYTHON_CMD={q(sys.executable)}
RUNTIME_VERSION={q(runtime)}
RUNTIME_PROFILE=provider-exec
source {q(native.ROOT / 'libs' / 'installer-engine' / 'installer-engine.sh')}
_fail() {{ printf '%s\\n' "$*" >&2; }}
_ok() {{ :; }}
_bootstrap_python() {{ printf '%s\\n' {q(sys.executable)}; }}
_source_python() {{ _bootstrap_python; }}
_source_runtime_identity() {{
    "$PYTHON_CMD" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin {q(plugin)} --format json
}}
_source_runtime_version() {{
    "$PYTHON_CMD" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin {q(plugin)} "$@"
}}
_assert_source_runtime() {{
    local observed
    observed="$("$PYTHON_CMD" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "${{1:-$PLUGIN_DIR}}" --package-version "$PACKAGE_VERSION" \
        --plugin {q(plugin)} --format json)" || return 1
    [[ "$observed" == "$SOURCE_RUNTIME_IDENTITY" ]] || return 1
}}
_assert_source_runtime_identity() {{ _assert_source_runtime "$@"; }}
_source_runtime_unchanged() {{ _assert_source_runtime; }}
{chr(10).join(helpers)}
{_initialize(plugin, language)}
profile_result={q('provider-exec' + chr(9) + runtime + chr(9) + native._quote(slot, language)[1:-1] + chr(9) + native._quote(slot / 'bin/python', language)[1:-1])}
{_profile_mapping(language) if plugin == 'agent-containers' else ''}
[[ "$VENV_DIR" == {q(slot)} ]]
_assert_source_runtime
PAYLOAD_HASH="$({ '_payload_hash' if plugin in SHARED else "printf ''" })"
"""


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("language", ["ps1", "sh"])
def test_actual_manifest_writer_uses_precommit_source_facts(
    tmp_path: Path, plugin: str, language: str,
) -> None:
    payload, fields = _payload(tmp_path, plugin)
    script = _setup(tmp_path, plugin, language, payload, fields)
    script += _prepare(plugin, language)
    if language == "ps1":
        selected_variable = "$profileResult.runtimeVersion" if plugin == "agent-containers" else "$SrcVersion"
        script += f"""
$hashBefore = $payloadHash
[IO.File]::WriteAllText((Join-Path $InstallDir 'admitted-hash'), $hashBefore)
[IO.File]::WriteAllText((Join-Path $InstallDir 'current-version'), {selected_variable})
[IO.File]::WriteAllText((Join-Path $PluginDir 'src/input.py'), 'changed after activation')
[IO.File]::WriteAllText((Join-Path $PluginDir 'pyproject.toml'), 'version = "9.9.9"')
"""
    else:
        selected_variable = "$RUNTIME_VERSION" if plugin == "agent-containers" else "$SRC_VERSION"
        script += f"""
printf '%s' "$PAYLOAD_HASH" > "$INSTALL_DIR/admitted-hash"
printf '%s' "{selected_variable}" > "$INSTALL_DIR/current-version"
printf 'changed after activation' > "$PLUGIN_DIR/src/input.py"
printf 'version = "9.9.9"' > "$PLUGIN_DIR/pyproject.toml"
"""
    script += _writer(plugin, language)
    result = native._run(tmp_path, language, script)
    assert result.returncode == 0, result.stdout + result.stderr
    root = tmp_path / "install"
    manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["runtime_source"] == fields["runtime_source"]
    assert manifest["source"]["version"] == native.VERSION
    owner = native._quote(payload, language)[1:-1] if language == "sh" else str(payload)
    assert manifest["source"]["path"].replace("\\", "/") == owner.replace("\\", "/")
    assert manifest["source"]["kind"] == "local"
    expected = fields["runtime_version"]
    if plugin == "agent-containers":
        expected = native.IDENTITY.runtime_version(
            native.VERSION + "+provider-exec", "a" * 40, fields["runtime_source"]["assembly_hash"],
        )
        assert manifest["runtime_profile"] == "provider-exec"
    assert manifest["runtime_version"] == expected
    slot = root / "versions" / expected
    serving = native._quote(slot, language)[1:-1] if language == "sh" else str(slot)
    assert manifest["venv"].replace("\\", "/") == serving.replace("\\", "/")
    assert (root / "current-version").read_text() == expected
    if plugin in SHARED:
        assert manifest["source"]["content_hash"] == (root / "admitted-hash").read_text()


@pytest.mark.parametrize("language", ["ps1", "sh"])
def test_actual_machines_cell_manifest_uses_pinned_source_after_cutover(
    tmp_path: Path, language: str,
) -> None:
    plugin = "agent-machines"
    payload, fields = _payload(tmp_path, plugin)
    script = _setup(tmp_path, plugin, language, payload, fields) + _prepare(plugin, language)
    if language == "ps1":
        script += _function(plugin, "Write-CellDeployManifest", language) + """
[IO.File]::WriteAllText((Join-Path $InstallDir 'current-version'), $SrcVersion)
[IO.File]::WriteAllText((Join-Path $PluginDir 'src/input.py'), 'changed after cutover')
Write-CellDeployManifest -PluginRoot $InstallDir -SourcePluginDir $PluginDir `
    -SourceVersion $PackageVersion -RuntimeSlot $VenvDir -RuntimeVersion $SrcVersion `
    -ContextPath (Join-Path $InstallDir 'context.json') -MarketplaceId 'fixture'
"""
    else:
        script += _function(plugin, "_json_escape", language)
        script += _function(plugin, "_write_cell_manifest", language) + """
printf '%s' "$SRC_VERSION" > "$INSTALL_DIR/current-version"
printf 'changed after cutover' > "$PLUGIN_DIR/src/input.py"
_write_cell_manifest "$INSTALL_DIR" "$PLUGIN_DIR" "$PACKAGE_VERSION" \
    "$VENV_DIR" "$SRC_VERSION" "$INSTALL_DIR/context.json" fixture
"""
    result = native._run(tmp_path, language, script)
    assert result.returncode == 0, result.stdout + result.stderr
    root = tmp_path / "install"
    manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["runtime_source"] == fields["runtime_source"]
    assert manifest["runtime_version"] == fields["runtime_version"]
    assert manifest["runtime"]["version"] == fields["runtime_version"]
    assert manifest["source"]["version"] == native.VERSION
    owner = native._quote(payload, language)[1:-1] if language == "sh" else str(payload)
    assert manifest["source"]["path"].replace("\\", "/") == owner.replace("\\", "/")
    assert manifest["runtime"]["selectedBy"]["path"] == manifest["source"]["path"]
