from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"
SERVICE_UTILS = PLUGIN / "scripts" / "service-utils.ps1"

pytestmark = pytest.mark.guard


def test_context_stage_captures_both_streams_before_wait_and_retains_unsettled_stage():
    installer = INSTALLER.read_text(encoding="utf-8")
    stage = installer.split("$contextStart = ", 1)[1].split(
        "# === install-contract:v4 self-stage", 1,
    )[0]
    assert stage.index("RedirectStandardOutput = $true") < stage.index(
        "[Diagnostics.Process]::Start"
    )
    assert stage.index("RedirectStandardError = $true") < stage.index(
        "[Diagnostics.Process]::Start"
    )
    assert stage.index("StandardOutput.ReadToEndAsync()") < stage.index(
        "WaitForExit($contextDeadline"
    )
    assert stage.index("StandardError.ReadToEndAsync()") < stage.index(
        "WaitForExit($contextDeadline"
    )
    assert "if (-not $contextChild.WaitForExit(10000))" in stage
    assert "[Threading.Tasks.Task]::WaitAll(" in stage
    assert "retaining stage: $contextStage" in stage
    assert stage.index("GetAwaiter().GetResult()") < stage.index(
        "Remove-Item -LiteralPath $contextStage"
    )


def test_windows_provision_bootstraps_uv_before_runtime_build():
    installer = INSTALLER.read_text(encoding="utf-8")
    provision = installer.split("'provision' {", 1)[1].split("'install' {", 1)[0]
    runtime = installer.split("function Deploy-PythonRuntime {", 1)[1].split("\nfunction ", 1)[0]
    manifest = installer.split("function Write-V3Manifest", 1)[1].split(
        "function Ensure-UvIndex", 1
    )[0]

    assert "Deploy-PythonRuntime" in provision
    assert "Deploy-VenvAndPackage" not in provision
    assert "if (-not (Ensure-Uv)) { throw 'Runtime construction requires uv.' }" in runtime
    assert runtime.index("Ensure-Uv") < runtime.index("runtime_install.py")
    assert runtime.index("Ensure-UvIndex") < runtime.index("runtime_install.py")
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
    assert "& $VenvPython -I (Join-Path $PSScriptRoot 'installer_dependency_policy.py')" in body
    assert "--uv $uvPath -- --python $VenvPython" in body
    assert "--reinstall-package $PkgName" in body
    assert "& $uvPath pip install" not in body
    assert "& uv pip install" not in body


@pytest.mark.parametrize("host", list(dict.fromkeys(
    path for name in ("powershell", "pwsh") if (path := shutil.which(name))
)) or [None])
@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("contextual", [False, True])
def test_python_runtime_resolves_one_uv_application(
    tmp_path: Path, host: str | None, complete: bool, exit_code: int, contextual: bool,
):
    if os.name != "nt" or host is None:
        pytest.skip("native Windows PowerShell application discovery")
    for name in ("first", "second"):
        directory = tmp_path / name
        directory.mkdir()
        shutil.copyfile(sys.executable, directory / "uv.exe")
    slot = tmp_path / "root" / "versions" / "fixture"
    slot.mkdir(parents=True)
    if complete:
        (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    staged = tmp_path / "staged-plugin"
    original = tmp_path / "live-plugin"
    staged.mkdir()
    original.mkdir()
    script = r"""
$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors)
foreach ($name in @('Get-ApplicationPath', 'Resolve-WinGetPackageExecutable', 'Deploy-PythonRuntime')) {
    $node = $ast.Find({
        param($n)
        $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name
    }, $true)
    if (-not $node) { throw "Missing function: $name" }
    Invoke-Expression $node.Extent.Text
}
$InstallDir = $env:FIXTURE_ROOT
$VenvDir = Join-Path $InstallDir 'versions\fixture'
$PluginDir = Get-Item -LiteralPath $env:FIXTURE_PLUGIN
$RuntimeInputPlugin = $env:FIXTURE_LIVE
$ContextualInstall = $env:FIXTURE_CONTEXTUAL -eq '1'
$ScriptDir = Join-Path $PluginDir 'scripts'
$Action = 'update'
$before = @((Get-Command uv -CommandType Application).Source)
if ($before.Count -lt 2) { throw 'Fixture did not expose multiple uv applications' }
function Get-SignedBasePython { $env:FIXTURE_PYTHON }
function Ensure-Uv { $script:ensured = $true; return $true }
function Ensure-UvIndex { $script:indexed = $true }
function Ensure-InstallDir { param($Path) [void][IO.Directory]::CreateDirectory($Path) }
function Assert-SourceRuntime { }
function Assert-ContextPublication { $script:publicationChecked = $true }
function Write-ServiceOk { }
function Invoke-Hidden {
    param($FilePath, $ArgList, $ErrorOutput)
    $uvIndex = [Array]::IndexOf($ArgList, '--uv')
    if ($uvIndex -lt 0 -or $ArgList[$uvIndex + 1] -ne $before[0] -or
        $ArgList[$uvIndex + 2] -ne '--base-python') { throw 'uv argument is not scalar' }
    $pluginIndex = [Array]::IndexOf($ArgList, '--plugin')
    if ($pluginIndex -lt 0 -or $ArgList[$pluginIndex + 1] -ne $env:FIXTURE_PLUGIN) {
        throw 'ordinary update did not preserve its staged construction input'
    }
    $payloadIndex = [Array]::IndexOf($ArgList, '--payload')
    if ($ArgList[$payloadIndex + 1] -ne $env:FIXTURE_ORIGIN) {
        throw 'update lost its original payload provenance'
    }
    if ($ArgList -notcontains '--recover-adopted-manager') { throw 'missing recovery handoff' }
    $resultIndex = [Array]::IndexOf($ArgList, '--result')
    $result = @{
        runtime_version = 'fixture'; runtime_python = $env:FIXTURE_PYTHON
        deployment_manifest_published = $true; reused = $true
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($ArgList[$resultIndex + 1], $result)
    $ErrorOutput.Value = 'fixture construction diagnostic'
    $global:LASTEXITCODE = [int]$env:FIXTURE_EXIT
}
Deploy-PythonRuntime
if ($ContextualInstall -and -not $script:publicationChecked) { throw 'missing publication check' }
if ($env:FIXTURE_COMPLETE -eq '0' -and (-not $script:ensured -or -not $script:indexed)) {
    throw 'Missing preparation'
}
if ($env:FIXTURE_COMPLETE -eq '1' -and ($script:ensured -or $script:indexed)) {
    throw 'Unexpected preparation'
}
if (@((Get-Command uv -CommandType Application).Source).Count -ne $before.Count) {
    throw 'Application discovery changed'
}
"""
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, encoding="utf-8", errors="replace", timeout=20,
        env={
            **os.environ, "INSTALLER": str(INSTALLER),
            "FIXTURE_ROOT": str(tmp_path / "root"), "FIXTURE_PLUGIN": str(staged),
            "FIXTURE_LIVE": str(original), "FIXTURE_CONTEXTUAL": str(int(contextual)),
            "FIXTURE_ORIGIN": str(original if contextual else staged),
            "FIXTURE_PYTHON": sys.executable, "FIXTURE_EXIT": str(exit_code),
            "FIXTURE_COMPLETE": str(int(complete)),
            "COPILOT_PLUGIN_STAGED_FROM": str(original) if contextual else "",
            "PATH": os.pathsep.join([str(tmp_path / "first"), str(tmp_path / "second"),
                                    os.environ.get("PATH", "")]),
        },
        **no_window_kwargs(),
    )
    assert result.returncode == (1 if exit_code else 0), result.stdout + result.stderr
    assert "fixture construction diagnostic" in result.stderr
    if exit_code:
        assert "Python runtime lifecycle failed (exit 7)" in result.stderr
    assert not list((tmp_path / "root" / ".installer-inputs").glob("result-*.json"))


def test_slot_clean_reports_failure_instead_of_silently_downgrading_signed_venv():
    """#5416 regression guard: a stale/still-in-use runtime slot means
    another process may genuinely own (or still be building into) $VenvDir
    right now, so `Deploy-Venv` must refuse to build ANYTHING into it --
    signed or unsigned -- rather than racing that writer.
    `Invoke-VersionedSlotClean` must acquire canonical construction ownership
    or throw, `Deploy-Venv` must retry reported refusal before giving up, and must hard-fail
    (return $false, loud error) instead of falling through to any venv
    build when the slot is still dirty after retries."""
    installer = INSTALLER.read_text(encoding="utf-8")
    clean_fn = installer.split("function Invoke-VersionedSlotClean", 1)[1].split(
        "function Invoke-VersionedMarkComplete", 1
    )[0]
    deploy_fn = installer.split("function Deploy-Venv", 1)[1].split(
        "function Deploy-Wrappers", 1
    )[0]

    assert "construction-shell --shell ps1" in clean_fn
    assert "if ($LASTEXITCODE -ne 0) { throw 'Canonical construction adapter is unavailable' }" in clean_fn
    assert "$script:RuntimeConstructionReuse = Start-RuntimeConstruction" in clean_fn
    assert "-Python $py -Helper $vr -Root $InstallDir -Version $SrcVersion" in clean_fn
    assert "-PayloadHash (Get-PayloadHash)" in clean_fn
    assert clean_fn.index("Start-RuntimeConstruction `") < clean_fn.index("return $true")
    assert "catch" not in clean_fn

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
    via `Get-BootstrapPython -ExcludeVenvDir`, and must fail CLOSED for both
    existing and absent candidates when no interpreter can be found: even a
    fresh candidate requires construction ownership before its first write."""
    installer = INSTALLER.read_text(encoding="utf-8")
    clean_fn = installer.split("function Invoke-VersionedSlotClean", 1)[1].split(
        "function Invoke-VersionedMarkComplete", 1
    )[0]
    bootstrap_fn = installer.split("function Get-BootstrapPython {", 1)[1].split(
        "\n}\n", 1
    )[0]

    assert "$py = Get-BootstrapPython -ExcludeVenvDir" in clean_fn
    assert "if (-not $py) { throw 'Construction requires a bootstrap Python interpreter' }" in clean_fn
    assert clean_fn.index("if (-not $py)") < clean_fn.index("Start-RuntimeConstruction `")

    assert "[switch]$ExcludeVenvDir" in bootstrap_fn
    assert "[switch]$ExcludeRuntimeRoot" in bootstrap_fn
    assert (
        "$dirs = if ($ExcludeRuntimeRoot) { @() } elseif ($ExcludeVenvDir) "
        "{ @($LinkDir) } else { @($VenvDir, $LinkDir) }"
    ) in bootstrap_fn
    # $LinkDir is not guaranteed to differ from $VenvDir (in the current
    # versioned-runtime wiring they are in fact always the same path), so
    # the exclude branch must not simply trust $LinkDir as a safe stand-in
    # -- it must actively filter out any candidate equal to $VenvDir.
    assert "if ($ExcludeVenvDir -and $VenvDir -and ($d -eq $VenvDir)) { continue }" in bootstrap_fn


@pytest.mark.skipif(os.name != "nt", reason="PowerShell execution is Windows-only here")
@pytest.mark.parametrize("exclusion", ["ExcludeVenvDir", "ExcludeRuntimeRoot"])
def test_get_bootstrap_python_excludes_the_target_slot_even_when_link_dir_equals_venv_dir(
    tmp_path: Path, exclusion: str,
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
$InstallDir = $env:INSTALL_DIR

function Get-Command { $null }               # no `py` launcher resolvable
function Get-ApplicationPath { 'FALLBACK-USED' }

$options=@{}
$options[$env:EXCLUSION]=$true
Get-BootstrapPython @options
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
            "INSTALL_DIR": str(tmp_path),
            "EXCLUSION": exclusion,
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
@pytest.mark.parametrize("exclusion", ["ExcludeVenvDir", "ExcludeRuntimeRoot"])
def test_get_bootstrap_python_excludes_a_path_fallback_resolving_into_venv_dir(
    tmp_path: Path, exclusion: str,
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
$InstallDir = $env:INSTALL_DIR

function Get-Command { $true }   # `py` launcher IS resolvable
function Invoke-NativeCapture {
    param($ScriptBlock)
    [pscustomobject]@{ ExitCode = 0; Output = $env:EXCLUDED_PYTHON }
}
function Get-ApplicationPath { $env:EXCLUDED_PYTHON }

$options=@{}
$options[$env:EXCLUSION]=$true
Get-BootstrapPython @options
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
            "INSTALL_DIR": str(tmp_path),
            "EXCLUSION": exclusion,
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
    `Deploy-Venv` must acquire an OS-level exclusive build lease FIRST (via
    the bounded-wait wrapper, `Wait-ForVersionedSlotLease`), then validate
    slot liveness/cleanliness UNCONDITIONALLY (before the
    existing-unsigned-venv-removal logic, regardless of whether $VenvPython
    already exists -- an incomplete slot can still contain a stale
    python.exe), fail if another live process still holds the lease after
    the bounded wait, and `Invoke-VersionedActivate` must release that lease
    afterward regardless of outcome."""
    installer = INSTALLER.read_text(encoding="utf-8")
    deploy_fn = installer.split("function Deploy-Venv", 1)[1].split(
        "function Deploy-Wrappers", 1
    )[0]
    activate_wrapper = installer.split("function Invoke-VersionedActivate {", 1)[1]

    lease_idx = deploy_fn.index("Wait-ForVersionedSlotLease")
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
    assert "if (-not (Wait-ForVersionedSlotLease)) {" in deploy_fn
    lease_fail_branch = deploy_fn.split(
        "if (-not (Wait-ForVersionedSlotLease)) {", 1
    )[1][:2200]
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
    # `File.Open` reports some persistent failures (e.g. an ACL denial) as
    # `UnauthorizedAccessException`, NOT `IOException` -- a bare catch
    # typed to IOException alone would let those throw straight out of
    # this function uncaught instead of returning $false with a reason.
    assert "} catch {" in enter_fn
    catchall_branch = enter_fn.split("} catch {", 1)[1]
    assert "$script:VersionedSlotLeaseFailureReason = $_.Exception.Message" in catchall_branch
    assert "return $false" in catchall_branch

    assert "VersionedSlotLeaseFailureReason -and $script:VersionedSlotLeaseFailureReason -ne 'contention'" in deploy_fn
    assert "Could not acquire the build lease" in deploy_fn
    assert "Another process is still building this runtime slot" in deploy_fn
    assert "AGENT_WORKTREES_SLOT_LEASE_WAIT_SEC" in deploy_fn


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
    wait_fn = installer_text.split("function Wait-ForVersionedSlotLease {", 1)[
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
function Wait-ForVersionedSlotLease {{
{wait_fn}
}}
function Exit-VersionedSlotLease {{
{exit_fn}
}}
"""


def test_versioned_slot_lease_classifies_only_platform_contention_codes(tmp_path: Path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    functions = _extract_lease_functions(INSTALLER.read_text("utf-8"))
    functions = re.sub(
        r"\$script:VersionedSlotLeaseHandle = \[System.IO.File\]::Open\([\s\S]*?\)",
        "throw [System.IO.IOException]::new('synthetic lease failure', $TestHResult)",
        functions, count=1,
    )
    functions, probe_count = re.subn(
        r"\[System\.Runtime\.InteropServices\.RuntimeInformation\]::IsOSPlatform"
        r"\(\[System\.Runtime\.InteropServices\.OSPlatform\]::(\w+)\)",
        r"(Test-FixtureOSPlatform '\1')",
        functions,
    )
    assert probe_count == 2
    script = tmp_path / "platform-contention.ps1"
    script.write_text(
        """
function Test-FixtureOSPlatform {
    param([string]$Requested)
    return $Requested.ToUpperInvariant() -eq $script:FixtureLeaseOS
}
""" + functions + f'\n$InstallDir="{tmp_path}"\n$SrcVersion="1.2.3"\n'
        "$VersionedRuntime=$true\n"
        "$cases=@(\n"
        " @{OS='WINDOWS';Code=32}, @{OS='WINDOWS';Code=33},\n"
        " @{OS='LINUX';Code=11}, @{OS='OSX';Code=35},\n"
        " @{OS='LINUX';Code=13}, @{OS='WINDOWS';Code=11})\n"
        "$results=@(foreach ($case in $cases) {\n"
        " $script:FixtureLeaseOS=$case.OS\n"
        " $env:OS=if ($case.OS -eq 'WINDOWS') {'Windows_NT'} else {'Fixture_Posix'}\n"
        " $TestHResult=-2147024896+$case.Code\n"
        " $acquired=Enter-VersionedSlotLease\n"
        " [pscustomobject]@{acquired=$acquired;reason=$script:VersionedSlotLeaseFailureReason}\n"
        "})\nConvertTo-Json -InputObject $results -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(script)],
        capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    results = json.loads(result.stdout)
    assert all(not item["acquired"] for item in results)
    assert [item["reason"] for item in results] == [
        "contention", "contention", "contention", "contention",
        "synthetic lease failure", "synthetic lease failure",
    ]


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

    # The file intentionally survives release. Its existence must not be
    # mistaken for a live builder, and deleting it must not be required.
    lease_path = install_dir / ".build-lease-1.2.3.lock"
    assert lease_path.exists()
    after_release = subprocess.run(
        [
            pwsh, "-NoProfile", "-Command",
            common_preamble
            + "[Console]::Out.Write((Enter-VersionedSlotLease)); Exit-VersionedSlotLease",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert after_release.returncode == 0, after_release.stderr
    assert after_release.stdout.strip() == "True"
    assert lease_path.exists()


@pytest.mark.parametrize(
    "native_code,platform,contention",
    [(11, "Linux", True), (35, "OSX", True), (35, "Linux", False),
     (11, "OSX", False), (11, "Windows", False), (32, "Windows", True),
     (33, "Windows", True), (13, "Linux", False), (32, "Linux", False),
     (33, "Linux", False), (32, "OSX", False), (33, "OSX", False)],
)
def test_wrapped_slot_lease_io_errors_keep_platform_specific_reasons(
    tmp_path: Path, native_code: int, platform: str, contention: bool,
):
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    functions = _extract_lease_functions(INSTALLER.read_text(encoding="utf-8"))
    functions = functions.replace("[System.IO.File]::Open(", "[FixtureLeaseFile]::Open(")
    functions = functions.replace(
        "[System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform",
        "[FixtureLeasePlatform]::IsOSPlatform",
    )
    script = f"""
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
public static class FixtureLeaseFile {{
    public static FileStream Open(string path, FileMode mode, FileAccess access, FileShare share) {{
        throw new IOException("fixture I/O failure", {native_code});
    }}
}}
public static class FixtureLeasePlatform {{
    public static bool IsOSPlatform(OSPlatform requested) {{
        return requested == OSPlatform.{platform};
    }}
}}
'@
{functions}
$VersionedRuntime=$true
$InstallDir='{tmp_path}'
$SrcVersion='1.2.3'
$env:OS='{"Windows_NT" if platform == "Windows" else "Fixture_Posix"}'
if (Enter-VersionedSlotLease) {{ throw 'unexpected lease acquisition' }}
$isContention=$script:VersionedSlotLeaseFailureReason -eq 'contention'
if ($isContention -ne ${str(contention).lower()}) {{ throw "wrong failure classification: $script:VersionedSlotLeaseFailureReason" }}
if (-not $isContention -and $script:VersionedSlotLeaseFailureReason -notlike '*fixture I/O failure*') {{ throw 'persistent error lost' }}
"""
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_wait_for_versioned_slot_lease_reuses_winner_within_bounded_budget(tmp_path: Path):
    """Bounded-join regression (phase-3-runtime-admission, #5472/#5788):
    a contender calling Wait-ForVersionedSlotLease while another process
    holds the lease must NOT give up on first contention. It must poll,
    and once the holder releases (well within the configured budget), the
    contender must acquire the now-free lease itself -- proving this is a
    real bounded wait/retry, not merely a renamed single-shot refusal."""
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
[void](Enter-VersionedSlotLease)
[System.IO.File]::WriteAllText("{ready_marker}", "ready")
while (-not (Test-Path "{release_marker}")) {{ Start-Sleep -Milliseconds 50 }}
Exit-VersionedSlotLease
"""
    holder = subprocess.Popen(
        [pwsh, "-NoProfile", "-Command", holder_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(100):  # up to ~10s
            if ready_marker.exists() or holder.poll() is not None:
                break
            time.sleep(0.1)
        assert ready_marker.exists(), "holder process never reported readiness"

        contender_env = dict(os.environ)
        contender_env["AGENT_WORKTREES_SLOT_LEASE_WAIT_SEC"] = "20"
        contender_env["AGENT_WORKTREES_SLOT_LEASE_POLL_MS"] = "100"
        contender_script = common_preamble + "[Console]::Out.Write((Wait-ForVersionedSlotLease))"
        contender = subprocess.Popen(
            [pwsh, "-NoProfile", "-Command", contender_script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=contender_env,
        )
        try:
            # Let the contender observe genuine contention at least once
            # before the holder releases, so this actually exercises the
            # poll/retry path rather than a lucky first-attempt acquisition.
            time.sleep(0.5)
            release_marker.write_text("go")
            out, err = contender.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            contender.kill()
            pytest.fail("contender's bounded wait did not return within its own budget")
        assert contender.returncode == 0, err
        assert out.strip() == "True", (
            "a bounded-wait contender must acquire the lease once the "
            "holder releases it within the configured budget"
        )
    finally:
        release_marker.write_text("go")
        try:
            holder.wait(timeout=30)
        except subprocess.TimeoutExpired:
            holder.kill()
    assert holder.returncode == 0, holder.stderr.read() if holder.stderr else ""


def test_wait_for_versioned_slot_lease_times_out_when_never_released(tmp_path: Path):
    """The bounded wait must actually be bounded: if the holder never
    releases, a contender configured with a short budget must return
    $false at (approximately) that budget, not hang indefinitely."""
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
[void](Enter-VersionedSlotLease)
[System.IO.File]::WriteAllText("{ready_marker}", "ready")
while (-not (Test-Path "{release_marker}")) {{ Start-Sleep -Milliseconds 50 }}
Exit-VersionedSlotLease
"""
    holder = subprocess.Popen(
        [pwsh, "-NoProfile", "-Command", holder_script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(100):  # up to ~10s
            if ready_marker.exists() or holder.poll() is not None:
                break
            time.sleep(0.1)
        assert ready_marker.exists(), "holder process never reported readiness"

        contender_env = dict(os.environ)
        contender_env["AGENT_WORKTREES_SLOT_LEASE_WAIT_SEC"] = "2"
        contender_env["AGENT_WORKTREES_SLOT_LEASE_POLL_MS"] = "100"
        contender_script = common_preamble + "[Console]::Out.Write((Wait-ForVersionedSlotLease))"
        started = time.monotonic()
        contender = subprocess.run(
            [pwsh, "-NoProfile", "-Command", contender_script],
            capture_output=True, text=True, timeout=30,
            env=contender_env,
        )
        elapsed = time.monotonic() - started
        assert contender.returncode == 0, contender.stderr
        assert contender.stdout.strip() == "False", (
            "a bounded-wait contender must give up, not hang, once its "
            "configured budget elapses while the holder never releases"
        )
        assert elapsed < 15, (
            f"bounded wait took {elapsed:.1f}s against a 2s budget -- "
            "the timeout is not actually bounding the wait"
        )
        assert elapsed >= 1.5, (
            f"bounded wait took only {elapsed:.1f}s against a 2s budget -- "
            "the configured wait duration is not actually being honored "
            "(a waiter that ignores the override and returns immediately, "
            "or after some shorter hardcoded timeout, would also satisfy "
            "the upper-bound check alone)"
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
def test_native_stamp_directory_copy_preserves_complete_tree(tmp_path: Path):
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is unavailable")
    source = tmp_path / "source [literal]"
    destination = tmp_path / "snapshot"
    (source / ".hidden" / "nested").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / ".hidden" / "nested" / "payload.bin").write_bytes(bytes(range(256)))
    (source / "metadata.json").write_text('{"fixture": true}', encoding="utf-8")
    bytecode = source / ".hidden" / "nested" / "__pycache__"
    bytecode.mkdir()
    (bytecode / "stale.pyc").write_bytes(b"not payload")
    installer = INSTALLER.read_text(encoding="utf-8")
    function = "function Copy-StampDirectory" + installer.split(
        "function Copy-StampDirectory", 1
    )[1].split("function Invoke-Stamp", 1)[0]
    script = tmp_path / "copy.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n" + function
        + "\nCopy-StampDirectory $args[0] $args[1]\n", encoding="utf-8"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(script), str(source), str(destination)],
        capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    payload = [
        path for path in source.rglob("*")
        if "__pycache__" not in path.relative_to(source).parts
    ]
    assert {str(path.relative_to(source)) for path in payload} == {
        str(path.relative_to(destination)) for path in destination.rglob("*")
    }
    for path in payload:
        if path.is_file():
            assert (destination / path.relative_to(source)).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("paused", [False, True])
def test_powershell_hook_bootstrap_uses_canonical_lifecycle(tmp_path: Path, paused):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "killswitch.json"
    state.write_text(json.dumps({"active": paused}), encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(PLUGIN),
        "COPILOT_EXTENSIONS_CONTEXT": "",
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(state),
    }
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(PLUGIN / "scripts" / "bootstrap-check.ps1"),
         "-BootstrapPython", sys.executable],
        capture_output=True, text=True, env=env, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    runtime = home / ".agent-worktrees"
    if paused:
        assert not runtime.exists()
        assert "bootstrap-killswitch" in result.stderr
    else:
        assert (runtime / "stamped-version").is_file(), result.stderr
        snapshot = Path((runtime / "payload-dir").read_text().strip())
        assert (snapshot / "scripts" / "command_binding.py").is_file()
        assert (home / ".local" / "bin" / "agent-worktrees.cmd").is_file()
        assert not (runtime / "versions").exists()


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
