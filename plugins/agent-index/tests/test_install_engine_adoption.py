"""Contained installer mechanics: no real packages, engine, or service effects."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
ENGINE = REPO / "libs" / "installer-engine"


def function(name: str, ext: str) -> str:
    text = (PLUGIN / "scripts" / f"install.{ext}").read_text(encoding="utf-8")
    start = f"function {name}" if ext == "ps1" else f"{name}() {{"
    tail = text.split(start, 1)[1]
    next_function = r"\nfunction \w" if ext == "ps1" else r"\n[a-z_]+\(\) \{"
    return start + re.split(next_function, tail, maxsplit=1)[0]


def environment(tmp_path: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_", "PYTHON", "UV_", "PIP_", "GIT_"))
    }
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMP", "TEMP", "TMPDIR"):
        root = tmp_path / key.lower()
        root.mkdir(exist_ok=True)
        env[key] = str(root)
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    return env


def run_ps(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    harness = tmp_path / "harness.ps1"
    formatter = next(
        line for line in (PLUGIN / "scripts/install.ps1").read_text().splitlines()
        if line.startswith("function Write-Step ")
    )
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Ok { param($Msg) }\n"
        "function Write-Warn { param($Msg) }\n"
        + formatter + "\n"
        "function Write-Skip { param($Msg) }\n"
        "function Write-Fail { param($Msg) throw $Msg }\n"
        "function Start-Process { throw 'Forbidden process launch' }\n"
        "function Stop-Process { throw 'Forbidden process termination' }\n"
        "function Register-ScheduledTask { throw 'Forbidden task registration' }\n"
        "function Start-ScheduledTask { throw 'Forbidden task activation' }\n"
        "function Restart-Service { throw 'Forbidden service restart' }\n"
        "function Invoke-WebRequest { throw 'Forbidden bootstrap download' }\n"
        f". '{ENGINE / 'installer-engine.ps1'}'\n" + script,
        encoding="utf-8",
    )
    return subprocess.run(
        [pwsh, "-NoProfile", "-File", str(harness)],
        env=environment(tmp_path), capture_output=True, text=True, timeout=25,
    )


def test_canonical_references_and_both_runtime_builds():
    sh = (PLUGIN / "scripts/install.sh").read_text(encoding="utf-8")
    ps = (PLUGIN / "scripts/install.ps1").read_text(encoding="utf-8")
    assert '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"' in sh
    assert r". (Join-Path $PSScriptRoot '..\..\..\libs\installer-engine\installer-engine.ps1')" in ps
    assert not (PLUGIN / "scripts/installer-engine.sh").exists()
    assert not (PLUGIN / "scripts/installer-engine.ps1").exists()
    for directory in ("$VENV_DIR", "$ENGINE_VENV", "$server_venv_dir"):
        assert f'invoke_uv_venv_resilient "${{UV_COMMAND:-uv}}" "{directory}" --allow-existing' in sh
    for directory in ("$VenvDir", "$EngineVenv", "$serverVenvDir"):
        assert f"New-IndexVenv -Dir {directory}" in ps
    assert "Invoke-UvVenvResilient" in function("New-IndexVenv", "ps1")
    assert "Invoke-UvPipInstallResilient" in function("Invoke-IndexUvPipInstall", "ps1")


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("transient", [False, True])
def test_posix_engine_build_order_and_durable_skip(tmp_path: Path, failure: bool, transient: bool):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    log = tmp_path / "calls"
    uv = tmp_path / "uv"
    retry = (
        f' if [[ ! -f "{tmp_path / "venv-attempt"}" ]]; then\n'
        f'  touch "{tmp_path / "venv-attempt"}"; exit 0; fi\n'
        if transient else ""
    )
    uv.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        'if [[ "$1" == --version ]]; then exit 0; fi\n'
        'if [[ "$1" == venv ]]; then\n'
        + retry +
        ' mkdir -p "$2/bin"; echo "home = fixture" > "$2/pyvenv.cfg"\n'
        ' printf "#!/usr/bin/env bash\\nexit 0\\n" > "$2/bin/python"\n'
        ' chmod +x "$2/bin/python"; exit 0\nfi\n'
        + ('if [[ "$*" == *agent-procutil* ]]; then exit 37; fi\n' if failure else ""),
        encoding="utf-8",
    )
    uv.chmod(0o755)
    for library in ("zdd", "agent-procutil"):
        directory = tmp_path / "plugin/libs" / library
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    env = environment(tmp_path)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["AGENT_INDEX_TORCH_INDEX"] = "https://example.invalid/cuda"
    script = f"""
set -uo pipefail
. '{ENGINE / "installer-engine.sh"}'
_ok() {{ :; }}; _skip() {{ :; }}; _step() {{ :; }}; _warn() {{ :; }}
_fail() {{ echo "$*" >&2; }}
sleep() {{ :; }}
systemctl() {{ echo FORBIDDEN >&2; exit 97; }}
curl() {{ echo FORBIDDEN >&2; exit 97; }}
wget() {{ echo FORBIDDEN >&2; exit 97; }}
_find_python() {{ echo '{tmp_path / "forbidden-base-python"}'; }}
_ensure_uv_index() {{ :; }}
INSTALL_DIR='{tmp_path / "runtime"}'
PLUGIN_DIR='{tmp_path / "plugin"}'
ENGINE_HOME='{tmp_path / "engine"}'
ENGINE_VENV="$ENGINE_HOME/.venv"
ENGINE_VENV_PYTHON="$ENGINE_VENV/bin/python"
{function("_ensure_uv", "sh")}
{function("_uv_pip_install", "sh")}
{function("_resolve_vendored_lib", "sh")}
_resolve_zdd() {{ _resolve_vendored_lib zdd; }}
{function("_install_engine", "sh")}
_install_engine
rc=$?
if [[ $rc -eq 0 ]]; then _install_engine; fi
exit "$rc"
"""
    result = subprocess.run([bash, "-c", script], env=env, capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == (1 if failure else 0), result.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == "--version"
    assert sum(line.startswith("venv ") for line in calls) == (2 if transient else 1)
    installs = [line for line in calls if line.startswith("pip install")]
    assert all("--python" not in line for line in calls if line.startswith("venv "))
    assert "zdd" in installs[0] and "agent-procutil" in installs[1]
    if failure:
        assert len(installs) == 2
    else:
        assert str(tmp_path / "plugin") in installs[2]
        assert str(tmp_path / "plugin/server") in installs[3]
        assert "--index-url https://example.invalid/cuda --no-deps --reinstall-package torch torch" in installs[4]
        assert len(installs) == 5


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_shared_pip_retry_preserves_exact_exit(tmp_path: Path, ext: str):
    if ext == "sh":
        bash = shutil.which("bash")
        if os.name == "nt" or not bash:
            pytest.skip("native POSIX bash is unavailable")
        script = f"""
. '{ENGINE / "installer-engine.sh"}'
_warn() {{ :; }}; sleep() {{ :; }}
uv_fixture() {{ echo ordinary-failure; return 37; }}
{function("_uv_pip_install", "sh")}
UV_COMMAND=uv_fixture
_uv_pip_install --python fixture package
exit $?
"""
        result = subprocess.run([bash, "-c", script], env=environment(tmp_path),
                                capture_output=True, text=True, timeout=20)
    else:
        result = run_ps(tmp_path, f"""
function uv_fixture {{ 'ordinary-failure'; $global:LASTEXITCODE = 37 }}
$script:UvCommand = 'uv_fixture'
{function("Invoke-IndexUvPipInstall", "ps1")}
Invoke-IndexUvPipInstall --python fixture package
exit $LASTEXITCODE
""")
    assert result.returncode == 37, result.stderr
    assert "ordinary-failure" in result.stdout


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_shared_pip_retries_only_transient_failures(tmp_path: Path, ext: str):
    counter = tmp_path / "counter"
    if ext == "sh":
        bash = shutil.which("bash")
        if os.name == "nt" or not bash:
            pytest.skip("native POSIX bash is unavailable")
        script = f"""
. '{ENGINE / "installer-engine.sh"}'
_warn() {{ :; }}; sleep() {{ :; }}
uv_fixture() {{
    if [[ ! -f '{counter}' ]]; then
        echo 1 > '{counter}'; echo 'SRE module mismatch'; return 37
    fi
    echo 2 > '{counter}'; echo recovered; return 0
}}
{function("_uv_pip_install", "sh")}
UV_COMMAND=uv_fixture
_uv_pip_install --python fixture package
exit $?
"""
        result = subprocess.run([bash, "-c", script], env=environment(tmp_path),
                                capture_output=True, text=True, timeout=20)
    else:
        result = run_ps(tmp_path, f"""
function Start-Sleep {{ param($Seconds) }}
function uv_fixture {{
    if (-not (Test-Path '{counter}')) {{
        Set-Content '{counter}' '1'; 'SRE module mismatch'; $global:LASTEXITCODE = 37
    }} else {{ Set-Content '{counter}' '2'; 'recovered'; $global:LASTEXITCODE = 0 }}
}}
$script:UvCommand = 'uv_fixture'
{function("Invoke-IndexUvPipInstall", "ps1")}
Invoke-IndexUvPipInstall --python fixture package
exit $LASTEXITCODE
""")
    assert result.returncode == 0, result.stderr
    assert counter.read_text().strip() == "2"
    assert "recovered" in result.stdout


def test_powershell_existing_invalid_interpreter_cannot_bypass_rebuild(tmp_path: Path):
    result = run_ps(tmp_path, function("New-IndexVenv", "ps1") + f"""
$env:OS = 'Windows_NT'
$env:SystemDrive = '/'
$script:UvCommand = 'fixture-only-uv'
$Dir = '{tmp_path / "venv"}'
New-Item -ItemType Directory -Path $Dir -Force | Out-Null
$Python = Join-Path $Dir 'python'
Set-Content $Python 'invalid'
Set-Content (Join-Path $Dir 'pyvenv.cfg') 'present'
function Get-SignedBasePython {{ 'fixture-only-python' }}
function Get-AuthenticodeSignature {{ param($Path) [pscustomobject]@{{ Status='Unknown' }} }}
function Test-IndexVenv {{ param($Dir, $Python) return $script:Healthy }}
function Invoke-NativeCapture {{
    param($Command)
    if (Test-Path $Python) {{ throw 'invalid interpreter survived rebuild' }}
    $script:Rebuilt = $true; $script:Healthy = $true
    [pscustomobject]@{{ ExitCode=0; Output='' }}
}}
if (-not (New-IndexVenv -Dir $Dir -Python $Python -PythonCmd 'forbidden')) {{ throw 'failed' }}
if (-not $script:Rebuilt) {{ throw 'existing invalid python bypassed rebuild' }}
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("exit_code,healthy", [(0, True), (1, True), (0, False), (1, False)])
def test_signed_result_health_not_exit_controls_recovery(
    tmp_path: Path, exit_code: int, healthy: bool,
):
    result = run_ps(tmp_path, f"""
{function("New-IndexVenv", "ps1")}
$env:OS = 'Windows_NT'
$env:SystemDrive = '/'
$script:UvCommand = 'fixture-only-uv'
$Dir = '{tmp_path / "venv"}'
function Get-SignedBasePython {{ 'fixture-only-python' }}
function Set-Location {{ param($Path) }}
function Test-IndexVenv {{ param($Dir, $Python) return $script:Healthy }}
function Invoke-NativeCapture {{
    param($Command)
    $script:Healthy = ${str(healthy).lower()}
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    Set-Content (Join-Path $Dir 'signed-attempt') 'fixture'
    [pscustomobject]@{{ ExitCode = {exit_code}; Output = 'fixture' }}
}}
function Invoke-UvVenvResilient {{
    param($UvCommand, $VenvDir, $Arguments)
    if ($UvCommand -ne 'fixture-only-uv') {{ throw 'escaped fake uv' }}
    if ($Arguments -contains '--python') {{ throw 'changed interpreter selection policy' }}
    if (Test-Path (Join-Path $Dir 'signed-attempt')) {{ throw 'unusable signed attempt retained' }}
    $script:Retried = $true
    $script:Healthy = $true
    [pscustomobject]@{{ ExitCode = 0; Output = '' }}
}}
if (-not (New-IndexVenv -Dir $Dir -Python (Join-Path $Dir 'python') -PythonCmd 'forbidden')) {{ throw 'creation failed' }}
if ([bool]$script:Retried -ne ${str(not healthy).lower()}) {{ throw 'wrong recovery path' }}
""")
    assert result.returncode == 0, result.stderr


def test_windows_cli_preserves_python_fallback_when_uv_acquisition_fails(tmp_path: Path):
    runtime = function("Install-Runtime", "ps1")
    acquisition = "    $script:UvCommand = Ensure-Uv" + runtime.split(
        "    $script:UvCommand = Ensure-Uv", 1
    )[1].split("    # Detach an invalid active marker", 1)[0]
    result = run_ps(tmp_path, function("New-IndexVenv", "ps1") + f"""
$env:OS = 'Installer_Test'
$InstallDir = '{tmp_path / "runtime"}'
$Dir = Join-Path $InstallDir 'venv'
$Python = Join-Path $Dir 'python'
function Ensure-Uv {{ param($InstallRoot) return $null }}
function Invoke-UvVenvResilient {{ throw 'missing uv must not be invoked' }}
function Test-IndexVenv {{
    param($Dir, $Python)
    return ((Test-Path $Python) -and (Test-Path (Join-Path $Dir 'pyvenv.cfg')))
}}
function fixture_python {{
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    Set-Content $Python 'fixture'
    Set-Content (Join-Path $Dir 'pyvenv.cfg') 'fixture'
    $script:PythonFallbackUsed=$true
    $global:LASTEXITCODE=0
}}
{acquisition}
if (-not (New-IndexVenv -Dir $Dir -Python $Python -PythonCmd 'fixture_python')) {{ throw 'Python fallback failed' }}
if (-not $script:PythonFallbackUsed) {{ throw 'Python fallback was bypassed' }}
""")
    assert result.returncode == 0, result.stderr


def test_windows_engine_retains_uv_policy_without_signed_rebuild(tmp_path: Path):
    assert "-PreferSignedPython $false" in function("Install-Engine", "ps1")
    result = run_ps(tmp_path, function("New-IndexVenv", "ps1") + f"""
$env:OS = 'Windows_NT'
$env:SystemDrive = '/'
$Dir = '{tmp_path / "engine"}'
$script:UvCommand = 'fixture-only-uv'
function Get-SignedBasePython {{ throw 'engine policy must not discover signed Python' }}
function Get-AuthenticodeSignature {{ throw 'engine policy must not replace a healthy interpreter' }}
function Set-Location {{ param($Path) }}
function Test-IndexVenv {{ param($Dir, $Python) return $script:Healthy }}
function Invoke-UvVenvResilient {{
    param($UvCommand, $VenvDir, $Arguments)
    if ($UvCommand -ne 'fixture-only-uv' -or $Arguments -contains '--python') {{ throw 'changed uv policy' }}
    $script:Healthy=$true
    $script:UvCalls=1+$script:UvCalls
    [pscustomobject]@{{ ExitCode=0; Output='' }}
}}
function Invoke-NativeCapture {{ throw 'engine unexpectedly used base Python' }}
if (-not (New-IndexVenv -Dir $Dir -Python (Join-Path $Dir 'python') -PythonCmd 'forbidden' -PreferSignedPython $false)) {{ throw 'engine build failed' }}
if (-not (New-IndexVenv -Dir $Dir -Python (Join-Path $Dir 'python') -PythonCmd 'forbidden' -PreferSignedPython $false)) {{ throw 'healthy engine reuse failed' }}
if ($script:UvCalls -ne 1) {{ throw 'healthy durable engine was rebuilt' }}
""")
    assert result.returncode == 0, result.stderr


def test_powershell_snapshot_is_immutable_self_contained_and_ordered(tmp_path: Path):
    # Copy only into a disposable tree; the original source is never mutated.
    source = tmp_path / "source/plugins/agent-index"
    (source / "scripts").mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "plugin.json"):
        shutil.copy2(PLUGIN / name, source / name)
    for ext in ("sh", "ps1"):
        shutil.copy2(PLUGIN / "scripts" / f"install.{ext}", source / "scripts" / f"install.{ext}")
    (source / "server").mkdir()
    shutil.copy2(PLUGIN / "server/pyproject.toml", source / "server/pyproject.toml")
    for library in ("zdd", "agent-procutil", "dropin-registry"):
        origin = (PLUGIN if library == "dropin-registry" else REPO) / "libs" / library
        destination = (source if library == "dropin-registry" else source.parents[1]) / "libs" / library
        destination.mkdir(parents=True)
        shutil.copy2(origin / "pyproject.toml", destination / "pyproject.toml")
        (destination / "fixture.py").write_text("identity = 1\n", encoding="utf-8")
    shutil.copytree(ENGINE, source.parents[1] / "libs/installer-engine")
    (source / "Case.txt").write_text("upper", encoding="utf-8")
    (source / "case.txt").write_text("lower", encoding="utf-8")
    names = ("Get-PayloadHash", "Resolve-VendoredLib", "Publish-FileAtomically",
             "Enter-IndexStampLock", "Materialize-IndexSnapshot", "New-IndexSnapshot",
             "Invoke-Stamp")
    result = run_ps(tmp_path, "\n".join(function(name, "ps1") for name in names) + f"""
$PluginDir = '{source}'
$PSScriptRoot = '{source / "scripts"}'
$InstallDir = '{tmp_path / "runtime"}'
$LocalBin = '{tmp_path / "bin"}'
$SrcVersion = '1.0.0'
$probePayload = $PluginDir
function Test-VersionLt {{ param($A, $B) return $false }}
function Deploy-SetupGatedBinstub {{
    param($PayloadRoot)
    if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')) -cne $PayloadRoot) {{ throw 'wrong payload marker' }}
    $script:Launchers = 1 + $script:Launchers
}}
Invoke-Stamp
$first = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
Invoke-Stamp
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')) -cne $first) {{ throw 'unchanged stamp drift' }}
Set-Content -LiteralPath (Join-Path $PluginDir 'case.txt') 'changed'
$newer = New-IndexSnapshot
if ($newer -ceq $first) {{ throw 'case-distinct content omitted' }}
Invoke-Stamp
Add-Content '{source.parents[1] / "libs/installer-engine/installer-engine.sh"}' '# fixture edit'
$engineChanged = New-IndexSnapshot
if ($engineChanged -ceq $newer) {{ throw 'engine content omitted from identity' }}
Add-Content '{source.parents[1] / "libs/zdd/fixture.py"}' '# library edit'
$libraryChanged = New-IndexSnapshot
if ($libraryChanged -ceq $engineChanged) {{ throw 'library content omitted from identity' }}
$before = $script:Launchers
function New-IndexSnapshot {{ return $first }}
Invoke-Stamp
if ($script:Launchers -ne $before) {{ throw 'older snapshot overwrote launchers' }}
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')) -cne $newer) {{ throw 'older stamp replaced markers' }}
$first
$newer
""")
    assert result.returncode == 0, result.stderr
    snapshots = [Path(line) for line in result.stdout.splitlines() if line.startswith(str(tmp_path))]
    assert len(snapshots) == 2
    for snapshot in snapshots:
        for ext in ("sh", "ps1"):
            assert (snapshot / "scripts" / f"installer-engine.{ext}").read_bytes() == (
                ENGINE / f"installer-engine.{ext}"
            ).read_bytes()
        project = (snapshot / "pyproject.toml").read_text()
        assert "../../libs/" not in project and "editable = true" not in project
        for library in ("zdd", "agent-procutil", "dropin-registry"):
            assert (snapshot / "libs" / library / "pyproject.toml").is_file()
        assert (snapshot / "server/pyproject.toml").is_file()
    source.parents[1].rename(tmp_path / "unavailable-source")
    latest = snapshots[1]
    # Exercise the snapshot-local shared package mechanics with explicit fake uv.
    # The stub validates the complete dependency closure without installing it.
    result = run_ps(tmp_path, f"""
. '{latest / "scripts/installer-engine.ps1"}'
function fixture_uv {{
    if ($args[0] -ne 'pip' -or $args[1] -ne 'install') {{ throw 'unexpected uv operation' }}
    foreach ($lib in @('zdd', 'agent-procutil', 'dropin-registry')) {{
        if (-not (Test-Path (Join-Path '{latest}/libs' "$lib/pyproject.toml"))) {{ throw 'missing local dependency' }}
    }}
    if (-not (Test-Path '{latest / "server/pyproject.toml"}')) {{ throw 'missing engine package' }}
    $global:LASTEXITCODE = 0
}}
$result = Invoke-UvPipInstallResilient -UvCommand 'fixture_uv' -Arguments @('--python', 'fixture', '{latest}')
exit $result.ExitCode
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("staged", [False, True])
def test_manifest_fields_use_shared_writer(tmp_path: Path, staged: bool):
    original = tmp_path / ".copilot/installed-plugins/example/agent-index" if staged else PLUGIN
    result = run_ps(tmp_path, function("Write-Manifest", "ps1") + f"""
$InstallDir = '{tmp_path}'
$PluginDir = '{PLUGIN}'
$LinkDir = '{tmp_path / "versions/1.0.0"}'
$env:COPILOT_PLUGIN_STAGED_FROM = '{original if staged else ""}'
function Get-SourceKind {{ param($PluginPath) 'local' }}
function Get-GitInfo {{ param($Path) @{{ commit='abc'; branch='dev'; dirty=$false }} }}
Write-Manifest
""")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["schema_version"] == 3
    assert manifest["service"] == manifest["source"]["plugin"] == "agent-index"
    assert manifest["source"]["kind"] == ("marketplace" if staged else "local")
    assert manifest["source"]["commit"] == (None if staged else "abc")
    assert manifest["source"]["path"] == str(original).replace("\\", "/")
    assert manifest["venv"] == str(tmp_path / "versions/1.0.0").replace("\\", "/")

def test_snapshot_provision_keeps_snapshot_marker_without_original_source(tmp_path: Path):
    snapshot = tmp_path / "runtime/snapshots/1.0.0-identity"
    snapshot.mkdir(parents=True)
    result = run_ps(tmp_path,
                    function("Publish-FileAtomically", "ps1") +
                    function("Deploy-SetupGatedBinstub", "ps1") + f"""
$InstallDir = '{snapshot.parents[1]}'
$LocalBin = '{tmp_path / "bin"}'
$PluginDir = '{snapshot}'
$probePayload = '{tmp_path / "missing-authoring-source"}'
Deploy-SetupGatedBinstub
Deploy-SetupGatedBinstub
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')) -cne $PluginDir) {{
    throw 'provision rewrote snapshot pointer to unavailable origin'
}}
""")
    assert result.returncode == 0, result.stderr


def test_posix_cli_build_uses_shared_venv_and_keeps_engine_lazy(tmp_path: Path):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    log = tmp_path / "calls"
    uv = tmp_path / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        'if [[ "$1" == --version ]]; then exit 0; fi\n'
        'if [[ "$1" == venv ]]; then\n'
        ' mkdir -p "$2/bin"; echo "home = fixture" > "$2/pyvenv.cfg"\n'
        ' printf "#!/usr/bin/env bash\\nexit 0\\n" > "$2/bin/python"\n'
        ' chmod +x "$2/bin/python"; fi\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    plugin = tmp_path / "plugin"
    for library in ("zdd", "agent-procutil"):
        directory = plugin / "libs" / library
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (plugin / "src").mkdir()
    (plugin / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    env = environment(tmp_path)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    functions = "\n".join(function(name, "sh") for name in (
        "_ensure_uv", "_uv_pip_install", "_resolve_vendored_lib",
        "_install_server_venv", "_ensure_runtime",
    ))
    result = subprocess.run([bash, "-c", f"""
set -uo pipefail
. '{ENGINE / "installer-engine.sh"}'
_ok() {{ :; }}; _skip() {{ :; }}; _step() {{ :; }}; _warn() {{ :; }}
_fail() {{ echo "$*" >&2; }}
_stop() {{ echo forbidden-stop >&2; exit 97; }}
_install_engine() {{ echo forbidden-engine >&2; exit 97; }}
curl() {{ echo forbidden-download >&2; exit 97; }}
wget() {{ echo forbidden-download >&2; exit 97; }}
_find_python() {{ echo '{tmp_path / "forbidden-base-python"}'; }}
_ensure_uv_index() {{ :; }}
_activation_role() {{ echo client; }}
_runtime_origin_under() {{ return 0; }}
_versioned_slot_clean() {{ :; }}
_versioned_mark_complete() {{ :; }}
_versioned_activate() {{ :; }}
_versioned_current() {{ :; }}
_versioned_gc() {{ :; }}
deploy_binstub() {{ :; }}
_write_manifest() {{ :; }}
INSTALL_DIR='{tmp_path / "runtime"}'
LOCAL_BIN='{tmp_path / "bin"}'
PLUGIN_DIR='{plugin}'
PKG_SRC_DIR="$PLUGIN_DIR/src"
SCRIPT_DIR="$PLUGIN_DIR/scripts"
VERSIONED_RUNTIME=1
SRC_VERSION=1.0.0
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
VENV_PYTHON="$VENV_DIR/bin/python"
LINK_PYTHON="$VENV_PYTHON"
_resolve_zdd() {{ _resolve_vendored_lib zdd; }}
{functions}
_ensure_runtime
"""], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == "--version"
    assert sum(line.startswith("venv ") for line in calls) == 1
    installs = [line for line in calls if line.startswith("pip install")]
    assert len(installs) == 3
    assert "zdd" in installs[0] and "agent-procutil" in installs[1]
    assert installs[2].endswith(str(plugin))
    assert all("torch" not in line and "[store,server]" not in line for line in installs)
    assert not (tmp_path / "runtime/engine").exists()


def test_optional_server_package_failure_does_not_abort_primary_under_errexit(tmp_path: Path):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    server = tmp_path / "runtime/server"
    (server / "bin").mkdir(parents=True)
    (server / "pyvenv.cfg").write_text("home = fixture\n", encoding="utf-8")
    python = server / "bin/python3"
    python.write_text("#!/usr/bin/env bash\nexit 97\n", encoding="utf-8")
    python.chmod(0o755)
    uv = tmp_path / "uv"
    uv.write_text("#!/usr/bin/env bash\necho fixture-package-failure\nexit 37\n", encoding="utf-8")
    uv.chmod(0o755)
    env = environment(tmp_path)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    result = subprocess.run([bash, "-c", f"""
set -euo pipefail
_skip() {{ :; }}; _ok() {{ :; }}; _warn() {{ echo "$*" >&2; }}
new_signed_venv() {{ return 0; }}
_resolve_zdd() {{ return 1; }}
_uv_pip_install() {{ '{uv}' pip install "$@"; }}
VENV_DIR='{server.parent}'
PLUGIN_DIR='{tmp_path / "plugin"}'
{function("_install_server_venv", "sh")}
_install_server_venv host forbidden
echo primary-continues
"""], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "primary-continues" in result.stdout
    assert "fixture-package-failure" in result.stderr
    assert "Server venv package install failed" in result.stderr
