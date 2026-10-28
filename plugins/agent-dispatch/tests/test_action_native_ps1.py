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


@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("suffix", ["", "/", "//", "/./", "/../ordinary"])
def test_policy_absence_walk_preserves_lexical_link_components(
    tmp_path: Path, linked: bool, suffix: str,
) -> None:
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    root = ordinary
    if linked:
        root = tmp_path / "alias"
        try:
            root.symlink_to(ordinary, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"native symbolic-link support is unavailable: {exc}")
    body = (
        installer_policy(tmp_path)
        + f"\n$InstallDir = {quote(str(root) + suffix)}\n"
        + "function Get-BootstrapPython { return $null }\n"
        + "Assert-DispatchActions @('runtime-activation')\n"
    )
    result = run_ps(tmp_path, body)
    assert (result.returncode != 0) is linked, result.stdout + result.stderr
    if linked:
        assert "linked" in result.stderr
    assert not (ordinary / "action-controls.json").exists()


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


@pytest.mark.parametrize("failure", [
    "inhibited", "invalid-policy", "missing-helper", "missing-python",
    "invalid-response", "process-start",
])
def test_callable_policy_failures_keep_a_structured_terminating_refusal(
    tmp_path: Path, failure: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    policy = root / "action-controls.json"
    policy.write_text(
        "{" if failure == "invalid-policy" else json.dumps({
            "runtime-activation": {"disabled": failure == "inhibited"},
        }), encoding="utf-8",
    )
    body = installer_policy(tmp_path)
    if failure == "missing-helper":
        body += f"\n$script:ActionControlsHelper = {quote(tmp_path / 'missing.py')}\n"
    elif failure == "missing-python":
        body += "\nfunction Get-BootstrapPython { return $null }\n"
    elif failure == "invalid-response":
        helper = tmp_path / "invalid-response.py"
        helper.write_text("print('not a policy decision')\n", encoding="utf-8")
        body += f"\n$script:ActionControlsHelper = {quote(helper)}\n"
    elif failure == "process-start":
        executable = tmp_path / "not-python.exe"
        executable.write_text("not an executable", encoding="utf-8")
        body += f"\nfunction Get-BootstrapPython {{ return {quote(executable)} }}\n"
    body += """
try {
    Assert-DispatchActions @('runtime-activation')
    throw 'policy failure was admitted'
} catch {
    $marker = $_.Exception.Data['AgentDispatch.ActionPolicyRefusal']
    if ($marker -isnot [bool] -or -not $marker) { throw }
    Write-Output 'caught structured policy refusal'
}
"""
    result = run_ps(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "caught structured policy refusal" in result.stdout


@pytest.mark.parametrize(("action", "cleanup_failure"), [
    ("provision", False), ("provision", True), ("stamp", False), ("restore", False),
])
def test_real_installer_maps_only_policy_refusal_after_cleanup(
    tmp_path: Path, action: str, cleanup_failure: bool,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "action-controls.json").write_text(json.dumps({
        "runtime-activation": {"disabled": True},
    }), encoding="utf-8")
    cleaned = tmp_path / "cleaned"
    body = f"""
$env:COPILOT_EXTENSIONS_HUMAN_UTF8 = $null
function Stop-RuntimeConstruction {{
    [IO.File]::WriteAllText({quote(cleaned)}, 'cleaned')
    {"throw 'contained cleanup failure'" if cleanup_failure else ""}
}}
& {quote(INSTALLER)} -Action {quote(action)} -InstallDir {quote(root)} {
    f"-RetainedRuntime {quote(tmp_path / 'not-inspected.json')}" if action == "restore" else ""
}
exit $LASTEXITCODE
"""
    result = run_ps(tmp_path, body)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "runtime-activation" in result.stderr
    assert cleaned.read_text(encoding="utf-8") == "cleaned"
    if cleanup_failure:
        assert "contained cleanup failure" in result.stderr
    for name in ("current-version", "stamped-version", "deploy-manifest.json"):
        assert not (root / name).exists()


def test_outer_installer_does_not_classify_unmarked_errors_by_message(tmp_path: Path) -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    boundary = source[source.index("$script:InstallerLifecycleAction = if"):]
    cleaned = tmp_path / "cleaned"
    body = f"""
$Action = 'status'
$installerActionRefusal = $null
function Assert-InstallActions {{}}
function Invoke-Status {{ throw '[agent-dispatch] Action policy did not admit an unrelated operation' }}
function Stop-RuntimeConstruction {{ [IO.File]::WriteAllText({quote(cleaned)}, 'cleaned') }}
try {{
""" + boundary
    result = run_ps(tmp_path, body)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "unrelated operation" in result.stderr
    assert cleaned.read_text(encoding="utf-8") == "cleaned"


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
    if ($_.Exception.Data['AgentDispatch.ActionPolicyRefusal'] -ne $true) {{ throw }}
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
    cleaned = tmp_path / "installer-cleaned"
    upstream.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$env:COPILOT_EXTENSIONS_HUMAN_UTF8 = $null\n"
        f"$policy = {quote(root / 'action-controls.json')}\n"
        "[IO.File]::WriteAllText($policy, "
        """'{"runtime-activation":{"disabled":true}}')\n"""
        "function Stop-RuntimeConstruction {\n"
        f" [IO.File]::WriteAllText({quote(cleaned)}, 'cleaned')\n"
        "}\n"
        "try {\n"
        f" & {quote(INSTALLER)} provision -InstallDir {quote(root)}\n"
        " $code = $LASTEXITCODE\n"
        "} catch {\n"
        " [Console]::Error.WriteLine($_.ToString())\n"
        " $code = 1\n"
        "} finally { [IO.File]::WriteAllText($policy, '{}') }\n"
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
    assert "runtime-activation" in result.stderr
    assert not marker.exists()
    assert cleaned.read_text(encoding="utf-8") == "cleaned"
    for name in ("current-version", "stamped-version", "deploy-manifest.json"):
        assert not (root / name).exists()
    assert (root / ".provision-status").read_text(encoding="utf-8") == "failed rc=3"
    assert json.loads((root / "action-controls.json").read_text(encoding="utf-8")) == {}


@pytest.mark.parametrize("phase", ["stamp", "drift", "ready", "prerequisite", "installer-refusal"])
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
            "runtime-activation" if phase == "installer-refusal" else
            "automatic-runtime-reconcile": {"disabled": True},
        }), encoding="utf-8")
    if phase == "installer-refusal":
        (scripts / "install.ps1").write_text(
            "# 'stamp'\n$env:COPILOT_EXTENSIONS_HUMAN_UTF8 = $null\n"
            f"& {quote(INSTALLER)} stamp -InstallDir {quote(root)}\nexit $LASTEXITCODE\n",
            encoding="utf-8",
        )
    if phase in ("drift", "ready"):
        (root / ".venv").mkdir()
        (root / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "9.0.0" if phase == "ready" else "8.0.0"},
        }), encoding="utf-8")
    if phase in ("stamp", "drift", "installer-refusal"):
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
    if phase == "installer-refusal":
        assert "runtime-activation" in result.stderr
        assert "stamp did not complete (exit 3)" in result.stderr
    assert not marker.exists()
    assert not (root / "reconcile.lock").exists()
    assert not (root / "reconcile-status.json").exists()


def test_windows_lifecycle_fixture_stages_real_action_policy_support(tmp_path: Path) -> None:
    from .test_windows_bootstrap_reconcile_lifecycle import stage_action_policy_payload

    payload = tmp_path / "fixture with ' quote"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    stage_action_policy_payload(payload)
    root = tmp_path / "runtime"
    root.mkdir()
    policy = root / "action-controls.json"
    policy.write_text(json.dumps({
        "automatic-runtime-reconcile": {"disabled": True},
    }), encoding="utf-8")
    body = function(BOOTSTRAP, "Get-BootstrapActionAdmission") + f"""
$denied = Get-BootstrapActionAdmission -Root {quote(root)} -ScriptsRoot {quote(scripts)}
if ($denied -ne 3) {{ throw "expected real policy denial, got $denied" }}
[IO.File]::WriteAllText({quote(policy)}, '{{}}')
$allowed = Get-BootstrapActionAdmission -Root {quote(root)} -ScriptsRoot {quote(scripts)}
if ($allowed -ne 0) {{ throw "cleared fixture policy remained unavailable: $allowed" }}
"""
    result = run_ps(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "automatic-runtime-reconcile" in result.stderr


@pytest.mark.parametrize("frontend", ["bootstrap", "runtime-gate"])
@pytest.mark.parametrize("linked", [False, True])
def test_automatic_admission_preserves_lexical_selected_root(
    tmp_path: Path, frontend: str, linked: bool,
) -> None:
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    alias = tmp_path / "alias"
    if linked:
        try:
            alias.symlink_to(ordinary, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"native symbolic-link support is unavailable: {exc}")
    else:
        alias.mkdir()
    lexical = str(alias) + "/../ordinary"
    if frontend == "bootstrap":
        body = function(BOOTSTRAP, "Get-BootstrapActionAdmission") + f"""
$code = Get-BootstrapActionAdmission -Root {quote(lexical)} -ScriptsRoot {quote(SCRIPTS)}
exit $code
"""
    else:
        body = (
            f"$payloadRoot = {quote(SCRIPTS.parent)}\n"
            f"$runtimeRoot = {quote(ordinary)}\n$context = ''\n"
            f"$legacyPolicyRoot = {quote(lexical)}\n"
            + function(GATE, "Assert-AutomaticRuntimeReconcile")
            + "\nAssert-AutomaticRuntimeReconcile\n"
        )
    result = run_ps(tmp_path, body)
    assert result.returncode == (3 if linked else 0), result.stdout + result.stderr
    if linked:
        assert "linked" in result.stderr
        assert "automatic-runtime-reconcile" in result.stderr
    if frontend == "runtime-gate":
        source = GATE.read_text(encoding="utf-8")
        assert source.index("$legacyPolicyRoot = $legacyRoot") < source.index("$resolution = Read-Decision")
        assert source.index("$legacyPolicyRoot = Join-Path") < source.index("$legacyRoot = (Resolve-Path")
    assert not (ordinary / ".provision.lock").exists()
    assert not (ordinary / ".provision-status").exists()


@pytest.mark.parametrize("frontend", ["bootstrap", "runtime-gate"])
@pytest.mark.parametrize("root", [
    "", "relative", "C:relative", "C:", r"\relative",
    pytest.param(
        "/relative",
        marks=pytest.mark.skipif(os.name != "nt", reason="native Windows root-relative syntax"),
    ),
])
def test_automatic_admission_rejects_partially_qualified_roots(
    tmp_path: Path, frontend: str, root: str,
) -> None:
    if frontend == "bootstrap":
        body = function(BOOTSTRAP, "Get-BootstrapActionAdmission") + f"""
$code = Get-BootstrapActionAdmission -Root {quote(root)} -ScriptsRoot {quote(SCRIPTS)}
exit $code
"""
    else:
        body = (
            f"$payloadRoot = {quote(SCRIPTS.parent)}\n$runtimeRoot = {quote(root)}\n$context = ''\n"
            + function(GATE, "Assert-AutomaticRuntimeReconcile")
            + "\nAssert-AutomaticRuntimeReconcile\n"
        )
    result = run_ps(tmp_path, body)
    assert result.returncode == 126, result.stdout + result.stderr
    assert "selected installation root must be absolute" in result.stderr
    assert "automatic-runtime-reconcile" in result.stderr


@pytest.mark.parametrize("linked", [False, True])
def test_actual_bootstrap_hook_checks_lexical_home_before_stamp(tmp_path: Path, linked: bool) -> None:
    from .test_windows_bootstrap_reconcile_lifecycle import stage_action_policy_payload

    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    alias = tmp_path / "alias"
    if linked:
        try:
            alias.symlink_to(ordinary, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"native symbolic-link support is unavailable: {exc}")
    else:
        alias.mkdir()
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    stage_action_policy_payload(payload)
    hook = scripts / BOOTSTRAP.name
    hook.write_bytes(BOOTSTRAP.read_bytes())
    (payload / "plugin.json").write_text('{"name":"agent-dispatch"}', encoding="utf-8")
    marker = tmp_path / "stamp-called"
    (scripts / "install.ps1").write_text(
        f"# 'stamp'\n[IO.File]::WriteAllText({quote(marker)}, 'stamp')\nexit 0\n", encoding="utf-8",
    )
    body = f"$env:USERPROFILE = {quote(str(alias) + '/..' + '/ordinary')}\n& {quote(hook)}\nexit $LASTEXITCODE\n"
    result = run_ps(tmp_path, body)
    assert result.returncode == (3 if linked else 0), result.stdout + result.stderr
    assert result.stdout == "{}"
    assert marker.exists() is not linked
    if linked:
        assert "linked" in result.stderr
    assert not (ordinary / ".agent-dispatch" / "reconcile-status.json").exists()


def test_namespaced_admission_does_not_consult_an_unselected_legacy_root(tmp_path: Path) -> None:
    root = tmp_path / "selected"
    root.mkdir()
    body = (
        f"$payloadRoot = {quote(SCRIPTS.parent)}\n$runtimeRoot = {quote(root)}\n"
        "$context = 'selected-context'\n$legacyPolicyRoot = 'not-an-absolute-selected-root'\n"
        + function(GATE, "Assert-AutomaticRuntimeReconcile")
        + "\nAssert-AutomaticRuntimeReconcile\n"
    )
    result = run_ps(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr


def global_gate_fixture(tmp_path: Path, *, warm: bool = False):
    if PWSH is None:
        pytest.skip("actual PowerShell is required")
    from . import source_publication_fixture as fixture

    env, root, sources = fixture.stage(tmp_path)
    payload, identity = sources[0]
    scripts = payload / "scripts"
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name in ("runtime-gate.ps1", "resolve-runtime.ps1"):
        shutil.copyfile(SCRIPTS / name, bin_dir / name)
    shutil.copytree(scripts / "installation-context", bin_dir / "installation-context")
    decision = {
        "status": "ready", "actualMode": "legacy", "desiredMode": "legacy",
        "installGeneration": "owned-fixture", "policy": {"enabled": False},
        "legacy": {"root": str(root), "disposition": "active", "tombstone": None},
        "environment": {"homeRealPath": env["HOME"]}, "allowMutation": True,
        "deployment": {
            "allowPluginPublication": True, "path": str(root / "runtime-deployment.json"),
        },
    }
    # The caller consumes this owned canonical decision; guest registration is not under test.
    (bin_dir / "installation-context" / "installation-context.ps1").write_text(
        f"Write-Output {quote(json.dumps(decision))}\nexit 0\n", encoding="utf-8",
    )
    (root / "payload-dir").write_text(str(payload), encoding="utf-8")
    env["AGENT_DISPATCH_PAYLOAD_ROOT"] = str(payload)
    env["AGENT_DISPATCH_PROVISION_OFFLINE"] = "1"
    env.pop("BOOTSTRAP_KILLSWITCH_STATE_FILE", None)
    installer_called = tmp_path / "installer-called"
    if warm:
        fixture.activate(env, root, identity.runtime_version)
    else:
        (root / "versions").rename(tmp_path / "qualified-fixture-versions")
        (scripts / "install.ps1").write_text(
            f"[IO.File]::WriteAllText({quote(installer_called)}, 'called')\nexit 17\n",
            encoding="utf-8",
        )
    shared = Path(env["HOME"]) / ".copilot-extensions" / "bootstrap-killswitch.json"
    shared.parent.mkdir(exist_ok=True)
    shared.write_text('{"active":true}', encoding="utf-8")
    return env, root, bin_dir / "runtime-gate.ps1", shared, installer_called


def run_global_gate(tmp_path: Path, entry: Path, env: dict[str, str], prefix: str = ""):
    if PWSH is None:
        pytest.skip("actual PowerShell is required")
    harness = tmp_path / "global-entry.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + prefix
        + "\nfunction Invoke-WebRequest { throw 'forbidden network boundary' }\n"
        + f"& {quote(entry)} --version\nexit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        env=env, capture_output=True, text=True, timeout=25, creationflags=HEADLESS,
    )


@pytest.mark.parametrize(("selection", "active", "warm"), [
    ("plain", True, False), ("home-alias", True, False),
    ("invocation-alias", True, False), ("plain", False, False),
    ("home-alias", True, True),
])
def test_full_global_entry_keeps_default_bootstrap_veto_and_warm_availability(
    tmp_path: Path, selection: str, active: bool, warm: bool,
) -> None:
    env, root, entry, shared, installer = global_gate_fixture(tmp_path, warm=warm)
    shared.write_text(json.dumps({"active": active}), encoding="utf-8")
    if active:
        env.pop("AGENT_DISPATCH_PROVISION_OFFLINE", None)
    if selection != "plain":
        alias = tmp_path / "home-alias"
        try:
            alias.symlink_to(Path(env["HOME"]), target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"native symbolic-link support is unavailable: {exc}")
        if selection == "home-alias":
            env["HOME"] = env["USERPROFILE"] = str(alias)
        else:
            entry = alias / ".agent-dispatch" / "bin" / "runtime-gate.ps1"
    ignored = tmp_path / "ignored-custom-root"
    ignored.mkdir()
    (ignored / "action-controls.json").write_text(
        '{"automatic-runtime-reconcile":{"disabled":true}}', encoding="utf-8",
    )
    env["AGENT_DISPATCH_INSTALL_DIR"] = str(ignored)
    status = root / ".provision-status"
    if warm:
        status.write_text("historical provisioning", encoding="utf-8")
    result = run_global_gate(tmp_path, entry, env)
    assert result.returncode == (0 if warm else 3 if active else 17), result.stdout + result.stderr
    assert "forbidden network boundary" not in result.stderr
    if warm:
        assert "agent-dispatch " in result.stdout
        assert status.read_text(encoding="utf-8") == "historical provisioning"
    elif active:
        assert not status.exists()
        assert "automatic-runtime-reconcile" in result.stderr
    else:
        assert status.read_text(encoding="utf-8") == "failed rc=17"
    assert installer.exists() is (not warm and not active)
    if warm or active:
        assert not (root / ".provision.lock").exists()


@pytest.mark.parametrize("unwritable_status", [False, True])
def test_full_global_late_refusal_settles_only_its_owned_provision_status(
    tmp_path: Path, unwritable_status: bool,
) -> None:
    env, root, entry, shared, installer = global_gate_fixture(tmp_path)
    shared.write_text('{"active":false}', encoding="utf-8")
    env.pop("AGENT_DISPATCH_PROVISION_OFFLINE", None)
    status = root / ".provision-status"
    policy = root / "action-controls.json"
    harness = tmp_path / "late-global-entry.ps1"
    harness.write_text(f"""
$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {{
    param($Uri, [switch]$UseBasicParsing, $TimeoutSec, $OutFile)
    [IO.File]::WriteAllText({quote(policy)},
        '{{"automatic-runtime-reconcile":{{"disabled":true,"reason":"late cold refusal"}}}}')
    [IO.File]::WriteAllText($OutFile, 'throw "forbidden installer boundary"')
    {
        f"[IO.File]::Move({quote(status)}, {quote(root / 'owned-status-before-refusal')}); "
        f"[void][IO.Directory]::CreateDirectory({quote(status)})"
        if unwritable_status else ""
    }
}}
& {quote(entry)} --version
exit $LASTEXITCODE
""", encoding="utf-8")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        env=env, capture_output=True, text=True, timeout=25, creationflags=HEADLESS,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "late cold refusal" in result.stderr
    assert "forbidden installer boundary" not in result.stderr
    assert not installer.exists()
    if unwritable_status:
        assert "provision status" in result.stderr.lower()
        assert (root / "owned-status-before-refusal").read_text(encoding="utf-8") == "provisioning"
    else:
        assert status.read_text(encoding="utf-8") == "failed rc=3"
    with (root / ".provision.lock").open("r+b") as lock:
        assert lock.read() == b""


def test_full_global_exception_settles_its_started_attempt_without_installer(tmp_path: Path) -> None:
    env, root, entry, shared, installer = global_gate_fixture(tmp_path)
    shared.write_text('{"active":false}', encoding="utf-8")
    result = run_global_gate(
        tmp_path, entry, env,
        "function Get-Process { param($Id) throw 'contained termination with owned lock' }\n",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "contained termination with owned lock" in result.stderr
    assert (root / ".provision-status").read_text(encoding="utf-8") == "failed rc=1"
    assert not installer.exists()


def test_global_custom_effect_root_does_not_consult_a_default_home_alias(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    alias = tmp_path / "home-alias"
    try:
        alias.symlink_to(home, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"native symbolic-link support is unavailable: {exc}")
    root = tmp_path / "custom-installation"
    (root / "bin").mkdir(parents=True)
    body = (
        f"$env:HOME = {quote(alias)}\n$env:USERPROFILE = {quote(alias)}\n"
        f"$payloadRoot = {quote(SCRIPTS.parent)}\n$runtimeRoot = {quote(root)}\n"
        f"$legacyPolicyRoot = {quote(str(root) + '/bin/..')}\n"
        f"$resolution = [pscustomobject]@{{environment=[pscustomobject]@{{homeRealPath={quote(home)}}}}}\n"
        "$context = ''; $globalSurface = $true\n"
        + function(GATE, "Assert-AutomaticRuntimeReconcile")
        + "\nAssert-AutomaticRuntimeReconcile\n"
    )
    result = run_ps(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr


def bootstrap_worker(tmp_path: Path, root: Path, installer: str) -> tuple[str, Path]:
    status = root / "reconcile-status.json"
    status.write_text(json.dumps({
        "attempt_id": "contained-attempt", "launched_pid": 0, "worker_started_at": None,
    }), encoding="utf-8")
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
        "__INSTALLER__": installer,
    }
    for key, value in replacements.items():
        body = body.replace(key, value if key == "__INSTALLER__" else value.replace("'", "''"))
    return body, status


@pytest.mark.parametrize("existing_log", [False, True])
def test_background_bootstrap_attempt_records_denial_without_installer(
    tmp_path: Path, existing_log: bool,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    policy = root / "action-controls.json"
    policy.write_text("{}", encoding="utf-8")
    log = root / "reconcile.log"
    if existing_log:
        log.write_text("attempt=previous completed successfully\n", encoding="utf-8")
    marker = tmp_path / "installer-called"
    body, status = bootstrap_worker(
        tmp_path, root, f"[IO.File]::WriteAllText({quote(marker)}, 'called')",
    )
    body = function(BOOTSTRAP, "Get-BootstrapActionAdmission") + f"""
$before = Get-BootstrapActionAdmission -Root {quote(root)} -ScriptsRoot {quote(SCRIPTS)}
if ($before -ne 0) {{ throw 'hook preflight was not admitted' }}
[IO.File]::WriteAllText({quote(policy)},
    '{{"automatic-runtime-reconcile":{{"disabled":true,"reason":"late pause"}}}}')
""" + body
    result = run_ps(tmp_path, body)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "automatic-runtime-reconcile" in result.stderr
    assert not marker.exists()
    recorded = json.loads(status.read_text(encoding="utf-8"))
    assert recorded["attempt_id"] == "contained-attempt"
    assert recorded["exit_code"] == 3
    assert recorded["success"] is False
    assert "automatic-runtime-reconcile" in recorded["diagnostic"]
    assert "late pause" in recorded["diagnostic"]
    assert "attempt=contained-attempt" in log.read_text(encoding="utf-8-sig")
    assert "late pause" in log.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("condition", ["unwritable-log", "replaced-attempt"])
def test_late_worker_refusal_keeps_diagnostics_and_attempt_fences(
    tmp_path: Path, condition: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "action-controls.json").write_text(
        '{"automatic-runtime-reconcile":{"disabled":true,"reason":"late refusal"}}', encoding="utf-8",
    )
    marker = tmp_path / "installer-called"
    body, status = bootstrap_worker(
        tmp_path, root, f"[IO.File]::WriteAllText({quote(marker)}, 'called')",
    )
    log = root / "reconcile.log"
    replacement = '{"attempt_id":"replacement","launched_pid":0}'
    if condition == "unwritable-log":
        log.mkdir()
    else:
        log.write_text("replacement attempt log\n", encoding="utf-8")
        completion = "try { [void](Update-ReconcileAttempt $true $code $failureDiagnostic) }"
        assert completion in body
        body = body.replace(
            completion,
            f"[IO.File]::WriteAllText({quote(status)}, {quote(replacement)})\n" + completion,
        )
    result = run_ps(tmp_path, body)
    assert result.returncode == 3, result.stdout + result.stderr
    assert not marker.exists()
    if condition == "unwritable-log":
        recorded = json.loads(status.read_text(encoding="utf-8"))
        assert recorded["attempt_id"] == "contained-attempt"
        assert recorded["exit_code"] == 3 and recorded["success"] is False
        assert "late refusal" in recorded["diagnostic"]
        assert "reconcile log unavailable" in recorded["diagnostic"]
    else:
        assert status.read_text(encoding="utf-8") == replacement
        assert log.read_text(encoding="utf-8") == "replacement attempt log\n"


@pytest.mark.parametrize("boundary", ["native-installer", "callable-refusal", "unmarked-failure"])
def test_bootstrap_worker_preserves_policy_classification_and_exit_code(
    tmp_path: Path, boundary: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "action-controls.json").write_text(json.dumps({
        "runtime-activation": {"disabled": True},
    }), encoding="utf-8")
    invocation = {
        "native-installer": f"& {quote(INSTALLER)} provision -InstallDir {quote(root)}",
        "callable-refusal": "Assert-DispatchActions @('runtime-activation')",
        "unmarked-failure": "throw '[agent-dispatch] Action policy did not admit an unrelated operation'",
    }[boundary]
    body, status = bootstrap_worker(tmp_path, root, invocation)
    body = (
        installer_policy(tmp_path)
        + "\n$env:COPILOT_EXTENSIONS_HUMAN_UTF8 = $null\n"
        + body
    )
    result = run_ps(tmp_path, body)
    expected = 1 if boundary == "unmarked-failure" else 3
    assert result.returncode == expected, result.stdout + result.stderr
    recorded = json.loads(status.read_text(encoding="utf-8"))
    assert recorded["exit_code"] == expected
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
