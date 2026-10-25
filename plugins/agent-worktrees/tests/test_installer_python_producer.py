"""Real isolated Python producers; no construction, service or network calls."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from update_telemetry.human import _available

from test_installer_diagnostics import POWERSHELLS, SCRIPTS, load_diagnostics, native_environment, ps_run
from test_installer_diagnostics import diagnostic_context as _diagnostic_context

diagnostic_context = _diagnostic_context


def invoke(tmp_path, program=None, *, entry=None, absent=False, inherited=False):
    environment = native_environment(USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    if not inherited:
        for name in ("ATTEMPT", "WRITER", "DIRECTORY", "MISSING"):
            environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_" + name, None)
    if entry is not None and not absent:
        argv = [sys.executable, "-I", "-B", "-X", "utf8", str(entry), "--unknown-diagnostic-fixture-option"]
    else:
        prefix = "import sys; sys.path.insert(0, sys.argv[1]); import installer_diagnostics as diagnostics\n"
        if absent:
            prefix += "sys.stdout = None; sys.stderr = None\n"
        if entry is not None:
            prefix += ("import runpy; target = sys.argv[2]; "
                       "sys.argv = [target, '--unknown-diagnostic-fixture-option']; "
                       "runpy.run_path(target, run_name='__main__')")
        else:
            prefix += program
        argv = [sys.executable, "-I", "-B", "-X", "utf8", "-c", prefix, str(SCRIPTS)]
        if entry is not None:
            argv.append(str(entry))
    return subprocess.run(
        argv, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def evidence(tmp_path):
    root = tmp_path / ".copilot-extensions" / "installer-diagnostics"
    attempts = list(root.iterdir())
    assert len(attempts) == 1
    directory = attempts[0]
    receipt = json.loads((directory / "attempt.json").read_text(encoding="utf-8"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in directory.glob("*.log"))
    completed = [json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.completed.json")]
    return directory, receipt, text, completed


@pytest.mark.parametrize("absent", [False, True])
def test_real_entry_early_parser_failure_is_durable(tmp_path, absent):
    result = invoke(tmp_path, entry=SCRIPTS / "runtime_install.py", absent=absent)
    assert result.returncode == 2
    directory, receipt, text, completed = evidence(tmp_path)
    assert receipt["attribution"] == "UNATTRIBUTED"
    assert receipt["plugin"] == "agent-worktrees"
    assert "error:" in text and "unrecognized arguments" in text
    assert "--unknown-diagnostic-fixture-option" not in text
    assert completed[0]["exit"] == 2
    diagnostics = load_diagnostics()
    diagnostics.files().private_directory(directory)
    for path in directory.iterdir():
        fd = diagnostics.files().open_file(path)
        os.close(fd)


@pytest.mark.parametrize("damage", ["missing", "syntax", "import", "runtime"])
@pytest.mark.parametrize("entrypoint", [True, False])
@pytest.mark.parametrize("option,code", [("--help", 0), ("--unknown-diagnostic-fixture-option", 2)])
def test_unavailable_helper_does_not_replace_parser_result(tmp_path, damage, entrypoint, option, code):
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, scripts)
    helper = scripts / "installer_diagnostics.py"
    if damage == "missing":
        helper.unlink()
    else:
        helper.write_text({
            "syntax": "def invalid(\n",
            "import": "import unavailable_diagnostics_fixture_dependency\n",
            "runtime": "raise RuntimeError('optional diagnostics import failed')\n",
        }[damage], encoding="utf-8")
    entry = scripts / "runtime_install.py"
    program = (
        "import runpy,sys; sys.path.insert(0,sys.argv[1]); "
        "module=runpy.run_path(sys.argv[2]); "
        "sys.argv=[sys.argv[2],sys.argv[3]]; "
        "raise SystemExit(module['main']())"
    )
    argv = ([str(entry), option] if entrypoint else
            ["-c", program, str(scripts), str(entry), option])
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", *argv],
        env=native_environment(USERPROFILE=str(tmp_path), HOME=str(tmp_path)),
        stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == code, result.stderr
    if code:
        assert b"unrecognized arguments" in result.stderr
        assert result.stdout == b""
    else:
        assert b"usage:" in result.stdout
    assert result.stderr.count(b"continuing with missing diagnostics") == 1
    assert b"Traceback" not in result.stderr


def test_missing_helper_preserves_implementation_import_failure(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(SCRIPTS / "runtime_install.py", scripts / "runtime_install.py")
    (scripts / "installer_engine.py").write_text(
        "raise RuntimeError('native implementation import failure')\n", encoding="utf-8",
    )
    result = invoke(tmp_path, entry=scripts / "runtime_install.py")
    assert result.returncode == 1
    assert b"native implementation import failure" in result.stderr
    assert result.stderr.count(b"continuing with missing diagnostics") == 1
    assert b"No module named 'installer_diagnostics'" not in result.stderr


def test_missing_helper_warning_cannot_hold_entrypoint(tmp_path):
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, scripts)
    (scripts / "installer_diagnostics.py").unlink()
    program = """
import runpy,sys,threading
class Blocked:
    def fileno(self):
        threading.Event().wait(60)
        raise OSError('fixture released')
    def write(self,text):
        raise AssertionError('optional warning used blocking text presentation')
    def flush(self):
        pass
sys.stderr=Blocked()
sys.argv=[sys.argv[1],'--help']
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", program, str(scripts / "runtime_install.py")],
        env=native_environment(USERPROFILE=str(tmp_path), HOME=str(tmp_path)),
        stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert b"usage:" in result.stdout


@pytest.mark.parametrize("missing", [False, True])
def test_available_helper_notices_cannot_hold_entry_on_unread_stderr(tmp_path, missing):
    release = tmp_path / "release"
    program = """
import json,os,sys,threading,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import installer_diagnostics as diagnostics
filler=os.dup(2)
def fill():
    try:
        os.write(filler,b'x'*1048576)
    finally:
        os.close(filler)
threading.Thread(target=fill,daemon=True).start()
release=Path(sys.argv[2])
deadline=time.monotonic()+10
while not release.exists():
    if time.monotonic()>deadline:
        raise SystemExit(91)
    time.sleep(.01)
with diagnostics.writer(announce=True):
    diagnostics.phase('producer-entered')
    if sys.argv[3]=='True':
        diagnostics.warn()
    print(json.dumps(diagnostics.reference()),flush=True)
    raise SystemExit(23)
"""
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", program, str(SCRIPTS), str(release), str(missing)],
        env=native_environment(USERPROFILE=str(tmp_path), HOME=str(tmp_path)),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 10
        while not _available(process.stderr.fileno()) and process.poll() is None:
            assert time.monotonic() < deadline, "fixture did not fill stderr"
            time.sleep(.01)
        assert process.poll() is None
        release.touch()
        process.wait(timeout=10)  # Keep the parent presentation pipe full and unread.
        assert process.returncode == 23
        reference = json.loads(process.stdout.read())
        assert reference["missing_diagnostics"] is missing
        directory, _, text, completed = evidence(tmp_path)
        assert reference["directory"] == str(directory)
        assert "Installer phase: producer-entered" in text
        assert completed[0]["exit"] == 23
        assert (directory / "missing-diagnostics.json").exists() is missing
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def test_uncaught_python_exception_preserves_class_and_useful_text(tmp_path):
    result = invoke(tmp_path, """
with diagnostics.writer():
    print("building isolated fixture", flush=True)
    raise ValueError("useful root failure café")
""", absent=True)
    assert result.returncode == 1
    _, _, text, completed = evidence(tmp_path)
    assert "building isolated fixture" in text
    assert "useful root failure café" in text
    assert completed[0]["error_class"] == "ValueError"
    assert completed[0]["exit"] == 1


@pytest.mark.parametrize("absent", [False, True])
def test_phase_and_exact_reference_survive_protocol_and_abrupt_exit(tmp_path, absent):
    result = invoke(tmp_path, """
import os
with diagnostics.writer(announce=True):
    sys.stdout.write('{"protocol":37}')
    sys.stdout.flush()
    diagnostics.phase("retain-uv-inputs")
    os._exit(37)
""", absent=absent)
    assert result.returncode == 37
    directory, _, text, completed = evidence(tmp_path)
    assert "Installer phase: retain-uv-inputs" in text
    assert completed == [] and not (directory / "completed.json").exists()
    if not absent:
        assert result.stdout == b'{"protocol":37}'
        assert ("Installer diagnostics: " + str(directory)).encode("utf-8") in result.stderr
        assert b"Installer phase: retain-uv-inputs" in result.stderr


def test_native_child_error_does_not_dump_command_or_authority(tmp_path):
    result = invoke(tmp_path, """
import subprocess
with diagnostics.writer():
    subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c", "import sys; print('useful native error café', file=sys.stderr); sys.exit(23)",
         "--governance-generation", "synthetic-command-authority"],
        capture_output=True, check=True, timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
""")
    assert result.returncode == 1
    _, _, text, completed = evidence(tmp_path)
    assert "useful native error café" in text
    assert "synthetic-command-authority" not in text
    assert "--governance-generation" not in text
    assert completed[0]["error_class"] == "CalledProcessError"
    assert completed[0]["native_exit"] == 23


def test_binary_split_credentials_unicode_large_records_and_protocol_stdout(tmp_path):
    result = invoke(tmp_path, """
import json
with diagnostics.writer() as producer:
    assert sys.stdout.fileno() == sys.stdout._installer_original.fileno()
    import os
    assert not os.get_inheritable(producer.output.fileno())
    assert sys.stdout.isatty() == sys.stdout._installer_original.isatty()
    assert sys.stdout.encoding == sys.stdout._installer_original.encoding
    assert sys.stdout.errors == sys.stdout._installer_original.errors
    print(json.dumps({"result": "café"}, ensure_ascii=False), end="", flush=True)
    scheme = "bea" + "rer"
    credential = "synthetic-producer-credential"
    message = "Authorization: " + scheme + "\\n" + credential + "\\nuseful café\\n"
    data = message.encode("utf-8")
    for value in data:
        sys.stderr.buffer.write(bytes([value]))
    sys.stderr.buffer.flush()
    sys.stderr.writelines(["text-writelines café", "\\n"])
    sys.stderr.buffer.writelines(["binary-writelines café\\n".encode("utf-8")])
    sys.stderr.write("x" * 2000000 + "\\nafter-large-record\\n")
    raise SystemExit(17)
""")
    assert result.returncode == 17
    assert json.loads(result.stdout.decode("utf-8")) == {"result": "café"}
    _, _, text, completed = evidence(tmp_path)
    assert "synthetic-producer-credential" not in text
    assert "useful café" in text
    assert "oversized diagnostic record omitted" in text
    assert "after-large-record" in text
    assert "text-writelines café" in text
    assert "binary-writelines café" in text
    assert completed[0]["exit"] == 17


def test_binary_evidence_survives_absent_python_streams(tmp_path):
    result = invoke(tmp_path, """
with diagnostics.writer():
    sys.stderr.buffer.write("useful absent-stream error café\\n".encode("utf-8"))
    sys.stderr.buffer.flush()
""", absent=True)
    assert result.returncode == 0
    _, _, text, completed = evidence(tmp_path)
    assert "useful absent-stream error café" in text
    assert completed[0]["exit"] == 0


def _overlong_header(prefix, newline):
    scheme = "bea" + "rer"
    credential = "synthetic-large-header-value"
    message = prefix + " " + scheme + (" " * 73728) + newline
    message += credential + newline + "useful error after long header" + newline
    assert credential in message
    return message, credential


@pytest.mark.parametrize("prefix", ["Authorization:", "http.extraheader=Authorization:"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_overlong_header_continuation_remains_private(tmp_path, prefix, newline):
    _, credential = _overlong_header(prefix, newline)
    result = invoke(tmp_path, f"""
scheme = "bea" + "rer"
credential = {credential!r}
message = {prefix!r} + " " + scheme + (" " * 73728) + {newline!r}
message += credential + {newline!r} + "useful error after long header" + {newline!r}
assert credential in message
with diagnostics.writer():
    sys.stderr.write(message)
    sys.stderr.flush()
""")
    assert result.returncode == 0
    assert credential.encode() in result.stderr
    _, _, text, _ = evidence(tmp_path)
    assert credential not in text
    assert "oversized diagnostic record omitted" in text
    assert "useful error after long header" in text


@pytest.mark.parametrize("prefix", ["Authorization:", "http.extraheader=Authorization:"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_overlong_header_snapshot_remains_private(diagnostic_context, prefix, newline):
    import io

    diagnostics, directory = diagnostic_context
    message, credential = _overlong_header(prefix, newline)
    path = directory / "overlong-header.raw"
    with os.fdopen(diagnostics.files().open_file(path, create=True), "wb") as stream:
        stream.write(message.encode("utf-8"))
    assert credential.encode() in path.read_bytes()
    archive = io.StringIO()
    diagnostics.archive_snapshot(path, archive, diagnostics._redactor())
    text = archive.getvalue()
    assert credential not in text
    assert "oversized diagnostic record omitted" in text
    assert "useful error after long header" in text


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_reconfigured_binary_stream_keeps_archive_encoding(tmp_path, channel):
    result = invoke(tmp_path, f"""
getattr(sys, {channel!r}).reconfigure(encoding="latin-1")
with diagnostics.writer():
    stream = getattr(sys, {channel!r})
    stream.reconfigure(encoding="utf-8")
    stream.buffer.write("useful café after reconfigure\\n".encode("utf-8"))
    stream.buffer.flush()
""")
    assert result.returncode == 0
    observed = getattr(result, channel).decode("utf-8")
    assert observed == "useful café after reconfigure\n"
    _, _, text, _ = evidence(tmp_path)
    assert "useful café after reconfigure" in text


def test_abrupt_python_entry_retains_sanitized_progress_without_success(tmp_path):
    result = invoke(tmp_path, """
import os
with diagnostics.writer():
    print("useful progress before abrupt exit", flush=True)
    os._exit(37)
""", absent=True)
    assert result.returncode == 37
    directory, _, text, completed = evidence(tmp_path)
    assert "useful progress before abrupt exit" in text
    assert completed == []
    assert not (directory / "completed.json").exists()


def test_flushed_incomplete_record_survives_abrupt_exit_privately(tmp_path):
    result = invoke(tmp_path, """
import os
with diagnostics.writer():
    print("independently useful complete record", flush=True)
    sys.stderr.write("recoverable incomplete progress café")
    sys.stderr.flush()
    os._exit(37)
""", absent=True)
    assert result.returncode == 37
    directory, _, text, completed = evidence(tmp_path)
    assert "independently useful complete record" in text
    assert completed == []
    assert not (directory / "completed.json").exists()
    pending = list(directory.glob("*.producer.pending"))
    assert pending, "flushed logical state was lost with the terminated interpreter"
    snapshot = json.loads(pending[0].read_bytes().splitlines()[-1])
    assert snapshot["channels"]["stderr"]["pending"] == "recoverable incomplete progress café"
    diagnostics = load_diagnostics()
    environment = native_environment(USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    environment["COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT"] = directory.name
    recovered = diagnostics.recover_producer_checkpoint(environment, snapshot["writer"])
    recovered_text = Path(recovered["recovered_log"]).read_text(encoding="utf-8")
    assert "recoverable incomplete progress" in recovered_text
    assert recovered["quiescence"] == "not-asserted"
    assert recovered["raw_retained"] and pending[0].exists()
    assert not (directory / "completed.json").exists()


@pytest.mark.parametrize("kind", ["header", "extraheader", "oversized", "short-token", "url"])
def test_partial_flush_never_archives_interrupted_credentials(tmp_path, kind):
    result = invoke(tmp_path, """
import os
with diagnostics.writer():
    print("independently meaningful record", flush=True)
    scheme = "bea" + "rer"
    secret = "synthetic-interrupted-secret"
    kind = """ + repr(kind) + """
    if kind == "header":
        value = "Authorization: " + scheme + "\\n" + secret
    elif kind == "extraheader":
        value = "http.extraheader=Authorization:\\r\\n" + scheme + "\\r\\n" + secret
    elif kind == "oversized":
        value = "Authorization:" + " " * 70000 + scheme + "\\n" + secret
    elif kind == "short-token":
        value = "ghp_SHORTPART waiting for continuation"
    else:
        value = "access https://user:" + secret + " waiting for continuation"
    sys.stderr.write(value)
    sys.stderr.flush()
    os._exit(37)
""", absent=True)
    assert result.returncode == 37
    directory, _, text, completed = evidence(tmp_path)
    identifier = next(directory.glob("*.start.json")).name.removesuffix(".start.json")
    diagnostics = load_diagnostics()
    environment = native_environment(USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    environment["COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT"] = directory.name
    recovered = diagnostics.recover_producer_checkpoint(environment, identifier)
    text += Path(recovered["recovered_log"]).read_text(encoding="utf-8")
    assert "independently meaningful record" in text
    assert "synthetic-interrupted-secret" not in text
    assert "ghp_SHORTPART" not in text
    assert completed == []
    pending = directory / f"{identifier}.producer.pending"
    os.close(diagnostics.files().open_file(pending))
    assert recovered["raw_retained"] and recovered["quiescence"] == "not-asserted"


def test_binary_flush_retains_incomplete_encoding_state_and_exact_protocol(tmp_path):
    result = invoke(tmp_path, """
import os
with diagnostics.writer() as producer:
    sys.stdout.write('{"protocol":37}')
    sys.stdout.flush()
    sys.stderr.buffer.write(b"recoverable encoding prefix \\xc3")
    sys.stderr.buffer.flush()
    assert not os.get_inheritable(producer.checkpoints.fileno())
    os._exit(37)
""")
    assert result.returncode == 37 and result.stdout == b'{"protocol":37}'
    directory, _, text, completed = evidence(tmp_path)
    raw = next(directory.glob("*.producer.pending"))
    record = json.loads(raw.read_bytes().splitlines()[-1])
    assert record["binary"]["stderr"]["pending"] == "c3"
    diagnostics = load_diagnostics()
    environment = native_environment(USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    environment["COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT"] = directory.name
    recovered = diagnostics.recover_producer_checkpoint(environment, record["writer"])
    recovered_text = Path(recovered["recovered_log"]).read_text()
    assert "recoverable encoding" in text + recovered_text
    assert "encoded unit retained privately" in recovered_text
    assert completed == [] and raw.exists()


def test_later_torn_checkpoint_preserves_prior_flushed_snapshot(tmp_path):
    result = invoke(tmp_path, """
import os
with diagnostics.writer() as producer:
    print("earlier complete record", flush=True)
    sys.stderr.write("earlier recoverable checkpoint marker")
    sys.stderr.flush()
    producer.checkpoints.write(b'{"unfinished":')
    os.fsync(producer.checkpoints.fileno())
    os._exit(37)
""", absent=True)
    assert result.returncode == 37
    directory, _, text, _ = evidence(tmp_path)
    raw = next(directory.glob("*.producer.pending"))
    before = raw.read_bytes()
    identifier = raw.name.removesuffix(".producer.pending")
    diagnostics = load_diagnostics()
    environment = native_environment(USERPROFILE=str(tmp_path), HOME=str(tmp_path))
    environment["COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT"] = directory.name
    recovered = diagnostics.recover_producer_checkpoint(environment, identifier)
    assert recovered["truncated_tail"]
    assert "earlier recoverable checkpoint" in Path(recovered["recovered_log"]).read_text()
    assert "earlier complete record" in text
    assert raw.read_bytes() == before


def test_active_writer_recovery_does_not_claim_quiescence(diagnostic_context):
    diagnostics, directory = diagnostic_context
    with diagnostics.writer() as producer:
        sys.stderr.write("active recoverable progress token")
        sys.stderr.flush()
        assert not os.get_inheritable(producer.checkpoints.fileno())
        result = diagnostics.recover_producer_checkpoint(dict(os.environ), producer.identifier)
        assert result["quiescence"] == "not-asserted"
        assert result["raw_retained"]
        assert not (directory / "completed.json").exists()
    assert (directory / f"{producer.identifier}.producer.pending").exists()


def test_checkpoint_storage_failure_preserves_primary_result(diagnostic_context, monkeypatch):
    diagnostics, directory = diagnostic_context
    with diagnostics.writer() as producer:
        original = diagnostics.files().open_file

        def fail_checkpoint(path, create=False):
            if str(path).endswith(".producer.pending"):
                raise OSError("synthetic checkpoint storage failure")
            return original(path, create)

        monkeypatch.setattr(diagnostics.files(), "open_file", fail_checkpoint)
        sys.stderr.write("native presentation still works")
        sys.stderr.flush()
    assert producer.failed
    assert json.loads(next(directory.glob("*.completed.json")).read_text())["exit"] == 0
    assert (directory / "missing-diagnostics.json").exists()


@pytest.mark.parametrize("record", [b'{"writer":"another-writer"}\n', b'{"invalid":\n'])
def test_unknown_checkpoint_is_not_silently_reconciled(diagnostic_context, record):
    diagnostics, directory = diagnostic_context
    with diagnostics.writer() as producer:
        sys.stderr.write("private pending value")
        sys.stderr.flush()
        producer.checkpoints.write(record)
        os.fsync(producer.checkpoints.fileno())
        pending = directory / f"{producer.identifier}.producer.pending"
        before = pending.read_bytes()
        with pytest.raises(ValueError):
            diagnostics.recover_producer_checkpoint(dict(os.environ), producer.identifier)
        assert pending.read_bytes() == before
        assert not list(directory.glob("*.recovered.*.log"))
        assert not (directory / "completed.json").exists()


def test_child_fileno_is_original_transport_not_archive(tmp_path):
    result = invoke(tmp_path, """
import subprocess
with diagnostics.writer():
    print("root protocol")
    sys.stdout.flush()
    subprocess.run(
        [sys.executable, "-I", "-c", "print('child-direct-fd')"],
        stdout=sys.stdout, stderr=sys.stderr, check=True, timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
""")
    assert result.returncode == 0
    assert result.stdout.decode().splitlines() == ["root protocol", "child-direct-fd"]
    _, _, text, _ = evidence(tmp_path)
    assert "root protocol" in text
    assert "child-direct-fd" not in text


def test_native_correlation_and_receipt_are_unchanged(diagnostic_context, monkeypatch):
    diagnostics, directory = diagnostic_context
    monkeypatch.setenv("COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER", "b" * 32)
    previous = (directory / "attempt.json").read_bytes()
    with diagnostics.writer():
        print("correlated producer")
        assert os.environ["COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT"] == directory.name
    assert (directory / "attempt.json").read_bytes() == previous
    record = json.loads(next(directory.glob("*.start.json")).read_text())
    assert record["parent"] == "b" * 32
    assert record["kind"] == "retained-installer"
    assert not (directory / "completed.json").exists()


@pytest.mark.parametrize("host", POWERSHELLS)
def test_real_native_attempt_is_inherited_by_python_entry(tmp_path, host):
    child = tmp_path / "correlated.py"
    child.write_text(
        "import sys\nsys.path.insert(0, " + repr(str(SCRIPTS)) + ")\n"
        "import installer_diagnostics as diagnostics\n"
        "with diagnostics.writer():\n    print('native-python-protocol')\n",
        encoding="utf-8",
    )
    executable = sys.executable.replace("'", "''")
    path = str(child).replace("'", "''")
    result = ps_run(tmp_path, host, f"""
Initialize-InstallerDiagnostics 'agent-worktrees'
$capture = New-InstallerDiagnosticCapture -Piped
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = '{executable}'
$start.Arguments = '-I -B -X utf8 "{path}"'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$process = [Diagnostics.Process]::Start($start)
$process.StandardInput.Close()
$null = $process.Handle
if (-not (Wait-InstallerDiagnosticChild $process $capture 10)) {{ throw 'isolated producer did not exit' }}
$rc = $process.ExitCode
Save-InstallerDiagnosticCapture $capture
exit $rc
""", env={"COPILOT_EXTENSIONS_HUMAN_UTF8": "1"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "native-python-protocol"
    directory, receipt, text, completed = evidence(tmp_path)
    assert receipt["plugin"] == "agent-worktrees"
    assert receipt["schema"] == "copilot-extensions.installer-diagnostics"
    assert "producer" not in receipt
    assert "native-python-protocol" in text
    assert len(list(directory.glob("*.start.json"))) == 2
    assert len(completed) == 1 and completed[0]["exit"] == 0


@pytest.mark.parametrize("field,value", [
    ("plugin", "another-plugin"), ("schema", "another-schema"), ("attempt", "c" * 32),
])
def test_invalid_inherited_identity_is_not_adopted(diagnostic_context, field, value):
    diagnostics, directory = diagnostic_context
    path = directory / "attempt.json"
    document = json.loads(path.read_text())
    document[field] = value
    path.write_text(json.dumps(document), encoding="utf-8")
    with diagnostics.writer() as producer:
        assert producer is None
    assert not list(directory.glob("*.start.json"))
    assert diagnostics.reference()["missing_diagnostics"]


def test_logging_failure_preserves_primary_exception(diagnostic_context):
    diagnostics, directory = diagnostic_context
    primary = RuntimeError("primary failure remains")
    with pytest.raises(RuntimeError) as caught:
        with diagnostics.writer() as producer:
            producer.output.close()
            print("native output still works")
            raise primary
    assert caught.value is primary
    record = json.loads(next(directory.glob("*.completed.json")).read_text())
    assert record["missing_diagnostics"] is True
    assert record["exit"] == 1
    assert (directory / "missing-diagnostics.json").exists()


def test_existing_nonprivate_directory_is_not_repaired_or_used(tmp_path):
    root = tmp_path / ".copilot-extensions" / "installer-diagnostics"
    root.mkdir(parents=True)
    if os.name != "nt":
        root.chmod(0o755)
    result = invoke(tmp_path, "with diagnostics.writer():\n    print('successful primary')\n")
    assert result.returncode == 0
    assert result.stdout.strip() == b"successful primary"
    assert b"missing diagnostics" in result.stderr
    assert not list(root.iterdir())


def test_hardlinked_diagnostic_file_is_refused(diagnostic_context):
    diagnostics, directory = diagnostic_context
    original, alias = directory / "original", directory / "alias"
    fd = diagnostics.files().open_file(original, create=True)
    os.close(fd)
    os.link(original, alias)
    with pytest.raises(PermissionError, match="exclusive"):
        diagnostics.files().open_file(alias)


def test_linked_inherited_attempt_is_refused(diagnostic_context, tmp_path):
    diagnostics, directory = diagnostic_context
    target = tmp_path / "retained-attempt"
    directory.rename(target)
    try:
        directory.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(directory), str(target)],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
    with diagnostics.writer() as producer:
        assert producer is None
    assert not list(target.glob("*.start.json"))


def test_python_retention_preserves_incomplete_and_pending_writers(diagnostic_context):
    diagnostics, active = diagnostic_context
    root = active.parent
    for name in ("b" * 32, "c" * 32, "d" * 32):
        directory = diagnostics.files().private_directory(root / name, create=True)
        diagnostics._record(directory / "attempt.json", {
            "schema": "copilot-extensions.installer-diagnostics", "producer": "python",
            "plugin": "agent-worktrees", "attempt": name,
        })
        diagnostics._record(directory / "completed.json", {"completed": "2000-01-01T00:00:00+00:00"})
        diagnostics._record(directory / "writer.start.json", {})
        if name != "c" * 32:
            diagnostics._record(directory / "writer.completed.json", {})
        if name == "d" * 32:
            diagnostics._record(directory / "transport.pending", {})
    diagnostics._prune(root, active)
    assert not (root / ("b" * 32)).exists()
    assert (root / ("c" * 32)).exists()
    assert (root / ("d" * 32)).exists()
    assert active.exists()


@pytest.mark.parametrize("code,expected", [(None, 0), (19, 19), ("useful exit failure", 1)])
def test_system_exit_records_actual_requested_result(diagnostic_context, code, expected):
    diagnostics, directory = diagnostic_context
    with pytest.raises(SystemExit) as caught:
        with diagnostics.writer():
            raise SystemExit(code)
    assert caught.value.code == code
    record = json.loads(next(directory.glob("*.completed.json")).read_text())
    assert record["exit"] == expected
    if isinstance(code, str):
        assert code in next(directory.glob("*.log")).read_text()


def test_frozen_bootstrap_carries_private_helper_and_records_import_failure(tmp_path):
    import agent_worktrees.runtime_candidate as candidate

    required = {"installer_diagnostics.py", "installer_diagnostics_redact.py", "installer_diagnostics_files.py"}
    assert required <= set(candidate._LIFECYCLE_FILES)
    frozen = tmp_path / ".runtime-bootstrap"
    frozen.mkdir()
    for name in ("runtime_install.py", *required):
        shutil.copyfile(SCRIPTS / name, frozen / name)
    result = invoke(tmp_path, entry=frozen / "runtime_install.py")
    assert result.returncode == 1
    _, _, text, completed = evidence(tmp_path)
    assert "installer_engine" in text
    assert completed[0]["error_class"] == "ModuleNotFoundError"


@pytest.mark.skipif(os.name != "nt", reason="Windows TokenUser/DACL control")
@pytest.mark.parametrize("leaf", [False, True])
def test_foreign_read_ace_is_refused_even_without_public_write(diagnostic_context, leaf):
    import ctypes as c

    diagnostics, directory = diagnostic_context
    native = diagnostics.files().windows()
    path = directory / "attempt.json" if leaf else directory
    descriptor = native.w.LPVOID()
    sddl = f"O:{native.account}D:P(A;OICI;FA;;;{native.account})(A;OICI;FR;;;WD)"
    assert native.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, c.byref(descriptor), None,
    )
    method = native.security.SetFileSecurityW
    method.argtypes, method.restype = [native.w.LPCWSTR, native.w.DWORD, native.w.LPVOID], native.w.BOOL
    try:
        assert method(str(path), 0x80000004, descriptor)
    finally:
        native.kernel.LocalFree(descriptor)
    with pytest.raises(PermissionError, match="foreign"):
        diagnostics.context()
