param([switch]$PrepareUv)

function Find-MachinesInstallerUv {
    param([Parameter(Mandatory)][scriptblock]$InvokeCaptured)
    foreach ($command in @(Get-Command uv -CommandType Application -All -ErrorAction SilentlyContinue)) {
        if ($command.Source -match '[\r\n]' -or $command.Source -match '[\\/]WindowsApps[\\/]') { continue }
        $result = Invoke-UvBootstrapSelection -Uv $command.Source -Arguments @('--version') `
            -InvokeCaptured $InvokeCaptured -IsolateSelection
        if ($result.ExitCode -eq 0 -and
            $result.Stdout -match '\Auv[ \t]+([0-9]+\.[0-9]+\.[0-9]+)(?:[ \t(+]|\r?\n|\z)' -and
            [version]$Matches[1] -ge [version]'0.8.0') {
            return $command.Source
        }
    }
    return $null
}

function Get-MachinesInstallerSupport {
    if ((Test-Path -LiteralPath (Join-Path $PSScriptRoot 'bootstrap-uv.ps1')) -or
        (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'installer-instance-python-version.txt'))) {
        return $PSScriptRoot
    }
    return [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../libs/installer-engine'))
}

function Initialize-MachinesBootstrap {
    param(
        [ValidateSet('Inspect', 'Prepare')][string]$Operation = 'Inspect',
        [ValidateRange(1, 2147483647)][int]$TimeoutMilliseconds = 480000
    )
    $support = Get-MachinesInstallerSupport
    . (Join-Path $support 'bootstrap-uv.ps1')
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        . (Join-Path $support 'bootstrap-capture.ps1')
    }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $budget = if ($Operation -eq 'Prepare') { 480000 } else { 30000 }
    $budget = [Math]::Min($budget, $TimeoutMilliseconds)
    $capture = {
        param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
        $remaining = $budget - $clock.ElapsedMilliseconds
        if ($remaining -le 1) { throw [TimeoutException]::new('WATCHDOG: installer bootstrap deadline.') }
        $cleanup = [int][Math]::Min(3000, [Math]::Max(1, [Math]::Floor($remaining / 4)))
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            return Invoke-BootstrapOwnedCapture -FilePath $FilePath -ArgList $ArgList `
                -Environment $Environment -TimeoutMilliseconds ([int]$remaining) -CleanupMilliseconds $cleanup
        }
        $diagnostic = ''
        $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList -Environment $Environment `
            -InputText '' -ErrorOutput ([ref]$diagnostic) `
            -TimeoutMilliseconds ([int]$remaining) -CleanupMilliseconds $cleanup
        [pscustomobject]@{ ExitCode = $LASTEXITCODE; Stdout = [string]$output; Stderr = $diagnostic }
    }
    $uv = Find-MachinesInstallerUv -InvokeCaptured $capture
    if (-not $uv -and $Operation -eq 'Prepare') {
        $hostPath = (Get-Process -Id $PID).Path
        $result = & $capture -FilePath $hostPath -ArgList @(
            '-NoProfile', '-File', $PSCommandPath, '-PrepareUv'
        ) -Environment (Get-UvBootstrapEnvironment)
        if ($result.Stderr) { [Console]::Error.Write($result.Stderr) }
        if ($result.ExitCode -ne 0) { throw 'UV prerequisite setup failed; no Python fallback was attempted.' }
        $localBin = Join-Path $env:USERPROFILE '.local/bin'
        $env:PATH = $localBin + [IO.Path]::PathSeparator + $env:PATH
        $uv = Find-MachinesInstallerUv -InvokeCaptured $capture
    }
    if (-not $uv) { throw 'Prepared uv 0.8.0 or newer is required; run explicit prerequisite setup.' }
    $selection = Resolve-UvBootstrapPython -RequestFile (
        Join-Path $support 'installer-instance-python-version.txt'
    ) -Operation $Operation -UvPath $uv -InvokeCaptured $capture -IsolateSelection
    if ($selection.Status -cne 'selected') {
        throw ('Managed installer Python is unavailable: ' + $selection.Reason)
    }
    $probe = & $capture -FilePath $selection.Python -ArgList @(
        '-I', '-S', '-B', '-c', 'import sys; sys.exit(sys.version_info < (3, 10))'
    ) -Environment (Get-UvBootstrapEnvironment)
    if ($probe.ExitCode -ne 0) { throw 'Managed installer Python does not satisfy the helper requirement.' }
    $script:MachinesInstallerSelection = $selection
    $env:PATH = (Split-Path -Parent $uv) + [IO.Path]::PathSeparator + $env:PATH
}

if ($PrepareUv) {
    $ErrorActionPreference = 'Stop'
    # Astral owns user-local prerequisite installation and durable command exposure.
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } else {
        Invoke-RestMethod https://astral.sh/uv/install.sh | & sh
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    exit 0
}
