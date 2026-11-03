"""Opt-in disposable Windows TaskScheduler qualification; NEVER run on a host.

Requires a fresh GitHub-hosted Windows job, AGENT_INDEX_NATIVE_HISTORICAL=1,
and pytest --basetemp beneath RUNNER_TEMP. Both fixed archived names must be
absent before creation. Uses the real Index task producer and production
retirement owner, without changing its matcher or replacing its native backend.
No workflow opt-in is added here. No Index server/engine is ever launched.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

REPO = Path(__file__).resolve().parents[3]
UNIT_FIXTURES = REPO / "worktree-manager/tests/test_historical_registrations.py"
NAMES = ("agent-index", "agent-index-engine")


def _admit_disposable_ci(tmp_path):
    if os.environ.get("AGENT_INDEX_NATIVE_HISTORICAL") != "1":
        pytest.skip("disposable native TaskScheduler qualification is explicitly opt-in")
    required = {
        "GITHUB_ACTIONS": "true", "CI": "true", "RUNNER_OS": "Windows",
        "RUNNER_ENVIRONMENT": "github-hosted",
    }
    if os.name != "nt" or any(os.environ.get(key) != value for key, value in required.items()):
        pytest.fail("REFUSED: native registration qualification requires GitHub-hosted Windows, never a self-hosted/operator machine")
    if not os.environ.get("ImageOS", "").lower().startswith("win") or not os.environ.get("ImageVersion"):
        pytest.fail("REFUSED: GitHub-hosted Windows image attribution is missing")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "RUNNER_NAME", "RUNNER_TEMP", "GITHUB_WORKSPACE"):
        if not os.environ.get(key):
            pytest.fail("REFUSED: fresh job attribution is missing " + key)
    if not os.environ["GITHUB_RUN_ID"].isdigit() or not os.environ["GITHUB_RUN_ATTEMPT"].isdigit():
        pytest.fail("REFUSED: invalid GitHub run attribution")
    if Path(os.environ["GITHUB_WORKSPACE"]).resolve() != REPO.resolve():
        pytest.fail("REFUSED: source is not this disposable job's checkout")
    temporary = Path(os.environ["RUNNER_TEMP"]).resolve()
    if tmp_path.resolve() == temporary or not tmp_path.resolve().is_relative_to(temporary):
        pytest.fail("REFUSED: set pytest --basetemp to a unique child of RUNNER_TEMP")
    host = shutil.which("pwsh")
    if not host:
        pytest.fail("native qualification requires the hosted image's PowerShell")
    return host


# This script only executes after both Python and native CI admission. Its
# ledger records successful fixture creation, not discovered machine resources.
# Cleanup requires unchanged full native contents and never stops/kills a task.
_NATIVE_FIXTURE = r"""
param([string]$Phase, [string]$Payload, [string]$Root, [string]$LedgerPath,
      [string]$FixtureId, [string]$EventName)
$ErrorActionPreference = 'Stop'
if ($env:AGENT_INDEX_NATIVE_HISTORICAL -cne '1' -or
    $env:GITHUB_ACTIONS -cne 'true' -or $env:CI -cne 'true' -or
    $env:RUNNER_OS -cne 'Windows' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or
    -not $env:ImageOS.StartsWith('win') -or -not $env:ImageVersion -or
    -not $env:GITHUB_RUN_ID -or -not $env:GITHUB_RUN_ATTEMPT -or -not $env:GITHUB_JOB) {
    throw 'REFUSED: not the explicitly admitted disposable GitHub-hosted Windows job'
}
$workspace = [IO.Path]::GetFullPath($env:GITHUB_WORKSPACE).TrimEnd('\')
if ([IO.Path]::GetFullPath($Payload) -ine (Join-Path $workspace 'plugins\agent-index')) {
    throw 'REFUSED: native fixture payload is not the job checkout'
}
$temp = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
foreach ($path in @($Root, $LedgerPath)) {
    if (-not [IO.Path]::GetFullPath($path).StartsWith($temp, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'REFUSED: native fixture state escaped RUNNER_TEMP'
    }
}
if ($FixtureId -notmatch '^[a-f0-9]{32}$' -or $EventName -cne ('Local\IndexHistoricalFixture-' + $FixtureId)) {
    throw 'REFUSED: invalid disposable fixture identity'
}
if ($Phase -cin @('create', 'edit-service', 'run-engine-waiter')) {
    $module = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules\ScheduledTasks\ScheduledTasks.psd1'
    if (-not (Test-Path -LiteralPath $module -PathType Leaf)) {
        throw 'Native Windows ScheduledTasks module is unavailable'
    }
    Import-Module -Name $module -ErrorAction Stop
}
. (Join-Path $Payload 'scripts\historical-registration-owner.ps1')
$names = @('agent-index', 'agent-index-engine')
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Read-Task([string]$Name) {
    if ($Name -cnotin $names) { throw 'Unknown native fixture task identity' }
    try { return $folder.GetTask($Name) }
    catch {
        $errorObject = $_.Exception
        while ($null -ne $errorObject) {
            if ($errorObject.HResult -eq -2147024894) { return $null }
            $errorObject = $errorObject.InnerException
        }
        throw
    }
}
function Read-Snapshot([string]$Name) {
    $task = Read-Task $Name
    if ($null -eq $task) { return $null }
    $security = $task.GetSecurityDescriptor(7)
    $descriptor = [Security.AccessControl.RawSecurityDescriptor]::new($security)
    return @{name=$Name; path=$task.Path; xml=$task.Xml; securityDescriptor=$security;
             ownerSid=$descriptor.Owner.Value; sid=$sid;
             user=[Security.Principal.WindowsIdentity]::GetCurrent().Name; state=[int]$task.State}
}
function Write-Ledger($Ledger) {
    [IO.File]::WriteAllText($LedgerPath, ($Ledger | ConvertTo-Json -Compress -Depth 12), [Text.UTF8Encoding]::new($false))
}
function Read-Ledger {
    if (-not (Test-Path -LiteralPath $LedgerPath -PathType Leaf)) { throw 'Fixture creation receipt is missing' }
    $ledger = Get-Content -LiteralPath $LedgerPath -Raw | ConvertFrom-Json
    if ($ledger.fixtureId -cne $FixtureId -or $ledger.runId -cne $env:GITHUB_RUN_ID -or
        $ledger.attempt -cne $env:GITHUB_RUN_ATTEMPT -or $ledger.job -cne $env:GITHUB_JOB -or
        $ledger.root -cne $Root -or $ledger.sid -cne $sid -or $ledger.names.Count -ne 2 -or
        $ledger.names[0] -cne $names[0] -or $ledger.names[1] -cne $names[1]) {
        throw 'Fixture creation receipt does not identify this exact job/root/user'
    }
    foreach ($entry in $ledger.owned) {
        if ($entry.name -cnotin $names) { throw 'Foreign fixture receipt entry' }
    }
    return $ledger
}
function Assert-ReceiptTask($Entry) {
    $snapshot = Read-Snapshot $Entry.name
    if ($null -ne $snapshot -and
        (Get-IndexHistoricalTaskFingerprint $snapshot) -cne $Entry.sha256) {
        throw ('REFUSED: fixture registration contents changed: ' + $Entry.name)
    }
    return $snapshot
}
$mutex = Enter-IndexStampLock -Scope 'Transaction' -LockRoot $Root -RequireGlobal
try {
    switch -CaseSensitive ($Phase) {
        'assert-absent' {
            if (Test-Path -LiteralPath $LedgerPath) { throw 'Fixture admission receipt already exists; job is not fresh' }
            foreach ($name in $names) {
                if ($null -ne (Read-Task $name)) { throw ('REFUSED: fixed historical task already exists: ' + $name) }
            }
            Write-Ledger @{fixtureId=$FixtureId; runId=$env:GITHUB_RUN_ID; attempt=$env:GITHUB_RUN_ATTEMPT;
                           job=$env:GITHUB_JOB; root=$Root; sid=$sid; names=$names; owned=@()}
            @{bothAbsent=$true; sid=$sid} | ConvertTo-Json -Compress
        }
        'create' {
            $ledger = Read-Ledger
            if ($ledger.owned.Count) { throw 'Fixture creation cannot be replayed' }
            # Recheck BOTH names immediately before creating either; never Force.
            foreach ($name in $names) {
                if ($null -ne (Read-Task $name)) { throw ('REFUSED: task appeared before fixture creation: ' + $name) }
            }
            $tokens=$null; $errors=$null
            $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $Payload 'scripts\install.ps1'),[ref]$tokens,[ref]$errors)
            $producer=@($ast.FindAll({param($node)
                $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'New-TaskSpec'
            },$false))
            if ($errors.Count -or $producer.Count -ne 1) { throw 'Actual native Index task producer is unavailable/ambiguous' }
            . ([scriptblock]::Create($producer[0].Extent.Text))
            foreach ($name in $names) {
                $launcher = if ($name -ceq 'agent-index') { Join-Path $Root 'service.ps1' } else { Join-Path $Root 'engine\engine.ps1' }
                if ($null -ne (Read-Task $name)) { throw 'REFUSED: task appeared at the creation boundary' }
                $spec=New-TaskSpec -LauncherPath $launcher
                Register-ScheduledTask -TaskName $name -TaskPath '\' -Action $spec.Action -Trigger $spec.Trigger `
                    -Settings $spec.Settings -Principal $spec.Principal -ErrorAction Stop | Out-Null
                $created=Read-Snapshot $name
                if ($null -eq $created) { throw 'Fixture task creation is unconfirmed' }
                $entry=@{name=$name; sha256=(Get-IndexHistoricalTaskFingerprint $created)}
                $ledger.owned=@($ledger.owned) + @($entry)
                Write-Ledger $ledger
                # Explicit fixture ownership, not the elevated runner's default
                # Administrators owner; preserve the real task XML and other ACLs.
                $current=Assert-ReceiptTask $entry
                if ($current.ownerSid -cne $sid) {
                    $task=Read-Task $name
                    $descriptor=[Security.AccessControl.RawSecurityDescriptor]::new($task.GetSecurityDescriptor(7))
                    $descriptor.Owner=[Security.Principal.SecurityIdentifier]::new($sid)
                    $task.SetSecurityDescriptor($descriptor.GetSddlForm([Security.AccessControl.AccessControlSections]::All), 0)
                    $current=Read-Snapshot $name
                    if ($null -eq $current -or $current.ownerSid -cne $sid) { throw 'Fixture current-user security ownership is unconfirmed' }
                    $entry.sha256=Get-IndexHistoricalTaskFingerprint $current
                    Write-Ledger $ledger
                }
            }
            @{created=$ledger.owned.Count; sid=$sid} | ConvertTo-Json -Compress
        }
        'observe' {
            $ledger=Read-Ledger
            ConvertTo-Json -InputObject @($ledger.owned | ForEach-Object { Read-Snapshot $_.name }) -Compress -Depth 12
        }
        'edit-service' {
            $ledger=Read-Ledger
            $entry=@($ledger.owned | Where-Object name -CEQ 'agent-index')
            if ($entry.Count -ne 1 -or $null -eq (Assert-ReceiptTask $entry[0])) { throw 'Edited task is not this fixture registration' }
            $task=Get-ScheduledTask -TaskName 'agent-index' -TaskPath '\' -ErrorAction Stop
            $task.Settings.RestartCount=4
            Set-ScheduledTask -TaskName 'agent-index' -TaskPath '\' -Settings $task.Settings -ErrorAction Stop | Out-Null
            $entry[0].sha256=Get-IndexHistoricalTaskFingerprint (Read-Snapshot 'agent-index')
            Write-Ledger $ledger
            @{edited=$true} | ConvertTo-Json -Compress
        }
        'run-engine-waiter' {
            $ledger=Read-Ledger
            $entry=@($ledger.owned | Where-Object name -CEQ 'agent-index-engine')
            if ($entry.Count -ne 1 -or $null -eq (Assert-ReceiptTask $entry[0])) { throw 'Waiter task is not this fixture registration' }
            Start-ScheduledTask -TaskName 'agent-index-engine' -TaskPath '\' -ErrorAction Stop
            $deadline=[DateTime]::UtcNow.AddSeconds(20)
            do {
                $task=Read-Task 'agent-index-engine'
                $waiterReady = $false
                try {
                    $event=[Threading.EventWaitHandle]::OpenExisting($EventName)
                    $event.Dispose()
                    $waiterReady=$true
                } catch [Threading.WaitHandleCannotBeOpenedException] { }
                if ($null -ne $task -and [int]$task.State -eq 4 -and $waiterReady) { break }
                Start-Sleep -Milliseconds 100
            } while ([DateTime]::UtcNow -lt $deadline)
            if ($null -eq $task -or [int]$task.State -ne 4 -or -not $waiterReady) { throw 'Benign waiter did not reach actual TaskScheduler Running state and ready event' }
            @{running=$true} | ConvertTo-Json -Compress
        }
        'scheduling-race' {
            $ledger=Read-Ledger
            $entry=@($ledger.owned | Where-Object name -CEQ 'agent-index-engine')
            if ($entry.Count -ne 1 -or $null -eq (Assert-ReceiptTask $entry[0])) { throw 'Race target is not this fixture registration' }
            $script:launchBlocked=$false
            $script:deleteBoundaryReached=$false
            function Remove-IndexHistoricalTask {
                param([string]$Name)
                if ($Name -cne 'agent-index-engine') { throw 'Foreign race target' }
                $task=Read-Task $Name
                if ($task.Enabled -or $task.GetInstances(0).Count -ne 0) { throw 'Scheduling was not quiescent at delete' }
                try { $null=$task.Run($null) }
                catch { $script:launchBlocked=$true }
                if (-not $script:launchBlocked -or $task.GetInstances(0).Count -ne 0) { throw 'Disabled task admitted a new instance' }
                $script:deleteBoundaryReached=$true
                # Deliberately retain this exact task, proving restoration before
                # the later full Python/native production retirement removes it.
                throw 'fixture-delete-interruption'
            }
            $before=Read-Snapshot 'agent-index-engine'
            $result=Invoke-IndexHistoricalRegistration -Request @{name='agent-index-engine';legacyRoot=$Root} -Admit {
                param($observed)
                return @{action='remove';sha256=(Get-IndexHistoricalTaskFingerprint $observed)}
            }
            $after=Read-Snapshot 'agent-index-engine'
            if ($result.status -cne 'failed' -or $result.reason -cne 'fixture-delete-interruption' -or
                -not $script:deleteBoundaryReached -or -not $script:launchBlocked -or
                $null -eq $after -or -not (Read-Task 'agent-index-engine').Enabled -or
                -not (Test-IndexHistoricalTaskConfiguration $before $after)) {
                throw 'Native scheduling race or enabled-state restoration was not proven'
            }
            $entry[0].sha256=Get-IndexHistoricalTaskFingerprint $after
            Write-Ledger $ledger
            @{blocked=$true; restored=$true} | ConvertTo-Json -Compress
        }
        'release' {
            $null=Read-Ledger
            try {
                $event=[Threading.EventWaitHandle]::OpenExisting($EventName)
                try { $null=$event.Set() } finally { $event.Dispose() }
            } catch [Threading.WaitHandleCannotBeOpenedException] { }
            $deadline=[DateTime]::UtcNow.AddSeconds(30)
            do {
                $task=Read-Task 'agent-index-engine'
                if ($null -eq $task -or [int]$task.State -in @(1,3)) { break }
                Start-Sleep -Milliseconds 100
            } while ([DateTime]::UtcNow -lt $deadline)
            if ($null -ne $task -and [int]$task.State -notin @(1,3)) { throw 'Waiter exit is unsettled; no task stop/kill is permitted' }
            @{released=$true} | ConvertTo-Json -Compress
        }
        'cleanup' {
            $ledger=Read-Ledger
            foreach ($entry in $ledger.owned) {
                $snapshot=Assert-ReceiptTask $entry
                if ($null -eq $snapshot) { continue }
                if ($snapshot.state -notin @(1,3)) { throw 'REFUSED: owned fixture task is still running/queued' }
                # Exact current contents verified under the native owner mutex.
                Remove-IndexHistoricalTask -Name $entry.name
                if ($null -ne (Read-Task $entry.name)) { throw 'Fixture cleanup absence is unconfirmed' }
            }
            foreach ($name in $names) {
                if ($null -ne (Read-Task $name)) { throw ('Residual registration preserved for disposable-runner diagnosis: ' + $name) }
            }
            @{bothAbsent=$true} | ConvertTo-Json -Compress
        }
        default { throw 'Unsupported native fixture phase' }
    }
} finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
"""


def _load_unit_helpers():
    spec = importlib.util.spec_from_file_location("index_native_registration_unit_helpers", UNIT_FIXTURES)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_disposable_task_scheduler_archive_and_production_retirement(tmp_path, pytestconfig):
    host = _admit_disposable_ci(tmp_path)
    helpers = _load_unit_helpers()
    owner = helpers.load(helpers.OWNER_PATH, "index_native_scheduler_production_owner")
    fixture_id = uuid.uuid4().hex
    event_name = "Local\\IndexHistoricalFixture-" + fixture_id
    root, selection, _, layout, _, _ = helpers.create_selected_cell(
        tmp_path, owner, pytestconfig, launcher_paths=("service.ps1", r"engine\engine.ps1"),
        launcher_content="exit 0\n", governance_profile=Path(os.environ["USERPROFILE"]),
    )
    # Default production governance must be available; don't inject a fake native
    # factory, alternative environment, final success, or relaxed archive codec.
    assert callable(getattr(owner._governance(), "historical_registration_retirement_custody", None)), "synchronize canonical installation-context sources before this lane"
    wrapper = tmp_path / "disposable-scheduler-fixture.ps1"
    wrapper.write_text(_NATIVE_FIXTURE, encoding="utf-8")
    ledger = tmp_path / "task-creation-receipt.json"

    def native(phase):
        result = subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-File", str(wrapper), phase,
             str(helpers.OWNER_PATH.parent.parent), str(root), str(ledger), fixture_id, event_name],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, f"native fixture {phase} failed: {result.stderr}\n{result.stdout}"
        return json.loads(result.stdout)

    assert native("assert-absent")["bothAbsent"] is True
    try:
        assert native("create")["created"] == 2
        actual = native("observe")
        assert {item["name"] for item in actual} == set(NAMES)
        findings = {}
        for snapshot in actual:
            assert snapshot["ownerSid"] == snapshot["sid"], "fixture task security owner is not current SID"
            assert owner.matches_archive(snapshot, str(root), snapshot["name"]), (
                "actual exporter XML differs from the frozen finite archive for " + snapshot["name"]
            )
            findings[snapshot["name"]] = owner.operate(root, selection, snapshot["name"])
            assert findings[snapshot["name"]]["status"] == "candidate", findings[snapshot["name"]]

        activation = Path(layout["plugin_root"]) / "installation-activation.json"
        saved_activation = activation.read_bytes()
        current = json.loads(saved_activation)
        current["mode"] = "legacy"
        current["legacy"]["disposition"] = "restored"
        try:
            activation.write_text(json.dumps(current), encoding="utf-8")
            held = owner.operate(root, selection, "agent-index", findings["agent-index"])
            assert held["status"] == "preserved", held
            assert held["reason"] == "Historical registration is still current or its disposition is unproven."
            assert {item["name"] for item in native("observe")} == set(NAMES)
        finally:
            activation.write_bytes(saved_activation)

        assert native("edit-service")["edited"] is True
        held = owner.operate(root, selection, "agent-index", findings["agent-index"])
        assert held["status"] == "preserved", held
        assert held["reason"] == "current-foreign-edited-or-unsettled-registration"
        edited = next(item for item in native("observe") if item["name"] == "agent-index")
        assert not owner.matches_archive(edited, str(root), "agent-index")

        engine_launcher = root / "engine" / "engine.ps1"
        engine_launcher.write_text(
            "$event=[Threading.EventWaitHandle]::new($false,[Threading.EventResetMode]::ManualReset,'" + event_name + "')\n"
            "try { $null=$event.WaitOne(120000) } finally { $event.Dispose() }\nexit 0\n",
            encoding="utf-8",
        )
        assert native("run-engine-waiter")["running"] is True
        held = owner.operate(root, selection, "agent-index-engine", findings["agent-index-engine"])
        assert held["status"] == "preserved", held
        assert held["reason"] == "current-foreign-edited-or-unsettled-registration"
        assert native("release")["released"] is True
        engine_launcher.write_text("exit 0\n", encoding="utf-8")

        race = native("scheduling-race")
        assert race == {"blocked": True, "restored": True}
        positive = owner.operate(root, selection, "agent-index-engine")
        assert positive["status"] == "candidate", positive
        retired = owner.operate(root, selection, "agent-index-engine", positive)
        assert retired["status"] == "applied", retired
        assert {item["name"] for item in native("observe") if item is not None} == {"agent-index"}
    finally:
        # Never kill a process/task by name. The bounded benign waiter signals
        # its own exit; exact creation receipts gate every remaining unregister.
        try:
            native("release")
        finally:
            assert native("cleanup")["bothAbsent"] is True
