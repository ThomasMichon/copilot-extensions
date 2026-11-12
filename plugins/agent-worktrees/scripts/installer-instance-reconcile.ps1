function Invoke-InstallerInstanceReconcile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$HelperRoot,
        [string]$RegistryRoot = '',
        [string]$RuntimeRoot = ''
    )

    if (-not $RegistryRoot -and
        ($env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or $env:PYTEST_CURRENT_TEST)) {
        return
    }
    $savedExit = Get-Variable LASTEXITCODE -Scope Global -ValueOnly -ErrorAction SilentlyContinue
    try {
        if (-not $RuntimeRoot) { throw 'Addressed runtime root is required for installer reconciliation.' }
        $helper = Join-Path $HelperRoot 'daemon_instances.ps1'
        $entry = Get-Item -LiteralPath $helper -Force -ErrorAction Stop
        if ($entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Native instance reconciliation requires an ordinary helper file.'
        }
        . $helper
        $parameters = (Get-Command Invoke-DaemonInstanceReconcile -CommandType Function).Parameters
        if (-not $parameters.ContainsKey('Terminate') -or -not $parameters.ContainsKey('RuntimeRoot')) {
            throw 'Native reconciliation helper lacks scoped termination-policy support.'
        }
        $arguments = @{ Limit = 128; MaxSeconds = 5; Terminate = $false; RuntimeRoot = $RuntimeRoot }
        if ($RegistryRoot) { $arguments.RegistryRoot = $RegistryRoot }
        $null = Invoke-DaemonInstanceReconcile @arguments
    } catch {
        [Console]::Error.WriteLine(
            "[installer-instance-gc] deferred: native-reconcile-failed ($($_.Exception.GetType().Name))")
    } finally {
        $global:LASTEXITCODE = $savedExit
    }
}
