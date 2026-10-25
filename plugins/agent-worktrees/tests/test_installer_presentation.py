"""Native bounded presentation and private-capture replay contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time

import pytest

from test_installer_diagnostics import (
    NativeCapture, POWERSHELLS, SCRIPTS, _available, native_environment, ps_run,
)


@pytest.mark.parametrize("host", POWERSHELLS)
@pytest.mark.parametrize("blocked,expected", [("stdout", 37), ("stderr", 0), ("both", 124)])
def test_native_unread_parent_preserves_capture_deadline_and_modes(tmp_path, host, blocked, expected):
    child, fixture, phase = (tmp_path / name for name in ("unread-child.py", "unread.ps1", "unread.json"))
    volume = 100000 if expected == 124 else 300000
    child.write_text(
        "import os,time\n"
        f"os.write(1,b'o'*{volume})\nos.write(2,b'e'*{volume})\n"
        + ("time.sleep(20)\n" if expected == 124 else f"os._exit({expected})\n"), encoding="utf-8")
    helper, initializer, python, script, report = [
        str(path).replace("'", "''") for path in (
            SCRIPTS / "installer-diagnostics.ps1", SCRIPTS / "installer-human-output.ps1",
            Path(sys.executable), child, phase)]
    fixture.write_text(f"""
$ErrorActionPreference='Stop'
. '{initializer}'
. '{helper}'
Initialize-InstallerDiagnostics 'agent-worktrees'
$capture=New-InstallerDiagnosticCapture -Piped
$capture.Measure=@{{available_ticks=[long]0;read_ticks=[long]0;store_ticks=[long]0;present_ticks=[long]0;polls=0;bytes=0}}
[IO.File]::WriteAllText('{report}.trace','capture ready')
$before=@([InstallerDiagnosticsPipeV1]::Mode([InstallerDiagnosticsPipeV1]::GetStdHandle(-11)),
          [InstallerDiagnosticsPipeV1]::Mode([InstallerDiagnosticsPipeV1]::GetStdHandle(-12)))
[IO.File]::AppendAllText('{report}.trace',"`nmodes queried")
$number=$(if ('{blocked}' -eq 'stderr') {{ -12 }} else {{ -11 }})
$oversized=[InstallerDiagnosticsPipeV1]::Write(
    [InstallerDiagnosticsPipeV1]::GetStdHandle($number),(New-Object byte[] 8192),$true)
[IO.File]::AppendAllText('{report}.trace',"`noversized returned: $oversized")
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName='{python}'
$start.Arguments='-I "{script}"'
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
$start.RedirectStandardOutput=$true
$start.RedirectStandardError=$true
$process=[Diagnostics.Process]::Start($start)
[IO.File]::AppendAllText('{report}.trace',"`nchild started")
$null=$process.Handle
[IO.File]::AppendAllText('{report}.trace',"`nchild handle ready")
$clock=[Diagnostics.Stopwatch]::StartNew()
$exited=Wait-InstallerDiagnosticChild $process $capture $(if ({expected} -eq 124) {{ 2 }} else {{ 10 }})
$waitMs=$clock.ElapsedMilliseconds
[IO.File]::AppendAllText('{report}.trace',("`nwait returned: $waitMs " + ($capture.Measure | Microsoft.PowerShell.Utility\\ConvertTo-Json -Compress)))
if ($exited) {{ $rc=$process.ExitCode }} else {{
    $rc=124
    $process.Kill()
    if (-not $process.WaitForExit(1000)) {{ throw 'owned synthetic root teardown unsettled' }}
}}
$null=Receive-InstallerDiagnosticCapture $process $capture -Final
[IO.File]::AppendAllText('{report}.trace',"`nfinal captured")
Write-InstallerDiagnosticsWarning
Write-InstallerDiagnosticPresentation ('Installer diagnostics: synthetic discovery' + [Environment]::NewLine) -ErrorChannel
Save-InstallerDiagnosticCapture $capture
[IO.File]::AppendAllText('{report}.trace',"`narchive saved")
Complete-InstallerDiagnostics $rc
[IO.File]::AppendAllText('{report}.trace',"`nwriter completed")
$after=@([InstallerDiagnosticsPipeV1]::Mode([InstallerDiagnosticsPipeV1]::GetStdHandle(-11)),
         [InstallerDiagnosticsPipeV1]::Mode([InstallerDiagnosticsPipeV1]::GetStdHandle(-12)))
$record=@{{exited=$exited;rc=$rc;wait_ms=$waitMs;before=$before;after=$after;oversized=$oversized;
    measure=$capture.Measure;frequency=[Diagnostics.Stopwatch]::Frequency;
    stdout_bytes=([IO.FileInfo]::new($capture.Stdout)).Length;
    stderr_bytes=([IO.FileInfo]::new($capture.Stderr)).Length;
    outputs=@($script:InstallerDiagnosticOutputs | ForEach-Object {{
        @{{pending=$_.Pending;peak=$_.Peak;written=$_.Written;zero=$_.ZeroWrites;
            busy=$_.BusyModes;native_failures=$_.NativeFailures;loss=$_.Loss;dropped=$_.Dropped}}
    }});missing=$env:COPILOT_EXTENSIONS_DIAGNOSTICS_PRESENTATION_MISSING}}
[IO.File]::WriteAllText('{report}.tmp',($record | Microsoft.PowerShell.Utility\\ConvertTo-Json -Depth 5 -Compress))
[IO.File]::Move('{report}.tmp','{report}')
$process.Dispose()
exit $rc
""", encoding="utf-8-sig")
    environment = native_environment(host=host, USERPROFILE=str(tmp_path), COPILOT_EXTENSIONS_HUMAN_UTF8="1")
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", None)
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER", None)
    capture = NativeCapture(
        [shutil.which(host), "-NoProfile", "-NonInteractive", "-File", str(fixture)],
        environment, tmp_path / "stdout", tmp_path / "stderr")
    process, started = capture.process, time.monotonic()
    blocked_indices = {0, 1} if blocked == "both" else {0 if blocked == "stdout" else 1}
    try:
        while not phase.exists() and process.poll() is None and time.monotonic() - started < 20:
            for index, (source, target) in enumerate(zip((process.stdout, process.stderr), capture.files)):
                if index not in blocked_indices:
                    available = min(_available(source.fileno()), 8192)
                    if available:
                        target.write(os.read(source.fileno(), available))
                        target.flush()
            time.sleep(.01)
        if not phase.exists():
            capture.drain(final=True)
            trace = phase.with_suffix(".json.trace")
            pytest.fail(
                "relay/deadline failed before publishing its result; "
                f"exit={process.poll()}, phase={trace.read_text() if trace.exists() else 'not entered'}, "
                f"stderr={Path(capture.files[1].name).read_text(errors='replace')[-2048:]}"
            )
        record = json.loads(phase.read_text(encoding="utf-8-sig"))
        print("unread-parent-native-evidence " + json.dumps(record, ensure_ascii=True))
        assert record["rc"] == expected
        assert record["before"] == record["after"] == [0, 0]
        assert record["oversized"] == 0
        assert record["missing"] == "1"
        assert record["wait_ms"] < (3000 if expected == 124 else 10000)
        assert record["exited"] is (expected != 124)
        for index, output in enumerate(record["outputs"]):
            assert output["pending"] == 0 and output["peak"] <= 262144
            assert output["native_failures"] == 0
            if index in blocked_indices:
                assert output["written"] > 0 and output["zero"] + output["busy"] > 0, record
                assert output["busy"] > 0
                assert output["loss"] and output["dropped"] > 0
            else:
                assert output["loss"] is None and output["dropped"] == 0
        assert record["stdout_bytes"] > 4096 and record["stderr_bytes"] > 4096
        if expected != 124:
            assert record["stdout_bytes"] == record["stderr_bytes"] == volume
        loss = list((tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/presentation-loss.json"))
        assert len(loss) == 1
        assert all(item["reason"] for item in json.loads(loss[0].read_text(encoding="utf-8-sig")))
        # Only now allow reads from the deliberately blocked parent channels.
        assert capture.wait(max(0, 20 - (time.monotonic() - started))) == expected
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        capture.close()
    archive = "\n".join(path.read_text(encoding="utf-8") for path in
                        (tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.log"))
    assert "o" * 8192 in archive and "e" * 8192 in archive
    if blocked == "stdout":
        assert (tmp_path / "stderr").read_bytes() == (
            b"e" * volume
            + b"WARNING: installer diagnostics unavailable; continuing with missing diagnostics.\r\n"
            + b"Installer diagnostics: synthetic discovery\r\n")
    if blocked == "stderr":
        assert (tmp_path / "stdout").read_bytes() == b"o" * volume


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_presentation_preserves_legacy_encoding_bytes(tmp_path, host):
    child = tmp_path / "legacy-child.py"
    child.write_text(
        "import os\nos.write(1,b'legacy caf\\xe9\\n')\n"
        "os.write(2,b'failure caf\\xe9\\n')\nos._exit(37)\n", encoding="utf-8")
    python, script = (str(path).replace("'", "''") for path in (Path(sys.executable), child))
    result = ps_run(tmp_path, host, f"""
[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(1252)
$legacyOut=[IO.StreamWriter]::new([Console]::OpenStandardOutput(),[Text.Encoding]::GetEncoding(1252))
$legacyError=[IO.StreamWriter]::new([Console]::OpenStandardError(),[Text.Encoding]::GetEncoding(1252))
[Console]::SetOut($legacyOut)
[Console]::SetError($legacyError)
Initialize-InstallerDiagnostics 'agent-worktrees'
$env:COPILOT_EXTENSIONS_HUMAN_UTF8='0'
$capture=New-InstallerDiagnosticCapture -Piped
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName='{python}'
$start.Arguments='-I "{script}"'
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
$start.RedirectStandardOutput=$true
$start.RedirectStandardError=$true
$process=[Diagnostics.Process]::Start($start)
$null=$process.Handle
if (-not (Wait-InstallerDiagnosticChild $process $capture 10)) {{ throw 'legacy child did not exit' }}
$rc=$process.ExitCode
Save-InstallerDiagnosticCapture $capture
Complete-InstallerDiagnostics $rc
$process.Dispose()
exit $rc
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "1"})
    assert result.returncode == 37, result.stderr
    assert (tmp_path / "fixture.stdout").read_bytes() == b"legacy caf\xe9\n"
    assert (tmp_path / "fixture.stderr").read_bytes() == b"failure caf\xe9\n"
    archive = "\n".join(path.read_text(encoding="utf-8") for path in
                        (tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.log"))
    assert "legacy café" in archive and "failure café" in archive


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_capture_reuses_current_buffer_before_reading_raw_backlog(tmp_path, host):
    child = tmp_path / "buffer-child.py"
    payload = b"current-buffer\n" * 100
    child.write_text(f"import os\nos.write(1,{payload!r})\nos._exit(37)\n", encoding="utf-8")
    python, script, report = [
        str(path).replace("'", "''") for path in (Path(sys.executable), child, tmp_path / "buffer.json")
    ]
    result = ps_run(tmp_path, host, f"""
Initialize-InstallerDiagnostics 'agent-worktrees'
$capture=New-InstallerDiagnosticCapture -Piped
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName='{python}'
$start.Arguments='-I "{script}"'
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
$start.RedirectStandardOutput=$true
$start.RedirectStandardError=$true
$process=[Diagnostics.Process]::Start($start)
if (-not $process.WaitForExit(10000)) {{ throw 'small owned child did not exit' }}
if (-not (Wait-InstallerDiagnosticChild $process $capture 10)) {{ throw 'exited child became a timeout' }}
$state=$script:InstallerDiagnosticPresentation[0]
$record=@{{recorded=$state.Recorded;cursor=$state.Cursor;raw_reads=$state.RawReadBytes;
    captured=([IO.FileInfo]::new($capture.Stdout)).Length;loss=$state.Output.Loss}}
[IO.File]::WriteAllText('{report}',($record | Microsoft.PowerShell.Utility\\ConvertTo-Json -Compress))
$rc=$process.ExitCode
$process.Dispose()
Complete-InstallerDiagnostics $rc
exit $rc
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "1"})
    assert result.returncode == 37, result.stderr
    assert (tmp_path / "fixture.stdout").read_bytes() == payload
    record = json.loads((tmp_path / "buffer.json").read_text(encoding="utf-8-sig"))
    assert record["recorded"] == record["cursor"] == record["captured"] == len(payload)
    assert record["raw_reads"] == 0 and record["loss"] is None


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_replay_preserves_order_and_recorded_cutoff(tmp_path, host):
    report = str(tmp_path / "replay.json").replace("'", "''")
    result = ps_run(tmp_path, host, f"""
Initialize-InstallerDiagnostics 'agent-worktrees'
$capture=New-InstallerDiagnosticCapture -Piped
$state=$script:InstallerDiagnosticPresentation[0]
$prefix=([string]::Concat('replay café 😃',[char]10) * 20000)
$bytes=[Text.Encoding]::UTF8.GetBytes($prefix)
[IO.File]::WriteAllBytes($capture.Stdout,$bytes)
$state.RawPath=$capture.Stdout
$state.Recorded=[long]$bytes.Length
$state.Final=$true
[IO.File]::AppendAllText($capture.Stdout,'EXCLUDED-UNRECORDED-TAIL',[Text.UTF8Encoding]::new($false))
$script:InstallerDiagnosticClock=[Diagnostics.Stopwatch]::StartNew()
$script:InstallerDiagnosticBudgetMilliseconds=10000
Complete-InstallerDiagnosticPresentation
$record=@{{recorded=$state.Recorded;cursor=$state.Cursor;final=$state.DecoderFinal;
    raw_reads=$state.RawReadBytes;
    peak=$state.Output.Peak;pending=$state.Output.Pending;loss=$state.Output.Loss;
    mode=[InstallerDiagnosticsPipeV1]::Mode([InstallerDiagnosticsPipeV1]::GetStdHandle(-11))}}
[IO.File]::WriteAllText('{report}.tmp',($record | Microsoft.PowerShell.Utility\\ConvertTo-Json -Compress))
[IO.File]::Move('{report}.tmp','{report}')
Complete-InstallerDiagnostics 0
exit 0
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "1"})
    expected = ("replay café 😃\n" * 20000).encode("utf-8")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "fixture.stdout").read_bytes() == expected
    record = json.loads((tmp_path / "replay.json").read_text(encoding="utf-8-sig"))
    assert record["recorded"] == record["cursor"] == len(expected) > 262144
    assert record["raw_reads"] == len(expected)
    assert record["final"] and record["pending"] == record["mode"] == 0
    assert record["peak"] <= 262144 and record["loss"] is None


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_midstream_append_failure_preserves_known_prefix(tmp_path, host):
    child = tmp_path / "tail-child.py"
    tail = b"must-not-overtake-recorded-prefix\n"
    child.write_text(f"import os\nos.write(1,{tail!r})\nos._exit(37)\n", encoding="utf-8")
    python, script = (str(path).replace("'", "''") for path in (Path(sys.executable), child))
    result = ps_run(tmp_path, host, f"""
Initialize-InstallerDiagnostics 'agent-worktrees'
$capture=New-InstallerDiagnosticCapture -Piped
$state=$script:InstallerDiagnosticPresentation[0]
$prefix=[Text.Encoding]::UTF8.GetBytes(('p' * 300000))
[IO.File]::WriteAllBytes($capture.Stdout,$prefix)
$state.RawPath=$capture.Stdout
$state.Recorded=[long]$prefix.Length
$capture.Stdout=$script:InstallerDiagnostic.Directory
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName='{python}'
$start.Arguments='-I "{script}"'
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
$start.RedirectStandardOutput=$true
$start.RedirectStandardError=$true
$process=[Diagnostics.Process]::Start($start)
$null=$process.Handle
if (-not (Wait-InstallerDiagnosticChild $process $capture 10)) {{ throw 'synthetic tail did not exit' }}
$rc=$process.ExitCode
Complete-InstallerDiagnostics $rc
$process.Dispose()
exit $rc
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "1"})
    assert result.returncode == 37, result.stderr
    assert (tmp_path / "fixture.stdout").read_bytes() == b"p" * 300000
    records = json.loads(next((tmp_path / ".copilot-extensions" / "installer-diagnostics").glob(
        "*/presentation-loss.json")).read_text(encoding="utf-8-sig"))
    loss = next(item for item in records if item["reason"] == "raw-append-failed-with-backlog")
    assert loss["recorded"] == loss["replayed"] == 300000
    assert loss["raw_pending"] == 0 and loss["input_lost"] == len(tail)


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_replay_uses_remaining_deadline_without_changing_success(tmp_path, host):
    child, fixture, phase = (tmp_path / name for name in ("deadline-child.py", "deadline.ps1", "deadline.json"))
    child.write_text("import os\nos.write(1,b'p')\nos._exit(0)\n", encoding="utf-8")
    helper, initializer, python, script, report = [
        str(path).replace("'", "''") for path in (
            SCRIPTS / "installer-diagnostics.ps1", SCRIPTS / "installer-human-output.ps1",
            Path(sys.executable), child, phase)]
    fixture.write_text(f"""
$ErrorActionPreference='Stop'
. '{initializer}'
. '{helper}'
Initialize-InstallerDiagnostics 'agent-worktrees'
$capture=New-InstallerDiagnosticCapture -Piped
$prefix=[Text.Encoding]::UTF8.GetBytes(('p' * 99999))
[IO.File]::WriteAllBytes($capture.Stdout,$prefix)
$state=$script:InstallerDiagnosticPresentation[0]
$state.RawPath=$capture.Stdout
$state.Recorded=[long]$prefix.Length
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName='{python}'
$start.Arguments='-I "{script}"'
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
$start.RedirectStandardOutput=$true
$start.RedirectStandardError=$true
$process=[Diagnostics.Process]::Start($start)
$null=$process.Handle
if (-not $process.WaitForExit(10000)) {{ throw 'small owned child did not establish exited success' }}
$exited=Wait-InstallerDiagnosticChild $process $capture 1
$waitMs=$script:InstallerDiagnosticClock.ElapsedMilliseconds
if (-not $exited) {{ throw 'already-exited success became a timeout' }}
$rc=$process.ExitCode
$beforeCalls=@($script:InstallerDiagnosticOutputs | ForEach-Object {{ $_.Calls }})
Write-InstallerDiagnosticPresentation 'late-discovery' -ErrorChannel
$null=Receive-InstallerDiagnosticCapture $process $capture -Final
Save-InstallerDiagnosticCapture $capture
Complete-InstallerDiagnostics $rc
$record=@{{rc=$rc;wait_ms=$waitMs;before_calls=$beforeCalls;
    after_calls=@($script:InstallerDiagnosticOutputs | ForEach-Object {{ $_.Calls }});
    loss=$script:InstallerDiagnosticOutputs[0].Loss;bytes=([IO.FileInfo]::new($capture.Stdout)).Length;
    mode=[InstallerDiagnosticsPipeV1]::Mode([InstallerDiagnosticsPipeV1]::GetStdHandle(-11))}}
[IO.File]::WriteAllText('{report}.tmp',($record | Microsoft.PowerShell.Utility\\ConvertTo-Json -Compress))
[IO.File]::Move('{report}.tmp','{report}')
$process.Dispose()
exit $rc
""", encoding="utf-8-sig")
    environment = native_environment(host=host, USERPROFILE=str(tmp_path), COPILOT_EXTENSIONS_HUMAN_UTF8="1")
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", None)
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER", None)
    capture = NativeCapture(
        [shutil.which(host), "-NoProfile", "-NonInteractive", "-File", str(fixture)],
        environment, tmp_path / "stdout", tmp_path / "stderr")
    process, started = capture.process, time.monotonic()
    try:
        while not phase.exists() and process.poll() is None and time.monotonic() - started < 20:
            available = min(_available(process.stderr.fileno()), 8192)
            if available:
                capture.files[1].write(os.read(process.stderr.fileno(), available))
                capture.files[1].flush()
            time.sleep(.01)
        assert phase.exists(), "replay restarted a budget while parent remained unread"
        record = json.loads(phase.read_text(encoding="utf-8-sig"))
        assert record["rc"] == record["mode"] == 0 and record["bytes"] == 100000
        assert record["wait_ms"] < 1500 and record["loss"] == "presentation-deadline"
        assert record["before_calls"] == record["after_calls"]
        assert capture.wait(max(0, 20 - (time.monotonic() - started))) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        capture.close()


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_final_snapshot_is_not_resampled_for_retained_descendant(tmp_path, host):
    child, descendant, release, done = (tmp_path / name for name in (
        "snapshot-root.py", "snapshot-descendant.py", "release-snapshot", "snapshot-done"))
    descendant.write_text("""
import os,pathlib,sys,time
deadline=time.monotonic()+5
while not pathlib.Path(sys.argv[1]).exists() and time.monotonic()<deadline:
    time.sleep(.01)
if pathlib.Path(sys.argv[1]).exists():
    os.write(1,b"late-descendant-must-not-be-resampled\\n")
    pathlib.Path(sys.argv[2]).touch()
""", encoding="utf-8")
    child.write_text("""
import os,subprocess,sys
subprocess.Popen([sys.executable,*sys.argv[1:]],stdout=sys.stdout,stderr=sys.stderr,
                 creationflags=subprocess.CREATE_NO_WINDOW)
os.write(1,b"root-prefix\\n")
os._exit(37)
""", encoding="utf-8")
    python, script, leaf, ack, finished = [
        str(path).replace("'", "''") for path in (Path(sys.executable), child, descendant, release, done)]
    try:
        result = ps_run(tmp_path, host, f"""
Initialize-InstallerDiagnostics 'agent-worktrees'
$capture=New-InstallerDiagnosticCapture -Piped
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName='{python}'
$start.Arguments='-I "{script}" "{leaf}" "{ack}" "{finished}"'
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
$start.RedirectStandardOutput=$true
$start.RedirectStandardError=$true
$process=[Diagnostics.Process]::Start($start)
$null=$process.Handle
if (-not (Wait-InstallerDiagnosticChild $process $capture 10)) {{ throw 'synthetic root did not exit' }}
$rc=$process.ExitCode
[IO.File]::WriteAllText('{ack}','go')
$clock=[Diagnostics.Stopwatch]::StartNew()
while (-not [IO.File]::Exists('{finished}') -and $clock.ElapsedMilliseconds -lt 5000) {{
    Start-Sleep -Milliseconds 10
}}
if (-not [IO.File]::Exists('{finished}')) {{ throw 'owned finite descendant did not settle' }}
$copied=Receive-InstallerDiagnosticCapture $process $capture -Final
if ($copied -ne 0) {{ throw 'final native snapshot was sampled twice' }}
Save-InstallerDiagnosticCapture $capture
Complete-InstallerDiagnostics $rc
$process.Dispose()
exit $rc
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "1"})
        assert result.returncode == 37, result.stderr
        assert result.stdout == "root-prefix\n"
        raw = b"".join(path.read_bytes() for path in
                       (tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.stdout.raw"))
        assert raw == b"root-prefix\n"
    finally:
        release.touch(exist_ok=True)
        deadline = time.monotonic() + 8
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert done.exists(), "owned finite descendant did not finish"


@pytest.mark.parametrize("host", POWERSHELLS)
@pytest.mark.parametrize("kind", ["initialization", "write"])
def test_native_presentation_failure_is_durably_explicit(tmp_path, host, kind):
    readonly = tmp_path / "readonly-input"
    readonly.write_bytes(b"synthetic ordinary file")
    path = str(readonly).replace("'", "''")
    if kind == "initialization":
        failure = """
function Add-Type { throw 'synthetic native compiler unavailable' }
$capture=New-InstallerDiagnosticCapture -Piped
if ($capture -or -not $script:InstallerDiagnosticNativeFailed) { throw 'native initialization did not fail' }
Write-InstallerDiagnosticsWarning
"""
    else:
        failure = f"""
if (-not (Initialize-InstallerDiagnosticNative)) {{ throw 'native API unavailable' }}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class SyntheticStandardHandle {{
    [DllImport("kernel32.dll",SetLastError=true)]
    public static extern bool SetStdHandle(int number,IntPtr handle);
}}
'@
$original=[InstallerDiagnosticsPipeV1]::GetStdHandle(-11)
$reader=[IO.File]::OpenRead('{path}')
try {{
    if (-not [SyntheticStandardHandle]::SetStdHandle(-11,$reader.SafeFileHandle.DangerousGetHandle())) {{
        throw 'synthetic destination could not be selected'
    }}
    $output=[InstallerDiagnosticOutput]::new(-11,[Console]::Out)
    $script:InstallerDiagnosticOutputs[0]=$output
    $text='synthetic native write failure'.ToCharArray()
    $output.Add($text,$text.Length,$true)
    $output.Pump()
    if ($output.Loss -ne 'native-write-5' -or $output.NativeFailures -ne 1 -or $output.Calls -ne 1) {{
        throw 'native readonly write failure was hidden'
    }}
}} finally {{
    $restored=[SyntheticStandardHandle]::SetStdHandle(-11,$original)
    $reader.Dispose()
    if (-not $restored) {{ throw 'synthetic destination restoration failed' }}
}}
Save-InstallerDiagnosticPresentationState
"""
    result = ps_run(tmp_path, host, """
Initialize-InstallerDiagnostics 'agent-worktrees'
""" + failure + """
Complete-InstallerDiagnostics 37
[Console]::Out.Write('{"native":37}')
exit 37
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "1"})
    assert result.returncode == 37, result.stderr
    assert result.stdout == '{"native":37}'
    root = tmp_path / ".copilot-extensions" / "installer-diagnostics"
    losses = list(root.glob("*/presentation-loss.json"))
    assert len(losses) == 1
    records = json.loads(losses[0].read_text(encoding="utf-8-sig"))
    expected = "native-initialization-failed" if kind == "initialization" else "native-write-5"
    assert any(record["reason"] == expected for record in records)
    archive = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*/*.log"))
    assert "installer finished; exit=37" in archive


@pytest.mark.parametrize("host", POWERSHELLS)
def test_storage_warning_before_human_initializer_rebinds_requested_utf8(tmp_path, host):
    (tmp_path / ".copilot-extensions").write_text("occupied", encoding="utf-8")
    initializer = str(SCRIPTS / "installer-human-output.ps1").replace("'", "''")
    entry = (SCRIPTS / "install.ps1").read_text()
    setup = ". (Join-Path $PSScriptRoot 'installer-human-output.ps1')"
    assert entry.index("[void](Reset-InstallerDiagnosticPresentationOutputs)", entry.index(setup)) > entry.index(setup)
    result = ps_run(tmp_path, host, f"""
[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(1252)
[Console]::SetOut([IO.StreamWriter]::new([Console]::OpenStandardOutput(),[Text.Encoding]::GetEncoding(1252)))
[Console]::SetError([IO.StreamWriter]::new([Console]::OpenStandardError(),[Text.Encoding]::GetEncoding(1252)))
$env:COPILOT_EXTENSIONS_HUMAN_UTF8='1'
Initialize-InstallerDiagnostics 'agent-worktrees'
. '{initializer}'
[void](Reset-InstallerDiagnosticPresentationOutputs)
Write-InstallerDiagnosticPresentation ('requested café 😃' + [char]10)
Complete-InstallerDiagnostics 37
exit 37
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "0"})
    assert result.returncode == 37, result.stderr
    assert (tmp_path / "fixture.stdout").read_bytes() == "requested café 😃\n".encode("utf-8")
    assert b"missing diagnostics" in (tmp_path / "fixture.stderr").read_bytes()


@pytest.mark.parametrize("host", POWERSHELLS)
def test_next_capture_recovers_after_previous_presentation_cutoff(tmp_path, host):
    child = tmp_path / "next-child.py"
    payload = "next child café 😃\n".encode("utf-8")
    child.write_text(f"import os\nos.write(1,{payload!r})\nos._exit(37)\n", encoding="utf-8")
    python, script, report = [
        str(path).replace("'", "''") for path in (Path(sys.executable), child, tmp_path / "next.json")
    ]
    result = ps_run(tmp_path, host, f"""
Initialize-InstallerDiagnostics 'agent-worktrees'
$first=New-InstallerDiagnosticCapture -Piped
[IO.File]::WriteAllText($first.Stdout,'unpresented',[Text.UTF8Encoding]::new($false))
$old=$script:InstallerDiagnosticPresentation[0]
$old.RawPath=$first.Stdout
$old.Recorded=11
$old.Final=$true
$script:InstallerDiagnosticClock=[Diagnostics.Stopwatch]::StartNew()
$script:InstallerDiagnosticBudgetMilliseconds=0
Complete-InstallerDiagnosticPresentation
if ($old.Output.Loss -ne 'presentation-deadline') {{ throw 'prior cutoff was not established' }}
$capture=New-InstallerDiagnosticCapture -Piped
if ($script:InstallerDiagnosticOutputs[0].Loss) {{ throw 'prior cutoff poisoned the next child' }}
$start=[Diagnostics.ProcessStartInfo]::new()
$start.FileName='{python}'
$start.Arguments='-I "{script}"'
$start.UseShellExecute=$false
$start.CreateNoWindow=$true
$start.RedirectStandardOutput=$true
$start.RedirectStandardError=$true
$process=[Diagnostics.Process]::Start($start)
if (-not (Wait-InstallerDiagnosticChild $process $capture 10)) {{ throw 'next owned child did not exit' }}
$record=@{{loss=$script:InstallerDiagnosticOutputs[0].Loss;missing=$env:COPILOT_EXTENSIONS_DIAGNOSTICS_PRESENTATION_MISSING}}
[IO.File]::WriteAllText('{report}',($record | Microsoft.PowerShell.Utility\\ConvertTo-Json -Compress))
$rc=$process.ExitCode
$process.Dispose()
Save-InstallerDiagnosticCapture $capture
Complete-InstallerDiagnostics $rc
exit $rc
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "1"})
    assert result.returncode == 37, result.stderr
    assert (tmp_path / "fixture.stdout").read_bytes() == payload
    record = json.loads((tmp_path / "next.json").read_text(encoding="utf-8-sig"))
    assert record["loss"] is None and record["missing"] == "1"
    history = next((tmp_path / ".copilot-extensions" / "installer-diagnostics").glob(
        "*/presentation-loss.history.jsonl",
    ))
    records = [record for line in history.read_text().splitlines() for record in json.loads(line)]
    assert any(record["reason"] == "presentation-deadline" for record in records)
