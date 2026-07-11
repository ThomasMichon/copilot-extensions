<#
.SYNOPSIS
    WSL Keepalive -- installer (Windows).

.DESCRIPTION
    Pins a WSL2 distro up (and optionally ensures a systemd service is started)
    so a WSL-hosted listener stays reachable. An idle WSL distro terminates and
    takes its services with it.

    The keepalive is a detached `sleep infinity` inside the distro, launched via
    a **windowless** VBS launcher (WScript.Shell.Run style 0) so wsl.exe never
    flashes a console window -- registering a Scheduled Task to run wsl.exe
    directly would pop a visible console on every fire (the task -Hidden flag
    hides the task, not the child console). A logon trigger re-establishes it
    after each reboot.

    Run from the copilot-extensions repo root:
      $ka = 'plugins\wsl-setup\skills\setting-up-wsl\references\wsl-keepalive.ps1'
      pwsh -File $ka install -Distro Ubuntu-22.04 -Service ssh -TaskName WSL-SSH-Keepalive
      pwsh -File $ka status  -TaskName WSL-SSH-Keepalive
      pwsh -File $ka uninstall -TaskName WSL-SSH-Keepalive

    Registering/removing the Scheduled Task requires an elevated shell.

.PARAMETER Action
    Lifecycle action: install | uninstall | status.
.PARAMETER Distro
    WSL distro to keep alive (required for install), e.g. Ubuntu-22.04.
.PARAMETER Service
    Optional systemd service to `systemctl start` before pinning (e.g. ssh).
.PARAMETER TaskName
    Scheduled Task name. Defaults to WSL-Keepalive-<Distro>.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'status')]
    [string]$Action = 'status',
    [string]$Distro,
    [string]$Service,
    [string]$TaskName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Ok      { param([string]$m) Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Changed { param([string]$m) Write-Host "  [->]   $m" -ForegroundColor Yellow }
function Write-Step    { param([string]$m) Write-Host "  ...    $m" }
function Write-Warn    { param([string]$m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-Fail    { param([string]$m) Write-Host "  [FAIL] $m" -ForegroundColor Red }

$InstallDir = Join-Path $env:LOCALAPPDATA 'wsl-keepalive'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Resolve-TaskName {
    if ($TaskName) { return $TaskName }
    if ($Distro)   { return "WSL-Keepalive-$Distro" }
    Write-Fail "Provide -TaskName (or -Distro to derive it)."; exit 1
}

function New-KeepaliveVbs {
    param([string]$Distro, [string]$Service, [string]$VbsPath)
    # Build the in-distro command: optionally start the service, then pin.
    $svc = if ($Service) { "systemctl start $Service; " } else { "" }
    $inner = "$svc" + "exec sleep infinity"
    # VBScript quoting: a literal double-quote inside a string literal must be
    # DOUBLED. Use [char]34 to sidestep PowerShell's own quote-escaping entirely.
    $q  = [char]34          # one literal "  (delimits VBS string literals)
    $dq = "$q$q"            # ""  -> one escaped quote inside a VBS string
    # The sh -c payload is wrapped in escaped quotes so it survives as one arg.
    $wslCmd  = "wsl.exe -d $Distro -u root --exec /bin/sh -c $dq$inner$dq"
    $runLine = "CreateObject(${q}WScript.Shell${q}).Run ${q}$wslCmd${q}, 0, False"
    $vbs = @(
        "' Windowless WSL keepalive launcher (managed by wsl-setup plugin).",
        "' Run style 0 = hidden; no console window or flash.",
        $runLine
    ) -join "`r`n"
    New-Item -ItemType Directory -Force (Split-Path $VbsPath) | Out-Null
    Set-Content -Path $VbsPath -Value $vbs -Encoding ASCII
}

function Invoke-Install {
    if (-not $Distro) { Write-Fail "install requires -Distro (e.g. Ubuntu-22.04)."; exit 1 }
    if (-not (Test-Admin)) { Write-Fail "install must run in an ELEVATED shell (Scheduled Task registration)."; exit 1 }
    $tn = Resolve-TaskName
    $vbsPath = Join-Path $InstallDir "$tn.vbs"

    Write-Step "Deploying windowless launcher -> $vbsPath"
    New-KeepaliveVbs -Distro $Distro -Service $Service -VbsPath $vbsPath
    Write-Ok "Launcher deployed"

    Write-Step "Registering Scheduled Task '$tn' (at-logon, windowless)"
    $act = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$vbsPath`""
    $trg = New-ScheduledTaskTrigger -AtLogOn
    $prn = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
    Register-ScheduledTask -TaskName $tn -Action $act -Trigger $trg -Principal $prn -Settings $set -Force | Out-Null
    Write-Ok "Task registered"

    # Kill any prior keepalive for this distro (ad-hoc or stale), then start fresh.
    Write-Step "Restarting keepalive"
    Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" |
        Where-Object { $_.CommandLine -match [regex]::Escape($Distro) -and $_.CommandLine -match 'sleep infinity' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
    Start-ScheduledTask -TaskName $tn

    $manifest = @{ distro = $Distro; service = $Service; task = $tn; vbs = $vbsPath; installedAt = (Get-Date -Format o) } | ConvertTo-Json
    Set-Content -Path (Join-Path $InstallDir 'deploy-manifest.json') -Value $manifest -Encoding UTF8
    Write-Ok "WSL keepalive installed (distro=$Distro service=$Service task=$tn)"
}

function Invoke-Uninstall {
    if (-not (Test-Admin)) { Write-Fail "uninstall must run in an ELEVATED shell."; exit 1 }
    $tn = Resolve-TaskName
    if (Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $tn -Confirm:$false
        Write-Changed "Removed task '$tn'"
    } else { Write-Ok "Task '$tn' not present" }
    $vbsPath = Join-Path $InstallDir "$tn.vbs"
    if (Test-Path $vbsPath) { Remove-Item $vbsPath -Force; Write-Changed "Removed launcher" }
    Write-Ok "WSL keepalive uninstalled ($tn)"
}

function Invoke-Status {
    $tn = Resolve-TaskName
    $task = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
    if ($task) { Write-Ok "Task '$tn': $($task.State)" } else { Write-Warn "Task '$tn': not registered" }
    if ($Distro) {
        $running = (wsl.exe -l -v 2>$null | Select-String $Distro | Select-String 'Running')
        if ($running) { Write-Ok "Distro '$Distro': Running" } else { Write-Warn "Distro '$Distro': not Running" }
        if ($Service) {
            $active = (wsl.exe -d $Distro -u root bash -c "systemctl is-active $Service" 2>$null)
            Write-Host "  ...    service '$Service': $active"
        }
    }
}

switch ($Action) {
    'install'   { Invoke-Install }
    'uninstall' { Invoke-Uninstall }
    default     { Invoke-Status }
}
