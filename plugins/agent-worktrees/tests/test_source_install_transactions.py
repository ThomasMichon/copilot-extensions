"""Native source selection and publication without changing installed runtimes."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import ModuleType

import pytest

from test_installer_slot_clean_posix import _BASH

pytestmark = pytest.mark.guard
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
VERSION = "1.2.3"
HOSTS = [
    host for host in (
        shutil.which("pwsh"),
        shutil.which("powershell.exe") if os.name == "nt" else None,
    ) if host
]


def _source_module() -> ModuleType:
    name = "worktrees_source_fixture"
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "versioned_runtime_source.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _payload(root: Path) -> tuple[Path, dict[str, object]]:
    payload = root / "payload"
    (payload / "src").mkdir(parents=True)
    (payload / "src" / "fixture.txt").write_text("original", encoding="utf-8")
    (payload / "tests").mkdir()
    (payload / "tests" / "hashed.txt").write_text("retained", encoding="utf-8")
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": VERSION}), encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        f'[project]\nname="agent-worktrees"\nversion="{VERSION}"\n', encoding="utf-8",
    )
    source = _source_module()
    digest = source.assembly_hash(payload)
    receipt = source.SourceIdentity(
        "agent-worktrees", VERSION, "a" * 40, "b" * 40, digest,
        source.runtime_version(VERSION, "a" * 40, digest),
    ).document()
    (payload / "runtime-source.json").write_text(json.dumps(receipt), encoding="utf-8")
    return payload, receipt


def _function(name: str, suffix: str) -> str:
    text = (SCRIPTS / f"install.{suffix}").read_text(encoding="utf-8")
    if suffix == "sh":
        start = text.index(f"\n{name}() {{")
        return text[start:text.index("\n}\n\n", start) + 3]
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
    return shlex.quote(value)


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        timeout=40, check=False,
        env={**os.environ, "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _ps_setup(payload: Path, root: Path, *, real_bootstrap: bool = False) -> str:
    bootstrap = (
        _function("Get-ApplicationPath", "ps1") + "\n" + _function("Get-BootstrapPython", "ps1")
        if real_bootstrap else
        f"function Get-BootstrapPython {{ param([switch]$ExcludeRuntimeRoot, [switch]$ExcludeVenvDir) return {_ps(sys.executable)} }}"
    )
    return f"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PluginDir = {_ps(payload)}
$ScriptDir = {_ps(SCRIPTS)}
$InstallDir = {_ps(root / 'install')}
$LocalBin = {_ps(root / 'bin')}
$ServiceName = 'agent-worktrees'
$SrcVersion = '{VERSION}'
$VenvDir = Join-Path $InstallDir 'legacy'
$LinkDir = $VenvDir
$VersionedRuntime = $true
{bootstrap}
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
. (Join-Path $ScriptDir 'source-runtime.ps1')
{_function('Get-PayloadHash', 'ps1')}
{_function('Invoke-VersionedSlotClean', 'ps1')}
{_function('Invoke-VersionedMarkComplete', 'ps1')}
function Start-RuntimeConstruction {{
    param($Python,$Helper,$Root,$Version,$PayloadHash,$LinkName)
    if ($MutateAtLock) {{ [IO.File]::AppendAllText((Join-Path $PluginDir 'src\\fixture.txt'), 'changed') }}
    return $true
}}
function Complete-RuntimeConstruction {{ param($PayloadHash) throw 'Completion must not run after mutation' }}
function Ensure-InstallDir($Path) {{ [void][IO.Directory]::CreateDirectory($Path) }}
function Write-ServiceHeader {{ }}
function Write-ServiceOk {{ }}
function Deploy-GlobalBinstub {{ [IO.File]::WriteAllText((Join-Path $LocalBin 'agent-worktrees.cmd'), 'fixture') }}
{_function('Write-Step', 'ps1')}
{_function('Invoke-Stamp', 'ps1')}
Initialize-SourceRuntime
"""


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "reselected"])
def test_source_manifest_uses_precommit_metadata_after_origin_changes(
    tmp_path: Path, host: str | None, legacy: bool,
) -> None:
    if not host:
        pytest.skip("PowerShell unavailable")
    payload, receipt = _payload(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    version = receipt["runtime_version"]
    assert isinstance(version, str)
    slot = root / "versions" / version
    slot.mkdir(parents=True)
    link = root / ".venv"
    if legacy:
        link.mkdir()
        (link / "legacy.txt").write_text("not the selected runtime", encoding="utf-8")
    script = tmp_path / "manifest.ps1"
    script.write_text(
        _ps_setup(payload, tmp_path) + f"""
$ContextualInstall = $true
$env:COMPUTERNAME = 'fixture-host'
$env:COPILOT_PLUGIN_STAGED_FROM = $null
function Resolve-Machine {{ return 'fixture-host' }}
function Get-SourceKind {{ param($PluginPath) return 'marketplace' }}
{_function('New-V3Manifest', 'ps1')}
{_function('Write-V3Manifest', 'ps1')}
$VenvPython = {_ps(sys.executable)}
$LinkDir = {_ps(link)}
$script:SourceRuntimeManifest = New-V3Manifest
$script:SourceRuntimeManifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 {_ps(tmp_path / 'prepared.json')}
[IO.File]::WriteAllText((Join-Path $PluginDir 'runtime-source.json'), 'invalid origin')
[IO.File]::WriteAllText((Join-Path $PluginDir 'pyproject.toml'), 'version="9.9.9"')
Write-V3Manifest
""", encoding="utf-8",
    )
    result = _run([host, "-NoProfile", "-File", str(script)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest == json.loads((tmp_path / "prepared.json").read_text(encoding="utf-8-sig"))
    assert manifest["runtime_version"] == receipt["runtime_version"]
    assert manifest["runtime_source"] == receipt
    assert manifest["source"]["version"] == VERSION
    assert manifest["source"]["path"] == str(payload).replace("\\", "/")
    assert Path(manifest["venv"]).resolve() == slot.resolve()
    if legacy:
        assert (link / "legacy.txt").read_text() == "not the selected runtime"
    else:
        assert not link.exists()


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "reselected"])
def test_bash_source_manifest_uses_precommit_metadata_after_origin_changes(tmp_path: Path, legacy: bool) -> None:
    if not _BASH:
        pytest.skip("Bash unavailable")
    payload, receipt = _payload(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    version = receipt["runtime_version"]
    assert isinstance(version, str)
    slot = root / "versions" / version
    slot.mkdir(parents=True)
    link = root / ".venv"
    if legacy:
        link.mkdir()
        (link / "legacy.txt").write_text("not the selected runtime", encoding="utf-8")
    script = tmp_path / "manifest.sh"
    script.write_text(f"""
set -euo pipefail
PLUGIN_DIR={_sh(payload)}
SCRIPT_DIR={_sh(SCRIPTS)}
INSTALL_DIR={_sh(root)}
SRC_VERSION={VERSION}
VENV_PYTHON={_sh(sys.executable)}
LINK_DIR={_sh(link)}
CONTEXTUAL_INSTALL=true
unset COPILOT_PLUGIN_STAGED_FROM
_bootstrap_python() {{ printf '%s\\n' {_sh(sys.executable)}; }}
resolve_machine() {{ printf fixture-host; }}
detect_platform() {{ printf linux; }}
_source_kind() {{ printf marketplace; }}
err() {{ printf '%s\\n' "$*" >&2; }}
ok() {{ :; }}
source "$SCRIPT_DIR/source-runtime.sh"
{_function('_build_deploy_manifest_json', 'sh')}
{_function('write_deploy_manifest', 'sh')}
_initialize_source_runtime
VENV_PYTHON={_sh(sys.executable)}
SOURCE_RUNTIME_MANIFEST="$(_build_deploy_manifest_json)"
printf '%s\\n' "$SOURCE_RUNTIME_MANIFEST" > {_sh(tmp_path / 'prepared.json')}
printf 'invalid origin' > "$PLUGIN_DIR/runtime-source.json"
printf 'version="9.9.9"' > "$PLUGIN_DIR/pyproject.toml"
write_deploy_manifest
""", encoding="utf-8")
    result = _run([_BASH, str(script)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
    assert manifest == json.loads((tmp_path / "prepared.json").read_text(encoding="utf-8"))
    assert manifest["runtime_version"] == receipt["runtime_version"]
    assert manifest["runtime_source"] == receipt
    assert manifest["source"]["version"] == VERSION
    assert Path(manifest["source"]["path"]).name == payload.name
    manifest_path = manifest["venv"]
    if os.name == "nt" and manifest_path.startswith("/"):
        manifest_path = manifest_path[1] + ":" + manifest_path[2:]
    assert Path(manifest_path).resolve() == slot.resolve()
    if legacy:
        assert (link / "legacy.txt").read_text() == "not the selected runtime"
    else:
        assert not link.exists()


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("boundary", ("lock", "completion", "receipt"))
def test_native_source_refusal_preserves_previous(
    tmp_path: Path, host: str | None, boundary: str,
) -> None:
    if not host:
        pytest.skip("PowerShell unavailable")
    payload, _ = _payload(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    previous = root / "current-version"
    previous.write_text("1.2.2", encoding="utf-8")
    mutation = (
        "[IO.File]::WriteAllText((Join-Path $PluginDir 'runtime-source.json'), "
        "((Get-Content (Join-Path $PluginDir 'runtime-source.json') -Raw).Replace(('b'*40),('c'*40))))"
        if boundary == "receipt" else
        "[IO.File]::AppendAllText((Join-Path $PluginDir 'src\\fixture.txt'), 'changed')"
    )
    script = tmp_path / "case.ps1"
    script.write_text(
        _ps_setup(payload, tmp_path) + f"""
$MutateAtLock = ${str(boundary == 'lock').lower()}
try {{
    {'' if boundary == 'lock' else mutation}
    {'[void](Invoke-VersionedSlotClean)' if boundary == 'lock' else '[void](Invoke-VersionedMarkComplete)'}
    throw 'Unexpected source admission'
}} catch {{
    if ($_.Exception.Message -notmatch 'Source runtime identity') {{ throw }}
    'refused'
}}
""", encoding="utf-8",
    )
    result = _run([host, "-NoProfile", "-File", str(script)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refused" in result.stdout
    assert previous.read_text(encoding="utf-8") == "1.2.2"
    assert not list(root.rglob(".install-complete.json"))


@pytest.mark.parametrize("host", HOSTS or [None])
def test_native_source_snapshot_reuse_and_corruption(tmp_path: Path, host: str | None) -> None:
    if not host:
        pytest.skip("PowerShell unavailable")
    payload, receipt = _payload(tmp_path)
    script = tmp_path / "snapshot.ps1"
    script.write_text(
        _ps_setup(payload, tmp_path)
        + "Invoke-Stamp\n$first = (Get-Content (Join-Path $InstallDir 'payload-dir') -Raw).Trim()\n"
        + "Invoke-Stamp\n$second = (Get-Content (Join-Path $InstallDir 'payload-dir') -Raw).Trim()\n"
        + "if ($first -cne $second) { throw 'Snapshot changed' }\n"
        + "Assert-SourceRuntime -Payload $first\nWrite-Output $first\n",
        encoding="utf-8",
    )
    result = _run([host, "-NoProfile", "-File", str(script)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = tmp_path / "install" / "snapshots" / str(receipt["runtime_version"])
    original = {str(p.relative_to(snapshot)): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
    assert original["tests\\hashed.txt" if os.name == "nt" else "tests/hashed.txt"] == b"retained"
    (snapshot / "src" / "fixture.txt").write_text("corrupted", encoding="utf-8")
    failed = _run([host, "-NoProfile", "-File", str(script)], tmp_path)
    assert failed.returncode != 0
    assert "Source" in failed.stdout + failed.stderr
    assert (snapshot / "src" / "fixture.txt").read_text(encoding="utf-8") == "corrupted"
    assert not (snapshot / str(receipt["runtime_version"])).exists()


@pytest.mark.parametrize("marker", ["payload-dir", "stamped-version"])
def test_bash_stamp_does_not_advance_after_redirect_failure(tmp_path: Path, marker: str) -> None:
    if not _BASH:
        pytest.skip("Native Bash unavailable")
    payload, _ = _payload(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    other = "stamped-version" if marker == "payload-dir" else "payload-dir"
    (root / marker).mkdir()
    (root / marker / "sentinel").write_text("preserved", encoding="utf-8")
    (root / other).write_text("previous", encoding="utf-8")
    script = tmp_path / "failure.sh"
    script.write_text(f"""
set -euo pipefail
PLUGIN_DIR={_sh(payload)}
SCRIPT_DIR={_sh(SCRIPTS)}
INSTALL_DIR={_sh(root)}
SRC_VERSION='{VERSION}'
_bootstrap_python() {{ printf '%s\\n' {_sh(sys.executable)}; }}
err() {{ printf '%s\\n' "$*" >&2; }}
. "$SCRIPT_DIR/source-runtime.sh"
_initialize_source_runtime
if _stamp_source_snapshot; then exit 92; fi
""", encoding="utf-8")
    result = _run([_BASH, _sh(script).strip("'")], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Source redirect must be an ordinary file" in result.stderr
    assert (root / other).read_text(encoding="utf-8") == "previous"
    assert sorted(path.name for path in (root / marker).iterdir()) == ["sentinel"]
    assert (root / marker / "sentinel").read_text() == "preserved"
    assert not (root / "snapshots").exists()


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("marker", ["payload-dir", "stamped-version"])
def test_native_stamp_redirect_collision_preserves_other_pointer(
    tmp_path: Path, host: str | None, marker: str,
) -> None:
    if not host:
        pytest.skip("PowerShell unavailable")
    payload, _ = _payload(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    other = "stamped-version" if marker == "payload-dir" else "payload-dir"
    (root / marker).mkdir()
    (root / marker / "sentinel").write_text("preserved", encoding="utf-8")
    (root / other).write_text("previous", encoding="utf-8")
    script = tmp_path / "stamp-collision.ps1"
    script.write_text(
        _ps_setup(payload, tmp_path) + """
try {
    Invoke-Stamp
    throw 'Unexpected stamp publication'
} catch {
    if ($_.Exception.Message -notmatch 'Source redirect must be an ordinary file') { throw }
    'refused'
}
""", encoding="utf-8",
    )
    result = _run([host, "-NoProfile", "-File", str(script)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refused" in result.stdout
    assert (root / other).read_text(encoding="utf-8") == "previous"
    assert sorted(path.name for path in (root / marker).iterdir()) == ["sentinel"]
    assert (root / marker / "sentinel").read_text() == "preserved"
    assert not (root / "snapshots").exists()
    assert not (tmp_path / "bin" / "agent-worktrees.cmd").exists()


@pytest.mark.skipif(os.name != "nt", reason="real Windows second-marker sharing lock")
@pytest.mark.parametrize("host", HOSTS or [None])
def test_native_source_stamp_locked_second_marker_preserves_both_pointers(
    tmp_path: Path, host: str | None,
) -> None:
    if not host:
        pytest.skip("PowerShell unavailable")
    payload, _ = _payload(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    (root / "payload-dir").write_bytes(b"previous payload\n")
    (root / "stamped-version").write_bytes(b"previous version\n")
    script = tmp_path / "locked-second.ps1"
    script.write_text(
        _ps_setup(payload, tmp_path) + """
$held = [IO.File]::Open((Join-Path $InstallDir 'stamped-version'), 'Open', 'Read', 'Read')
try {
    try {
        Invoke-Stamp
        throw 'Unexpected source redirect publication'
    } catch {
        if ($_.Exception.Message -notmatch '\\[WinError (5|32)\\]') { throw }
        if ($_.Exception.Message -notmatch 'stamped-version') { throw }
        if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')) -cne "previous payload`n") {
            throw 'First marker changed while second marker remained locked'
        }
        if ([IO.File]::ReadAllText((Join-Path $InstallDir 'stamped-version')) -cne "previous version`n") {
            throw 'Second marker changed while locked'
        }
        'refused'
    }
} finally { $held.Dispose() }
""", encoding="utf-8",
    )
    result = _run([host, "-NoProfile", "-File", str(script)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refused" in result.stdout
    assert (root / "payload-dir").read_bytes() == b"previous payload\n"
    assert (root / "stamped-version").read_bytes() == b"previous version\n"
    assert not (tmp_path / "bin" / "agent-worktrees.cmd").exists()


def test_bash_source_snapshot_and_receipt_mutation(tmp_path: Path) -> None:
    if not _BASH:
        pytest.skip("Native Bash unavailable")
    payload, receipt = _payload(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    script = tmp_path / "case.sh"
    script.write_text(f"""
set -euo pipefail
PLUGIN_DIR={_sh(payload)}
SCRIPT_DIR={_sh(SCRIPTS)}
INSTALL_DIR={_sh(root)}
SRC_VERSION='{VERSION}'
_bootstrap_python() {{ printf '%s\\n' {_sh(sys.executable)}; }}
err() {{ printf '%s\\n' "$*" >&2; }}
. "$SCRIPT_DIR/source-runtime.sh"
_initialize_source_runtime
_stamp_source_snapshot
_stamp_source_snapshot
{_sh(sys.executable)} -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["source_tree"]="c"*40; open(p,"w").write(json.dumps(d))' {_sh(payload / 'runtime-source.json')}
if _stamp_source_snapshot; then exit 92; fi
""", encoding="utf-8",
    )
    result = _run([_BASH, _sh(script).strip("'")], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "stamped-version").read_text().strip() == receipt["runtime_version"]
    snapshot = Path((root / "payload-dir").read_text().strip())
    if os.name == "nt":
        snapshot = root / "snapshots" / str(receipt["runtime_version"])
    assert (snapshot / "tests" / "hashed.txt").read_text() == "retained"
    assert not (snapshot / str(receipt["runtime_version"])).exists()


def test_bash_source_admission_before_nested_install_root_exists(tmp_path: Path) -> None:
    if not _BASH:
        pytest.skip("Native Bash unavailable")
    payload, receipt = _payload(tmp_path)
    root = tmp_path / "missing" / "parent" / "install"
    script = tmp_path / "nested.sh"
    script.write_text(f"""
set -euo pipefail
PLUGIN_DIR={_sh(payload)}
SCRIPT_DIR={_sh(SCRIPTS)}
INSTALL_DIR={_sh(root)}
SRC_VERSION='{VERSION}'
LINK_DIR="$INSTALL_DIR/.venv"
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
PATH={_sh(Path(sys.executable).parent)}:$PATH
err() {{ printf '%s\\n' "$*" >&2; }}
{_function('_bootstrap_python', 'sh')}
. "$SCRIPT_DIR/source-runtime.sh"
_initialize_source_runtime
_assert_source_runtime
printf '%s\\n' "$SRC_VERSION"
""", encoding="utf-8")
    result = _run([_BASH, _sh(script).strip("'")], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == receipt["runtime_version"]
    assert not root.exists()


@pytest.mark.parametrize("host", HOSTS or [None])
def test_native_failed_activation_preserves_redirects(tmp_path: Path, host: str | None) -> None:
    if not host:
        pytest.skip("PowerShell unavailable")
    redirect = tmp_path / "resolver"
    redirect.write_text("previous", encoding="utf-8")
    installer = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    body = installer.split("    'provision' {", 1)[1].split("    'install' {", 1)[0]
    body = body.rsplit("}", 1)[0]
    script = tmp_path / "provision.ps1"
    script.write_text(f"""
$ErrorActionPreference = 'Stop'
$ServiceName = 'fixture'
$InstallDir = {_ps(tmp_path / 'install')}
$BinDir = {_ps(tmp_path / 'bin')}
$LocalBin = {_ps(tmp_path / 'local-bin')}
{_function('Write-Step', 'ps1')}
function Write-ServiceHeader {{ }}
function Ensure-Uv {{ return $true }}
function Ensure-UvIndex {{ }}
function Ensure-InstallDir {{ }}
function Deploy-PythonRuntime {{ throw 'injected retained candidate failure' }}
function Deploy-RuntimeResolvers {{ [IO.File]::WriteAllText({_ps(redirect)}, 'changed'); return $true }}
{body}
""", encoding="utf-8")
    result = _run([host, "-NoProfile", "-File", str(script)], tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "injected retained candidate failure" in result.stderr
    assert redirect.read_text(encoding="utf-8") == "previous"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows window observation")
@pytest.mark.parametrize("host", HOSTS or [None])
def test_source_reader_windowless_parent_two_cycles(tmp_path: Path, host: str | None) -> None:
    if not host:
        pytest.skip("PowerShell unavailable")
    payload, _ = _payload(tmp_path)
    observer_path = (
        SCRIPTS.parents[2] / "libs" / "agent-procutil" / "tests"
        / "test_spawn_daemon_windows_integration.py"
    )
    spec = importlib.util.spec_from_file_location("worktrees_source_windows", observer_path)
    assert spec and spec.loader
    observer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = observer
    spec.loader.exec_module(observer)
    observer._SURFACED_PROCESS_NAMES.update({"py.exe", "pwsh.exe", "powershell.exe"})
    user32, kernel32, callback = observer._configure_win32()
    pythonw = Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")
    assert pythonw.is_file()
    script = tmp_path / "reader.ps1"
    script.write_text(
        _ps_setup(payload, tmp_path, real_bootstrap=True)
        + "Assert-SourceRuntime\nWrite-Output $SrcVersion\n", encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys\n"
        "for cycle in range(2):\n"
        "    result = subprocess.run([sys.argv[1], '-NoProfile', '-File', sys.argv[2]],\n"
        "        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)\n"
        "    if result.returncode:\n"
        "        raise RuntimeError(result.stdout + result.stderr)\n"
        "    assert '+source.' in result.stdout, result.stdout\n",
        encoding="utf-8",
    )
    log = tmp_path / "parent.log"
    with log.open("w", encoding="utf-8") as output:
        with observer._WindowObserver(user32, kernel32, callback) as windows:
            process = subprocess.Popen(
                [str(pythonw), "-I", "-B", str(parent), host, str(script)],
                stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            result = process.wait(timeout=40)
    assert result == 0, log.read_text(encoding="utf-8")
    assert not windows.surfaced, windows.surfaced
    assert not windows.focus_changes, windows.focus_changes


def test_release_materialization_preserves_native_source_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = SCRIPTS.parents[2]
    monkeypatch.syspath_prepend(str(root / "tools"))
    import materialize_main

    destination = tmp_path / "release"
    payload = destination / "plugins" / "agent-worktrees"
    shutil.copytree(
        SCRIPTS.parent, payload,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "build", "*.egg-info"),
    )
    materialize_main.materialize(destination, canonical_root=root)
    for name in ("source-runtime.sh", "source-runtime.ps1", "versioned_runtime_native.ps1"):
        assert (payload / "scripts" / name).read_bytes() == (SCRIPTS / name).read_bytes()
    assert 'editable = true' not in (payload / "pyproject.toml").read_text(encoding="utf-8")
