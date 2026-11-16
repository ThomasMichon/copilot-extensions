"""Bounded fault-module metadata for Python faults owned by one TTY witness."""

from __future__ import annotations

import json
import re
import shutil
import subprocess


def _fault_pids(directory):
    pids = {}
    for index, path in enumerate(directory.glob("manager-*.fault.log")):
        if index >= 16:
            break
        match = re.fullmatch(r"manager-([0-9]{1,10})\.fault\.log", path.name)
        if match is None or path.is_symlink() or not path.is_file():
            continue
        pid = int(match[1])
        if not 0 < pid <= 0xFFFFFFFF:
            continue
        with path.open("rb") as stream:
            if b"Windows fatal exception: access violation" in stream.read(256):
                info = path.stat()
                born = getattr(info, "st_birthtime_ns", None)
                if born is None:
                    born = info.st_ctime_ns
                pids[pid] = born // 100 + 116444736000000000
    return pids


def _validated_events(value, pids, minimum_birth):
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("Invalid native fault event envelope")
    expected = {"pid", "process_birth", "module", "module_version", "exception", "offset"}
    for row in value:
        if not isinstance(row, dict) or set(row) != expected:
            raise ValueError("Unexpected native fault metadata")
        if type(row["pid"]) is not int or row["pid"] not in pids:
            raise ValueError("Native fault metadata belongs to another process")
        if (
            type(row["process_birth"]) is not int
            or not minimum_birth <= row["process_birth"] <= pids[row["pid"]]
        ):
            raise ValueError("Native fault metadata belongs to another process incarnation")
        for key, pattern in (
            ("module", r"[A-Za-z0-9_.-]{1,128}"),
            ("module_version", r"[0-9.]{0,64}"),
            ("exception", r"c0000005"),
            ("offset", r"[0-9a-f]{1,16}"),
        ):
            if not isinstance(row[key], str) or re.fullmatch(pattern, row[key]) is None:
                raise ValueError("Invalid native fault metadata field")
    return value


def capture_fault_events(directory, diagnostics, env, project, label, started, run):
    from plugin_test_containment import UnreapedProcessError

    pids = _fault_pids(directory)
    if not pids:
        return
    script = r"""
$ErrorActionPreference = 'Stop'
$owned = @{__PIDS__}
$minimumBirth = [UInt64]__MINIMUM_BIRTH__
$since = [DateTimeOffset]::FromUnixTimeMilliseconds(__START__).LocalDateTime
try {
    $events = @(Get-WinEvent -FilterHashtable @{
        LogName = 'Application'; Id = 1000; StartTime = $since
    } -MaxEvents 32 -ErrorAction Stop)
} catch {
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {
        throw 'Application error metadata unavailable'
    }
    $events = @()
}
$rows = @()
foreach ($event in $events) {
    $data = @{}
    foreach ($item in ([xml]$event.ToXml()).Event.EventData.Data) {
        $data[[string]$item.Name] = [string]$item.InnerText
    }
    try { $faultPid = [uint32]$data['ProcessId'] } catch { continue }
    if (-not $owned.ContainsKey([string]$faultPid) -or $data['AppName'] -notmatch '^python(w)?\.exe$') { continue }
    try { $birth = [UInt64]$data['ProcessCreationTime'] } catch { continue }
    if ($birth -lt $minimumBirth -or $birth -gt $owned[[string]$faultPid]) { continue }
    $code = ([string]$data['ExceptionCode'] -replace '^0x', '').ToLowerInvariant()
    if ($code -ne 'c0000005') { continue }
    $version = [string]$data['ModuleVersion']
    if ($version -notmatch '^[0-9.]{1,64}$') { $version = '' }
    $rows += @{
        pid = $faultPid
        process_birth = $birth
        module = [IO.Path]::GetFileName([string]$data['ModuleName'])
        module_version = $version
        exception = $code
        offset = ([string]$data['FaultingOffset'] -replace '^0x', '').ToLowerInvariant()
    }
}
ConvertTo-Json -InputObject @($rows | Select-Object -First 16) -Compress
""".replace("__PIDS__", ";".join(
        f"'{pid}'=[UInt64]{born}" for pid, born in sorted(pids.items())
    )).replace(
        "__START__", str(int((started - 1) * 1000)),
    ).replace(
        "__MINIMUM_BIRTH__", str(int((started - 1) * 10000000) + 116444736000000000),
    )
    minimum_birth = int((started - 1) * 10000000) + 116444736000000000
    result = {"owned_fault_pids": sorted(pids), "query_complete": False, "events": []}
    try:
        text = run(
            [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            env, project, diagnostics, label + "-windows-error-events", timeout=5,
        )
        result.update(
            query_complete=True, events=_validated_events(json.loads(text), pids, minimum_birth),
        )
    except UnreapedProcessError:
        raise
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        result["unavailable"] = type(error).__name__
    (diagnostics / (label + ".native-error-events.json")).write_text(
        json.dumps(result, indent=2), encoding="utf-8",
    )
