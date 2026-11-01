"""Real shell facade checks with an inert dispatcher, never native launch proof."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_daemon_instance_powershell import _literal, _run

HELPER = Path(__file__).resolve().parents[1] / "src/agent_procutil/native_owned.ps1"


def test_import_is_inert_and_cannot_compile_a_helper() -> None:
    source = HELPER.read_text(encoding="utf-8")
    assert "Add-Type" not in source and "Reflection.Emit" not in source
    result = _run(
        "$ErrorActionPreference='Stop'; "
        f". {_literal(HELPER)}; "
        "if ($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1')) "
        "{ throw 'import activated a helper' }; "
        "'inert-import'"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "inert-import"


def test_native_lifetime_fixture_parses_without_execution() -> None:
    fixture = Path(__file__).with_name("fixture_native_owned_lifetime.ps1")
    assert "Add-Type" not in fixture.read_text(encoding="utf-8")
    result = _run(
        "$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null; "
        f"$null=[Management.Automation.Language.Parser]::ParseFile({_literal(fixture)},"
        "[ref]$tokens,[ref]$errors); "
        "if ($errors.Count) { throw ($errors | Out-String) }; 'fixture-parsed'"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "fixture-parsed"


def test_legacy_import_refusal_preserves_the_existing_function() -> None:
    result = _run(
        "$ErrorActionPreference='Stop'; "
        "function Invoke-NativeOwnedProcess { return 'retained-owner' }; "
        "$types=[psobject].Assembly.GetType('System.Management.Automation.TypeAccelerators'); "
        "$types::Add('CopilotExtensions.NativeOwnedProcess',[string]); "
        "try { "
        f"try {{ . {_literal(HELPER)}; throw 'expected refusal' }} "
        "catch { if ($_.Exception.Message -notlike '*legacy compiled owned-process adapter*') { throw } }; "
        "if ((Invoke-NativeOwnedProcess) -cne 'retained-owner') { throw 'guard replaced' }; "
        "'legacy-retained' "
        "} finally { [void]$types::Remove('CopilotExtensions.NativeOwnedProcess') }"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "legacy-retained"


def test_runspace_cancellation_closes_original_helper_custody() -> None:
    instances = HELPER.with_name("daemon_instances.ps1")
    result = _run(
        "$ErrorActionPreference='Stop'; "
        "$gate=[string]::Intern('copilot-daemon-instance-shell-v1'); "
        "$pending=New-Object 'Threading.Tasks.TaskCompletionSource[string]'; "
        "$reader=[pscustomobject]@{pending=$pending;closed=$false}; "
        "$reader | Add-Member ScriptMethod ReadLineAsync { return $this.pending.Task }; "
        "$reader | Add-Member ScriptMethod Dispose { $this.closed=$true }; "
        "$job=New-Object IO.MemoryStream; $pipe=New-Object IO.MemoryStream; "
        "$inputWriter=New-Object IO.StreamWriter($pipe); $inputWriter.AutoFlush=$true; "
        "$state=@{process=$null;worker=$null;job=$job;input=$inputWriter;output=$reader;"
        "error=$null;diagnostics=$null;nonce=('a'*32);sequence=0;broken=$false;offerPermitted=$false}; "
        "[AppDomain]::CurrentDomain.SetData($gate,$state); "
        "$runner=[PowerShell]::Create(); "
        f"$null=$runner.AddScript('. ' + {_literal(instances)}.Replace(\"'\",\"''\").Insert(0,\"'\") + "
        "\"'; Invoke-DaemonInstanceRequest @{command='run-owned';parameters=@{timeout_ms=60000}}\""
        "); "
        "try { "
        "$async=$runner.BeginInvoke(); $clock=[Diagnostics.Stopwatch]::StartNew(); "
        "while ($state.sequence -eq 0) { "
        "if ($async.IsCompleted -or $clock.ElapsedMilliseconds -gt 5000) { throw 'request did not start' }; "
        "Start-Sleep -Milliseconds 10 }; "
        "$runner.Stop(); "
        "@{broken=$state.broken;jobClosed=(-not $job.CanWrite);readerClosed=$reader.closed} "
        "| ConvertTo-Json -Compress "
        "} finally { $runner.Dispose(); [AppDomain]::CurrentDomain.SetData($gate,$null); "
        "$job.Dispose(); $pipe.Dispose() }"
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"broken": True, "jobClosed": True, "readerClosed": True}


@pytest.mark.parametrize("outcome", [
    "success", "confirmed-error", "unknown-error", "nonboolean-error", "bad-result",
])
def test_facade_keeps_arguments_status_and_explicit_cleanup(
    tmp_path: Path, outcome: str,
) -> None:
    shim = tmp_path / "dispatcher.ps1"
    response = {
        "success": "return [pscustomobject]@{returncode=-1073741819}",
        "confirmed-error": (
            "$e=[InvalidOperationException]::new('fixture deadline'); "
            "$e.Data['CleanupConfirmed']=$true; throw $e"
        ),
        "unknown-error": "throw 'fixture unknown outcome'",
        "nonboolean-error": (
            "$e=[InvalidOperationException]::new('fixture unknown outcome'); "
            "$e.Data['CleanupConfirmed']='True'; throw $e"
        ),
        "bad-result": "return [pscustomobject]@{returncode='not-a-status'}",
    }[outcome]
    shim.write_text(
        "function Invoke-DaemonInstanceRequest { param([hashtable]$Request)\n"
        "if ($Request.command -cne 'run-owned') { throw 'wrong command' }\n"
        "if ($Request.parameters.timeout_ms -cne 2345) { throw 'deadline changed' }\n"
        "if ($Request.parameters.environment['OWNED_FIXTURE'] -cne 'original') "
        "{ throw 'original environment missing' }\n"
        "$script:observedArguments=@($Request.parameters.arguments)\n"
        + response + "\n}\n",
        encoding="utf-8",
    )
    result = _run(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2; "
        f". {_literal(HELPER)}; $script:NativeOwnedInstances={_literal(shim)}; "
        "$env:OS='Windows_NT'; $env:OWNED_FIXTURE='original'; "
        "$observedArguments=$null; $code=$null; $errorMessage=$null; $settled=$null; "
        "try { "
        f"$code=Invoke-NativeOwnedProcess -FilePath {_literal(HELPER)} "
        "-Arguments @('','a b','x\"y','tail\\') "
        f"-WorkingDirectory {_literal(tmp_path)} -LogPath {_literal(tmp_path / 'unused.log')} "
        "-TimeoutMilliseconds 2345 "
        "} catch { $errorMessage=$_.Exception.Message; $settled=$_.Exception.Data['CleanupConfirmed'] }; "
        "@{code=$code;error=$errorMessage;settled=$settled;arguments=$observedArguments} "
        "| ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["arguments"] == ["", "a b", 'x"y', "tail\\"]
    if outcome == "success":
        assert value["code"] == -1073741819 and value["error"] is None
    else:
        assert value["code"] is None and value["error"]
        assert value["settled"] is (outcome == "confirmed-error")
    assert not (tmp_path / "unused.log").exists()


@pytest.mark.parametrize("outcome", [
    "confirmed", "denied", "missing", "malformed", "wrapped", "outer-denied", "success",
])
def test_foundation_child_preserves_cleanup_and_original_failure(
    tmp_path: Path, outcome: str,
) -> None:
    producer = HELPER.parents[4] / "libs/update-driver/scripts/foundation-produce.ps1"
    response = {
        "confirmed": "$e.Data['CleanupConfirmed']=$true; throw $e",
        "denied": "$e.Data['CleanupConfirmed']=$false; throw $e",
        "missing": "throw $e",
        "malformed": "$e.Data['CleanupConfirmed']='True'; throw $e",
        "wrapped": (
            "$e.Data['CleanupConfirmed']=$true; "
            "throw [InvalidOperationException]::new('wrapper',$e)"
        ),
        "outer-denied": (
            "$e.Data['CleanupConfirmed']=$true; "
            "$outer=[InvalidOperationException]::new('fixture-outer-unknown',$e); "
            "$outer.Data['CleanupConfirmed']=$false; throw $outer"
        ),
        "success": "return [pscustomobject]@{returncode=7}",
    }[outcome]
    shim = tmp_path / "foundation-dispatcher.ps1"
    shim.write_text(
        "function Invoke-DaemonInstanceRequest { param([hashtable]$Request)\n"
        "$e=[InvalidOperationException]::new('fixture-child-failure')\n" + response + "\n}\n",
        encoding="utf-8",
    )
    result = _run(
        "$ErrorActionPreference='Stop'; "
        f". {_literal(HELPER)}; $script:NativeOwnedInstances={_literal(shim)}; "
        f"$plugin={_literal(tmp_path)}; $producer={_literal(producer)}; "
        "$tokens=$null; $errors=$null; "
        "$ast=[Management.Automation.Language.Parser]::ParseFile($producer,[ref]$tokens,[ref]$errors); "
        "if ($errors.Count) { throw ($errors | Out-String) }; "
        "$function=$ast.Find({param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -ceq 'Invoke-FoundationChild'},$true); "
        "if ($null -eq $function) { throw 'source consumer unavailable' }; "
        ". ([scriptblock]::Create($function.Extent.Text)); "
        "$env:OS='Windows_NT'; $script:descendantsSettled=$true; $code=$null; $failure=$null; "
        f"try {{ $code=Invoke-FoundationChild -Executable {_literal(HELPER)} "
        f"-Arguments @('fixture') -Log {_literal(tmp_path / 'unused.log')} -Timeout 2345 }} "
        "catch { $failure=$_.Exception.Message }; "
        "@{settled=$script:descendantsSettled;code=$code;failure=$failure} | ConvertTo-Json -Compress"
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    if outcome == "success":
        assert value == {"settled": True, "code": 7, "failure": None}
    else:
        assert value["code"] is None
        assert "fixture-" in value["failure"]
        assert value["settled"] is (outcome in ("confirmed", "wrapped"))
    assert not (tmp_path / "unused.log").exists()
