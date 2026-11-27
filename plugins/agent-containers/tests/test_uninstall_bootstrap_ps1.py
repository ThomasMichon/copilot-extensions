"""Native Windows selection refusal and owned stalled-process settlement."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from .test_containers_legacy_runtime_uninstall import cases
from .test_uninstall_bootstrap import Removal, removal as removal

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Native Windows bootstrap capture"),
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]
HEADLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _function(removal: Removal) -> str:
    text = (removal.payload / "scripts/legacy-uninstall.ps1").read_text(encoding="utf-8")
    start = text.index("function Get-LegacyUninstallPython {")
    return text[start:text.index("\n}\n", start) + 2]


def _harness(removal: Removal, host: str, code: str, *, legacy_reader: bool = False) -> dict[str, object]:
    reader = ""
    if legacy_reader:
        reader = (
            "function Resolve-NativeBootstrapPython { param($MinimumMinor,$Action,$Root,$BootstrapDirectory) "
            "[IO.File]::WriteAllText($env:CONTAINERS_TEST_READER_USED,'unexpected'); return $null }\n"
        )
    script = removal.payload / "scripts/test-bootstrap.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version 2\n"
        f". {_ps(script.parent / 'versioned_runtime_native.ps1')}\n"
        f"$InstallDir={_ps(removal.root)}\n"
        + reader + _function(removal) + "\n" + code,
        encoding="utf-8",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env=removal.env, cwd=removal.root.parent, capture_output=True, text=True,
        timeout=25, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    details: dict[str, object] = json.loads(result.stdout)
    return details


@pytest.mark.parametrize("host", cases.HOSTS)
@pytest.mark.parametrize("stage", ["version", "find"])
def test_windows_stalled_uv_retires_owned_root_and_child_before_return(
    removal: Removal, host: str, stage: str,
) -> None:
    _, python, _ = cases.prepared_python_inputs("cpython@3.11.18")
    receipt = removal.root.parent / "stalled-uv.json"
    stall = removal.root.parent / "stalled-uv.ps1"
    stall.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$start=New-Object Diagnostics.ProcessStartInfo\n"
        "$start.FileName=$env:CONTAINERS_TEST_STALL_PYTHON\n"
        """$start.Arguments='-I -c "import time; time.sleep(60)"'\n"""
        "$start.UseShellExecute=$false\n$start.CreateNoWindow=$true\n"
        "$child=[Diagnostics.Process]::Start($start)\n"
        "$root=[Diagnostics.Process]::GetCurrentProcess()\n"
        "try {\n"
        " $record=@{root_pid=$PID;root_birth=$root.StartTime.ToUniversalTime().ToFileTimeUtc();"
        "child_pid=$child.Id;child_birth=$child.StartTime.ToUniversalTime().ToFileTimeUtc()}\n"
        " [IO.File]::WriteAllText($env:CONTAINERS_TEST_STALL_RECEIPT,"
        "($record|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))\n"
        "} finally {$root.Dispose();$child.Dispose()}\n"
        "Start-Sleep -Seconds 60\n",
        encoding="utf-8",
    )
    removal.env.update(
        CONTAINERS_TEST_STALL_PYTHON=str(python), CONTAINERS_TEST_STALL_RECEIPT=str(receipt),
        CONTAINERS_TEST_STALL_HOST=host, CONTAINERS_TEST_STALL_SCRIPT=str(stall),
    )
    provider = removal.payload / "scripts/bootstrap-uv.ps1"
    text = provider.read_text(encoding="utf-8")
    needle = "    $result = & $InvokeCaptured @options\n"
    condition = "$Arguments[0] -eq '--version'" if stage == "version" else "$Arguments[0] -eq 'python'"
    provider.write_text(text.replace(needle, (
        f"    if ({condition}) {{\n"
        "        $options.FilePath=$env:CONTAINERS_TEST_STALL_HOST\n"
        "        $options.ArgList=@('-NoProfile','-NonInteractive','-File',$env:CONTAINERS_TEST_STALL_SCRIPT)\n"
        "    }\n" + needle
    )), encoding="utf-8", newline="\n")
    helper = removal.payload / "scripts/legacy-uninstall.ps1"
    text = helper.read_text(encoding="utf-8")
    assert text.count("$budget = 30000") == 1
    helper.write_text(text.replace("$budget = 30000", "$budget = 8000"), encoding="utf-8", newline="\n")
    code = """
$watch=[Diagnostics.Stopwatch]::StartNew()
$failure=''
try { [void](Get-LegacyUninstallPython) } catch { $failure=$_.Exception.Message }
$elapsed=$watch.ElapsedMilliseconds
if (-not [IO.File]::Exists($env:CONTAINERS_TEST_STALL_RECEIPT)) { throw 'UV did not reach its owned child' }
$record=Get-Content -Raw -LiteralPath $env:CONTAINERS_TEST_STALL_RECEIPT|ConvertFrom-Json
function Test-SameLiveProcess {
    param([int]$Identity,[long]$Birth)
    $process=$null
    try {
        $process=[Diagnostics.Process]::GetProcessById($Identity)
        return -not $process.HasExited -and $process.StartTime.ToUniversalTime().ToFileTimeUtc() -eq $Birth
    } catch { return $false } finally { if ($process) { $process.Dispose() } }
}
@{failure=$failure;milliseconds=$elapsed;
  root_live=(Test-SameLiveProcess $record.root_pid $record.root_birth);
  child_live=(Test-SameLiveProcess $record.child_pid $record.child_birth);
  cleanup_confirmed=($null -eq [AppDomain]::CurrentDomain.GetData('CopilotExtensions.BootstrapCapture.Unconfirmed'))
} | ConvertTo-Json -Compress
"""
    details = _harness(removal, host, code)
    assert "deadline" in str(details["failure"]).lower(), details
    elapsed = details["milliseconds"]
    assert isinstance(elapsed, (int, float)) and 0 < elapsed < 10000, details
    assert details["cleanup_confirmed"] is True, details
    assert details["root_live"] is False and details["child_live"] is False, details
    assert not (removal.root / ".install.lock").exists()
    assert (removal.root / "versions/1.2.3/runtime.txt").read_bytes() == b"runtime"


@pytest.mark.parametrize("host", cases.HOSTS)
def test_windows_selected_venv_is_rejected_before_external_base_can_hide_it(
    removal: Removal, host: str,
) -> None:
    _, python, _ = cases.prepared_python_inputs("cpython@3.11.18")
    selected = removal.root / "selected-bootstrap"
    built = subprocess.run(
        [str(python), "-I", "-B", "-m", "venv", "--without-pip", str(selected)],
        env=removal.env, capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    executable = selected / "Scripts/python.exe"
    probe = subprocess.run(
        [str(executable), "-I", "-B", "-c",
         "import json,sys; print(json.dumps({'base':sys._base_executable,'prefix':sys.prefix}))"],
        env=removal.env, capture_output=True, text=True, timeout=10, creationflags=HEADLESS,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    actual = json.loads(probe.stdout)
    assert Path(actual["prefix"]).is_relative_to(removal.root)
    assert not Path(actual["base"]).resolve().is_relative_to(removal.root.resolve())
    proof = removal.root.parent / "selected-probe.json"
    removal.env["CONTAINERS_TEST_SELECTION_PROOF"] = str(proof)
    reader = removal.payload / "scripts/versioned_runtime_native.ps1"
    text = reader.read_text(encoding="utf-8")
    needle = "assert sys.version_info >= (3,MINIMUM_MINOR)\n"
    assert text.count(needle) == 1
    reader.write_text(text.replace(needle, (
        "import os\n"
        "Path(os.environ['CONTAINERS_TEST_SELECTION_PROOF']).write_text(json.dumps("
        "{'executable':sys.executable,'prefix':sys.prefix,'base':sys._base_executable}))\n"
        + needle
    )), encoding="utf-8", newline="\n")
    provider = removal.payload / "scripts/bootstrap-uv.ps1"
    text = provider.read_text(encoding="utf-8")
    needle = "    $result = & $InvokeCaptured @options\n"
    provider.write_text(text.replace(needle, (
        "    if ($Arguments[0] -eq 'python') {\n"
        f"        return [pscustomobject]@{{ExitCode=0;Stdout={_ps(executable)};Stderr=''}}\n"
        "    }\n" + needle
    )), encoding="utf-8", newline="\n")
    marker = removal.root.parent / "finite-helper-entered"
    helper = removal.payload / "scripts/installer_engine_uninstall.py"
    helper.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('unexpected')\nraise SystemExit(98)\n",
        encoding="utf-8",
    )
    result = cases._run(host, removal.root, removal.env, payload=removal.payload)
    assert result.returncode != 0, result.stdout + result.stderr
    observed = json.loads(proof.read_text(encoding="utf-8"))
    assert Path(observed["prefix"]).is_relative_to(removal.root)
    assert not Path(observed["base"]).resolve().is_relative_to(removal.root.resolve())
    assert Path(observed["executable"]).samefile(executable)
    assert not marker.exists() and not (removal.root / ".install.lock").exists()
    assert executable.exists()
    assert (removal.root / "versions/1.2.3/runtime.txt").read_bytes() == b"runtime"


@pytest.mark.parametrize("host", cases.HOSTS)
def test_windows_old_reader_without_capture_contract_refuses_before_invocation(
    removal: Removal, host: str,
) -> None:
    marker = removal.root.parent / "old-reader-entered"
    removal.env["CONTAINERS_TEST_READER_USED"] = str(marker)
    details = _harness(removal, host, """
$failure=''
try { [void](Get-LegacyUninstallPython) } catch { $failure=$_.Exception.Message }
@{failure=$failure}|ConvertTo-Json -Compress
""", legacy_reader=True)
    assert "lacks bounded capture" in str(details["failure"]), details
    assert not marker.exists() and not removal.calls.exists()
    assert not (removal.root / ".install.lock").exists()
