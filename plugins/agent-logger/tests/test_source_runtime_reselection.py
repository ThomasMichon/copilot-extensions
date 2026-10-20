"""Retained native transactions with real probes, leases and manifest writers."""
from __future__ import annotations

from functools import lru_cache
import json
import os
import runpy
from pathlib import Path
import shlex
import shutil
import sys
from types import SimpleNamespace
import venv

import pytest

from .test_source_runtime_install import (
    REPOSITORY,
    _function,
    _ps,
    _receipt,
    _run,
    _small_stage,
    source,
)


def _host():
    executable = (shutil.which("pwsh") or shutil.which("powershell")) if os.name == "nt" else shutil.which("bash")
    if not executable:
        pytest.skip("native installer shell unavailable")
    return executable


def _script(state, body, name):
    path = state.work / (name + (".ps1" if os.name == "nt" else ".sh"))
    path.write_text(body, encoding="utf-8", newline="\n")
    argv = (
        [_host(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)]
        if os.name == "nt" else [_host(), str(path)]
    )
    return _run(argv, env=state.env, cwd=state.payload / "src")


def _payload_hash(state):
    if os.name == "nt":
        body = f"$PluginDir = {_ps(state.payload)}\n"
        body += _function(state.plugin, "Get-PayloadHash", "ps1")
        body += "\n[Console]::Out.Write((Get-PayloadHash))\n"
    else:
        body = f"PLUGIN_DIR={shlex.quote(str(state.payload))}\n"
        body += _function(state.plugin, "_payload_hash", "sh") + "\n_payload_hash\n"
    result = _script(state, body, "fingerprint")
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


@lru_cache(maxsize=8)
def _powershell_functions(plugin, names, source_mtime):
    path = REPOSITORY / "plugins" / plugin / "scripts" / "install.ps1"
    wanted = ", ".join(_ps(name) for name in names)
    command = f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile({_ps(path)}, [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw ($errors | Out-String) }}
$definitions = [ordered]@{{}}
foreach ($wanted in @({wanted})) {{
    $found = @($tree.FindAll({{
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $wanted
    }}, $true))
    if ($found.Count -ne 1) {{ throw "Expected one real function: $wanted" }}
    $definitions[$wanted] = $found[0].Extent.Text
}}
$definitions | ConvertTo-Json -Depth 3 -Compress
"""
    result = _run([_host(), "-NoProfile", "-NonInteractive", "-Command", command])
    assert result.returncode == 0, result.stdout + result.stderr
    definitions = json.loads(result.stdout)
    return "\n".join(definitions[name] for name in names)


def _install_real_cli(site, plugin):
    project = REPOSITORY / "plugins" / plugin
    parse = runpy.run_path(str(project / "scripts" / "installer_dependency_policy.py"))["_toml"]
    pending, visited = [project], set()
    while pending:
        current = pending.pop().resolve()
        if current in visited:
            continue
        visited.add(current)
        shutil.copytree(
            current / "src", site, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
        )
        for declaration in parse(current / "pyproject.toml").get("tool", {}).get("uv", {}).get("sources", {}).values():
            if isinstance(declaration, dict) and "path" in declaration:
                pending.append(current / declaration["path"])
    if plugin == "agent-logger":
        import yaml

        shutil.copytree(Path(yaml.__file__).parent, site / "yaml", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


@pytest.fixture(autouse=True)
def _reclaim_case_runtimes(tmp_path, record_property):
    # Copied interpreters must not accumulate until the module's last case.
    targets = [tmp_path / "candidate-template"]
    targets += [tmp_path / "home" / ("." + plugin) / "versions"
                for plugin in ("agent-logger", "agent-vault")]
    try:
        yield
    finally:
        for target in targets:
            if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
                pytest.fail(f"Refusing linked runtime fixture cleanup target: {target}")
            if not target.exists():
                continue
            if not target.resolve().is_relative_to(tmp_path.resolve()):
                pytest.fail(f"Runtime fixture cleanup escaped its test directory: {target}")
            size = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
            record_property(f"runtime_fixture_bytes:{target.relative_to(tmp_path)}", size)
            print(f"Runtime fixture reclaimed: {target.relative_to(tmp_path)} ({size} bytes)")
            shutil.rmtree(target)
            assert not target.exists(), f"Runtime fixture cleanup failed: {target}"


def _case(tmp_path, plugin, *, incomplete_first=False):
    payload, fixtures = _small_stage(tmp_path, plugin)
    package = plugin.replace("-", "_")
    checkout = payload / "src" / package
    checkout.mkdir(parents=True)
    wrong_source = tmp_path / "checkout-imported"
    (checkout / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(wrong_source)!r}).write_text('checkout')\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    env = fixtures["_isolated_install_env"](home)
    env["COPILOT_PLUGIN_STAGED_FROM"] = str(payload)
    state = SimpleNamespace(
        plugin=plugin, payload=payload, work=tmp_path, home=home, env=env,
        root=home / ("." + plugin), wrong_source=wrong_source,
    )
    state.root.mkdir(parents=True, exist_ok=True)
    state.copy_blocker = tmp_path / "copy-blocker"
    state.copy_blocker.write_text("not a directory", encoding="utf-8")
    state.first = _receipt(payload, plugin, "a" * 40)
    source.publish_source_snapshot(payload, state.root, state.first)
    state.second = _receipt(payload, plugin, "c" * 40)
    source.publish_source_snapshot(payload, state.root, state.second)
    fingerprint = _payload_hash(state)
    first_slot = state.root / "versions" / state.first.runtime_version
    second_slot = state.root / "versions" / state.second.runtime_version
    state.template = tmp_path / "candidate-template" if incomplete_first else None
    first_environment = state.template if incomplete_first else first_slot
    venv.EnvBuilder(with_pip=False, symlinks=False).create(first_environment)
    shutil.copytree(first_environment, second_slot)
    state.packages = {}
    state.clis = {}
    state.probes = {}
    for identity, slot in ((state.first, first_environment), (state.second, second_slot)):
        site = slot / "Lib" / "site-packages" if os.name == "nt" else (
            slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        )
        module = site / package
        _install_real_cli(site, plugin)
        probe = tmp_path / (identity.source_commit[0] + "-probe")
        initializer = module / "__init__.py"
        initializer.write_text(
            initializer.read_text(encoding="utf-8") +
            f"\nimport sys\nfrom pathlib import Path\nPath({str(probe)!r}).write_text(sys.prefix)\n",
            encoding="utf-8",
        )
        metadata = site / (package + "-1.2.3.dist-info")
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {plugin}\nVersion: 1.2.3\n", encoding="utf-8",
        )
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        healthy = _run([str(python), "-I", "-m", package, "--help"], env=env, cwd=payload / "src")
        assert healthy.returncode == 0, healthy.stdout + healthy.stderr
        if not (incomplete_first and identity == state.first):
            with source._runtime().construction_lease(state.root, identity.runtime_version):
                source._runtime().mark_complete(state.root, identity.runtime_version, payload_hash=fingerprint)
        state.packages[identity.runtime_version] = module / "__init__.py"
        state.clis[identity.runtime_version] = module / ("cli.py" if plugin == "agent-vault" else "__main__.py")
        state.probes[identity.runtime_version] = probe
    selected = state.second if incomplete_first else state.first
    source._runtime().activate(state.root, selected.runtime_version, link_free=True)
    state.completions = {
        identity.runtime_version: (
            (state.root / "versions" / identity.runtime_version / ".install-complete.json").stat().st_mtime_ns,
            (state.root / "versions" / identity.runtime_version / ".install-complete.json").read_bytes(),
        )
        for identity in (state.first, state.second)
        if not (incomplete_first and identity == state.first)
    }
    return state


def _reuse(state, identity, *, fail_helper_copy=False, build_candidate=False, stamp_only=False):
    assert not build_candidate or state.template is not None
    document = json.dumps(identity.document())
    (state.payload / "runtime-source.json").write_text(document, encoding="utf-8")
    if os.name == "nt":
        names = [
            "Get-PayloadHash", "Invoke-VersionedSlotClean", "Invoke-VersionedActivate",
            "Get-SourceKind",
        ]
        names += (
            [
                "Install-Package", "Invoke-VersionedMarkComplete", "Write-LoggerDeployManifest",
                "Publish-PayloadSnapshot", "Test-SnapshotRequiresMaterializedEngine",
                "Materialize-SnapshotLibs", "Materialize-SnapshotInstallerEngine",
                "Resolve-SnapshotInstallerEngineSource",
                "Deploy-ResolverHelpers", "Test-LoggerRuntimeHealthy",
                "Remove-ConsoleTrampolines", "Remove-LoggerTrampolines",
                "Write-SyncTaskLauncher", "Get-ConfigRepoRegistrationPath",
                "Invoke-Stamp",
            ]
            if state.plugin == "agent-logger" else
            [
                "Install-Runtime", "Test-RuntimeHealthy", "Test-VenvIsLink", "Write-VaultDeployManifest",
                "Write-Binstubs", "Remove-ConsoleTrampolines", "Get-VersionedCurrent",
                "Invoke-VersionedMarkComplete", "Invoke-VersionedGc", "Test-KeePassXCCli",
                "Invoke-Stamp",
            ]
        )
        path = REPOSITORY / "plugins" / state.plugin / "scripts" / "install.ps1"
        functions = _powershell_functions(state.plugin, tuple(names), path.stat().st_mtime_ns)
        functions = functions.replace("$PSScriptRoot", "$ScriptDir")
        body = f"""
$ErrorActionPreference = 'Stop'
$ScriptDir = {_ps(state.payload / 'scripts')}
$PluginDir = {_ps(state.payload)}
$InstallDir = {_ps(state.root)}
$LocalBin = {_ps(state.home / '.local' / 'bin')}
$PkgSrcDir = Join-Path $PluginDir 'src\\agent_vault'
$SrcVersion = '1.2.3'
$VersionedRuntime = $true
$VenvDir = Join-Path $InstallDir '.venv'
$LinkDir = $VenvDir
$TaskName = 'retained-source-fixture'
$TaskLauncher = Join-Path $InstallDir 'bin\\session-sync-task.ps1'
$publishGlobalBinstubs = $false
$BuildCandidate = ${str(build_candidate).lower()}
$FailHelperCopy = ${str(fail_helper_copy).lower()}
$CandidateTemplate = {_ps(state.template or '')}
$CopyBlocker = {_ps(state.copy_blocker)}
. (Join-Path $ScriptDir 'installer-engine.ps1')
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
. (Join-Path $ScriptDir 'source-runtime.ps1')
function Get-BootstrapPython {{ return {_ps(sys.executable)} }}
function Resolve-PythonCommand {{ return {_ps(sys.executable)} }}
function Ensure-UvIndex {{ }}
function Ensure-Uv {{ return 'unused-retained-provider' }}
function New-SignedVenv {{
    param($VenvDir, $VenvPython, $PythonVersion, $UvCommand, $RequireSignedBase, $AllowExisting)
    if (-not $BuildCandidate) {{ throw 'A completed slot must not rebuild' }}
    Get-ChildItem -LiteralPath $CandidateTemplate -Force | ForEach-Object {{
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $VenvDir $_.Name) -Recurse -Force
    }}
    return $true
}}
function Invoke-UvPipInstallResilient {{
    param($UvCommand, $Arguments, $PayloadDirToScrub)
    if (-not $BuildCandidate) {{ throw 'A completed slot must not install packages' }}
    return [pscustomobject]@{{ ExitCode=0; Output='' }}
}}
function Copy-Item {{
    [CmdletBinding()]
    param([Parameter(Position=0)]$Path, [Parameter(Position=1)]$Destination, $LiteralPath,
          [switch]$Force, [switch]$Recurse)
    $inputPath = if ($LiteralPath) {{ $LiteralPath }} else {{ $Path }}
    if ($FailHelperCopy -and [IO.Path]::GetFileName($inputPath) -eq 'resolve-runtime.ps1') {{
        [Console]::Error.WriteLine('injected required helper copy failure')
        Microsoft.PowerShell.Management\\Copy-Item -LiteralPath $inputPath `
            -Destination (Join-Path $CopyBlocker 'child') -Force -ErrorAction Stop
        return
    }}
    Microsoft.PowerShell.Management\\Copy-Item @PSBoundParameters
}}
function Invoke-Stop {{ }}
function Stop-ScheduledTask {{ }}
function Get-CopilotPersistentEnvironmentVariable {{ return $LocalBin }}
function Set-CopilotPersistentEnvironmentVariable {{ throw 'Unexpected host environment mutation' }}
function Get-GitInfo {{ return @{{ commit='fixture'; branch='fixture'; dirty=$false }} }}
function Write-Fail($value) {{ Write-Host $value }}
function Write-Step($value) {{ }}
function Write-Ok($value) {{ }}
function Write-Warn($value) {{ }}
{functions}
Initialize-SourceRuntime
try {{
    {'Invoke-Stamp' if stamp_only else ('Install-Package' if state.plugin == 'agent-logger' else 'Install-Runtime')}
}} finally {{
    if (Get-Command Stop-RuntimeConstruction -ErrorAction SilentlyContinue) {{ Stop-RuntimeConstruction }}
}}
"""
    else:
        names = ["_payload_hash", "_versioned_slot_clean", "_versioned_activate", "_write_runtime_manifest", "_source_kind"]
        names += (
            [
                "install_package", "_versioned_mark_complete", "publish_payload_snapshot",
                "_snapshot_requires_materialized_engine", "_materialize_snapshot_libs",
                "_materialize_snapshot_engine", "_resolve_snapshot_engine_source",
                "_rewrite_snapshot_engine_ref",
                "deploy_runtime_helpers", "_test_runtime_healthy",
                "do_stamp",
            ]
            if state.plugin == "agent-logger" else
            [
                "_ensure_runtime", "_test_runtime_healthy", "do_install",
                "_downgrade_guard", "_installed_version", "_source_version", "_version_lt",
                "_deploy_binstub", "_write_askpass", "_versioned_current",
                "_versioned_mark_complete", "_versioned_gc", "_check_keepassxc",
                "do_stamp",
            ]
        )
        functions = "\n".join(_function(state.plugin, name, "sh") for name in names)
        installer = (state.payload / "scripts" / "install.sh").read_text(encoding="utf-8")
        dispatch = installer[installer.rindex('case "${ACTION}" in' if state.plugin == "agent-logger" else 'case "$ACTION" in'):]
        body = f"""
set -euo pipefail
PLUGIN_DIR={shlex.quote(str(state.payload))}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={shlex.quote(str(state.root))}
LOCAL_BIN={shlex.quote(str(state.home / '.local' / 'bin'))}
PKG_SRC_DIR="$PLUGIN_DIR/src/agent_vault"
SRC_VERSION=1.2.3
LINK_DIR="$INSTALL_DIR/.venv"
VERSIONED_RUNTIME=1
SKIP_PACKAGE_INSTALL=0
SKIP_STAMP=0
ACTION={'stamp' if stamp_only else 'provision'}
FORCE=0
PUBLISH_GLOBAL_BINSTUBS=0
ASKPASS="$LOCAL_BIN/vault-askpass"
_build_candidate={1 if build_candidate else 0}
_fail_helper_copy={1 if fail_helper_copy else 0}
_candidate_template={shlex.quote(str(state.template or ''))}
_copy_blocker={shlex.quote(str(state.copy_blocker))}
. "$SCRIPT_DIR/installer-engine.sh"
. "$SCRIPT_DIR/source-runtime.sh"
_bootstrap_python() {{ printf '%s\\n' {shlex.quote(sys.executable)}; }}
_find_python() {{ printf '%s\\n' {shlex.quote(sys.executable)}; }}
_ensure_uv_index() {{ :; }}
ensure_uv() {{ printf '%s\\n' unused-retained-provider; }}
new_signed_venv() {{
    [[ "$_build_candidate" == 1 ]] || {{ printf '%s\\n' 'A completed slot must not rebuild' >&2; return 71; }}
    command cp -a "$_candidate_template/." "$2/"
}}
invoke_uv_pip_install_resilient() {{
    [[ "$_build_candidate" == 1 ]] || {{ printf '%s\\n' 'A completed slot must not install packages' >&2; return 71; }}
}}
cp() {{
    if [[ "$_fail_helper_copy" == 1 && "${{1:-}}" == -f && "${{2:-}}" == "$SCRIPT_DIR/resolve-runtime.ps1" ]]; then
        printf '%s\\n' 'injected required helper copy failure' >&2
        command cp -f "$2" "$_copy_blocker/child"
        return "$?"
    fi
    command cp "$@"
}}
_install_service() {{ :; }}
_git_info() {{ printf '%s\\n' 'fixture fixture false'; }}
_fail() {{ printf '%s\\n' "$1" >&2; }}
_warn() {{ :; }}
warn() {{ :; }}
_ok() {{ :; }}
ok() {{ :; }}
_step() {{ :; }}
chg() {{ :; }}
{functions}
_initialize_source_runtime
{dispatch}
"""
    return _script(state, body, "reuse-" + identity.source_commit[0])


def _assert_completion_unchanged(state):
    for version, (mtime, content) in state.completions.items():
        path = state.root / "versions" / version / ".install-complete.json"
        assert (path.stat().st_mtime_ns, path.read_bytes()) == (mtime, content)


@pytest.mark.parametrize("plugin", ["agent-logger", "agent-vault"])
def test_retained_source_reselection_publishes_exact_manifest_without_recompletion(tmp_path, plugin):
    state = _case(tmp_path, plugin)
    for identity in (state.first, state.second, state.first):
        result = _reuse(state, identity)
        assert result.returncode == 0, result.stdout + result.stderr
        manifest = json.loads((state.root / "deploy-manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == 3
        assert manifest["source"]["version"] == identity.package_version
        assert manifest["runtime_version"] == identity.runtime_version
        assert manifest["runtime_source"] == identity.document()
        assert Path(manifest["venv"]).resolve() == (state.root / "versions" / identity.runtime_version).resolve()
        assert (state.root / "current-version").read_text().strip() == identity.runtime_version
        assert Path(state.probes[identity.runtime_version].read_text()).resolve() == (
            state.root / "versions" / identity.runtime_version
        ).resolve()
        _assert_completion_unchanged(state)
    assert not state.wrong_source.exists()


def test_corrupt_retained_vault_import_preserves_healthy_selection_and_manifest(tmp_path):
    state = _case(tmp_path, "agent-vault")
    selected = _reuse(state, state.second)
    assert selected.returncode == 0, selected.stdout + selected.stderr
    names = ("current-version", "last-known-good", "deploy-manifest.json", "payload-dir", "stamped-version")
    before = {name: (state.root / name).read_bytes() for name in names}
    module = state.packages[state.first.runtime_version]
    module.write_text(
        module.read_text(encoding="utf-8") + "\nraise RuntimeError('retained slot import refused')\n",
        encoding="utf-8",
    )
    result = _reuse(state, state.first)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "retained slot import refused" in result.stdout + result.stderr
    assert all((state.root / name).read_bytes() == value for name, value in before.items())
    _assert_completion_unchanged(state)
    assert not state.wrong_source.exists()


@pytest.mark.parametrize("plugin", ["agent-logger", "agent-vault"])
def test_broken_retained_cli_with_healthy_initializer_is_not_selected_and_can_retry(tmp_path, plugin):
    state = _case(tmp_path, plugin)
    baseline = _reuse(state, state.second)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    before = _protected_publication(state)
    cli = state.clis[state.first.runtime_version]
    healthy_body = cli.read_text(encoding="utf-8")
    future = "from __future__ import annotations\n"
    assert future in healthy_body
    cli.write_text(
        healthy_body.replace(future, future + "\nraise RuntimeError('retained CLI body refused')\n", 1),
        encoding="utf-8",
    )
    slot = state.root / "versions" / state.first.runtime_version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    initializer = _run([str(python), "-I", "-c", f"import {plugin.replace('-', '_')}"],
                       env=state.env, cwd=state.payload / "src")
    assert initializer.returncode == 0, initializer.stdout + initializer.stderr
    failed = _reuse(state, state.first)
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert "retained CLI body refused" in failed.stdout + failed.stderr
    assert _protected_publication(state) == before
    _assert_completion_unchanged(state)
    cli.write_text(healthy_body, encoding="utf-8")
    repaired = _reuse(state, state.first)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    _assert_manifest_and_helpers(state, state.first)
    _assert_completion_unchanged(state)
    assert not state.wrong_source.exists()


def _protected_publication(state):
    names = ("current-version", "last-known-good", "deploy-manifest.json", "payload-dir", "stamped-version")
    paths = [state.root / name for name in names]
    paths += [path for directory in (state.root / "bin", state.home / ".local" / "bin")
              for path in directory.rglob("*") if path.is_file()]
    return {str(path): (path.stat().st_mtime_ns, path.read_bytes()) for path in paths}


def _assert_manifest_and_helpers(state, identity):
    manifest = json.loads((state.root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["runtime_version"] == identity.runtime_version
    assert manifest["runtime_source"] == identity.document()
    assert Path(manifest["venv"]).resolve() == (
        state.root / "versions" / identity.runtime_version
    ).resolve()
    required = ["resolve-runtime.ps1", "resolve-runtime.sh"]
    if state.plugin == "agent-logger":
        required += [
            "versioned_runtime.py", "versioned_runtime_source.py",
            "versioned_runtime_publication.py",
            "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        ]
    for name in required:
        assert (state.root / "bin" / name).read_bytes() == (state.payload / "scripts" / name).read_bytes()
    assert (state.root / "current-version").read_text().strip() == identity.runtime_version


@pytest.mark.parametrize("plugin", ["agent-logger", "agent-vault"])
@pytest.mark.parametrize("fresh", [False, True], ids=["retained", "fresh"])
def test_native_helper_copy_failure_then_retry_preserves_selection_before_activation(tmp_path, plugin, fresh):
    state = _case(tmp_path, plugin, incomplete_first=fresh)
    baseline = _reuse(state, state.second)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    before = _protected_publication(state)
    probe_before = state.probes[state.first.runtime_version].stat().st_mtime_ns
    failed = _reuse(state, state.first, fail_helper_copy=True, build_candidate=fresh)
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert "injected required helper copy failure" in failed.stdout + failed.stderr
    assert "copy-blocker" in failed.stdout + failed.stderr
    assert _protected_publication(state) == before
    assert state.probes[state.first.runtime_version].stat().st_mtime_ns > probe_before
    assert Path(state.probes[state.first.runtime_version].read_text()).resolve() == (
        state.root / "versions" / state.first.runtime_version
    ).resolve()
    _assert_completion_unchanged(state)
    if fresh:
        assert not (state.root / "versions" / state.first.runtime_version / ".install-complete.json").exists()
    repaired = _reuse(state, state.first, build_candidate=fresh)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    _assert_manifest_and_helpers(state, state.first)
    _assert_completion_unchanged(state)
    assert (state.root / "versions" / state.first.runtime_version / ".install-complete.json").is_file()
    assert not state.wrong_source.exists()


@pytest.mark.parametrize("plugin", ["agent-logger", "agent-vault"])
def test_selected_retained_source_retry_repairs_missing_runtime_resolver(tmp_path, plugin):
    state = _case(tmp_path, plugin)
    baseline = _reuse(state, state.second)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    selectors = {name: (state.root / name).read_bytes()
                 for name in ("current-version", "last-known-good")}
    (state.root / "bin" / "resolve-runtime.ps1").unlink()
    repaired = _reuse(state, state.second)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert all((state.root / name).read_bytes() == value for name, value in selectors.items())
    _assert_manifest_and_helpers(state, state.second)
    _assert_completion_unchanged(state)


@pytest.mark.parametrize("plugin", ["agent-logger", "agent-vault"])
def test_native_source_stamp_helper_copy_failure_preserves_old_payload_pointer(tmp_path, plugin):
    state = _case(tmp_path, plugin)
    baseline = _reuse(state, state.second)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    before = _protected_publication(state)
    failed = _reuse(state, state.first, fail_helper_copy=True, stamp_only=True)
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert "injected required helper copy failure" in failed.stdout + failed.stderr
    assert _protected_publication(state) == before
    _assert_completion_unchanged(state)
