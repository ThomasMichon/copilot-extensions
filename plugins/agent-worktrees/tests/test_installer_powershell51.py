from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"
SERVICE_UTILS = PLUGIN / "scripts" / "service-utils.ps1"

pytestmark = pytest.mark.guard


def test_windows_provision_bootstraps_uv_before_runtime_build():
    installer = INSTALLER.read_text(encoding="utf-8")
    provision = installer.split("'provision' {", 1)[1].split("'install' {", 1)[0]
    manifest = installer.split("function Write-V3Manifest", 1)[1].split(
        "function Ensure-UvIndex", 1
    )[0]

    assert "if (-not (Ensure-Uv)) { exit 1 }" in provision
    assert provision.index("Ensure-Uv") < provision.index("Deploy-Venv")
    assert "Invoke-NativeCapture" in installer
    assert "[System.IO.File]::WriteAllText" in manifest
    assert "Set-Content -Path $tmp -Encoding UTF8" not in manifest
    assert "if ($env:APPDATA)" in installer
    assert "if ($env:PROGRAMDATA)" in installer
    assert "$pythonPath = Get-BootstrapPython" in installer
    assert "& $pythonPath -m pip config get global.index-url" in installer
    assert "$env:AGENT_WORKTREES_UV_BOOTSTRAP_URL" in installer
    assert "$urlTemplate.Replace('{asset}', $asset)" in installer


def test_uv_index_bridge_preserves_file_configured_index(tmp_path: Path):
    installer = INSTALLER.read_text(encoding="utf-8")
    configured = installer.split("function Test-UvConfiguredIndex", 1)[1].split(
        "function Ensure-UvIndex", 1
    )[0]
    configured_body = configured.split("{", 1)[1].rsplit("}", 1)[0]
    bridge = installer.split("function Ensure-UvIndex", 1)[1].split(
        "function Ensure-Uv", 1
    )[0]

    assert "$env:UV_CONFIG_FILE" in configured
    assert "uv\\uv.toml" in configured
    assert "$env:PROGRAMDATA" in configured
    assert "(Test-UvConfiguredIndex)" in bridge
    assert bridge.index("(Test-UvConfiguredIndex)") < bridge.index(
        "pip config get global.index-url"
    )

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    appdata = tmp_path / "appdata"
    config = appdata / "uv" / "uv.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[[index]] # configured\nurl = "https://example.invalid/simple/"\n'
        "default = true # preferred\n",
        encoding="utf-8",
    )
    script = f"""
function Test-UvConfiguredIndex {{
{configured_body}
}}
[Console]::Out.Write((Test-UvConfiguredIndex))
"""
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "APPDATA": str(appdata),
            "PROGRAMDATA": str(tmp_path / "programdata"),
            "UV_CONFIG_FILE": "",
        },
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "True"


def test_posix_uv_index_bridge_preserves_file_configured_index(tmp_path: Path):
    installer = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    bridge = installer.split("_ensure_uv_index() {", 1)[1].split(
        "# Deploy ONLY", 1
    )[0].rsplit("}", 1)[0]

    assert '"${UV_CONFIG_FILE:-}"' in bridge
    assert '"${XDG_CONFIG_HOME:-$HOME/.config}/uv/uv.toml"' in bridge
    assert "/etc/uv/uv.toml" in bridge
    assert bridge.index("for uv_config in") < bridge.index("pip config get")

    bash = shutil.which("bash")
    if os.name == "nt" or not bash:
        pytest.skip("native POSIX bash is unavailable")
    config = tmp_path / "uv.toml"
    config.write_text(
        '[[index]] # configured\nurl = "https://example.invalid/simple/"\n'
        "default = true # preferred\n",
        encoding="utf-8",
    )
    script = f"""
changed() {{ :; }}
_ensure_uv_index() {{
{bridge}
}}
unset UV_INDEX_URL UV_DEFAULT_INDEX
UV_CONFIG_FILE="$1"
export UV_CONFIG_FILE
_ensure_uv_index
[[ -z "${{UV_DEFAULT_INDEX:-}}" ]]
"""
    proc = subprocess.run(
        [bash, "-c", script, "test", str(config)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def test_uv_bootstrap_python_survives_windows_powershell_argument_passing():
    installer = INSTALLER.read_text(encoding="utf-8")
    bootstrap = installer.split("$bootstrap = @'", 1)[1].split("'@", 1)[0]

    assert '"' not in bootstrap
    assert "missing_ok" not in bootstrap
    assert "$ErrorActionPreference = 'Continue'" in installer


def test_application_path_selects_one_usable_match(tmp_path: Path):
    pwsh = (
        (shutil.which("powershell.exe") if os.name == "nt" else None)
        or shutil.which("pwsh")
        or shutil.which("powershell")
    )
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    first = tmp_path / "first-python"
    second = tmp_path / "second-python"
    store_alias = tmp_path / "WindowsApps" / "python.exe"
    for path in (first, second, store_alias):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    script = r"""
$tokens = $null
$errors = $null
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
foreach ($name in @('Resolve-WinGetPackageExecutable', 'Get-ApplicationPath')) {
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true)
    if (-not $functionAst) { throw "Missing installer function: $name" }
    Invoke-Expression $functionAst.Extent.Text
}
function Get-Command {
    @(
        [pscustomobject]@{ Source = $env:STORE_ALIAS },
        [pscustomobject]@{ Source = $env:FIRST_PYTHON },
        [pscustomobject]@{ Source = $env:SECOND_PYTHON }
    )
}
Get-ApplicationPath -Name @('python')
"""
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "INSTALLER": str(INSTALLER),
            "STORE_ALIAS": str(store_alias),
            "FIRST_PYTHON": str(first),
            "SECOND_PYTHON": str(second),
        },
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(first)


@pytest.mark.skipif(os.name != "nt", reason="WinGet paths are Windows-only")
def test_application_path_resolves_winget_link_to_package_binary(
    tmp_path: Path,
):
    pwsh = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    local_appdata = tmp_path / "localappdata"
    link = (
        local_appdata / "Microsoft" / "WinGet" / "Links" / "uv[preview].exe"
    )
    package = (
        local_appdata
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "example.uv_source"
        / "uv[preview].exe"
    )
    link.parent.mkdir(parents=True)
    package.parent.mkdir(parents=True)
    link.touch()
    package.write_bytes(b"ordinary executable")

    script = r"""
$tokens = $null
$errors = $null
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
foreach ($name in @('Resolve-WinGetPackageExecutable', 'Get-ApplicationPath')) {
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true)
    if (-not $functionAst) { throw "Missing installer function: $name" }
    Invoke-Expression $functionAst.Extent.Text
}
function Get-Command {
    [pscustomobject]@{ Source = $env:WINGET_LINK }
}
function Get-Item {
    [pscustomobject]@{
        Attributes = [IO.FileAttributes]::ReparsePoint
    }
}
Get-ApplicationPath -Name @('uv')
"""
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "INSTALLER": str(INSTALLER),
            "LOCALAPPDATA": str(local_appdata),
            "WINGET_LINK": str(link),
        },
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(package)


@pytest.mark.skipif(os.name != "nt", reason="WinGet paths are Windows-only")
def test_application_path_preserves_ambiguous_winget_link(tmp_path: Path):
    pwsh = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    local_appdata = tmp_path / "localappdata"
    link = local_appdata / "Microsoft" / "WinGet" / "Links" / "tool.exe"
    link.parent.mkdir(parents=True)
    link.write_bytes(b"link shim")
    for package_name in ("example.one", "example.two"):
        package = (
            local_appdata
            / "Microsoft"
            / "WinGet"
            / "Packages"
            / package_name
            / "tool.exe"
        )
        package.parent.mkdir(parents=True)
        package.write_bytes(package_name.encode("ascii"))

    script = r"""
$tokens = $null
$errors = $null
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
foreach ($name in @('Resolve-WinGetPackageExecutable', 'Get-ApplicationPath')) {
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true)
    if (-not $functionAst) { throw "Missing installer function: $name" }
    Invoke-Expression $functionAst.Extent.Text
}
function Get-Command {
    [pscustomobject]@{ Source = $env:WINGET_LINK }
}
function Get-Item {
    [pscustomobject]@{
        Attributes = [IO.FileAttributes]::ReparsePoint
    }
}
Get-ApplicationPath -Name @('tool')
"""
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "INSTALLER": str(INSTALLER),
            "LOCALAPPDATA": str(local_appdata),
            "WINGET_LINK": str(link),
        },
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(link)


def test_venv_install_invokes_resolved_uv_path():
    installer = INSTALLER.read_text(encoding="utf-8")
    body = installer.split("function Invoke-VenvPackageInstall", 1)[1].split(
        "function Deploy-Venv", 1
    )[0]

    assert "$uvPath = Get-ApplicationPath -Name @('uv')" in body
    assert "& $uvPath pip install" in body
    assert "& uv pip install" not in body


def test_slot_clean_reports_failure_instead_of_silently_downgrading_signed_venv():
    """#5416 regression guard (supersedes the #2413 guard this test used to
    cover): a stale/still-in-use runtime slot means another process may
    genuinely own (or still be building into) $VenvDir right now, so
    `Deploy-Venv` must refuse to build ANYTHING into it -- signed or
    unsigned -- rather than racing that writer. `Invoke-VersionedSlotClean`
    must surface success/failure via its exit code, `Deploy-Venv` must retry
    before giving up, and must hard-fail (return $false, loud error) instead
    of falling through to any venv build when the slot is still dirty after
    retries."""
    installer = INSTALLER.read_text(encoding="utf-8")
    clean_fn = installer.split("function Invoke-VersionedSlotClean", 1)[1].split(
        "function Invoke-VersionedMarkComplete", 1
    )[0]
    deploy_fn = installer.split("function Deploy-Venv", 1)[1].split(
        "function Deploy-Wrappers", 1
    )[0]

    # The clean helper must propagate the underlying python call's exit code
    # instead of implicitly returning nothing (falsy $null) unconditionally.
    assert "return ($LASTEXITCODE -eq 0)" in clean_fn

    # Deploy-Venv must capture that result and retry before giving up.
    assert "$slotClean = Invoke-VersionedSlotClean" in deploy_fn
    assert "for ($i = 0; $i -lt 3 -and -not $slotClean; $i++)" in deploy_fn

    # A still-dirty slot after retries must hard-fail BEFORE any venv build
    # is attempted -- never gate the signed build on `-and $slotClean` and
    # then quietly fall through to an unsigned uv build anyway (the exact
    # failure mode of #2413/#5416).
    assert "if ($signedBase -and $slotClean) {" not in deploy_fn
    assert "if ($signedBase) {" in deploy_fn
    assert 'Write-ServiceErr "Runtime slot still in use after retries' in deploy_fn
    slot_dirty_branch = deploy_fn.split(
        'Write-ServiceErr "Runtime slot still in use after retries', 1
    )[1][:400]
    assert "return $false" in slot_dirty_branch


def test_deploy_venv_acquires_exclusive_build_lease_before_slot_clean():
    """#5439 review finding: `Invoke-VersionedSlotClean`'s liveness check alone
    is check-then-act -- two concurrent installer invocations could both
    observe a clean slot (neither has started its external build yet) and
    then both build into it. `Deploy-Venv` must acquire an OS-level exclusive
    build lease FIRST (before even attempting slot-clean), fail immediately
    if another live process already holds it, and `Invoke-VersionedActivate`
    must release that lease afterward regardless of outcome."""
    installer = INSTALLER.read_text(encoding="utf-8")
    deploy_fn = installer.split("function Deploy-Venv", 1)[1].split(
        "function Deploy-Wrappers", 1
    )[0]
    activate_wrapper = installer.split("function Invoke-VersionedActivate {", 1)[1]

    lease_idx = deploy_fn.index("Enter-VersionedSlotLease")
    clean_idx = deploy_fn.index("Invoke-VersionedSlotClean")
    assert lease_idx < clean_idx, (
        "the exclusive build lease must be acquired before the slot-clean "
        "check, not after"
    )
    assert "if (-not (Enter-VersionedSlotLease)) {" in deploy_fn
    lease_fail_branch = deploy_fn.split(
        "if (-not (Enter-VersionedSlotLease)) {", 1
    )[1][:600]
    assert "return $false" in lease_fail_branch

    # The wrapper must release the lease in a `finally`, so it runs whether
    # Invoke-VersionedActivateInner succeeds or fails.
    assert "try {" in activate_wrapper
    assert "Invoke-VersionedActivateInner" in activate_wrapper
    assert "finally {" in activate_wrapper
    assert "Exit-VersionedSlotLease" in activate_wrapper


def test_deploy_venv_calls_uv_retry_helper():
    """Deploy-Venv's uv fallback must go through the shared retry helper
    (behavior is covered standalone by the Invoke-UvVenvWithRetry tests
    below) rather than re-inlining its own ad hoc retry loop."""
    installer = INSTALLER.read_text(encoding="utf-8")
    deploy_fn = installer.split("function Deploy-Venv", 1)[1].split(
        "function Deploy-Wrappers", 1
    )[0]

    assert "$uvResult = Invoke-UvVenvWithRetry -VenvDir $VenvDir" in deploy_fn


def test_early_installer_utilities_are_powershell_51_safe_ascii():
    utilities = SERVICE_UTILS.read_text(encoding="utf-8")

    assert "#Requires -Version 7.0" not in utilities
    assert "??" not in utilities
    assert "Join-Path $PSScriptRoot '..\\..\\..'" in utilities
    utilities.encode("ascii")


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility")
def test_powershell_51_stamp_succeeds(tmp_path: Path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")

    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    proc = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER),
            "stamp",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    runtime = home / ".agent-worktrees"
    assert (runtime / "payload-dir").is_file()
    assert (runtime / "stamped-version").is_file()
    assert (home / ".local" / "bin" / "agent-worktrees.cmd").is_file()
