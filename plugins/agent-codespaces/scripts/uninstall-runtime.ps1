function Invoke-StatePreservingUninstall {
    param([switch]$Preview, [switch]$AdmissionOnly)
    $python = Get-BootstrapPython
    if (-not $python) { throw 'Uninstall requires an existing bootstrap Python; nothing removed' }
    if (Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq $OwnerTaskName }) {
        throw 'Uninstall refuses Connection Owner service custody; runtime and transports left intact'
    }
    $arguments = @('-I', '-B', (Join-Path $PSScriptRoot 'uninstall-state.py'), $InstallDir,
        '--shell', (Get-Process -Id $PID).Path)
    if ($Preview -or $AdmissionOnly) { $arguments += '--preview' }
    $nativeError = ''
    $output = Invoke-Hidden -FilePath $python -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) { throw "Uninstall admission refused: $output$nativeError" }
    if (-not $AdmissionOnly) { Write-Host $output }
}
