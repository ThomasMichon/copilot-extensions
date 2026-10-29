# Python-free counterpart of daemon.py's verified Windows outside-Job admission.
if ($PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion -lt [version]'5.1') {
    throw 'Native daemon admission requires stock Windows PowerShell 5.1.'
}
if (-not ('CopilotExtensions.NativeDaemonAdmission' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Collections;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace CopilotExtensions {
    public sealed class NativeDaemonSpawnException : Exception {
        public readonly bool CleanupConfirmed;
        public readonly NativeDaemonAdmission.Child RetainedChild;
        public NativeDaemonSpawnException(string message, Exception inner, bool settled)
            : this(message, inner, settled, null) { }
        public NativeDaemonSpawnException(string message, Exception inner, bool settled,
            NativeDaemonAdmission.Child retained)
            : base(message, inner) { CleanupConfirmed = settled; RetainedChild = retained; }
    }
    public static class NativeDaemonAdmission {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct Startup {
            public uint Size;
            public string Reserved, Desktop, Title;
            public uint X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
            public ushort Show, ReservedSize;
            public IntPtr ReservedData, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct StartupEx { public Startup Info; public IntPtr Attributes; }
        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInfo { public IntPtr Process, Thread; public uint Pid, Tid; }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcess(
            string application, StringBuilder command, IntPtr processAttributes,
            IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string cwd,
            ref StartupEx startup, out ProcessInfo process);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr OpenJobObject(uint access, bool inherit, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetHandleInformation(SafeFileHandle handle, uint mask, uint flags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool InitializeProcThreadAttributeList(
            IntPtr list, int count, uint flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool UpdateProcThreadAttribute(
            IntPtr list, uint flags, IntPtr key, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")]
        private static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetNamedPipeClientProcessId(SafePipeHandle pipe, out uint result);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe, out uint result);

        private static void Check(bool success) {
            if (!success) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        private static void Stage(FileStream output, string stage) {
            byte[] data = Encoding.UTF8.GetBytes("native-daemon-stage:" + stage + "\n");
            output.Write(data, 0, data.Length);
            output.Flush();
        }
        public sealed class Child : IDisposable {
            private IntPtr handle;
            public readonly uint Pid;
            internal Child(IntPtr process, uint pid) { handle = process; Pid = pid; }
            public bool Stop() {
                if (WaitForSingleObject(handle, 0) == 0) return true;
                Check(TerminateProcess(handle, 125));
                return WaitForSingleObject(handle, 10000) == 0;
            }
            public void Dispose() {
                if (handle != IntPtr.Zero) {
                    if (!CloseHandle(handle))
                        throw new NativeDaemonSpawnException("native daemon custody handle close failed",
                            new Win32Exception(Marshal.GetLastWin32Error()), false, this);
                    handle = IntPtr.Zero;
                }
            }
        }
        public static Child Start(string executable, string arguments, string cwd, string log, bool contained) {
            const string key = "COPILOT_EXTENSIONS_OWNED_JOB_SCOPES";
            var environment = new SortedDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (DictionaryEntry item in Environment.GetEnvironmentVariables())
                environment[(string)item.Key] = (string)item.Value;
            var names = new HashSet<string>(StringComparer.Ordinal);
            string scopes;
            if (environment.TryGetValue(key, out scopes)) {
                if (String.IsNullOrEmpty(scopes) || scopes.Length > 4096)
                    throw new NativeDaemonSpawnException("invalid owned Job scopes", null, true);
                foreach (string name in scopes.Split(';')) {
                    if (!Regex.IsMatch(name, @"\ALocal\\ce-owned-job-[0-9a-f]{32}\z") || !names.Add(name))
                        throw new NativeDaemonSpawnException("invalid owned Job scopes", null, true);
                }
                if (names.Count > 64)
                    throw new NativeDaemonSpawnException("owned Job scope limit exceeded", null, true);
            }
            if (!contained) environment.Remove(key);
            var block = new StringBuilder();
            foreach (var item in environment) block.Append(item.Key).Append('=').Append(item.Value).Append('\0');
            block.Append('\0');
            IntPtr env = IntPtr.Zero, attributes = IntPtr.Zero, handles = IntPtr.Zero;
            var queries = new List<IntPtr>();
            bool initialized = false;
            var child = new ProcessInfo();
            NativeDaemonSpawnException failure = null;
            try {
                // Query-only, noninheritable handles never become continuing Job owners.
                if (!contained && scopes != null) {
                    foreach (string name in scopes.Split(';')) {
                        IntPtr query = OpenJobObject(4, false, name);
                        Check(query != IntPtr.Zero);
                        queries.Add(query);
                    }
                }
                env = Marshal.StringToHGlobalUni(block.ToString());
                using (var output = new FileStream(log, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
                using (var input = CreateFile("NUL", 0x80000000, 3, IntPtr.Zero, 3, 0x80, IntPtr.Zero)) {
                    Stage(output, "prepared");
                    Check(!input.IsInvalid);
                    Check(SetHandleInformation(output.SafeFileHandle, 1, 1));
                    Check(SetHandleInformation(input, 1, 1));
                    IntPtr size = IntPtr.Zero;
                    InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
                    if (size == IntPtr.Zero) throw new IOException("cannot size daemon handle list");
                    attributes = Marshal.AllocHGlobal(size);
                    Check(InitializeProcThreadAttributeList(attributes, 1, 0, ref size));
                    initialized = true;
                    handles = Marshal.AllocHGlobal(IntPtr.Size * 2);
                    Marshal.WriteIntPtr(handles, output.SafeFileHandle.DangerousGetHandle());
                    Marshal.WriteIntPtr(handles, IntPtr.Size, input.DangerousGetHandle());
                    Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x20002), handles,
                        new IntPtr(IntPtr.Size * 2), IntPtr.Zero, IntPtr.Zero));
                    var startup = new StartupEx();
                    startup.Info.Size = (uint)Marshal.SizeOf(typeof(StartupEx));
                    startup.Info.Flags = 0x100;
                    startup.Info.Input = input.DangerousGetHandle();
                    startup.Info.Output = startup.Info.Error = output.SafeFileHandle.DangerousGetHandle();
                    startup.Attributes = attributes;
                    var command = new StringBuilder("\"" + executable + "\" " + arguments);
                    uint flags = 0x08080404; // NO_WINDOW | EXTENDED_STARTUPINFO | UNICODE_ENVIRONMENT | SUSPENDED.
                    if (!contained) flags |= 0x01000000; // BREAKAWAY_FROM_JOB; denial has no fallback.
                    Stage(output, "creating");
                    Check(CreateProcess(executable, command, IntPtr.Zero, IntPtr.Zero, true,
                        flags, env, cwd, ref startup, out child));
                    Stage(output, "created-suspended");
                    foreach (IntPtr query in queries) {
                        bool member;
                        Check(IsProcessInJob(child.Process, query, out member));
                        if (member) throw new IOException("native daemon remained inside an owned Job");
                    }
                    // Closing these before resume prevents even a query handle extending caller custody.
                    for (int i = 0; i < queries.Count; i++) {
                        Check(CloseHandle(queries[i]));
                        queries[i] = IntPtr.Zero;
                    }
                    Check(ResumeThread(child.Thread) != UInt32.MaxValue);
                    Stage(output, "resumed");
                    Check(CloseHandle(child.Thread));
                    child.Thread = IntPtr.Zero;
                }
                var result = new Child(child.Process, child.Pid);
                child.Process = IntPtr.Zero;
                return result;
            } catch (Exception error) {
                bool settled = child.Process == IntPtr.Zero;
                try {
                    if (!settled) {
                        if (WaitForSingleObject(child.Process, 0) != 0)
                            Check(TerminateProcess(child.Process, 125));
                        settled = WaitForSingleObject(child.Process, 10000) == 0;
                    }
                } catch (Exception cleanup) {
                    error = new AggregateException(error, cleanup);
                    settled = false;
                }
                Child retained = null;
                if (!settled && child.Process != IntPtr.Zero) {
                    retained = new Child(child.Process, child.Pid);
                    child.Process = IntPtr.Zero;
                }
                failure = new NativeDaemonSpawnException("native daemon direct launch refused", error, settled, retained);
                throw failure;
            } finally {
                bool closed = true;
                foreach (IntPtr query in queries)
                    if (query != IntPtr.Zero && !CloseHandle(query)) closed = false;
                if (child.Thread != IntPtr.Zero && !CloseHandle(child.Thread)) closed = false;
                if (child.Process != IntPtr.Zero && !CloseHandle(child.Process)) closed = false;
                if (initialized) DeleteProcThreadAttributeList(attributes);
                if (attributes != IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                if (handles != IntPtr.Zero) Marshal.FreeHGlobal(handles);
                if (env != IntPtr.Zero) Marshal.FreeHGlobal(env);
                if (!closed) throw new NativeDaemonSpawnException("native daemon handle close failed", failure, false,
                    failure == null ? null : failure.RetainedChild);
            }
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
    [Console]::Error.WriteLine('native-daemon-stage:bootstrap')
    . `$d.helper
    [Console]::Error.WriteLine('native-daemon-stage:peer-ready')
    `$pipe = New-Object IO.Pipes.NamedPipeClientStream(
        '.', `$d.channel, [IO.Pipes.PipeDirection]::InOut, [IO.Pipes.PipeOptions]::Asynchronous
    )
    try {
        `$pipe.Connect(15000)
        [Console]::Error.WriteLine('native-daemon-stage:connected')
        if ([CopilotExtensions.NativeDaemonAdmission]::PipeProcess(`$pipe, `$false) -ne `$d.parent) {
            throw 'Native daemon admission server identity changed.'
        }
        `$frame = [BitConverter]::GetBytes([int]`$PID)
        `$pipe.Write(`$frame, 0, 4)
        [Console]::Error.WriteLine('native-daemon-stage:offered')
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
    [Console]::Error.WriteLine((`$_ | Out-String))
    exit 1
}
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
    $commandArgs = "-NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"
    $launcher = $null
    $process = $null
    $connected = $null
    $adopted = $false
    try {
        $connected = $server.BeginWaitForConnection($null, $null)
        $launcher = [CopilotExtensions.NativeDaemonAdmission]::Start(
            $shell, $commandArgs, $WorkingDirectory, $LogPath, $contained
        )
        if (-not $connected.AsyncWaitHandle.WaitOne(15000)) { throw 'Native daemon did not reach admission.' }
        $server.EndWaitForConnection($connected)
        $frame = Read-NativeDaemonFrame -Pipe $server -Length 4
        $actualPid = [CopilotExtensions.NativeDaemonAdmission]::PipeProcess($server, $true)
        if ($actualPid -ne [BitConverter]::ToInt32($frame, 0) -or $actualPid -ne $launcher.Pid) {
            throw 'Native daemon client identity changed.'
        }
        $process = [Diagnostics.Process]::GetProcessById($actualPid)
        [void]$process.Handle
        # Once any acknowledgement may have reached the child, a lost return
        # is ambiguous. Its caller must consult the durable operation outcome.
        $null = Set-DaemonInstanceProcess $receipt $process
        $adopted = $true
        $server.WriteByte(1)
        $server.Flush()
        return $process
    } catch {
        $failure = $_.Exception
        $settled = $false
        $retained = $null
        if (-not $launcher) {
            $settled = $true
            $cause = $failure
            while ($cause -and $cause -isnot [CopilotExtensions.NativeDaemonSpawnException]) {
                $cause = $cause.InnerException
            }
            if ($cause) {
                $settled = $cause.CleanupConfirmed
                $retained = $cause.RetainedChild
            }
        }
        $server.Dispose()
        if (-not $adopted -and $launcher) {
            try {
                $settled = $launcher.Stop()
            } catch {
                $failure = [AggregateException]::new(
                    'native daemon cleanup failed', [Exception[]]@($failure, $_.Exception)
                )
                $settled = $false
            }
        }
        if (-not $settled -and $launcher) {
            $retained = $launcher
            $launcher = $null
        }
        if ($process) { $process.Dispose() }
        throw (New-Object CopilotExtensions.NativeDaemonSpawnException -ArgumentList `
            'Native daemon admission failed; no foreground fallback is permitted.', $failure, $settled, $retained)
    } finally {
        $server.Dispose()
        if ($connected) { $connected.AsyncWaitHandle.Dispose() }
        if ($launcher) { $launcher.Dispose() }
    }
}
