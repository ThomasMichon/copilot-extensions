#Requires -Version 7.0
# Dot-sourced by an existing creation/wait owner; never a separate launcher.

function New-AwNativeSeedSuspendedProcess {
    param(
        [Parameter(Mandatory)][Diagnostics.ProcessStartInfo]$StartInfo,
        [IntPtr[]]$InheritedHandles = @()
    )
    if (-not $IsWindows) { throw 'Native suspended creation requires Windows.' }
    if (-not ('AwNativeSeed.SuspendedProcess' -as [type])) {
        Add-Type -TypeDefinition ([IO.File]::ReadAllText(
            (Join-Path $PSScriptRoot 'native-seed-process.cs')
        )) -ErrorAction Stop
    }
    return [AwNativeSeed.SuspendedProcess]::Create($StartInfo, $InheritedHandles)
}
