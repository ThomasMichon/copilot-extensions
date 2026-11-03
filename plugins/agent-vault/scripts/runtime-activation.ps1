function Get-VaultRuntimeActivation {
    param(
        [string]$Project = (Get-Location).ProviderPath,
        [string]$RuntimeRoot,
        [string]$Context
    )
    $reader = Join-Path $PSScriptRoot 'runtime-activation.py'
    $python = Get-Command python3 -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
    if (-not $python -or $python.Source -like '*\WindowsApps\*') {
        $python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
    }
    $pythonArgs = @()
    if (-not $python) {
        $python = Get-Command py -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
        $pythonArgs = @('-3')
    }
    if (-not $python -or $python.Source -like '*\WindowsApps\*') {
        throw 'Agent Vault activation requires an available Python reader; no installation was attempted.'
    }
    $previousPreference = $ErrorActionPreference
    $readerArgs = @('--cwd', $Project)
    if ($RuntimeRoot) { $readerArgs += "--runtime-root=$RuntimeRoot" }
    if ($PSBoundParameters.ContainsKey('Context')) { $readerArgs += "--context=$Context" }
    $ErrorActionPreference = 'Continue'
    try {
        $output = @(& $python.Source @pythonArgs -I -B -X utf8 $reader @readerArgs)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    $result = ($output -join "`n") | ConvertFrom-Json -ErrorAction Stop
    if ($code -notin @(0, 2) -or $result.state -notin @('configured', 'configuration-empty')) {
        throw 'Agent Vault activation configuration could not be resolved; correct its configuration, binding or payload.'
    }
    return $result
}
