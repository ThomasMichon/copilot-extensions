"""Bounded native scheduler adapter; exact task custody stays with enrollment."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys

from agent_procutil.owned_process import run_caller_owned
from agent_procutil.owned_budget import OwnedExecutionBudget


class NativeTask:
    def __init__(self, name: str, owner: str):
        self.name = name
        self.owner = owner

    def call(self, operation: str, *, expected: str = "", definition: str = "") -> dict:
        if sys.platform != "win32" or operation not in {"read", "create", "replace", "enable", "disable", "remove"}:
            raise ValueError("native-health-job-unsupported")
        request = base64.b64encode(json.dumps({
            "name": self.name, "owner": self.owner, "operation": operation,
            "expected": expected, "definition": definition,
        }, ensure_ascii=True).encode("ascii")).decode("ascii")
        script = _SCRIPT.replace("__REQUEST__", request)
        command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        if len(command) > 30000:
            raise ValueError("native-health-task-definition-oversize")
        host = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        timeout = 60 if operation in {"disable", "remove"} else 20
        completed = run_caller_owned(
            [str(host), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
            timeout=timeout, budget=OwnedExecutionBudget.from_timeout(timeout, cleanup_reserve=5),
            max_stdout_bytes=128 * 1024, max_stderr_bytes=4096,
        )
        if completed.returncode != 0:
            raise RuntimeError("native-health-task-operation-unconfirmed")
        result = json.loads(completed.stdout)
        if type(result) is not dict:
            raise RuntimeError("native-health-task-response-invalid")
        return result


_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$r = [Text.Encoding]::ASCII.GetString([Convert]::FromBase64String('__REQUEST__')) | ConvertFrom-Json
if ($r.name -cnotmatch '^ce-health-[a-f0-9]{32}$' -or
    $r.owner -cnotmatch '^urn:copilot-extensions:health-job:[a-f0-9]{64}$') {
    throw 'Invalid task identity'
}
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Read-Task {
    try { return $folder.GetTask($r.name) }
    catch {
        if ($_.Exception.GetBaseException().HResult -eq -2147024894) { return $null }
        throw
    }
}
function Check-Owner($task) {
    [xml]$xml = $task.Xml
    $ns = New-Object Xml.XmlNamespaceManager($xml.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    if ($xml.SelectSingleNode('/t:Task/t:RegistrationInfo/t:Description', $ns).InnerText -cne $r.owner) {
        throw 'Foreign task definition'
    }
}
function Digest([string]$text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Disabled-DefinitionDigest([string]$text) {
    [xml]$xml = $text
    $ns = New-Object Xml.XmlNamespaceManager($xml.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    $enabled = $xml.SelectSingleNode('/t:Task/t:Settings/t:Enabled', $ns)
    if ($null -ne $enabled) { $null = $enabled.ParentNode.RemoveChild($enabled) }
    return Digest $xml.OuterXml
}
$task = Read-Task
if ($r.operation -ne 'read') {
    if ($null -eq $task) {
        if ($r.expected -cne 'absent') { throw 'Task changed before mutation' }
    } else {
        Check-Owner $task
        if ((Digest $task.Xml) -cne $r.expected) { throw 'Task changed before mutation' }
    }
    if ($r.operation -in @('create', 'replace')) {
        [xml]$candidate = $r.definition
        $check = New-Object PSObject -Property @{ Xml = $candidate.OuterXml }
        Check-Owner $check
        if ($r.operation -eq 'create') {
            if ($null -ne $task) { throw 'Task already exists' }
            $flags = 2
        } else {
            if ($null -eq $task -or $task.Enabled -or $task.GetInstances(0).Count -ne 0) {
                throw 'Replacement requires a settled disabled task'
            }
            $flags = 4
        }
        $null = $folder.RegisterTask($r.name, $candidate.OuterXml, $flags, $sid, $null, 3, $null)
    } elseif ($r.operation -in @('disable', 'remove')) {
        if ($null -ne $task) {
            $settlingDigest = Disabled-DefinitionDigest $task.Xml
            $task.Enabled = $false
            $task = Read-Task
            if ($null -eq $task -or $task.Enabled) { throw 'Task disable not confirmed' }
            Check-Owner $task
            $deadline = [DateTime]::UtcNow.AddSeconds(50)
            while ($task.GetInstances(0).Count -ne 0 -or $task.LastTaskResult -eq 267009) {
                if ([DateTime]::UtcNow -ge $deadline) { throw 'Task instances did not settle' }
                Start-Sleep -Milliseconds 50
                $task = Read-Task
                if ($null -eq $task) { throw 'Task disappeared during settlement' }
                Check-Owner $task
                if ((Disabled-DefinitionDigest $task.Xml) -cne $settlingDigest) { throw 'Task changed during settlement' }
            }
            if ($task.Enabled -or (Disabled-DefinitionDigest $task.Xml) -cne $settlingDigest) { throw 'Task changed during settlement' }
            # Only these installed-root results attest whole child-Job settlement.
            # A native hard stop or cleanup-unknown result never proves shutdown.
            if ($task.LastTaskResult -notin @(0, 2, 124, 267011)) {
                throw 'Owned collector termination is unconfirmed'
            }
            if ($r.operation -eq 'remove') { $folder.DeleteTask($r.name, 0) }
        }
    } elseif ($r.operation -eq 'enable') {
        if ($null -eq $task) { throw 'Task disappeared' }
        $task.Enabled = $true
    }
}
$task = Read-Task
if ($null -eq $task) {
    @{ configured = $false; xml = $null; digest = 'absent'; instances = @(); enabled = $false; account_sid = $sid } | ConvertTo-Json -Compress
} else {
    $lastRun = $null
    $nextRun = $null
    try {
        # COM can return UTC-valued LastRunTime but local NextRunTime, both unspecified-kind.
        $timing = Get-ScheduledTaskInfo -TaskPath '\' -TaskName $r.name
        $lastRun = $timing.LastRunTime.ToUniversalTime().ToString('o')
        $nextRun = $timing.NextRunTime.ToUniversalTime().ToString('o')
    } catch {
        # Unavailable timing metadata must not block exact native withdrawal.
    }
    @{
        configured = $true; xml = $task.Xml; digest = (Digest $task.Xml)
        enabled = [bool]$task.Enabled; state = [int]$task.State
        instances = @($task.GetInstances(0) | ForEach-Object { $_.InstanceGuid })
        last_result = [long]$task.LastTaskResult
        last_run = $lastRun
        next_run = $nextRun
        account_sid = $sid
    } | ConvertTo-Json -Compress -Depth 4
}
"""
