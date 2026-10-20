"""Native source snapshots and full-identity admission at installer boundaries."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "_index_source_owned_fixtures",
    Path(__file__).with_name("test_install_engine_adoption.py"),
)
assert _FIXTURE_SPEC is not None and _FIXTURE_SPEC.loader is not None
_FIXTURES = importlib.util.module_from_spec(_FIXTURE_SPEC)
_FIXTURE_SPEC.loader.exec_module(_FIXTURES)
function = _FIXTURES.function

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
PACKAGE = "1.2.3-dev4"
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@pytest.fixture
def source(tmp_path: Path):
    spec = importlib.util.spec_from_file_location(
        "index_source_install_test", SCRIPTS / "versioned_runtime_source.py",
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
    for name in ("session-stamp.py", "stamp_paths.py", "stamp_repair.py"):
        shutil.copy2(SCRIPTS / name, payload / "scripts" / name)
    (payload / "plugin.json").write_text(json.dumps({
        "name": "agent-index", "version": PACKAGE,
    }), encoding="utf-8")
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "agent-index"\nversion = "{PACKAGE}"\n',
        encoding="utf-8",
    )
    digest = api.assembly_hash(payload)
    identity = api.SourceIdentity(
        "agent-index", PACKAGE, "a" * 40, "b" * 40, digest,
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
    _FIXTURES.construction_bundle(tmp_path)
    for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
        shutil.copy2(SCRIPTS / name, tmp_path / name)
    harness = tmp_path / "native-source.ps1"
    definitions = "\n".join(function(name, "ps1") for name in (
        "Get-PayloadHash", "Invoke-VersionedSlotClean", "Invoke-VersionedMarkComplete",
        "New-IndexMutex", "Enter-IndexStampLock", "Invoke-VersionedActivate",
        "Get-VerTuple", "Test-VersionLt", "Test-IndexPublicationFresh",
        "New-IndexSnapshot", "Publish-FileAtomically", "Invoke-Stamp", "Deploy-SetupGatedBinstub",
    ))
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$PSScriptRoot = '{SCRIPTS}'\n$ScriptDir = '{SCRIPTS}'\n"
        f"$PluginDir = '{payload}'\n$InstallDir = '{tmp_path / 'runtime'}'\n"
        f"$LocalBin = '{tmp_path / 'bin'}'\n$probePayload = $PluginDir\n"
        f"$SrcVersion = '{PACKAGE}'\n$VersionedRuntime = $true\n$Force = $false\n"
        "$VenvDir = Join-Path $InstallDir 'absent'; $LinkDir = Join-Path $InstallDir '.venv'\n"
        "function Write-Ok { param($Msg) }\nfunction Write-Skip { param($Msg) }\n"
        "function Write-Fail { param($Msg) throw $Msg }\n"
        f". '{SCRIPTS / 'versioned_runtime_native.ps1'}'\n"
        + definitions
        + f"\nfunction Get-BootstrapPython {{ return '{sys.executable}' }}\n"
        + "Initialize-SourceRuntime\nAssert-SourceRuntime\n"
        + "New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null\n"
        + "[IO.File]::WriteAllText((Join-Path $InstallDir 'current-version'), '1.0.0')\n"
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
    expected_target = ("-> " + repr(str(tmp_path / "runtime/stamped-version"))).replace("'", "''")
    result = native(source, tmp_path, f"$expectedTarget = '{expected_target}'\n" + """
$stamp = Join-Path $InstallDir 'stamped-version'
$payload = Join-Path $InstallDir 'payload-dir'
$origin = Join-Path $InstallDir 'payload-origin'
[IO.File]::WriteAllText($stamp, '0.9.0')
[IO.File]::WriteAllText($origin, 'healthy-origin')
$beforePayload = [Convert]::ToBase64String([IO.File]::ReadAllBytes($payload))
$beforeStamp = [Convert]::ToBase64String([IO.File]::ReadAllBytes($stamp))
$beforeOrigin = [Convert]::ToBase64String([IO.File]::ReadAllBytes($origin))
$held = [IO.File]::Open($stamp, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
$refusal = ''
try {
    try { Invoke-Stamp } catch { $refusal = $_.Exception.Message }
    if ($refusal -notmatch '\\[WinError (5|32)\\]' -or
        -not $refusal.Contains($expectedTarget)) {
        throw "Expected real second-marker sharing violation at $stamp`: $refusal"
    }
    if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($payload)) -cne $beforePayload -or
        [Convert]::ToBase64String([IO.File]::ReadAllBytes($stamp)) -cne $beforeStamp -or
        [Convert]::ToBase64String([IO.File]::ReadAllBytes($origin)) -cne $beforeOrigin) {
        throw 'Rejected source stamp changed its redirects while locked'
    }
} finally { $held.Dispose() }
Invoke-Stamp
if ([IO.File]::ReadAllText($stamp).Trim() -cne $SrcVersion -or
    [IO.File]::ReadAllText($payload).Trim() -cne (Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion) -or
    [IO.File]::ReadAllText($origin).Trim() -cne $probePayload) {
    throw 'Source stamp failed after sharing lock release'
}
Write-Output 'second-marker-lock-refused'
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "second-marker-lock-refused" in result.stdout


def test_native_stamp_publishes_exact_complete_snapshot_and_reuses(source, tmp_path):
    _, payload, identity = source
    result = native(source, tmp_path, """
Invoke-Stamp
$first = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')).Trim()
$before = [IO.File]::ReadAllBytes((Join-Path $first 'runtime-source.json'))
Invoke-Stamp
$second = [IO.File]::ReadAllText((Join-Path $InstallDir 'payload-dir')).Trim()
if ($first -cne $second -or [Convert]::ToBase64String($before) -cne [Convert]::ToBase64String(
    [IO.File]::ReadAllBytes((Join-Path $second 'runtime-source.json')))) { throw 'snapshot rewritten' }
[IO.Directory]::Move($PluginDir, "$PluginDir-unavailable")
$PluginDir = $second
Assert-SourceRuntime
Write-Output $SrcVersion
""")
    assert result.returncode == 0, result.stderr
    assert identity.runtime_version in result.stdout
    root = tmp_path / "runtime"
    snapshot = root / "snapshots" / identity.runtime_version
    assert Path((root / "payload-dir").read_text().strip()) == snapshot
    assert (root / "stamped-version").read_text().strip() == identity.runtime_version
    assert (tmp_path / "bin" / "agent-index.ps1").is_file()
    assert not payload.exists()


@pytest.mark.parametrize("boundary", ["construction", "completion", "activation", "snapshot"])
def test_native_full_receipt_change_refuses_and_preserves_healthy_selection(source, tmp_path, boundary):
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
function Enter-IndexStampLock {
    param($Scope)
    Change-Receipt
    return [Threading.Mutex]::new($true)
}
Invoke-VersionedActivate
""",
        "snapshot": "Change-Receipt\nNew-IndexSnapshot",
    }[boundary]
    result = native(source, tmp_path, """
function Change-Receipt {
    $path = Join-Path $PluginDir 'runtime-source.json'
    $receipt = [IO.File]::ReadAllText($path) | ConvertFrom-Json
    $receipt.source_tree = 'c' * 40
    [IO.File]::WriteAllText($path, ($receipt | ConvertTo-Json -Compress))
}
""" + action)
    assert result.returncode != 0
    assert "identity changed" in result.stderr.lower()
    root = tmp_path / "runtime"
    assert (root / "current-version").read_text() == "1.0.0"
    assert (root / "payload-dir").read_text() == "healthy-payload"
    assert not list(root.glob("versions/*/.install-complete.json"))


def test_native_completed_source_generation_reuses_unchanged_evidence(source, tmp_path):
    result = native(source, tmp_path, f"""
Invoke-VersionedSlotClean
if ($script:RuntimeConstructionReuse) {{ throw 'fresh candidate unexpectedly reused' }}
Invoke-Hidden -FilePath '{sys.executable}' -ArgList @('-I', '-m', 'venv', '--without-pip', $VenvDir) | Out-Null
if ($LASTEXITCODE -ne 0) {{ throw 'fixture venv failed' }}
[void](Invoke-VersionedMarkComplete)
Stop-RuntimeConstruction
$marker = Join-Path $VenvDir '.install-complete.json'
$before = [IO.File]::ReadAllBytes($marker)
$stamp = [IO.File]::GetLastWriteTimeUtc($marker)
Invoke-VersionedSlotClean
if (-not $script:RuntimeConstructionReuse) {{ throw 'immutable candidate was not reused' }}
[void](Invoke-VersionedMarkComplete)
if ($stamp -ne [IO.File]::GetLastWriteTimeUtc($marker) -or
    [Convert]::ToBase64String($before) -cne [Convert]::ToBase64String([IO.File]::ReadAllBytes($marker))) {{
    throw 'completion evidence was rewritten'
}}
""")
    assert result.returncode == 0, result.stderr


def test_same_package_distinct_commits_publish_distinct_immutable_snapshots(source, tmp_path):
    api, payload, identity = source
    root = tmp_path / "runtime"
    first = api.publish_source_snapshot(payload, root, identity)
    before = (first / "runtime-source.json").read_bytes()
    other = api.SourceIdentity(
        identity.plugin, PACKAGE, "c" * 40, identity.source_tree, identity.assembly_hash,
        api.runtime_version(PACKAGE, "c" * 40, identity.assembly_hash),
    )
    (payload / "runtime-source.json").write_text(json.dumps(other.document()), encoding="utf-8")
    second = api.publish_source_snapshot(payload, root, other)
    assert first != second
    assert (first / "runtime-source.json").read_bytes() == before
    assert api.read_source_identity(second, PACKAGE) == other


@pytest.mark.parametrize("origin", ["unchanged", "changed", "removed", "snapshot-changed"])
def test_bash_manifest_after_origin_change_uses_pinned_snapshot(source, tmp_path, origin):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("POSIX installer fixture")
    api, payload, identity = source
    root = tmp_path / "runtime"
    (root / ".venv").mkdir(parents=True)
    (root / "versions" / identity.runtime_version).mkdir(parents=True)
    definitions = "\n".join(function(name, "sh") for name in (
        "_write_manifest", "_source_kind", "_payload_hash",
        "_with_index_advisory_lock", "_with_index_publication_lock",
        "_index_publication_fresh", "_version_lt",
    ))
    result = subprocess.run(
        [shutil.which("bash"), "-c", f"""
set -euo pipefail
. '{PLUGIN.parents[1] / "libs/installer-engine/installer-engine.sh"}'
{definitions}
PLUGIN_DIR='{payload}'; SCRIPT_DIR='{SCRIPTS}'; INSTALL_DIR='{root}'
LINK_DIR="$INSTALL_DIR/.venv"
SRC_VERSION='{PACKAGE}'; VERSIONED_RUNTIME=1; FORCE=0
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
"""], env=_FIXTURES.environment(tmp_path), cwd=tmp_path,
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
        function("Write-Manifest", "ps1") + function("Get-SourceKind", "ps1")
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


def test_native_resolver_requires_exact_source_selector_even_with_newer_plain_slot(source, tmp_path):
    api, _, identity = source
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell unavailable")
    root = tmp_path / "runtime"
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


def test_posix_source_activation_with_existing_current_does_not_resolve_dependencies(source, tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX installer fixture")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable")
    _, payload, identity = source
    root = tmp_path / "runtime"
    python = root / "versions" / identity.runtime_version / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/bin/sh\nif [ "$6" = activate ]; then printf "%s" "$7" > "$3/current-version"; fi\nexit 0\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    (root / "current-version").write_text("1.0.0", encoding="utf-8")
    script = (
        "set -eu\n"
        f"SCRIPT_DIR='{SCRIPTS}'\nPLUGIN_DIR='{payload}'\nINSTALL_DIR='{root}'\n"
        f"SRC_VERSION='{PACKAGE}'\nLINK_DIR='{root / '.venv'}'\nFORCE=0\nVERSIONED_RUNTIME=1\n"
        f"_bootstrap_python() {{ printf '%s' '{sys.executable}'; }}\n"
        "_ok() { :; }; _skip() { :; }; _fail() { printf '%s\\n' \"$*\" >&2; }\n"
        "_runtime_origin_under() { return 0; }\n"
        "_with_index_publication_lock() { \"$@\"; }\n"
        + "\n".join(function(name, "sh") for name in (
            "_version_lt", "_index_publication_fresh", "_versioned_activate",
        ))
        + "\n_initialize_source_runtime\n_versioned_activate\n"
    )
    result = subprocess.run(
        [bash, "-c", script], env=_FIXTURES.environment(tmp_path),
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "current-version").read_text() == identity.runtime_version


def test_posix_source_dependencies_resolve_locally_or_fail_explicitly(source, tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX installer fixture")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable")
    api, payload, identity = source
    library = payload / "libs" / "required"
    library.mkdir(parents=True)
    (library / "pyproject.toml").write_text(
        '[project]\nname = "required"\nversion = "1.0.0"\n', encoding="utf-8",
    )
    digest = api.assembly_hash(payload)
    updated = api.SourceIdentity(
        identity.plugin, PACKAGE, identity.source_commit, identity.source_tree, digest,
        api.runtime_version(PACKAGE, identity.source_commit, digest),
    )
    (payload / "runtime-source.json").write_text(json.dumps(updated.document()), encoding="utf-8")
    script = (
        "set -eu\n"
        f"SCRIPT_DIR='{SCRIPTS}'\nPLUGIN_DIR='{payload}'\nINSTALL_DIR='{tmp_path / 'runtime'}'\n"
        f"SRC_VERSION='{PACKAGE}'\n"
        f"_bootstrap_python() {{ printf '%s' '{sys.executable}'; }}\n"
        "_fail() { printf '%s\\n' \"$*\" >&2; }\n"
        + function("_resolve_vendored_lib", "sh")
        + "\n_initialize_source_runtime\n"
        + '_resolve_vendored_lib required\n'
        + "if _resolve_vendored_lib absent; then exit 88; fi\n"
    )
    result = subprocess.run(
        [bash, "-c", script], env=_FIXTURES.environment(tmp_path),
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == library
    assert "Source-qualified payload is missing a required vendored dependency: absent" in result.stderr
