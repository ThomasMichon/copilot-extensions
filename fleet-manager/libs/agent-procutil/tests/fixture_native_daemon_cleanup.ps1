param(
    [ValidateSet('cleanup', 'mismatch', 'unconfirmed-launcher')][string]$Mode,
    [string]$Helper,
    [string]$Root
)
$ErrorActionPreference = 'Stop'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED = $null
. $Helper
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
public static class NativeAdmissionFixtureJob {
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);
    public static IntPtr Assign(Process process) {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        if (!AssignProcessToJobObject(job, process.Handle)) {
            int error = Marshal.GetLastWin32Error();
            CloseHandle(job);
            throw new Win32Exception(error);
        }
        return job;
    }
    public static void Close(IntPtr job) {
        if (job != IntPtr.Zero && !CloseHandle(job))
            throw new Exception("fixture Job handle close failed");
    }
}
'@
$script:worker = $null
$script:launcher = $null
$script:job = [IntPtr]::Zero
$script:readOriginal = (Get-Item function:Read-NativeDaemonFrame).ScriptBlock
function Read-NativeDaemonFrame {
    param([IO.Pipes.PipeStream]$Pipe, [int]$Length, [int]$Timeout = 15000)
    $frame = & $script:readOriginal -Pipe $Pipe -Length $Length -Timeout $Timeout
    if ($Length -ne 4) { throw 'unexpected fixture admission frame' }
    $actual = [CopilotExtensions.NativeDaemonAdmission]::PipeProcess($Pipe, $true)
    if ($actual -ne [BitConverter]::ToInt32($frame, 0)) { throw 'fixture peer identity mismatch' }
    $script:worker = [Diagnostics.Process]::GetProcessById($actual)
    [void]$script:worker.Handle
    if ($script:worker.HasExited) { throw 'fixture worker exited before its observation gate' }
    if ($Mode -eq 'mismatch') {
        $frame = [BitConverter]::GetBytes([int]$PID)
    } else {
        # The child has passed its own membership check. Changing membership
        # here deterministically exercises the parent's pre-ACK refusal.
        $script:job = [NativeAdmissionFixtureJob]::Assign($script:worker)
    }
    return ,$frame
}

Import-Module (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules\CimCmdlets\CimCmdlets.psd1')
$script:cim = Get-Command Invoke-CimMethod -CommandType Cmdlet
function Invoke-CimMethod {
    [CmdletBinding()]
    param([string]$ClassName, [string]$MethodName, [int]$OperationTimeoutSec, [hashtable]$Arguments)
    if ($Mode -eq 'unconfirmed-launcher') {
        $encoded = ($Arguments.CommandLine -split ' ')[-1]
        $wrapper = @"
`$s=New-Object Diagnostics.ProcessStartInfo
`$s.FileName=Join-Path `$PSHOME 'powershell.exe'
`$s.Arguments='-NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded'
`$s.UseShellExecute=`$false
`$s.CreateNoWindow=`$true
`$p=[Diagnostics.Process]::Start(`$s)
try { Start-Sleep -Seconds 15; `$p.WaitForExit() } finally { `$p.Dispose() }
"@
        $wrapperEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($wrapper))
        $conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
        $shell = Join-Path $PSHOME 'powershell.exe'
        $Arguments.CommandLine = "`"$conhost`" --headless `"$shell`" -NoLogo -NoProfile -NonInteractive -EncodedCommand $wrapperEncoded"
    }
    $result = & $script:cim @PSBoundParameters
    if ($result.ReturnValue -ne 0) { throw 'fixture WMI activation failed' }
    $script:launcher = [Diagnostics.Process]::GetProcessById([int]$result.ProcessId)
    [void]$script:launcher.Handle
    return $result
}

$entry = Join-Path $Root 'entry.ps1'
[IO.File]::WriteAllText($entry,
    "[IO.File]::WriteAllText((Join-Path `$PSScriptRoot 'started'),'unexpected entry point')")
if ($Mode -ne 'mismatch') {
    $literal = "'" + $Helper.Replace("'", "''") + "'"
    $shim = @"
. $literal
`$script:read = (Get-Item function:Read-NativeDaemonFrame).ScriptBlock
function Read-NativeDaemonFrame {
    param([IO.Pipes.PipeStream]`$Pipe, [int]`$Length, [int]`$Timeout = 15000)
    try { return ,(& `$script:read -Pipe `$Pipe -Length `$Length -Timeout `$Timeout) }
    catch { Start-Sleep -Seconds 6; throw }
}
"@
    $slow = Join-Path $Root 'slow-ack.ps1'
    [IO.File]::WriteAllText($slow, $shim, [Text.UTF8Encoding]::new($true))
    $script:NativeDaemonSource = $slow
}
try {
    $failure = $null
    try {
        $unexpected = Start-NativeDaemon -Script $entry -Arguments @('unused') `
            -WorkingDirectory $Root -LogPath (Join-Path $Root 'worker.log')
        $unexpected.Dispose()
    } catch {
        $failure = $_.Exception
        while ($failure.InnerException -and $failure -isnot [CopilotExtensions.NativeDaemonSpawnException]) {
            $failure = $failure.InnerException
        }
    }
    if ($null -eq $failure -or $failure -isnot [CopilotExtensions.NativeDaemonSpawnException]) {
        throw 'expected a typed native admission failure'
    }
    if ($null -eq $script:worker -or -not $script:worker.WaitForExit(10000)) {
        throw 'fixture worker did not retire'
    }
    @{
        settled = $failure.CleanupConfirmed
        workerCode = $script:worker.ExitCode
        error = $failure.InnerException.Message
    } | ConvertTo-Json -Compress
} finally {
    if ($script:worker) {
        try {
            if (-not $script:worker.HasExited) {
                $script:worker.Kill()
                [void]$script:worker.WaitForExit(5000)
            }
        } finally { $script:worker.Dispose() }
    }
    if ($script:launcher) {
        try {
            if (-not $script:launcher.WaitForExit(20000)) {
                $script:launcher.Kill()
                [void]$script:launcher.WaitForExit(5000)
                throw 'fixture launcher did not retire'
            }
        } finally { $script:launcher.Dispose() }
    }
    [NativeAdmissionFixtureJob]::Close($script:job)
}
