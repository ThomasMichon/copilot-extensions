"""Contained source publication using the actual installer runtime functions."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
SCRIPTS = PLUGIN / "scripts"
PACKAGE = "1.2.3-dev4"
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run_publication_bash(argv: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    from agent_dispatch.procutil import run_background_capture

    # Git Bash pays native process-start cost for each stat/hash probe.
    timeout = 40 if os.name == "nt" else 20
    result = run_background_capture(
        argv, env=environment(tmp_path), cwd=tmp_path, timeout=timeout,
        max_output=65536,
    )
    assert result is not None, f"publication fixture exceeded {timeout}s or could not start"
    return result


def function(ext: str, name: str) -> str:
    lines = (SCRIPTS / f"install.{ext}").read_text(encoding="utf-8").splitlines(keepends=True)
    declaration = (
        rf"^function {re.escape(name)}(?:\s|\()"
        if ext == "ps1" else rf"^{re.escape(name)}\(\) "
    )
    next_declaration = (
        r"^function [A-Za-z_][A-Za-z0-9_-]*"
        if ext == "ps1" else r"^[A-Za-z_][A-Za-z0-9_]*\(\) [({]"
    )
    start = next(index for index, line in enumerate(lines) if re.match(declaration, line))
    end = len(lines)
    delimiter = None
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        if re.match(next_declaration, line):
            end = index
            break
        if ext == "ps1":
            match = re.search(r"@(['\"])\s*$", line)
            if match:
                delimiter = match[1] + "@"
        else:
            match = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
            if match:
                delimiter = match[2]
    return "".join(lines[start:end])


def script(ext: str) -> str:
    if ext == "ps1":
        names = (
            "Assert-DispatchActions",
            "Install-Runtime", "Get-PayloadHash", "Get-BootstrapPython",
            "Invoke-VersionedSlotClean", "Invoke-VersionedMarkComplete",
            "Invoke-RuntimeBrokerAdoption",
            "New-PluginBuildSnapshot", "Enter-PluginSnapshotLock", "Publish-FileAtomically",
            "Invoke-VersionedActivate", "Get-VersionedCurrent", "Test-ActivationSupersededNow",
            "Get-VerTuple", "Test-VersionLt", "Write-Manifest",
            "Deploy-SelfProvisioningBinstub", "Get-SourceKind", "Test-VenvIsLink", "Invoke-Stamp",
        )
        header = """
param([string]$Action = 'provision')
$ErrorActionPreference = 'Stop'
$ScriptDir = $PSScriptRoot
$PluginDir = Split-Path -Parent $ScriptDir
$InstallDir = Join-Path $env:USERPROFILE '.agent-dispatch'
$legacyInstallDir = $InstallDir
$publishLegacyNames = $true
$LocalBin = Join-Path $env:USERPROFILE '.local/bin'
$PkgSrcDir = Join-Path $PluginDir 'src'
$SrcVersion = '1.2.3-dev4'
$VenvDir = Join-Path $InstallDir '.venv'
$LinkDir = $VenvDir
$VersionedRuntime = $true
$Force = $false
$PreserveSupervisors = $env:FIXTURE_PRESERVE_SUPERVISORS -eq '1'
$script:ActivationSuperseded = $false
$script:GlobalActivationLockTimeoutSeconds = 5
$script:InstallerDependencyPolicy = Join-Path $ScriptDir 'installer_dependency_policy.py'
$env:AGENT_DISPATCH_SUPERVISE_MACHINE = 'fixture'
. (Join-Path $ScriptDir 'installer-engine.ps1')
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
. (Join-Path $ScriptDir 'source-runtime.ps1')
function Write-Ok { param($Msg) }
function Write-Skip { param($Msg) }
function Write-Warn { param($Msg) }
function Write-Step { param($Msg) }
function Write-Fail { param($Msg) throw $Msg }
"""
        footer = """
function Ensure-Uv { param($InstallRoot) return $null }
function Invoke-SupervisorAudit { param($Event,$Reason,$Outcome) }
function Register-PickerPivot {}
function Invoke-VersionedGc { param($KeepPrev) }
function Get-GitInfo { param($Path) return @{commit='unknown'; branch='unknown'; dirty=$false} }
function Get-CopilotPersistentEnvironmentVariable { param($Name,$Target) return $LocalBin }
function Set-CopilotPersistentEnvironmentVariable { throw 'Forbidden host environment mutation' }
Initialize-SourceRuntime
try {
    if ($Action -eq 'stamp') { Invoke-Stamp }
    elseif ($Action -eq 'prepare') { Install-Runtime -PrepareOnly }
    elseif ($Action -in @('install', 'provision', 'update')) { Install-Runtime }
    else { throw 'Unsupported fixture lifecycle action' }
} finally {
    if (Get-Command Stop-RuntimeConstruction -ErrorAction SilentlyContinue) { Stop-RuntimeConstruction }
}
"""
    else:
        names = (
            "_ensure_runtime", "_payload_hash", "_versioned_slot_clean", "_versioned_mark_complete",
            "_versioned_activate", "_versioned_current", "_activation_superseded_now",
            "_version_lt", "_write_manifest", "deploy_binstub", "do_stamp",
        )
        header = f"""#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="$HOME/.agent-dispatch"
LEGACY_INSTALL_DIR="$INSTALL_DIR"
SERVICE_SUFFIX=""
LOCAL_BIN="$HOME/.local/bin"
PKG_SRC_DIR="$PLUGIN_DIR/src"
SRC_VERSION="{PACKAGE}"
LINK_DIR="$INSTALL_DIR/.venv"
STUB="$LOCAL_BIN/agent-dispatch"
BOARD_STUB="$LOCAL_BIN/agent-dispatch-board"
VERSIONED_RUNTIME=1
ACTIVATION_SUPERSEDED=0
FORCE=0
PRESERVE_SUPERVISORS="${{FIXTURE_PRESERVE_SUPERVISORS:-0}}"
AGENT_DISPATCH_SUPERVISE_MACHINE=fixture
_ok() {{ :; }}; _skip() {{ :; }}; _warn() {{ :; }}; _step() {{ :; }}
_fail() {{ printf '%s\\n' "$*" >&2; }}
. "$SCRIPT_DIR/installer-engine.sh"
. "$SCRIPT_DIR/source-runtime.sh"
"""
        footer = f"""
_bootstrap_python() {{ printf '%s' '{sys._base_executable}'; }}
_find_python() {{ printf '%s' '{sys._base_executable}'; }}
_ensure_uv() {{ :; }}
_ensure_uv_index() {{ :; }}
_supervisor_audit() {{ :; }}
_register_pivot() {{ :; }}
_versioned_gc() {{ :; }}
ACTION="${{1:-provision}}"
_initialize_source_runtime
case "${{1:-provision}}" in
    stamp) do_stamp ;;
    prepare) _ensure_runtime 1 ;;
    install|provision|update) _ensure_runtime ;;
    *) exit 2 ;;
esac
"""
    return header + "\n".join(function(ext, name) for name in names) + footer


def peer_failure_python(tmp_path: Path) -> Path:
    wrapper = tmp_path / "peer-python"
    wrapper.write_text(f"""#!{sys._base_executable}
import os
from pathlib import Path
import runpy
import sys
sys.dont_write_bytecode = True
args = sys.argv[1:]
if 'publish-redirects' not in args:
    os.execv(sys.executable, [sys.executable, *args])
while args[0] in ('-I', '-B'):
    args.pop(0)
root = Path(args[args.index('--root') + 1])
replace = os.replace
fired = False
def peer_replace(source, destination):
    global fired
    if Path(destination) == root / 'stamped-version' and not fired:
        fired = True
        peer = root / 'peer-payload'
        peer.write_bytes(b'peer-owned-redirect\\n')
        replace(peer, root / 'payload-dir')
        Path(os.environ['FIXTURE_FAILURE_LOG']).write_text('peer')
        raise OSError(5, 'injected second-marker failure')
    return replace(source, destination)
os.replace = peer_replace
sys.argv = args
sys.path.insert(0, str(Path(args[0]).parent))
runpy.run_path(args[0], run_name='__main__')
""", encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def successful_peer_python(tmp_path: Path) -> Path:
    wrapper = tmp_path / "successful-peer-python"
    wrapper.write_text(f"""#!{sys._base_executable}
import json
import os
from pathlib import Path
import subprocess
import sys
args = sys.argv[1:]
result = subprocess.run([sys.executable, *args])
if result.returncode == 0 and 'publish-redirects' in args:
    root = Path(args[args.index('--root') + 1])
    target = root / 'payload-dir'
    original = target.stat()
    peer = root / 'same-byte-peer'
    data = target.read_bytes()
    peer.write_bytes(data)
    os.replace(peer, target)
    changed = target.stat()
    assert target.read_bytes() == data
    assert (original.st_dev, original.st_ino) != (changed.st_dev, changed.st_ino)
    Path(os.environ['FIXTURE_PEER_RECORD']).write_text(json.dumps({{
        'device': changed.st_dev, 'inode': changed.st_ino, 'data': data.hex(),
        'producer_inode': original.st_ino,
    }}))
sys.exit(result.returncode)
""", encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def environment(tmp_path: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_", "PYTHON", "UV_", "PIP_", "GIT_"))
    }
    for key in (
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMP", "TEMP", "TMPDIR",
    ):
        path = tmp_path / key.lower()
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    env["USERPROFILE"] = env["HOME"]
    paths = [str(Path(sys._base_executable).parent)]
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell:
        paths.append(str(Path(powershell).resolve().parent))
    if os.name == "nt":
        system = Path(env["SYSTEMROOT"])
        paths.extend((str(system / "System32"), str(system)))
    else:
        env["WSL_DISTRO_NAME"] = "Fixture Distro"
        test_bin = Path(env["HOME"]) / "test-bin"
        test_bin.mkdir(exist_ok=True)
        getent = test_bin / "getent"
        getent.write_text(
            "#!/bin/sh\n"
            'printf "fixture:x:%s:%s:fixture:%s:/bin/sh\\n" "$(id -u)" "$(id -g)" "$HOME"\n',
            encoding="utf-8",
        )
        getent.chmod(0o755)
        paths.insert(0, str(test_bin))
        paths.extend(os.defpath.split(os.pathsep))
    env["PATH"] = os.pathsep.join(dict.fromkeys(paths))
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    return env


def stage(tmp_path: Path, *, broken_b: bool = False):
    started = time.monotonic()
    print("publication fixture: staging begin", file=sys.stderr, flush=True)
    env = environment(tmp_path)
    root = Path(env["HOME"]) / ".agent-dispatch"
    spec = importlib.util.spec_from_file_location(
        "_dispatch_publication_source_api", SCRIPTS / "versioned_runtime_source.py",
    )
    assert spec and spec.loader
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
    result = []
    for name, commit in (("a", "a" * 40), ("b", "c" * 40)):
        payload = tmp_path / f"source-{name}"
        scripts = payload / "scripts"
        shutil.copytree(
            REPO / "libs" / "installer-engine", scripts,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for relative in (
            "action_controls.py",
            "source-runtime.sh", "source-runtime.ps1", "base-python.ps1", "versioned_runtime_native.ps1",
            "versioned_runtime.py", "versioned_runtime_leases.py",
            "versioned_runtime_construction.py", "versioned_runtime_source.py",
            "versioned_runtime_publication.py",
            "resolve-runtime.sh", "resolve-runtime.ps1",
            "runtime-gate.sh", "runtime-gate.ps1",
            "delegated-waiter-callback.sh", "delegated-waiter-callback.ps1",
        ):
            shutil.copy2(SCRIPTS / relative, scripts / relative)
        shutil.copytree(SCRIPTS / "installation-context", scripts / "installation-context")
        if os.name != "nt":
            helper = scripts / "installation-context/installation-context.ps1"
            text = helper.read_text(encoding="utf-8")
            original = "$passwdHome = [CePosixAccount]::Home()"
            assert text.count(original) == 1
            helper.write_text(text.replace(original, "$passwdHome = $env:HOME"), encoding="utf-8")
        for ext in ("sh", "ps1"):
            (scripts / f"install.{ext}").write_bytes(script(ext).encode("utf-8"))
        package = payload / "src" / "agent_dispatch"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        for module in ("action_policy.py", "action_controls.py", "install_paths.py"):
            shutil.copy2(PLUGIN / "src" / "agent_dispatch" / module, package / module)
        (package / "embody.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(
            'import sys\nfrom pathlib import Path\nif __name__ == "__main__":\n'
            '    print("agent-dispatch " + Path(sys.prefix).name)\n', encoding="utf-8",
        )
        (payload / "plugin.json").write_text(
            json.dumps({
                "name": "agent-dispatch", "version": PACKAGE,
                "repository": "https://github.com/ThomasMichon/copilot-extensions",
            }), encoding="utf-8",
        )
        (payload / "pyproject.toml").write_text(
            f'[project]\nname = "agent-dispatch"\nversion = "{PACKAGE}"\n',
            encoding="utf-8",
        )
        digest = api.assembly_hash(payload)
        identity = api.SourceIdentity(
            "agent-dispatch", PACKAGE, commit, "b" * 40, digest,
            api.runtime_version(PACKAGE, commit, digest),
        )
        (payload / "runtime-source.json").write_text(
            json.dumps(identity.document()), encoding="utf-8",
        )
        slot = root / "versions" / identity.runtime_version
        subprocess.run(
            [sys._base_executable, "-I", "-m", "venv", "--without-pip", str(slot)],
            env=env, capture_output=True, text=True, check=True, timeout=30, creationflags=HEADLESS,
        )
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        query = subprocess.run(
            [str(python), "-I", "-c", 'import sysconfig; print(sysconfig.get_path("purelib"))'],
            env=env, capture_output=True, text=True, check=True, timeout=15, creationflags=HEADLESS,
        )
        installed = Path(query.stdout.strip()) / "agent_dispatch"
        shutil.copytree(package, installed)
        full_identity = json.dumps(api.manifest_fields(identity), sort_keys=True)
        api._runtime().retain_runtime_helpers(slot, complete=False)
        api._runtime().mark_complete(
            root, identity.runtime_version,
            payload_hash=hashlib.sha256(full_identity.encode()).hexdigest(),
        )
        if broken_b and name == "b":
            (installed / "embody.py").write_text(
                "raise RuntimeError('broken fixture runtime')\n", encoding="utf-8",
            )
        result.append((payload, identity))
        print(
            f"publication fixture: staged {name}, elapsed={time.monotonic() - started:.3f}s",
            file=sys.stderr, flush=True,
        )
    return env, root, result


def activate(env: dict[str, str], root: Path, version: str) -> None:
    slot = root / "versions" / version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(python), "-I", "-c", "import agent_dispatch, agent_dispatch.embody, agent_dispatch.__main__"],
        env=env, capture_output=True, text=True, check=True, timeout=15, creationflags=HEADLESS,
    )
    subprocess.run(
        [sys._base_executable, "-I", "-B", str(SCRIPTS / "versioned_runtime.py"),
         "--root", str(root), "--link-name", ".venv", "activate", version, "--no-link"],
        env=env, capture_output=True, text=True, check=True, timeout=15, creationflags=HEADLESS,
    )


def run(
    ext: str, entry: Path, args: list[str], env: dict[str, str], *, cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    from agent_dispatch.procutil import run_background_capture

    executable = (
        (shutil.which("pwsh") or shutil.which("powershell"))
        if ext == "ps1" else shutil.which("bash")
    )
    if not executable:
        pytest.skip(f"{ext} unavailable")
    prefix = ["-NoProfile", "-NonInteractive", "-File"] if ext == "ps1" else []
    started = time.monotonic()
    print(
        f"publication fixture: begin {ext} {entry} {args}; "
        f"python={shutil.which('python', path=env.get('PATH'))}; "
        f"py={shutil.which('py', path=env.get('PATH'))}",
        file=sys.stderr, flush=True,
    )
    result = run_background_capture(
        [executable, *prefix, str(entry), *args], env=env, cwd=cwd, timeout=90,
        max_output=65536,
    )
    assert result is not None, "publication fixture exceeded 90s or could not start"
    print(
        f"publication fixture: end code={result.returncode}, elapsed={time.monotonic() - started:.3f}s\n"
        + result.stderr,
        file=sys.stderr, flush=True,
    )
    return result
