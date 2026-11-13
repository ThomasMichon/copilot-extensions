"""Read-only native Windows consumer census; the caller owns execution policy."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
from typing import Any


def inspect_processes(
    root: Path, shell: str, module: str, run: Callable[[list[str]], str], *,
    descendants: bool = False,
) -> list[dict[str, Any]]:
    prefix = str(root).replace("\\", "/").casefold()
    quote = lambda value: "'" + value.replace("'", "''") + "'"  # noqa: E731
    script = f"$prefix={quote(prefix)};$module={quote(module)};$inspector={os.getpid()}\n"
    script += f"$descendants=${str(descendants).lower()}\n"
    script += r"""
$ErrorActionPreference='Stop'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$nativeOwnerAvailable=$false
try {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
public static class CensusTokenOwner {
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool GetProcessTimes(IntPtr process, out long created,
        out long exited, out long kernel, out long user);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);
    public static string Read(int pid, long expectedCreation) {
        IntPtr process = OpenProcess(0x1000, false, pid);
        if (process == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        IntPtr token = IntPtr.Zero;
        try {
            long created, exited, kernel, user;
            if (!GetProcessTimes(process, out created, out exited, out kernel, out user))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            // CIM retains microseconds; the native identity has 100ns precision.
            if (created - created % 10 != expectedCreation)
                throw new InvalidOperationException("Process identity changed during inspection");
            if (!OpenProcessToken(process, 8, out token))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            using (WindowsIdentity identity = new WindowsIdentity(token)) {
                if (identity.User == null)
                    throw new InvalidOperationException("Process token has no user identity");
                return identity.User.Value;
            }
        } finally {
            if (token != IntPtr.Zero) CloseHandle(token);
            CloseHandle(process);
        }
    }
}
'@ -ErrorAction Stop
    $nativeOwnerAvailable=$true
} catch {
    # Restricted hosts retain the existing CIM ownership observation.
}
function Test-CensusProcessExited([int]$processId) {
    try { $process=[Diagnostics.Process]::GetProcessById($processId) } catch {
        if ($_.Exception.InnerException -is [ArgumentException]) { return $true }
        throw
    }
    try { return $process.HasExited } finally { $process.Dispose() }
}
function Get-CensusProcessOwner($row) {
    if ($nativeOwnerAvailable) {
        try {
            $nativeSid=[CensusTokenOwner]::Read(
                $row.ProcessId, ([datetime]$row.CreationDate).ToFileTimeUtc())
            return [pscustomobject]@{Sid=$nativeSid;ReturnValue=0}
        } catch {
            if ($_.Exception.InnerException -isnot [ComponentModel.Win32Exception]) { throw }
        }
    }
    try { $owner=Invoke-CimMethod -InputObject $row -MethodName GetOwnerSid -ErrorAction Stop } catch {
        if (-not (Get-Process -Id $row.ProcessId -ErrorAction SilentlyContinue)) { return }; throw
    }
    if ($owner.ReturnValue -ne 0) {
        if (Test-CensusProcessExited $row.ProcessId) { return }
        throw ("Cannot attribute candidate process ownership (pid={0}, result={1})" -f
            $row.ProcessId,$owner.ReturnValue)
    }
    return $owner
}
function Get-CensusProcessMetadata($row) {
    $image=([string]$row.ExecutablePath).Replace('\','/').ToLowerInvariant()
    $command=([string]$row.CommandLine).Replace('\','/').ToLowerInvariant()
    if (-not $image -or -not $command) {
        $fresh=@(Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $row.ProcessId) -ErrorAction Stop)
        if ($fresh.Count -gt 1) { throw 'Candidate process refresh is ambiguous' }
        if ($fresh.Count -eq 1) {
            if ($fresh[0].ProcessId -ne $row.ProcessId -or
                ([datetime]$fresh[0].CreationDate).ToFileTimeUtc() -ne
                    ([datetime]$row.CreationDate).ToFileTimeUtc()) {
                throw 'Process identity changed during inspection'
            }
            $row=$fresh[0]
            $image=([string]$row.ExecutablePath).Replace('\','/').ToLowerInvariant()
            $command=([string]$row.CommandLine).Replace('\','/').ToLowerInvariant()
        }
        if (-not $image -or -not $command) {
            if (Test-CensusProcessExited $row.ProcessId) { return }
            throw ("Cannot inspect a current-user candidate process (pid={0})" -f $row.ProcessId)
        }
    }
    return $row
}
function Test-CensusCandidate($row,$ancestors,$rooted) {
    $image=([string]$row.ExecutablePath).Replace('\','/').ToLowerInvariant()
    $command=([string]$row.CommandLine).Replace('\','/').ToLowerInvariant()
    return ($row.ProcessId -in $ancestors -or $row.ProcessId -in $rooted -or
        $row.Name -match '^(bash|sh)(\.exe)?$' -or
        ($row.ParentProcessId -in $ancestors -and $row.Name -match '^(bash|sh)(\.exe)?$') -or
        $image.StartsWith($prefix+'/') -or
        $command.Contains($prefix) -or $command.Contains($module) -or
        ($row.Name -match '^(python|pythonw|pypy)[0-9.]*\.exe$' -and (-not $image -or -not $command)))
}
$raw=@(Get-CimInstance Win32_Process -ErrorAction Stop)
$ancestors=@($inspector)
$current=$inspector
foreach ($level in 1..4) {
    $parent=@($raw | Where-Object ProcessId -eq $current)
    if ($parent.Count -ne 1) { break }
    $current=$parent[0].ParentProcessId
    $ancestors+=@($current)
}
$rooted=@()
$exited=@{}
if ($descendants) {
    # Recover roots before closure; exited rows remain as ancestry evidence.
    $raw=@($raw | ForEach-Object {
        $row=$_
        if (Test-CensusCandidate $row $ancestors $rooted) {
            if (-not $row.ExecutablePath -or -not $row.CommandLine) {
                $owner=Get-CensusProcessOwner $row
                if (-not $owner) {
                    $exited[$row.ProcessId]=$true
                } elseif ($owner.Sid -eq $sid) {
                    $fresh=Get-CensusProcessMetadata $row
                    if ($fresh) { $row=$fresh } else { $exited[$row.ProcessId]=$true }
                }
            }
        }
        $row
    })
}
$rooted=@($raw | Where-Object {
    ([string]$_.ExecutablePath).Replace('\','/').ToLowerInvariant().StartsWith($prefix+'/') -or
    ([string]$_.CommandLine).Replace('\','/').ToLowerInvariant().Contains($prefix)
} | ForEach-Object { $_.ProcessId })
if ($descendants) {
    foreach ($level in 1..64) {
        $added=@($raw | Where-Object {
            $_.ParentProcessId -in $rooted -and $_.ProcessId -notin $rooted
        } | ForEach-Object { $_.ProcessId })
        if ($added.Count -eq 0) { break }
        $rooted+=@($added)
        if ($level -eq 64) { throw 'Rooted process descendants exceed the inspection budget' }
    }
}
$rows=@($raw | ForEach-Object {
    $row=$_
    if ($exited.ContainsKey($row.ProcessId)) { return }
    if (-not (Test-CensusCandidate $row $ancestors $rooted)) { return }
    $owner=Get-CensusProcessOwner $row
    if (-not $owner) { return }
    if ($descendants -and $row.ProcessId -in $rooted -and $owner.Sid -ne $sid) {
        throw 'A rooted descendant belongs to another user'
    }
    if ($owner.Sid -eq $sid) {
        $row=Get-CensusProcessMetadata $row
        if (-not $row) { return }
        $created=$null
        if ($descendants) {
            try {
                $native=[Diagnostics.Process]::GetProcessById($row.ProcessId)
                try { $created=$native.StartTime.ToUniversalTime().ToFileTimeUtc() } finally { $native.Dispose() }
            } catch {
                if (-not (Get-Process -Id $row.ProcessId -ErrorAction SilentlyContinue)) { return }; throw
            }
            # CIM has microsecond precision; retain the exact native 100ns identity.
            if ($created - ($created % 10) -ne ([datetime]$row.CreationDate).ToFileTimeUtc()) {
                throw 'Process identity changed during inspection'
            }
        }
        [pscustomobject]@{pid=$row.ProcessId;ppid=$row.ParentProcessId;image=$row.ExecutablePath;
            command=$row.CommandLine;created=$created;
            inspectionHost=($row.ProcessId -eq $PID)}
    }
})
ConvertTo-Json -InputObject $rows -Compress
"""
    return json.loads(run([shell, "-NoProfile", "-NonInteractive", "-Command", script]))
