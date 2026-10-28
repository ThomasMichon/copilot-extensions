"""Contained native action admission against the real read-only policy helper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
INSTALLER = SCRIPTS / "install.ps1"
GATE = SCRIPTS / "runtime-gate.ps1"
BOOTSTRAP = SCRIPTS / "bootstrap-check.ps1"
PWSH = shutil.which("pwsh")
HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def function(path: Path, name: str) -> str:
    match = re.search(
        rf"(?ms)^function {re.escape(name)}(?:\s|\().*?^\}}",
        path.read_text(encoding="utf-8"),
    )
    assert match, name
    return match.group(0)


def quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(
    tmp_path: Path, body: str, *, policy_required: bool = True,
) -> subprocess.CompletedProcess[str]:
    if PWSH is None:
        pytest.skip("pwsh is not available; native PowerShell execution required")
    if policy_required and not (SCRIPTS / "action_controls.py").is_file():
        pytest.skip("shared read-only action policy helper is not available")
    harness = tmp_path / "action-test.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n" + body,
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["HOME"] = env["USERPROFILE"] = str(tmp_path)
    env["TMPDIR"] = env["TMP"] = env["TEMP"] = str(tmp_path)
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(tmp_path / "bootstrap-policy.json")
    env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True, text=True, timeout=25, creationflags=HEADLESS, env=env,
    )


def installer_policy(tmp_path: Path) -> str:
    return (
        f". {quote(SCRIPTS / 'versioned_runtime_native.ps1')}\n"
        f"$script:ActionControlsHelper = {quote(SCRIPTS / 'action_controls.py')}\n"
        f"$InstallDir = {quote(tmp_path / 'runtime')}\n"
        f"function Get-BootstrapPython {{ return {quote(sys.executable)} }}\n"
        + function(INSTALLER, "Assert-DispatchActions") + "\n"
    )


@pytest.mark.parametrize("selection", ["NoSupervisor", "NoService"])
def test_supervisor_denial_preserves_native_roots_then_same_caller_clears(
    tmp_path: Path, selection: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    policy = root / "action-controls.json"
    policy.write_text(json.dumps({
        "supervisor-reconcile": {"disabled": True, "reason": "maintenance hold"},
    }), encoding="utf-8")
    body = (
        installer_policy(tmp_path) + function(INSTALLER, "Assert-InstallActions")
        + "\n" + function(INSTALLER, "Install-SupervisorTask")
    )
    body += f"""
$env:OS = 'Windows_NT'
${selection} = $true
$Force = $true
$SupervisorTaskName = 'contained-supervisor'
$script:effects = @()
function Invoke-SupervisorAudit {{}}
function Write-Skip {{ param($Message) }}
function Write-Step {{ param($Message) }}
function Invoke-SupervisorsStop {{ $script:effects += 'stop' }}
function Retire-SupervisorProcesses {{ $script:effects += 'reap'; return 0 }}
function Remove-AllSupervisorTasks {{ $script:effects += 'remove' }}
[IO.File]::WriteAllText({quote(policy)}, '{{}}')
$Action = 'update'
Assert-InstallActions
[IO.File]::WriteAllText({quote(policy)}, {quote(policy.read_text(encoding="utf-8"))})
try {{ Install-SupervisorTask; throw 'denial was bypassed' }}
catch {{
    if ($_.Exception.Message -notmatch 'supervisor-reconcile') {{ throw }}
}}
if ($script:effects.Count) {{ throw 'denied supervisor touched native roots' }}
[IO.File]::WriteAllText({quote(policy)}, '{{}}')
Install-SupervisorTask
if (($script:effects -join ',') -cne 'stop,reap,remove') {{
    throw "clear did not restore existing reconciliation: $script:effects"
}}
"""
    result = run_ps(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("action", [
    "runtime-activation", "coordinator-replacement", "supervisor-reconcile",
])
def test_full_preflight_rejects_before_reconcile_or_publication(
    tmp_path: Path, action: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "action-controls.json").write_text(json.dumps({
        action: {"disabled": True},
    }), encoding="utf-8")
    body = installer_policy(tmp_path) + function(INSTALLER, "Assert-InstallActions")
    body += """
$Action = 'update'
$NoService = $true
$NoSupervisor = $true
$Force = $true
Assert-InstallActions
throw 'native reconciliation boundary reached'
"""
    result = run_ps(tmp_path, body)
    assert result.returncode != 0
    assert action in result.stderr
    assert "native reconciliation boundary reached" not in result.stderr
    source = INSTALLER.read_text(encoding="utf-8")
    dispatch = source[source.index("$script:InstallerLifecycleAction ="):]
    assert dispatch.index("Assert-InstallActions") < dispatch.index("Initialize-SourceRuntime")
    assert dispatch.index("Assert-InstallActions") < dispatch.index("Invoke-InstallerInstanceReconcile")


def test_invalid_policy_is_not_permission(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "action-controls.json").write_text("{", encoding="utf-8")
    result = run_ps(
        tmp_path, installer_policy(tmp_path)
        + "\nAssert-DispatchActions @('runtime-activation')\n",
    )
    assert result.returncode != 0
    assert "runtime-activation" in result.stderr


def test_cold_gate_denial_precedes_filesystem_and_provision_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "action-controls.json").write_text(json.dumps({
        "automatic-runtime-reconcile": {"disabled": True},
    }), encoding="utf-8")
    body = (
        f"$payloadRoot = {quote(SCRIPTS.parent)}\n"
        f"$runtimeRoot = {quote(root)}\n"
        + function(GATE, "Assert-AutomaticRuntimeReconcile")
        + "\nAssert-AutomaticRuntimeReconcile\nthrow 'provision boundary reached'\n"
    )
    result = run_ps(tmp_path, body)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "automatic-runtime-reconcile" in result.stderr
    assert "provision boundary reached" not in result.stderr
    assert not (root / ".provision.lock").exists()
    source = GATE.read_text(encoding="utf-8")
    warm = source.index("if ($python) { Invoke-Runtime $python }")
    cold = source.index("\nAssert-AutomaticRuntimeReconcile\n")
    assert warm < cold < source.index("[IO.Directory]::CreateDirectory($runtimeRoot)")
    assert source.index("$python = Resolve-Runtime normal") < cold
    installer = function(GATE, "Invoke-Installer")
    assert installer.index("Assert-AutomaticRuntimeReconcile") < installer.index("& $hostExe")


@pytest.mark.parametrize("resolution", ["ready", "normal"])
def test_ready_runtime_executes_before_any_action_policy_prerequisite(
    tmp_path: Path, resolution: str,
) -> None:
    source = GATE.read_text(encoding="utf-8")
    if resolution == "ready":
        start = source.index("\n$python = Resolve-Runtime\n")
        end = source.index("\nif ($env:AGENT_DISPATCH_NO_SELFPROVISION)", start)
    else:
        start = source.index("\n$provisionSource = Select-ProvisionSource\n")
        end = source.index("\n[void][IO.Directory]::CreateDirectory($runtimeRoot)", start)
    body = (
        f"$runtimeRoot = {quote(tmp_path / 'runtime')}\n"
        "$context = ''\n$module = 'json.tool'\n$forwardArgs = @('--help')\n"
        f"function Resolve-Runtime {{ return {quote(sys.executable)} }}\n"
        "function Select-ProvisionSource { return @{} }\n"
        + function(GATE, "Invoke-Runtime") + "\n"
        + source[start:end]
        + "\nthrow 'warm invocation fell through to cold policy'\n"
    )
    result = run_ps(tmp_path, body, policy_required=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--help" in result.stdout
    assert "cold policy" not in result.stderr
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("missing", ["helper", "python"])
def test_missing_policy_prerequisite_never_admits_action(
    tmp_path: Path, missing: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "action-controls.json").write_text("{}", encoding="utf-8")
    body = installer_policy(tmp_path)
    if missing == "helper":
        body += f"\n$script:ActionControlsHelper = {quote(tmp_path / 'missing.py')}\n"
    else:
        body += "\nfunction Get-BootstrapPython { return $null }\n"
    body += "\nAssert-DispatchActions @('runtime-activation')\nthrow 'admitted'\n"
    result = run_ps(tmp_path, body, policy_required=False)
    assert result.returncode != 0
    assert "runtime-activation" in result.stderr
    assert "prerequisite unavailable" in result.stderr
    assert "throw 'admitted'" not in result.stderr


@pytest.mark.parametrize("existing_root", [False, True])
def test_absent_explicit_policy_needs_neither_python_nor_helper(
    tmp_path: Path, existing_root: bool,
) -> None:
    root = tmp_path / "runtime"
    if existing_root:
        root.mkdir()
    body = installer_policy(tmp_path)
    body += f"""
$script:ActionControlsHelper = {quote(tmp_path / 'missing.py')}
function Get-BootstrapPython {{ throw 'Python discovery must not run for absent policy' }}
Assert-DispatchActions @('runtime-activation')
Assert-DispatchActions @('coordinator-replacement', 'supervisor-reconcile')
"""
    result = run_ps(tmp_path, body, policy_required=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert root.exists() == existing_root


def test_absence_proof_is_not_cached_when_explicit_policy_appears(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    body = installer_policy(tmp_path)
    body += f"""
$script:ActionControlsHelper = {quote(tmp_path / 'missing.py')}
Assert-DispatchActions @('runtime-activation')
[void][IO.Directory]::CreateDirectory($InstallDir)
[IO.File]::WriteAllText((Join-Path $InstallDir 'action-controls.json'), '{{}}')
Assert-DispatchActions @('runtime-activation')
throw 'new explicit policy was bypassed'
"""
    result = run_ps(tmp_path, body, policy_required=False)
    assert result.returncode != 0
    assert "runtime-activation" in result.stderr
    assert "prerequisite unavailable" in result.stderr
    assert (root / "action-controls.json").is_file()
    assert "throw 'new explicit policy was bypassed'" not in result.stderr


@pytest.mark.parametrize("linked", ["policy", "root", "ancestor"])
def test_linked_path_is_never_an_absent_policy(tmp_path: Path, linked: str) -> None:
    root = tmp_path / "runtime"
    try:
        if linked == "policy":
            root.mkdir()
            (root / "action-controls.json").symlink_to(tmp_path / "missing-policy")
        else:
            target = tmp_path / "target"
            target.mkdir()
            root.symlink_to(target, target_is_directory=True)
            if linked == "ancestor":
                root = root / "not-created"
    except OSError as exc:
        pytest.skip(f"native symbolic-link support is unavailable: {exc}")
    body = installer_policy(tmp_path)
    body += f"""
$InstallDir = {quote(root)}
$script:ActionControlsHelper = {quote(tmp_path / 'missing.py')}
Assert-DispatchActions @('runtime-activation')
throw 'linked policy path was admitted'
"""
    result = run_ps(tmp_path, body, policy_required=False)
    assert result.returncode != 0
    assert "runtime-activation" in result.stderr
    assert "linked policy path" in result.stderr
    assert "throw 'linked policy path was admitted'" not in result.stderr


@pytest.mark.parametrize("unreadable", ["policy", "ancestor"])
def test_permission_denial_is_not_policy_absence(tmp_path: Path, unreadable: str) -> None:
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("contained POSIX access-denial fixture requires an unprivileged user")
    root = tmp_path / "runtime"
    root.mkdir()
    policy = root / "action-controls.json"
    policy.write_text("{}", encoding="utf-8")
    inaccessible = policy if unreadable == "policy" else root
    inaccessible.chmod(0)
    try:
        result = run_ps(
            tmp_path, installer_policy(tmp_path)
            + "\nAssert-DispatchActions @('runtime-activation')\nthrow 'access denial bypassed'\n",
        )
    finally:
        inaccessible.chmod(0o600 if unreadable == "policy" else 0o700)
    assert result.returncode != 0
    assert "runtime-activation" in result.stderr
    assert "throw 'access denial bypassed'" not in result.stderr


@pytest.mark.parametrize("graceful_required", [False, True])
def test_cutover_denial_is_terminal_even_when_policy_clears_before_return(
    tmp_path: Path, graceful_required: bool,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    fixture = tmp_path / "contained-cutover"
    package = fixture / "agent_dispatch"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "root = Path(os.environ['ACTION_TEST_ROOT'])\n"
        "policy = root / 'action-controls.json'\n"
        "policy.write_text(json.dumps({'coordinator-replacement': {'disabled': True}}), encoding='utf-8')\n"
        "denied = subprocess.run([sys.executable, '-I', '-B', os.environ['ACTION_TEST_HELPER'],\n"
        "    '--root', str(root), '--action', 'coordinator-replacement'],\n"
        "    capture_output=True, text=True, timeout=10)\n"
        "policy.write_text('{}', encoding='utf-8')\n"
        "sys.stderr.write(denied.stderr)\n"
        "print(json.dumps({'ok': True}))\n"
        "raise SystemExit(denied.returncode)\n",
        encoding="utf-8",
    )
    body = (
        installer_policy(tmp_path)
        + function(INSTALLER, "Invoke-CoordinatorCutover") + "\n"
        + function(INSTALLER, "Invoke-Update")
    )
    body += f"""
$env:PYTHONPATH = {quote(fixture)}
$env:ACTION_TEST_ROOT = {quote(root)}
$env:ACTION_TEST_HELPER = {quote(SCRIPTS / 'action_controls.py')}
$VenvPython = {quote(sys.executable)}
$LinkPython = $VenvPython
$NoService = $false
$RequireGracefulCutover = ${str(graceful_required).lower()}
$script:GlobalActivationLockTimeoutSeconds = 1
$script:fallback = $false
$script:cleared = $false
function Write-Step {{ param($Message) }}
function Write-Ok {{ param($Message) }}
function Write-Warn {{ param($Message) }}
function Write-UpdateMarker {{}}
function Clear-UpdateMarker {{ $script:cleared = $true }}
function Invoke-DowngradeGuard {{}}
function Install-Runtime {{}}
function Test-ActivationSupersededNow {{ return $false }}
function Test-CoordinatorHealthy {{ return $true }}
function Test-CoordinatorRouted {{ return $true }}
function Enter-PluginSnapshotLock {{
    $mutex = New-Object PSObject
    $mutex | Add-Member ScriptMethod ReleaseMutex {{ return $true }}
    $mutex | Add-Member ScriptMethod Dispose {{}}
    return $mutex
}}
function Install-CoordinatorTask {{ $script:fallback = $true }}
function Install-SupervisorTask {{ $script:fallback = $true }}
function Confirm-CoordinatorRunning {{ $script:fallback = $true }}
function Remove-CoordinatorFirewallRule {{ $script:fallback = $true }}
function Invoke-SupervisorAudit {{}}
try {{ Invoke-Update; throw 'update incorrectly returned success' }}
catch {{
    if ($_.Exception.Message -notmatch 'coordinator-replacement inhibited during cutover' -or
        $_.Exception.Message -notmatch 'runtime selection may already have changed') {{ throw }}
    [Console]::Error.WriteLine($_.Exception.Message)
}}
if ($script:fallback) {{ throw 'cutover denial reached restart/reconcile fallback' }}
if (-not $script:cleared) {{ throw 'update marker cleanup was lost' }}
Assert-DispatchActions @('coordinator-replacement')
"""
    result = run_ps(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "coordinator-replacement" in result.stderr
    assert json.loads((root / "action-controls.json").read_text(encoding="utf-8")) == {}


def test_upstream_provision_denial_cannot_fall_back_after_policy_clears(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    upstream = tmp_path / "upstream.ps1"
    upstream.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". {quote(SCRIPTS / 'versioned_runtime_native.ps1')}\n"
        f"$policy = {quote(root / 'action-controls.json')}\n"
        "[IO.File]::WriteAllText($policy, "
        """'{"automatic-runtime-reconcile":{"disabled":true}}')\n"""
        "$diagnostic = ''\n"
        f"$null = Invoke-Hidden -FilePath {quote(sys.executable)} -ArgList @("
        f"'-I', '-B', {quote(SCRIPTS / 'action_controls.py')}, '--root', {quote(root)}, "
        "'--action', 'automatic-runtime-reconcile', '--automatic') "
        "-ErrorOutput ([ref]$diagnostic)\n"
        "$code = $LASTEXITCODE\n"
        "[IO.File]::WriteAllText($policy, '{}')\n"
        "[Console]::Error.Write($diagnostic)\n"
        "exit $code\n",
        encoding="utf-8",
    )
    fallback = tmp_path / "local.ps1"
    marker = tmp_path / "local-called"
    fallback.write_text(
        f"[IO.File]::WriteAllText({quote(marker)}, 'called')\nexit 0\n",
        encoding="utf-8",
    )
    source = GATE.read_text(encoding="utf-8")
    provision = source[source.index("\ntry {\n    $python = Resolve-Runtime\n"):]
    body = (
        f"$payloadRoot = {quote(SCRIPTS.parent)}\n"
        f"$runtimeRoot = {quote(root)}\n"
        f"$statusPath = {quote(root / '.provision-status')}\n"
        "$rc = 1\n$globalSurface = $true\n"
        "$env:AGENT_DISPATCH_PROVISION_OFFLINE = $null\n"
        "$lock = New-Object PSObject\n"
        "$lock | Add-Member ScriptMethod Dispose {}\n"
        + function(GATE, "Assert-AutomaticRuntimeReconcile") + "\n"
        + function(GATE, "Invoke-Installer") + "\n"
        "function Resolve-Runtime { return $null }\n"
        "function Assert-Publication { Assert-AutomaticRuntimeReconcile }\n"
        f"function Select-ProvisionSource {{ return @{{SourcePinned=$false; Installer={quote(fallback)}}} }}\n"
        "function Invoke-WebRequest {\n"
        " param($Uri, [switch]$UseBasicParsing, $TimeoutSec, $OutFile)\n"
        f" Copy-Item -LiteralPath {quote(upstream)} -Destination $OutFile\n"
        "}\n"
        + provision
    )
    result = run_ps(tmp_path, body)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "no local snapshot fallback attempted" in result.stderr
    assert not marker.exists()
    assert (root / ".provision-status").read_text(encoding="utf-8") == "failed rc=3"
    assert json.loads((root / "action-controls.json").read_text(encoding="utf-8")) == {}


@pytest.mark.parametrize("phase", ["stamp", "drift", "ready", "prerequisite"])
def test_actual_bootstrap_denial_is_output_free_and_ready_is_ungated(
    tmp_path: Path, phase: str,
) -> None:
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    hook = scripts / "bootstrap-check.ps1"
    hook.write_bytes(BOOTSTRAP.read_bytes())
    (payload / "plugin.json").write_text('{"name":"agent-dispatch"}', encoding="utf-8")
    (payload / "pyproject.toml").write_text('[project]\nversion = "9.0.0"\n', encoding="utf-8")
    marker = tmp_path / "installer-called"
    (scripts / "install.ps1").write_text(
        "# 'stamp'\n"
        f"[IO.File]::WriteAllText({quote(marker)}, 'called')\nexit 0\n",
        encoding="utf-8",
    )
    root = tmp_path / ".agent-dispatch"
    root.mkdir()
    if phase != "prerequisite":
        (root / "action-controls.json").write_text(json.dumps({
            "automatic-runtime-reconcile": {"disabled": True},
        }), encoding="utf-8")
    if phase in ("drift", "ready"):
        (root / ".venv").mkdir()
        (root / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "9.0.0" if phase == "ready" else "8.0.0"},
        }), encoding="utf-8")
    if phase in ("stamp", "drift"):
        for name in ("action_controls.py", "versioned_runtime_native.ps1"):
            (scripts / name).write_bytes((SCRIPTS / name).read_bytes())
        package = payload / "src" / "agent_dispatch"
        package.mkdir(parents=True)
        for name in ("__init__.py", "source_build.py", "action_controls.py", "action_policy.py", "install_paths.py"):
            (package / name).write_bytes((SCRIPTS.parent / "src" / "agent_dispatch" / name).read_bytes())
    result = run_ps(tmp_path, f"& {quote(hook)}\nexit $LASTEXITCODE\n")
    expected = 0 if phase == "ready" else 126 if phase == "prerequisite" else 3
    assert result.returncode == expected, result.stdout + result.stderr
    assert result.stdout == "{}"
    if phase != "ready":
        assert "automatic-runtime-reconcile" in result.stderr
    assert not marker.exists()
    assert not (root / "reconcile.lock").exists()
    assert not (root / "reconcile-status.json").exists()


def test_background_bootstrap_attempt_records_denial_without_installer(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "action-controls.json").write_text(json.dumps({
        "automatic-runtime-reconcile": {"disabled": True},
    }), encoding="utf-8")
    status = root / "reconcile-status.json"
    status.write_text(json.dumps({
        "attempt_id": "contained-attempt", "launched_pid": 0, "worker_started_at": None,
    }), encoding="utf-8")
    marker = tmp_path / "installer-called"
    source = BOOTSTRAP.read_text(encoding="utf-8")
    worker = re.search(r"(?s)\$worker = @'\n(.*?)\n'@", source)
    assert worker
    reader = function(BOOTSTRAP, "Get-BootstrapActionAdmission").split("{", 1)[1].rsplit("}", 1)[0]
    body = worker.group(1).replace("__ACTION_READER__", reader)
    replacements = {
        "__INSTALL_ROOT__": str(root),
        "__SCRIPTS_ROOT__": str(SCRIPTS),
        "__MUTEX__": f"dispatch-action-test-{tmp_path.name}",
        "__ATTEMPT__": "contained-attempt",
        "__STATUSFILE__": str(status),
        "__LOCKFILE__": str(root / "reconcile.lock"),
        "__RECONCILELOG__": str(root / "reconcile.log"),
        "__INSTALLER__": f"[IO.File]::WriteAllText({quote(marker)}, 'called')",
    }
    for key, value in replacements.items():
        body = body.replace(key, value if key == "__INSTALLER__" else value.replace("'", "''"))
    result = run_ps(tmp_path, body)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "automatic-runtime-reconcile" in result.stderr
    assert not marker.exists()
    recorded = json.loads(status.read_text(encoding="utf-8"))
    assert recorded["attempt_id"] == "contained-attempt"
    assert recorded["exit_code"] == 3
    assert recorded["success"] is False


def test_native_scripts_parse(tmp_path: Path) -> None:
    body = ""
    for path in (INSTALLER, GATE, BOOTSTRAP):
        body += f"""
$tokens = $null
$errors = $null
$null = [Management.Automation.Language.Parser]::ParseFile(
    {quote(path)}, [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw ($errors -join "`n") }}
"""
    result = run_ps(tmp_path, body, policy_required=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_start_remains_available_and_publication_boundaries_recheck() -> None:
    assert "Assert-DispatchActions" not in function(INSTALLER, "Invoke-Start")
    for name in (
        "Invoke-VersionedActivate", "Install-Runtime", "Invoke-Stamp",
        "Install-CoordinatorTask", "Invoke-CoordinatorCutover",
    ):
        assert "Assert-DispatchActions" in function(INSTALLER, name), name
