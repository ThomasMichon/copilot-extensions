"""Retained host health uses real isolated imports before runtime publication."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
ENGINE = PLUGIN.parents[1] / "libs" / "installer-engine"
BASE = sys._base_executable
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
SPEC = importlib.util.spec_from_file_location(
    "_index_retained_health_fixtures", Path(__file__).with_name("test_source_runtime_install.py"),
)
assert SPEC and SPEC.loader
FIXTURES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURES)
source = FIXTURES.source
function = FIXTURES.function


def venv(path: Path, *, host: bool = False) -> tuple[Path, Path]:
    subprocess.run(
        [BASE, "-I", "-m", "venv", "--without-pip", str(path)],
        check=True, capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    python = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python3")
    query = subprocess.run(
        [str(python), "-I", "-c", 'import sysconfig; print(sysconfig.get_path("purelib"))'],
        check=True, capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    site = Path(query.stdout.strip())
    package = site / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import argparse\n"
        "if __name__ == '__main__':\n"
        "    argparse.ArgumentParser(prog='agent-index').parse_args()\n",
        encoding="utf-8",
    )
    if host:
        for name in ("fastapi", "uvicorn", "numpy", "pyarrow", "lancedb"):
            (site / f"{name}.py").write_text("", encoding="utf-8")
        (package / "server.py").write_text(
            "import fastapi, uvicorn, numpy, pyarrow, lancedb\nREADY = True\n",
            encoding="utf-8",
        )
        subprocess.run(
            [str(python), "-I", "-c",
             "import fastapi, uvicorn, numpy, pyarrow, lancedb; import agent_index.server"],
            check=True, capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
        )
    return python, site


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize(("role", "corruption"), [
    ("host", "server"), ("host", "dependency"), ("host", "none"), ("client", "server"),
    ("host", "cli"), ("client", "cli"), ("host", "server-cli"),
])
def test_retained_host_health_gates_publication(
    source, tmp_path: Path, ext: str, role: str, corruption: str,
) -> None:
    if ext == "sh" and os.name == "nt":
        pytest.skip("POSIX installer fixture")
    executable = (
        (shutil.which("pwsh") or shutil.which("powershell"))
        if ext == "ps1" else shutil.which("bash")
    )
    if not executable:
        pytest.skip(f"{ext} unavailable")
    api, payload, identity = source
    root = tmp_path / "runtime"
    previous = root / "versions" / "1.0.0"
    candidate = root / "versions" / identity.runtime_version
    venv(previous)
    venv(previous / "server", host=True)
    _, cli_site = venv(candidate)
    _, server_site = venv(candidate / "server", host=True)
    runtime = api._runtime()
    runtime.mark_complete(root, "1.0.0", payload_hash="e" * 64)
    runtime.activate(root, "1.0.0", link_free=True)
    full_identity = json.dumps(api.manifest_fields(identity), sort_keys=True)
    runtime.mark_complete(
        root, identity.runtime_version,
        payload_hash=hashlib.sha256(full_identity.encode()).hexdigest(),
    )
    damaged = (
        cli_site / "agent_index/__main__.py" if corruption == "cli" else
        server_site / "agent_index/__main__.py" if corruption == "server-cli" else
        server_site / ("agent_index/server.py" if corruption == "server" else "lancedb.py")
    )
    token = f"retained {corruption} corruption"
    if corruption != "none":
        damaged.write_text(f"raise ImportError({token!r})\n", encoding="utf-8")
    previous_payload = tmp_path / "previous-payload"
    previous_payload.mkdir()
    (root / "payload-dir").write_text(str(previous_payload), encoding="utf-8")
    (root / "stamped-version").write_text("1.0.0", encoding="utf-8")
    (root / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": "agent-index", "runtime": "python",
        "venv": str(previous), "runtime_version": "1.0.0",
        "source": {"kind": "local", "path": str(previous_payload),
                   "plugin": "agent-index", "version": "1.0.0", "dirty": False},
    }), encoding="utf-8")
    names = ("current-version", "last-known-good", "stamped-version", "payload-dir", "deploy-manifest.json")
    if os.name != "nt":
        root.chmod(0o700)
        for name in names:
            if (root / name).exists():
                (root / name).chmod(0o600)
    before = {name: (root / name).read_bytes() if (root / name).exists() else None for name in names}
    readers = (
        root / "bin/resolve-runtime.sh", root / "bin/resolve-runtime.ps1",
        tmp_path / "bin/agent-index", tmp_path / "bin/agent-index.ps1",
        tmp_path / "bin/agent-index.cmd",
    )
    for reader in readers:
        reader.parent.mkdir(parents=True, exist_ok=True)
        reader.write_bytes(b"# previous runtime reader\n")
        if os.name != "nt":
            reader.parent.chmod(0o700)
            reader.chmod(0o700)
    reader_before = {reader: reader.read_bytes() for reader in readers}
    completion = (candidate / ".install-complete.json").read_bytes()
    env = FIXTURES._FIXTURES.environment(tmp_path)
    env["AGENT_INDEX_ROLE"] = role
    paths = [str(Path(BASE).parent), str(Path(executable).resolve().parent)]
    if os.name == "nt":
        system = Path(env["SYSTEMROOT"])
        paths.extend((str(system / "System32"), str(system)))
    else:
        paths.extend(os.defpath.split(os.pathsep))
    env["PATH"] = os.pathsep.join(dict.fromkeys(paths))
    harness = tmp_path / f"retained-health.{ext}"
    if ext == "ps1":
        FIXTURES._FIXTURES.construction_bundle(tmp_path)
        for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
            shutil.copy2(SCRIPTS / name, tmp_path / name)
        definitions = "\n".join(function(name, ext) for name in (
            "Install-Runtime", "Test-IndexVenv", "Test-RuntimeOrigin", "Get-MachineRole",
            "New-IndexMutex", "Enter-IndexBuildLock", "Enter-IndexStampLock",
            "Get-PayloadHash", "Invoke-VersionedSlotClean", "Invoke-VersionedMarkComplete",
            "Get-VerTuple", "Test-VersionLt", "Test-IndexPublicationFresh",
            "Get-VersionedCurrent", "Invoke-VersionedActivate", "Publish-IndexRuntime",
            "Deploy-SetupGatedBinstub", "Write-Manifest", "Publish-FileAtomically", "Get-SourceKind",
        ))
        text = f"""
$ErrorActionPreference = 'Stop'
$ScriptDir = '{SCRIPTS}'
$PluginDir = '{payload}'
$PkgSrcDir = Join-Path $PluginDir 'src'
$InstallDir = '{root}'
$LocalBin = '{tmp_path / "bin"}'
$probePayload = $PluginDir
$LegacyVenvDir = '{tmp_path / "absent-legacy"}'
$VenvDir = Join-Path $InstallDir 'absent'
$LinkDir = $VenvDir
$SrcVersion = '{FIXTURES.PACKAGE}'
$VersionedRuntime = $true
$Force = $false
. '{ENGINE / "installer-engine.ps1"}'
. '{SCRIPTS / "versioned_runtime_native.ps1"}'
function Write-Ok {{ param($Msg) }}
function Write-Skip {{ param($Msg) }}
function Write-Warn {{ param($Msg) [Console]::Error.WriteLine($Msg) }}
function Write-Fail {{ param($Msg) throw $Msg }}
{definitions}
function Get-BootstrapPython {{ return '{BASE}' }}
function Get-IndexUv {{ return $null }}
function Get-ActivationRole {{ return 'unconfigured' }}
function Invoke-VersionedGc {{ param($KeepPrev) }}
function Get-GitInfo {{ param($Path) return @{{commit='unknown'; branch='unknown'; dirty=$false}} }}
Initialize-SourceRuntime
try {{ Install-Runtime }}
finally {{
    if (Get-Command Stop-RuntimeConstruction -ErrorAction SilentlyContinue) {{ Stop-RuntimeConstruction }}
}}
"""
        argv = [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)]
    else:
        definitions = "\n".join(function(name, ext) for name in (
            "_ensure_runtime", "_test_index_venv", "_runtime_origin_under", "_machine_role",
            "_with_index_build_lock", "_with_index_publication_lock", "_with_index_advisory_lock",
            "_payload_hash", "_versioned_slot_clean", "_versioned_mark_complete",
            "_version_lt", "_index_publication_fresh", "_versioned_current", "_versioned_activate",
            "deploy_binstub", "_write_manifest", "_write_index_stamp_file", "_source_kind",
        ))
        text = f"""
set -euo pipefail
SCRIPT_DIR='{SCRIPTS}'
PLUGIN_DIR='{payload}'
PKG_SRC_DIR="$PLUGIN_DIR/src"
INSTALL_DIR='{root}'
LOCAL_BIN='{tmp_path / "bin"}'
STUB="$LOCAL_BIN/agent-index"
LINK_DIR="$INSTALL_DIR/.venv"
SRC_VERSION='{FIXTURES.PACKAGE}'
VERSIONED_RUNTIME=1
FORCE=0
. '{ENGINE / "installer-engine.sh"}'
_ok() {{ :; }}; _skip() {{ :; }}; _warn() {{ printf '%s\\n' "$*" >&2; }}
_step() {{ :; }}
_fail() {{ printf '%s\\n' "$*" >&2; }}
{definitions}
_bootstrap_python() {{ printf '%s' '{BASE}'; }}
_find_python() {{ printf '%s' '{BASE}'; }}
_ensure_uv() {{ :; }}; _ensure_uv_index() {{ :; }}
_activation_role() {{ printf unconfigured; }}
_versioned_gc() {{ :; }}
_initialize_source_runtime
_ensure_runtime
"""
        argv = [executable, str(harness)]
    harness.write_bytes(text.encode("utf-8"))
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=25, creationflags=HEADLESS,
    )
    if corruption == "cli" or (role == "host" and corruption != "none"):
        assert result.returncode != 0, result.stdout + result.stderr
        assert token in result.stdout + result.stderr
        after = {name: (root / name).read_bytes() if (root / name).exists() else None for name in names}
        assert after == before
        assert {reader: reader.read_bytes() for reader in readers} == reader_before
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "current-version").read_text().strip() == identity.runtime_version
        manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
        assert manifest["runtime_source"] == identity.document()
    assert (candidate / ".install-complete.json").read_bytes() == completion
