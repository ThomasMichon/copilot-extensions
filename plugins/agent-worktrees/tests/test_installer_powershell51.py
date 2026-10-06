from __future__ import annotations

import os
import shutil
import subprocess
import time
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
    """#5416 regression guard: a stale/still-in-use runtime slot means
    another process may genuinely own (or still be building into) $VenvDir
    right now, so `Deploy-Venv` must refuse to build ANYTHING into it --
    signed or unsigned -- rather than racing that writer.
    `Invoke-VersionedSlotClean` must surface success/failure via its exit
    code, `Deploy-Venv` must retry before giving up, and must hard-fail
    (return $false, loud error) instead of falling through to any venv
    build when the slot is still dirty after retries."""
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


def test_slot_clean_uses_an_interpreter_outside_the_target_slot():
    """`Invoke-VersionedSlotClean` inspects whether a live process is
    running FROM the target slot -- using that slot's OWN interpreter to
    run the census would make the helper process itself show up as such a
    process, permanently self-reporting an incomplete slot with a stale
    python.exe as still in use on every retry. Must resolve its interpreter
    via `Get-BootstrapPython -ExcludeVenvDir`, and must fail CLOSED (not
    silently report clean) for an EXISTING slot when no such interpreter
    can be found at all -- an ABSENT slot needs no validation and is
    trivially, correctly clean."""
    installer = INSTALLER.read_text(encoding="utf-8")
    clean_fn = installer.split("function Invoke-VersionedSlotClean", 1)[1].split(
        "function Invoke-VersionedMarkComplete", 1
    )[0]
    bootstrap_fn = installer.split("function Get-BootstrapPython {", 1)[1].split(
        "\n}\n", 1
    )[0]

    assert "$py = Get-BootstrapPython -ExcludeVenvDir" in clean_fn
    assert "if (-not $py) { return -not (Test-Path $VenvDir) }" in clean_fn

    assert "[switch]$ExcludeVenvDir" in bootstrap_fn
    assert "$dirs = if ($ExcludeVenvDir) { @($LinkDir) } else { @($VenvDir, $LinkDir) }" in bootstrap_fn
    # $LinkDir is not guaranteed to differ from $VenvDir (in the current
    # versioned-runtime wiring they are in fact always the same path), so
    # the exclude branch must not simply trust $LinkDir as a safe stand-in
    # -- it must actively filter out any candidate equal to $VenvDir.
    assert "if ($ExcludeVenvDir -and $VenvDir -and ($d -eq $VenvDir)) { continue }" in bootstrap_fn


@pytest.mark.skipif(os.name != "nt", reason="PowerShell execution is Windows-only here")
def test_get_bootstrap_python_excludes_the_target_slot_even_when_link_dir_equals_venv_dir(
    tmp_path: Path,
):
    """Reproduces the exact production invariant a purely textual check
    can't: $LinkDir can equal $VenvDir (the current versioned-runtime
    wiring always sets them equal), so `Get-BootstrapPython -ExcludeVenvDir`
    must never select that shared directory's own python.exe -- it must
    fall through to the `py` launcher / `Get-ApplicationPath` instead."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    target_dir = tmp_path / "slot"
    (target_dir / "Scripts").mkdir(parents=True)
    (target_dir / "Scripts" / "python.exe").write_bytes(
        b"target slot python -- must never be selected by -ExcludeVenvDir"
    )

    script = r"""
$tokens = $null
$errors = $null
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
$functionAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-BootstrapPython'
}, $true)
if (-not $functionAst) { throw "Missing installer function: Get-BootstrapPython" }
Invoke-Expression $functionAst.Extent.Text

# The exact production invariant: $LinkDir equals $VenvDir.
$VenvDir = $env:TARGET_DIR
$LinkDir = $env:TARGET_DIR

function Get-Command { $null }               # no `py` launcher resolvable
function Get-ApplicationPath { 'FALLBACK-USED' }

Get-BootstrapPython -ExcludeVenvDir
"""
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "INSTALLER": str(INSTALLER),
            "TARGET_DIR": str(target_dir),
        },
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    result = proc.stdout.strip()
    assert result == "FALLBACK-USED", (
        "Get-BootstrapPython -ExcludeVenvDir must never select the target "
        f"slot's own interpreter even when $LinkDir == $VenvDir; got {result!r}"
    )


@pytest.mark.skipif(os.name != "nt", reason="PowerShell execution is Windows-only here")
def test_get_bootstrap_python_excludes_a_path_fallback_resolving_into_venv_dir(
    tmp_path: Path,
):
    """The `py -3` launcher and `Get-ApplicationPath` PATH-search fallbacks
    must be excluded too, not just the initial $dirs candidates: if an
    activated target venv has put its own `Scripts` directory on PATH,
    either fallback could still resolve straight back into $VenvDir and
    defeat -ExcludeVenvDir entirely."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    venv_dir = tmp_path / "slot"
    (venv_dir / "Scripts").mkdir(parents=True)
    excluded_python = venv_dir / "Scripts" / "python.exe"
    excluded_python.write_bytes(b"excluded -- lives inside VenvDir's Scripts")

    link_dir = tmp_path / "other-link"  # genuinely distinct from VenvDir
    (link_dir / "Scripts").mkdir(parents=True)

    script = r"""
$tokens = $null
$errors = $null
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
$functionAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-BootstrapPython'
}, $true)
if (-not $functionAst) { throw "Missing installer function: Get-BootstrapPython" }
Invoke-Expression $functionAst.Extent.Text

$VenvDir = $env:VENV_DIR
$LinkDir = $env:LINK_DIR   # distinct from VenvDir -- no Scripts\python.exe here

function Get-Command { $true }   # `py` launcher IS resolvable
function Invoke-NativeCapture {
    param($ScriptBlock)
    [pscustomobject]@{ ExitCode = 0; Output = $env:EXCLUDED_PYTHON }
}
function Get-ApplicationPath { $env:EXCLUDED_PYTHON }

Get-BootstrapPython -ExcludeVenvDir
"""
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "INSTALLER": str(INSTALLER),
            "VENV_DIR": str(venv_dir),
            "LINK_DIR": str(link_dir),
            "EXCLUDED_PYTHON": str(excluded_python),
        },
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    result = proc.stdout.strip()
    assert result == "", (
        "Get-BootstrapPython -ExcludeVenvDir must reject a py-launcher/"
        f"Get-ApplicationPath result resolving into $VenvDir; got {result!r}"
    )


def test_deploy_venv_acquires_exclusive_build_lease_before_slot_clean():
    """A slot-clean liveness check alone is check-then-act -- two concurrent
    installer invocations could both observe a clean slot (neither has
    started its external build yet) and then both build into it (#5439).
    `Deploy-Venv` must acquire an OS-level exclusive build lease FIRST, then
    validate slot liveness/cleanliness UNCONDITIONALLY (before the
    existing-unsigned-venv-removal logic, regardless of whether $VenvPython
    already exists -- an incomplete slot can still contain a stale
    python.exe), fail immediately if another live process already holds the
    lease, and `Invoke-VersionedActivate` must release that lease afterward
    regardless of outcome."""
    installer = INSTALLER.read_text(encoding="utf-8")
    deploy_fn = installer.split("function Deploy-Venv", 1)[1].split(
        "function Deploy-Wrappers", 1
    )[0]
    activate_wrapper = installer.split("function Invoke-VersionedActivate {", 1)[1]

    lease_idx = deploy_fn.index("Enter-VersionedSlotLease")
    clean_idx = deploy_fn.index("Invoke-VersionedSlotClean")
    rebuild_idx = deploy_fn.index("Rebuild an existing venv")
    assert lease_idx < clean_idx < rebuild_idx, (
        "the exclusive build lease must be acquired first, and slot "
        "liveness/cleanliness validated immediately afterward -- "
        "UNCONDITIONALLY, before the existing-unsigned-venv removal logic "
        "(which is gated on Test-Path $VenvPython and so would otherwise "
        "skip validation for an incomplete slot that still has a stale "
        "python.exe)"
    )
    assert "if (-not (Enter-VersionedSlotLease)) {" in deploy_fn
    lease_fail_branch = deploy_fn.split(
        "if (-not (Enter-VersionedSlotLease)) {", 1
    )[1][:1400]
    assert "return $false" in lease_fail_branch

    # The wrapper must release the lease in a `finally`, so it runs whether
    # Invoke-VersionedActivateInner succeeds or fails.
    assert "try {" in activate_wrapper
    assert "Invoke-VersionedActivateInner" in activate_wrapper
    assert "finally {" in activate_wrapper
    assert "Exit-VersionedSlotLease" in activate_wrapper


def test_versioned_slot_lease_distinguishes_contention_from_a_persistent_failure():
    """Catching bare `[System.IO.IOException]` and always reporting
    "another process is building this slot" would misattribute EVERY
    lease-file failure (permission denied, path too long, disk full, ...)
    to contention, sending an operator chasing a retry loop instead of the
    real, persistent failure. `Enter-VersionedSlotLease` must distinguish
    a genuine sharing/lock violation (ERROR_SHARING_VIOLATION /
    ERROR_LOCK_VIOLATION) from anything else via
    `$script:VersionedSlotLeaseFailureReason`, and `Deploy-Venv`'s error
    message must branch on it."""
    installer = INSTALLER.read_text(encoding="utf-8")
    enter_fn = installer.split("function Enter-VersionedSlotLease {", 1)[1].split(
        "function Exit-VersionedSlotLease {", 1
    )[0]
    deploy_fn = installer.split("function Deploy-Venv", 1)[1].split(
        "function Deploy-Wrappers", 1
    )[0]

    assert "$script:VersionedSlotLeaseFailureReason = $null" in enter_fn
    assert "catch [System.IO.IOException] {" in enter_fn
    assert "$ERROR_SHARING_VIOLATION = 32" in enter_fn
    assert "$ERROR_LOCK_VIOLATION = 33" in enter_fn
    assert "$script:VersionedSlotLeaseFailureReason = 'contention'" in enter_fn
    # Anything that ISN'T a sharing/lock violation must preserve the real
    # exception message, never collapse into the same "contention" bucket.
    assert "$script:VersionedSlotLeaseFailureReason = $_.Exception.Message" in enter_fn

    assert "VersionedSlotLeaseFailureReason -and $script:VersionedSlotLeaseFailureReason -ne 'contention'" in deploy_fn
    assert "Could not acquire the build lease" in deploy_fn
    assert "Another process is already building this runtime slot" in deploy_fn


def test_versioned_slot_lease_handle_initialized_before_use_under_strict_mode():
    """This installer runs under `Set-StrictMode -Version Latest` (asserted
    below): reading a script-scoped variable that was never assigned throws
    `VariableIsUndefined` rather than treating it as falsy/null. The first
    read of `$script:VersionedSlotLeaseHandle` happens in
    `Enter-VersionedSlotLease` (and again in `Exit-VersionedSlotLease`), so
    it must be explicitly initialized at script scope before either
    function can ever run -- not left to spring into existence on first
    assignment."""
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "Set-StrictMode -Version Latest" in installer

    init_idx = installer.index("$script:VersionedSlotLeaseHandle = $null")
    enter_fn_idx = installer.index("function Enter-VersionedSlotLease")
    exit_fn_idx = installer.index("function Exit-VersionedSlotLease")
    assert init_idx < enter_fn_idx < exit_fn_idx, (
        "the script-scoped lease handle must be initialized to $null before "
        "either Enter-VersionedSlotLease or Exit-VersionedSlotLease is "
        "defined, so it already exists by the time either one is called"
    )


def _extract_lease_functions(installer_text: str) -> str:
    """Pull the lease primitive (and its script-scope init) out of the full
    installer, standalone and runnable under `Set-StrictMode -Version
    Latest` without needing the rest of install.ps1's argument parsing /
    side effects."""
    get_path_fn = installer_text.split(
        "function Get-VersionedSlotLeasePath {", 1
    )[1].split("\n}\n", 1)[0]
    enter_fn = installer_text.split("function Enter-VersionedSlotLease {", 1)[
        1
    ].split("\n}\n", 1)[0]
    exit_fn = installer_text.split("function Exit-VersionedSlotLease {", 1)[
        1
    ].split("\n}\n", 1)[0]
    return f"""
Set-StrictMode -Version Latest
function Get-VersionedSlotLeasePath {{
{get_path_fn}
}}
$script:VersionedSlotLeaseHandle = $null
function Enter-VersionedSlotLease {{
{enter_fn}
}}
function Exit-VersionedSlotLease {{
{exit_fn}
}}
"""


def test_versioned_slot_lease_enforces_real_cross_process_exclusion(tmp_path: Path):
    """Behavioral (not just textual) regression guard: under
    `Set-StrictMode -Version Latest`, a process acquiring the lease must not
    crash, acquiring it twice in the SAME process must be idempotent, and a
    SECOND, independent process must be unable to acquire the same version's
    lease while the first one holds it -- proving this is a real OS-level
    exclusive lock, not merely a textual contract."""
    pwsh = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    installer = INSTALLER.read_text(encoding="utf-8")
    lease_functions = _extract_lease_functions(installer)
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    ready_marker = tmp_path / "holder-ready.txt"
    release_marker = tmp_path / "release-now.txt"

    common_preamble = f"""
{lease_functions}
$VersionedRuntime = $true
$InstallDir = "{install_dir}"
$SrcVersion = "1.2.3"
"""

    holder_script = common_preamble + f"""
$first = Enter-VersionedSlotLease
$second = Enter-VersionedSlotLease
[System.IO.File]::WriteAllText("{ready_marker}", "$first,$second")
while (-not (Test-Path "{release_marker}")) {{ Start-Sleep -Milliseconds 100 }}
Exit-VersionedSlotLease
"""
    holder = subprocess.Popen(
        [pwsh, "-NoProfile", "-Command", holder_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(100):  # up to ~10s
            if ready_marker.exists():
                break
            if holder.poll() is not None:
                break
            time.sleep(0.1)
        if not ready_marker.exists():
            # Only read captured output once the holder has actually
            # exited -- reading a live process's pipe blocks until it
            # closes (EOF), which would hang this assertion indefinitely
            # if the holder were still running rather than crashed.
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=30)
            out, err = holder.communicate(timeout=5)
            pytest.fail(f"holder process never reported readiness: {out} {err}")
        assert ready_marker.read_text().strip() == "True,True", (
            "the lease must be acquirable (idempotently, no strict-mode "
            "crash) by its own holder"
        )

        contender_script = common_preamble + "[Console]::Out.Write((Enter-VersionedSlotLease))"
        contender = subprocess.run(
            [pwsh, "-NoProfile", "-Command", contender_script],
            capture_output=True, text=True, timeout=30,
        )
        assert contender.returncode == 0, contender.stderr
        assert contender.stdout.strip() == "False", (
            "a second, independent process must NOT be able to acquire the "
            "same version's build lease while the first process holds it"
        )
    finally:
        release_marker.write_text("go")
        try:
            holder.wait(timeout=30)
        except subprocess.TimeoutExpired:
            holder.kill()
    assert holder.returncode == 0, holder.stderr.read() if holder.stderr else ""


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
