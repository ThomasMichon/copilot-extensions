function Invoke-InstallerInstanceReconcile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$HelperRoot,
        [string]$RegistryRoot = ''
    )

    if (-not $RegistryRoot -and
        ($env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or $env:PYTEST_CURRENT_TEST)) {
        return
    }
    $savedExit = Get-Variable LASTEXITCODE -Scope Global -ValueOnly -ErrorAction SilentlyContinue
    try {
        $helper = Join-Path $HelperRoot 'daemon_instances.ps1'
        $entry = Get-Item -LiteralPath $helper -Force -ErrorAction Stop
        if ($entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Native instance reconciliation requires an ordinary helper file.'
        }
        . $helper
        $arguments = @{ Limit = 128; MaxSeconds = 5 }
        if ($RegistryRoot) { $arguments.RegistryRoot = $RegistryRoot }
        $null = Invoke-DaemonInstanceReconcile @arguments
    } catch {
        [Console]::Error.WriteLine(
            "[installer-instance-gc] deferred: native-reconcile-failed ($($_.Exception.GetType().Name))")
    } finally {
        $global:LASTEXITCODE = $savedExit
    }
}
