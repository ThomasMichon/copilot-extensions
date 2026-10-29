"""Native transaction guards, with real receipt checks and stubbed builders.

These tests execute installer functions, not a host install or a real uv build.
Both plugins share the test matrix; no production plugin imports its sibling.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.guard
PLUGINS = ("agent-mcp", "agent-codespaces")
SPEC = importlib.util.spec_from_file_location(
    "scoped_source_identity",
    ROOT / "plugins" / "agent-mcp" / "scripts" / "versioned_runtime_source.py",
)
assert SPEC and SPEC.loader
IDENTITY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = IDENTITY
SPEC.loader.exec_module(IDENTITY)
VERSION = "1.2.3"


def _quote(value: Path | str, language: str) -> str:
    text = str(value)
    if language == "ps1":
        return "'" + text.replace("'", "''") + "'"
    text = text.replace("\\", "/")
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return "'" + text.replace("'", "'\\''") + "'"


def _host(language: str) -> str:
    if language == "ps1":
        result = shutil.which("pwsh") or shutil.which("powershell")
    elif os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        result = str(candidate) if candidate and candidate.is_file() else None
    else:
        result = shutil.which("bash")
    if not result:
        pytest.skip(f"Native {language} host unavailable")
    return result


def _function(plugin: str, name: str, language: str) -> str:
    entry = "init" if plugin == "agent-mcp" else "install"
    text = (ROOT / "plugins" / plugin / "scripts" / f"{entry}.{language}").read_text(
        encoding="utf-8",
    )
    pattern = (
        rf"(?ms)^function {re.escape(name)} \{{.*?^\}}"
        if language == "ps1" else rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}"
    )
    match = re.search(pattern, text)
    assert match, name
    return match.group().replace("$PSScriptRoot", "$ScriptDir")


def _payload(
    tmp_path: Path, plugin: str, *, source: bool = True, construction_diagnostics: bool = False,
) -> tuple[Path, str]:
    payload = tmp_path / "payload with spaces"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for path in (ROOT / "plugins" / plugin / "scripts").glob("versioned_runtime*.py"):
        shutil.copyfile(path, scripts / path.name)
    if plugin == "agent-mcp":
        for name in ("installer_engine_footprint.py", "uninstall-profile.json", "runtime_launcher.py"):
            shutil.copyfile(ROOT / "plugins" / plugin / "scripts" / name, scripts / name)
    if construction_diagnostics:
        helper = scripts / "versioned_runtime.py"
        helper.write_text(helper.read_text(encoding="utf-8").replace(
            "    except Exception as exc:\n        print(f\"versioned_runtime: {exc}\", file=sys.stderr)",
            "    except Exception as exc:\n        import traceback\n        traceback.print_exc()\n"
            "        print(f\"versioned_runtime: {exc}\", file=sys.stderr)",
        ), encoding="utf-8")
    shutil.copyfile(
        ROOT / "plugins" / plugin / "scripts" / "versioned_runtime_native.ps1",
        scripts / "versioned_runtime_native.ps1",
    )
    for suffix in ("ps1", "sh"):
        shutil.copyfile(
            ROOT / "plugins" / plugin / "scripts" / f"source-runtime.{suffix}",
            scripts / f"source-runtime.{suffix}",
        )
        shutil.copyfile(
            ROOT / "plugins" / plugin / "scripts" / f"resolve-runtime.{suffix}",
            scripts / f"resolve-runtime.{suffix}",
        )
    for directory in ("src", "tests", "node_modules", "libs/local-lib/src"):
        path = payload / directory / "hashed.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("immutable input\n", encoding="utf-8")
    (payload / "libs/local-lib/pyproject.toml").write_text(
        '[project]\nname = "local-lib"\nversion = "1.0.0"\n', encoding="utf-8",
    )
    (payload / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": VERSION}), encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "{plugin}"\nversion = "{VERSION}"\n'
        '[tool.uv.sources]\nlocal-lib = { path = "libs/local-lib" }\n',
        encoding="utf-8",
    )
    digest = IDENTITY.assembly_hash(payload)
    identity = IDENTITY.SourceIdentity(
        plugin, VERSION, "0" * 39 + "1", "b" * 40, digest,
        IDENTITY.runtime_version(VERSION, "0" * 39 + "1", digest),
    )
    if source:
        (payload / "runtime-source.json").write_text(
            json.dumps(identity.document()), encoding="utf-8",
        )
    return payload, identity.runtime_version


def _instrument_source_main(payload: Path, code: str) -> str:
    """Instrument only the isolated CLI copy, then seal its actual bytes."""
    helper = payload / "scripts" / "versioned_runtime_source.py"
    text = helper.read_text(encoding="utf-8")
    entry = "    raise SystemExit(main())"
    assert text.count(entry) == 1
    injected = "\n".join("    " + line if line else "" for line in code.splitlines())
    helper.write_text(text.replace(entry, injected + "\n" + entry), encoding="utf-8")
    receipt = payload / "runtime-source.json"
    identity = json.loads(receipt.read_text(encoding="utf-8"))
    identity["assembly_hash"] = IDENTITY.assembly_hash(payload)
    identity["runtime_version"] = IDENTITY.runtime_version(
        VERSION, identity["source_commit"], identity["assembly_hash"],
    )
    receipt.write_text(json.dumps(identity), encoding="utf-8")
    return identity["runtime_version"]


def _instrument_snapshot_copy(payload: Path, mutation_target: str) -> str:
    return _instrument_source_main(payload, f"""
import shutil as _fixture_shutil
_fixture_copytree = _fixture_shutil.copytree
def _fixture_copy(source, destination, *args, **kwargs):
    result = _fixture_copytree(source, destination, *args, **kwargs)
    if Path(source).resolve() == Path({str(payload)!r}).resolve():
        if {mutation_target!r} == 'original':
            with (Path(source) / 'pyproject.toml').open('a', encoding='utf-8') as stream:
                stream.write('changed')
        elif {mutation_target!r} == 'snapshot':
            (Path(destination) / 'corrupt.txt').write_text('changed', encoding='utf-8')
        else:
            receipt = Path(destination) / 'runtime-source.json'
            identity = json.loads(receipt.read_text(encoding='utf-8'))
            identity['source_tree'] = 'c' * 40
            receipt.write_text(json.dumps(identity), encoding='utf-8')
    return result
_fixture_shutil.copytree = _fixture_copy
""")


def _run(tmp_path: Path, language: str, script: str) -> subprocess.CompletedProcess[str]:
    host = _host(language)
    harness = tmp_path / f"native.{language}"
    harness.write_text(script, encoding="utf-8")
    args = [host, "-NoProfile", "-File", str(harness)] if language == "ps1" else [
        host, _quote(harness, "sh")[1:-1],
    ]
    return subprocess.run(
        args, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
        timeout=60, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _setup(tmp_path: Path, payload: Path, plugin: str, language: str) -> str:
    def q(value: Path | str) -> str:
        return _quote(value, language)

    install = tmp_path / "install"
    install.mkdir()
    (install / "current-version").write_text("previous", encoding="utf-8")
    (install / "stamped-version").write_text("previous", encoding="utf-8")
    (install / "payload-dir").write_text("previous", encoding="utf-8")
    if language == "ps1":
        return f"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$PluginDir = {q(payload)}
$ScriptDir = Join-Path $PluginDir 'scripts'
$PSScriptRoot = $ScriptDir
$InstallDir = {q(install)}
$SrcVersion = '{VERSION}'
$VersionedRuntime = $true
$LinkDir = Join-Path $InstallDir '.venv'
$Events = {q(tmp_path / 'events')}
function Rec($value) {{ Add-Content -LiteralPath $Events -Value $value }}
function Get-BootstrapPython {{ return {q(sys.executable)} }}
function Get-PayloadHash {{ return 'fingerprint' }}
function Write-Fail($value) {{ Write-Host $value }}
function Write-Step($value) {{ }}
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
. (Join-Path $ScriptDir 'source-runtime.ps1')
{_function(plugin, 'Invoke-VersionedSlotClean', language)}
{_function(plugin, 'Invoke-VersionedMarkComplete', language)}
"""
    return f"""
set -euo pipefail
PLUGIN_DIR={q(payload)}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={q(install)}
SRC_VERSION='{VERSION}'
VERSIONED_RUNTIME=1
LINK_DIR="$INSTALL_DIR/.venv"
EVENTS={q(tmp_path / 'events')}
_bootstrap_python() {{ printf '%s\\n' {q(sys.executable)}; }}
_payload_hash() {{ echo fingerprint; }}
_fail() {{ echo "$*" >&2; }}
rec() {{ printf '%s\\n' "$1" >> "$EVENTS"; }}
source "$SCRIPT_DIR/source-runtime.sh"
{_function(plugin, '_versioned_slot_clean', language)}
{_function(plugin, '_versioned_mark_complete', language)}
"""


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("language", ["ps1", "sh"])
@pytest.mark.parametrize("boundary", ["admission", "lock", "build", "reuse"])
@pytest.mark.parametrize("mutation", ["content", "removed", "identity"])
def test_native_transaction_refuses_changed_source(
    tmp_path: Path, plugin: str, language: str, boundary: str, mutation: str,
) -> None:
    payload, _ = _payload(tmp_path, plugin)
    script = _setup(tmp_path, payload, plugin, language)
    if language == "ps1":
        mutate = {
            "content": "Add-Content -LiteralPath (Join-Path $PluginDir 'src/hashed.txt') changed",
            "removed": "Remove-Item -LiteralPath (Join-Path $PluginDir 'runtime-source.json')",
            "identity": """
$path = Join-Path $PluginDir 'runtime-source.json'
$data = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
$data.source_tree = 'cccccccccccccccccccccccccccccccccccccccc'
$data | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding UTF8
""",
        }[mutation]
        script += f"""
function Mutate {{ {mutate} }}
function Start-RuntimeConstruction {{
    Rec 'LOCK'
    $script:RuntimeConstructionSession = [pscustomobject]@{{ Version = $SrcVersion }}
    if ('{boundary}' -eq 'lock' -or '{boundary}' -eq 'reuse') {{ Mutate }}
    return ('{boundary}' -eq 'reuse')
}}
function Complete-RuntimeConstruction {{ param($PayloadHash) Rec 'COMPLETE' }}
Initialize-SourceRuntime
if ('{boundary}' -eq 'admission') {{ Mutate }}
[void](Invoke-VersionedSlotClean)
Rec 'BUILD'
if ('{boundary}' -eq 'build') {{ Mutate }}
[void](Invoke-VersionedMarkComplete)
Rec 'ACTIVATE'
"""
    else:
        mutate = {
            "content": 'printf changed >> "$PLUGIN_DIR/src/hashed.txt"',
            "removed": 'rm "$PLUGIN_DIR/runtime-source.json"',
            "identity": (
                """\"$(_bootstrap_python)\" -c 'import json,sys; from pathlib import Path; """
                """p=Path(sys.argv[1]); d=json.loads(p.read_text()); """
                """d["source_tree"]="c"*40; p.write_text(json.dumps(d))' """
                """ "$PLUGIN_DIR/runtime-source.json" """
            ),
        }[mutation]
        script += f"""
mutate() {{ {mutate}
}}
_runtime_construction_begin() {{
    rec LOCK
    _RUNTIME_CONSTRUCTION_VERSION="$SRC_VERSION"
    if [[ '{boundary}' == lock || '{boundary}' == reuse ]]; then mutate; fi
    _RUNTIME_CONSTRUCTION_REUSE=0
}}
_runtime_construction_complete() {{ rec COMPLETE; }}
_initialize_source_runtime
if [[ '{boundary}' == admission ]]; then mutate; fi
_versioned_slot_clean
rec BUILD
if [[ '{boundary}' == build ]]; then mutate; fi
_versioned_mark_complete
rec ACTIVATE
"""
    result = _run(tmp_path, language, script)
    assert result.returncode != 0, result.stdout + result.stderr
    events = (tmp_path / "events").read_text() if (tmp_path / "events").exists() else ""
    assert "COMPLETE" not in events
    assert "ACTIVATE" not in events
    for marker in ("current-version", "stamped-version", "payload-dir"):
        assert (tmp_path / "install" / marker).read_text() == "previous"


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("language", ["ps1", "sh"])
def test_snapshot_marker_failure_refuses_success_without_advancing_stamp(
    tmp_path: Path, plugin: str, language: str,
) -> None:
    payload, selected = _payload(tmp_path, plugin)
    script = _setup(tmp_path, payload, plugin, language)
    root = tmp_path / "install"
    (root / "payload-dir").rename(root / "previous-payload-dir")
    (root / "payload-dir").mkdir()
    stamp = root / "stamped-version"
    if language == "ps1":
        script += f"""
$ServiceName = 'fixture'
$LocalBin = Join-Path $InstallDir 'bin'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-ServiceHeader($value) {{ }}
function Write-ServiceOk($value) {{ }}
function Write-Ok($value) {{ }}
function Deploy-SelfProvisioningBinstub {{
    if ('{plugin}' -ne 'agent-codespaces') {{ throw 'Unexpected binstub publication' }}
}}
function Materialize-SnapshotDependencies {{ throw 'Source snapshot was rematerialized' }}
{_function(plugin, 'Invoke-Stamp', language)}
Initialize-SourceRuntime
try {{ Invoke-Stamp; exit 0 }} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 23
}}
"""
    else:
        script += """
_initialize_source_runtime
if _stamp_source_snapshot; then exit 0; fi
exit 23
"""
    result = _run(tmp_path, language, script)
    assert result.returncode == 23, result.stdout + result.stderr
    assert "payload-dir" in result.stderr
    assert stamp.read_text(encoding="utf-8") == "previous"
    assert IDENTITY.read_source_identity(
        root / "snapshots" / selected, VERSION, plugin=plugin,
    ).runtime_version == selected


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("language", ["ps1", "sh"])
def test_native_selection_reuse_and_noneditable_snapshot(
    tmp_path: Path, plugin: str, language: str,
) -> None:
    payload, selected = _payload(tmp_path, plugin)
    script = _setup(tmp_path, payload, plugin, language)
    if language == "ps1":
        script += f"""
function Start-RuntimeConstruction {{
    Rec 'REUSE'
    $script:RuntimeConstructionSession = [pscustomobject]@{{ Version = $SrcVersion }}
    return $true
}}
function Complete-RuntimeConstruction {{ param($PayloadHash) Rec 'COMPLETE' }}
$ServiceName = 'fixture'
$LocalBin = Join-Path $InstallDir 'bin'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-ServiceHeader($value) {{ }}
function Write-ServiceOk($value) {{ }}
function Write-Ok($value) {{ }}
function Deploy-SelfProvisioningBinstub {{ Rec 'BINSTUB' }}
function Materialize-SnapshotDependencies {{ throw 'Source snapshot was rematerialized' }}
{_function(plugin, 'Invoke-Stamp', language)}
Initialize-SourceRuntime
if ($SrcVersion -ne '{selected}' -or $PackageVersion -ne '{VERSION}') {{ throw 'identity lost' }}
[void](Invoke-VersionedSlotClean)
if (-not $script:RuntimeConstructionReuse) {{ throw 'reuse lost' }}
Invoke-Stamp
Invoke-Stamp
"""
    else:
        script += f"""
_runtime_construction_begin() {{
    rec REUSE
    _RUNTIME_CONSTRUCTION_VERSION="$SRC_VERSION"
    _RUNTIME_CONSTRUCTION_REUSE=1
}}
_initialize_source_runtime
[[ "$SRC_VERSION" == '{selected}' && "$PACKAGE_VERSION" == '{VERSION}' ]]
_versioned_slot_clean
[[ "$_RUNTIME_CONSTRUCTION_REUSE" == 1 ]]
_stamp_source_snapshot
_stamp_source_snapshot
"""
    result = _run(tmp_path, language, script)
    assert result.returncode == 0, result.stdout + result.stderr
    snap = tmp_path / "install" / "snapshots" / selected
    assert IDENTITY.read_source_identity(snap, VERSION, plugin=plugin).runtime_version == selected
    assert (snap / "tests/hashed.txt").is_file()
    assert (snap / "node_modules/hashed.txt").is_file()
    assert 'editable' not in (snap / "pyproject.toml").read_text()
    assert (snap / "libs/local-lib/src/hashed.txt").is_file()


@pytest.mark.parametrize("language", ["ps1", "sh"])
@pytest.mark.parametrize("condition", [
    "current", "missing-deployment", "corrupt-completion", "completion-fifo", "fallback-directory",
    "occupied-fallback", "oversized-desired", "invalid-venv-desired", "invalid-path-desired",
])
def test_native_mcp_footprint_routes_actual_construction_without_old_receipt_permission(
    tmp_path: Path, language: str, condition: str,
) -> None:
    if language == "sh" and os.name == "nt":
        pytest.skip("POSIX FIFO construction requires a native POSIX Python, not Git Bash with Windows Python")
    if condition == "completion-fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("FIFO completion evidence requires native POSIX")
    payload, selected = _payload(tmp_path, "agent-mcp", construction_diagnostics=True)
    script = _setup(tmp_path, payload, "agent-mcp", language)
    root = tmp_path / "install"
    damaged_desired = condition.endswith("-desired")
    incumbent_version = (
        "1.2.2" if damaged_desired or condition in {"fallback-directory", "occupied-fallback"}
        else selected
    )
    incumbent = root / "versions" / incumbent_version
    incumbent.mkdir(parents=True)
    (incumbent / "incumbent-data").write_bytes(b"unchanged")
    (root / "current-version").write_text(incumbent_version, encoding="utf-8")
    if condition == "fallback-directory":
        (root / "last-known-good").mkdir()
    if condition == "occupied-fallback":
        occupied = root / "versions" / selected
        occupied.mkdir()
        (occupied / "unfinished").write_bytes(b"keep")
        (root / "last-known-good").write_text(selected, encoding="utf-8")
    if condition != "missing-deployment":
        (root / "deploy-manifest.json").write_text(
            '{"schema_version":3,"service":"agent-mcp"}', encoding="utf-8",
        )
    completion = incumbent / ".install-complete.json"
    if condition == "completion-fifo":
        os.mkfifo(completion)
    else:
        completion.write_text(
            "{" if condition == "corrupt-completion" else json.dumps({
                "version": incumbent_version, "completed_at": "fixture", "pid": 1,
                "payload_hash": "a" * 64,
            }), encoding="utf-8",
        )
    if damaged_desired:
        desired = root / "versions" / selected
        desired.mkdir()
        marker = desired / completion.name
        marker.write_text(json.dumps({
            "version": selected, "completed_at": "fixture", "pid": 1, "payload_hash": "a" * 64,
        }), encoding="utf-8")
        if condition == "oversized-desired":
            with marker.open("ab") as stream:
                stream.write(b" " * 65537)
        elif condition == "invalid-venv-desired":
            (desired / "pyvenv.cfg").write_bytes(b"\xff")
        else:
            site = desired / "Lib" / "site-packages"
            site.mkdir(parents=True)
            (site / "invalid.pth").write_bytes(b"\xff")
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    if language == "ps1":
        script += """
function Get-PayloadHash { return ('a' * 64) }
Initialize-SourceRuntime
try {
    [void](Invoke-VersionedSlotClean)
    $chosen = $SrcVersion
    [void](Invoke-VersionedSlotClean)
    if ($chosen -ne $SrcVersion) { throw 'construction was planned twice' }
    Assert-SourceRuntime
    if (-not $script:RuntimeConstructionReuse) {
        Set-Content -LiteralPath (Join-Path $VenvDir 'constructed') -Value 'new'
        [void](Invoke-VersionedMarkComplete)
    }
    [IO.File]::WriteAllText((Join-Path $InstallDir 'chosen-version'), $SrcVersion)
    [IO.File]::WriteAllText((Join-Path $InstallDir 'construction-mode'),
        $script:RuntimeConstructionSession.Construction)
} finally {
    if (Get-Command Stop-RuntimeConstruction -ErrorAction SilentlyContinue) {
        $pending = $script:RuntimeConstructionSession
        try { Stop-RuntimeConstruction } catch {
            if ($pending -and $pending.Errors.IsCompleted) {
                [Console]::Error.WriteLine($pending.Errors.Result)
            }
            throw
        }
    }
}
"""
    else:
        script += """
_payload_hash() { printf '%064d\\n' 0 | tr 0 a; }
_initialize_source_runtime
_versioned_slot_clean
chosen="$SRC_VERSION"
_versioned_slot_clean
[[ "$chosen" == "$SRC_VERSION" ]]
_assert_source_runtime
if [[ "$_RUNTIME_CONSTRUCTION_REUSE" != 1 ]]; then
    printf new > "$VENV_DIR/constructed"
    _versioned_mark_complete
fi
printf '%s' "$SRC_VERSION" > "$INSTALL_DIR/chosen-version"
printf '%s' "$_RUNTIME_CONSTRUCTION_MODE" > "$INSTALL_DIR/construction-mode"
_runtime_construction_finish
"""
    result = _run(tmp_path, language, script)
    assert result.returncode == 0, result.stdout + result.stderr
    version = (root / "chosen-version").read_text(encoding="utf-8")
    mode = (root / "construction-mode").read_text(encoding="utf-8")
    if condition in {"current", "fallback-directory"}:
        assert version == selected and mode == "incremental"
    else:
        assert version.startswith(selected + ".repair.") and mode == "fresh"
        candidate = root / "versions" / version
        assert (candidate / "constructed").is_file()
        assert json.loads((candidate / ".install-complete.json").read_text())["version"] == version
    assert {name: (root / name).read_bytes() for name in before} == before
    if condition == "completion-fifo":
        assert completion.is_fifo()
    if condition == "fallback-directory":
        assert (root / "last-known-good").is_dir()
        assert (root / "versions" / selected / "constructed").is_file()


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("language", ["ps1", "sh"])
@pytest.mark.parametrize("invalid", ["receipt", "appeared"])
def test_native_selection_rejects_invalid_or_new_receipt(
    tmp_path: Path, plugin: str, language: str, invalid: str,
) -> None:
    payload, _ = _payload(tmp_path, plugin, source=invalid != "appeared")
    script = _setup(tmp_path, payload, plugin, language)
    if invalid == "receipt":
        (payload / "runtime-source.json").write_text("{}", encoding="utf-8")
    if language == "ps1":
        script += "Initialize-SourceRuntime\n"
        if invalid == "appeared":
            script += (
                "Set-Content -LiteralPath (Join-Path $PluginDir 'runtime-source.json') '{}'\n"
                "Assert-SourceRuntime\n"
            )
    else:
        script += "_initialize_source_runtime\n"
        if invalid == "appeared":
            script += (
                """printf '{}' > "$PLUGIN_DIR/runtime-source.json"\n"""
                "_assert_source_runtime\n"
            )
    result = _run(tmp_path, language, script)
    assert result.returncode != 0, result.stdout + result.stderr
    assert (tmp_path / "install/current-version").read_text() == "previous"


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize(("language", "mutation_target"), [
    (language, target)
    for language in ("ps1", "sh")
    for target in ("original", "snapshot")
] + [("sh", "snapshot-tree"), ("sh", "existing-tree")])
def test_native_stamp_rechecks_copied_bytes_before_redirects(
    tmp_path: Path, plugin: str, language: str, mutation_target: str,
) -> None:
    payload, selected = _payload(tmp_path, plugin)
    if mutation_target != "existing-tree":
        selected = _instrument_snapshot_copy(payload, mutation_target)
    script = _setup(tmp_path, payload, plugin, language)
    if mutation_target == "existing-tree":
        snapshot = tmp_path / "install" / "snapshots" / selected
        shutil.copytree(payload, snapshot)
        receipt = snapshot / "runtime-source.json"
        identity = json.loads(receipt.read_text(encoding="utf-8"))
        identity["source_tree"] = "c" * 40
        assert identity["runtime_version"] == selected
        receipt.write_text(json.dumps(identity), encoding="utf-8")
    if language == "ps1":
        script += f"""
$ServiceName = 'fixture'
$LocalBin = Join-Path $InstallDir 'bin'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-ServiceHeader($value) {{ }}
function Write-ServiceOk($value) {{ }}
function Write-Ok($value) {{ }}
function Deploy-SelfProvisioningBinstub {{ throw 'Unexpected redirect publication' }}
function Materialize-SnapshotDependencies {{ throw 'Source snapshot was rematerialized' }}
{_function(plugin, 'Invoke-Stamp', language)}
Initialize-SourceRuntime
Invoke-Stamp
"""
    else:
        script += """
_initialize_source_runtime
_stamp_source_snapshot
"""
    result = _run(tmp_path, language, script)
    assert result.returncode != 0, result.stdout + result.stderr
    if mutation_target in ("snapshot-tree", "existing-tree"):
        assert "Source snapshot identity changed after runtime selection" in result.stderr
    for marker in ("current-version", "stamped-version", "payload-dir"):
        assert (tmp_path / "install" / marker).read_text() == "previous"


@pytest.mark.parametrize("language", ["ps1", "sh"])
def test_codespaces_native_preinstall_accepts_noneditable_local_sources(
    tmp_path: Path, language: str,
) -> None:
    plugin = "agent-codespaces"
    payload, _ = _payload(tmp_path, plugin)
    script = _setup(tmp_path, payload, plugin, language)
    if language == "ps1":
        variables = (
            "SshMgrDir", "CredRelayDir", "CfgMigrateDir", "ZddDir", "VenueCopilotDir",
            "SessionLivenessProbeDir", "SingleInstanceLeaseDir", "RemoteLoginShellDir",
            "UpdateDriverDir",
        )
        script += "\n".join(
            f"${name} = Join-Path $PluginDir 'libs/local-lib'" for name in variables
        )
        script += f"""
$UvCommand = 'fixture-uv'
function Remove-ConsoleTrampolines {{ param($VenvDir) }}
function Invoke-UvPipInstallResilient {{
    param($UvCommand, $Arguments)
    if ($Arguments -contains '--editable') {{ throw 'Editable escaped source admission' }}
    if ($Arguments -notcontains '--reinstall-package') {{ throw 'Reinstall lost' }}
    [IO.File]::WriteAllText((Join-Path $InstallDir 'install-arguments.json'),
        (ConvertTo-Json -InputObject @($Arguments) -Compress))
    Rec 'INSTALL'
    return @{{ ExitCode = 0; Output = '' }}
}}
function Write-ServiceErr($value) {{ throw $value }}
{_function(plugin, 'Install-PackageInto', language)}
Initialize-SourceRuntime
if (-not (Install-PackageInto -Python (Join-Path $InstallDir 'venv/Scripts/python.exe'))) {{
    throw 'Preinstall failed'
}}
Assert-SourceRuntime
"""
    else:
        variables = (
            "SSH_MGR_DIR", "CRED_RELAY_DIR", "CFG_MIGRATE_DIR", "ZDD_DIR",
            "VENUE_COPILOT_DIR", "SESSION_LIVENESS_PROBE_DIR", "SINGLE_INSTANCE_LEASE_DIR",
            "REMOTE_LOGIN_SHELL_DIR",
            "UPDATE_DRIVER_DIR",
        )
        script += "\n".join(f'{name}="$PLUGIN_DIR/libs/local-lib"' for name in variables)
        script += f"""
UV_CMD=fixture-uv
invoke_uv_pip_install_resilient() {{
    [[ " $* " != *" --editable "* && " $* " == *" --reinstall-package "* ]] || return 1
    "$(_bootstrap_python)" -c 'import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))' \
        "$INSTALL_DIR/install-arguments.json" "$@"
    rec INSTALL
}}
{_function(plugin, '_install_package_into', language)}
_initialize_source_runtime
_install_package_into "$INSTALL_DIR/venv/bin/python"
_assert_source_runtime
"""
    result = _run(tmp_path, language, script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "events").read_text().splitlines() == ["INSTALL"]
    arguments = json.loads((tmp_path / "install/install-arguments.json").read_text())
    assert "--no-editable" in arguments and "--editable" not in arguments
    indices = [index for index, argument in enumerate(arguments) if argument == "--reinstall-package"]
    assert [arguments[index + 1] for index in indices] == [
        "agent-ssh-manager", "agent-credential-relay", "agent-config-migrate", "agent-zdd",
        "agent-venue-copilot", "agent-session-liveness-probe", "agent-single-instance-lease",
        "agent-remote-login-shell", "agent-update-driver", "agent-codespaces",
    ]
    assert [_quote(arguments[index + 2], language)[1:-1] for index in indices] == [
        _quote(payload / "libs/local-lib", language)[1:-1],
    ] * 9 + [_quote(payload, language)[1:-1]]


@pytest.mark.parametrize("language", ["ps1", "sh"])
@pytest.mark.parametrize("selector", ["current-version", "last-known-good", None])
def test_codespaces_vendored_resolver_requires_an_exact_source_selector(
    tmp_path: Path, language: str, selector: str | None,
) -> None:
    payload, selected = _payload(tmp_path, "agent-codespaces")
    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    shutil.copyfile(
        payload / "scripts" / f"resolve-runtime.{language}",
        install / "bin" / f"resolve-runtime.{language}",
    )
    dev = f"1.2.3-dev9+source.{'f' * 40}.{'f' * 16}"
    for version in (selected, dev, "1.2.2"):
        slot = install / "versions" / version
        windows_layout = os.name == "nt" or language == "ps1"
        interpreter = slot / ("Scripts/python.exe" if windows_layout else "bin/python")
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("# isolated resolver fixture\n", encoding="utf-8")
        interpreter.chmod(0o755)
        (slot / ".install-complete.json").write_text(
            json.dumps({"version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": 1}),
            encoding="utf-8",
        )
    if selector:
        (install / selector).write_text(selected, encoding="utf-8")
    q = _quote(install, language)
    resolver = _quote(install / "bin" / f"resolve-runtime.{language}", language)
    script = (
        f"$env:AGENT_RT_ROOT = {q}\n. {resolver} -TraceMode read-only\n$AgentRtPy\n"
        if language == "ps1" else
        f"export AGENT_RT_ROOT={q}\nAGENT_RT_RESOLVE_TRACE_MODE=read-only\n"
        f". {resolver}\nprintf '%s\\n' \"$AGENT_RT_PY\"\n"
    )
    result = _run(tmp_path, language, script)
    if selector:
        assert result.returncode == 0, result.stdout + result.stderr
        assert selected in result.stdout
    else:
        assert selected not in result.stdout
        assert "1.2.2" not in result.stdout
    assert dev not in result.stdout
