"""Execute installer transaction boundaries against real source identities."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "plugins" / "agent-bridge" / "scripts"
VERSION = "1.2.3"
HOSTS = [
    pytest.param(host, id=name, marks=pytest.mark.skipif(not host, reason=f"{name} unavailable"))
    for name, host in (
        ("pwsh", shutil.which("pwsh")),
        ("powershell", shutil.which("powershell") if os.name == "nt" else None),
    )
]
spec = importlib.util.spec_from_file_location(
    "transaction_source_identity", ROOT / "libs" / "versioned-runtime" / "versioned_runtime_source.py",
)
assert spec and spec.loader
identity_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = identity_module
spec.loader.exec_module(identity_module)


def _ps(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sh(value: Path | str) -> str:
    text = str(value).replace("\\", "/")
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return "'" + text.replace("'", "'\\''") + "'"


def _bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
    return str(candidate) if candidate is not None and candidate.is_file() else None


def _function(name: str, suffix: str) -> str:
    text = (SCRIPTS / f"install.{suffix}").read_text(encoding="utf-8")
    if suffix == "sh":
        start = text.index(f"\n{name}() {{")
        return text[start:text.index("\n}\n", start) + 3]
    start = text.index(f"function {name} {{")
    depth = 0
    for index in range(text.index("{", start), len(text)):
        depth += (text[index] == "{") - (text[index] == "}")
        if depth == 0:
            return text[start:index + 1]
    raise AssertionError(f"Unbalanced function: {name}")


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        timeout=60, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _payload(tmp_path: Path) -> Path:
    payload = tmp_path / "payload"
    (payload / "scripts").mkdir(parents=True)
    for source in (ROOT / "libs" / "versioned-runtime").glob("versioned_runtime*.*"):
        shutil.copyfile(source, payload / "scripts" / source.name)
    for name in ("install.ps1", "install.sh"):
        shutil.copyfile(SCRIPTS / name, payload / "scripts" / name)
    (payload / "src").mkdir()
    (payload / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (payload / "tests").mkdir()
    (payload / "tests" / "hashed.txt").write_text("hashed test input\n", encoding="utf-8")
    (payload / "node_modules").mkdir()
    (payload / "node_modules" / "hashed.txt").write_text("also hashed\n", encoding="utf-8")
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-bridge", "version": VERSION}), encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "agent-bridge"\nversion = "{VERSION}"\n', encoding="utf-8",
    )
    digest = identity_module.assembly_hash(payload)
    identity = identity_module.SourceIdentity(
        "agent-bridge", VERSION, "a" * 40, "b" * 40, digest,
        identity_module.runtime_version(VERSION, "a" * 40, digest),
    )
    (payload / "runtime-source.json").write_text(
        json.dumps(identity.document()), encoding="utf-8",
    )
    return payload


def _previous(tmp_path: Path) -> dict[str, bytes]:
    install = tmp_path / "install"
    install.mkdir()
    previous = {
        "current-version": b"1.2.2",
        "deploy.json": b'{"version":"1.2.2"}',
        "payload-dir": b"previous-payload",
        "stamped-version": b"1.2.2",
        "redirect": b"previous-runtime",
        "versions/1.2.2/.install-complete.json": b"previous-completion",
    }
    for name, content in previous.items():
        path = install / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return previous


@pytest.mark.parametrize("host", HOSTS)
def test_native_manifest_uses_selected_source_after_origin_changes(tmp_path: Path, host: str) -> None:
    payload = _payload(tmp_path)
    selected = identity_module.read_source_identity(payload, VERSION)
    fields = identity_module.manifest_fields(selected)
    install = tmp_path / "install"
    install.mkdir()
    script = tmp_path / "manifest.ps1"
    script.write_text(f"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. {_ps(ROOT / 'libs' / 'installer-engine' / 'installer-engine.ps1')}
Set-Item -Path Function:Write-DeployManifestShared -Value ${{function:Write-DeployManifest}}
$PluginDir = {_ps(payload)}
$InstallDir = {_ps(install)}
$LinkDir = {_ps(install / 'runtime')}
$PackageVersion = '{VERSION}'
$SrcVersion = {_ps(fields['runtime_version'])}
$SourceRuntimeIdentity = {_ps(json.dumps(fields, sort_keys=True))}
$env:COPILOT_PLUGIN_STAGED_FROM = $null
$env:COMPUTERNAME = 'fixture-host'
function Get-SourceKind {{ param($PluginPath) return 'marketplace' }}
function Get-GitInfo {{ throw 'Marketplace source has no Git query' }}
function Get-SourceRuntimeIdentity {{ throw 'Mutable origin must not be revalidated after commit' }}
function Write-Ok {{ }}
{_function('Write-DeployManifest', 'ps1')}
[IO.File]::WriteAllText((Join-Path $PluginDir 'runtime-source.json'), 'invalid origin')
Write-DeployManifest -PayloadHash 'admitted-payload-hash'
""", encoding="utf-8")
    result = _run([host, "-NoProfile", "-File", str(script)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((install / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["runtime_source"] == fields["runtime_source"]
    assert manifest["runtime_version"] == fields["runtime_version"]
    assert manifest["source"]["content_hash"] == "admitted-payload-hash"


@pytest.mark.skipif(not _bash(), reason="Bash unavailable")
def test_bash_manifest_uses_selected_source_after_origin_changes(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    fields = identity_module.manifest_fields(identity_module.read_source_identity(payload, VERSION))
    install = tmp_path / "install"
    install.mkdir()
    script = tmp_path / "manifest.sh"
    script.write_text(f"""
set -euo pipefail
source {_sh(ROOT / 'libs' / 'installer-engine' / 'installer-engine.sh')}
PLUGIN_DIR={_sh(payload)}
INSTALL_DIR={_sh(install)}
VENV_DIR={_sh(install / 'runtime')}
PACKAGE_VERSION={VERSION}
SRC_VERSION={_sh(fields['runtime_version'])}
SOURCE_RUNTIME_IDENTITY={_sh(json.dumps(fields, sort_keys=True))}
PAYLOAD_HASH_FIXED=1
PAYLOAD_HASH=admitted-payload-hash
unset COPILOT_PLUGIN_STAGED_FROM
_source_kind() {{ printf marketplace; }}
_fail() {{ printf '%s\\n' "$*" >&2; }}
_ok() {{ :; }}
{_function('_git_info', 'sh')}
{_function('_payload_hash', 'sh')}
{_function('_write_deploy_manifest', 'sh')}
printf 'invalid origin' > "$PLUGIN_DIR/runtime-source.json"
_write_deploy_manifest
""", encoding="utf-8")
    result = _run([_bash(), str(script)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((install / "deploy-manifest.json").read_text(encoding="utf-8"))
    assert manifest["runtime_source"] == fields["runtime_source"]
    assert manifest["runtime_version"] == fields["runtime_version"]
    assert manifest["source"]["content_hash"] == "admitted-payload-hash"


def _assert_previous(tmp_path: Path, previous: dict[str, bytes]) -> None:
    for name, content in previous.items():
        assert (tmp_path / "install" / name).read_bytes() == content, name
    assert not list((tmp_path / "install" / "versions").glob("1.2.3*/.install-complete.json"))


def _ps_setup(tmp_path: Path, payload: Path) -> str:
    names = (
        "Get-SourceRuntimeIdentity", "Initialize-SourceRuntime", "Get-UvSourcePaths", "Get-PayloadLibRoots",
        "Get-PayloadHash", "Test-PayloadUnchangedSinceAdmission",
        "Invoke-Install", "Invoke-Update", "Invoke-Stamp",
    )
    return f"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$PluginDir = {_ps(payload)}
$ScriptDir = Join-Path $PluginDir 'scripts'
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
$InstallDir = {_ps(tmp_path / 'install')}
$LocalBin = {_ps(tmp_path / 'bin')}
$PackageVersion = '{VERSION}'
$SrcVersion = $PackageVersion
$SourceRuntimeIdentity = $null
$VersionedRuntime = $true
$publishGlobalBinstubs = $false
$Events = {_ps(tmp_path / 'events')}
$RealPython = {_ps(sys.executable)}
function Rec($value) {{ Add-Content -LiteralPath $Events -Value $value }}
function Get-BootstrapPython {{ return $RealPython }}
function Write-Fail($value) {{ Write-Host $value }}
function Write-Ok($value) {{
    if ($value -eq 'Package updated' -and $Boundary -eq 'completion') {{ Mutate }}
}}
function Write-Skip($value) {{ }}
function Write-Step($value) {{ }}
function Write-Warn($value) {{ }}
function Ensure-Uv {{ return $true }}
function Invoke-MigrationCheck {{ }}
function Resolve-VendoredLib {{ param($LibName) return $null }}
function Test-SlotContentCurrent {{ param($PayloadHash) return $false }}
function Get-SlotAdmission {{ param($PayloadHash) Rec 'ADMIT'; return 'construct' }}
function Invoke-VersionedSlotClean {{
    param($PayloadHash)
    $script:RuntimeConstructionReuse = $false
}}
function Enter-InstallLock {{ Rec 'LOCK'; if ($Boundary -eq 'lock') {{ Mutate }}; return $true }}
function Exit-InstallLock {{ }}
function Invoke-InstallSlotBuild {{ Rec 'BUILD'; if ($Boundary -eq 'build') {{ Mutate }} }}
function Invoke-UpdateSlotBuild {{ param($UseCutover) Invoke-InstallSlotBuild }}
function Test-RuntimeHealthy {{
    param($Python)
    if ($Action -eq 'Invoke-Install' -and $Boundary -eq 'completion') {{ Mutate }}
    return $true
}}
function Test-ActiveIsForward {{ return $false }}
function Get-RunningProcess {{ return [pscustomobject]@{{ Id = 123 }} }}
function Get-ActiveSignature {{ return 'previous-serving-runtime' }}
function Write-UpdateMarker {{ Rec 'UPDATE-MARKER' }}
function Invoke-UpdateDrainStop {{ Rec 'SERVICE'; throw 'Unexpected service stop' }}
function Invoke-UpdateStart {{ Rec 'SERVICE'; throw 'Unexpected service start' }}
function Clear-UpdateMarker {{ }}
function Get-VersionedCurrent {{ return '1.2.2' }}
function Remove-VenvBackup {{ }}
function Invoke-VersionedMarkComplete {{ param($PayloadHash) Rec 'COMPLETE'; return $true }}
function Invoke-VersionedActivate {{
    Rec 'ACTIVATE'
    Set-Content -LiteralPath (Join-Path $InstallDir 'current-version') $SrcVersion
    exit 51
}}
function Write-DeployManifest {{ param($PayloadHash) Rec 'MANIFEST' }}
function Write-Binstubs {{ Rec 'BINSTUB' }}
{chr(10).join(_function(name, 'ps1') for name in names)}
Initialize-SourceRuntime
"""


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(("action", "boundary", "stopped_predecessor"), [
    (action, boundary, False)
    for action in ("Invoke-Install", "Invoke-Update")
    for boundary in ("lock", "build", "completion")
] + [pytest.param("Invoke-Update", "completion", True, id="stopped-predecessor")])
@pytest.mark.parametrize("mutation", ["content", "identity"])
def test_source_ps_transaction_revalidates_before_publication(
    tmp_path: Path, host: str, action: str, boundary: str, stopped_predecessor: bool,
    mutation: str,
) -> None:
    payload = _payload(tmp_path)
    previous = _previous(tmp_path)
    predecessor_setup = ""
    if stopped_predecessor:
        selected = json.loads((payload / "runtime-source.json").read_text())["runtime_version"]
        previous["current-version"] = selected.encode()
        (tmp_path / "install" / "current-version").write_bytes(previous["current-version"])
        predecessor_setup = """
function Get-VersionedCurrent { return $SrcVersion }
function Invoke-UpdateDrainStop {
    param($Signature, $TimeoutSec)
    Rec "STOP:$Signature"
    return $true
}
function Invoke-UpdateStart {
    param($Signature, $Message)
    if ([IO.File]::ReadAllText((Join-Path $InstallDir 'current-version')) -ne $SrcVersion) {
        throw 'Restart selected the wrong predecessor'
    }
    Rec "RESTART:$Signature"
    return $true
}
"""
    mutation_body = (
        "Add-Content -LiteralPath (Join-Path $PluginDir 'tests\\hashed.txt') 'changed'"
        if mutation == "content" else """
$path = Join-Path $PluginDir 'runtime-source.json'
$document = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
$document.source_tree = 'cccccccccccccccccccccccccccccccccccccccc'
[IO.File]::WriteAllText($path, ($document | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
"""
    )
    harness = tmp_path / "transaction.ps1"
    harness.write_text(
        _ps_setup(tmp_path, payload) +
        f"\n$Action = '{action}'\n$Boundary = '{boundary}'\n"
        f"function Mutate {{ {mutation_body} }}\n{predecessor_setup}\n{action}\n",
        encoding="utf-8",
    )
    result = _run([host, "-NoProfile", "-NonInteractive", "-File", str(harness)], tmp_path)
    detail = result.stdout + result.stderr
    assert result.returncode == 1, detail
    assert "identity" in detail.lower(), detail
    events = (tmp_path / "events").read_text().split()
    assert "LOCK" in events
    assert ("ADMIT" in events) == (boundary != "lock")
    assert not {"COMPLETE", "ACTIVATE", "MANIFEST", "BINSTUB", "SERVICE"} & set(events)
    if stopped_predecessor:
        assert events.count("STOP:previous-serving-runtime") == 1, detail
        assert events.count("RESTART:previous-serving-runtime") == 1, detail
        assert events.index("STOP:previous-serving-runtime") < events.index("BUILD")
        assert events.index("BUILD") < events.index("RESTART:previous-serving-runtime")
    _assert_previous(tmp_path, previous)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("corrupt_snapshot", [False, True])
def test_source_stamp_preserves_assembly_for_first_use(
    tmp_path: Path, host: str, corrupt_snapshot: bool,
) -> None:
    payload = _payload(tmp_path)
    previous = _previous(tmp_path)
    harness = tmp_path / "stamp.ps1"
    harness.write_text(
        _ps_setup(tmp_path, payload) + f"""
$Action = 'Invoke-Install'
$Boundary = ''
function Get-BootstrapPython {{
    if ({'$true' if corrupt_snapshot else '$false'} -and (Get-Variable snapDir -ErrorAction SilentlyContinue)) {{
        Add-Content -LiteralPath (Join-Path $snapDir 'tests\\hashed.txt') 'corrupted copy'
    }}
    return $RealPython
}}
try {{ Invoke-Stamp }} catch {{ [Console]::Error.WriteLine($_.Exception.Message); exit 1 }}
$PluginDir = ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir'))).Trim()
$ScriptDir = Join-Path $PluginDir 'scripts'
Initialize-SourceRuntime
Invoke-Install
""",
        encoding="utf-8",
    )
    result = _run([host, "-NoProfile", "-NonInteractive", "-File", str(harness)], tmp_path)
    detail = result.stdout + result.stderr
    if corrupt_snapshot:
        assert result.returncode == 1, detail
        assert "payload bytes do not match the source identity" in detail, detail
        _assert_previous(tmp_path, previous)
    else:
        assert result.returncode == 51, detail
        snapshot = Path((tmp_path / "install" / "payload-dir").read_text().strip())
        assert (snapshot / "tests" / "hashed.txt").read_bytes() == (payload / "tests" / "hashed.txt").read_bytes()
        assert identity_module.read_source_identity(snapshot, VERSION, plugin="agent-bridge")
        events = (tmp_path / "events").read_text().split()
        assert all(event in events for event in ("LOCK", "ADMIT", "BUILD", "COMPLETE", "ACTIVATE"))


@pytest.mark.parametrize("host", HOSTS)
def test_bridge_source_stamp_reuses_the_exact_snapshot_files(tmp_path: Path, host: str) -> None:
    payload = _payload(tmp_path)
    _previous(tmp_path)
    harness = tmp_path / "stamp-reuse.ps1"
    harness.write_text(
        _ps_setup(tmp_path, payload) + "\n$Boundary = ''\nInvoke-Stamp\n", encoding="utf-8",
    )
    command = [host, "-NoProfile", "-NonInteractive", "-File", str(harness)]
    first = _run(command, tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    snapshot = Path((tmp_path / "install" / "payload-dir").read_text().strip())
    before = {
        path.relative_to(snapshot): (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
        for path in snapshot.rglob("*") if path.is_file()
    }
    second = _run(command, tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert {
        path.relative_to(snapshot): (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
        for path in snapshot.rglob("*") if path.is_file()
    } == before


def test_bridge_bash_source_stamp_reuses_its_complete_snapshot(tmp_path: Path) -> None:
    bash = _bash()
    if not bash:
        pytest.skip("Bash unavailable")
    payload = _payload(tmp_path)
    _previous(tmp_path)
    harness = tmp_path / "stamp-reuse.sh"
    harness.write_text(f"""
set -euo pipefail
PLUGIN_DIR={_sh(payload)}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={_sh(tmp_path / 'install')}
LOCAL_BIN={_sh(tmp_path / 'bin')}
PACKAGE_VERSION='{VERSION}'
SRC_VERSION="$PACKAGE_VERSION"
PUBLISH_GLOBAL_BINSTUBS=false
_bootstrap_python() {{ printf '%s' {_sh(sys.executable)}; }}
_fail() {{ printf '%s\\n' "$*" >&2; }}
_ok() {{ :; }}
deploy_binstub() {{ :; }}
{_function('_initialize_source_runtime', 'sh')}
{_function('_payload_unchanged_since_admission', 'sh')}
{_function('do_stamp', 'sh')}
_initialize_source_runtime
do_stamp
""", encoding="utf-8")
    command = [bash, _sh(harness).strip("'")]
    first = _run(command, tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    snapshot = tmp_path / "install" / "snapshots" / (
        json.loads((payload / "runtime-source.json").read_text())["runtime_version"]
    )
    before = {path.relative_to(snapshot): (path.stat().st_ino, path.read_bytes())
              for path in snapshot.rglob("*") if path.is_file()}
    second = _run(command, tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert {path.relative_to(snapshot): (path.stat().st_ino, path.read_bytes())
            for path in snapshot.rglob("*") if path.is_file()} == before
    assert (snapshot / "tests" / "hashed.txt").read_bytes() == (payload / "tests" / "hashed.txt").read_bytes()


@pytest.mark.parametrize(("action", "boundary", "stopped_predecessor"), [
    (action, boundary, False)
    for action in ("do_install", "do_update")
    for boundary in ("lock", "build", "completion")
] + [pytest.param("do_update", "completion", True, id="stopped-predecessor")])
@pytest.mark.parametrize("mutation", ["content", "identity"])
def test_source_sh_transaction_revalidates_before_publication(
    tmp_path: Path, action: str, boundary: str, stopped_predecessor: bool, mutation: str,
) -> None:
    bash = _bash()
    if not bash:
        pytest.skip("native bash unavailable")
    payload = _payload(tmp_path)
    previous = _previous(tmp_path)
    mutate = (
        "printf changed >> \"$PLUGIN_DIR/tests/hashed.txt\""
        if mutation == "content" else
        """\"$PY\" -I -c 'import json,sys; from pathlib import Path; p=Path(sys.argv[1]); d=json.loads(p.read_text()); d["source_tree"]="c"*40; p.write_text(json.dumps(d))' "$PLUGIN_DIR/runtime-source.json" """
    )
    names = ("_initialize_source_runtime", "_payload_unchanged_since_admission", action)
    harness = tmp_path / "transaction.sh"
    harness.write_text(f"""
set -euo pipefail
PLUGIN_DIR={_sh(payload)}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={_sh(tmp_path / 'install')}
LOCAL_BIN={_sh(tmp_path / 'bin')}
LINK_DIR="$INSTALL_DIR/venv"
PACKAGE_VERSION='{VERSION}'
SRC_VERSION="$PACKAGE_VERSION"
VERSIONED_RUNTIME=1
PUBLISH_GLOBAL_BINSTUBS=false
PY={_sh(sys.executable)}
EVENTS={_sh(tmp_path / 'events')}
BOUNDARY='{boundary}'
rec() {{ printf '%s\\n' "$1" >> "$EVENTS"; }}
_bootstrap_python() {{ printf '%s\\n' "$PY"; }}
_fail() {{ printf '%s\\n' "$*" >&2; }}
_step() {{ :; }}
_ok() {{ :; }}
_skip() {{ :; }}
_warn() {{ :; }}
_ensure_uv() {{ :; }}
_ensure_uv_index() {{ :; }}
_migration_check() {{ :; }}
_downgrade_guard() {{ :; }}
mutate() {{ {mutate}; }}
_require_install_lock() {{ rec LOCK; if [[ "$BOUNDARY" == lock ]]; then mutate; fi; }}
_guard_published_slot() {{
    rec ADMIT
    PAYLOAD_HASH_FIXED=1
    PAYLOAD_HASH=unchanged
    SLOT_ADMISSION=construct
    return 1
}}
_compute_payload_hash() {{ printf unchanged; }}
_versioned_slot_clean() {{ _RUNTIME_CONSTRUCTION_REUSE=0; }}
_install_slot_build() {{ rec BUILD; if [[ "$BOUNDARY" == build ]]; then mutate; fi; }}
_update_core() {{ _install_slot_build; }}
_runtime_healthy() {{ if [[ "$BOUNDARY" == completion ]]; then mutate; fi; }}
_active_is_forward() {{ return 1; }}
_active_signature() {{ printf previous-serving-runtime; }}
_get_pid() {{ printf 123; }}
_write_update_marker() {{ rec UPDATE-MARKER; }}
_clear_update_marker() {{ :; }}
_exit_install_lock() {{ :; }}
_update_lifecycle_drain_stop() {{ rec SERVICE; return 1; }}
_update_lifecycle_start() {{ rec SERVICE; return 1; }}
_versioned_current() {{ printf 1.2.2; }}
_remove_venv_backup() {{ :; }}
_versioned_mark_complete() {{
    if [[ '{action}' == do_update && "$BOUNDARY" == completion ]]; then
        echo 'completion validation did not run' >&2; exit 62
    fi
    rec COMPLETE
}}
_versioned_activate() {{ rec ACTIVATE; printf bad > "$INSTALL_DIR/current-version"; exit 51; }}
_write_deploy_manifest() {{ rec MANIFEST; }}
{chr(10).join(_function(name, 'sh') for name in names)}
_initialize_source_runtime
""", encoding="utf-8")
    # Update has no health seam after its final admission check. Mutate from
    # the real post-build check's fingerprint call, immediately before its
    # return, so the final source-only check must independently catch it.
    if action == "do_update" and boundary == "completion":
        with harness.open("a", encoding="utf-8") as output:
            output.write('_compute_payload_hash() { mutate; printf unchanged; }\n')
    if stopped_predecessor:
        selected = json.loads((payload / "runtime-source.json").read_text())["runtime_version"]
        previous["current-version"] = selected.encode()
        (tmp_path / "install" / "current-version").write_bytes(previous["current-version"])
        with harness.open("a", encoding="utf-8") as output:
            output.write("""
_versioned_current() { printf '%s' "$SRC_VERSION"; }
_update_lifecycle_drain_stop() { rec "STOP:$1"; return 0; }
_update_lifecycle_start() {
    [[ "$(cat "$INSTALL_DIR/current-version")" == "$SRC_VERSION" ]] || return 1
    rec "RESTART:$1"
}
""")
    with harness.open("a", encoding="utf-8") as output:
        output.write(f"{action}\n")
    result = _run([bash, str(harness)], tmp_path)
    detail = result.stdout + result.stderr
    assert result.returncode == 1, detail
    assert "identity" in detail.lower(), detail
    events = (tmp_path / "events").read_text().split()
    assert "LOCK" in events
    assert ("ADMIT" in events) == (boundary != "lock")
    assert not {"COMPLETE", "ACTIVATE", "MANIFEST", "SERVICE"} & set(events)
    if stopped_predecessor:
        assert events.count("STOP:previous-serving-runtime") == 1, detail
        assert events.count("RESTART:previous-serving-runtime") == 1, detail
        assert events.index("STOP:previous-serving-runtime") < events.index("BUILD")
        assert events.index("BUILD") < events.index("RESTART:previous-serving-runtime")
    _assert_previous(tmp_path, previous)
