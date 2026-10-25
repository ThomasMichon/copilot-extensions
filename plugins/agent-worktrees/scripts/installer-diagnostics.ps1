# Native, non-authoritative installer diagnostics. No runtime Python is required.
$script:InstallerDiagnosticWarned = $false
$script:InstallerDiagnostic = $null
$script:InstallerDiagnosticPipeReady = $false
$script:InstallerDiagnosticWarningPending = $false
$script:InstallerDiagnosticPresentation = @()
$script:InstallerDiagnosticReadStreams = @()
$script:InstallerDiagnosticReadProcess = 0
$script:InstallerDiagnosticNativeFailed = $false
$script:InstallerDiagnosticNativeFailure = $null
$script:InstallerDiagnosticOutputs = @()
$script:InstallerDiagnosticClock = $null
$script:InstallerDiagnosticBudgetMilliseconds = 0
$script:InstallerDiagnosticFinalSnapshots = $null

function Initialize-InstallerDiagnosticNative {
    if ($script:InstallerDiagnosticNativeFailed) { return $false }
    try {
        if (-not ('InstallerDiagnosticsPipeV1' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class InstallerDiagnosticsPipeV1 {
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool PeekNamedPipe(SafeFileHandle handle, IntPtr buffer,
        uint size, IntPtr read, out uint available, IntPtr remaining);
    [DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int number);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern uint GetFileType(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetConsoleMode(IntPtr handle, out uint mode);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetNamedPipeInfo(IntPtr handle, out uint flags,
        out uint output, out uint input, out uint instances);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool GetNamedPipeHandleState(IntPtr handle, out uint mode,
        IntPtr instances, IntPtr collection, IntPtr timeout, IntPtr user, uint length);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetNamedPipeHandleState(IntPtr handle, ref uint mode, IntPtr collection, IntPtr timeout);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool WriteFile(IntPtr handle, byte[] data, uint count, out uint written, IntPtr overlapped);
    static readonly object Gate = new object();
    public static int Available(SafeFileHandle handle) {
        uint available;
        if (PeekNamedPipe(handle,IntPtr.Zero,0,IntPtr.Zero,out available,IntPtr.Zero))
            return checked((int)available);
        int error=Marshal.GetLastWin32Error();
        if (error==109 || error==232) return 0;
        throw new Win32Exception(error);
    }
    static Win32Exception Failure(string operation, int code) {
        var error=new Win32Exception(code);
        error.Data["operation"]=operation;
        return error;
    }
    static uint QueryMode(IntPtr handle, string operation) {
        uint mode;
        if (!GetNamedPipeHandleState(handle,out mode,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero,0))
            throw Failure(operation,Marshal.GetLastWin32Error());
        return mode;
    }
    public static uint Mode(IntPtr handle) { return QueryMode(handle,"mode-query"); }
    internal static bool Scope(IntPtr handle, bool pipe, Action action) {
        lock (Gate) {
            uint before=0;
            bool changed=false;
            try {
                if (pipe) {
                    before=Mode(handle);
                    uint mode=before|1u;
                    if (!SetNamedPipeHandleState(handle,ref mode,IntPtr.Zero,IntPtr.Zero)) {
                        int error=Marshal.GetLastWin32Error();
                        // Only this operation's BUSY, with positively unchanged
                        // mode and no write issued, is deferred backpressure.
                        if (error==231 && QueryMode(handle,"busy-mode-query")==before) return false;
                        throw Failure("mode-set",error);
                    }
                    changed=true;
                }
                action();
                return true;
            } finally {
                // Duplicated/inherited write handles share mode: restore before
                // returning to PowerShell, never leave NOWAIT enabled between calls.
                if (changed) {
                    uint mode=before;
                    if (!SetNamedPipeHandleState(handle,ref mode,IntPtr.Zero,IntPtr.Zero))
                        throw Failure("mode-restore",Marshal.GetLastWin32Error());
                    if (QueryMode(handle,"restore-query")!=before)
                        throw new IOException("presentation pipe mode restoration mismatch");
                }
            }
        }
    }
    internal static int WriteBytes(IntPtr handle, byte[] data) {
        uint written;
        if (!WriteFile(handle,data,(uint)data.Length,out written,IntPtr.Zero))
            throw Failure("write",Marshal.GetLastWin32Error());
        if (written>data.Length) throw new IOException("invalid presentation write count");
        return checked((int)written);
    }
    public static int Write(IntPtr handle, byte[] data, bool pipe) {
        int written=0;
        Scope(handle,pipe,delegate { written=WriteBytes(handle,data); });
        return written;
    }
}
public sealed class InstallerDiagnosticOutput {
    public const int Limit=262144, Chunk=1024, Work=16384;
    readonly Queue<byte[]> queue=new Queue<byte[]>();
    readonly TextWriter writer;
    readonly IntPtr handle;
    readonly uint type;
    readonly Encoder encoder;
    int offset;
    public int Pending { get; private set; }
    public int Peak { get; private set; }
    public long Dropped { get; private set; }
    public long Written { get; private set; }
    public long ZeroWrites { get; private set; }
    public long Calls { get; private set; }
    public long BusyModes { get; private set; }
    public long NativeFailures { get; private set; }
    public string Loss { get; private set; }
    public InstallerDiagnosticOutput(int number, TextWriter destination) {
        writer=destination; encoder=destination.Encoding.GetEncoder();
        handle=InstallerDiagnosticsPipeV1.GetStdHandle(number);
        type=InstallerDiagnosticsPipeV1.GetFileType(handle);
        if (type!=1 && type!=2 && type!=3) Disable("unsupported-destination");
        uint mode,flags,output,input,instances;
        if (type==2 && !InstallerDiagnosticsPipeV1.GetConsoleMode(handle,out mode))
            Disable("unsupported-character-device");
        if (type==3 && (!InstallerDiagnosticsPipeV1.GetNamedPipeInfo(
                handle,out flags,out output,out input,out instances) || (flags&4u)!=0))
            Disable("unsupported-pipe");
    }
    public void Disable(string reason) {
        if (Loss==null) Loss=reason;
        Dropped+=Pending; Pending=0; offset=0; queue.Clear();
    }
    public bool CanAccept(int characters) {
        return Loss==null && writer.Encoding.GetMaxByteCount(characters)<=Limit-Pending;
    }
    public void Add(char[] text, int count, bool final) {
        try {
            if (type==2 && Loss==null) {
                writer.Write(text,0,count); writer.Flush(); return;
            }
            byte[] data=new byte[writer.Encoding.GetMaxByteCount(count)];
            int length=encoder.GetBytes(text,0,count,data,0,final);
            if (Loss!=null) { Dropped+=length; return; }
            if (length>Limit-Pending) { Dropped+=length; Disable("queue-saturated"); return; }
            if (length==0) return;
            Array.Resize(ref data,length); queue.Enqueue(data); Pending+=length; Peak=Math.Max(Peak,Pending);
        } catch { Disable("encoding-failed"); }
    }
    public void Pump() {
        if (Loss!=null || Pending==0) return;
        try {
            bool entered=InstallerDiagnosticsPipeV1.Scope(handle,type==3,delegate {
                int budget=Work, attempts=Work/Chunk;
                while (Pending>0 && budget>0 && attempts-- > 0) {
                    byte[] head=queue.Peek();
                    int count=Math.Min(Math.Min(head.Length-offset,Chunk),budget);
                    byte[] data=new byte[count]; Array.Copy(head,offset,data,0,count);
                    Calls++;
                    int written=InstallerDiagnosticsPipeV1.WriteBytes(handle,data);
                    if (written==0) { ZeroWrites++; break; }
                    Written+=written; offset+=written; Pending-=written; budget-=written;
                    if (offset==head.Length) { queue.Dequeue(); offset=0; }
                }
            });
            if (!entered) BusyModes++;
        } catch (Win32Exception error) {
            NativeFailures++;
            Disable("native-"+error.Data["operation"]+"-"+error.NativeErrorCode);
        } catch (Exception error) {
            NativeFailures++;
            Disable("presentation-"+error.GetType().Name);
        }
    }
}
'@
        }
        if ($script:InstallerDiagnosticOutputs.Count -eq 0) {
            $script:InstallerDiagnosticOutputs = @(
                [InstallerDiagnosticOutput]::new(-11, [Console]::Out),
                [InstallerDiagnosticOutput]::new(-12, [Console]::Error)
            )
        }
        return $true
    } catch {
        $script:InstallerDiagnosticNativeFailed = $true
        $script:InstallerDiagnosticNativeFailure = 'native-initialization-failed'
        $env:COPILOT_EXTENSIONS_DIAGNOSTICS_PRESENTATION_MISSING = '1'
        Save-InstallerDiagnosticPresentationState
        return $false
    }
}

function Save-InstallerDiagnosticPresentationState([switch]$Archive) {
    $loss = @()
    if ($script:InstallerDiagnosticNativeFailure) {
        $loss += @{ channel = 'native'; reason = $script:InstallerDiagnosticNativeFailure; dropped = 0 }
    }
    for ($index = 0; $index -lt $script:InstallerDiagnosticOutputs.Count; $index++) {
        $output = $script:InstallerDiagnosticOutputs[$index]
        if ($output.Loss) {
            $loss += @{ channel = $index; reason = $output.Loss; dropped = $output.Dropped
                native_failures = $output.NativeFailures; native_calls = $output.Calls
                zero_writes = $output.ZeroWrites; busy_modes = $output.BusyModes }
        }
        if ($index -lt $script:InstallerDiagnosticPresentation.Count) {
            $state = $script:InstallerDiagnosticPresentation[$index]
            if ($state.Loss -or ($output.Loss -and $state.Recorded -gt $state.Cursor)) {
                $loss += @{ channel = $index; reason = $(if ($state.Loss) { $state.Loss } else { $output.Loss })
                    recorded = $state.Recorded; replayed = $state.Cursor
                    raw_pending = ($state.Recorded - $state.Cursor); input_lost = $state.InputLost }
            }
        }
    }
    if (-not $loss.Count) { return }
    $env:COPILOT_EXTENSIONS_DIAGNOSTICS_PRESENTATION_MISSING = '1'
    if ($script:InstallerDiagnostic) {
        try {
            $path = [IO.Path]::Combine($script:InstallerDiagnostic.Directory, 'presentation-loss.json')
            Test-InstallerDiagnosticsPath $path
            $record = Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject @($loss) -Compress
            [IO.File]::WriteAllText($path, $record)
            if ($Archive) {
                $history = [IO.Path]::Combine($script:InstallerDiagnostic.Directory, 'presentation-loss.history.jsonl')
                Test-InstallerDiagnosticsPath $history
                [IO.File]::AppendAllText($history, ($record + "`n"), [Text.UTF8Encoding]::new($false))
            }
        } catch { Write-InstallerDiagnosticsWarning -DeferPresentation }
    }
}

function Reset-InstallerDiagnosticPresentationOutputs {
    Complete-InstallerDiagnosticPresentation
    Save-InstallerDiagnosticPresentationState -Archive
    $script:InstallerDiagnosticOutputs = @()
    return (Initialize-InstallerDiagnosticNative)
}

function Write-InstallerDiagnosticPresentation([string]$Message, [switch]$ErrorChannel) {
    if (-not (Initialize-InstallerDiagnosticNative)) {
        Save-InstallerDiagnosticPresentationState
        return
    }
    $index = if ($ErrorChannel) { 1 } else { 0 }
    $output = $script:InstallerDiagnosticOutputs[$index]
    if ($script:InstallerDiagnosticClock -and (Get-InstallerDiagnosticPresentationRemaining) -le 0) {
        $output.Disable('presentation-deadline')
    }
    foreach ($offset in 0..([Math]::Max(0, [int][Math]::Ceiling($Message.Length / 8192.0) - 1))) {
        $start = $offset * 8192
        $count = [Math]::Min(8192, $Message.Length - $start)
        $characters = $Message.Substring($start, $count).ToCharArray()
        $output.Add($characters, $characters.Length, $false)
        if (-not $script:InstallerDiagnosticClock -or (Get-InstallerDiagnosticPresentationRemaining) -gt 0) {
            $output.Pump()
        }
    }
    Save-InstallerDiagnosticPresentationState
}

function Get-InstallerDiagnosticPresentationRemaining {
    if (-not $script:InstallerDiagnosticClock) { return 0 }
    return [Math]::Max(0, $script:InstallerDiagnosticBudgetMilliseconds - $script:InstallerDiagnosticClock.ElapsedMilliseconds)
}

function Stop-InstallerDiagnosticPresentation([string]$Reason) {
    for ($index = 0; $index -lt $script:InstallerDiagnosticOutputs.Count; $index++) {
        $output = $script:InstallerDiagnosticOutputs[$index]
        $rawPending = 0
        $unfinished = $false
        if ($index -lt $script:InstallerDiagnosticPresentation.Count) {
            $state = $script:InstallerDiagnosticPresentation[$index]
            $rawPending = $state.Recorded - $state.Cursor
            $unfinished = $state.Final -and -not $state.DecoderFinal
            if (($rawPending -gt 0 -or $unfinished) -and -not $state.Loss) { $state.Loss = $Reason }
        }
        if ($output.Pending -gt 0 -or $rawPending -gt 0 -or $unfinished) { $output.Disable($Reason) }
    }
    Save-InstallerDiagnosticPresentationState
}

function Update-InstallerDiagnosticPresentation {
    $pending = [long]0
    for ($index = 0; $index -lt $script:InstallerDiagnosticOutputs.Count; $index++) {
        if ($script:InstallerDiagnosticClock -and (Get-InstallerDiagnosticPresentationRemaining) -le 0) {
            Stop-InstallerDiagnosticPresentation 'presentation-deadline'
            return 0
        }
        $output = $script:InstallerDiagnosticOutputs[$index]
        $before = $output.Written
        $output.Pump()
        if ($index -ge $script:InstallerDiagnosticPresentation.Count) {
            $pending += $output.Pending
            continue
        }
        $state = $script:InstallerDiagnosticPresentation[$index]
        $cursorBefore = $state.Cursor
        if (-not $output.Loss) {
            try {
                if (-not $state.Direct -and $state.RawPath -and $state.Recorded -gt $state.Cursor -and
                    $output.CanAccept($state.Characters.Length)) {
                    Test-InstallerDiagnosticsPath $state.RawPath
                    $reader = [IO.File]::Open($state.RawPath, 'Open', 'Read', 'ReadWrite,Delete')
                    try {
                        $count = [int][Math]::Min(8192, $state.Recorded - $state.Cursor)
                        if ($reader.Length -lt $state.Recorded) { throw 'Recorded raw range is no longer available.' }
                        $reader.Position = $state.Cursor
                        $bytes = New-Object byte[] $count
                        $read = 0
                        while ($read -lt $count) {
                            $copied = $reader.Read($bytes, $read, $count - $read)
                            if ($copied -eq 0) { throw 'Recorded raw range ended early.' }
                            $read += $copied
                        }
                    } finally { $reader.Dispose() }
                    $length = $state.Decoder.GetChars($bytes, 0, $count, $state.Characters, 0, $false)
                    $output.Add($state.Characters, $length, $false)
                    $state.Cursor += $count
                    $state.RawReadBytes += $count
                }
                if ($state.Final -and -not $state.DecoderFinal -and $state.Cursor -eq $state.Recorded -and
                    $output.CanAccept($state.Characters.Length)) {
                    if (-not $state.Loss) {
                        $length = $state.Decoder.GetChars([byte[]]@(), 0, 0, $state.Characters, 0, $true)
                        $output.Add($state.Characters, $length, $true)
                    }
                    $state.DecoderFinal = $true
                }
            } catch {
                $state.Loss = 'raw-replay-failed'
                $output.Disable('raw-replay-failed')
                Write-InstallerDiagnosticsWarning -DeferPresentation
            }
        }
        $backlog = $state.Recorded - $state.Cursor + $output.Pending
        if ($output.Written -gt $before -or ($output.Pending -eq 0 -and $state.Cursor -gt $cursorBefore)) {
            $state.Stall = [Diagnostics.Stopwatch]::StartNew()
        }
        if ($backlog -gt 0 -and -not $state.Stall) { $state.Stall = [Diagnostics.Stopwatch]::StartNew() }
        if ($backlog -eq 0) { $state.Stall = $null }
        # Keep the existing one-second best-effort stall bound inside, never
        # beyond, the producer's original deadline. Each channel progresses alone.
        if ($state.Final -and $state.Stall -and $state.Stall.ElapsedMilliseconds -ge 1000 -and -not $output.Loss) {
            if ($state.Recorded -gt $state.Cursor -and -not $state.Loss) { $state.Loss = 'presentation-backpressure' }
            $output.Disable('presentation-backpressure')
        }
        if (-not $output.Loss) {
            $pending += $backlog
            if ($state.Final -and -not $state.DecoderFinal) { $pending++ }
        }
    }
    return $pending
}

function Complete-InstallerDiagnosticPresentation {
    while ($true) {
        if ($script:InstallerDiagnosticClock -and (Get-InstallerDiagnosticPresentationRemaining) -le 0) {
            Stop-InstallerDiagnosticPresentation 'presentation-deadline'
            break
        }
        $pending = Update-InstallerDiagnosticPresentation
        if ($pending -eq 0) { break }
        if (-not $script:InstallerDiagnosticClock) {
            Stop-InstallerDiagnosticPresentation 'presentation-backpressure'
            break
        }
        $remaining = Get-InstallerDiagnosticPresentationRemaining
        if ($remaining -gt 0) { Start-Sleep -Milliseconds ([Math]::Min(10, $remaining)) }
    }
    Save-InstallerDiagnosticPresentationState
}

function Write-InstallerDiagnosticsWarning([switch]$DeferPresentation) {
    $env:COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING = '1'
    if ($script:InstallerDiagnostic) {
        try {
            $path = [IO.Path]::Combine($script:InstallerDiagnostic.Directory, 'missing-diagnostics.json')
            Test-InstallerDiagnosticsPath $path
            [IO.File]::WriteAllText($path, '{"missing_diagnostics":true}')
        } catch {}
    }
    if ($DeferPresentation) { $script:InstallerDiagnosticWarningPending = $true; return }
    $script:InstallerDiagnosticWarningPending = $false
    if ($script:InstallerDiagnosticWarned) { return }
    $script:InstallerDiagnosticWarned = $true
    Write-InstallerDiagnosticPresentation ('WARNING: installer diagnostics unavailable; continuing with missing diagnostics.' + [Environment]::NewLine) -ErrorChannel
}

function Test-InstallerDiagnosticsPath([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if ([IO.File]::Exists($cursor) -or [IO.Directory]::Exists($cursor)) {
            if (([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Diagnostics paths must not contain links or reparse points.'
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function New-InstallerDiagnosticsDirectory([string]$Path) {
    Test-InstallerDiagnosticsPath $Path
    if (-not [IO.Directory]::Exists($Path)) {
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $security = [Security.AccessControl.DirectorySecurity]::new()
        $security.SetAccessRuleProtection($true, $false)
        $security.SetOwner($sid)
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $security.AddAccessRule($rule)
        $directory = [IO.DirectoryInfo]::new($Path)
        if ($PSVersionTable.PSVersion.Major -lt 7) {
            [void]$directory.Create($security)
        } else {
            [void][IO.FileSystemAclExtensions]::Create($directory, $security)
        }
    }
    Test-InstallerDiagnosticsPath $Path
    $directory = [IO.DirectoryInfo]::new($Path)
    $acl = if ($PSVersionTable.PSVersion.Major -lt 7) {
        $directory.GetAccessControl()
    } else { [IO.FileSystemAclExtensions]::GetAccessControl($directory) }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid -or
        -not $acl.AreAccessRulesProtected) {
        throw 'Diagnostics directory is not private and owned by the current user.'
    }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -ne $sid) {
            throw 'Diagnostics directory grants another identity access.'
        }
    }
}

function ConvertTo-InstallerDiagnosticText([string]$Text) {
    if ($Text -cmatch 'UPDATE_DRIVER_WITNESS|--governance-generation|COPILOT_EXTENSIONS_CONTEXT') {
        return '[sensitive diagnostic record omitted]'
    }
    $mask = '***'
    $schemes = ('bea' + 'rer|ba' + 'sic')
    $Text = [regex]::Replace($Text,
        ('(?i)(http\.extraheader\s*=)\s*(?:authorization\s*:\s*)*(?:(?:' + $schemes + ')\s+)*[^\r\n]*'),
        ('$1' + $mask))
    $Text = [regex]::Replace($Text,
        ('(?i)(authorization\s*:)\s*(?:(?:' + $schemes + ')\s+)*[^\r\n]*'),
        ('$1 ' + $mask))
    $Text = [regex]::Replace($Text, ('(?i)\b(' + $schemes + ')(\s+)[A-Za-z0-9._~+/-]+=*'), ('$1$2' + $mask))
    $Text = [regex]::Replace($Text, '(gh[pousr]_\w{20,}|github_pat_\w{20,})', $mask)
    return [regex]::Replace($Text, '(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@', ('$1' + $mask + '@'))
}

function Write-InstallerDiagnostic([string]$Message) {
    if (-not $script:InstallerDiagnostic) { return }
    try {
        $path = $script:InstallerDiagnostic.Log
        Test-InstallerDiagnosticsPath $path
        $text = ConvertTo-InstallerDiagnosticText $Message
        [IO.File]::AppendAllText($path, ($text + "`n"), [Text.UTF8Encoding]::new($false))
    } catch { Write-InstallerDiagnosticsWarning }
}

function Initialize-InstallerDiagnostics([string]$Plugin) {
    $script:InstallerDiagnostic = $null
    try {
        if ($Plugin -cnotmatch '^[a-z][a-z0-9-]{0,63}$') { throw 'Invalid diagnostic plugin name.' }
        $diagnosticsHome = [IO.Path]::Combine($env:USERPROFILE, '.copilot-extensions')
        Test-InstallerDiagnosticsPath $diagnosticsHome
        [void][IO.Directory]::CreateDirectory($diagnosticsHome)
        $root = [IO.Path]::Combine($diagnosticsHome, 'installer-diagnostics')
        New-InstallerDiagnosticsDirectory $root
        $attempt = $env:COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT
        $parentWriter = $env:COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER
        if ($parentWriter -and $parentWriter -cnotmatch '^[a-f0-9]{32}$') { $parentWriter = $null }
        $owner = $false
        if ($attempt -and $attempt -cmatch '^[a-f0-9]{32}$') {
            $directory = [IO.Path]::Combine($root, $attempt)
            New-InstallerDiagnosticsDirectory $directory
            $receipt = [IO.Path]::Combine($directory, 'attempt.json')
            Test-InstallerDiagnosticsPath $receipt
            $metadata = [IO.File]::ReadAllText($receipt) | Microsoft.PowerShell.Utility\ConvertFrom-Json
            if ($metadata.schema -cne 'copilot-extensions.installer-diagnostics' -or
                $metadata.plugin -cne $Plugin -or $metadata.attempt -cne $attempt) {
                throw 'Diagnostic correlation does not match this installer.'
            }
        } else {
            $owner = $true
            $attempt = [guid]::NewGuid().ToString('N')
            $directory = [IO.Path]::Combine($root, $attempt)
            New-InstallerDiagnosticsDirectory $directory
            $metadata = @{ schema = 'copilot-extensions.installer-diagnostics'; plugin = $Plugin
                attempt = $attempt; attribution = 'UNATTRIBUTED'; started = [DateTime]::UtcNow.ToString('o') }
            [IO.File]::WriteAllText([IO.Path]::Combine($directory, 'attempt.json'),
                ($metadata | Microsoft.PowerShell.Utility\ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
        }
        $writer = [guid]::NewGuid().ToString('N')
        $script:InstallerDiagnostic = @{ Directory = $directory; Writer = $writer; Owner = $owner
            Log = [IO.Path]::Combine($directory, "$writer.log") }
        $record = @{ writer = $writer; parent = $parentWriter; started = [DateTime]::UtcNow.ToString('o') }
        [IO.File]::WriteAllText([IO.Path]::Combine($directory, "$writer.start.json"),
            ($record | Microsoft.PowerShell.Utility\ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
        $env:COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT = $attempt
        $env:COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER = $writer
        $env:COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY = $directory
        $env:COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING = '0'
        Write-InstallerDiagnostic 'installer entered; attribution=UNATTRIBUTED'
        Remove-CompletedInstallerDiagnostics $root
        if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -ne '1') {
            Write-InstallerDiagnosticPresentation ("Installer diagnostics: $directory" + [Environment]::NewLine) -ErrorChannel
        }
    } catch { $script:InstallerDiagnostic = $null; Write-InstallerDiagnosticsWarning }
}

function Complete-InstallerDiagnostics([int]$ExitCode) {
    Complete-InstallerDiagnosticPresentation
    if (-not $script:InstallerDiagnostic) { return }
    try {
        Write-InstallerDiagnostic "installer finished; exit=$ExitCode"
        $record = @{ exit = $ExitCode; completed = [DateTime]::UtcNow.ToString('o') }
        $path = [IO.Path]::Combine($script:InstallerDiagnostic.Directory, ($script:InstallerDiagnostic.Writer + '.completed.json'))
        Test-InstallerDiagnosticsPath $path
        [IO.File]::WriteAllText($path, ($record | Microsoft.PowerShell.Utility\ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
        if ($script:InstallerDiagnostic.Owner) {
            $path = [IO.Path]::Combine($script:InstallerDiagnostic.Directory, 'completed.json')
            Test-InstallerDiagnosticsPath $path
            [IO.File]::WriteAllText($path, ($record | Microsoft.PowerShell.Utility\ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
        }
    } catch { Write-InstallerDiagnosticsWarning }
}

function Publish-InstallerDiagnosticHandoff([string]$Identifier) {
    if ($Identifier -cnotmatch '^[a-f0-9]{32}$' -or -not $script:InstallerDiagnostic) {
        throw 'Invalid diagnostic handoff.'
    }
    $path = [IO.Path]::Combine([IO.Path]::GetDirectoryName($script:InstallerDiagnostic.Directory), "handoff-$Identifier")
    Test-InstallerDiagnosticsPath $path
    $stream = [IO.File]::Open($path, 'CreateNew', 'Write', 'None')
    try {
        $text = [string]::Join([char]10, @(
            $env:COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT,
            $env:COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER,
            [string]$script:InstallerDiagnostic.Owner,
            $env:COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY
        )) + [char]10
        $bytes = [Text.Encoding]::UTF8.GetBytes($text)
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
}

function Initialize-InstallerDiagnosticPipe {
    try {
        if (-not (Reset-InstallerDiagnosticPresentationOutputs)) { return $false }
        $encoding = if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -eq '1') {
            [Text.Encoding]::UTF8
        } else { [Console]::OutputEncoding }
        $script:InstallerDiagnosticPresentation = @(
            @{ Output = $script:InstallerDiagnosticOutputs[0]; Decoder = $encoding.GetDecoder()
                Characters = (New-Object char[] ($encoding.GetMaxCharCount(8192))); Final = $false
                DecoderFinal = $false; RawPath = $null; Recorded = [long]0; Cursor = [long]0
                RawReadBytes = [long]0
                Direct = $false; RawFailed = $false; InputLost = [long]0; Loss = $null; Stall = $null },
            @{ Output = $script:InstallerDiagnosticOutputs[1]; Decoder = $encoding.GetDecoder()
                Characters = (New-Object char[] ($encoding.GetMaxCharCount(8192))); Final = $false
                DecoderFinal = $false; RawPath = $null; Recorded = [long]0; Cursor = [long]0
                RawReadBytes = [long]0
                Direct = $false; RawFailed = $false; InputLost = [long]0; Loss = $null; Stall = $null }
        )
        $script:InstallerDiagnosticReadStreams = @()
        $script:InstallerDiagnosticReadProcess = 0
        $script:InstallerDiagnosticFinalSnapshots = $null
        $script:InstallerDiagnosticClock = $null
        $script:InstallerDiagnosticBudgetMilliseconds = 0
        $script:InstallerDiagnosticPipeReady = $true
        return $true
    } catch {
        $script:InstallerDiagnosticPipeReady = $false
        Write-InstallerDiagnosticsWarning
        return $false
    }
}

function New-InstallerDiagnosticCapture([switch]$Piped) {
    if ($Piped -and -not (Initialize-InstallerDiagnosticPipe)) { return $null }
    if (-not $script:InstallerDiagnostic) { return $null }
    try {
        $id = [guid]::NewGuid().ToString('N')
        $directory = $script:InstallerDiagnostic.Directory
        $capture = @{ Stdout = [IO.Path]::Combine($directory, "$id.stdout.raw")
            Stderr = [IO.Path]::Combine($directory, "$id.stderr.raw")
            Log = [IO.Path]::Combine($directory, "$id.log") }
        foreach ($path in @($capture.Stdout, $capture.Stderr)) {
            $stream = [IO.File]::Open($path, 'CreateNew', 'Write', 'ReadWrite')
            $stream.Dispose()
        }
        [IO.File]::WriteAllText([IO.Path]::Combine($directory, "$id.pending"),
            'Private raw transport; inherited writers may remain.')
        return $capture
    } catch { Write-InstallerDiagnosticsWarning; return $null }
}

function Receive-InstallerDiagnosticCapture($Process, $Capture, [switch]$Final) {
    if (-not $script:InstallerDiagnosticPipeReady) { return 0 }
    $measure = if ($Capture -and $Capture.ContainsKey('Measure')) { $Capture.Measure } else { $null }
    $receiveTick = if ($measure) { [Diagnostics.Stopwatch]::GetTimestamp() } else { 0 }
    if ($script:InstallerDiagnosticReadProcess -ne $Process.Id) {
        # Match the handle's async mode, but disable managed prefetch: availability
        # snapshots describe kernel bytes, not an additional FileStream buffer.
        $stdout = $Process.StandardOutput.BaseStream
        $stderr = $Process.StandardError.BaseStream
        try {
            $script:InstallerDiagnosticReadStreams = @(
                [IO.FileStream]::new($stdout.SafeFileHandle, [IO.FileAccess]::Read, 1, $stdout.IsAsync),
                [IO.FileStream]::new($stderr.SafeFileHandle, [IO.FileAccess]::Read, 1, $stderr.IsAsync)
            )
        } catch {
            Write-InstallerDiagnosticsWarning -DeferPresentation
            $script:InstallerDiagnosticReadStreams = @($stdout, $stderr)
        }
        $script:InstallerDiagnosticReadProcess = $Process.Id
        $script:InstallerDiagnosticFinalSnapshots = $null
    }
    $copied = 0
    $channels = @(
        @{ Stream = $script:InstallerDiagnosticReadStreams[0]; Path = $(if ($Capture) { $Capture.Stdout }); Presentation = $script:InstallerDiagnosticPresentation[0] },
        @{ Stream = $script:InstallerDiagnosticReadStreams[1]; Path = $(if ($Capture) { $Capture.Stderr }); Presentation = $script:InstallerDiagnosticPresentation[1] }
    )
    if ($Final -and $null -eq $script:InstallerDiagnosticFinalSnapshots) {
        # Freeze both native ranges once. Replay never re-samples descendant output.
        $script:InstallerDiagnosticFinalSnapshots = @(0, 0)
        for ($index = 0; $index -lt $channels.Count; $index++) {
            try {
                $script:InstallerDiagnosticFinalSnapshots[$index] =
                    [InstallerDiagnosticsPipeV1]::Available($channels[$index].Stream.SafeFileHandle)
            } catch { Write-InstallerDiagnosticsWarning -DeferPresentation }
        }
    }
    for ($index = 0; $index -lt $channels.Count; $index++) {
        $channel = $channels[$index]
        $state = $channel.Presentation
        try {
            $tick = if ($measure) { [Diagnostics.Stopwatch]::GetTimestamp() } else { 0 }
            $remaining = if ($null -ne $script:InstallerDiagnosticFinalSnapshots) {
                $script:InstallerDiagnosticFinalSnapshots[$index]
            } else { [InstallerDiagnosticsPipeV1]::Available($channel.Stream.SafeFileHandle) }
            if ($measure) {
                $measure.available_ticks += [Diagnostics.Stopwatch]::GetTimestamp() - $tick
                $measure.polls += 1
            }
            if ($null -eq $script:InstallerDiagnosticFinalSnapshots) { $remaining = [Math]::Min($remaining, 8192) }
            $buffer = New-Object byte[] 8192
            while ($remaining -gt 0) {
                $tick = if ($measure) { [Diagnostics.Stopwatch]::GetTimestamp() } else { 0 }
                $count = $channel.Stream.Read($buffer, 0, [Math]::Min($remaining, $buffer.Length))
                if ($measure) {
                    $measure.read_ticks += [Diagnostics.Stopwatch]::GetTimestamp() - $tick
                    $measure.bytes += $count
                }
                if ($count -eq 0) { break }
                $remaining -= $count
                if ($null -ne $script:InstallerDiagnosticFinalSnapshots) {
                    $script:InstallerDiagnosticFinalSnapshots[$index] = $remaining
                }
                $copied += $count
                if (-not $state.Direct -and -not $state.RawFailed) {
                    $tick = if ($measure) { [Diagnostics.Stopwatch]::GetTimestamp() } else { 0 }
                    try {
                        if (-not $channel.Path) {
                            if ($state.RawPath) { throw 'Raw destination disappeared.' }
                            $state.Direct = $true
                        } else {
                            if ($state.RawPath -and $state.RawPath -cne $channel.Path) {
                                throw 'Raw destination changed.'
                            }
                            Test-InstallerDiagnosticsPath $channel.Path
                            $file = [IO.File]::Open($channel.Path, 'Append', 'Write', 'ReadWrite,Delete')
                            try {
                                if ($file.Length -ne $state.Recorded) { throw 'Raw range changed.' }
                                $file.Write($buffer, 0, $count)
                                $file.Flush()
                                $state.RawPath = $channel.Path
                                $state.Recorded += $count
                            } finally { $file.Dispose() }
                        }
                    } catch {
                        $state.RawFailed = $true
                        if ($state.Cursor -eq $state.Recorded) {
                            # Already-enqueued data stays ahead of the direct tail.
                            $state.Direct = $true
                        } else {
                            # Keep replaying the known prefix; never jump a gap.
                            $state.Loss = 'raw-append-failed-with-backlog'
                        }
                        Write-InstallerDiagnosticsWarning -DeferPresentation
                    }
                    if ($measure) { $measure.store_ticks += [Diagnostics.Stopwatch]::GetTimestamp() - $tick }
                }
                $tick = if ($measure) { [Diagnostics.Stopwatch]::GetTimestamp() } else { 0 }
                try {
                    if ($state.Direct) {
                        $length = $state.Decoder.GetChars($buffer, 0, $count, $state.Characters, 0, $false)
                        $state.Output.Add($state.Characters, $length, $false)
                    } elseif ($state.RawFailed) {
                        $state.InputLost += $count
                    } elseif ($state.Recorded - $state.Cursor -eq $count -and
                        $state.Output.CanAccept($state.Characters.Length)) {
                        # The recorded range is already in this owned buffer.
                        # Read it back only when an earlier backlog prevents enqueue.
                        $length = $state.Decoder.GetChars($buffer, 0, $count, $state.Characters, 0, $false)
                        $state.Output.Add($state.Characters, $length, $false)
                        $state.Cursor += $count
                    }
                } catch {
                    $state.Loss = 'presentation-decoding-failed'
                    $state.Output.Disable('presentation-decoding-failed')
                }
                if ($measure) { $measure.present_ticks += [Diagnostics.Stopwatch]::GetTimestamp() - $tick }
            }
            if ($null -ne $script:InstallerDiagnosticFinalSnapshots -and $remaining -eq 0) {
                $state.Final = $true
            }
        } catch { Write-InstallerDiagnosticsWarning -DeferPresentation }
    }
    if (-not $script:InstallerDiagnosticClock -or (Get-InstallerDiagnosticPresentationRemaining) -gt 0) {
        $tick = if ($measure) { [Diagnostics.Stopwatch]::GetTimestamp() } else { 0 }
        $null = Update-InstallerDiagnosticPresentation
        if ($measure) {
            $measure['replay_ticks'] = [long]$measure['replay_ticks'] + [Diagnostics.Stopwatch]::GetTimestamp() - $tick
        }
    }
    Save-InstallerDiagnosticPresentationState
    if ($measure) {
        $measure['receive_ticks'] = [long]$measure['receive_ticks'] + [Diagnostics.Stopwatch]::GetTimestamp() - $receiveTick
    }
    return $copied
}

function Wait-InstallerDiagnosticChild($Process, $Capture, [int]$Seconds) {
    $script:InstallerDiagnosticClock = [Diagnostics.Stopwatch]::StartNew()
    $script:InstallerDiagnosticBudgetMilliseconds = [long]$Seconds * 1000
    if (-not $script:InstallerDiagnosticPipeReady) { return $Process.WaitForExit($Seconds * 1000) }
    $timer = $script:InstallerDiagnosticClock
    while ($true) {
        $exited = $Process.HasExited
        $copied = Receive-InstallerDiagnosticCapture $Process $Capture -Final:$exited
        if ($exited) {
            Complete-InstallerDiagnosticPresentation
            return $true
        }
        if ($timer.Elapsed.TotalSeconds -ge $Seconds) {
            Stop-InstallerDiagnosticPresentation 'presentation-deadline'
            return $false
        }
        if ($copied -eq 0) { Start-Sleep -Milliseconds 10 }
    }
}

function Save-InstallerDiagnosticCapture($Capture) {
    if ($script:InstallerDiagnosticWarningPending) { Write-InstallerDiagnosticsWarning }
    Complete-InstallerDiagnosticPresentation
    if (-not $Capture) { return }
    try {
        Test-InstallerDiagnosticsPath $Capture.Log
        foreach ($path in @($Capture.Stdout, $Capture.Stderr)) {
            Test-InstallerDiagnosticsPath $path
            $reader = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite,Delete')
            try {
                # A fixed byte snapshot cannot wait on a descendant's EOF.
                $length = $reader.Length
                $bytes = New-Object byte[] $length
                $offset = 0
                while ($offset -lt $length) {
                    $count = $reader.Read($bytes, $offset, [int]($length - $offset))
                    if ($count -eq 0) { break }
                    $offset += $count
                }
                $encoding = if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -eq '1') {
                    [Text.Encoding]::UTF8
                } else { [Console]::OutputEncoding }
                $memory = [IO.MemoryStream]::new($bytes, 0, $offset, $false)
                $decoder = [IO.StreamReader]::new($memory, $encoding, $true)
                try { $text = $decoder.ReadToEnd() } finally { $decoder.Dispose() }
                $text = ConvertTo-InstallerDiagnosticText $text
                [IO.File]::AppendAllText($Capture.Log, ($text + "`n"), [Text.UTF8Encoding]::new($false))
            } finally { $reader.Dispose() }
        }
    } catch { Write-InstallerDiagnosticsWarning }
}

function Remove-CompletedInstallerDiagnostics([string]$Root) {
    foreach ($directory in [IO.Directory]::EnumerateDirectories($Root)) {
        try {
            if ([IO.Path]::GetFileName($directory) -cnotmatch '^[a-f0-9]{32}$') { continue }
            Test-InstallerDiagnosticsPath $directory
            $receipt = [IO.Path]::Combine($directory, 'attempt.json')
            Test-InstallerDiagnosticsPath $receipt
            $identity = [IO.File]::ReadAllText($receipt) | Microsoft.PowerShell.Utility\ConvertFrom-Json
            if ($identity.schema -cne 'copilot-extensions.installer-diagnostics' -or
                $identity.attempt -cne [IO.Path]::GetFileName($directory)) { continue }
            $path = [IO.Path]::Combine($directory, 'completed.json')
            if (-not [IO.File]::Exists($path)) { continue }
            Test-InstallerDiagnosticsPath $path
            $record = [IO.File]::ReadAllText($path) | Microsoft.PowerShell.Utility\ConvertFrom-Json
            if ([DateTime]::Parse($record.completed).ToUniversalTime() -ge [DateTime]::UtcNow.AddDays(-30)) { continue }
            $settled = $true
            foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($directory)) {
                Test-InstallerDiagnosticsPath $entry
                if ([IO.Directory]::Exists($entry) -or $entry.EndsWith('.pending') -or
                    ($entry.EndsWith('.start.json') -and -not [IO.File]::Exists($entry.Replace('.start.json', '.completed.json')))) {
                    $settled = $false
                }
            }
            if ($settled) { [IO.Directory]::Delete($directory, $true) }
        } catch { Write-InstallerDiagnosticsWarning }
    }
}
