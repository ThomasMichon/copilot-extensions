"""Cross-version publication and supported snapshot provisioning contracts."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_install_engine_adoption import ENGINE, PLUGIN, environment, function, run_ps


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("boundary", ["stamp", "preparation"])
def test_posix_stamp_and_new_activation_share_publication_not_build_descriptor(tmp_path, fallback, boundary):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("0.9.0")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "versioned_runtime.py").write_text(
        "import pathlib, sys\n"
        "a=sys.argv\n"
        "if 'activate' in a:\n"
        " pathlib.Path(a[a.index('--root')+1], 'current-version').write_text(a[a.index('activate')+1])\n"
    )
    pkg = tmp_path / "new"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text('[project]\nversion = "2.0.0"\n')
    venv = root / "versions/2.0.0"
    (venv / "bin").mkdir(parents=True)
    py = venv / "bin/python"
    py.write_text(f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")
    py.chmod(0o755)
    definitions = "\n".join(function(name, "sh") for name in (
        "_with_index_advisory_lock", "_with_index_build_lock", "_with_index_publication_lock",
        "_index_publication_fresh", "_version_lt", "_bootstrap_python", "_find_python",
        "_versioned_activate", "deploy_binstub", "do_stamp", "_write_manifest",
        "_ensure_runtime", "_test_index_venv",
    ))
    prelude = f"""
set -uo pipefail
. '{ENGINE / "installer-engine.sh"}'
{definitions}
INSTALL_DIR='{root}'
LOCAL_BIN='{tmp_path / "bin"}'
STUB="$LOCAL_BIN/agent-index"
SCRIPT_DIR='{scripts}'
LINK_DIR='{tmp_path / "absent"}'
VENV_DIR='{venv}'
VERSIONED_RUNTIME=1
FORCE=0
export COPILOT_EXT_NO_FLOCK={1 if fallback else 0}
_ok() {{ :; }}; _skip() {{ :; }}; _step() {{ :; }}; _warn() {{ echo "$*" >&2; }}
_fail() {{ echo "$*" >&2; }}
_source_kind() {{ echo local; }}
_runtime_origin_under() {{ return 0; }}
"""
    checked, ready, attempted = (tmp_path / name for name in ("checked", "ready", "attempted"))
    worker = tmp_path / "new.sh"
    worker.write_text(prelude + f"""
SRC_VERSION=2.0.0
PLUGIN_DIR='{pkg}'
echo ready > '{ready}'
deadline=$((SECONDS + 10))
while [[ ! -f '{checked}' ]]; do ((SECONDS < deadline)) || exit 97; sleep 0.02; done
probe() {{ echo unserialized > '{attempted}'; }}
if ! _with_index_advisory_lock "$INSTALL_DIR/publication" 9 0 probe; then
    echo blocked > '{attempted}'
fi
publish() {{ _versioned_activate && deploy_binstub && _write_manifest; }}
_with_index_build_lock "$VENV_DIR" _with_index_publication_lock publish
""")
    if boundary == "preparation":
        (tmp_path / "old/src").mkdir(parents=True)
        entry = f"""
PKG_SRC_DIR="$PLUGIN_DIR/src"
VENV_DIR="$INSTALL_DIR/versions/1.0.0"
VENV_PYTHON="$VENV_DIR/bin/python"
_find_python() {{ echo '{sys.executable}'; }}
_ensure_uv() {{ return 0; }}
_ensure_uv_index() {{ :; }}
_new_index_venv() {{ return 1; }}
_versioned_slot_clean() {{ :; }}
_stop() {{ _version_lt 1.0.0 0.9.0; }}
_ensure_runtime
"""
    else:
        entry = "do_stamp"
    with subprocess.Popen([bash, str(worker)], env=environment(tmp_path),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        result = subprocess.run([bash, "-c", prelude + f"""
SRC_VERSION=1.0.0
PLUGIN_DIR='{tmp_path / "old"}'
deadline=$((SECONDS + 10))
while [[ ! -f '{ready}' ]]; do ((SECONDS < deadline)) || exit 97; sleep 0.02; done
_version_lt() {{
    if [[ "$1" == 1.0.0 && "$2" == 0.9.0 && ! -f '{checked}' ]]; then
        echo checked > '{checked}'
        while [[ ! -f '{attempted}' ]]; do ((SECONDS < deadline)) || exit 97; sleep 0.02; done
        [[ "$(cat '{attempted}')" == blocked ]] || exit 97
        # Another version's build descriptor must remain independently available.
        independent() {{ :; }}
        INDEX_BUILD_LOCK_TIMEOUT_SECONDS=0 _with_index_build_lock "$INSTALL_DIR/versions/3.0.0" independent || exit 97
        [[ "$(cat "$INSTALL_DIR/current-version")" == 0.9.0 ]] || exit 97
    fi
    [[ "$1" == 1.0.0 && "$2" == 2.0.0 ]]
}}
{entry}
"""], env=environment(tmp_path), capture_output=True, text=True, timeout=25)
        stdout, stderr = child.communicate(timeout=25)
    assert result.returncode == (1 if boundary == "preparation" else 0), result.stderr
    assert child.returncode == 0, stdout + stderr
    assert (root / "current-version").read_text() == "2.0.0"
    assert (root / "payload-dir").read_text().strip() == str(pkg)
    assert json.loads((root / "deploy-manifest.json").read_text())["source"]["version"] == "2.0.0"
    # Direct stale stamp/launcher callers may not overwrite the winning payload.
    result = subprocess.run([bash, "-c", prelude + f"""
SRC_VERSION=1.0.0
PLUGIN_DIR='{tmp_path / "old"}'
do_stamp
deploy_binstub
"""], env=environment(tmp_path), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert (root / "payload-dir").read_text().strip() == str(pkg)


@pytest.mark.parametrize("blocked", [False, True])
def test_missing_origin_supported_runtime_gate_keeps_legacy_authorization(tmp_path, blocked):
    from test_runtime_gate import _fixture, _run

    gate, env = _fixture(tmp_path, "powershell")
    env.update(environment(tmp_path))
    if not (shutil.which("pwsh") or shutil.which("powershell")):
        pytest.skip("PowerShell is unavailable")
    payload = gate.parent.parent
    manifest = json.loads((PLUGIN / "payload-invocation.json").read_text())
    manifest["installation"]["legacyFootprint"] = {"paths": [], "services": [], "tasks": []}
    (payload / "payload-invocation.json").write_text(json.dumps(manifest))
    context = gate.parent / "installation-context"
    shutil.copy2(PLUGIN / "scripts/installation-context/legacy-entrypoint-probe.ps1",
                 context / "legacy-entrypoint-probe.ps1")
    # The declaration/probe is real; governance is a bounded, negative-capable fixture.
    mode = context / "installation-context.ps1"
    mode.write_text(mode.read_text() + "\n", encoding="utf-8")
    original_mode = mode.read_text()
    mode.write_text(
        "if ($args[0] -eq 'probe-legacy') {\n"
        "$index=[Array]::IndexOf($args,'-LegacyProbeJson')\n"
        "$probe=$args[$index+1] | ConvertFrom-Json\n"
        "if (-not $probe.declared -or $probe.result -eq 'unknown') { exit 1 }\n"
        f"@{{ allowMutation=${str(not blocked).lower()}; probeReason='fixture-governance' }} | ConvertTo-Json -Compress\n"
        f"exit {3 if blocked else 0}\n"
        "}\n" + original_mode,
        encoding="utf-8",
    )
    root = Path(env["AGENT_INDEX_HOME"])
    root.mkdir()
    origin = tmp_path / "missing-origin"
    (root / "payload-origin").write_text(str(origin))
    env["TEST_PYTHON"] = ""
    env["AGENT_INDEX_REPO"] = str(tmp_path)
    for name in ("ps1", "sh"):
        shutil.copy2(ENGINE / f"installer-engine.{name}", gate.parent / f"installer-engine.{name}")
    installer = (PLUGIN / "scripts/install.ps1").read_text()
    # Retain the actual mandatory installer authorization prologue; replace only build effects.
    prologue = installer.split("# Status and dependency-light cell-slot actions", 1)[0]
    prologue = prologue.replace(
        r". (Join-Path $PSScriptRoot '..\..\..\libs\installer-engine\installer-engine.ps1')",
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')",
    )
    marker = tmp_path / "provision-reached"
    uv = tmp_path / ("uv.cmd" if os.name == "nt" else "uv")
    uv.write_text(
        f'@echo off\r\necho fake-uv>"{marker}"\r\nexit /b 0\r\n' if os.name == "nt" else
        f'#!/bin/sh\necho fake-uv > "{marker}"\nexit 0\n'
    )
    uv.chmod(0o755)
    (gate.parent / "install.ps1").write_text(prologue + f"""
function Start-Process {{ throw 'forbidden process activation' }}
function Register-ScheduledTask {{ throw 'forbidden task mutation' }}
function Restart-Service {{ throw 'forbidden service mutation' }}
if ($Action -ne 'provision') {{ throw 'unexpected action' }}
if ($env:COPILOT_PLUGIN_STAGED_FROM -ne '{origin}') {{ throw 'provenance lost' }}
& '{uv}' --version
exit 47
""")
    result = _run("pwsh", gate, env, "setup", "--single", "--yes", cwd=tmp_path)
    assert result.returncode == (3 if blocked else 47), result.stderr
    assert marker.exists() == (not blocked)
    assert (root / "payload-origin").read_text() == str(origin)


def test_rejected_discoverable_uv_uses_pip_for_cli_server_and_engine(tmp_path):
    from test_installer_publication_and_health import publication_fixture

    definitions, root = publication_fixture(tmp_path)
    package = tmp_path / "package"
    (package / "src").mkdir(parents=True)
    broken = tmp_path / ("broken-uv.cmd" if os.name == "nt" else "broken-uv")
    calls = tmp_path / "uv-calls"
    broken.write_text(
        f'@echo off\r\necho %*>>"{calls}"\r\nexit /b 47\r\n' if os.name == "nt" else
        f'#!/bin/sh\necho "$*" >> "{calls}"\nexit 47\n'
    )
    broken.chmod(0o755)
    server = function("Install-ServerVenv", "ps1").replace(
        "Join-Path $serverVenvDir 'Scripts\\python.exe'", "'fixture_python'",
    )
    result = run_ps(tmp_path, definitions + function("Install-Runtime", "ps1") +
                    server + function("Install-Engine", "ps1") + f"""
$PkgSrcDir = '{package / "src"}'
$PluginDir = '{package}'
$LocalBin = '{tmp_path / "bin"}'
$VersionedRuntime = $false
$VenvPython = 'fixture_python'; $LinkPython = $VenvPython; $LinkDir = $VenvDir
$EngineHome = '{tmp_path / "engine"}'
$EngineVenv = Join-Path $EngineHome 'venv'
$EngineVenvPython = 'fixture_python'
$realEnsure = ${{function:Ensure-Uv}}
function Get-Command {{
    [CmdletBinding()]param($Name, $CommandType)
    if ($Name -eq 'uv') {{ return [pscustomobject]@{{ Source='{broken}' }} }}
    if ($Name -in @('python','python3','py')) {{ return [pscustomobject]@{{ Source='{sys.executable}' }} }}
    throw "unexpected executable lookup: $Name"
}}
function Ensure-Uv {{
    param($InstallRoot)
    return (& $realEnsure -InstallRoot $InstallRoot -AcquireIfMissing $false)
}}
function fixture_python {{
    if ($args.Count -ge 2 -and $args[0] -eq '-m' -and $args[1] -eq 'pip') {{
        Add-Content '{tmp_path / "pip-calls"}' ($args -join ' ')
    }}
    $global:LASTEXITCODE = 0
}}
function Invoke-IndexUvPipInstall {{ throw 'rejected uv was rediscovered for packages' }}
function New-IndexVenv {{ param($Dir,$Python,$PythonCmd,$PreferSignedPython) return $true }}
function Test-IndexVenv {{ param($Dir,$Python) return $false }}
function Resolve-Zdd {{ return '{package}' }}
function Resolve-VendoredLib {{ param($LibName) return '{package}' }}
function Get-ActivationRole {{ return 'client' }}
function Get-SignedBasePython {{ return $null }}
function Invoke-VersionedSlotClean {{ }}
function Remove-ConsoleTrampolines {{ param($VenvDir) }}
Install-Runtime
if ($script:UvCommand) {{ throw 'broken uv was accepted' }}
Install-ServerVenv -InstallRole host -PythonCmd fixture_python
if (-not (Install-Engine -Upgrade)) {{ throw 'engine pip fallback failed' }}
""")
    assert result.returncode == 0, result.stderr
    assert len((tmp_path / "pip-calls").read_text().splitlines()) == 9
    assert calls.read_text().splitlines() == ["--version", "--version"]
