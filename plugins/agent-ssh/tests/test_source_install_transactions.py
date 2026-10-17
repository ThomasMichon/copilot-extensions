"""Native source receipt boundaries, without installing packages or touching services."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard
ROOT = Path(__file__).resolve().parents[3]
VERSION = "1.2.3"
PLUGINS = ("agent-ssh", "agent-machines")
HOSTS = [
    pytest.param(host, id=name, marks=pytest.mark.skipif(not host, reason=f"{name} unavailable"))
    for name, host in (
        ("pwsh", shutil.which("pwsh")),
        ("powershell", shutil.which("powershell") if os.name == "nt" else None),
    )
]


def _scripts(plugin: str) -> Path:
    return ROOT / "plugins" / plugin / "scripts"


def _entry(plugin: str) -> str:
    return "install" if plugin == "agent-ssh" else "init"


def _identity_module(plugin: str):
    name = "fixture_source_" + plugin.replace("-", "_")
    spec = importlib.util.spec_from_file_location(
        name, _scripts(plugin) / "versioned_runtime_source.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _function(plugin: str, name: str, suffix: str) -> str:
    text = (_scripts(plugin) / f"{_entry(plugin)}.{suffix}").read_text(encoding="utf-8")
    if suffix == "sh":
        start = text.index(f"\n{name}() {{")
        return text[start:text.index("\n}\n", start) + 3]
    start = text.index(f"function {name} {{")
    depth = 0
    for index in range(text.index("{", start), len(text)):
        depth += (text[index] == "{") - (text[index] == "}")
        if depth == 0:
            return text[start:index + 1].replace("$PSScriptRoot", "$ScriptDir")
    raise AssertionError(name)


def _ps(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sh(value: Path | str) -> str:
    value = str(value).replace("\\", "/")
    if os.name == "nt" and len(value) > 1 and value[1] == ":":
        value = "/" + value[0].lower() + value[2:]
    return "'" + value.replace("'", "'\\''") + "'"


def _bash() -> str:
    git = shutil.which("git")
    candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
    bash = (
        str(candidate) if os.name == "nt" and candidate and candidate.is_file()
        else shutil.which("bash")
    )
    if not bash:
        pytest.skip("native Bash unavailable")
    return bash


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PATH"] = (
        str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    )
    environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    environment.pop("AGENT_MACHINES_EXPECTED_SOURCE_IDENTITY", None)
    return subprocess.run(  # noqa: S603 - fixture-owned native executable and harness
        argv, cwd=cwd, env=environment, capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _payload(tmp_path: Path, plugin: str) -> tuple[Path, dict]:
    payload = tmp_path / "payload"
    (payload / "scripts").mkdir(parents=True)
    for source in _scripts(plugin).glob("versioned_runtime*.*"):
        shutil.copyfile(source, payload / "scripts" / source.name)
    for suffix in ("ps1", "sh"):
        source = _scripts(plugin) / f"{_entry(plugin)}.{suffix}"
        shutil.copyfile(source, payload / "scripts" / source.name)
    for directory in ("src", "tests", "node_modules"):
        (payload / directory).mkdir()
        (payload / directory / "hashed.txt").write_text("original\n", encoding="utf-8")
    (payload / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": VERSION}), encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "{plugin}"\nversion = "{VERSION}"\n', encoding="utf-8",
    )
    module = _identity_module(plugin)
    digest = module.assembly_hash(payload)
    receipt = module.SourceIdentity(
        plugin, VERSION, "a" * 40, "b" * 40, digest,
        module.runtime_version(VERSION, "a" * 40, digest),
    ).document()
    (payload / "runtime-source.json").write_text(json.dumps(receipt), encoding="utf-8")
    return payload, receipt


def _previous(tmp_path: Path) -> dict[str, bytes]:
    previous = {
        "current-version": b"1.2.2",
        "deploy-manifest.json": b'{"source":{"version":"1.2.2"}}',
        "payload-dir": b"previous-payload",
        "stamped-version": b"1.2.2",
        "versions/candidate/preserve": b"unrelated candidate",
    }
    for name, value in previous.items():
        target = tmp_path / "install" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    return previous


def _assert_previous(tmp_path: Path, previous: dict[str, bytes]) -> None:
    for name, value in previous.items():
        assert (tmp_path / "install" / name).read_bytes() == value, name
    assert not list((tmp_path / "install" / "versions").glob("*/.install-complete.json"))


def _ps_setup(tmp_path: Path, plugin: str, payload: Path) -> str:
    names = (
        "Get-SourceRuntimeIdentity", "Initialize-SourceRuntime", "Assert-SourceRuntimeIdentity",
        "Invoke-VersionedSlotClean", "Invoke-VersionedMarkComplete",
    )
    return f"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$PluginDir = {_ps(payload)}
$ScriptDir = Join-Path $PluginDir 'scripts'
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
$InstallDir = {_ps(tmp_path / 'install')}
$LocalBin = {_ps(tmp_path / 'bin')}
$SrcVersion = '{VERSION}'
$PackageVersion = $SrcVersion
$VenvDir = Join-Path $InstallDir 'legacy'
$LinkDir = $VenvDir
$VersionedRuntime = $true
$RealPython = {_ps(sys.executable)}
$Events = {_ps(tmp_path / 'events')}
function Rec($value) {{ Add-Content -LiteralPath $Events -Value $value }}
function Get-BootstrapPython {{ return $RealPython }}
function Get-PayloadHash {{ return 'fixture-construction-hash' }}
function Start-RuntimeConstruction {{
    param($Python, $Helper, $Root, $Version, $PayloadHash, $LinkName)
    Rec "BEGIN:$Version"
    if ($Boundary -eq 'lock') {{ Mutate }}
    return $Reuse
}}
function Complete-RuntimeConstruction {{ param($PayloadHash) Rec 'COMPLETE' }}
{chr(10).join(_function(plugin, name, "ps1") for name in names)}
"""


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("boundary", ("lock", "completion", "reuse"))
@pytest.mark.parametrize("mutation", ("content", "tree", "remove", "appear"))
def test_source_ps_native_admission_preserves_previous_on_mutation(
    tmp_path: Path, plugin: str, host: str, boundary: str, mutation: str,
) -> None:
    payload, receipt = _payload(tmp_path, plugin)
    previous = _previous(tmp_path)
    saved = tmp_path / "receipt.json"
    saved.write_text(json.dumps(receipt), encoding="utf-8")
    if mutation == "appear":
        (payload / "runtime-source.json").unlink()
    mutations = {
        "content": "Add-Content -LiteralPath (Join-Path $PluginDir 'tests\\hashed.txt') 'changed'",
        "tree": """
$path = Join-Path $PluginDir 'runtime-source.json'
$data = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
$data.source_tree = 'cccccccccccccccccccccccccccccccccccccccc'
[IO.File]::WriteAllText($path, ($data | ConvertTo-Json))
""",
        "remove": "[IO.File]::Delete((Join-Path $PluginDir 'runtime-source.json'))",
        "appear": (
            f"Copy-Item -LiteralPath {_ps(saved)} "
            "-Destination (Join-Path $PluginDir 'runtime-source.json')"
        ),
    }
    harness = tmp_path / "transaction.ps1"
    harness.write_text(_ps_setup(tmp_path, plugin, payload) + f"""
$Boundary = '{boundary}'
$Reuse = {'$true' if boundary == 'reuse' else '$false'}
function Mutate {{ {mutations[mutation]} }}
Initialize-SourceRuntime
if ($Boundary -eq 'reuse') {{ Mutate }}
Invoke-VersionedSlotClean
if ($Boundary -eq 'completion') {{ Mutate }}
Invoke-VersionedMarkComplete
""", encoding="utf-8")
    result = _run([host, "-NoProfile", "-NonInteractive", "-File", str(harness)], tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "identity" in (result.stdout + result.stderr).lower()
    if (tmp_path / "events").exists():
        assert "COMPLETE" not in (tmp_path / "events").read_text()
    _assert_previous(tmp_path, previous)


def _sh_setup(tmp_path: Path, plugin: str, payload: Path) -> str:
    names = (
        "_source_python", "_source_runtime_identity", "_initialize_source_runtime",
        "_assert_source_runtime_identity",
        "_versioned_slot_clean", "_versioned_mark_complete",
    )
    return f"""
set -euo pipefail
PLUGIN_DIR={_sh(payload)}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={_sh(tmp_path / 'install')}
SRC_VERSION='{VERSION}'
PACKAGE_VERSION="$SRC_VERSION"
LINK_DIR="$INSTALL_DIR/legacy"
VERSIONED_RUNTIME=1
PY={_sh(sys.executable)}
EVENTS={_sh(tmp_path / 'events')}
_bootstrap_python() {{ printf '%s\\n' "$PY"; }}
_payload_hash() {{ printf fixture-construction-hash; }}
_fail() {{ printf '%s\\n' "$1" >&2; }}
_runtime_construction_begin() {{
    printf 'BEGIN:%s\\n' "$4" >> "$EVENTS"
    if [[ "$BOUNDARY" == lock ]]; then mutate; fi
    _RUNTIME_CONSTRUCTION_REUSE="$REUSE"
}}
_runtime_construction_complete() {{ printf 'COMPLETE\\n' >> "$EVENTS"; }}
{chr(10).join(_function(plugin, name, "sh") for name in names)}
"""


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("boundary", ("lock", "completion", "reuse"))
@pytest.mark.parametrize("mutation", ("content", "tree", "remove", "appear"))
def test_source_sh_native_admission_preserves_previous_on_mutation(
    tmp_path: Path, plugin: str, boundary: str, mutation: str,
) -> None:
    payload, receipt = _payload(tmp_path, plugin)
    previous = _previous(tmp_path)
    saved = tmp_path / "receipt.json"
    saved.write_text(json.dumps(receipt), encoding="utf-8")
    if mutation == "appear":
        (payload / "runtime-source.json").unlink()
    mutations = {
        "content": 'printf changed >> "$PLUGIN_DIR/tests/hashed.txt"',
        "tree": (
            '''"$PY" -I -c 'import json,sys; from pathlib import Path; '''
            '''p=Path(sys.argv[1]); d=json.loads(p.read_text()); '''
            '''d["source_tree"]="c"*40; p.write_text(json.dumps(d))' '''
            '''"$PLUGIN_DIR/runtime-source.json"'''
        ),
        "remove": 'rm "$PLUGIN_DIR/runtime-source.json"',
        "appear": f'cp {_sh(saved)} "$PLUGIN_DIR/runtime-source.json"',
    }
    harness = tmp_path / "transaction.sh"
    harness.write_text(_sh_setup(tmp_path, plugin, payload) + f"""
BOUNDARY='{boundary}'
REUSE={'1' if boundary == 'reuse' else '0'}
mutate() {{ {mutations[mutation]}; }}
_initialize_source_runtime
if [[ "$BOUNDARY" == reuse ]]; then mutate; fi
_versioned_slot_clean
if [[ "$BOUNDARY" == completion ]]; then mutate; fi
_versioned_mark_complete
""", encoding="utf-8")
    result = _run([_bash(), str(harness)], tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "identity" in (result.stdout + result.stderr).lower()
    if (tmp_path / "events").exists():
        assert "COMPLETE" not in (tmp_path / "events").read_text()
    _assert_previous(tmp_path, previous)


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("host", HOSTS)
def test_source_ps_native_reuse_keeps_package_and_exact_slot(
    tmp_path: Path, plugin: str, host: str,
) -> None:
    payload, receipt = _payload(tmp_path, plugin)
    _previous(tmp_path)
    harness = tmp_path / "reuse.ps1"
    harness.write_text(_ps_setup(tmp_path, plugin, payload) + f"""
$Boundary = ''
$Reuse = $true
Initialize-SourceRuntime
Invoke-VersionedSlotClean
Invoke-VersionedMarkComplete
if ($PackageVersion -cne '{VERSION}' -or $SrcVersion -cne '{receipt["runtime_version"]}' -or
    (Split-Path -Leaf $VenvDir) -cne $SrcVersion) {{ throw 'Incorrect generation selection' }}
""", encoding="utf-8")
    result = _run([host, "-NoProfile", "-NonInteractive", "-File", str(harness)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "COMPLETE" in (tmp_path / "events").read_text()


@pytest.mark.parametrize("plugin", PLUGINS)
def test_source_sh_native_reuse_keeps_package_and_exact_slot(tmp_path: Path, plugin: str) -> None:
    payload, receipt = _payload(tmp_path, plugin)
    _previous(tmp_path)
    harness = tmp_path / "reuse.sh"
    harness.write_text(_sh_setup(tmp_path, plugin, payload) + f"""
BOUNDARY=''
REUSE=1
_initialize_source_runtime
_versioned_slot_clean
_versioned_mark_complete
[[ "$PACKAGE_VERSION" == '{VERSION}' && "$SRC_VERSION" == '{receipt["runtime_version"]}' &&
   "${{VENV_DIR##*/}}" == "$SRC_VERSION" ]]
""", encoding="utf-8")
    result = _run([_bash(), str(harness)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "COMPLETE" in (tmp_path / "events").read_text()


@pytest.mark.parametrize("plugin", PLUGINS)
def test_source_sh_selection_skips_nonfunctional_python3(tmp_path: Path, plugin: str) -> None:
    payload, receipt = _payload(tmp_path, plugin)
    candidates = tmp_path / "interpreters"
    candidates.mkdir()
    broken = candidates / "python3"
    broken.write_text("#!/usr/bin/env bash\nexit 91\n", encoding="utf-8")
    broken.chmod(0o755)
    working = candidates / "python"
    working.write_text(
        f'#!/usr/bin/env bash\nexec {_sh(sys.executable)} "$@"\n', encoding="utf-8",
    )
    working.chmod(0o755)
    harness = tmp_path / "source-python.sh"
    harness.write_text(_sh_setup(tmp_path, plugin, payload) + f"""
export PATH={_sh(candidates)}:"$PATH"
_initialize_source_runtime
_assert_source_runtime_identity
[[ "$SRC_VERSION" == '{receipt["runtime_version"]}' ]]
""", encoding="utf-8")
    result = _run([_bash(), str(harness)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("corrupt_copy", (False, True))
def test_source_ps_stamp_preserves_hashed_closure_and_refuses_corruption(
    tmp_path: Path, plugin: str, host: str, corrupt_copy: bool,
) -> None:
    payload, _receipt = _payload(tmp_path, plugin)
    previous = _previous(tmp_path)
    names = ("Invoke-Stamp",)
    if plugin == "agent-ssh":
        names += ("Get-SnapshotSourceMarkerPath", "Get-SnapshotVersionMarkerPath")
    functions = "\n".join(_function(plugin, name, "ps1") for name in names)
    real_identity = _function(plugin, "Get-SourceRuntimeIdentity", "ps1").replace(
        "function Get-SourceRuntimeIdentity {", "function Invoke-RealSourceIdentity {",
    )
    harness = tmp_path / "stamp.ps1"
    harness.write_text(_ps_setup(tmp_path, plugin, payload) + f"""
$DefaultInstallDir = $InstallDir
$probePayload = $PluginDir
$utf8NoBom = [Text.UTF8Encoding]::new($false)
function Write-Ok($value) {{ }}
function Write-Skip($value) {{ }}
function Write-Fail($value) {{ throw $value }}
function Write-Binstubs {{ }}
function Deploy-SelfProvisioningBinstub {{ }}
function Get-SourceKind {{ param($PluginPath) return 'local' }}
function Acquire-StampPublicationLock {{ return [IO.MemoryStream]::new() }}
function Enter-StampLock {{ return [Threading.Mutex]::new($true) }}
function Get-CurrentSnapshotPath {{ return '' }}
function Test-PublishedSnapshotIsNewer {{
    param($SnapshotDir, $SourcePath, $SourceVersion)
    return $false
}}
function Test-SnapshotReusable {{
    param($SnapshotDir, $SourceKind, $SourcePath, $SourceVersion)
    return $false
}}
function Publish-FileAtomically {{
    param($Path, $Content, $Encoding)
    [IO.File]::WriteAllText($Path, $Content, $Encoding)
}}
function Materialize-SnapshotLibs {{ throw 'Source closure must not be rematerialized' }}
function Materialize-SnapshotInstallerEngine {{ throw 'Source engine must not be rewritten' }}
{real_identity}
function Get-SourceRuntimeIdentity {{
    param([string]$Payload = $PluginDir, [string]$ExpectedVersion = '')
    if ({'$true' if corrupt_copy else '$false'} -and $Payload -ne $PluginDir) {{
        Add-Content -LiteralPath (Join-Path $Payload 'tests\\hashed.txt') 'corrupt copy'
    }}
    Invoke-RealSourceIdentity -Payload $Payload -ExpectedVersion $ExpectedVersion
}}
{functions}
Initialize-SourceRuntime
Invoke-Stamp
""", encoding="utf-8")
    result = _run([host, "-NoProfile", "-NonInteractive", "-File", str(harness)], tmp_path)
    if corrupt_copy:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "identity" in (result.stdout + result.stderr).lower()
        _assert_previous(tmp_path, previous)
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        snapshot = Path((tmp_path / "install" / "payload-dir").read_text().strip())
        for directory in ("tests", "node_modules"):
            assert (snapshot / directory / "hashed.txt").read_bytes() == (
                payload / directory / "hashed.txt"
            ).read_bytes()
        assert _identity_module(plugin).read_source_identity(snapshot, VERSION, plugin=plugin)


@pytest.mark.parametrize("corrupt_copy", (False, True))
def test_source_sh_ssh_stamp_preserves_hashed_closure_and_refuses_corruption(
    tmp_path: Path, corrupt_copy: bool,
) -> None:
    plugin = "agent-ssh"
    payload, _receipt = _payload(tmp_path, plugin)
    previous = _previous(tmp_path)
    text = (_scripts(plugin) / "install.sh").read_text(encoding="utf-8")
    start = text.index("# Cheap 'stamp':")
    stamp = text[start:text.index('if [[ "$ACTION" == "status" ]]', start)]
    identity = _function(plugin, "_source_runtime_identity", "sh").replace(
        "_source_runtime_identity()", "_real_source_runtime_identity()", 1,
    )
    harness = tmp_path / "stamp.sh"
    harness.write_text(_sh_setup(tmp_path, plugin, payload) + f"""
LOCAL_BIN={_sh(tmp_path / 'bin')}
ACTION=stamp
_acquire_stamp_publication_lock() {{ :; }}
_release_stamp_publication_lock() {{ :; }}
_exit_install_lock() {{ :; }}
_source_kind() {{ printf local; }}
_current_snapshot() {{ printf previous-payload; }}
_published_snapshot_is_newer() {{ return 1; }}
_snapshot_is_reusable() {{ return 1; }}
_deploy_binstub() {{ :; }}
_ok() {{ :; }}
_skip() {{ :; }}
_materialize_snapshot_vendored_libs() {{ printf 'unexpected rematerialization' >&2; return 81; }}
_materialize_snapshot_installer_engine() {{ printf 'unexpected engine rewrite' >&2; return 82; }}
{_function(plugin, "_write_stamped_version_marker", "sh")}
{identity}
_source_runtime_identity() {{
    if [[ {'1' if corrupt_copy else '0'} == 1 && "${{1:-$PLUGIN_DIR}}" != "$PLUGIN_DIR" ]]; then
        printf corrupt >> "$1/tests/hashed.txt"
    fi
    _real_source_runtime_identity "$@"
}}
_initialize_source_runtime
{stamp}
""", encoding="utf-8")
    result = _run([_bash(), str(harness)], tmp_path)
    if corrupt_copy:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "identity" in (result.stdout + result.stderr).lower()
        _assert_previous(tmp_path, previous)
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        snapshot_text = (tmp_path / "install" / "payload-dir").read_text().strip()
        if os.name == "nt" and snapshot_text.startswith("/"):
            snapshot_text = snapshot_text[1].upper() + ":" + snapshot_text[2:]
        snapshot = Path(snapshot_text)
        assert _identity_module(plugin).read_source_identity(snapshot, VERSION, plugin=plugin)
        for directory in ("tests", "node_modules"):
            assert (snapshot / directory / "hashed.txt").read_bytes() == (
                payload / directory / "hashed.txt"
            ).read_bytes()
