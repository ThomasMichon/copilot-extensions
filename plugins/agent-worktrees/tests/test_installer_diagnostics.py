"""Offline native-entry and durable-capture controls; never install a runtime."""

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from update_telemetry.human import _available


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
POWERSHELLS = [host for host in ("powershell.exe", "pwsh") if os.name == "nt" and shutil.which(host)]


def native_environment(*, host=None, **values):
    environment = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    environment.update(values)
    if host:
        environment["PSModulePath"] = str(Path(shutil.which(host)).parent / "Modules")
    return environment


def constructed_review_records():
    credential = "synthetic-review-token"
    records = []
    for prefix in ("Authorization:", "http.extraheader=", "http.extraheader=Authorization:"):
        for newline in ("\n", "\r\n"):
            for scheme in ("bea" + "rer", "ba" + "sic"):
                for fold in (" ", newline):
                    value = prefix + fold + scheme + newline + credential
                    assert credential in value
                    records.append(value + newline + "useful review error café" + newline)
            records.append(prefix + newline + credential + newline + "useful review error café" + newline)
    return records, credential


def load_diagnostics():
    spec = importlib.util.spec_from_file_location("diagnostics_test_helper", SCRIPTS / "installer_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeCapture:
    """Drain both fixture pipes without joining a descendant's inherited EOF."""

    def __init__(self, argv, environment, stdout, stderr):
        self.argv = argv
        self.files = [stdout.open("wb"), stderr.open("wb")]
        try:
            self.process = subprocess.Popen(
                argv, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except BaseException:
            for stream in self.files:
                stream.close()
            raise

    def drain(self, final=False):
        for source, target in zip((self.process.stdout, self.process.stderr), self.files):
            remaining = _available(source.fileno())
            if not final:
                remaining = min(remaining, 8192)
            while remaining:
                data = os.read(source.fileno(), min(remaining, 8192))
                if not data:
                    break
                target.write(data)
                target.flush()
                remaining -= len(data)

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while self.process.poll() is None:
            self.drain()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.argv, seconds)
            time.sleep(.01)
        self.drain(final=True)
        return self.process.returncode

    def close(self):
        for stream in (self.process.stdout, self.process.stderr, *self.files):
            stream.close()


@pytest.fixture
def diagnostic_context(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    attempt = "a" * 32
    directory = tmp_path / ".copilot-extensions" / "installer-diagnostics" / attempt
    diagnostics = load_diagnostics()
    directory.parent.parent.mkdir()
    diagnostics.files().private_directory(directory.parent, create=True)
    diagnostics.files().private_directory(directory, create=True)
    diagnostics._record(directory / "attempt.json", {
        "schema": "copilot-extensions.installer-diagnostics",
        "attempt": attempt, "plugin": "agent-worktrees", "attribution": "UNATTRIBUTED",
    })
    monkeypatch.setenv("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", attempt)
    monkeypatch.delenv("COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER", raising=False)
    return diagnostics, directory


def ps_run(tmp_path, host, body, *, env=None):
    fixture = tmp_path / "fixture.ps1"
    fixture.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -eq '1') {\n"
        f"    . '{str(SCRIPTS / 'installer-human-output.ps1').replace(chr(39), chr(39) * 2)}'\n"
        "}\n"
        f". '{str(SCRIPTS / 'installer-diagnostics.ps1').replace(chr(39), chr(39) * 2)}'\n"
        + body, encoding="utf-8-sig")
    environment = native_environment(host=host, USERPROFILE=str(tmp_path))
    for name in ("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", "COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER"):
        environment.pop(name, None)
    environment.update(env or {})
    argv = [shutil.which(host), "-NoProfile", "-NonInteractive", "-File", str(fixture)]
    stdout, stderr = tmp_path / "fixture.stdout", tmp_path / "fixture.stderr"
    capture = NativeCapture(argv, environment, stdout, stderr)
    process = capture.process
    try:
        try:
            capture.wait(20)
        except subprocess.TimeoutExpired:
            description = ("native fixture exceeded 20s" +
                           f"; stdout_bytes={stdout.stat().st_size}; stderr_bytes={stderr.stat().st_size}" +
                           f"; raw_sizes={[path.stat().st_size for path in tmp_path.rglob('*.raw')]}")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pytest.fail(description + "; native teardown unsettled", pytrace=False)
            pytest.fail(description, pytrace=False)
    finally:
        capture.close()
    # The fixture itself must not join EOF from native descendants either.
    def snapshot(path):
        with path.open("rb") as stream:
            return stream.read(os.fstat(stream.fileno()).st_size).decode("utf-8", errors="replace")

    return subprocess.CompletedProcess(argv, process.returncode, snapshot(stdout), snapshot(stderr))


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_entry_private_and_clean_stdout(tmp_path, host):
    result = ps_run(tmp_path, host, """
Initialize-InstallerDiagnostics 'agent-worktrees'
Write-InstallerDiagnostic 'useful native failure'
Complete-InstallerDiagnostics 17
[Console]::Out.Write('{"result":17}')
exit 17
""")
    assert result.returncode == 17, result.stderr
    assert json.loads(result.stdout) == {"result": 17}
    root = tmp_path / ".copilot-extensions" / "installer-diagnostics"
    attempt = next(root.iterdir())
    assert json.loads((attempt / "attempt.json").read_text())["attribution"] == "UNATTRIBUTED"
    assert json.loads((attempt / "completed.json").read_text())["exit"] == 17
    assert "useful native failure" in next(attempt.glob("*.log")).read_text(encoding="utf-8")


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_logging_failure_continues(tmp_path, host):
    (tmp_path / ".copilot-extensions").write_text("occupied")
    result = ps_run(tmp_path, host, """
Initialize-InstallerDiagnostics 'agent-worktrees'
[Console]::Out.Write($env:COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING)
exit 23
""")
    assert result.returncode == 23
    assert result.stdout == "1"
    assert "missing diagnostics" in result.stderr


@pytest.mark.parametrize("host", POWERSHELLS)
@pytest.mark.parametrize("discard_streams", [False, True])
def test_real_entry_admission_refusal_retains_error(tmp_path, host, discard_streams):
    environment = native_environment(host=host, USERPROFILE=str(tmp_path))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", None)
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", None)
    destinations = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if discard_streams else {}
    result = subprocess.run(
        [shutil.which(host), "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / "install.ps1"),
         "install", "-BootstrapPython", str(tmp_path / "missing-python.exe")],
        env=environment, stdin=subprocess.DEVNULL, **destinations,
        timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    logs = list((tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.log"))
    assert any("existing absolute executable" in log.read_text(encoding="utf-8") for log in logs)


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_redaction_and_unicode(tmp_path, host):
    secret = "synthetic-private-credential"
    result = ps_run(tmp_path, host, """
Initialize-InstallerDiagnostics 'agent-worktrees'
$scheme = 'bea' + 'rer'
Write-InstallerDiagnostic ($scheme + "`nsynthetic-private-credential`nuseful error: café")
Complete-InstallerDiagnostics 3
""")
    assert result.returncode == 0, result.stderr
    text = "".join(path.read_text(encoding="utf-8") for path in
                   (tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.log"))
    assert secret not in text
    assert "café" in text


def test_review_header_continuations_python_file_bytes(diagnostic_context):
    diagnostics, directory = diagnostic_context
    records, credential = constructed_review_records()
    raw = directory / "constructed.raw"
    raw.write_bytes("".join(records).encode("utf-8"))
    if os.name != "nt":
        raw.chmod(0o600)
    assert raw.read_bytes().count(credential.encode()) == len(records)
    archive = io.StringIO()
    diagnostics.archive_snapshot(raw, archive, diagnostics._redactor())
    result = archive.getvalue().encode("utf-8")
    assert credential.encode() not in result
    assert result.count("useful review error café".encode()) == len(records)


@pytest.mark.parametrize("host", POWERSHELLS)
def test_review_header_continuations_powershell_file_bytes(tmp_path, host):
    records, credential = constructed_review_records()
    payload = tmp_path / "constructed.json"
    payload.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    assert payload.read_bytes().count(credential.encode()) == len(records)
    escaped = str(payload).replace("'", "''")
    result = ps_run(tmp_path, host, f"""
Initialize-InstallerDiagnostics 'agent-worktrees'
$records = [IO.File]::ReadAllText('{escaped}') | ConvertFrom-Json
foreach ($record in $records) {{ Write-InstallerDiagnostic $record }}
Complete-InstallerDiagnostics 0
""")
    assert result.returncode == 0, result.stderr
    archive = b"".join(path.read_bytes() for path in
                       (tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.log"))
    assert credential.encode() not in archive
    assert archive.count("useful review error café".encode()) == len(records)


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_staged_capture_and_writer_correlation(tmp_path, host):
    child = tmp_path / "child.ps1"
    phases = tmp_path / "child-phases"
    helper = str(SCRIPTS / "installer-diagnostics.ps1").replace("'", "''")
    phase_path = str(phases).replace("'", "''")
    child.write_text(f"""
$ErrorActionPreference = 'Stop'
$phaseClock = [Diagnostics.Stopwatch]::StartNew()
function Record-Phase($Name) {{
    [IO.File]::AppendAllText('{phase_path}', ($Name + '|' + $phaseClock.ElapsedMilliseconds + "`n"))
}}
Record-Phase 'entered'
. '{helper}'
Record-Phase 'helper-loaded'
function Join-Path {{ throw 'logging must not resolve a provider path' }}
function ConvertFrom-Json {{ throw 'logging must qualify its native JSON parser' }}
function ConvertTo-Json {{ throw 'logging must qualify its native JSON serializer' }}
$script:NativeDirectory = ${{function:New-InstallerDiagnosticsDirectory}}
function New-InstallerDiagnosticsDirectory($Path) {{
    Record-Phase 'private-enter'
    & $script:NativeDirectory $Path
    Record-Phase 'private-ready'
}}
$script:NativeRecord = ${{function:Write-InstallerDiagnostic}}
function Write-InstallerDiagnostic($Message) {{
    Record-Phase 'log-enter'
    & $script:NativeRecord $Message
    Record-Phase 'log-ready'
}}
$script:NativeRetention = ${{function:Remove-CompletedInstallerDiagnostics}}
function Remove-CompletedInstallerDiagnostics($Root) {{
    Record-Phase 'retention-enter'
    & $script:NativeRetention $Root
    Record-Phase 'retention-ready'
}}
Initialize-InstallerDiagnostics 'agent-worktrees'
Record-Phase 'diagnostics-ready'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::Out.Write(('o' * 200000))
Record-Phase 'stdout-written'
[Console]::Error.Write(('e' * 200000))
$scheme = 'bea' + 'rer'
[Console]::Error.Write("`n" + $scheme + "`nsynthetic-private-credential`nuseful child error café")
Record-Phase 'stderr-written'
Complete-InstallerDiagnostics 29
Record-Phase 'completed'
exit 29
""", encoding="utf-8-sig")
    escaped = str(child).replace("'", "''")
    executable = shutil.which(host).replace("'", "''")
    result = ps_run(tmp_path, host, f"""
Initialize-InstallerDiagnostics 'agent-worktrees'
$capture = New-InstallerDiagnosticCapture -Piped
$capture.Measure = @{{ available_ticks = [long]0; read_ticks = [long]0; store_ticks = [long]0; present_ticks = [long]0; polls = 0; bytes = 0 }}
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = '{executable}'
$start.Arguments = '-NoProfile -NonInteractive -File "' + '{escaped}' + '"'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$process = [Diagnostics.Process]::Start($start)
$process.StandardInput.Close()
$null = $process.Handle
$waitClock = [Diagnostics.Stopwatch]::StartNew()
$settled = Wait-InstallerDiagnosticChild $process $capture 10
$evidence = @{{
    settled = $settled; wait_ms = $waitClock.ElapsedMilliseconds
    child_pid = $process.Id; child_started = $process.StartTime.ToUniversalTime().ToString('o')
    child_exited = $process.HasExited
    stdout_bytes = ([IO.FileInfo]::new($capture.Stdout)).Length
    stderr_bytes = ([IO.FileInfo]::new($capture.Stderr)).Length
    stdout_stream = $script:InstallerDiagnosticReadStreams[0].GetType().FullName
    stdout_async = $script:InstallerDiagnosticReadStreams[0].IsAsync
    stdout_fallback = [object]::ReferenceEquals($script:InstallerDiagnosticReadStreams[0], $process.StandardOutput.BaseStream)
    measure = $capture.Measure
    timer_frequency = [Diagnostics.Stopwatch]::Frequency
    phases = $(if ([IO.File]::Exists('{phase_path}')) {{ [IO.File]::ReadAllText('{phase_path}') }} else {{ 'not-entered' }})
}}
if ($process.HasExited) {{ $evidence.native_exit = $process.ExitCode }}
$evidenceText = $evidence | ConvertTo-Json -Compress
[IO.File]::WriteAllText('{str(tmp_path / "staged-evidence.json").replace(chr(39), chr(39) * 2)}', $evidenceText)
if (-not $settled) {{ throw ('synthetic child did not exit: ' + $evidenceText) }}
$rc = $process.ExitCode
Save-InstallerDiagnosticCapture $capture
Complete-InstallerDiagnostics $rc
[Console]::Out.Write('{{"child":29}}')
exit $rc
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "1"})
    evidence = json.loads((tmp_path / "staged-evidence.json").read_text(encoding="utf-8"))
    print("staged-capture-evidence " + json.dumps(evidence, ensure_ascii=True))
    assert result.returncode == 29, result.stderr
    assert result.stdout == "o" * 200000 + '{"child":29}'
    assert result.stderr.endswith("useful child error café")
    root = tmp_path / ".copilot-extensions" / "installer-diagnostics"
    attempts = list(root.iterdir())
    assert len(attempts) == 1
    assert len(list(attempts[0].glob("*.start.json"))) == 2
    assert len(list(attempts[0].glob("*.completed.json"))) == 2
    archive = "\n".join(path.read_text(encoding="utf-8") for path in attempts[0].glob("*.log"))
    assert "synthetic-private-credential" not in archive
    assert "useful child error café" in archive


@pytest.mark.parametrize("host", POWERSHELLS)
def test_native_pipe_drain_does_not_wait_for_descendant(tmp_path, host):
    child = tmp_path / "pipe-child.py"
    done = tmp_path / "descendant-finished"
    child.write_text("""
import os, subprocess, sys
subprocess.Popen(
    [sys.executable, "-c", "import pathlib,sys,time; time.sleep(5); pathlib.Path(sys.argv[1]).touch()", sys.argv[1]],
    stdout=sys.stdout, stderr=sys.stderr, creationflags=subprocess.CREATE_NO_WINDOW,
)
os.write(2, b"useful retained-descriptor error\\n")
os._exit(29)
""", encoding="utf-8")
    python = sys.executable.replace("'", "''")
    escaped = str(child).replace("'", "''")
    finished = str(done).replace("'", "''")
    try:
        result = ps_run(tmp_path, host, f"""
Initialize-InstallerDiagnostics 'agent-worktrees'
$capture = New-InstallerDiagnosticCapture -Piped
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = '{python}'
$start.Arguments = '"{escaped}" "{finished}"'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$process = [Diagnostics.Process]::Start($start)
$null = $process.Handle
if (-not (Wait-InstallerDiagnosticChild $process $capture 10)) {{ throw 'synthetic child did not exit' }}
$process.WaitForExit()
Save-InstallerDiagnosticCapture $capture
[Console]::Out.Write(([IO.File]::Exists('{finished}')).ToString().ToLowerInvariant())
exit $process.ExitCode
""")
        assert result.returncode == 29, result.stderr
        assert result.stdout == "false", "capture waited for the retained descendant's EOF"
        archive = "\n".join(path.read_text(encoding="utf-8") for path in
                            (tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.log"))
        assert "useful retained-descriptor error" in archive
    finally:
        deadline = time.monotonic() + 8
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert done.exists(), "owned finite descendant did not settle"


@pytest.mark.parametrize("host", POWERSHELLS)
@pytest.mark.parametrize("mode", ["capture", "archive-failure", "raw-failure", "unavailable"])
def test_review_presentation_is_live_exact_and_independent_of_storage(tmp_path, host, mode):
    child = tmp_path / "presentation-child.py"
    release = tmp_path / "release-child"
    finished = tmp_path / "child-finished"
    trace = tmp_path / "presentation-phase"
    child.write_text("""
import os, pathlib, sys, time
progress = "progress café\\n".encode("utf-8")
cut = progress.index(bytes([0xc3])) + 1
os.write(1, progress[:cut])
time.sleep(.05)
os.write(1, progress[cut:])
os.write(2, b"e" * 100000 + "failure café\\n".encode("utf-8"))
for _ in range(200):
    if pathlib.Path(sys.argv[1]).exists():
        break
    time.sleep(.05)
else:
    raise RuntimeError("progress acknowledgement absent")
pathlib.Path(sys.argv[2]).touch()
os.write(1, "finished café\\n".encode("utf-8"))
os._exit(37)
""", encoding="utf-8")
    if mode == "unavailable":
        (tmp_path / ".copilot-extensions").write_text("occupied", encoding="utf-8")
    fixture = tmp_path / "presentation.ps1"
    quoted = [str(path).replace("'", "''") for path in
              (SCRIPTS / "installer-diagnostics.ps1", Path(sys.executable), child, release, finished)]
    helper, python, script, ack, done = quoted
    phase = str(trace).replace("'", "''")
    initializer = str(SCRIPTS / "installer-human-output.ps1").replace("'", "''")
    fixture.write_text(f"""
$ErrorActionPreference = 'Stop'
. '{initializer}'
. '{helper}'
[IO.File]::WriteAllText('{phase}', 'loaded')
Initialize-InstallerDiagnostics 'agent-worktrees'
[IO.File]::WriteAllText('{phase}', 'before capture')
$capture = New-InstallerDiagnosticCapture -Piped
[IO.File]::WriteAllText('{phase}', 'capture ready')
if ('{mode}' -eq 'archive-failure') {{ $capture.Log = $script:InstallerDiagnostic.Directory }}
if ('{mode}' -eq 'raw-failure') {{ $capture.Stdout = $script:InstallerDiagnostic.Directory }}
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = '{python}'
$start.Arguments = '-I "{script}" "{ack}" "{done}"'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$process = [Diagnostics.Process]::Start($start)
[IO.File]::WriteAllText('{phase}', 'child started')
$null = $process.Handle
if (-not (Wait-InstallerDiagnosticChild $process $capture 10)) {{ throw 'synthetic child did not exit' }}
[IO.File]::WriteAllText('{phase}', 'child returned')
$process.WaitForExit()
$null = Receive-InstallerDiagnosticCapture $process $capture -Final
$rc = $process.ExitCode
Save-InstallerDiagnosticCapture $capture
[IO.File]::WriteAllText('{phase}', 'saved')
Complete-InstallerDiagnostics $rc
[IO.File]::WriteAllText('{phase}', 'completed')
exit $rc
""", encoding="utf-8-sig")
    environment = native_environment(host=host, USERPROFILE=str(tmp_path), COPILOT_EXTENSIONS_HUMAN_UTF8="1")
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", None)
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER", None)
    stdout, stderr = tmp_path / "stdout", tmp_path / "stderr"
    started = time.monotonic()
    capture = NativeCapture(
        [shutil.which(host), "-NoProfile", "-NonInteractive", "-File", str(fixture)],
        environment, stdout, stderr,
    )
    process = capture.process
    try:
        try:
            host_deadline = started + 20
            while process.poll() is None and time.monotonic() < host_deadline:
                capture.drain()
                if trace.exists() and trace.read_text() in ("capture ready", "child started", "child returned", "saved"):
                    break
                time.sleep(.02)
            # Keep both existing bounds: 20s total host time, 10s child progress.
            # The child cannot exit until this observer ACKs presentation.
            deadline = min(host_deadline, time.monotonic() + 10)
            progress = "progress café\n".encode("utf-8")
            while progress not in stdout.read_bytes() and time.monotonic() < deadline and process.poll() is None:
                capture.drain()
                time.sleep(.02)
            assert progress in stdout.read_bytes(), (
                trace.read_text() if trace.exists() else "not entered",
                stdout.stat().st_size, stderr.stat().st_size,
            )
            assert not finished.exists(), "progress was forwarded only after child exit"
        finally:
            release.touch()
            try:
                capture.wait(max(0, 20 - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        assert process.returncode == 37, (
            trace.read_text() if trace.exists() else "not entered",
            stdout.stat().st_size, stderr.stat().st_size,
        )
    finally:
        capture.close()
    assert stdout.read_bytes() == "progress café\nfinished café\n".encode("utf-8")
    warning = b"WARNING: installer diagnostics unavailable; continuing with missing diagnostics."
    presented = stderr.read_bytes().replace(warning + b"\r\n", b"").replace(warning + b"\n", b"")
    assert presented == b"e" * 100000 + "failure café\n".encode("utf-8")


@pytest.mark.parametrize("host", POWERSHELLS)
def test_completed_retention_never_prunes_incomplete(tmp_path, host):
    result = ps_run(tmp_path, host, """
Initialize-InstallerDiagnostics 'agent-worktrees'
$root = Split-Path $env:COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY
foreach ($name in @(('b' * 32), ('c' * 32), ('d' * 32))) {
    $directory = Join-Path $root $name
    New-InstallerDiagnosticsDirectory $directory
    [IO.File]::WriteAllText((Join-Path $directory 'attempt.json'),
        ('{"schema":"copilot-extensions.installer-diagnostics","attempt":"' + $name + '"}'))
    [IO.File]::WriteAllText((Join-Path $directory 'completed.json'),
        '{"completed":"2000-01-01T00:00:00Z"}')
}
$incomplete = Join-Path $root ('c' * 32)
[IO.File]::WriteAllText((Join-Path $incomplete 'writer.start.json'), '{}')
$pending = Join-Path $root ('d' * 32)
[IO.File]::WriteAllText((Join-Path $pending 'capture.pending'), 'unsettled')
Remove-CompletedInstallerDiagnostics $root
""")
    assert result.returncode == 0, result.stderr
    root = tmp_path / ".copilot-extensions" / "installer-diagnostics"
    assert not (root / ("b" * 32)).exists()
    assert (root / ("c" * 32)).exists()
    assert (root / ("d" * 32)).exists()
    assert len(list(root.iterdir())) == 3


def test_capture_regular_files_both_streams_and_native_exit(diagnostic_context):
    diagnostics, directory = diagnostic_context
    with diagnostics.capture() as destinations:
        result = subprocess.run(
            [sys.executable, "-c",
             "import os; os.write(1,b'o'*200000); os.write(2,b'e'*200000); raise SystemExit(19)"],
            **destinations, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    assert result.returncode == 19
    assert len(list(directory.glob("*.raw"))) == 2
    assert list(directory.glob("*.pending"))
    assert list(directory.glob("*.log"))


def test_capture_does_not_wait_for_retained_descriptor(diagnostic_context):
    diagnostics, directory = diagnostic_context
    descendant = None
    try:
        started = time.monotonic()
        with diagnostics.capture() as destinations:
            descendant = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                **destinations, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            destinations["stderr"].write(b"useful failure\n")
        assert time.monotonic() - started < 5
        assert descendant.poll() is None
        assert "useful failure" in next(directory.glob("*.log")).read_text()
    finally:
        if descendant is not None:
            descendant.terminate()
            descendant.wait(timeout=5)


def test_snapshot_redacts_split_credentials_unicode_json(diagnostic_context):
    diagnostics, directory = diagnostic_context
    text = "bea" + "rer\nsynthetic-private-credential\ncafé\n"
    with diagnostics.capture() as streams:
        streams["stdout"].write(b'{"result":false}\n')
        streams["stderr"].write(text.encode("utf-8"))
    archive = next(directory.glob("*.log")).read_text(encoding="utf-8")
    assert "synthetic-private-credential" not in archive
    assert "café" in archive
    assert '{"result":false}' in archive


def test_storage_failure_is_advisory(monkeypatch, capsys):
    diagnostics = load_diagnostics()
    monkeypatch.delenv("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", raising=False)
    with diagnostics.capture() as destinations:
        assert destinations == {}
    assert diagnostics.reference() == {"missing_diagnostics": True}
    assert "missing diagnostics" in capsys.readouterr().err


def test_capture_unavailable_keeps_discoverable_missing_status(diagnostic_context, monkeypatch):
    diagnostics, directory = diagnostic_context
    monkeypatch.setattr(diagnostics, "_redactor", lambda: (_ for _ in ()).throw(OSError("unavailable")))
    with diagnostics.capture() as destinations:
        assert destinations == {}
    assert json.loads((directory / "missing-diagnostics.json").read_text()) == {"missing_diagnostics": True}


@pytest.mark.parametrize("native_failure", [False, True])
def test_capture_close_failure_preserves_native_outcome(
    diagnostic_context, monkeypatch, native_failure,
):
    diagnostics, directory = diagnostic_context
    fdopen = diagnostics.os.fdopen

    class FailedClose:
        def __init__(self, stream):
            self.stream = stream

        def close(self):
            self.stream.close()
            raise OSError("capture close unavailable")

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def open_stream(fd, mode="r", *args, **kwargs):
        stream = fdopen(fd, mode, *args, **kwargs)
        return FailedClose(stream) if mode == "wb" else stream

    monkeypatch.setattr(diagnostics.os, "fdopen", open_stream)
    primary = RuntimeError("native failure")

    def run():
        with diagnostics.capture() as destinations:
            destinations["stdout"].write(b"native output\n")
            if native_failure:
                raise primary
            return 23

    if native_failure:
        with pytest.raises(RuntimeError) as caught:
            run()
        assert caught.value is primary
    else:
        assert run() == 23
    assert (directory / "missing-diagnostics.json").exists()
    assert "native output" in next(directory.glob("*.log")).read_text()


def test_link_refusal(diagnostic_context, tmp_path):
    diagnostics, directory = diagnostic_context
    target = tmp_path / "foreign"
    target.mkdir()
    link = directory / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
    with pytest.raises(ValueError, match="links"):
        diagnostics.ordinary(link / "capture")
    assert not list(target.iterdir())


def test_retained_writer_identity_and_outcome(diagnostic_context):
    diagnostics, directory = diagnostic_context
    with pytest.raises(SystemExit) as exit_info:
        with diagnostics.writer():
            raise SystemExit(31)
    assert exit_info.value.code == 31
    record = json.loads(next(directory.glob("*.start.json")).read_text())
    assert record["kind"] == "retained-installer"
    assert json.loads(next(directory.glob("*.completed.json")).read_text())["exit"] == 31


def test_witness_text_not_archived(diagnostic_context):
    diagnostics, directory = diagnostic_context
    with diagnostics.capture() as streams:
        streams["stderr"].write(b"UPDATE_DRIVER_WITNESS=synthetic-private-value\nuseful failure\n")
    text = next(directory.glob("*.log")).read_text()
    assert "synthetic-private-value" not in text
    assert "sensitive diagnostic record omitted" in text
    assert "useful failure" in text


def test_timeout_keeps_primary_exception_and_written_diagnostics(diagnostic_context):
    diagnostics, directory = diagnostic_context
    with pytest.raises(subprocess.TimeoutExpired):
        with diagnostics.capture() as streams:
            subprocess.run(
                [sys.executable, "-c",
                 "import os,time; os.write(2,b'primary timeout failure\\n'); time.sleep(20)"],
                **streams, timeout=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    assert "primary timeout failure" in next(directory.glob("*.log")).read_text()
    assert list(directory.glob("*.pending"))


def test_abrupt_exit_keeps_private_unprocessed_capture(diagnostic_context):
    _, directory = diagnostic_context
    program = """
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("abrupt_diagnostic_writer", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module.capture() as streams:
    streams["stderr"].write(b"abrupt child evidence\\n")
    os._exit(37)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(SCRIPTS / "installer_diagnostics.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 37
    assert list(directory.glob("*.pending"))
    assert any(b"abrupt child evidence" in path.read_bytes() for path in directory.glob("*.raw"))
    assert not list(directory.glob("*.log"))
    assert not (directory / "completed.json").exists()


def test_snapshot_bounds_oversized_records(diagnostic_context):
    diagnostics, directory = diagnostic_context
    path = directory / "large.raw"
    path.write_bytes(b"x" * 1000000 + b"\nuseful error\n")
    if os.name != "nt":
        path.chmod(0o600)
    archive = io.StringIO()
    diagnostics.archive_snapshot(path, archive, diagnostics._redactor())
    assert archive.getvalue() == "[oversized diagnostic record omitted]\nuseful error\n"


def native_bash():
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe" if git else None
    return str(candidate) if candidate and candidate.is_file() else None


@pytest.mark.skipif(native_bash() is None, reason="native Bash/Git Bash unavailable; WSL is not inferred")
def test_bash_native_cold_redaction_and_exit(tmp_path):
    environment = native_environment(host="powershell.exe" if os.name == "nt" else None, HOME=tmp_path.as_posix())
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", None)
    result = subprocess.run(
        [native_bash(), "-c", """
set -euo pipefail
source "$1"
installer_diagnostics_init agent-worktrees
scheme=bea
scheme="${scheme}rer"
installer_diagnostics_write "$scheme
synthetic-private-credential
useful native error"
installer_diagnostics_complete 27
printf '{"native":27}'
exit 27
""", "bash", (SCRIPTS / "installer-diagnostics.sh").as_posix()],
        env=environment, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 27, result.stderr
    assert json.loads(result.stdout) == {"native": 27}
    logs = list((tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.log"))
    assert logs
    assert "synthetic-private-credential" not in logs[0].read_text()
    assert "useful native error" in logs[0].read_text()


@pytest.mark.skipif(native_bash() is None, reason="native Bash/Git Bash unavailable")
def test_review_header_continuations_bash_file_bytes(tmp_path):
    records, credential = constructed_review_records()
    payload = tmp_path / "constructed.records"
    payload.write_bytes("".join(records).encode("utf-8"))
    assert payload.read_bytes().count(credential.encode()) == len(records)
    environment = native_environment(host="powershell.exe" if os.name == "nt" else None, HOME=tmp_path.as_posix())
    environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT", None)
    result = subprocess.run(
        [native_bash(), "-c", """
set -euo pipefail
source "$1"
installer_diagnostics_init agent-worktrees
record="$(cat "$2")"
installer_diagnostics_write "$record"
installer_diagnostics_complete 0
""", "bash", (SCRIPTS / "installer-diagnostics.sh").as_posix(), payload.as_posix()],
        env=environment, stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    archive = b"".join(path.read_bytes() for path in
                       (tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.log"))
    assert credential.encode() not in archive
    assert archive.count("useful review error café".encode()) == len(records)
