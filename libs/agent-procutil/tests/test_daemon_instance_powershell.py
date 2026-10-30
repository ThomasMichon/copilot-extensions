"""PowerShell syntax/facade controls with a stub dispatcher, not native proof."""

import base64
import json
import io
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_procutil import no_window_kwargs

HELPER = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "daemon_instances.ps1"


def _run(body: str, *, script: Path | None = None) -> subprocess.CompletedProcess:
    if os.name == "nt":
        host = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    else:
        executable = shutil.which("pwsh")
        if not executable:
            pytest.skip("PowerShell syntax/facade host is unavailable")
        host = Path(executable)
    if script is None:
        encoded = base64.b64encode(body.encode("utf-16le")).decode("ascii")
        command = ["-EncodedCommand", encoded]
    else:
        script.write_text(body, encoding="utf-8")
        command = ["-File", str(script)]
    return subprocess.run(
        [str(host), "-NoLogo", "-NoProfile", "-NonInteractive", *command],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def test_import_is_inert_and_parses_without_compilation():
    source = HELPER.read_text(encoding="utf-8")
    assert "Add-Type" not in source and "Reflection.Emit" not in source
    result = _run(
        "$ErrorActionPreference='Stop'; "
        f"$path={_literal(HELPER)}; "
        "$tokens=$null; $errors=$null; "
        "$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors); "
        "if ($errors.Count) { throw ($errors | Out-String) }; "
        "$statements=@($ast.EndBlock.Statements); "
        "if ($statements[0].GetType().Name -ne 'IfStatementAst' -or "
        "$statements[0].Extent.Text -notlike '*legacy compiled daemon-instance adapter*') "
        "{ throw 'missing pre-definition legacy refusal' }; "
        "$unexpected=@($statements | Select-Object -Skip 1 | Where-Object {"
        "$_.GetType().Name -notin @('AssignmentStatementAst','FunctionDefinitionAst')}); "
        "if ($unexpected.Count) { throw 'executable import body' }; "
        ". $path; "
        "if ($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1')) "
        "{ throw 'import activated the helper' }; "
        "[Console]::Out.WriteLine('inert-import')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "inert-import"


def test_loaded_legacy_adapter_refuses_before_helper_activation_without_compilation():
    result = _run(
        "$ErrorActionPreference='Stop'; "
        f". {_literal(HELPER)}; "
        "$types=[psobject].Assembly.GetType('System.Management.Automation.TypeAccelerators'); "
        "$types::Add('CopilotExtensions.DaemonInstanceNative',[string]); "
        "try { "
        "try { Start-DaemonInstanceWorker @{}; throw 'expected legacy refusal' } "
        "catch { if ($_.Exception.Message -notlike '*legacy compiled daemon-instance adapter*') { throw } }; "
        "if ($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1')) "
        "{ throw 'legacy refusal activated a helper' }; "
        "[Console]::Out.WriteLine('legacy-refused') "
        "} finally { [void]$types::Remove('CopilotExtensions.DaemonInstanceNative') }"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "legacy-refused"


def test_legacy_import_refusal_preserves_the_existing_guard_exit_function():
    result = _run(
        "$ErrorActionPreference='Stop'; "
        "function Exit-DaemonInstance { return 'retained-legacy-owner' }; "
        "$types=[psobject].Assembly.GetType('System.Management.Automation.TypeAccelerators'); "
        "$types::Add('CopilotExtensions.DaemonInstanceNative',[string]); "
        "try { "
        f"try {{ . {_literal(HELPER)}; throw 'expected legacy refusal' }} "
        "catch { if ($_.Exception.Message -notlike '*legacy compiled daemon-instance adapter*') { throw } }; "
        "if ((Exit-DaemonInstance) -cne 'retained-legacy-owner') "
        "{ throw 'source import replaced the existing guard owner' }; "
        "if ($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1')) "
        "{ throw 'legacy import refusal activated a helper' }; "
        "[Console]::Out.WriteLine('legacy-owner-retained') "
        "} finally { [void]$types::Remove('CopilotExtensions.DaemonInstanceNative') }"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "legacy-owner-retained"


def test_native_lifetime_fixture_parses_without_executing_or_compiling():
    fixture = Path(__file__).with_name("fixture_daemon_instance_shell.ps1")
    assert "Add-Type" not in fixture.read_text()
    result = _run(
        "$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null; "
        f"$null=[Management.Automation.Language.Parser]::ParseFile({_literal(fixture)},"
        "[ref]$tokens,[ref]$errors); "
        "if ($errors.Count) { throw ($errors | Out-String) }; "
        "[Console]::Out.WriteLine('fixture-parsed')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "fixture-parsed"


@pytest.mark.parametrize("relative,load", [
    ("tools/windows_normal_process_lifetime.py",
     "f\". {literal(native.with_name('daemon_instances.ps1'))}\\n\""),
    ("libs/agent-procutil/tests/test_native_daemon.py",
     "f\". {literal(helper.with_name('daemon_instances.ps1'))}\\n\""),
    ("libs/agent-procutil/tests/fixture_native_daemon.ps1",
     ". (Join-Path (Split-Path $Helper) 'daemon_instances.ps1')"),
])
def test_native_fixture_callers_load_birth_observer_in_their_own_scope(relative, load):
    root = Path(__file__).resolve().parents[3]
    source = (root / relative).read_text(encoding="utf-8")
    assert source.index(load) < source.index("Get-DaemonInstanceProcessBirth")
    result = _run(
        "$ErrorActionPreference='Stop'; "
        f". {_literal(HELPER)}; "
        "function Start-Fixture { "
        f". {_literal(HELPER)}; "
        "return [Diagnostics.Process]::GetCurrentProcess() }; "
        "$process=Start-Fixture; "
        "try { $birth=Get-DaemonInstanceProcessBirth $process; "
        "if ($birth -notlike 'windows:*') { throw 'caller observer unavailable' } "
        "} finally { $process.Dispose() }; "
        "if ($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1')) "
        "{ throw 'scope fixture activated a helper' }; "
        "[Console]::Out.WriteLine('caller-scope')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "caller-scope"


def test_facade_retains_original_process_handle_and_guard_close_is_idempotent(tmp_path):
    result = _run(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2; "
        f". {_literal(HELPER)}; "
        "$script:requests=New-Object 'Collections.Generic.List[object]'; "
        "function Invoke-DaemonInstanceRequest { param([hashtable]$Request) "
        "$script:requests.Add($Request); "
        "switch ($Request.command) { "
        "'reserve' { return [pscustomobject]@{instance_id=('a'*32);registry_root='fixture';record_path='fixture'} } "
        "'enter' { return [pscustomobject]@{instance_id=('a'*32);registry_root='fixture';"
        "record_path='fixture';guard_token=('b'*32);job_scopes=$null} } "
        "'read' { return [pscustomobject]@{purpose='production'} } "
        "'reconcile' { return [pscustomobject]@{instance_id=('a'*32);status='held';reason='invalid-record'} } "
        "default { return $null } } }; "
        f"$root={_literal(tmp_path)}; $source={_literal(HELPER)}; "
        "$reserved=New-DaemonInstanceReservation -Service fixture -RuntimeRoot $root -Source $source; "
        "$env:COPILOT_EXTENSIONS_DAEMON_INSTANCE='inherited'; "
        "$env:COPILOT_EXTENSIONS_DAEMON_REGISTRY='inherited'; "
        "$guard=Enter-DaemonInstance -Service fixture -RuntimeRoot $root -Source $source -Purpose production; "
        "if ($env:COPILOT_EXTENSIONS_DAEMON_INSTANCE -or $env:COPILOT_EXTENSIONS_DAEMON_REGISTRY) "
        "{ throw 'inherited admission locator leaked' }; "
        "$row=Read-DaemonInstanceRecord $guard.record_path; "
        "if ($row -isnot [hashtable] -or $row.purpose -ne 'production') { throw 'record shape changed' }; "
        "$process=[Diagnostics.Process]::GetCurrentProcess(); "
        "try { $null=Set-DaemonInstanceProcess $reserved $process -Admitted } finally {$process.Dispose()}; "
        "Exit-DaemonInstance $guard; Exit-DaemonInstance $guard; "
        "$null=Invoke-DaemonInstanceReconcile -RegistryRoot $root; "
        "@{pid=$PID;requests=@($script:requests.ToArray())} | ConvertTo-Json -Depth 10 -Compress"
    )
    assert result.returncode == 0, result.stderr
    assert "daemon instance held: " + "a" * 32 + " invalid-record" in result.stderr
    value = json.loads(result.stdout)
    requests = value["requests"]
    assert [request["command"] for request in requests] == [
        "reserve", "enter", "read", "bind", "exit", "reconcile",
    ]
    assert requests[3]["process"]["pid"] == value["pid"]
    assert requests[3]["process"]["birth"].startswith("windows:")
    assert isinstance(requests[3]["process"]["handle"], int)
    assert requests[3]["admitted"] is True
    assert set(requests[3]["receipt"]) == {"instance_id", "registry_root"}
    assert requests[4]["guard_token"] == "b" * 32
    assert set(requests[4]["receipt"]) == {"instance_id", "registry_root"}


@pytest.mark.parametrize("kind", ["maximum-batch", "removed-paths"])
def test_real_python_response_fragments_are_reassembled_by_powershell(tmp_path, monkeypatch, kind):
    from agent_procutil import _daemon_instance_shell as shell

    rows = (
        [{"instance_id": f"{index:032x}", "status": "settled",
          "reason": "finite-worker-exit-observed", "removed": []} for index in range(4096)]
        if kind == "maximum-batch" else
        [{"instance_id": "a" * 32, "status": "settled", "reason": "removed",
          "removed": ["C:/fixture/" + "x" * 2048 + str(index) for index in range(256)]}]
    )
    response = {"ok": True, "nonce": "a" * 32, "sequence": 1, "result": rows}
    output = io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(shell.sys, "stdout", output)
        shell._write_response(response)
    wire = tmp_path / "response.jsonl"
    wire.write_text(output.getvalue(), encoding="ascii")
    assert len(output.getvalue().splitlines()) > 1
    result = _run(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2; "
        f". {_literal(HELPER)}; "
        f"$reader=[IO.StringReader]::new([IO.File]::ReadAllText({_literal(wire)})); "
        "$state=@{nonce=('a'*32);sequence=1;output=$reader}; "
        "$clock=[Diagnostics.Stopwatch]::StartNew(); "
        "$response=Read-DaemonInstanceResponse $state ($reader.ReadLineAsync()) $clock 5; "
        "$response | ConvertTo-Json -Depth 6 -Compress"
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == response


@pytest.mark.parametrize("fault", ["nonce", "sequence", "part", "missing-tail", "mixed-frame"])
def test_powershell_fragment_reader_refuses_uncorrelated_or_partial_outcomes(tmp_path, monkeypatch, fault):
    from agent_procutil import _daemon_instance_shell as shell

    output = io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(shell.sys, "stdout", output)
        shell._write_response({"ok": True, "nonce": "a" * 32, "sequence": 1, "result": "x" * 600000})
    frames = [json.loads(line) for line in output.getvalue().splitlines()]
    if fault == "missing-tail":
        frames.pop()
    elif fault == "mixed-frame":
        frames[1] = {"ok": True, "nonce": "a" * 32, "sequence": 1, "result": []}
    else:
        frames[1][fault] = "b" * 32 if fault == "nonce" else 9
    wire = tmp_path / "response.jsonl"
    wire.write_text("".join(json.dumps(frame) + "\n" for frame in frames), encoding="ascii")
    result = _run(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2; "
        f". {_literal(HELPER)}; "
        f"$reader=[IO.StringReader]::new([IO.File]::ReadAllText({_literal(wire)})); "
        "$state=@{nonce=('a'*32);sequence=1;output=$reader}; "
        "$clock=[Diagnostics.Stopwatch]::StartNew(); $refusal=$null; "
        "try { $null=Read-DaemonInstanceResponse $state ($reader.ReadLineAsync()) $clock 5 } "
        "catch { $refusal=$_.Exception.Message }; "
        "if (-not $refusal) { throw 'partial response was accepted' }; "
        "[Console]::Out.WriteLine($refusal)"
    )
    assert result.returncode == 0, result.stderr
    assert "Daemon instance" in result.stdout


@pytest.mark.parametrize("kind", ["dtssh", "wsl"])
def test_standalone_publishers_copy_complete_helper_source_without_host_effects(tmp_path, kind):
    root = Path(__file__).resolve().parents[3]
    helpers = tmp_path / "source"
    helpers.mkdir()
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "proof").write_text("retain")
    if kind == "dtssh":
        source = (root / "plugins/agent-ssh/transports/dtssh/scripts/install-host.ps1").read_text()
        resolver = source.split("function Get-DaemonInstanceHelperRoot {", 1)[1].split(
            "\nfunction Install-Shortcut {", 1,
        )[0]
        publisher = source.split("function Install-Shortcut {", 1)[1].split(
            "    Copy-Item $LauncherSrc $LauncherDst -Force", 1,
        )[0]
        body = (
            "function Get-DaemonInstanceHelperRoot {" + resolver + "\n"
            "function Publish-Only {" + publisher + "\n}\n"
        )
        files = ("daemon_instances.ps1", "daemon_instance_runtime.pyz", "installer-instance-reconcile.ps1")
        command = "Publish-Only"
    else:
        source = (root / "plugins/wsl-setup/skills/setting-up-wsl/references/wsl-keepalive.ps1").read_text()
        publisher = source.split("function Publish-KeepaliveInstanceHelpers {", 1)[1].split(
            "\nfunction Invoke-Install {", 1,
        )[0]
        body = "function Publish-KeepaliveInstanceHelpers {" + publisher + "\n"
        files = ("wsl-keepalive-owner.ps1", "daemon_instances.ps1", "daemon_instance_runtime.pyz")
        command = "Publish-KeepaliveInstanceHelpers"
    for name in files:
        (helpers / name).write_bytes(("fixture " + name).encode())
    result = _run(
        "$ErrorActionPreference='Stop'; "
        f"$InstallDir={_literal(installed)}; "
        + body + f"{command}; {command}; [Console]::Out.WriteLine('published')",
        script=helpers / "publish.ps1",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "published"
    assert {path.name for path in installed.iterdir()} == {*files, "proof"}
    for name in files:
        assert (installed / name).read_bytes() == (helpers / name).read_bytes()
    assert (installed / "proof").read_text() == "retain"
