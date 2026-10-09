"""Codespaces engine wiring, retry behavior, and standalone payload regressions."""

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
PWSH = shutil.which("pwsh")
PACKAGES = (
    "agent-ssh-manager",
    "agent-credential-relay",
    "agent-config-migrate",
    "agent-zdd",
    "agent-venue-copilot",
    "agent-session-liveness-probe",
    "agent-single-instance-lease",
    "agent-remote-login-shell",
    "agent-codespaces",
)
VARIABLES = (
    "SSH_MGR_DIR",
    "CRED_RELAY_DIR",
    "CFG_MIGRATE_DIR",
    "ZDD_DIR",
    "VENUE_COPILOT_DIR",
    "SESSION_LIVENESS_PROBE_DIR",
    "SINGLE_INSTANCE_LEASE_DIR",
    "REMOTE_LOGIN_SHELL_DIR",
    "PLUGIN_DIR",
)
PS_VARIABLES = (
    "SshMgrDir",
    "CredRelayDir",
    "CfgMigrateDir",
    "ZddDir",
    "VenueCopilotDir",
    "SessionLivenessProbeDir",
    "SingleInstanceLeaseDir",
    "RemoteLoginShellDir",
    "PluginDir",
)
pytestmark = pytest.mark.timeout(60)


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        "COPILOT_PLUGIN_STAGED_FROM",
        "COPILOT_PLUGIN_INSTALL_SMOKE",
    ):
        env.pop(key, None)
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
        "AGENT_HOME": "home",
    }.items():
        root = tmp_path / suffix
        root.mkdir(exist_ok=True)
        env[key] = str(root)
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = "1"
    env["TEST_UV_LOG"] = str(tmp_path / "uv.jsonl")
    return env


def _function(ext: str, name: str) -> str:
    source = (PLUGIN / "scripts" / f"install.{ext}").read_text(encoding="utf-8")
    pattern = (
        rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}"
        if ext == "sh"
        else (rf"(?ms)^function {re.escape(name)} \{{.*?^\}}")
    )
    match = re.search(pattern, source)
    assert match, name
    return match.group(0) + "\n"


def _run(ext: str, script: str, tmp_path: Path, env: dict[str, str]):
    path = tmp_path / f"harness.{ext}"
    path.write_text(script, encoding="utf-8")
    command = ["bash", str(path)] if ext == "sh" else [PWSH, "-NoProfile", "-File", str(path)]
    return subprocess.run(
        command,
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
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
    print("uv test")
    raise SystemExit(0)
mode = os.environ.get("TEST_UV_MODE", "ok")
if mode == "fatal":
    print("permanent failure")
    raise SystemExit(23)
count = sum(item == args for item in history)
if args[:2] == ["pip", "install"]:
    if mode == "sre" and count < 2:
        print("SRE module mismatch")
        raise SystemExit(1)
    raise SystemExit(0)
if args[0] == "venv":
    root = Path(args[1])
    if mode == "fallback" and "--python" in args:
        print("requested Python unavailable")
        raise SystemExit(24)
    root.mkdir(parents=True, exist_ok=True)
    (root / "bin").mkdir(exist_ok=True)
    (root / "bin" / "python").touch()
    (root / "Scripts").mkdir(exist_ok=True)
    (root / "Scripts" / "python.exe").touch()
    if mode == "missing-cfg" and count < 2:
        raise SystemExit(0)
    if mode == "corrupt" and count < 2:
        print("failed to locate pyvenv.cfg; exit code: 106")
        raise SystemExit(106)
    if mode == "sre" and count < 2:
        print("SRE module mismatch")
        raise SystemExit(1)
    (root / "pyvenv.cfg").write_text("home = test")
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        uv = tmp_path / "uv.cmd"
        uv.write_text(f'@"{sys.executable}" "{driver}" %*\n', encoding="utf-8")
    else:
        uv = tmp_path / "uv"
        uv.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{driver}" "$@"\n', encoding="utf-8")
        uv.chmod(0o755)
    return uv


def _prelude(ext: str) -> str:
    if ext == "sh":
        return (
            "set -euo pipefail\n"
            '_warn() { echo "$*" >&2; }\n_fail() { echo "$*" >&2; }\n'
            "_step() { :; }\n_ok() { :; }\nsleep() { :; }\n"
            f'. "{ENGINE / "installer-engine.sh"}"\n'
        )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-ServiceWarn { param($Msg) [Console]::Error.WriteLine($Msg) }\n"
        "function Write-ServiceErr { param($Msg) [Console]::Error.WriteLine($Msg) }\n"
        "function Write-ServiceOk { param($Msg) }\n"
        "function Write-Warn { param($Msg) Write-ServiceWarn $Msg }\n"
        "function Write-Ok { param($Msg) }\nfunction Start-Sleep { param($Seconds) }\n"
        f". '{ENGINE / 'installer-engine.ps1'}'\n"
    )


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("mode", ["sre", "missing-cfg", "corrupt", "fallback", "fatal"])
def test_codespaces_deploy_venv_uses_shared_retry_and_fallback(tmp_path, ext, mode):
    if ext == "ps1" and PWSH is None:
        pytest.skip("pwsh unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["TEST_UV_MODE"] = mode
    slot = tmp_path / "slot"
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "deploy_venv")
            + f"""
VENV_DIR='{slot}'
VENV_PYTHON="$VENV_DIR/bin/python"
UV_CMD='{uv}'
_assert_uv() {{ :; }}
_ensure_uv_index() {{ :; }}
_versioned_slot_clean() {{ :; }}
deploy_venv
"""
        )
    else:
        script = (
            _prelude(ext)
            + _function(ext, "Deploy-Venv")
            + f"""
$env:OS = 'Test'
$VenvDir = '{slot}'
$VenvPython = Join-Path $VenvDir 'Scripts/python.exe'
$UvCommand = '{uv}'
function Assert-Uv {{}}
function Invoke-VersionedSlotClean {{ return $true }}
function Test-PythonVenv {{ param($Dir, $Python) return Test-Path (Join-Path $Dir 'pyvenv.cfg') }}
if (-not (Deploy-Venv)) {{ exit 1 }}
if ($ErrorActionPreference -ne 'Stop') {{ throw 'preference leaked' }}
"""
        )
    result = _run(ext, script, tmp_path, env)
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert bool(result.returncode) == (mode == "fatal"), result.stdout + result.stderr
    assert calls[0] == ["venv", str(slot), "--python", "3.11", "--allow-existing"]
    assert len(calls) == (2 if mode in ("fatal", "fallback") else 3)
    if mode in ("fallback", "fatal"):
        assert calls[-1] == ["venv", str(slot), "--allow-existing"]
    elif ext == "ps1":
        assert "retrying" in result.stderr


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("editable", [False, True])
@pytest.mark.parametrize("mode", ["sre", "fatal"])
def test_codespaces_library_install_order_retry_and_errors(tmp_path, ext, editable, mode):
    if ext == "ps1" and PWSH is None:
        pytest.skip("pwsh unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["TEST_UV_MODE"] = mode
    sources = []
    for name in PACKAGES:
        source = tmp_path / name
        source.mkdir()
        (source / "pyproject.toml").touch()
        sources.append(source)
    if ext == "sh":
        script = _prelude(ext) + _function(ext, "_install_package_into")
        script += f"\nUV_CMD='{uv}'\n"
        script += "".join(f"{var}='{path}'\n" for var, path in zip(VARIABLES, sources))
        script += "_install_package_into target-python" + (" --editable\n" if editable else "\n")
    else:
        script = _prelude(ext) + _function(ext, "Install-PackageInto")
        script += (
            f"\n$UvCommand='{uv}'\nfunction Remove-ConsoleTrampolines {{ param($VenvDir) }}\n"
        )
        script += "".join(f"${var}='{path}'\n" for var, path in zip(PS_VARIABLES, sources))
        script += "if (-not (Install-PackageInto -Python 'root/Scripts/python.exe'"
        script += " -Editable" if editable else ""
        script += ")) { exit 1 }\n"
    result = _run(ext, script, tmp_path, env)
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert bool(result.returncode) == (mode == "fatal"), result.stdout + result.stderr
    if mode == "fatal":
        assert len(calls) == 1
        assert "permanent failure" in result.stderr
        return
    assert len(calls) == 27
    for index, (package, source) in enumerate(zip(PACKAGES, sources)):
        args = calls[index * 3]
        assert calls[index * 3 : index * 3 + 3] == [args] * 3
        assert args[-2:] == [str(source), "--quiet"]
        assert args[4:-2] == (["--editable"] if editable else ["--reinstall-package", package])


def test_codespaces_canonical_reference_registration_and_release_materialization(tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import installer_engine_ref as ier
        import materialize_main as mm
    finally:
        sys.path.pop(0)
    assert "agent-codespaces" in ier.ADOPTERS
    for ext in ("sh", "ps1"):
        ref = ier.find_engine_ref(PLUGIN / "scripts" / f"install.{ext}", ext)
        assert ref is not None and ier.is_canonical_ref(ref, PLUGIN)
        assert not (PLUGIN / "scripts" / ref.file_name).exists()
    payload = tmp_path / "release" / "plugins" / PLUGIN.name
    shutil.copytree(PLUGIN, payload, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    logs = mm.materialize_uv_editable_refs(tmp_path / "release", canonical_root=REPO)
    logs += mm.materialize_installer_engine_refs(tmp_path / "release", canonical_root=REPO)
    assert not any(line.startswith("SKIP") for line in logs), logs
    for ext in ("sh", "ps1"):
        ref = ier.find_engine_ref(payload / "scripts" / f"install.{ext}", ext)
        assert ref is not None and ier.is_local_ref(ref, payload)
        assert ref.local_path.read_bytes() == (ENGINE / ref.file_name).read_bytes()
    project = tomllib.loads((payload / "pyproject.toml").read_text())
    for spec in project["tool"]["uv"]["sources"].values():
        assert (payload / spec["path"]).resolve().is_relative_to(payload)
        assert (payload / spec["path"] / "pyproject.toml").is_file()
        assert not spec.get("editable", False)


@pytest.mark.skipif(PWSH is None, reason="pwsh unavailable")
@pytest.mark.parametrize("materialized", [False, True])
def test_codespaces_stamp_snapshot_is_standalone(tmp_path, materialized):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import materialize_main as mm
    finally:
        sys.path.pop(0)
    stage = tmp_path / "source"
    payload = stage / "plugins" / PLUGIN.name
    shutil.copytree(PLUGIN, payload, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    if materialized:
        logs = mm.materialize_uv_editable_refs(stage, canonical_root=REPO)
        logs += mm.materialize_installer_engine_refs(stage, canonical_root=REPO)
        assert not any(line.startswith("SKIP") for line in logs), logs
    else:
        for lib in (
            "installer-engine",
            *(
                Path(spec["path"]).name
                for spec in tomllib.loads((PLUGIN / "pyproject.toml").read_text())["tool"]["uv"][
                    "sources"
                ].values()
            ),
        ):
            shutil.copytree(REPO / "libs" / lib, stage / "libs" / lib)
    env = _env(tmp_path)
    root = tmp_path / "home" / ".agent-codespaces"
    stamp = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(root),
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr
    snapshot = Path((root / "payload-dir").read_text().strip())
    for ext in ("sh", "ps1"):
        assert (snapshot / "scripts" / f"installer-engine.{ext}").read_bytes() == (
            ENGINE / f"installer-engine.{ext}"
        ).read_bytes()
        text = (snapshot / "scripts" / f"install.{ext}").read_text()
        expected = (
            '. "$SCRIPT_DIR/installer-engine.sh"'
            if ext == "sh"
            else (". (Join-Path $PSScriptRoot 'installer-engine.ps1')")
        )
        assert expected in text.splitlines()
    project = tomllib.loads((snapshot / "pyproject.toml").read_text())
    for spec in project["tool"]["uv"]["sources"].values():
        library = snapshot / spec["path"]
        assert library.resolve().is_relative_to(snapshot)
        assert (library / "pyproject.toml").is_file()
        assert not spec.get("editable", False)
        nested = tomllib.loads((library / "pyproject.toml").read_text())
        for dependency in nested.get("tool", {}).get("uv", {}).get("sources", {}).values():
            assert (library / dependency["path"]).resolve().is_relative_to(snapshot)
            assert (library / dependency["path"] / "pyproject.toml").is_file()
            assert not dependency.get("editable", False)
    stage.rename(tmp_path / "unavailable-source")
    status = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(snapshot / "scripts" / "install.ps1"),
            "status",
            "-InstallDir",
            str(root),
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # A stamped payload deliberately has no runtime yet.
    assert status.returncode != 0
    assert "Venv missing" in status.stdout
    if os.name != "nt":
        status_sh = subprocess.run(
            [
                "bash",
                str(snapshot / "scripts" / "install.sh"),
                "status",
                "--install-dir",
                str(root),
            ],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert status_sh.returncode == 0, status_sh.stdout + status_sh.stderr
    uv = _fake_uv(tmp_path)
    env["PATH"] = str(uv.parent) + os.pathsep + env["PATH"]
    env["TEST_UV_MODE"] = "fatal"
    if os.name != "nt":
        # The nested snap-packaged pwsh host can sanitize PATH; exercise the
        # engine's plugin-owned tool fallback without any network acquisition.
        tool = root / "tool"
        tool.mkdir()
        shutil.copy2(uv, tool / "uv.exe")
    first_use = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(tmp_path / "home" / ".local" / "bin" / "agent-codespaces.ps1"),
            "version",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert first_use.returncode != 0
    assert "permanent failure" in first_use.stdout + first_use.stderr, (
        first_use.stdout + first_use.stderr
    )
    assert "provisioning failed" in first_use.stdout + first_use.stderr


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="POSIX pwsh signed-flow simulation")
@pytest.mark.parametrize("usable", [False, True])
@pytest.mark.parametrize("signed_rc", [0, 23])
def test_codespaces_signed_venv_recovery_keeps_usable_nonzero_result(tmp_path, usable, signed_rc):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    slot = tmp_path / "slot"
    signed = tmp_path / "signed-python"
    signed.write_text(
        '#!/bin/sh\nmkdir -p "$4/Scripts"\ntouch "$4/Scripts/python.exe" "$4/pyvenv.cfg"\n'
        f"exit {signed_rc}\n",
        encoding="utf-8",
    )
    signed.chmod(0o755)
    # Signed invocation is: -m venv --copies <slot>.
    script = (
        _prelude("ps1")
        + _function("ps1", "Deploy-Venv")
        + f"""
$env:OS = 'Windows_NT'
$VenvDir = '{slot}'
$VenvPython = Join-Path $VenvDir 'Scripts/python.exe'
$UvCommand = '{uv}'
function py {{ param($Version, $Flag, $Code) $global:LASTEXITCODE = 0; return '{signed}' }}
function Get-AuthenticodeSignature {{ param($Path) return @{{ Status = 'Valid' }} }}
function Assert-Uv {{}}
function Invoke-VersionedSlotClean {{ return $true }}
function Test-PythonVenv {{
    param($Dir, $Python)
    if (Test-Path $env:TEST_UV_LOG) {{ return $true }}
    return ${str(usable).lower()}
}}
$ok = Deploy-Venv
if ($ErrorActionPreference -ne 'Stop') {{ throw 'preference leaked' }}
if (-not $ok) {{ exit 1 }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    log = tmp_path / "uv.jsonl"
    if usable:
        assert result.returncode == 0, result.stdout + result.stderr
        assert not log.exists()
        if signed_rc:
            assert "after producing a usable venv" in result.stderr
    elif signed_rc:
        assert result.returncode == 0, result.stdout + result.stderr
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        assert calls == [["venv", str(slot), "--python", "3.11", "--allow-existing"]]
        assert "falling back to uv" in result.stderr
    else:
        assert result.returncode != 0
        assert not log.exists()
        assert "Venv validation failed" in result.stderr


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("marketplace", [False, True])
def test_codespaces_manifest_shared_writer_preserves_slot_and_source(tmp_path, ext, marketplace):
    if ext == "ps1" and PWSH is None:
        pytest.skip("pwsh unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    env = _env(tmp_path)
    root = tmp_path / "runtime"
    root.mkdir()
    if marketplace:
        env["COPILOT_PLUGIN_STAGED_FROM"] = (
            "/home/example/.copilot/installed-plugins/mkt/agent-codespaces"
        )
    if ext == "sh":
        script = _prelude(ext) + _function(ext, "_source_kind")
        script += (
            _function(ext, "_write_codespaces_deploy_manifest")
            + f"""
INSTALL_DIR='{root}'
PLUGIN_DIR='{PLUGIN}'
VENV_DIR='{root}/versions/test'
_git_info() {{ echo 'abc dev true'; }}
_write_codespaces_deploy_manifest
"""
        )
    else:
        script = _prelude(ext) + _function(ext, "Get-SourceKind")
        script += (
            _function(ext, "Write-CodespacesDeployManifest")
            + f"""
$InstallDir='{root}'
$PluginDir='{PLUGIN}'
$RepoRoot='original-root'
$VenvDir='{root}/versions/build'
$LinkDir='{root}/versions/selected'
function Get-GitInfo {{ param($Path)
    if ($Path -ne 'original-root') {{ throw 'lost repository root' }}
    return @{{ commit = 'abc'; branch = 'dev'; dirty = $true }}
}}
Write-CodespacesDeployManifest
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["schema_version"] == 3
    assert manifest["service"] == manifest["source"]["plugin"] == "agent-codespaces"
    assert manifest["runtime"] == "python"
    assert manifest["venv"] == f"{root.as_posix()}/versions/" + (
        "test" if ext == "sh" else "selected"
    )
    assert manifest["source"]["kind"] == ("marketplace" if marketplace else "local")
    assert (
        manifest["source"]["version"]
        == tomllib.loads((PLUGIN / "pyproject.toml").read_text())["project"]["version"]
    )
    assert manifest["source"]["commit"] == (None if marketplace else "abc")
    assert manifest["source"]["dirty"] == (not marketplace)
    assert not (root / "deploy-manifest.json.tmp").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX owning-payload stamp")
def test_codespaces_materialized_posix_stamp_and_first_use_are_local(tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import materialize_main as mm
    finally:
        sys.path.pop(0)
    payload = tmp_path / "release" / "plugins" / PLUGIN.name
    shutil.copytree(PLUGIN, payload, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    logs = mm.materialize_uv_editable_refs(tmp_path / "release", canonical_root=REPO)
    logs += mm.materialize_installer_engine_refs(tmp_path / "release", canonical_root=REPO)
    assert not any(line.startswith("SKIP") for line in logs), logs
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    env["PATH"] = str(uv.parent) + os.pathsep + env["PATH"]
    env["TEST_UV_MODE"] = "fatal"
    stamp = subprocess.run(
        ["bash", str(payload / "scripts" / "install.sh"), "stamp"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr
    root = tmp_path / "home" / ".agent-codespaces"
    assert (root / "payload-dir").read_text().strip() == str(payload)
    assert not (tmp_path / "uv.jsonl").exists()
    first_use = subprocess.run(
        ["bash", str(tmp_path / "home" / ".local" / "bin" / "agent-codespaces"), "version"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert first_use.returncode != 0
    assert "permanent failure" in first_use.stderr
    assert "provisioning FAILED" in first_use.stderr


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_codespaces_dev_venv_uses_shared_retry_before_claiming(tmp_path, ext):
    if ext == "ps1" and PWSH is None:
        pytest.skip("pwsh unavailable")
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX harness")
    env = _env(tmp_path)
    env["TEST_UV_MODE"] = "missing-cfg"
    uv = _fake_uv(tmp_path)
    root = tmp_path / "runtime"
    if ext == "sh":
        script = (
            _prelude(ext)
            + _function(ext, "do_dev")
            + f"""
INSTALL_DIR='{root}'
LOCAL_BIN='{tmp_path}/bin'
SRC_VERSION='test'
SERVICE_NAME='Codespaces'
UV_CMD='{uv}'
_header() {{ :; }}
_deploy_versioned_runtime_helper() {{ :; }}
_resolve_dev_slot_owner() {{ echo 'owner'; }}
_assert_uv() {{ :; }}
_ensure_uv_index() {{ :; }}
_run_versioned_runtime() {{ return 1; }}
do_dev
"""
        )
    else:
        script = (
            _prelude(ext)
            + _function(ext, "Invoke-Dev")
            + f"""
$InstallDir='{root}'
$LocalBin='{tmp_path}/bin'
$VersionedRuntime=$true
$ServiceName='Codespaces'
$Force=$false
$UvCommand='{uv}'
function Write-ServiceHeader {{ param($Msg) }}
function Deploy-VersionedRuntimeHelper {{}}
function Resolve-DevSlotOwner {{ return 'owner' }}
function Assert-Uv {{}}
function Test-PythonVenv {{ param($Dir, $Python) return Test-Path (Join-Path $Dir 'pyvenv.cfg') }}
function Invoke-VersionedRuntime {{ param($Arguments)
    return @{{ ExitCode=1; Output='claim stopped' }}
}}
if (-not (Invoke-Dev)) {{ exit 1 }}
"""
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode != 0
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    expected = ["venv", str(root / "versions" / "dev"), "--python", "3.11", "--allow-existing"]
    assert calls == [expected] * 3


@pytest.mark.skipif(os.name == "nt", reason="POSIX uv tool PATH")
def test_codespaces_assert_uv_keeps_cached_tool_on_caller_path(tmp_path):
    env = _env(tmp_path)
    uv = _fake_uv(tmp_path)
    root = tmp_path / "runtime"
    tool = root / "tool"
    tool.mkdir(parents=True)
    shutil.copy2(uv, tool / "uv")
    env["PATH"] = "/usr/bin:/bin"
    script = (
        _prelude("sh")
        + _function("sh", "_assert_uv")
        + f"""
INSTALL_DIR='{root}'
_assert_uv
[[ "$UV_CMD" == '{tool}/uv' ]]
[[ "$(command -v uv)" == "$UV_CMD" ]]
uv --version
"""
    )
    result = _run("sh", script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in (tmp_path / "uv.jsonl").read_text().splitlines()]
    assert calls == [["--version"], ["--version"]]


@pytest.mark.skipif(PWSH is None, reason="pwsh unavailable")
@pytest.mark.parametrize("available", [False, True])
def test_codespaces_assert_uv_uses_shared_acquisition_and_propagates_failure(tmp_path, available):
    env = _env(tmp_path)
    script = (
        _prelude("ps1")
        + _function("ps1", "Assert-Uv")
        + f"""
$InstallDir='{tmp_path}/runtime'
function Ensure-Uv {{
    param($InstallRoot)
    if ($InstallRoot -ne $InstallDir) {{ throw 'wrong tool root' }}
    if (${str(available).lower()}) {{ return 'resolved-uv' }}
    return $null
}}
Assert-Uv
if ($script:UvCommand -ne 'resolved-uv') {{ throw 'lost uv command' }}
"""
    )
    result = _run("ps1", script, tmp_path, env)
    assert bool(result.returncode) == (not available)
    if not available:
        assert "uv is required but acquisition failed" in result.stderr
