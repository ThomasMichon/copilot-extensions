# Native, non-authoritative installer diagnostics. No runtime Python is required.
$script:InstallerDiagnosticWarned = $false
$script:InstallerDiagnostic = $null
$script:InstallerDiagnosticPipeReady = $false
$script:InstallerDiagnosticWarningPending = $false
$script:InstallerDiagnosticPresentation = @()
$script:InstallerDiagnosticReadStreams = @()
$script:InstallerDiagnosticReadProcess = 0
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
    try { [Console]::Error.WriteLine('WARNING: installer diagnostics unavailable; continuing with missing diagnostics.') } catch {}
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
            try { [Console]::Error.WriteLine("Installer diagnostics: $directory") } catch {}
        }
    } catch { $script:InstallerDiagnostic = $null; Write-InstallerDiagnosticsWarning }
}

function Complete-InstallerDiagnostics([int]$ExitCode) {
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
        $encoding = if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -eq '1') {
            [Text.Encoding]::UTF8
        } else { [Console]::OutputEncoding }
        $script:InstallerDiagnosticPresentation = @(
            @{ Output = [Console]::Out; Decoder = $encoding.GetDecoder()
                Characters = (New-Object char[] ($encoding.GetMaxCharCount(8192))); Final = $false },
            @{ Output = [Console]::Error; Decoder = $encoding.GetDecoder()
                Characters = (New-Object char[] ($encoding.GetMaxCharCount(8192))); Final = $false }
        )
        $script:InstallerDiagnosticReadStreams = @()
        $script:InstallerDiagnosticReadProcess = 0
        if (-not ('InstallerDiagnosticsPipeV1' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class InstallerDiagnosticsPipeV1 {
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool PeekNamedPipe(SafeFileHandle handle, IntPtr buffer,
        uint size, IntPtr read, out uint available, IntPtr remaining);
    public static int Available(SafeFileHandle handle) {
        uint available;
        if (PeekNamedPipe(handle, IntPtr.Zero, 0, IntPtr.Zero, out available, IntPtr.Zero))
            return checked((int)available);
        int error = Marshal.GetLastWin32Error();
        if (error == 109 || error == 232) return 0;
        throw new Win32Exception(error);
    }
}
'@
        }
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
    }
    $copied = 0
    $channels = @(
        @{ Stream = $script:InstallerDiagnosticReadStreams[0]; Path = $(if ($Capture) { $Capture.Stdout }); Presentation = $script:InstallerDiagnosticPresentation[0] },
        @{ Stream = $script:InstallerDiagnosticReadStreams[1]; Path = $(if ($Capture) { $Capture.Stderr }); Presentation = $script:InstallerDiagnosticPresentation[1] }
    )
    foreach ($channel in $channels) {
        try {
            $tick = if ($measure) { [Diagnostics.Stopwatch]::GetTimestamp() } else { 0 }
            $remaining = [InstallerDiagnosticsPipeV1]::Available($channel.Stream.SafeFileHandle)
            if ($measure) {
                $measure.available_ticks += [Diagnostics.Stopwatch]::GetTimestamp() - $tick
                $measure.polls += 1
            }
            if (-not $Final) { $remaining = [Math]::Min($remaining, 8192) }
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
                $copied += $count
                if ($channel.Path) {
                    $tick = if ($measure) { [Diagnostics.Stopwatch]::GetTimestamp() } else { 0 }
                    try {
                        Test-InstallerDiagnosticsPath $channel.Path
                        $file = [IO.File]::Open($channel.Path, 'Append', 'Write', 'ReadWrite,Delete')
                        try { $file.Write($buffer, 0, $count); $file.Flush() } finally { $file.Dispose() }
                    } catch {
                        # Presentation and pipe draining do not depend on storage.
                        Write-InstallerDiagnosticsWarning -DeferPresentation
                    }
                    if ($measure) { $measure.store_ticks += [Diagnostics.Stopwatch]::GetTimestamp() - $tick }
                }
                $tick = if ($measure) { [Diagnostics.Stopwatch]::GetTimestamp() } else { 0 }
                try {
                    if (-not $channel.Presentation.Final) {
                        $characters = $channel.Presentation.Characters
                        $length = $channel.Presentation.Decoder.GetChars($buffer, 0, $count, $characters, 0, $false)
                        $channel.Presentation.Output.Write($characters, 0, $length)
                        $channel.Presentation.Output.Flush()
                    }
                } catch {}
                if ($measure) { $measure.present_ticks += [Diagnostics.Stopwatch]::GetTimestamp() - $tick }
            }
            if ($Final -and -not $channel.Presentation.Final) {
                $channel.Presentation.Final = $true
                try {
                    $characters = $channel.Presentation.Characters
                    $length = $channel.Presentation.Decoder.GetChars([byte[]]@(), 0, 0, $characters, 0, $true)
                    $channel.Presentation.Output.Write($characters, 0, $length)
                    $channel.Presentation.Output.Flush()
                } catch {}
            }
        } catch { Write-InstallerDiagnosticsWarning -DeferPresentation }
    }
    return $copied
}

function Wait-InstallerDiagnosticChild($Process, $Capture, [int]$Seconds) {
    if (-not $script:InstallerDiagnosticPipeReady) { return $Process.WaitForExit($Seconds * 1000) }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        $exited = $Process.HasExited
        $copied = Receive-InstallerDiagnosticCapture $Process $Capture -Final:$exited
        if ($exited) { return $true }
        if ($timer.Elapsed.TotalSeconds -ge $Seconds) { return $false }
        if ($copied -eq 0) { Start-Sleep -Milliseconds 10 }
    }
}

function Save-InstallerDiagnosticCapture($Capture) {
    if ($script:InstallerDiagnosticWarningPending) { Write-InstallerDiagnosticsWarning }
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
