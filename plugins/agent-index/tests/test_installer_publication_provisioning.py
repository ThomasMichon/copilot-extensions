"""Cross-version publication and supported snapshot provisioning contracts."""
from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_install_engine_adoption import (
    ENGINE, PLUGIN, REPO, construction_bundle, environment, function, run_ps,
)


def stage_index_policy_sources(repository: Path, *, materialized: bool) -> Path:
    """Publish the real owner metadata and its complete local dependency closure."""
    payload = repository / "plugins/agent-index"
    for relative in (
        "plugin.json", "pyproject.toml",
        "scripts/installer_dependency_policy.py", "scripts/installer_dependency_config.py",
    ):
        destination = payload / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PLUGIN / relative, destination)
    policy = runpy.run_path(str(payload / "scripts/installer_dependency_policy.py"))
    pending = [(PLUGIN, payload)]
    seen = set()
    while pending:
        source, destination = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        project = policy["_toml"](source / "pyproject.toml")
        for declarations in project.get("tool", {}).get("uv", {}).get("sources", {}).values():
            for declaration in declarations if isinstance(declarations, list) else [declarations]:
                if "path" not in declaration:
                    continue
                library = (source / declaration["path"]).resolve()
                target = (destination / declaration["path"]).resolve()
                assert library.is_relative_to(REPO.resolve())
                assert target.is_relative_to(repository.resolve())
                shutil.copytree(
                    library, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", ".pytest_cache", "build", "dist", "*.egg-info",
                    ),
                )
                pending.append((library, target))
    if materialized:
        previous_path = sys.path[:]
        try:
            release = runpy.run_path(str(REPO / "tools/materialize_main.py"))
            report = release["materialize_uv_editable_ref_into"](
                source_consumer_dir=PLUGIN, dest_consumer_dir=payload, canonical_root=REPO,
            )
        finally:
            sys.path[:] = previous_path
        assert not any(message.startswith("SKIP ") for message in report), report
    roster = json.loads((REPO / ".github/plugin/marketplace.json").read_text(encoding="utf-8"))
    entry = next(entry for entry in roster["plugins"] if entry["name"] == "agent-index")
    publication = repository / ".github/plugin/marketplace.json"
    publication.parent.mkdir(parents=True)
    publication.write_text(
        json.dumps({"name": roster["name"], "plugins": [entry]}), encoding="utf-8",
    )
    roots = policy["_anchor_roots"]()
    assert payload.resolve() in roots
    policy["_check_project"](payload, roots, set())
    return payload


def engine_construction_fixture(tmp_path):
    """Keep acquisition fixtures attached to the canonical construction protocol."""
    for source in (REPO / "libs/versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copy2(source, tmp_path / source.name)
    for name in ("installer_dependency_policy.py", "installer_dependency_config.py"):
        shutil.copy2(PLUGIN / "scripts" / name, tmp_path / name)
    return f"""
function Get-BootstrapPython {{ '{sys._base_executable}' }}
function Get-PayloadHash {{ 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }}
function New-IndexVenv {{
    param($Dir,$Python,$PythonCmd,$PreferSignedPython)
    if ($Python -eq 'fixture_python') {{ return $true }}
    & '{sys._base_executable}' -I -m venv --without-pip $Dir
    if ($LASTEXITCODE -ne 0) {{ throw 'Fixture venv creation failed' }}
    & '{sys._base_executable}' -I -c 'import os, pathlib, sys; root = pathlib.Path(sys.argv[1]); expected = pathlib.Path(sys.argv[2]); expected.parent.mkdir(exist_ok=True); expected.symlink_to(root / "bin/python") if os.name != "nt" and not expected.exists() else None' $Dir $Python
    if ($LASTEXITCODE -ne 0) {{ throw 'Fixture interpreter layout failed' }}
    $site = & $Python -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))'
    if ($LASTEXITCODE -ne 0) {{ throw 'Fixture site discovery failed' }}
    Set-Content (Join-Path $site 'torch.py') ''
    Set-Content (Join-Path $site 'agent_index.py') ''
    $app = Join-Path $site 'agent_index_engine'
    New-Item -ItemType Directory $app | Out-Null
    Set-Content (Join-Path $app '__init__.py') ''
    Set-Content (Join-Path $app 'app.py') ''
    return $true
}}
"""


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("boundary", ["stamp", "preparation"])
def test_posix_stamp_and_new_activation_share_publication_not_build_descriptor(tmp_path, fallback, boundary):
    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("0.9.0")
    (root / "versions/0.9.0").mkdir(parents=True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("session-stamp.py", "stamp_paths.py", "stamp_repair.py"):
        shutil.copy2(PLUGIN / "scripts" / name, scripts / name)
    construction_bundle(scripts)
    pkg = tmp_path / "new"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text('[project]\nversion = "2.0.0"\n')
    venv = root / "versions/2.0.0"
    definitions = "\n".join(function(name, "sh") for name in (
        "_with_index_advisory_lock", "_with_index_build_lock", "_with_index_publication_lock",
        "_index_publication_fresh", "_version_lt", "_bootstrap_python", "_find_python",
        "_versioned_activate", "_write_index_stamp_file", "deploy_binstub", "do_stamp", "_write_manifest",
        "_ensure_runtime", "_test_index_venv", "_versioned_slot_clean", "_versioned_mark_complete",
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
_bootstrap_python() {{ echo '{sys._base_executable}'; }}
_payload_hash() {{ printf '%s' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; }}
"""
    checked, ready, attempted = (tmp_path / name for name in ("checked", "ready", "attempted"))
    worker = tmp_path / "new.sh"
    worker.write_text(prelude + f"""
SRC_VERSION=2.0.0
PLUGIN_DIR='{pkg}'
VENV_PYTHON="$VENV_DIR/bin/python"
_versioned_slot_clean || exit 97
'{sys._base_executable}' -I -m venv --without-pip "$VENV_DIR" || exit 97
_versioned_mark_complete || exit 97
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
    assert (root / "current-version").read_bytes() == b"2.0.0\n"
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


def test_rejected_discoverable_uv_refuses_unseasoned_cli_server_and_engine(tmp_path):
    from test_installer_publication_and_health import publication_fixture

    definitions, root = publication_fixture(tmp_path)
    package = stage_index_policy_sources(tmp_path / "source-repository", materialized=True)
    (package / "src").mkdir()
    construction_bundle(package / "scripts")
    definitions = definitions.replace(
        f"$PSScriptRoot = '{tmp_path}'", f"$PSScriptRoot = '{package / 'scripts'}'",
    )
    broken = tmp_path / ("uv.cmd" if os.name == "nt" else "uv")
    calls = tmp_path / "uv-calls"
    broken.write_text(
        f'@echo off\r\necho %*>>"{calls}"\r\nexit /b 47\r\n' if os.name == "nt" else
        f'#!/bin/sh\necho "$*" >> "{calls}"\nexit 47\n'
    )
    broken.chmod(0o755)
    construction = engine_construction_fixture(tmp_path)
    server = function("Install-ServerVenv", "ps1").replace(
        "Join-Path $serverVenvDir 'Scripts\\python.exe'", "'fixture_python'",
    )
    result = run_ps(package / "scripts", definitions + function("Install-Runtime", "ps1") +
                    server + function("Install-Engine", "ps1") + f"""
$PkgSrcDir = '{package / "src"}'
$PluginDir = '{package}'
$LocalBin = '{tmp_path / "bin"}'
$VersionedRuntime = $false
$VenvPython = 'fixture_python'; $LinkPython = $VenvPython; $LinkDir = $VenvDir
$EngineHome = '{tmp_path / "engine"}'
$EngineVenv = Join-Path $EngineHome 'venv'
$EngineVenvPython = 'fixture_python'
{construction}
$PSScriptRoot = '{package / "scripts"}'
# No native bare uv may escape to the host after discovery rejected the shim.
$env:PATH = '{tmp_path}'
$realEnsure = ${{function:Ensure-Uv}}
function Get-Command {{
    [CmdletBinding()]param($Name, $CommandType)
    if ($Name -eq 'uv') {{ return [pscustomobject]@{{ Source='{broken}' }} }}
    if ($Name -in @('python','python3','py')) {{ return [pscustomobject]@{{ Source='{sys.executable}' }} }}
    return Microsoft.PowerShell.Core\\Get-Command $Name -ErrorAction SilentlyContinue
}}
function Ensure-Uv {{
    param($InstallRoot)
    return (& $realEnsure -InstallRoot $InstallRoot -AcquireIfMissing $false)
}}
function fixture_python {{
    if ($args[0] -ne '-I' -or $args[1] -ne (Join-Path $PSScriptRoot 'installer_dependency_policy.py')) {{
        throw 'unrestricted package installation attempted'
    }}
    $policyArgs = @($args)
    # PowerShell consumes -- at a function boundary; restore the native argv.
    if ($policyArgs[4] -ne '--') {{
        $policyArgs = @($policyArgs[0..3]) + @('--') + @($policyArgs[4..($policyArgs.Count - 1)])
    }}
    Add-Content '{tmp_path / "policy-calls"}' ($policyArgs -join ' ')
    $policyArgs[3] = '{broken}'
    & '{sys._base_executable}' @policyArgs
}}
function Invoke-IndexUvPipInstall {{ throw 'rejected uv was rediscovered for packages' }}
function Test-IndexVenv {{ param($Dir,$Python) return $false }}
function Resolve-Zdd {{ return '{package}' }}
function Resolve-VendoredLib {{ param($LibName) return '{package}' }}
function Get-ActivationRole {{ return 'client' }}
function Get-SignedBasePython {{ return $null }}
function Invoke-VersionedSlotClean {{ }}
function Remove-ConsoleTrampolines {{ param($VenvDir) }}
try {{ Install-Runtime; throw 'CLI accepted rejected uv' }} catch {{
    if ($_ -notmatch 'zdd install failed') {{ throw }}
}}
if ($script:UvCommand) {{ throw 'broken uv was accepted' }}
if (Install-ServerVenv -InstallRole host -PythonCmd fixture_python) {{ throw 'server accepted rejected uv' }}
$engineFailed = $false
try {{ $engineFailed = -not (Install-Engine -Upgrade) }} catch {{
    if ($_ -notmatch 'construction aborted') {{ throw }}
    $engineFailed = $true
}}
if (-not $engineFailed) {{ throw 'engine accepted rejected uv' }}
if (Test-Path (Join-Path $EngineHome 'current-version')) {{ throw 'failed engine was selected' }}
if (@(Get-ChildItem (Join-Path $EngineHome 'versions') -Directory).Count) {{ throw 'failed candidate was retained' }}
""")
    assert result.returncode == 0, result.stderr
    policy_calls = (tmp_path / "policy-calls").read_text().splitlines()
    assert len(policy_calls) == 2
    assert all("installer_dependency_policy.py --uv uv -- --python" in call for call in policy_calls)
    uv_calls = calls.read_text().splitlines()
    assert uv_calls.count("--version") == 2
    settings_calls = [call for call in uv_calls if "--show-settings" in call]
    assert len(settings_calls) >= len(policy_calls)
    assert all("pip install" in call for call in settings_calls)
    for call in policy_calls:
        target = call.split("--python ", 1)[1].split(" ", 1)[0]
        assert any(f"--python {target}" in probe for probe in settings_calls)
    assert all(call == "--version" or "--show-settings" in call for call in uv_calls)
    assert "dependency-seasoning: UV seasoning configuration probe failed" in result.stdout
