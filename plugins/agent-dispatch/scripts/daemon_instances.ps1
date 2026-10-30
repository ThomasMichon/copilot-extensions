# Standalone Python-free binding of agent_procutil.daemon_instances schema 1.
# Import only defines helpers; mutation occurs at explicit admission/reconcile.

function Initialize-DaemonInstanceNative {
    if ('CopilotExtensions.DaemonInstanceNative' -as [type]) { return }
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Diagnostics;
using System.Threading;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace CopilotExtensions {
    public static class DaemonInstanceNative {
        [StructLayout(LayoutKind.Sequential, Pack=4)] private struct FileInfo {
            public uint Attributes; public long Created, Accessed, Written;
            public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        [DllImport("kernel32.dll", SetLastError=true)]
        private static extern bool GetProcessTimes(IntPtr p, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError=true)]
        private static extern bool GetFileInformationByHandle(SafeFileHandle f, out FileInfo info);
        [StructLayout(LayoutKind.Sequential)] private struct FileId {
            public ulong Volume, Low, High;
        }
        [DllImport("kernel32.dll", SetLastError=true)]
        private static extern bool GetFileInformationByHandleEx(SafeFileHandle f, int kind, out FileId info, uint size);
        [DllImport("kernel32.dll", SetLastError=true)]
        private static extern bool SetFileInformationByHandle(SafeFileHandle f, int kind, ref int info, uint size);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern SafeFileHandle CreateFile(string p, uint a, uint s, IntPtr sec, uint c, uint flags, IntPtr t);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern bool MoveFileEx(string from, string to, uint flags);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder path, uint length, uint flags);
        [DllImport("kernel32.dll")] private static extern bool TerminateProcess(IntPtr p, uint code);
        [StructLayout(LayoutKind.Sequential)] private struct BasicLimit {
            public long ProcessTime, JobTime; public uint Flags;
            public UIntPtr Min, Max; public uint Active; public UIntPtr Affinity;
            public uint Priority, Scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] private struct Io {
            public ulong Read, Write, Other, ReadBytes, WriteBytes, OtherBytes;
        }
        [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimit {
            public BasicLimit Basic; public Io Counters;
            public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern IntPtr CreateJobObject(IntPtr a, string n);
        [DllImport("kernel32.dll", SetLastError=true)]
        private static extern bool SetInformationJobObject(IntPtr j, int c, ref ExtendedLimit i, uint s);
        [DllImport("kernel32.dll", SetLastError=true)]
        private static extern bool AssignProcessToJobObject(IntPtr j, IntPtr p);
        [DllImport("kernel32.dll")] private static extern bool TerminateJobObject(IntPtr j, uint code);
        [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr h);
        private static IntPtr Lifetime = IntPtr.Zero;
        private static void Retire() {
            if (Lifetime != IntPtr.Zero) TerminateJobObject(Lifetime, 1);
            TerminateProcess(Process.GetCurrentProcess().Handle, 1);
        }
        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Timer> Timers = new Dictionary<string, Timer>();
        private static string Current;
        private static int References;
        public static string AcquireCurrent() {
            lock (Gate) { if (Current != null) References++; return Current; }
        }
        public static void PublishCurrent(string path) {
            lock (Gate) {
                if (Current != null) throw new IOException("daemon process admission already owned");
                Current=path; References=1;
            }
        }
        public static bool ReleaseCurrent(string path) {
            lock (Gate) {
                if (Current != path || References <= 0) throw new IOException("daemon admission custody changed");
                if (--References != 0) return false;
                Current=null; return true;
            }
        }
        public static string Birth(Process process) {
            long created, exited, kernel, user;
            if (!GetProcessTimes(process.Handle, out created, out exited, out kernel, out user))
                throw new IOException("daemon process birth unavailable");
            return "windows:" + created.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }
        public static string[] FileIdentity(string path) {
            using (var handle = CreateFile(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
                return Identity(handle);
            }
        }
        public static string FinalPath(string path) {
            using (var handle=CreateFile(path,0,7,IntPtr.Zero,3,0x02000000,IntPtr.Zero)) {
                var result=new StringBuilder(32768);
                uint length=GetFinalPathNameByHandle(handle,result,(uint)result.Capacity,0);
                if (handle.IsInvalid || length==0 || length>=result.Capacity)
                    throw new IOException("daemon source artifact unavailable");
                string value=result.ToString();
                if (value.StartsWith(@"\\?\UNC\")) return @"\\"+value.Substring(8);
                return value.StartsWith(@"\\?\")?value.Substring(4):value;
            }
        }
        private static string[] Identity(SafeFileHandle handle) {
                FileId info;
                if (handle.IsInvalid || !GetFileInformationByHandleEx(handle, 18, out info, 24))
                    throw new IOException("daemon path identity unavailable");
                string number = info.High.ToString();
                for (int bit=63; bit>=0; bit--) {
                    int carry = (int)((info.Low >> bit) & 1);
                    var digits = number.ToCharArray();
                    for (int at=digits.Length-1; at>=0; at--) {
                        int value = (digits[at]-'0')*2+carry;
                        digits[at]=(char)('0'+value%10); carry=value/10;
                    }
                    number=(carry==0 ? "" : carry.ToString())+new string(digits);
                }
                return new string[] { info.Volume.ToString(), number };
        }
        public static void DeleteOwned(string path, string device, string inode) {
            using (var handle = CreateFile(path, 0x10080, 3, IntPtr.Zero, 3, 0x00200000, IntPtr.Zero)) {
                string[] actual=Identity(handle);
                FileInfo attributes;
                if (!GetFileInformationByHandle(handle, out attributes) ||
                    (attributes.Attributes & (0x400|0x10)) != 0 ||
                    actual[0] != device || actual[1] != inode)
                    throw new IOException("daemon artifact custody changed");
                int disposition=1;
                if (!SetFileInformationByHandle(handle, 4, ref disposition, 4))
                    throw new IOException("daemon artifact removal unconfirmed");
            }
        }
        public static void Replace(string from, string to) {
            if (!MoveFileEx(from, to, 9)) throw new IOException("daemon record publication failed");
        }
        public static void Watch(string id, string path, string birth, double hardRemaining) {
            lock (Gate) {
                if (Timers.ContainsKey(id)) return;
                if (Lifetime == IntPtr.Zero) {
                    const string key = "COPILOT_EXTENSIONS_OWNED_JOB_SCOPES";
                    string inherited = null;
                    foreach (System.Collections.DictionaryEntry item in Environment.GetEnvironmentVariables())
                        if (String.Equals((string)item.Key, key, StringComparison.OrdinalIgnoreCase))
                            inherited = (string)item.Value;
                    var names = new HashSet<string>(StringComparer.Ordinal);
                    if (inherited != null) {
                        if (inherited.Length == 0 || inherited.Length > 4096)
                            throw new IOException("invalid owned Job scopes");
                        foreach (string name in inherited.Split(';')) {
                            if (!Regex.IsMatch(name, @"\ALocal\\ce-owned-job-[0-9a-f]{32}\z") || !names.Add(name))
                                throw new IOException("invalid owned Job scopes");
                        }
                    }
                    string scope = @"Local\ce-owned-job-" + Guid.NewGuid().ToString("N");
                    string chain = inherited == null ? scope : inherited + ";" + scope;
                    if (names.Count >= 64 || chain.Length > 4096)
                        throw new IOException("owned Job scope limit exceeded");
                    IntPtr job = CreateJobObject(IntPtr.Zero, scope);
                    int jobError = Marshal.GetLastWin32Error();
                    if (job == IntPtr.Zero || jobError == 183) {
                        if (job != IntPtr.Zero) CloseHandle(job);
                        throw new IOException("disposable daemon scope creation refused");
                    }
                    var limits = new ExtendedLimit(); limits.Basic.Flags = 0x2000;
                    if (!SetInformationJobObject(job, 9, ref limits,
                        (uint)Marshal.SizeOf(typeof(ExtendedLimit))) ||
                        !AssignProcessToJobObject(job, Process.GetCurrentProcess().Handle)) {
                        if (job != IntPtr.Zero) CloseHandle(job);
                        throw new IOException("disposable daemon descendant custody unavailable");
                    }
                    Lifetime = job;
                    Environment.SetEnvironmentVariable(key, chain);
                }
                var clock = Stopwatch.StartNew();
                double hard = Math.Max(0, hardRemaining);
                Timer timer = null;
                timer = new Timer(delegate(object unused) {
                    try {
                        if (new System.IO.FileInfo(path).Length > 65536) throw new IOException();
                        var fields = new Dictionary<string, string>();
                        foreach (string line in File.ReadAllLines(path, Encoding.UTF8)) {
                            string[] parts = line.Split('\t');
                            if (parts.Length != 2 || fields.ContainsKey(parts[0])) throw new IOException();
                            fields.Add(parts[0], Encoding.UTF8.GetString(Convert.FromBase64String(parts[1])));
                        }
                        double deadline = double.Parse(fields["deadline"], System.Globalization.CultureInfo.InvariantCulture);
                        double now = (DateTime.UtcNow - new DateTime(1970,1,1)).TotalSeconds;
                        if (fields["instance_id"] != id || fields["birth"] != birth ||
                            clock.Elapsed.TotalSeconds >= hard || now >= deadline)
                            Retire();
                    } catch { Retire(); }
                }, null, 100, 100);
                Timers.Add(id, timer);
            }
        }
        public static void Unwatch(string id) {
            lock (Gate) {
                Timer timer;
                if (Timers.TryGetValue(id, out timer)) { timer.Dispose(); Timers.Remove(id); }
            }
        }
    }
}
'@
}

function Get-DaemonInstanceRoot {
    param([string]$RegistryRoot)
    if ($RegistryRoot) { return $RegistryRoot }
    return (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'copilot-extensions\daemon-instances')
}

function Assert-DaemonInstancePath {
    param([string]$Path, [switch]$Directory, [switch]$Owned)
    if (-not [IO.Path]::IsPathRooted($Path) -or [IO.Path]::GetFullPath($Path) -cne $Path) {
        throw 'Daemon instance path must be absolute and normalized.'
    }
    $current = $Path
    while ($current) {
        if ([IO.File]::Exists($current) -or [IO.Directory]::Exists($current)) {
            if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Daemon instance path is redirected.'
            }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    if ($Directory -and -not [IO.Directory]::Exists($Path)) { throw 'Daemon instance directory missing.' }
    if ($Owned -and ([IO.File]::Exists($Path) -or [IO.Directory]::Exists($Path))) {
        if ([IO.Directory]::Exists($Path)) {
            $security=[IO.Directory]::GetAccessControl($Path,[Security.AccessControl.AccessControlSections]::Owner)
        } else {
            $security=[IO.File]::GetAccessControl($Path,[Security.AccessControl.AccessControlSections]::Owner)
        }
        $owner=$security.GetOwner([Security.Principal.SecurityIdentifier])
        $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
        try {
            if (-not ($owner.Equals($identity.User) -or $owner.Equals($identity.Owner))) {
                throw 'Daemon instance path has a foreign owner.'
            }
        } finally { $identity.Dispose() }
    }
}

function Read-DaemonInstanceRecord {
    param([string]$Path)
    Assert-DaemonInstancePath $Path -Owned
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -gt 65536) { throw 'Daemon instance record exceeds its bound.' }
    $record = @{}
    foreach ($line in ([Text.Encoding]::UTF8.GetString($bytes) -split "`n")) {
        if (-not $line) { continue }
        $parts = $line.Split("`t")
        if ($parts.Length -ne 2 -or $parts[0] -cnotmatch '^[a-z][a-z0-9_]{0,63}$' -or $record.ContainsKey($parts[0])) {
            throw 'Invalid daemon instance field.'
        }
        $record[$parts[0]] = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($parts[1]))
    }
    if ($record.schema -ne '1' -or $record.instance_id -cne [IO.Path]::GetFileNameWithoutExtension($Path)) {
        throw 'Invalid daemon instance identity.'
    }
    return $record
}

function Write-DaemonInstanceRecord {
    param([string]$Path, [hashtable]$Record)
    Assert-DaemonInstancePath $Path -Owned
    $builder = New-Object Text.StringBuilder
    foreach ($key in ($Record.Keys | Sort-Object)) {
        [void]$builder.Append($key).Append("`t").Append(
            [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$Record[$key]))
        ).Append("`n")
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($builder.ToString())
    if ($bytes.Length -gt 65536) { throw 'Daemon instance record exceeds its bound.' }
    $staging = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.pending'
    try {
        $stream = [IO.File]::Open($staging, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
        [CopilotExtensions.DaemonInstanceNative]::Replace($staging, $Path)
    } finally {
        if ([IO.File]::Exists($staging)) { [IO.File]::Delete($staging) }
    }
}

function Enter-DaemonInstanceLock {
    param([string]$RecordPath)
    $path = [IO.Path]::ChangeExtension($RecordPath, '.lock')
    Assert-DaemonInstancePath $path -Owned
    $stream = [IO.File]::Open($path, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    try { $stream.Lock(1073741824, 1); return $stream } catch { $stream.Dispose(); throw }
}

function New-DaemonInstanceReservation {
    param(
        [Parameter(Mandatory)][string]$Service,
        [Parameter(Mandatory)][string]$RuntimeRoot,
        [Parameter(Mandatory)][string]$Source,
        [string]$RegistryRoot,
        [ValidateSet('production','unattended-test','interactive-test')][string]$Purpose = 'production',
        [double]$TtlSeconds = 0, [double]$MaxLifetimeSeconds = 0
    )
    Initialize-DaemonInstanceNative
    $Source=[CopilotExtensions.DaemonInstanceNative]::FinalPath($Source)
    Assert-DaemonInstancePath $Source
    if (-not [IO.File]::Exists($Source)) { throw 'Daemon source provenance missing.' }
    $owner = [Diagnostics.Process]::GetCurrentProcess()
    $ownerPid = $PID
    $ownerBirth = [CopilotExtensions.DaemonInstanceNative]::Birth($owner)
    $run = ''
    $ownerName = 'process:' + $PID
    if ($env:COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT) {
        $declaration = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(
            $env:COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT
        ))
        $context = @{}
        foreach ($line in ($declaration -split "`n")) {
            if (-not $line) { continue }
            $parts=$line.Split("`t")
            if ($parts.Length -ne 2 -or $context.ContainsKey($parts[0])) { throw 'Invalid test lifecycle declaration.' }
            $context[$parts[0]]=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($parts[1]))
        }
        $testOwner = [Diagnostics.Process]::GetProcessById([int]$context.pid)
        try {
            if ([CopilotExtensions.DaemonInstanceNative]::Birth($testOwner) -cne $context.birth -or $testOwner.HasExited) {
                throw 'Daemon test owner changed.'
            }
        } finally { $testOwner.Dispose() }
        if ($Purpose -eq 'interactive-test' -and $context.purpose -ne 'interactive-test') {
            throw 'Interactive daemon requires a pause-tolerant lifecycle owner.'
        }
        $Purpose = $context.purpose
        $RegistryRoot = $context.registry_root
        $TtlSeconds = [double]$context.ttl
        $MaxLifetimeSeconds = [double]$context.max_lifetime
        $ownerPid = $context.pid
        $ownerBirth = $context.birth
        $ownerName = $context.owner
        $run = $context.run_id
    }
    if ($Purpose -eq 'unattended-test' -and $TtlSeconds -eq 0) { $TtlSeconds = 600; $MaxLifetimeSeconds = 14400 }
    if ($Purpose -notin @('production','unattended-test','interactive-test')) { throw 'Invalid daemon purpose.' }
    if ($Purpose -ne 'production' -and (
        $TtlSeconds -le 0 -or $MaxLifetimeSeconds -lt $TtlSeconds -or
        [double]::IsInfinity($MaxLifetimeSeconds) -or [double]::IsNaN($MaxLifetimeSeconds) -or
        [double]::IsInfinity($TtlSeconds) -or [double]::IsNaN($TtlSeconds)
    )) { throw 'Disposable daemon requires finite explicit lifetime limits.' }
    if ($Purpose -eq 'production' -and ($TtlSeconds -ne 0 -or $MaxLifetimeSeconds -ne 0)) {
        throw 'Production daemon instances do not expire.'
    }
    Assert-DaemonInstancePath $RuntimeRoot
    $newRuntime=-not [IO.Directory]::Exists($RuntimeRoot)
    [void][IO.Directory]::CreateDirectory($RuntimeRoot)
    Assert-DaemonInstancePath $RuntimeRoot -Directory
    if ($newRuntime) { Assert-DaemonInstancePath $RuntimeRoot -Directory -Owned }
    $root = Get-DaemonInstanceRoot $RegistryRoot
    Assert-DaemonInstancePath $root
    [void][IO.Directory]::CreateDirectory($root)
    Assert-DaemonInstancePath $root -Directory -Owned
    $id = [Guid]::NewGuid().ToString('N')
    $path = Join-Path $root ($id + '.instance')
    $identity = [CopilotExtensions.DaemonInstanceNative]::FileIdentity($RuntimeRoot)
    $now = ([DateTime]::UtcNow - [DateTime]'1970-01-01').TotalSeconds
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $record = @{
        schema='1'; instance_id=$id; state='reserved'; service=$Service; purpose=$Purpose; lifecycle='resident'
        owner=$ownerName; owner_pid=[string]$ownerPid; owner_birth=$ownerBirth
        launcher_pid=[string]$PID; launcher_birth=([CopilotExtensions.DaemonInstanceNative]::Birth($owner))
        runtime_root=$RuntimeRoot; runtime_device=$identity[0]; runtime_inode=$identity[1]
        source=$Source; created=$now.ToString($culture); updated=$now.ToString($culture)
        artifact_count='0'; ttl=$TtlSeconds.ToString($culture); run_id=$run
        protected='0'
        deadline=($(if ($TtlSeconds) { $now + $TtlSeconds } else { 0 })).ToString($culture)
        hard_deadline=($(if ($TtlSeconds) { $now + $MaxLifetimeSeconds } else { 0 })).ToString($culture)
    }
    $lock = Enter-DaemonInstanceLock $path
    try { Write-DaemonInstanceRecord $path $record } finally { $lock.Dispose() }
    return [pscustomobject]@{ instance_id=$id; registry_root=$root; record_path=$path; source=$Source }
}

function Set-DaemonInstanceProcess {
    param($Receipt, [Diagnostics.Process]$Process, [switch]$Admitted)
    Initialize-DaemonInstanceNative
    $lock = Enter-DaemonInstanceLock $Receipt.record_path
    try {
        $record = Read-DaemonInstanceRecord $Receipt.record_path
        $birth = [CopilotExtensions.DaemonInstanceNative]::Birth($Process)
        if ($Admitted -and $record.purpose -ne 'production') {
            $now=([DateTime]::UtcNow-[DateTime]'1970-01-01').TotalSeconds
            $deadline=[double]$record.deadline; $hard=[double]$record.hard_deadline
            if ([double]::IsNaN($deadline) -or [double]::IsInfinity($deadline) -or
                [double]::IsNaN($hard) -or [double]::IsInfinity($hard) -or
                $deadline -gt $hard -or $deadline -le $now -or $hard -le $now) {
                throw 'Expired daemon admission refused.'
            }
        }
        if ($record.state -notin @('reserved','bound','running') -or (
            $record.ContainsKey('pid') -and ($record.pid -ne [string]$Process.Id -or $record.birth -cne $birth)
        )) { throw 'Daemon instance process changed.' }
        $record.pid = [string]$Process.Id
        $record.birth = $birth
        $record.state = $(if ($Admitted) { 'running' } else { 'bound' })
        Write-DaemonInstanceRecord $Receipt.record_path $record
    } finally { $lock.Dispose() }
    return $record
}

function Enter-DaemonInstance {
    param(
        [Parameter(Mandatory)][string]$Service, [Parameter(Mandatory)][string]$RuntimeRoot,
        [Parameter(Mandatory)][string]$Source, [string]$RegistryRoot,
        [string]$Purpose, [double]$TtlSeconds=0, [double]$MaxLifetimeSeconds=0
    )
    Initialize-DaemonInstanceNative
    $current=[CopilotExtensions.DaemonInstanceNative]::AcquireCurrent()
    if ($current) {
        return [pscustomobject]@{
            instance_id=[IO.Path]::GetFileNameWithoutExtension($current)
            registry_root=[IO.Path]::GetDirectoryName($current); record_path=$current
        }
    }
    if ($env:COPILOT_EXTENSIONS_DAEMON_INSTANCE) {
        $id = $env:COPILOT_EXTENSIONS_DAEMON_INSTANCE
        if ($id -cnotmatch '^[a-f0-9]{32}$') { throw 'Invalid daemon instance reservation.' }
        $root = $env:COPILOT_EXTENSIONS_DAEMON_REGISTRY
        $receipt = [pscustomobject]@{ instance_id=$id; registry_root=$root; record_path=(Join-Path $root ($id+'.instance')) }
    } else {
        if (-not $Purpose) {
            if ($env:COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT) { $Purpose='unattended-test' }
            else { throw 'Direct daemon startup requires an explicit purpose or lifecycle reservation.' }
        }
        $receipt = New-DaemonInstanceReservation -Service $Service -RuntimeRoot $RuntimeRoot -Source $Source `
            -RegistryRoot $RegistryRoot -Purpose $Purpose -TtlSeconds $TtlSeconds -MaxLifetimeSeconds $MaxLifetimeSeconds
    }
    $record = Set-DaemonInstanceProcess $receipt ([Diagnostics.Process]::GetCurrentProcess()) -Admitted
    Remove-Item Env:COPILOT_EXTENSIONS_DAEMON_INSTANCE -ErrorAction SilentlyContinue
    Remove-Item Env:COPILOT_EXTENSIONS_DAEMON_REGISTRY -ErrorAction SilentlyContinue
    if ($record.purpose -ne 'production') {
        $now = ([DateTime]::UtcNow - [DateTime]'1970-01-01').TotalSeconds
        [CopilotExtensions.DaemonInstanceNative]::Watch(
            $receipt.instance_id, $receipt.record_path, $record.birth, ([double]$record.hard_deadline-$now)
        )
    }
    [CopilotExtensions.DaemonInstanceNative]::PublishCurrent($receipt.record_path)
    return $receipt
}

function Exit-DaemonInstance {
    param([Parameter(Mandatory)]$Receipt, [Diagnostics.Process]$Process)
    Initialize-DaemonInstanceNative
    if (-not $Process) {
        if ($Receipt.PSObject.Properties['closed'] -and $Receipt.closed) { return }
        $last=[CopilotExtensions.DaemonInstanceNative]::ReleaseCurrent($Receipt.record_path)
        $Receipt | Add-Member -NotePropertyName closed -NotePropertyValue $true -Force
        if (-not $last) { return }
    }
    $target = $Process
    if (-not $target) { $target = [Diagnostics.Process]::GetCurrentProcess() }
    $lock = Enter-DaemonInstanceLock $Receipt.record_path
    try {
        $record = Read-DaemonInstanceRecord $Receipt.record_path
        if ($record.pid -ne [string]$target.Id -or
            $record.birth -cne [CopilotExtensions.DaemonInstanceNative]::Birth($target)) {
            throw 'Daemon instance completion identity changed.'
        }
        if ($Process -and -not $Process.HasExited) { throw 'Child daemon completion requires observed exit.' }
        $record.state = 'finished'
        Write-DaemonInstanceRecord $Receipt.record_path $record
        if (-not $Process) { [CopilotExtensions.DaemonInstanceNative]::Unwatch($Receipt.instance_id) }
    } finally { $lock.Dispose() }
}

function Read-DaemonReconcileCursor {
    param([string]$Root)
    $path=Join-Path $Root '.reconcile.cursor'
    try { $attributes=[IO.File]::GetAttributes($path) }
    catch [IO.FileNotFoundException] { return '' }
    catch [IO.DirectoryNotFoundException] { throw 'Inventory cursor parent unavailable.' }
    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Inventory cursor redirected.' }
    Assert-DaemonInstancePath $path -Owned
    $stream=[IO.File]::OpenRead($path)
    try {
        $bytes=New-Object byte[] 97
        $count=0
        while ($count -lt $bytes.Length) {
            $read=$stream.Read($bytes,$count,$bytes.Length-$count)
            if ($read -eq 0) { break }
            $count+=$read
        }
    } finally { $stream.Dispose() }
    if ($count -gt 96) { throw 'Invalid inventory cursor.' }
    $text=[Text.Encoding]::ASCII.GetString($bytes,0,$count)
    if ($text -cnotmatch '\Acopilot-daemon-reconcile-v1\n([a-f0-9]{32}|)\n\z') {
        throw 'Invalid inventory cursor.'
    }
    return $Matches[1]
}

function Write-DaemonReconcileCursor {
    param([string]$Root,[string]$Key)
    $path=Join-Path $Root '.reconcile.cursor'
    Assert-DaemonInstancePath $path -Owned
    $pending=Join-Path $Root ('.reconcile.'+[Guid]::NewGuid().ToString('N')+'.pending')
    try {
        $stream=[IO.File]::Open($pending,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try {
            $bytes=[Text.Encoding]::ASCII.GetBytes("copilot-daemon-reconcile-v1`n$Key`n")
            $stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)
        } finally { $stream.Dispose() }
        if ([IO.File]::Exists($path)) {
            Assert-DaemonInstancePath $path -Owned
            [IO.File]::Replace($pending,$path,[System.Management.Automation.Language.NullString]::Value)
        } else { [IO.File]::Move($pending,$path) }
    } finally { if ([IO.File]::Exists($pending)) { [IO.File]::Delete($pending) } }
}

function Invoke-DaemonInstanceReconcile {
    param([int]$Limit=128, [double]$MaxSeconds=5, [string]$RegistryRoot)
    if ($Limit -lt 1 -or $Limit -gt 4096 -or $MaxSeconds -le 0 -or
        [double]::IsNaN($MaxSeconds) -or [double]::IsInfinity($MaxSeconds)) { throw 'Invalid daemon reconciliation bounds.' }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $root = Get-DaemonInstanceRoot $RegistryRoot
    Assert-DaemonInstancePath $root -Owned
    if (-not [IO.Directory]::Exists($root)) { return }
    [string]$cursor=Read-DaemonReconcileCursor $root
    [string[]]$files=@([IO.Directory]::EnumerateFiles($root,'*.instance'))
    if ($files.Count -eq 0) { return }
    [Array]::Sort($files,[StringComparer]::Ordinal)
    foreach ($file in $files) {
        if ([IO.Path]::GetFileNameWithoutExtension($file) -cnotmatch '^[a-f0-9]{32}$') {
            throw 'Noncanonical inventory instance key.'
        }
    }
    $localGuard=[String]::Intern('copilot-daemon-reconcile:'+$root.ToUpperInvariant())
    if (-not [Threading.Monitor]::TryEnter($localGuard)) { throw 'Inventory collection already owned.' }
    $collector=$null
    try {
    $collector=Enter-DaemonInstanceLock (Join-Path $root '.reconcile.cursor')
    [string]$cursor=Read-DaemonReconcileCursor $root
    $after=@($files | Where-Object { [StringComparer]::Ordinal.Compare([IO.Path]::GetFileNameWithoutExtension($_),$cursor) -gt 0 })
    $before=@($files | Where-Object { [StringComparer]::Ordinal.Compare([IO.Path]::GetFileNameWithoutExtension($_),$cursor) -le 0 })
    $visited=$null
    $count = 0
    try {
    foreach ($path in @($after+$before)) {
        if ($count -ge $Limit -or $clock.Elapsed.TotalSeconds -ge $MaxSeconds) { break }
        $count++
        $lock=$null; $process=$null; $status='held'; $reason='invalid-record'
        $id = [IO.Path]::GetFileNameWithoutExtension($path)
        $visited=$id
        try {
            if ($id -cnotmatch '^[a-f0-9]{32}$') { throw 'Invalid instance id.' }
            $lock = Enter-DaemonInstanceLock $path
            $record = Read-DaemonInstanceRecord $path
            $now=([DateTime]::UtcNow-[DateTime]'1970-01-01').TotalSeconds
            $finite=$record.purpose -eq 'production' -and $record.ContainsKey('lifecycle') -and $record.lifecycle -eq 'finite-worker'
            if (-not $finite -and ($record.purpose -eq 'production' -or $now -lt [double]$record.deadline)) { continue }
            if (-not $finite -and ($record.purpose -notin @('unattended-test','interactive-test') -or
                [double]::IsNaN([double]$record.deadline) -or [double]::IsInfinity([double]$record.deadline) -or
                [double]::IsNaN([double]$record.hard_deadline) -or [double]::IsInfinity([double]$record.hard_deadline) -or
                [double]$record.deadline -le 0 -or [double]$record.hard_deadline -lt [double]$record.deadline)) {
                throw 'Invalid daemon expiry policy.'
            }
            Initialize-DaemonInstanceNative
            if ($record.ContainsKey('protected') -and $record.protected -ne '0') {
                $reason='protected-work'; throw 'Protected work requires its original owner.'
            }
            if ($finite) {
                if (-not $record.ContainsKey('state') -or $record.state -notin @('reserved','bound','running','finished','cleaning')) {
                    $reason='unknown-state'; throw 'Unknown instance lifecycle state.'
                }
                if ($record.ContainsKey('spawn_failure')) { $reason='spawn-failure-held'; throw 'Unsettled launch.' }
                if (-not $record.ContainsKey('pid') -or [int]$record.pid -le 0 -or -not $record.ContainsKey('birth')) {
                    $reason='launch-outcome-unknown'; throw 'Unknown launch outcome.'
                }
                $dead=$false
                try { $process=[Diagnostics.Process]::GetProcessById([int]$record.pid); [void]$process.Handle }
                catch [ArgumentException] { $dead=$true }
                if (-not $dead) {
                    if ([CopilotExtensions.DaemonInstanceNative]::Birth($process) -cne $record.birth) {
                        $reason='process-reused'; throw 'Process birth changed.'
                    }
                    $dead=$process.HasExited
                }
                if ($dead) {
                    [IO.File]::Delete($path); $status='settled'; $reason='finite-worker-exit-observed'
                } else { $status='live'; $reason='finite-worker-running' }
                [pscustomobject]@{ instance_id=$id; status=$status; reason=$reason; removed=@() }
                continue
            }
            Assert-DaemonInstancePath $record.runtime_root -Directory
            $identity = [CopilotExtensions.DaemonInstanceNative]::FileIdentity($record.runtime_root)
            if ($identity[0] -cne $record.runtime_device -or $identity[1] -cne $record.runtime_inode) {
                $reason='runtime-root-changed'; throw 'Runtime custody changed.'
            }
            $artifacts = @()
            $artifactCount = [int]$record.artifact_count
            if ($artifactCount -lt 0 -or $artifactCount -gt 64) { throw 'Invalid artifact count.' }
            for ($index=0; $index -lt $artifactCount; $index++) {
                $artifact = $record["artifact_${index}_path"]
                Assert-DaemonInstancePath $artifact -Owned
                $prefix=$record.runtime_root.TrimEnd('\')+'\'
                $inventory=$root.TrimEnd('\')+'\'
                if (-not $artifact.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -or
                    $artifact.StartsWith($inventory,[StringComparison]::OrdinalIgnoreCase) -or
                    $artifact.Equals($record.source,[StringComparison]::OrdinalIgnoreCase)) {
                    $reason='foreign-artifact'; throw 'Artifact lies outside declared lifecycle custody.'
                }
                if (-not [IO.File]::Exists($artifact)) { continue }
                $actual=[CopilotExtensions.DaemonInstanceNative]::FileIdentity($artifact)
                if ($actual[0] -cne $record["artifact_${index}_device"] -or
                    $actual[1] -cne $record["artifact_${index}_inode"]) {
                    $reason='artifact-identity-changed'; throw 'Artifact identity changed.'
                }
                $artifacts += $index
            }
            if (-not $record.ContainsKey('pid') -or -not $record.ContainsKey('birth')) {
                $reason='launch-outcome-unknown'; throw 'Unknown launch outcome.'
            }
            $dead=$false
            try { $process=[Diagnostics.Process]::GetProcessById([int]$record.pid); [void]$process.Handle }
            catch [ArgumentException] { $dead=$true }
            if (-not $dead) {
                if ([CopilotExtensions.DaemonInstanceNative]::Birth($process) -cne $record.birth) {
                    $reason='process-reused'; throw 'Process birth changed.'
                }
                $dead=$process.HasExited
            }
            if (-not $dead) {
                $now=([DateTime]::UtcNow-[DateTime]'1970-01-01').TotalSeconds
                if ($record.purpose -eq 'production' -or $now -lt [double]$record.deadline) {
                    $status='live'; $reason='production-or-unexpired'
                } else {
                    if ($process.Id -eq $PID) { $reason='self-cleanup-refused'; throw 'Cannot reap self.' }
                    $process.Kill()
                    $remaining=[Math]::Max(0, [Math]::Min(5000, ($MaxSeconds-$clock.Elapsed.TotalSeconds)*1000))
                    if (-not $process.WaitForExit([int]$remaining)) { $reason='stop-unconfirmed'; throw 'Stop unconfirmed.' }
                    $dead=$true
                }
            }
            if ($dead) {
                foreach ($index in $artifacts) {
                    [CopilotExtensions.DaemonInstanceNative]::DeleteOwned(
                        $record["artifact_${index}_path"], $record["artifact_${index}_device"],
                        $record["artifact_${index}_inode"]
                    )
                }
                [IO.File]::Delete($path); $status='settled'; $reason='process-exit-observed'
            }
        } catch {
            [Console]::Error.WriteLine('daemon instance held: '+$id+' '+$reason)
        } finally {
            if ($process) { $process.Dispose() }
            if ($lock) { $lock.Dispose() }
        }
        [pscustomobject]@{ instance_id=$id; status=$status; reason=$reason; removed=@() }
    }
    } finally {
        if ($null -ne $visited -and $visited -cne $cursor) { Write-DaemonReconcileCursor $root $visited }
    }
    } finally {
        if ($collector) { $collector.Dispose() }
        [Threading.Monitor]::Exit($localGuard)
    }
}
