# Python-free counterpart of daemon.py's verified Windows outside-Job admission.
if ($PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion -lt [version]'5.1') {
    throw 'Native daemon admission requires stock Windows PowerShell 5.1.'
}
if (-not ('CopilotExtensions.NativeDaemonAdmission' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace CopilotExtensions {
    public sealed class NativeDaemonSpawnException : Exception {
        public readonly bool CleanupConfirmed;
        public NativeDaemonSpawnException(string message, Exception inner, bool settled)
            : base(message, inner) { CleanupConfirmed = settled; }
    }
    public static class NativeDaemonAdmission {
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetNamedPipeClientProcessId(SafePipeHandle pipe, out uint result);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe, out uint result);

        public static bool InJob(Process process) {
            bool result;
            if (!IsProcessInJob(process.Handle, IntPtr.Zero, out result))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            return result;
        }

        public static uint PipeProcess(PipeStream pipe, bool client) {
            uint result;
            bool success = client ?
                GetNamedPipeClientProcessId(pipe.SafePipeHandle, out result) :
                GetNamedPipeServerProcessId(pipe.SafePipeHandle, out result);
            if (!success) throw new Win32Exception(Marshal.GetLastWin32Error());
            return result;
        }
    }
}
'@
}

$script:NativeDaemonSource = $PSCommandPath

function Read-NativeDaemonFrame {
    param([IO.Pipes.PipeStream]$Pipe, [int]$Length, [int]$Timeout = 15000)
    $buffer = New-Object byte[] $Length
    $offset = 0
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    while ($offset -lt $Length) {
        $remaining = $Timeout - [int]$deadline.ElapsedMilliseconds
        if ($remaining -le 0) { throw 'Native daemon admission channel timed out.' }
        $read = $Pipe.ReadAsync($buffer, $offset, $Length - $offset)
        if (-not $read.Wait($remaining)) { throw 'Native daemon admission channel timed out.' }
        if ($read.Result -eq 0) { throw 'Native daemon admission channel closed.' }
        $offset += $read.Result
    }
    return ,$buffer
}

function Start-NativeDaemon {
    param(
        [Parameter(Mandatory)][string]$Script,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$LogPath,
        [string]$RegistryRoot = $env:COPILOT_EXTENSIONS_DAEMON_REGISTRY
    )
    if ($env:OS -ne 'Windows_NT') { throw 'Native PowerShell daemon admission requires Windows.' }
    foreach ($path in @($Script, $WorkingDirectory, $script:NativeDaemonSource)) {
        if (-not [IO.Path]::IsPathRooted($path) -or [IO.Path]::GetFullPath($path) -ne $path) {
            throw 'Native daemon paths must be fully qualified and normalized.'
        }
        if (-not (Test-Path -LiteralPath $path)) {
            throw 'Native daemon admission requires existing paths.'
        }
    }
    if (-not [IO.Path]::IsPathRooted($LogPath) -or [IO.Path]::GetFullPath($LogPath) -ne $LogPath) {
        throw 'Native daemon diagnostic paths must be fully qualified and normalized.'
    }
    if (-not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($LogPath))) {
        throw 'Native daemon diagnostics require a file in an existing directory.'
    }
    $shell = Join-Path $PSHOME 'powershell.exe'
    if (-not [IO.File]::Exists($shell)) { throw 'Native daemon admission cannot resolve its interpreter.' }
    $contained = $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1'
    $instances = Join-Path ([IO.Path]::GetDirectoryName($script:NativeDaemonSource)) 'daemon_instances.ps1'
    . $instances
    $receipt = New-DaemonInstanceReservation -Service ([IO.Path]::GetFileNameWithoutExtension($Script)) `
        -RuntimeRoot $WorkingDirectory -Source $Script -RegistryRoot $RegistryRoot
    $Script = $receipt.source
    $channel = 'ce-native-daemon-' + [Guid]::NewGuid().ToString('N')
    $security = New-Object IO.Pipes.PipeSecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.AddAccessRule((New-Object IO.Pipes.PipeAccessRule(
        [Security.Principal.WindowsIdentity]::GetCurrent().User,
        [IO.Pipes.PipeAccessRights]::FullControl, [Security.AccessControl.AccessControlType]::Allow
    )))
    $server = New-Object IO.Pipes.NamedPipeServerStream(
        $channel, [IO.Pipes.PipeDirection]::InOut, 1,
        [IO.Pipes.PipeTransmissionMode]::Byte, [IO.Pipes.PipeOptions]::Asynchronous,
        4096, 4096, $security
    )
    $description = @{
        script = $Script
        arguments = @($Arguments)
        helper = $script:NativeDaemonSource
        contained = $contained
        log = $LogPath
        channel = $channel
        parent = $PID
        instance = $receipt.instance_id
        registry = $receipt.registry_root
        instances = $instances
        working = $WorkingDirectory
    } | ConvertTo-Json -Compress
    $data = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($description))
    $body = @"
`$ErrorActionPreference = 'Stop'
`$d = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$data')) | ConvertFrom-Json
try {
    . `$d.helper
    if (-not `$d.contained -and [CopilotExtensions.NativeDaemonAdmission]::InJob([Diagnostics.Process]::GetCurrentProcess())) {
        throw 'Native daemon remained inside a caller Job; execution refused.'
    }
    `$pipe = New-Object IO.Pipes.NamedPipeClientStream(
        '.', `$d.channel, [IO.Pipes.PipeDirection]::InOut, [IO.Pipes.PipeOptions]::Asynchronous
    )
    try {
        `$pipe.Connect(15000)
        if ([CopilotExtensions.NativeDaemonAdmission]::PipeProcess(`$pipe, `$false) -ne `$d.parent) {
            throw 'Native daemon admission server identity changed.'
        }
        `$frame = [BitConverter]::GetBytes([int]`$PID)
        `$pipe.Write(`$frame, 0, 4)
        `$ack = Read-NativeDaemonFrame -Pipe `$pipe -Length 1
        if (`$ack[0] -ne 1) { throw 'Native daemon adoption was refused.' }
    } finally { `$pipe.Dispose() }
    . `$d.instances
    `$env:COPILOT_EXTENSIONS_DAEMON_INSTANCE = `$d.instance
    `$env:COPILOT_EXTENSIONS_DAEMON_REGISTRY = `$d.registry
    `$instanceGuard = Enter-DaemonInstance -Service ([IO.Path]::GetFileNameWithoutExtension(`$d.script)) ``
        -RuntimeRoot `$d.working -Source `$d.script
    `$forward = [string[]]`$d.arguments
    & `$d.script @forward
    if (`$null -ne `$LASTEXITCODE) { exit `$LASTEXITCODE }
} catch {
    [IO.File]::AppendAllText(`$d.log, (`$_ | Out-String))
    exit 1
}
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
    $commandArgs = "-NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"
    $launcher = $null
    $process = $null
    $connected = $null
    $activated = $false
    $adopted = $false
    $pinned = $false
    try {
        $connected = $server.BeginWaitForConnection($null, $null)
        if ($contained) {
            $start = New-Object Diagnostics.ProcessStartInfo
            $start.FileName = $shell
            $start.Arguments = $commandArgs
            $start.WorkingDirectory = $WorkingDirectory
            $start.UseShellExecute = $false
            $start.CreateNoWindow = $true
            $activated = $true
            $launcher = [Diagnostics.Process]::Start($start)
        } else {
            $cimModule = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules\CimCmdlets\CimCmdlets.psd1'
            Import-Module $cimModule -ErrorAction Stop
            $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -ErrorAction Stop -Property @{
                ShowWindow = [uint16]0
                # WMI rejects CREATE_NO_WINDOW; conhost supplies a headless console.
                CreateFlags = [uint32]0x00000008
                EnvironmentVariables = [string[]]@(
                    [Environment]::GetEnvironmentVariables().GetEnumerator() |
                        ForEach-Object { "$($_.Key)=$($_.Value)" }
                )
            }
            $conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
            # A lost response can follow actual creation. Only a positive
            # refusal, or observed child settlement, clears this uncertainty.
            $activated = $true
            $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -OperationTimeoutSec 15 -ErrorAction Stop -Arguments @{
                CommandLine = "`"$conhost`" --headless `"$shell`" $commandArgs"
                CurrentDirectory = $WorkingDirectory
                ProcessStartupInformation = $startup
            }
            if ($null -eq $result -or
                ($result.ReturnValue -isnot [int] -and $result.ReturnValue -isnot [uint32])) {
                throw 'Native daemon broker returned no valid activation outcome.'
            }
            if ($result.ReturnValue -ne 0) {
                $activated = $false
                throw "Native daemon broker refused launch ($($result.ReturnValue))."
            }
            $launcher = [Diagnostics.Process]::GetProcessById([int]$result.ProcessId)
        }
        [void]$launcher.Handle
        if (-not $connected.AsyncWaitHandle.WaitOne(15000)) { throw 'Native daemon did not reach admission.' }
        $server.EndWaitForConnection($connected)
        $frame = Read-NativeDaemonFrame -Pipe $server -Length 4
        $actualPid = [CopilotExtensions.NativeDaemonAdmission]::PipeProcess($server, $true)
        if ($actualPid -ne [BitConverter]::ToInt32($frame, 0)) { throw 'Native daemon client identity changed.' }
        $process = [Diagnostics.Process]::GetProcessById($actualPid)
        [void]$process.Handle
        $pinned = $true
        if (-not $contained -and [CopilotExtensions.NativeDaemonAdmission]::InJob($process)) {
            throw 'Native daemon broker returned a process inside a Job.'
        }
        # Once any acknowledgement may have reached the child, a lost return
        # is ambiguous. Its caller must consult the durable operation outcome.
        $null = Set-DaemonInstanceProcess $receipt $process
        $adopted = $true
        $server.WriteByte(1)
        $server.Flush()
        return $process
    } catch {
        $failure = $_.Exception
        $settled = -not $activated
        $server.Dispose()
        if (-not $adopted -and $pinned) {
            try {
                if (-not $process.WaitForExit(2000)) { $process.Kill() }
                $settled = $process.WaitForExit(5000) -and $launcher.WaitForExit(5000)
            } catch {
                $failure = [AggregateException]::new(
                    'native daemon cleanup failed', [Exception[]]@($failure, $_.Exception)
                )
                $settled = $false
            }
        }
        if ($process) { $process.Dispose() }
        throw (New-Object CopilotExtensions.NativeDaemonSpawnException -ArgumentList `
            'Native daemon admission failed; no foreground fallback is permitted.', $failure, $settled)
    } finally {
        $server.Dispose()
        if ($connected) { $connected.AsyncWaitHandle.Dispose() }
        if ($launcher) { $launcher.Dispose() }
    }
}
