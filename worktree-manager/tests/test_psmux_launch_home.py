"""Execute the shipped creation boundary without starting a real mux."""

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"
PWSH = shutil.which("pwsh")


def _creation_source():
    source = SOURCE.read_text("utf-8")
    start = source.index("                try {\n", source.index("$savedAuth = $null"))
    end = source.index("\n                    $newSessionExit", start)
    helper = ""
    marker = "function Invoke-AwPsmuxFromHome {"
    if marker in source:
        first = source.index(marker)
        last = source.index("\n}\n", first) + 3
        helper = source[first:last]
    first = source.index("function Enter-AwMuxCreation {")
    last = source.index("\n}\n", first) + 3
    helper += "\n" + source[first:last]
    helper += "\nfunction Test-AwMuxCreationSession { return $false }\n"
    helper += "\nfunction Stop-AwOwnedPsmuxSession {}\n"
    helper += (
        "\nfunction Invoke-AwPsmuxCreation { param($CommandArgs,$Lease)\n"
        "& $script:AwPsmuxBin @CommandArgs\n"
        "Set-Variable -Name LASTEXITCODE -Value $LASTEXITCODE -Scope 1\n}\n"
    )
    return helper, source[start + len("                try {\n"):end]


def _native_driver_source():
    native = SOURCE.parents[1] / "tests" / "native" / "test_psmux_home_directory.py"
    tree = ast.parse(native.read_text("utf-8"))
    driver = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "DRIVER"
                          for target in node.targets))
    return ast.literal_eval(driver)


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell")
@pytest.mark.parametrize("exit_code", [0, 17])
def test_native_creation_uses_home_and_restores_caller(tmp_path, exit_code):
    home = tmp_path / "home with spaces"
    caller = tmp_path / "caller checkout"
    worktree = tmp_path / "selected pane worktree"
    for directory in (home, caller, worktree):
        directory.mkdir()
    stub = tmp_path / "mux-stub.py"
    captured = tmp_path / "captured.json"
    stub.write_text(
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['MUX_CAPTURE']).write_text(json.dumps({"
        "'cwd':os.getcwd(),'argv':sys.argv[1:]}))\n"
        "raise SystemExit(int(os.environ['MUX_EXIT']))\n",
        encoding="utf-8",
    )
    command = tmp_path / "psmux.cmd"
    command.write_text(
        '@echo off\n"' + sys.executable + '" "' + str(stub) + '" %*\n',
        encoding="utf-8",
    )
    helper, creation = _creation_source()
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$script:AwPsmuxBin=$args[0]\n"
        "$plan=@{work_dir=$args[1]}\n"
        "$sessName='fixture-session'\n"
        "$envFlags=@('-e','FIXTURE=two words')\n"
        "$paneCmd=@('child','two words','--allow-all')\n"
        + helper + "\n" + creation
        + "\n$code=$LASTEXITCODE\n"
        "ConvertTo-Json -Compress @{cwd=(Get-Location).ProviderPath;code=$code}\n",
        encoding="utf-8",
    )
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
               MUX_CAPTURE=str(captured), MUX_EXIT=str(exit_code))
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(command), str(worktree)],
        cwd=caller, env=env, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(captured.read_text("utf-8"))
    assert Path(observed["cwd"]) == home
    assert observed["argv"] == [
        "new-session", "-d", "-s", "fixture-session", "-c", str(worktree),
        "-e", "FIXTURE=two words", "child", "two words", "--allow-all",
    ]
    restored = json.loads(result.stdout)
    assert Path(restored["cwd"]) == caller
    assert restored["code"] == exit_code


def test_manager_creation_uses_the_home_scoped_boundary():
    helper, creation = _creation_source()
    assert "Push-Location -LiteralPath $HOME" in helper
    assert "finally" in helper and "Pop-Location" in helper
    assert "Invoke-AwPsmuxFromHome" in creation
    assert "$plan.work_dir" in creation


@pytest.mark.skipif(not PWSH, reason="PowerShell is required")
def test_published_creation_failure_preserves_handoffs_and_does_not_retry(tmp_path):
    source = SOURCE.read_text("utf-8")
    start = source.index("            $creationUncertain = $newSessionExit")
    end = source.index("\n        }\n\n        if (-not $creationUncertain", start)
    attempt_tail = source[start:end]
    start = source.index("        if (-not $creationUncertain", end)
    end = source.index("\n    }\n    $env:PSMUX_SESSION", start)
    retry_tail = source[start:end]
    start = source.index("    if ($newSessionExit -ne 0) {", end)
    end = source.index("\n    } else {\n        if ($paneArgsFile)", start)
    failure_tail = source[start:end] + "\n}\n"
    manifest, receipt, token = [tmp_path / name for name in ("args.json", "receipt.json", "token")]
    for path in (manifest, receipt, token):
        path.write_text("retained", encoding="utf-8")
    script = tmp_path / "failure.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$newSessionExit=17\n$newSessionError=''\n$muxPublished=$true\n"
        "$maxCreateAttempts=3\n$totalCreateAttempts=0\n"
        "$paneArgsFile=$args[0]\n$paneReceiptFile=$args[1]\n$ahpTokenFile=$args[2]\n"
        "$sessName='wt-fixture'\n"
        "$plan=[pscustomobject]@{worktree_id='fixture';work_dir=$args[3]}\n"
        "function Remove-AwPaneArgsFile { throw 'Deleted an uncertain handoff' }\n"
        "function Read-AwMuxRetryChoice { throw 'Retried an uncertain creation' }\n"
        "function Write-SetupStatus { throw 'Retried an uncertain creation' }\n"
        "function Write-SetupLog {}\n"
        "function Write-AwMuxFailure {\n"
        "param($Reason,$ExitCode,$Fields)\n"
        "ConvertTo-Json -Compress @{attempts=$totalCreateAttempts;fields=$Fields}\n}\n"
        "for($attempt=1;$attempt -le $maxCreateAttempts;$attempt++){\n"
        "$totalCreateAttempts++\n" + attempt_tail + "\n}\n"
        + retry_tail + "\n" + failure_tail,
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script),
         str(manifest), str(receipt), str(token), str(tmp_path)],
        capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 17, result.stdout + result.stderr
    assert all(path.read_text("utf-8") == "retained" for path in (manifest, receipt, token))
    report = json.loads(result.stdout)
    assert report["attempts"] == 1
    assert "recoverable=false" in report["fields"]
    assert "Inspect the existing session" in result.stderr


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell")
@pytest.mark.parametrize("failure", ["missing-executable", "missing-home"])
def test_creation_refusal_restores_caller_without_a_fallback(tmp_path, failure):
    home = tmp_path / "home"
    caller = tmp_path / "caller checkout"
    caller.mkdir()
    if failure != "missing-home":
        home.mkdir()
    helper, creation = _creation_source()
    script = tmp_path / "refusal.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$script:AwPsmuxBin=$args[0]\n"
        "$plan=@{work_dir=$args[1]}\n$sessName='fixture'\n"
        "$envFlags=@()\n$paneCmd=@('child')\n"
        + helper + "\n$refused=$false\ntry {\n" + creation
        + "\n} catch {$refused=$true}\n"
        "ConvertTo-Json -Compress @{cwd=(Get-Location).ProviderPath;"
        "refused=$refused}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script),
         str(tmp_path / "missing-psmux.exe"), str(caller)],
        cwd=caller, capture_output=True, text=True, timeout=20,
        env=dict(os.environ, HOME=str(home), USERPROFILE=str(home)),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"cwd": str(caller), "refused": True}


@pytest.mark.skipif(not PWSH, reason="PowerShell is required")
@pytest.mark.parametrize("selector", ["launcher", "native"])
def test_cleanup_refuses_older_children_of_reused_parent_pids(tmp_path, selector):
    if selector == "launcher":
        source = SOURCE.read_text("utf-8")
        start = source.index("        $depth = @{}", source.index(
            "function Stop-AwOwnedPsmuxSession",
        ))
        end = source.index("\n        foreach ($pidValue in @(", start)
        selected = "$depth.Keys"
    else:
        source = _native_driver_source()
        start = source.index("    $selected=@{}")
        end = source.index("    $held=@{}", start)
        selected = "$selected.Keys"
    rows = [
        (100, 0, "00:01:00"),
        (101, 100, "00:00:50"),
        (102, 101, "00:02:00"),
        (201, 100, "00:01:01"),
        (202, 201, "00:01:02"),
        (203, 201, "00:01:00.500"),
        (204, 201, None),
        (205, 100, "00:01:00"),
    ]
    payload = tmp_path / "processes.json"
    payload.write_text(json.dumps([
        {"ProcessId": pid, "ParentProcessId": parent,
         "CreationDate": "2025-01-01T" + born + "Z" if born else None}
        for pid, parent, born in rows
    ]), encoding="utf-8")
    script = tmp_path / "select.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$all=@(Get-Content -Raw -LiteralPath $args[0] | ConvertFrom-Json)\n"
        "foreach($row in $all){if($null -ne $row.CreationDate){"
        "$row.CreationDate=[DateTime]::Parse([string]$row.CreationDate).ToUniversalTime()}}\n"
        "$roots=@($all | Where-Object {$_.ProcessId -eq 100})\n"
        + source[start:end]
        + "\nConvertTo-Json -Compress -InputObject @(" + selected + " | Sort-Object)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(payload)],
        capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert set(json.loads(result.stdout)) == {100, 201, 202, 205}


@pytest.mark.skipif(not PWSH, reason="PowerShell is required")
@pytest.mark.parametrize("scenario", [
    "qualified", "birth-mismatch", "pre-existing", "foreign-namespace", "already-exited",
])
def test_native_cleanup_verifies_targets_before_signaling(tmp_path, scenario):
    source = _native_driver_source()
    start = source.index("function Stop-OwnedTestMux {")
    end = source.index("\n}\n", start) + 3
    function = source[start:end]
    script = tmp_path / "termination.ps1"
    script.write_text(
        r"""
$ErrorActionPreference='Stop'
$scenario=$args[0]
$namespace='owned-fixture'
$binary='C:\fixture\psmux.exe'
$began=[DateTime]::Parse('2025-01-01T00:00:00Z').ToUniversalTime()
$born=$began.AddMinutes(1)
$script:Rows=@(
    [pscustomobject]@{ProcessId=100;ParentProcessId=0;Name='psmux.exe';
        ExecutablePath=$binary;CreationDate=$born;
        CommandLine='psmux.exe server -s fixture -L owned-fixture'},
    [pscustomobject]@{ProcessId=201;ParentProcessId=100;Name='cmd.exe';
        CreationDate=$born.AddSeconds(1)}
)
if($scenario -eq 'foreign-namespace'){
    $script:Rows[0].CommandLine='psmux.exe server -s fixture -L unrelated'
}
$script:Signals=@()
$script:Disposals=@()
$script:Queried=@()
$script:Objects=@{}
foreach($row in $script:Rows){
    $p=[pscustomobject]@{Id=$row.ProcessId;Handle=1;
        StartTime=$row.CreationDate;HasExited=$false}
    $p | Add-Member ScriptMethod WaitForExit {param($Timeout) $this.HasExited}
    $p | Add-Member ScriptMethod Dispose {$script:Disposals += $this.Id}
    $script:Objects[$row.ProcessId]=$p
}
if($scenario -eq 'birth-mismatch'){
    $script:Objects[201].StartTime=$born.AddSeconds(3)
}
if($scenario -eq 'already-exited'){$script:Objects[201].HasExited=$true}
$prior=@{}
if($scenario -eq 'pre-existing'){$prior[201]=1}
$owned=[Collections.Generic.HashSet[int]]::new()
function Get-CimInstance {
    [CmdletBinding()]param([string]$ClassName)
    $script:Rows
}
function Get-Process {
    [CmdletBinding()]param([int]$Id)
    $script:Queried += $Id
    $script:Objects[$Id]
}
function Stop-Process {
    [CmdletBinding()]param([int]$Id,[switch]$Force)
    $script:Signals += $Id
    $script:Objects[$Id].HasExited=$true
}
""" + function + r"""
$refused=$false
$reason=''
try {Stop-OwnedTestMux} catch {$refused=$true;$reason=$_.Exception.Message}
ConvertTo-Json -Compress @{signals=@($script:Signals);
    disposed=@($script:Disposals);queried=@($script:Queried);
    refused=$refused;reason=$reason}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), scenario],
        capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    if scenario == "qualified":
        assert set(observed["signals"]) == {100, 201}
        assert observed["refused"] is False
    elif scenario == "already-exited":
        assert observed["signals"] == [100]
        assert observed["refused"] is False
    else:
        assert observed["signals"] == []
        assert observed["refused"] is (scenario != "foreign-namespace")
    if scenario == "birth-mismatch":
        assert "identity changed" in observed["reason"]
    if scenario == "pre-existing":
        assert "pre-existing" in observed["reason"]
    if scenario == "foreign-namespace":
        assert observed["queried"] == []
    else:
        assert set(observed["disposed"]) == set(observed["queried"])
