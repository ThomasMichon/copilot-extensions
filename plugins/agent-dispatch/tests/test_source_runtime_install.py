"""Native source selection, immutable snapshots and construction admission."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "_dispatch_source_owned_fixtures",
    Path(__file__).with_name("test_installer_engine_adoption.py"),
)
assert _FIXTURE_SPEC is not None and _FIXTURE_SPEC.loader is not None
_FIXTURES = importlib.util.module_from_spec(_FIXTURE_SPEC)
_FIXTURE_SPEC.loader.exec_module(_FIXTURES)
_function = _FIXTURES._function

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
PACKAGE = "1.2.3-dev4"
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@pytest.fixture
def source(tmp_path: Path):
    spec = importlib.util.spec_from_file_location(
        "dispatch_source_install_test", SCRIPTS / "versioned_runtime_source.py",
    )
    assert spec and spec.loader
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
    payload = tmp_path / "payload"
    (payload / "src").mkdir(parents=True)
    (payload / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (payload / "scripts").mkdir()
    for helper in SCRIPTS.glob("versioned_runtime*.py"):
        shutil.copy2(helper, payload / "scripts" / helper.name)
    (payload / "plugin.json").write_text(json.dumps({
        "name": "agent-dispatch", "version": PACKAGE,
    }), encoding="utf-8")
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "agent-dispatch"\nversion = "{PACKAGE}"\n',
        encoding="utf-8",
    )
    digest = api.assembly_hash(payload)
    identity = api.SourceIdentity(
        "agent-dispatch", PACKAGE, "a" * 40, "b" * 40, digest,
        api.runtime_version(PACKAGE, "a" * 40, digest),
    )
    (payload / "runtime-source.json").write_text(
        json.dumps(identity.document()), encoding="utf-8",
    )
    return api, payload, identity


def native(source, tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    _, payload, _ = source
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell unavailable")
    for name in (
        "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_construction.py", "versioned_runtime_source.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copy2(SCRIPTS / name, tmp_path / name)
    harness = tmp_path / "native-source.ps1"
    definitions = "\n".join(_function("ps1", name) for name in (
        "Get-PayloadHash", "Invoke-VersionedSlotClean", "Invoke-VersionedMarkComplete",
        "Enter-PluginSnapshotLock", "Invoke-VersionedActivate", "Get-VerTuple", "Test-VersionLt",
        "New-PluginBuildSnapshot",
    ))
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$PSScriptRoot = '{SCRIPTS}'\n$ScriptDir = '{SCRIPTS}'\n"
        f"$PluginDir = '{payload}'\n$InstallDir = '{tmp_path / 'runtime'}'\n"
        f"$SrcVersion = '{PACKAGE}'\n$VersionedRuntime = $true\n$Force = $false\n"
        "$VenvDir = Join-Path $InstallDir 'absent'; $LinkDir = Join-Path $InstallDir '.venv'\n"
        "$script:GlobalActivationLockTimeoutSeconds = 5\n"
        "function Write-Ok { param($Msg) }\nfunction Write-Skip { param($Msg) }\n"
        "function Write-Fail { param($Msg) throw $Msg }\n"
        "function Get-VersionedCurrent { return 'healthy' }\n"
        f". '{SCRIPTS / 'versioned_runtime_native.ps1'}'\n"
        + definitions
        + f"\nfunction Get-BootstrapPython {{ return '{sys.executable}' }}\n"
        + "Initialize-SourceRuntime\nAssert-SourceRuntime\n"
        + "New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null\n"
        + "[IO.File]::WriteAllText((Join-Path $InstallDir 'current-version'), 'healthy')\n"
        + "[IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), 'healthy-payload')\n"
        + "try {\n" + body + """
} finally {
    if (Get-Command Stop-RuntimeConstruction -ErrorAction SilentlyContinue) { Stop-RuntimeConstruction }
}
""",
        encoding="utf-8",
    )
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True, text=True, timeout=40, creationflags=HEADLESS,
    )


def test_native_redirect_commit_does_not_revalidate_mutable_origin(source, tmp_path):
    result = native(source, tmp_path, """
$snapshot = Publish-SourceSnapshot
$snapshotReceipt = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $snapshot 'runtime-source.json')))
$script:realNative = ${function:Invoke-Hidden}
function Invoke-Hidden {
    param([string]$FilePath, [string[]]$ArgList, [ref]$ErrorOutput)
    $output = & $script:realNative -FilePath $FilePath -ArgList $ArgList -ErrorOutput $ErrorOutput
    $nativeExit = $LASTEXITCODE
    if ($nativeExit -eq 0 -and $ArgList -contains 'publish-redirects') {
        $path = Join-Path $PluginDir 'runtime-source.json'
        $receipt = [IO.File]::ReadAllText($path) | ConvertFrom-Json
        $receipt | Add-Member -NotePropertyName source_tree -NotePropertyValue 'changed-after-commit' -Force
        [IO.File]::WriteAllText($path, ($receipt | ConvertTo-Json -Depth 20))
    }
    $global:LASTEXITCODE = $nativeExit
    return $output
}
Publish-SourceRedirects -Snapshot $snapshot
$origin = [IO.File]::ReadAllText((Join-Path $PluginDir 'runtime-source.json')) | ConvertFrom-Json
if ($origin.source_tree -cne 'changed-after-commit') { throw 'Origin mutation was not injected after commit' }
if ([IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')).Trim() -cne $snapshot -or
    [IO.File]::ReadAllText((Join-Path $InstallDir 'stamped-version')).Trim() -cne $SrcVersion -or
    [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $snapshot 'runtime-source.json'))) -cne $snapshotReceipt) {
    throw 'Committed redirects or their selected snapshot receipt changed'
}
Write-Output 'committed-source-redirects'
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "committed-source-redirects" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="native Windows sharing violation")
def test_native_source_stamp_second_marker_lock_preserves_redirect_pair(source, tmp_path):
    from . import source_publication_fixture

    expected_target = ("-> " + repr(str(tmp_path / "runtime/stamped-version"))).replace("'", "''")
    definitions = "\n".join(source_publication_fixture.function("ps1", name) for name in (
        "Invoke-Stamp", "Publish-FileAtomically", "Deploy-SelfProvisioningBinstub",
    ))
    result = native(source, tmp_path, definitions + f"""
$LocalBin = '{tmp_path / "bin"}'
$env:AGENT_DISPATCH_SUPERVISE_MACHINE = 'fixture-host'
function Write-Skip {{ param($Msg) throw "Source stamp unexpectedly skipped: $Msg" }}
[IO.File]::WriteAllText((Join-Path $InstallDir 'current-version'), '0.9.0')
$expectedTarget = '{expected_target}'
$stamp = Join-Path $InstallDir 'stamped-version'
$payload = Join-Path $InstallDir 'payload-dir'
[IO.File]::WriteAllText($stamp, '0.9.0')
$beforePayload = [Convert]::ToBase64String([IO.File]::ReadAllBytes($payload))
$beforeStamp = [Convert]::ToBase64String([IO.File]::ReadAllBytes($stamp))
$held = [IO.File]::Open($stamp, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
$refusal = ''
try {{
    try {{ Invoke-Stamp }} catch {{ $refusal = $_.Exception.Message }}
    if ($refusal -notmatch '\\[WinError (5|32)\\]' -or
        -not $refusal.Contains($expectedTarget)) {{
        throw "Expected real second-marker sharing violation at $stamp`: $refusal"
    }}
    if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($payload)) -cne $beforePayload -or
        [Convert]::ToBase64String([IO.File]::ReadAllBytes($stamp)) -cne $beforeStamp) {{
        throw 'Rejected source stamp changed its redirect pair while locked'
    }}
}} finally {{ $held.Dispose() }}
Invoke-Stamp
if ([IO.File]::ReadAllText($stamp).Trim() -cne $SrcVersion -or
    [IO.File]::ReadAllText($payload).Trim() -cne (Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion)) {{
    throw 'Source stamp failed after sharing lock release'
}}
Write-Output 'second-marker-lock-refused'
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "second-marker-lock-refused" in result.stdout


def test_native_snapshot_reuses_complete_receipt_and_survives_source_removal(source, tmp_path):
    _, payload, identity = source
    result = native(source, tmp_path, """
$first = New-PluginBuildSnapshot -PluginDir $PluginDir -InstallDir $InstallDir -Version $SrcVersion -ForStamp
$before = [IO.File]::ReadAllBytes((Join-Path $first 'runtime-source.json'))
$second = Publish-SourceSnapshot
Assert-SourceSnapshot -Snapshot $second
if ($first -cne $second) { throw 'snapshot was not reused' }
if ([Convert]::ToBase64String($before) -cne [Convert]::ToBase64String(
        [IO.File]::ReadAllBytes((Join-Path $second 'runtime-source.json')))) { throw 'receipt rewritten' }
[IO.Directory]::Move($PluginDir, "$PluginDir-unavailable")
$PluginDir = $second
Assert-SourceRuntime
Write-Output $SrcVersion
""")
    assert result.returncode == 0, result.stderr
    assert identity.runtime_version in result.stdout
    snapshot = tmp_path / "runtime" / "snapshots" / identity.runtime_version
    assert (snapshot / "src" / "module.py").read_text() == "VALUE = 1\n"
    assert not payload.exists()
    assert (tmp_path / "runtime" / "current-version").read_text() == "healthy"


@pytest.mark.parametrize("boundary", ["construction", "completion", "activation", "snapshot", "build-snapshot"])
def test_native_changed_full_receipt_refuses_without_changing_selection(source, tmp_path, boundary):
    action = {
        "construction": """
function Start-RuntimeConstruction {
    param($Python,$Helper,$Root,$Version,$PayloadHash,$LinkName)
    Change-Receipt
    return $false
}
Invoke-VersionedSlotClean
""",
        "completion": "Change-Receipt\nInvoke-VersionedMarkComplete",
        "activation": """
function Enter-PluginSnapshotLock {
    param($InstallDir,$TimeoutSeconds)
    Change-Receipt
    return [Threading.Mutex]::new($true)
}
Invoke-VersionedActivate
""",
        "snapshot": "Change-Receipt\nPublish-SourceSnapshot",
        "build-snapshot": """
$snapshot = Publish-SourceSnapshot
Change-Receipt -Root $snapshot
Assert-SourceSnapshot -Snapshot $snapshot
""",
    }[boundary]
    result = native(source, tmp_path, """
function Change-Receipt {
    param($Root = $PluginDir)
    $path = Join-Path $Root 'runtime-source.json'
    $receipt = [IO.File]::ReadAllText($path) | ConvertFrom-Json
    $receipt.source_tree = 'c' * 40
    [IO.File]::WriteAllText($path, ($receipt | ConvertTo-Json -Compress))
}
""" + action)
    assert result.returncode != 0
    message = "snapshot changed" if boundary == "build-snapshot" else "identity changed"
    assert message in result.stderr.lower()
    root = tmp_path / "runtime"
    assert (root / "current-version").read_text() == "healthy"
    assert (root / "payload-dir").read_text() == "healthy-payload"
    assert not list(root.glob("versions/*/.install-complete.json"))


def test_snapshot_lease_revalidates_receipt_before_any_copy(source, tmp_path, monkeypatch):
    api, payload, identity = source
    runtime = api._runtime()
    lease = runtime.construction_lease

    @contextmanager
    def changed_after_lock(root, version):
        with lease(root, version):
            path = payload / "runtime-source.json"
            receipt = json.loads(path.read_text())
            receipt["source_tree"] = "c" * 40
            path.write_text(json.dumps(receipt), encoding="utf-8")
            yield

    monkeypatch.setattr(runtime, "construction_lease", changed_after_lock)
    with pytest.raises(api.SourceIdentityError, match="identity changed"):
        api.publish_source_snapshot(payload, tmp_path / "runtime", identity)
    assert not (tmp_path / "runtime" / "snapshots" / identity.runtime_version).exists()


def test_native_completed_source_generation_reuses_without_rewriting(source, tmp_path):
    result = native(source, tmp_path, f"""
Invoke-VersionedSlotClean
if ($script:RuntimeConstructionReuse) {{ throw 'fresh generation unexpectedly reused' }}
Invoke-Hidden -FilePath '{sys.executable}' -ArgList @('-I', '-m', 'venv', '--without-pip', $VenvDir) | Out-Null
if ($LASTEXITCODE -ne 0) {{ throw 'fixture venv failed' }}
[void](Invoke-VersionedMarkComplete)
Stop-RuntimeConstruction
$marker = Join-Path $VenvDir '.install-complete.json'
$before = [IO.File]::ReadAllBytes($marker)
$stamp = [IO.File]::GetLastWriteTimeUtc($marker)
Invoke-VersionedSlotClean
if (-not $script:RuntimeConstructionReuse) {{ throw 'immutable generation not reused' }}
[void](Invoke-VersionedMarkComplete)
if ($stamp -ne [IO.File]::GetLastWriteTimeUtc($marker) -or
    [Convert]::ToBase64String($before) -cne [Convert]::ToBase64String([IO.File]::ReadAllBytes($marker))) {{
    throw 'completion evidence changed during reuse'
}}
Write-Output $SrcVersion
""")
    assert result.returncode == 0, result.stderr
    assert source[2].runtime_version in result.stdout


@pytest.mark.parametrize("origin", ["unchanged", "changed", "removed", "snapshot-changed"])
def test_bash_manifest_after_origin_change_uses_pinned_snapshot(source, tmp_path, origin):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX installer fixture")
    api, payload, identity = source
    root = tmp_path / "runtime"
    (root / ".venv").mkdir(parents=True)
    (root / "versions" / identity.runtime_version).mkdir(parents=True)
    definitions = "\n".join(_function("sh", name) for name in (
        "_write_manifest", "_source_kind", "_payload_hash",
    ))
    result = subprocess.run(
        [shutil.which("bash"), "-c", f"""
set -euo pipefail
. '{PLUGIN.parents[1] / "libs/installer-engine/installer-engine.sh"}'
{definitions}
PLUGIN_DIR='{payload}'; SCRIPT_DIR='{SCRIPTS}'; INSTALL_DIR='{root}'
LINK_DIR="$INSTALL_DIR/.venv"
SRC_VERSION='{PACKAGE}'; VERSIONED_RUNTIME=1
_bootstrap_python() {{ printf '%s\\n' '{sys._base_executable}'; }}
_ok() {{ :; }}; _warn() {{ printf '%s\\n' "$*" >&2; }}
_fail() {{ printf '%s\\n' "$*" >&2; }}
_initialize_source_runtime
[[ "$LINK_DIR" != "$VENV_DIR" ]]
snapshot="$(_source_snapshot)"
_publish_source_redirects "$snapshot"
printf '%s' '{{"before":true}}' > "$INSTALL_DIR/deploy-manifest.json"
if [[ '{origin}' == changed ]]; then printf '%s' CHANGED > "$PLUGIN_DIR/src/module.py"; fi
if [[ '{origin}' == removed ]]; then mv "$PLUGIN_DIR" "$PLUGIN_DIR-unavailable"; fi
if [[ '{origin}' == snapshot-changed ]]; then printf '%s' CHANGED > "$snapshot/src/module.py"; fi
_write_manifest
[[ "$PLUGIN_DIR" == '{payload}' && "$SCRIPT_DIR" == '{SCRIPTS}' ]]
"""], env=_FIXTURES._env(tmp_path), cwd=tmp_path,
        capture_output=True, text=True, timeout=25,
    )
    if origin == "snapshot-changed":
        assert result.returncode != 0
        assert "Materialized payload bytes do not match" in result.stderr
        assert (root / "deploy-manifest.json").read_bytes() == b'{"before":true}'
        return
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads((root / "deploy-manifest.json").read_text())
    assert document["runtime_source"] == identity.document()
    assert document["source"]["path"] == payload.as_posix()
    assert document["source"]["commit"] == identity.source_commit
    assert Path(document["venv"]) == root / "versions" / identity.runtime_version
    assert Path(document["venv"]).is_dir()
    assert document["source"]["content_hash"] == hashlib.sha256(
        json.dumps(api.manifest_fields(identity), sort_keys=True).encode(),
    ).hexdigest()


@pytest.mark.parametrize("origin", ["unchanged", "changed", "removed", "snapshot-changed"])
def test_native_manifest_keeps_package_version_and_flat_source_receipt(source, tmp_path, origin):
    engine = PLUGIN.parents[1] / "libs" / "installer-engine" / "installer-engine.ps1"
    result = native(
        source, tmp_path,
        _function("ps1", "Write-Manifest") + _function("ps1", "Get-SourceKind")
        + f"\n. '{engine}'\n$originFault = '{origin}'\n" + """
function Get-GitInfo {
    param($Path)
    throw 'Manifest consulted mutable origin git metadata'
}
$snapshot = Publish-SourceSnapshot
Publish-SourceRedirects -Snapshot $snapshot
$legacyLink = Join-Path $InstallDir '.venv'
if ($LinkDir -cne $VenvDir -or $LinkDir -ceq $legacyLink) { throw 'Actual source initializer retained the legacy link' }
[IO.Directory]::CreateDirectory($legacyLink) | Out-Null
[IO.Directory]::CreateDirectory($VenvDir) | Out-Null
[IO.File]::WriteAllText((Join-Path $InstallDir 'deploy-manifest.json'), '{"before":true}')
if ($originFault -eq 'changed') {
    [IO.File]::WriteAllText((Join-Path $PluginDir 'src/module.py'), 'CHANGED = True')
}
if ($originFault -eq 'removed') { [IO.Directory]::Move($PluginDir, "$PluginDir-unavailable") }
if ($originFault -eq 'snapshot-changed') {
    [IO.File]::WriteAllText((Join-Path $snapshot 'src/module.py'), 'CHANGED = True')
}
$ownerPayload = $PluginDir; $ownerScripts = $ScriptDir
Write-Manifest
if ($PluginDir -cne $ownerPayload -or $ScriptDir -cne $ownerScripts) {
    throw 'Manifest changed command-binding source ownership'
}
$manifest = [IO.File]::ReadAllText((Join-Path $InstallDir 'deploy-manifest.json')) | ConvertFrom-Json
if ($manifest.source.version -cne $PackageVersion) { throw 'package version was replaced by a generation' }
if ($manifest.runtime_version -cne $SrcVersion -or
    $manifest.runtime_source.runtime_version -cne $SrcVersion -or
    $manifest.runtime_source.schema -cne 'copilot-extensions.runtime-source') {
    throw 'source receipt was nested or incomplete'
}
""",
    )
    if origin == "snapshot-changed":
        assert result.returncode != 0
        assert "Materialized payload bytes do not match" in result.stderr
        assert (tmp_path / "runtime/deploy-manifest.json").read_bytes() == b'{"before":true}'
        return
    assert result.returncode == 0, result.stderr
    _, payload, identity = source
    document = json.loads((tmp_path / "runtime/deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert document["runtime_source"] == identity.document()
    assert document["source"]["path"] == payload.as_posix()
    assert document["source"]["commit"] == identity.source_commit
    assert Path(document["venv"]) == tmp_path / "runtime/versions" / identity.runtime_version
    assert Path(document["venv"]).is_dir()


def test_bash_stamp_propagates_marker_failure_under_conditional_errexit(source, tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX shell fixture")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable")
    _, payload, identity = source
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "payload-dir").mkdir()
    harness = (
        "set -eu\n"
        f"SCRIPT_DIR='{SCRIPTS}'\nPLUGIN_DIR='{payload}'\nINSTALL_DIR='{root}'\n"
        f"SRC_VERSION='{PACKAGE}'\nLOCAL_BIN='{tmp_path / 'bin'}'\nFORCE=0\n"
        f"source '{PLUGIN.parents[1] / 'libs' / 'installer-engine' / 'installer-engine.sh'}'\n"
        f"source '{SCRIPTS / 'source-runtime.sh'}'\n"
        f"_bootstrap_python() {{ printf '%s' '{sys.executable}'; }}\n"
        "_fail() { printf '%s\\n' \"$*\" >&2; }; _ok() { :; }; _skip() { :; }\n"
        "_version_lt() { return 1; }; deploy_binstub() { touch \"$INSTALL_DIR/deployed\"; }\n"
        + _function("sh", "do_stamp")
        + "\n_initialize_source_runtime\n"
        + "if do_stamp; then exit 88; fi\n"
        + '[[ ! -e "$INSTALL_DIR/stamped-version" && ! -e "$INSTALL_DIR/deployed" ]]\n'
    )
    result = subprocess.run(
        [bash, "-c", harness], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Publication destination must be an ordinary file" in result.stderr
    assert (root / "snapshots" / identity.runtime_version / "runtime-source.json").is_file()


def test_source_hashes_are_not_version_ordering_authority():
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    assert re.search(r'a="\$\{a%%\[\+\.\]source\.\*\}"', text)
    assert "requires_exact_selection" in (SCRIPTS / "versioned_runtime.py").read_text()


def test_native_resolver_uses_exact_source_markers_not_hash_order(source, tmp_path):
    api, _, identity = source
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell unavailable")
    root = tmp_path / "resolver-runtime"
    versions = [identity.runtime_version, api.runtime_version(PACKAGE, "c" * 40, "d" * 64), "99.0.0"]
    for version in versions:
        python = root / "versions" / version / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_text("fixture executable", encoding="utf-8")
        api._runtime().mark_complete(root, version, payload_hash="a" * 64)
    for current, good, expected in (
        (versions[0], versions[1], versions[0]),
        ("invalid", versions[1], versions[1]),
        ("invalid", "invalid", None),
    ):
        (root / "current-version").write_text(current, encoding="utf-8")
        (root / "last-known-good").write_text(good, encoding="utf-8")
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command",
             f"$env:AGENT_RT_ROOT = '{root}'; . '{SCRIPTS / 'resolve-runtime.ps1'}'; Write-Output $AgentRtPy"],
            capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
        )
        assert result.returncode == 0, result.stderr
        if expected:
            assert Path(result.stdout.strip()) == root / "versions" / expected / "Scripts" / "python.exe"
        else:
            assert not result.stdout.strip()
            assert "source" in result.stderr.lower()


def test_native_first_use_does_not_bootstrap_upstream_for_orphaned_source_slots(source, tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell unavailable")
    root = tmp_path / ".agent-dispatch"
    (root / "versions" / source[2].runtime_version).mkdir(parents=True)
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    stub = re.search(r"\$ps1Content = @'\n(.*?)\n'@", text, re.DOTALL)
    assert stub
    harness = tmp_path / "first-use.ps1"
    harness.write_text(
        "function Invoke-WebRequest { throw 'unexpected upstream request' }\n" + stub.group(1),
        encoding="utf-8",
    )
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_", "PYTHON"))
    }
    env["HOME"] = env["USERPROFILE"] = str(tmp_path)
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        env=env, capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    assert result.returncode == 126
    assert "exact source selector" in result.stderr.lower()
    assert "unexpected upstream request" not in result.stderr.lower()
