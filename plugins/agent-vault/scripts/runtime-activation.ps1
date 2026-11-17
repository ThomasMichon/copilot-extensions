function Get-VaultRuntimeActivation {
    param(
        [string]$Project = (Get-Location).ProviderPath,
        [string]$RuntimeRoot,
        [string]$Context,
        [string]$Python,
        [string]$InvocationDirectory,
        [switch]$RetainNativeSelector,
        [switch]$ReadOnlyProbe
    )
    $reader = Join-Path $PSScriptRoot 'runtime-activation.py'
    . (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
    $pythonPath = $Python
    if (-not $pythonPath) {
        $pythonPath = Resolve-NativeBootstrapPython -MinimumMinor 10
        if (-not $pythonPath) {
            throw 'Agent Vault activation requires an existing Python 3.10+ reader; no installation was attempted.'
        }
    }
    $previousPreference = $ErrorActionPreference
    $readerArgs = @('--cwd', $Project)
    if ($InvocationDirectory) { $readerArgs += "--invocation-cwd=$InvocationDirectory" }
    if ($RetainNativeSelector) { $readerArgs += '--retain-native-selector' }
    if ($RuntimeRoot) { $readerArgs += "--runtime-root=$RuntimeRoot" }
    if ($PSBoundParameters.ContainsKey('Context')) { $readerArgs += "--context=$Context" }
    $ErrorActionPreference = 'Continue'
    try {
        $arguments = @(
            '-I', '-B', '-X', 'utf8', $reader
        ) + $readerArgs
        if ($ReadOnlyProbe) {
            $arguments = @(
                '-I', '-B', '-X', 'utf8',
                (Join-Path $PSScriptRoot 'versioned_runtime_probe.py'),
                '--plugin', 'agent-vault', '--module', 'agent_vault', '--script', $reader
            )
            if (-not $Python) { $arguments += '--payload-only' }
            $arguments += @('--') + $readerArgs
        }
        $output = @(Invoke-Hidden -FilePath $pythonPath -ArgList $arguments)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    $result = ($output -join "`n") | ConvertFrom-Json -ErrorAction Stop
    if ($ReadOnlyProbe -and $code -eq 126 -and
        $result.schema -ceq 'copilot-extensions.probe-compatibility' -and
        $result.status -ceq 'unknown') {
        $contractError = [InvalidOperationException]::new('Vault probe import isolation is unqualified.')
        $contractError.Data['VaultProbeCompatibility'] = ($output -join "`n")
        throw $contractError
    }
    $reasonProperty = $result.PSObject.Properties['reason']
    if ($code -eq 3 -and $result.state -ceq 'failed' -and
        $reasonProperty -and $reasonProperty.Value -ceq 'repository-context-missing') {
        $contextError = [InvalidOperationException]::new(
            "Agent Vault repository context is missing. Run from an adopted repository or use '<repo> vault unlock' from any directory."
        )
        $contextError.Data['VaultRepositoryContextMissing'] = $true
        throw $contextError
    }
    if ($code -notin @(0, 2) -or $result.state -notin @('configured', 'configuration-empty')) {
        throw "Agent Vault activation configuration could not be resolved; correct its configuration, binding or payload. Reader exit: $code; state: $($result.state). $($result.detail)"
    }
    return $result
}
