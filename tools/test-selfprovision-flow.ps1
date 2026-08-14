<#
.SYNOPSIS
    Isolated-HOME validation of a runtime plugin's Windows self-provisioning flow
    (the #1393 "fast stamp + snapshot slot + self-provisioning binstub" contract).

.DESCRIPTION
    For a given plugin + installer entry (install.ps1 | init.ps1), runs the flow a
    naive fresh box would see -- with USERPROFILE/HOME redirected to a throwaway
    dir so the real ~/.<plugin> and ~/.local/bin are never touched:

      1. `<entry> stamp`   -> snapshot the payload SOURCE into
         ~/.<plugin>/snapshots/<ver>/, write markers (payload-dir, stamped-version),
         deploy the self-provisioning binstub. Assert: fast, NO venv, NO current-version.
      2. first binstub call -> self-provisions (builds the venv from the slot-local
         snapshot via `<entry> provision`) then dispatches. Assert: venv built,
         current-version published, rc == 0.
      3. second call        -> fast path (no re-provision).
      4. .cmd fast path     -> dispatches (when a .cmd binstub exists).

    Exits non-zero if any assertion fails. The install body persists a User PATH
    add when its bin dir isn't already on PATH, so we pre-add the isolated bin to
    $env:PATH to keep the real environment untouched.

.PARAMETER Plugin
    Plugin name (e.g. agent-ssh, agent-mcp). Drives ~/.<Plugin> and the module name.

.PARAMETER Entry
    Installer entry script under plugins/<Plugin>/scripts (install.ps1 | init.ps1).

.PARAMETER SmokeArgs
    Args passed to the binstub for the dispatch smoke (default: --help).

.PARAMETER Module
    Python module the binstub runs (default: <Plugin> with '-' -> '_').

.PARAMETER ProvisionTimeoutSec
    Deadline for the first (provisioning) call. Default 240.

.PARAMETER SmokeTimeoutSec
    Deadline for the fast-path calls. Default 30.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Plugin,
    [ValidateSet('install.ps1', 'init.ps1')][string]$Entry = 'install.ps1',
    [string[]]$SmokeArgs = @('--help'),
    [string]$Module,
    [int]$ProvisionTimeoutSec = 240,
    [int]$SmokeTimeoutSec = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $Module) { $Module = $Plugin -replace '-', '_' }

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pluginDir = Join-Path $repoRoot "plugins\$Plugin"
$inst = Join-Path $pluginDir "scripts\$Entry"
if (-not (Test-Path $inst)) { throw "Installer not found: $inst" }
$ver = ((Select-String -Path (Join-Path $pluginDir 'pyproject.toml') -Pattern '^\s*version\s*=' |
    Select-Object -First 1).Line -replace '.*=\s*"([^"]+)".*', '$1')
Write-Host "== $Plugin $ver ($Entry) ==" -ForegroundColor Cyan

$fails = New-Object System.Collections.Generic.List[string]
function Check($name, $ok) {
    "{0,-24} {1}" -f $name, $(if ($ok) { 'PASS' } else { 'FAIL' }) | Write-Host
    if (-not $ok) { $script:fails.Add($name) }
}

$th = Join-Path $env:TEMP ("cr-$Plugin-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
$localbin = Join-Path $th '.local\bin'
New-Item -ItemType Directory -Force -Path $localbin | Out-Null
$root = Join-Path $th ".$Plugin"
$savedUP = $env:USERPROFILE; $savedHOME = $env:HOME; $savedPATH = $env:PATH
$env:USERPROFILE = $th; $env:HOME = $th; $env:PATH = "$localbin;$env:PATH"
$pwshExe = (Get-Command pwsh).Source

function Invoke-Binstub([string[]]$binArgs, [int]$timeoutSec) {
    # Prefer the .ps1 (primary) then the .cmd; return @{rc, seconds, stub}.
    $ps1 = Join-Path $localbin "$Plugin.ps1"
    $cmd = Join-Path $localbin "$Plugin.cmd"
    $t0 = Get-Date
    if (Test-Path $ps1) {
        $p = Start-Process -FilePath $pwshExe -PassThru -NoNewWindow -Wait:$false `
            -ArgumentList (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ps1) + $binArgs)
        $stub = 'ps1'
    } else {
        $p = Start-Process -FilePath $env:ComSpec -PassThru -NoNewWindow -Wait:$false `
            -ArgumentList (@('/c', $cmd) + $binArgs)
        $stub = 'cmd'
    }
    if (-not $p.WaitForExit($timeoutSec * 1000)) {
        try { & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null } catch {}
        return @{ rc = 124; seconds = $timeoutSec; stub = $stub; timedout = $true }
    }
    return @{ rc = $p.ExitCode; seconds = [int]((Get-Date) - $t0).TotalSeconds; stub = $stub; timedout = $false }
}

try {
    Write-Host "`n=== STEP 1: stamp (fast, no venv) ===" -ForegroundColor Yellow
    & $pwshExe -NoProfile -ExecutionPolicy Bypass -File $inst stamp | Out-Host
    $snap = Join-Path $root ("snapshots\$ver")
    Check 'snapshot dir'          (Test-Path $snap)
    Check 'snapshot entry'        (Test-Path (Join-Path $snap "scripts\$Entry"))
    Check 'payload-dir marker'    (Test-Path (Join-Path $root 'payload-dir'))
    Check 'stamped-version'       (Test-Path (Join-Path $root 'stamped-version'))
    Check 'binstub present'       ((Test-Path (Join-Path $localbin "$Plugin.cmd")) -or (Test-Path (Join-Path $localbin "$Plugin.ps1")))
    Check 'NO venv yet'           (-not (Test-Path (Join-Path $root "versions\$ver\Scripts\python.exe")))
    Check 'NO current-version'    (-not (Test-Path (Join-Path $root 'current-version')))

    Write-Host "`n=== STEP 2: first call -> self-provision + dispatch ===" -ForegroundColor Yellow
    $r1 = Invoke-Binstub $SmokeArgs $ProvisionTimeoutSec
    Write-Host ("  first call: stub={0} rc={1} {2}s" -f $r1.stub, $r1.rc, $r1.seconds)
    Check 'venv built'            (Test-Path (Join-Path $root "versions\$ver\Scripts\python.exe"))
    Check 'current-version'       (Test-Path (Join-Path $root 'current-version'))
    Check 'first-call rc==0'      ($r1.rc -eq 0)

    Write-Host "`n=== STEP 3: second call -> fast path ===" -ForegroundColor Yellow
    $r2 = Invoke-Binstub $SmokeArgs $SmokeTimeoutSec
    Write-Host ("  second call: rc={0} {1}s" -f $r2.rc, $r2.seconds)
    Check 'second-call rc==0'     ($r2.rc -eq 0)
    Check 'second-call fast(<15s)' ($r2.seconds -lt 15)

    if (Test-Path (Join-Path $localbin "$Plugin.cmd")) {
        Write-Host "`n=== STEP 4: .cmd fast path ===" -ForegroundColor Yellow
        $c = Start-Process -FilePath $env:ComSpec -PassThru -NoNewWindow -Wait:$false `
            -ArgumentList (@('/c', (Join-Path $localbin "$Plugin.cmd")) + $SmokeArgs)
        $c.WaitForExit($SmokeTimeoutSec * 1000) | Out-Null
        Check '.cmd rc==0'        ($c.ExitCode -eq 0)
    }
}
finally {
    $env:USERPROFILE = $savedUP; $env:HOME = $savedHOME; $env:PATH = $savedPATH
    Remove-Item -Recurse -Force $th -ErrorAction SilentlyContinue
}

Write-Host ''
if ($fails.Count -eq 0) {
    Write-Host "ALL PASS -- $Plugin self-provisioning flow OK" -ForegroundColor Green
    exit 0
} else {
    Write-Host ("FAILED ({0}): {1}" -f $fails.Count, ($fails -join ', ')) -ForegroundColor Red
    exit 1
}
