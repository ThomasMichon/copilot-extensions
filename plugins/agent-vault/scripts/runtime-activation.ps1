function Get-VaultRuntimeActivation {
    param(
        [string]$Project = (Get-Location).ProviderPath,
        [string]$RuntimeRoot,
        [string]$Context,
        [string]$Python
    )
    $reader = Join-Path $PSScriptRoot 'runtime-activation.py'
    $pythonPath = $Python
    $pythonArgs = @()
    if (-not $pythonPath) {
        $candidate = Get-Command python3 -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
        if (-not $candidate) {
            $candidate = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
                Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
        }
        if (-not $candidate) {
            $candidate = Get-Command py -CommandType Application -ErrorAction SilentlyContinue |
                Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
            $pythonArgs = @('-3')
        }
        if (-not $candidate) {
            throw 'Agent Vault activation requires an available Python reader; no installation was attempted.'
        }
        $pythonPath = $candidate.Source
    }
    $previousPreference = $ErrorActionPreference
    $readerArgs = @('--cwd', $Project)
    if ($RuntimeRoot) { $readerArgs += "--runtime-root=$RuntimeRoot" }
    if ($PSBoundParameters.ContainsKey('Context')) { $readerArgs += "--context=$Context" }
    $ErrorActionPreference = 'Continue'
    try {
        $output = @(& $pythonPath @pythonArgs -I -B -X utf8 $reader @readerArgs)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    $result = ($output -join "`n") | ConvertFrom-Json -ErrorAction Stop
    if ($code -notin @(0, 2) -or $result.state -notin @('configured', 'configuration-empty')) {
        throw "Agent Vault activation configuration could not be resolved; correct its configuration, binding or payload. Reader exit: $code; state: $($result.state). $($result.detail)"
    }
    return $result
}
