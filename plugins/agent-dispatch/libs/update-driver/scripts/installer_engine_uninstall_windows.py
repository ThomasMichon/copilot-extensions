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
$raw=@(Get-CimInstance Win32_Process -ErrorAction Stop)
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
$ancestors=@($inspector)
$current=$inspector
foreach ($level in 1..4) {
    $parent=@($raw | Where-Object ProcessId -eq $current)
    if ($parent.Count -ne 1) { break }
    $current=$parent[0].ParentProcessId
    $ancestors+=@($current)
}
$rows=@($raw | ForEach-Object {
    $image=([string]$_.ExecutablePath).Replace('\','/').ToLowerInvariant()
    $command=([string]$_.CommandLine).Replace('\','/').ToLowerInvariant()
    $candidate=($_.ProcessId -in $ancestors -or $_.ProcessId -in $rooted -or
        $_.Name -match '^(bash|sh)(\.exe)?$' -or
        ($_.ParentProcessId -in $ancestors -and $_.Name -match '^(bash|sh)(\.exe)?$') -or
        $image.StartsWith($prefix+'/') -or
        $command.Contains($prefix) -or $command.Contains($module) -or
        ($_.Name -match '^(python|pythonw|pypy)[0-9.]*\.exe$' -and (-not $image -or -not $command)))
    if (-not $candidate) { return }
    $row=$_
    try { $owner=Invoke-CimMethod -InputObject $row -MethodName GetOwnerSid -ErrorAction Stop } catch {
        if (-not (Get-Process -Id $row.ProcessId -ErrorAction SilentlyContinue)) { return }; throw
    }
    if ($owner.ReturnValue -ne 0) { throw 'Cannot attribute candidate process ownership' }
    if ($descendants -and $row.ProcessId -in $rooted -and $owner.Sid -ne $sid) {
        throw 'A rooted descendant belongs to another user'
    }
    if ($owner.Sid -eq $sid) {
        if (-not $image -or -not $command) { throw 'Cannot inspect a current-user candidate process' }
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
