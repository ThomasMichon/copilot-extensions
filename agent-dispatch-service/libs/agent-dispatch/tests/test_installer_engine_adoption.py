"""Dispatch installer composition, offline retries, health and snapshot contracts."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
ENGINE = REPO / "libs" / "installer-engine"
PWSH = shutil.which("pwsh") or shutil.which("powershell")
LIBRARIES = {
    "agent-zdd": "zdd",
    "agent-procutil": "agent-procutil",
    "agent-dropin-registry": "dropin-registry",
    "agent-plugin-resolve": "plugin-resolve",
    "agent-single-instance-lease": "single-instance-lease",
    "agent-plugin-activation": "plugin-activation",
}


def _function(ext: str, name: str) -> str:
    text = (PLUGIN / "scripts" / f"install.{ext}").read_text(encoding="utf-8")
    pattern = (
        rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}"
        if ext == "sh"
        else rf"(?ms)^function {re.escape(name)} \{{.*?^\}}"
    )
    match = re.search(pattern, text)
    assert match, name
    return match.group(0) + "\n"


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith(("AGENT_", "COPILOT_PLUGIN_", "PYTHON", "UV_")):
            env.pop(key)
    for key, suffix in {
        "HOME": "home",
        "USERPROFILE": "home",
        "APPDATA": "roaming",
        "LOCALAPPDATA": "local",
        "PROGRAMDATA": "program",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state",
        "XDG_RUNTIME_DIR": "run",
        "TEMP": "tmp",
        "TMP": "tmp",
        "TMPDIR": "tmp",
        "COPILOT_HOME": "copilot",
    }.items():
        root = tmp_path / suffix
        root.mkdir(exist_ok=True)
        env[key] = str(root)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    if os.name != "nt":
        for name in (
            "grep",
            "dirname",
            "sed",
            "head",
            "date",
            "hostname",
            "uname",
            "tr",
            "mv",
            "cat",
            "git",
        ):
            command = shutil.which(name)
            assert command, name
            (fake_bin / name).symlink_to(command)
    env["PATH"] = str(fake_bin)
    env["OS"] = "Installer_Test"
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    env["TEST_UV_LOG"] = str(tmp_path / "uv.jsonl")
    return env


def _run(ext: str, script: str, tmp_path: Path, env: dict[str, str]):
    if ext == "ps1" and PWSH is None:
        pytest.skip("PowerShell unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    file = tmp_path / f"harness.{ext}"
    file.write_text(script, encoding="utf-8")
    command = ["/bin/bash", str(file)] if ext == "sh" else [PWSH, "-NoProfile", "-File", str(file)]
    return subprocess.run(
        command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30
    )


def _prelude(ext: str, engine: Path = ENGINE) -> str:
    if ext == "sh":
        return (
            "set -euo pipefail\n"
            '_warn() { echo "$*" >&2; }\n_fail() { echo "$*" >&2; }\n'
            "_ok() { :; }\n_step() { :; }\nsleep() { :; }\n"
            f'. "{engine / "installer-engine.sh"}"\n'
        )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Warn { param($Msg) Write-Host $Msg }\n"
        "function Write-Fail { param($Msg) throw $Msg }\n"
        "function Write-Ok { param($Msg) }\nfunction Write-Step { param($Msg) }\n"
        "function Write-Skip { param($Msg) }\nfunction Start-Sleep { param($Seconds) }\n"
        "function Start-Process { throw 'forbidden process launch' }\n"
        "function Register-ScheduledTask { throw 'forbidden scheduled task' }\n"
        "function Stop-Process { throw 'forbidden process termination' }\n"
        f". '{engine / 'installer-engine.ps1'}'\n"
    )


def _fake_uv(tmp_path: Path) -> Path:
    driver = tmp_path / "uv-driver.py"
    driver.write_text(
        """import json, os, sys
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ["TEST_UV_LOG"])
history = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args == ["--version"]:
    print("uv fixture")
    raise SystemExit(0)
count = sum(item == args for item in history)
mode = os.environ.get("TEST_UV_MODE", "ok")
if mode == "fatal":
    print("permanent failure")
    raise SystemExit(23)
if args[0] == "venv":
    root = Path(args[1])
    root.mkdir(parents=True, exist_ok=True)
    (root / "Scripts").mkdir(exist_ok=True)
    (root / "Scripts/python.exe").touch()
    if mode == "missing-cfg" and count < 2:
        raise SystemExit(0)
    if mode == "corrupt" and count < 2:
        print("failed to locate pyvenv.cfg; exit code: 106")
        raise SystemExit(106)
    if mode == "sre" and count < 2:
        print("SRE module mismatch")
        raise SystemExit(1)
    (root / "pyvenv.cfg").write_text("home = fixture")
    raise SystemExit(0)
if args[:2] == ["pip", "install"]:
    if mode == "sre" and count < 2:
        print("SRE module mismatch")
        raise SystemExit(1)
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    uv = tmp_path / "fake-bin" / ("uv.cmd" if os.name == "nt" else "uv")
    uv.write_text(
        f'@"{sys.executable}" "{driver}" %*\n@exit /b %ERRORLEVEL%\n'
        if os.name == "nt"
        else f'#!/bin/sh\nexec "{sys.executable}" "{driver}" "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    return uv


@pytest.mark.guard
def test_dispatch_composes_canonical_helpers_without_local_engine_copies():
    sh = (PLUGIN / "scripts/install.sh").read_text(encoding="utf-8")
    ps1 = (PLUGIN / "scripts/install.ps1").read_text(encoding="utf-8")
    for ext, text in (("sh", sh), ("ps1", ps1)):
        assert not (PLUGIN / "scripts" / f"installer-engine.{ext}").exists()
        assert f"installer-engine.{ext}" in text
    assert 'invoke_uv_pip_install_resilient "$UV_CMD"' in sh
    assert 'invoke_uv_venv_resilient "$UV_CMD"' in sh
    assert "Invoke-UvPipInstallResilient -UvCommand $UvCommand" in ps1
    assert "Invoke-UvVenvResilient -VenvDir $VenvDir" in ps1
    assert 'write_deploy_manifest "agent-dispatch"' in sh
    assert "Write-DeployManifest -Service 'agent-dispatch'" in ps1


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("mode", ["sre", "missing-cfg", "corrupt", "fatal"])
def test_shared_venv_retry_is_offline_and_preserves_failure(tmp_path, ext, mode):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["TEST_UV_MODE"] = mode
    slot = tmp_path / "slot"
    if ext == "sh":
        call = f"""
if invoke_uv_venv_resilient '{uv}' '{slot}' --allow-existing; then exit 0; else exit $?; fi
"""
    else:
        call = f"""
$result = Invoke-UvVenvResilient -VenvDir '{slot}' -Arguments @('--allow-existing') -UvCommand '{uv}'
if ($result.ExitCode -eq 0 -and -not (Test-Path '{slot}/pyvenv.cfg')) {{ exit 1 }}
exit $result.ExitCode
"""
    result = _run(ext, _prelude(ext) + call, tmp_path, env)
    assert result.returncode == (23 if mode == "fatal" else 0), result.stdout + result.stderr
    calls = [json.loads(line) for line in Path(env["TEST_UV_LOG"]).read_text().splitlines()]
    assert len(calls) == (1 if mode == "fatal" else 3)
    assert all(call == ["venv", str(slot), "--allow-existing"] for call in calls)


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("mode", ["sre", "fatal"])
def test_package_retry_keeps_optional_extra_and_exact_exit_code(tmp_path, ext, mode):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["TEST_UV_MODE"] = mode
    spec = str(tmp_path / "plugin") + "[mcp]"
    args = ["--python", "fixture-python", "--reinstall-package", "agent-dispatch", spec]
    if ext == "sh":
        call = f"""
if invoke_uv_pip_install_resilient '{uv}' --python fixture-python --reinstall-package agent-dispatch '{spec}'; then exit 0; else exit $?; fi
"""
    else:
        call = f"""
$result = Invoke-UvPipInstallResilient -UvCommand '{uv}' -Arguments @('--python', 'fixture-python', '--reinstall-package', 'agent-dispatch', '{spec}')
exit $result.ExitCode
"""
    result = _run(ext, _prelude(ext) + call, tmp_path, env)
    assert result.returncode == (23 if mode == "fatal" else 0), result.stdout + result.stderr
    calls = [json.loads(line) for line in Path(env["TEST_UV_LOG"]).read_text().splitlines()]
    assert calls == [["pip", "install", *args]] * (1 if mode == "fatal" else 3)


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_shared_acquisition_selects_fixture_and_never_bootstraps(tmp_path, ext):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "_ensure_uv")
            + f"""
INSTALL_DIR='{tmp_path / "runtime"}'
_ensure_uv
[[ "$UV_CMD" == '{uv}' ]]
"""
        )
    else:
        script = (
            _prelude(ext)
            + f"""
$uv = Ensure-Uv -InstallRoot '{tmp_path / "runtime"}' -AcquireIfMissing $false
if ($uv -ne '{uv}') {{ throw 'fixture uv not selected' }}
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [json.loads(line) for line in Path(env["TEST_UV_LOG"]).read_text().splitlines()] == [
        ["--version"]
    ]
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("signed_exit,usable", [(0, True), (7, True), (0, False), (7, False)])
@pytest.mark.parametrize("existing_invalid", ["none", "missing-cfg", "wrong-prefix"])
def test_signed_python_result_is_accepted_only_when_healthy(
    tmp_path, signed_exit, usable, existing_invalid
):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    slot = tmp_path / "slot"
    if existing_invalid != "none":
        (slot / "Scripts").mkdir(parents=True)
        (slot / "Scripts/python.exe").write_text("invalid existing Python")
        if existing_invalid == "wrong-prefix":
            (slot / "pyvenv.cfg").write_text("wrong prefix")
    signed = tmp_path / "signed-python.ps1"
    signed.write_text(
        f"""
$Dir = $args[-1]
New-Item -ItemType Directory -Path (Join-Path $Dir 'Scripts') -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $Dir 'Scripts/python.exe'), 'fixture')
if ('{usable}' -eq 'True') {{ [IO.File]::WriteAllText((Join-Path $Dir 'pyvenv.cfg'), 'fixture') }}
exit {signed_exit}
""",
        encoding="utf-8",
    )
    text = (PLUGIN / "scripts/install.ps1").read_text()
    block = text.split("    # -- venv (SAC-trusted", 1)[1].split("    # -- install package", 1)[0]
    block = "    # -- venv (SAC-trusted" + block
    script = (
        _prelude("ps1")
        + f"""
$env:OS = 'Windows_NT'
$VenvDir='{slot}'
$VenvPython=Join-Path $VenvDir 'Scripts/python.exe'
$UvCommand='{uv}'
$pythonCmd='{tmp_path / "forbidden-python.ps1"}'
function py {{ $global:LASTEXITCODE=0; '{signed}' }}
function Get-AuthenticodeSignature {{ param($Path) return @{{ Status='Valid' }} }}
function Invoke-VersionedSlotClean {{}}
function Test-DispatchVenv {{
    param($Dir, $Python)
    if ((Test-Path -LiteralPath $Python) -and [IO.File]::ReadAllText($Python) -eq 'invalid existing Python') {{ return $false }}
    $cfg = Join-Path $Dir 'pyvenv.cfg'
    return ((Test-Path -LiteralPath $Python) -and (Test-Path -LiteralPath $cfg) -and [IO.File]::ReadAllText($cfg) -ne 'wrong prefix')
}}
{block}
if ($ErrorActionPreference -ne 'Stop') {{ throw 'error preference leaked' }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    log = Path(env["TEST_UV_LOG"])
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert calls == ([] if usable else [["venv", str(slot), "--allow-existing"]])
    assert (slot / "pyvenv.cfg").exists()


@pytest.mark.parametrize(
    "probe,code,healthy",
    [
        ("matching", 0, True),
        ("wrong-prefix", 0, False),
        ("base-python", 0, False),
        ("matching", 23, False),
        ("missing-cfg", 0, False),
    ],
)
def test_dispatch_venv_health_checks_prefix_cfg_and_probe_exit(tmp_path, probe, code, healthy):
    env = _env(tmp_path)
    slot = tmp_path / "slot"
    slot.mkdir()
    python = slot / "python-fixture"
    python.touch()
    if probe != "missing-cfg":
        (slot / "pyvenv.cfg").touch()
    prefix = tmp_path / "wrong" if probe == "wrong-prefix" else slot
    flag = "0" if probe == "base-python" else "1"
    script = (
        _prelude("ps1")
        + _function("ps1", "Test-DispatchVenv")
        + f"""
function Invoke-NativeCapture {{
    param($Command)
    return [pscustomobject]@{{ ExitCode={code}; Output="{prefix}`n{flag}" }}
}}
$result=Test-DispatchVenv -Dir '{slot}' -Python '{python}'
if ($result -ne ${str(healthy).lower()}) {{ throw 'unexpected health outcome' }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_manifest_preserves_dispatch_schema_and_marketplace_origin(tmp_path, ext):
    env = _env(tmp_path)
    origin = tmp_path / ".copilot/installed-plugins/example/agent-dispatch"
    env["COPILOT_PLUGIN_STAGED_FROM"] = str(origin)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "_write_manifest")
            + f"""
INSTALL_DIR='{runtime}'
PLUGIN_DIR='{payload}'
VENV_DIR='{runtime / "versions/1.2.3"}'
SRC_VERSION=1.2.3
_source_kind() {{ echo marketplace; }}
_write_manifest
"""
        )
    else:
        script = (
            _prelude(ext)
            + _function(ext, "Write-Manifest")
            + f"""
$InstallDir='{runtime}'
$PluginDir='{payload}'
$LinkDir='{runtime / "versions/1.2.3"}'
$SrcVersion='1.2.3'
function Get-SourceKind {{ param($PluginPath) return 'marketplace' }}
function Get-GitInfo {{ throw 'marketplace must not query git' }}
Write-Manifest
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((runtime / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["schema_version"] == 3
    assert manifest["service"] == "agent-dispatch"
    assert manifest["runtime"] == "python"
    assert manifest["venv"] == str(runtime / "versions/1.2.3").replace("\\", "/")
    assert manifest["source"] == {
        "kind": "marketplace",
        "path": str(origin).replace("\\", "/"),
        "repo": "copilot-extensions",
        "plugin": "agent-dispatch",
        "version": "1.2.3",
        "commit": None,
        "branch": None,
        "dirty": False,
    }
    assert not (runtime / "deploy-manifest.json.tmp").exists()


def test_snapshot_hash_preserves_case_distinct_paths(tmp_path):
    env = _env(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "A.py").write_text("upper")
    (snapshot / "a.py").write_text("lower")
    if len(list(snapshot.iterdir())) != 2:
        pytest.skip("case-insensitive filesystem")
    script = (
        _prelude("ps1")
        + _function("ps1", "Get-DispatchSnapshotHash")
        + f"""
$first = Get-DispatchSnapshotHash -SnapshotDir '{snapshot}'
[IO.File]::WriteAllText('{snapshot / "A.py"}', 'upper changed')
$second = Get-DispatchSnapshotHash -SnapshotDir '{snapshot}'
if ($first -eq $second) {{ throw 'case-distinct upper path disappeared from hash' }}
[IO.File]::WriteAllText('{snapshot / "a.py"}', 'lower changed')
$third = Get-DispatchSnapshotHash -SnapshotDir '{snapshot}'
if ($second -eq $third) {{ throw 'case-distinct lower path disappeared from hash' }}
if ($third -ne (Get-DispatchSnapshotHash -SnapshotDir '{snapshot}')) {{ throw 'hash is not stable' }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr


def _fixture_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "authoring"
    plugin = root / "plugins/agent-dispatch"
    shutil.copytree(PLUGIN / "scripts", plugin / "scripts")
    shutil.copyfile(PLUGIN / "pyproject.toml", plugin / "pyproject.toml")
    shutil.copytree(ENGINE, root / "libs/installer-engine")
    for package, lib in LIBRARIES.items():
        source = root / "libs" / lib
        source.mkdir(parents=True)
        project = f'[project]\nname = "{package}"\nversion = "1.0.0"\n'
        if lib == "plugin-activation":
            project += '[tool.uv.sources]\nagent-dropin-registry = { path = "../dropin-registry", editable = true }\n'
        (source / "pyproject.toml").write_text(project, encoding="utf-8")
    return plugin


def _snapshot_prelude() -> str:
    script = _prelude("ps1")
    for name in (
        "Resolve-VendoredLib",
        "Materialize-DispatchSnapshot",
        "Get-DispatchSnapshotHash",
        "Get-SourceKind",
        "Enter-PluginSnapshotLock",
        "New-PluginBuildSnapshot",
        "Publish-FileAtomically",
        "Test-VersionLt",
        "Get-VerTuple",
        "Invoke-Stamp",
    ):
        script += _function("ps1", name)
    return script


def test_delayed_stamp_cannot_publish_over_newer_same_version_content(tmp_path):
    env = _env(tmp_path)
    plugin = _fixture_checkout(tmp_path)
    runtime = tmp_path / "runtime"
    script = _snapshot_prelude()
    script += _function("ps1", "Enter-PluginSnapshotLock").replace(
        "function Enter-PluginSnapshotLock", "function Enter-RealSnapshotLock", 1
    )
    script += f"""
$PluginDir='{plugin}'
$InstallDir='{runtime}'
$LocalBin='{tmp_path / "local-bin"}'
$SrcVersion='1.2.3'
$Force=$false
$script:GlobalActivationLockTimeoutSeconds=10
$script:interleaved=$false
$script:deployed=0
function Deploy-SelfProvisioningBinstub {{ $script:deployed++ }}
function Enter-PluginSnapshotLock {{
    param($InstallDir, $Version, $TimeoutSeconds=20)
    if (-not $Version -and -not $script:interleaved) {{
        $script:interleaved=$true
        [IO.File]::WriteAllText((Join-Path $PluginDir 'new-source.py'), 'newer content')
        Invoke-Stamp
    }}
    return Enter-RealSnapshotLock -InstallDir $InstallDir -Version $Version -TimeoutSeconds $TimeoutSeconds
}}
Invoke-Stamp
$published = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
if (-not (Test-Path (Join-Path $published 'new-source.py'))) {{ throw 'delayed stamp overwrote newer content' }}
if ($script:deployed -ne 1) {{ throw 'stale stamp deployed its launchers' }}
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'stamp-candidate-1.2.3')) -cne $published) {{ throw 'candidate and published identity diverged' }}
"""
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_stamp_materializes_standalone_engine_pair_and_all_libraries(tmp_path):
    env = _env(tmp_path)
    plugin = _fixture_checkout(tmp_path)
    runtime = tmp_path / "runtime"
    script = _snapshot_prelude()
    script += f"""
$PluginDir='{plugin}'
$InstallDir='{runtime}'
$LocalBin='{tmp_path / "local-bin"}'
$SrcVersion='1.2.3'
$Force=$false
$script:GlobalActivationLockTimeoutSeconds=10
function Deploy-SelfProvisioningBinstub {{ [IO.File]::WriteAllText((Join-Path $LocalBin 'stamp-proof'), 'deployed') }}
Invoke-Stamp
$first = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
Invoke-Stamp
$second = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
if ($first -ne $second) {{ throw 'identical local stamps did not reuse their snapshot' }}
if (@(Get-ChildItem (Join-Path $InstallDir 'snapshots') -Directory).Count -ne 1) {{ throw 'identical stamps leaked snapshots' }}
[IO.File]::WriteAllText((Join-Path $PluginDir 'new-source.py'), 'changed plugin source')
Invoke-Stamp
$third = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
if ($third -eq $second) {{ throw 'plugin edit did not change snapshot identity' }}
[IO.File]::AppendAllText((Join-Path $PluginDir '../../libs/installer-engine/installer-engine.sh'), "`n# fixture edit")
Invoke-Stamp
$fourth = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
if ($fourth -eq $third) {{ throw 'engine edit did not change snapshot identity' }}
[IO.File]::WriteAllText((Join-Path $PluginDir '../../libs/agent-procutil/new-lib.py'), 'changed library source')
Invoke-Stamp
$fifth = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))
if ($fifth -eq $fourth) {{ throw 'library edit did not change snapshot identity' }}
if (@(Get-ChildItem (Join-Path $InstallDir 'snapshots') -Directory).Count -ne 4) {{ throw 'unexpected snapshot count' }}
"""
    result = _run("ps1", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    snapshots = sorted((runtime / "snapshots").iterdir())
    snapshot = next(path for path in snapshots if not (path / "new-source.py").exists())
    assert snapshot.parent == runtime / "snapshots"
    assert (runtime / "stamped-version").read_text() == "1.2.3"
    assert not (runtime / "current-version").exists()
    assert len(snapshots) == 4
    for ext in ("sh", "ps1"):
        assert (snapshot / "scripts" / f"installer-engine.{ext}").read_bytes() == (
            ENGINE / f"installer-engine.{ext}"
        ).read_bytes()
        text = (snapshot / "scripts" / f"install.{ext}").read_text()
        if ext == "sh":
            assert '. "$SCRIPT_DIR/installer-engine.sh"' in text.splitlines()
            assert (
                '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"'
                not in text.splitlines()
            )
        else:
            assert ". (Join-Path $PSScriptRoot 'installer-engine.ps1')" in text.splitlines()
            assert (
                ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')"
                not in text.splitlines()
            )
    project = tomllib.loads((snapshot / "pyproject.toml").read_text())
    assert project["tool"]["uv"]["sources"] == {
        package: {"path": f"libs/{lib}"} for package, lib in LIBRARIES.items()
    }
    for lib in LIBRARIES.values():
        assert (snapshot / "libs" / lib / "pyproject.toml").is_file()
    nested = tomllib.loads((snapshot / "libs/plugin-activation/pyproject.toml").read_text())
    assert nested["tool"]["uv"]["sources"]["agent-dropin-registry"] == {
        "path": "../dropin-registry"
    }
    shutil.move(str(plugin.parents[1]), str(tmp_path / "authoring-unavailable"))
    uv = _fake_uv(tmp_path)
    proof = _run(
        "ps1",
        _prelude("ps1", snapshot / "scripts")
        + f"""
$result = Invoke-UvVenvResilient -VenvDir '{tmp_path / "slot"}' -Arguments @('--allow-existing') -UvCommand '{uv}'
exit $result.ExitCode
""",
        tmp_path,
        env,
    )
    assert proof.returncode == 0, proof.stdout + proof.stderr
