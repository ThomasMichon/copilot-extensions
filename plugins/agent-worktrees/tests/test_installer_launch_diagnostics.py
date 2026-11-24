"""Exact caller references do not claim installer entry or installation ownership."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, BinaryIO, NoReturn, cast
import pytest
import update_telemetry as ut
from update_telemetry.human import _available

from agent_worktrees import installer_diagnostics, installer_run, registry_paths, update_events
from agent_worktrees import update_runtime as runtime
from agent_worktrees import update_runtime_plan


PAYLOAD = Path(__file__).resolve().parents[1]


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("code", [0, 37])
@pytest.mark.timeout(30)
def test_publication_handoff_preserves_healthy_output_and_private_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int,
) -> None:
    monkeypatch.syspath_prepend(str(PAYLOAD / "scripts"))
    import runtime_install_diagnostics as entry
    import installer_diagnostics as diagnostics

    stdout_path, stderr_path = tmp_path / "stdout", tmp_path / "stderr"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in tuple(os.environ):
        if name.startswith("COPILOT_EXTENSIONS_DIAGNOSTICS_"):
            monkeypatch.delenv(name)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        with monkeypatch.context() as presentation:
            presentation.setattr(sys, "stdout", stdout)
            presentation.setattr(sys, "stderr", stderr)
            with diagnostics.writer():
                directory = diagnostics.context()
                completed = entry.settle_publication(
                    [sys.executable, "-I", "-c",
                     "import os;os.write(1,b'publication progress\\n');"
                     "os.write(2,b'publication warning and traceback\\n');"
                     f"raise SystemExit({code})"],
                    dict(os.environ), diagnostics=diagnostics, verifier=None,
                )
    assert completed.returncode == code
    loss = [json.loads(path.read_text()) for path in directory.glob("*.presentation-loss.json")]
    assert stdout_path.read_bytes() == b"publication progress\n", loss
    assert stderr_path.read_bytes() == b"publication warning and traceback\n", loss
    assert any(path.read_bytes() == b"publication progress\n" for path in directory.glob("*.stdout.raw"))
    assert any(path.read_bytes() == b"publication warning and traceback\n" for path in directory.glob("*.stderr.raw"))


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("failure", ["blocked", "large", "relay-refused", "late-close"])
@pytest.mark.timeout(30)
def test_publication_handoff_bounds_presentation_without_losing_native_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from agent_procutil import SyncProcessLaunchError, _output_relay

    monkeypatch.syspath_prepend(str(PAYLOAD / "scripts"))
    import runtime_install_diagnostics as entry
    import installer_diagnostics as diagnostics

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for name in tuple(os.environ):
        if name.startswith("COPILOT_EXTENSIONS_DIAGNOSTICS_"):
            monkeypatch.delenv(name)
    if failure == "relay-refused":
        def refuse(*args: Any, **kwargs: Any) -> NoReturn:
            raise SyncProcessLaunchError(OSError("fixture relay launch refusal"))
        monkeypatch.setattr(_output_relay, "ByteRelay", refuse)
    if failure == "late-close":
        original_close = _output_relay.ByteRelay.close

        def late_close(relay: _output_relay.ByteRelay) -> None:
            relay._errors.append(OSError("late publication delivery error"))
            original_close(relay)

        monkeypatch.setattr(_output_relay.ByteRelay, "close", late_close)
    reader, writer = os.pipe()
    try:
        if failure == "blocked":
            set_blocking: Callable[[int, bool], None] | None = getattr(os, "set_blocking", None)
            if set_blocking is None:
                pytest.skip("pipe blocking controls require Python 3.12 or newer on Windows")
            set_blocking(writer, False)
            while True:
                try:
                    os.write(writer, b"x" * 1024)
                except BlockingIOError:
                    break
            set_blocking(writer, True)
        size = 300000 if failure == "large" else 8192
        stdout_path, stderr_path = tmp_path / "stdout", tmp_path / "stderr"
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            with os.fdopen(os.dup(writer), "w", encoding="utf-8") as blocked, monkeypatch.context() as presentation:
                presentation.setattr(sys, "stdout", blocked if failure == "blocked" else stdout)
                presentation.setattr(sys, "stderr", stderr)
                with diagnostics.writer():
                    directory = diagnostics.context()
                    started = time.monotonic()
                    completed = entry.settle_publication(
                        [sys.executable, "-I", "-c",
                         f"import os;os.write(1,b'o'*{size});os.write(2,b'warning\\n');raise SystemExit(37)"],
                        dict(os.environ), diagnostics=diagnostics, verifier=None,
                    )
                    assert time.monotonic() - started < 5
        assert completed.returncode == 37
        assert any(path.read_bytes() == b"o" * size for path in directory.glob("*.stdout.raw"))
        assert any(path.read_bytes() == b"warning\n" for path in directory.glob("*.stderr.raw"))
        loss = json.loads(next(directory.glob("*.presentation-loss.json")).read_text())
        assert loss["presentation_incomplete"] is True
        assert loss["channels"] == (["stdout", "stderr"] if failure in {"relay-refused", "late-close"} else ["stdout"])
        if failure not in {"relay-refused", "late-close"}:
            assert stderr_path.read_bytes() == b"warning\n"
        if failure == "large":
            assert stdout_path.stat().st_size <= 256 * 1024
    finally:
        os.close(writer)
        os.close(reader)


def test_other_runtime_steps_do_not_enable_representative_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        captured.append(kwargs)
        return SimpleNamespace(status="OK")

    monkeypatch.setattr(runtime, "_run_human_installer", run)
    monkeypatch.setattr(
        update_events, "installer_diagnostics",
        lambda *args: pytest.fail("other consumers have not opted into capture"),
    )
    step = update_runtime_plan.RuntimeUpdateStep(
        name="agent-example", phase="registered", payload=tmp_path,
        argv=("owned-installer",), deadline_s=30,
    )
    assert runtime._execute_runtime_step(step) == "OK"
    assert len(captured) == 1 and "diagnostics" not in captured[0]


@pytest.fixture
def launch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(registry_paths, "_payload_root", lambda: PAYLOAD)
    # Tests execute the checkout helper, not an unrelated selected runtime.
    monkeypatch.setattr(cast(Any, installer_diagnostics).sys, "prefix", str(tmp_path / "python"))
    environment = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    environment.update(
        COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT="a" * 32,
        COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER="b" * 32,
        COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY="not-the-new-attempt",
    )
    return environment


@pytest.mark.parametrize("nested_environment", [False, True])
def test_launch_uses_retained_helper_without_payload_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nested_environment: bool,
) -> None:
    slot = tmp_path / "slot"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    for name in ("installer_diagnostics.py", "installer_diagnostics_files.py", "installer_diagnostics_redact.py"):
        shutil.copyfile(PAYLOAD / "scripts" / name, bootstrap / name)
    prefix = slot / ".venv" if nested_environment else slot
    monkeypatch.setattr(cast(Any, installer_diagnostics).sys, "prefix", str(prefix))

    def unavailable_payload() -> NoReturn:
        pytest.fail("a retained runtime must not read the independently mutable payload")

    monkeypatch.setattr(registry_paths, "_payload_root", unavailable_payload)
    helper = cast(Any, installer_diagnostics)._helper()
    assert Path(helper.__file__) == bootstrap / "installer_diagnostics.py"


def run(argv: list[str], environment: dict[str, str], **kwargs: Any) -> installer_run.InstallerRun:
    return installer_run.run_installer(
        argv, cwd=PAYLOAD, env=environment, deadline_s=10, diagnostics=True, **kwargs,
    )


def producer(body: str) -> list[str]:
    path = str(PAYLOAD / "scripts" / "installer_diagnostics.py")
    return [sys.executable, "-I", "-X", "utf8", "-c",
            f"import runpy;d=runpy.run_path({path!r});\n"
            f"with d['writer']():\n{body}"]


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("code", [0, 23])
def test_native_producer_reference_survives_missing_live_streams(launch_env: dict[str, str], tmp_path: Path, code: int) -> None:
    environment = dict(launch_env)
    parent_environment = {key: value for key, value in os.environ.items()
                          if key.startswith("COPILOT_EXTENSIONS_DIAGNOSTICS_")}
    with (tmp_path / "presentation").open("wb") as stream:
        result = run(
            producer(f" print('useful fixture output',flush=True)\n raise SystemExit({code})"),
            environment, output_fd=stream.fileno(),
        )
    reference = cast(dict[str, Any], result.diagnostics)
    assert reference["status"] == "available"
    assert reference["installer_entry"] == "observed"
    assert not reference["missing_diagnostics"]
    assert reference["attribution"] == "UNATTRIBUTED"
    directory = Path(reference["directory"])
    assert "useful fixture output" in next(directory.glob("*.log")).read_text(encoding="utf-8")
    assert json.loads((directory / "launch-result.json").read_text())["outcome"] == "returned"
    assert json.loads((directory / "launch-result.json").read_text())["native_exit"] == code
    assert result.rc == code
    assert result.status == ("OK" if code == 0 else "installer exited 23")
    assert environment == launch_env
    assert {key: value for key, value in os.environ.items()
            if key.startswith("COPILOT_EXTENSIONS_DIAGNOSTICS_")} == parent_environment
    assert reference["attempt"] != environment["COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT"]
    assert not (directory / "completed.json").exists(), "caller cannot complete installer writers"


@pytest.mark.real_installer_processes
def test_spawn_failure_keeps_exact_request_but_no_entry_claim(launch_env: dict[str, str], tmp_path: Path) -> None:
    argv = [str(tmp_path / "not-an-installer")]
    result = run(argv, launch_env)
    assert result.rc is None and not result.status.startswith("OK")
    assert cast(dict[str, Any], result.diagnostics)["installer_entry"] == "missing"
    assert cast(dict[str, Any], result.diagnostics)["missing_diagnostics"]
    directory = Path(cast(dict[str, Any], result.diagnostics)["directory"])
    assert (directory / "launch-request.json").is_file()
    assert not list(directory.glob("*.start.json"))
    assert not list(directory.glob("*.log"))
    assert json.loads((directory / "launch-result.json").read_text())["outcome"] == "spawn-failed"
    second = run(argv, launch_env)
    assert cast(dict[str, Any], second.diagnostics)["attempt"] != cast(dict[str, Any], result.diagnostics)["attempt"]
    assert (directory / "launch-result.json").is_file()


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_parser_failure_does_not_claim_entry(host: str, launch_env: dict[str, str], tmp_path: Path) -> None:
    if os.name != "nt" or not shutil.which(host):
        pytest.skip("Windows PowerShell host unavailable")
    script = tmp_path / "invalid.ps1"
    script.write_text("param(\n", encoding="utf-8")
    result = run([cast(str, shutil.which(host)), "-NoProfile", "-NonInteractive", "-File", str(script)], launch_env)
    assert result.rc != 0
    assert cast(dict[str, Any], result.diagnostics)["installer_entry"] == "missing"
    assert cast(dict[str, Any], result.diagnostics)["status"] == "missing"


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_powershell_entry_joins_the_reserved_attempt(host: str, launch_env: dict[str, str], tmp_path: Path) -> None:
    if os.name != "nt" or not shutil.which(host):
        pytest.skip("Windows PowerShell host unavailable")
    script = tmp_path / "entered.ps1"
    helper: Any = str(PAYLOAD / "scripts" / "installer-diagnostics.ps1").replace("'", "''")
    script.write_text(
        f". '{helper}'\nInitialize-InstallerDiagnostics 'agent-worktrees'\n"
        "Write-InstallerDiagnostic 'native entry fixture'\n"
        "Complete-InstallerDiagnostics 23\nexit 23\n", encoding="utf-8",
    )
    launch_env["PSModulePath"] = str(Path(cast(str, shutil.which(host))).parent / "Modules")
    result = run([cast(str, shutil.which(host)), "-NoProfile", "-NonInteractive", "-File", str(script)], launch_env)
    assert result.rc == 23 and result.status == "installer exited 23"
    directory = Path(cast(dict[str, Any], result.diagnostics)["directory"])
    helper = cast(Any, installer_diagnostics)._helper()
    for path in directory.iterdir():
        fd = helper.files().open_file(path)
        os.close(fd)
    assert cast(dict[str, Any], result.diagnostics)["status"] == "available", result.diagnostics
    assert "native entry fixture" in next(directory.glob("*.log")).read_text(encoding="utf-8")
    assert not (directory / "completed.json").exists()


@pytest.mark.real_installer_processes
def test_timeout_preserves_entry_reference_and_unfinished_writers(launch_env: dict[str, str]) -> None:
    result = installer_run.run_installer(
        producer(" import time\n print('before deadline',flush=True)\n time.sleep(60)"),
        cwd=PAYLOAD, env=launch_env, deadline_s=2, diagnostics=True,
    )
    assert result.status == "timed out (deadline hit during build)" and result.rc is None
    assert cast(dict[str, Any], result.diagnostics)["installer_entry"] == "observed"
    assert cast(dict[str, Any], result.diagnostics)["status"] == "available"
    directory = Path(cast(dict[str, Any], result.diagnostics)["directory"])
    assert "before deadline" in next(directory.glob("*.log")).read_text()
    assert not list(directory.glob("*.completed.json"))
    assert not (directory / "completed.json").exists()
    assert json.loads((directory / "launch-result.json").read_text())["outcome"] == "timeout"


@pytest.mark.real_installer_processes
@pytest.mark.skipif(os.name != "nt", reason="existing positive Job-tree teardown is Windows-only")
def test_flushed_partial_survives_owned_tree_timeout_without_retirement(launch_env: dict[str, str], tmp_path: Path) -> None:
    helper = cast(Any, installer_diagnostics)._helper()
    sink = helper.files().private_directory(tmp_path / "private-presentation", create=True) / "output"
    with os.fdopen(helper.files().open_file(sink, create=True), "wb") as stream:
        result = installer_run.run_installer(
            producer(
                " import sys,time\n"
                " sys.stdout.write('{\"protocol\":\"waiting\"}');sys.stdout.flush()\n"
                " sys.stderr.write('interrupted recoverable progress token');sys.stderr.flush()\n"
                " time.sleep(60)"
            ),
            cwd=PAYLOAD, env=launch_env, deadline_s=2, diagnostics=True,
            output_fd=stream.fileno(), require_tree_exit=True,
        )
    assert result.status.startswith("timed out") and result.rc is None
    directory = Path(cast(dict[str, Any], result.diagnostics)["directory"])
    identifier = next(directory.glob("*.start.json")).name.removesuffix(".start.json")
    environment = dict(launch_env, COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT=directory.name)
    recovered = helper.recover_producer_checkpoint(environment, identifier)
    assert "interrupted recoverable progress" in Path(recovered["recovered_log"]).read_text()
    assert b'{"protocol":"waiting"}' in sink.read_bytes()
    assert recovered["quiescence"] == "not-asserted"
    assert (directory / f"{identifier}.producer.pending").exists()
    assert not (directory / "completed.json").exists()
    assert not list(directory.glob("*.completed.json"))
    assert json.loads((directory / "launch-result.json").read_text())["outcome"] == "timeout"


@pytest.mark.real_installer_processes
def test_unavailable_diagnostics_preserve_native_result_and_warn(launch_env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    launch_env["USERPROFILE" if os.name == "nt" else "HOME"] = "relative-home"
    result = run([sys.executable, "-I", "-c", "raise SystemExit(23)"], launch_env)
    assert result.status == "installer exited 23" and result.rc == 23
    assert result.diagnostics == {
        "status": "missing", "installer_entry": "unknown", "missing_diagnostics": True,
    }
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("continuing with missing diagnostics") == 1


def test_logging_presentation_failure_cannot_replace_primary_result(launch_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    class Closed:
        def fileno(self) -> NoReturn:
            raise OSError("closed stderr")

    expected = installer_run.InstallerRun("installer exited 23", 23, None, False, .1)
    monkeypatch.setattr(installer_run, "_run_installer", lambda *a, **k: expected)
    with monkeypatch.context() as scoped:
        scoped.setattr(cast(Any, installer_diagnostics).sys, "stderr", Closed())
        result = run(["unused"], launch_env)
    assert result.status == expected.status and result.rc == expected.rc


@pytest.mark.parametrize("boundary", ["import", "completion"])
@pytest.mark.parametrize("error", [RuntimeError("optional helper failed"), SyntaxError("invalid helper")])
def test_helper_failure_cannot_replace_primary_result(launch_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, boundary: str, error: Exception) -> None:
    def failed(*args: Any, **kwargs: Any) -> NoReturn:
        raise error

    if boundary == "import":
        monkeypatch.setattr(installer_diagnostics, "_helper", failed)
    else:
        helper = cast(Any, installer_diagnostics)._helper()
        monkeypatch.setattr(helper, "complete_launch", failed)
        monkeypatch.setattr(installer_diagnostics, "_helper", lambda: helper)
    notices: list[str] = []
    monkeypatch.setattr(installer_diagnostics, "_notify", notices.append)
    expected = installer_run.InstallerRun("installer exited 23", 23, None, False, .1)
    monkeypatch.setattr(installer_run, "_run_installer", lambda *a, **k: expected)
    result = run(["unused"], launch_env)
    assert result.status == expected.status and result.rc == 23
    assert cast(dict[str, Any], result.diagnostics)["missing_diagnostics"]
    assert cast(dict[str, Any], result.diagnostics)["installer_entry"] == "unknown"
    assert cast(dict[str, Any], result.diagnostics)["status"] == ("missing" if boundary == "import" else "unknown")
    assert sum("continuing with missing diagnostics" in message for message in notices) == 1


def test_blocked_notification_keeps_one_worker_and_no_global_stream_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = threading.Event(), threading.Event()
    calls = []
    original_stdout = sys.stdout

    class Blocked:
        def fileno(self) -> NoReturn:
            calls.append("fileno")
            entered.set()
            release.wait(5)
            raise OSError("fixture released")

    monkeypatch.setattr(installer_diagnostics, "_NOTIFICATION", None)
    thread = None
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(cast(Any, installer_diagnostics).sys, "stderr", Blocked())
            started = time.monotonic()
            installer_diagnostics._notify("first")
            assert entered.wait(1)
            thread = installer_diagnostics._NOTIFICATION
            for _ in range(20):
                installer_diagnostics._notify("not queued or replayed")
            assert time.monotonic() - started < 1
            assert installer_diagnostics._NOTIFICATION is thread
            assert calls == ["fileno"]
            assert sys.stdout is original_stdout
    finally:
        release.set()
        if thread is not None:
            thread.join(1)
            assert not thread.is_alive()


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("missing", [False, True])
def test_full_unread_stderr_cannot_hold_launch_or_process_exit(launch_env: dict[str, str], tmp_path: Path, missing: bool) -> None:
    release = tmp_path / "release"
    program = """
import json,os,sys,threading,time
from pathlib import Path
from agent_worktrees import installer_run
filler=os.dup(2)
def fill():
    try:
        os.write(filler,b'x'*1048576)
    finally:
        os.close(filler)
threading.Thread(target=fill,daemon=True).start()
release=Path(sys.argv[1])
deadline=time.monotonic()+10
while not release.exists():
    if time.monotonic()>deadline:
        raise SystemExit(91)
    time.sleep(.01)
environment=dict(os.environ)
if sys.argv[2]=='True':
    environment['USERPROFILE' if os.name=='nt' else 'HOME']='relative-home'
with release.with_name('child-output').open('wb') as output:
    result=installer_run.run_installer(
        [sys.executable,'-I','-c',"print('healthy child output',flush=True); raise SystemExit(23)"],
        cwd=Path(sys.argv[3]),env=environment,deadline_s=2,diagnostics=True,
        output_fd=output.fileno(),
    )
print(json.dumps({'rc':result.rc,'status':result.status,'diagnostics':result.diagnostics}),flush=True)
"""
    # The runner's source paths are explicit; isolated mode would discard them.
    process = subprocess.Popen(
        [sys.executable, "-B", "-X", "utf8", "-c", program, str(release), str(missing), str(PAYLOAD)],
        env=launch_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 10
        while not _available(cast(BinaryIO, process.stderr).fileno()) and process.poll() is None:
            assert time.monotonic() < deadline, "fixture did not fill stderr"
            time.sleep(.01)
        assert process.poll() is None
        release.touch()
        process.wait(timeout=10)  # Deliberately do not drain stderr to unblock it.
        assert process.returncode == 0
        result = json.loads(cast(BinaryIO, process.stdout).read())
        assert result["rc"] == 23 and result["status"] == "installer exited 23", result
        assert release.with_name("child-output").read_bytes().strip() == b"healthy child output"
        reference = result["diagnostics"]
        assert reference["missing_diagnostics"]
        if not missing:
            directory = Path(reference["directory"])
            assert (directory / "launch-request.json").is_file()
            record = json.loads((directory / "launch-result.json").read_text())
            assert record["native_exit"] == 23 and record["outcome"] == "returned"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        cast(BinaryIO, process.stdout).close()
        cast(BinaryIO, process.stderr).close()


def test_interrupted_launch_retains_reference_without_replacing_exception(launch_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    error = KeyboardInterrupt()
    monkeypatch.setattr(installer_run, "_run_installer",
                        lambda *a, **k: (_ for _ in ()).throw(error))
    with pytest.raises(KeyboardInterrupt) as raised:
        run(["unused"], launch_env)
    assert raised.value is error
    root = Path(launch_env["HOME"]) / ".copilot-extensions" / "installer-diagnostics"
    result = json.loads(next(root.glob("*/launch-result.json")).read_text())
    assert result["outcome"] == "interrupted" and result["installer_entry"] == "missing"


@pytest.mark.parametrize("damage", ["writer", "privacy", "missing"])
def test_unverifiable_evidence_is_never_available(launch_env: dict[str, str], damage: str, monkeypatch: pytest.MonkeyPatch) -> None:
    launch = installer_diagnostics.Launch(launch_env)
    helper = cast(Any, launch.helper)
    directory = Path(cast(str, launch.reference["directory"]))
    identifier = "c" * 32
    helper._record(directory / f"{identifier}.start.json", {
        "writer": "d" * 32 if damage == "writer" else identifier,
    })
    helper._record(directory / f"{identifier}.log", {"fixture": True})
    if damage == "privacy":
        original = helper.files().open_file

        def refused(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path.name == f"{identifier}.log":
                raise PermissionError("private file validation refused")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(helper.files(), "open_file", refused)
    elif damage == "missing":
        helper._record(directory / "missing-diagnostics.json", {"missing_diagnostics": True})
    result = launch.finish()
    assert result["status"] != "available" and result["missing_diagnostics"]


def test_engine_records_exact_nested_reference_not_shared_root(launch_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    reference = {"attempt": "c" * 32, "directory": "fixture-directory",
                 "status": "missing", "missing_diagnostics": True}
    events = []
    monkeypatch.setattr(update_events, "_run",
                        SimpleNamespace(emit=lambda *a, **k: events.append((a, k))))
    monkeypatch.setattr(update_events, "record", lambda *a, **k: None)

    def install(argv: list[str], **kwargs: Any) -> installer_run.InstallerRun:
        assert kwargs["diagnostics"] is True and kwargs["receipt_owned"] is True
        return installer_run.InstallerRun("installer exited 23", 23, None, False, .1, reference)

    monkeypatch.setattr(runtime, "_run_human_installer", install)
    assert runtime._run_engine_installer(["fixture"], PAYLOAD, 10, launch_env) == 23
    assert events == [(("engine_runtime", "agent-worktrees@copilot-extensions", "info"), {
        "details": {"installer_diagnostics": reference},
    })]


def test_diagnostic_event_failure_preserves_native_result(
    launch_env: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_event(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("optional event sink failed")

    monkeypatch.setattr(update_events, "_run", SimpleNamespace(emit=failed_event))
    monkeypatch.setattr(update_events, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_run_human_installer", lambda *args, **kwargs: (
        installer_run.InstallerRun("installer exited 23", 23, None, False, .1, {"attempt": "c" * 32})
    ))
    assert runtime._run_engine_installer(["fixture"], PAYLOAD, 10, launch_env) == 23


@pytest.mark.real_installer_processes
def test_exact_reference_is_replayable_from_existing_update_events(launch_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ut.ENV_HOME, str(tmp_path / "telemetry"))
    for name in (ut.ENV_RUN_ID, ut.ENV_EVENTS, ut.ENV_OWNER):
        monkeypatch.delenv(name, raising=False)
    from agent_worktrees import reconcile

    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **k: None)
    update_events.start("fixture")
    events_path = cast(Path, cast(ut.Run, update_events._run).events_path)
    rc = 1
    try:
        rc = runtime._run_engine_installer(
            producer(" raise SystemExit(23)"), PAYLOAD, 10, launch_env,
        )
        assert rc == 23
    finally:
        update_events.finish(rc)
    events, _ = ut.read_events(events_path)
    references = [event["details"]["installer_diagnostics"] for event in events
                  if "installer_diagnostics" in event.get("details", {})]
    assert len(references) == 1
    reference = references[0]
    assert reference["status"] == "available" and reference["installer_entry"] == "observed"
    result = json.loads((Path(reference["directory"]) / "launch-result.json").read_text())
    assert reference["attempt"] == result["attempt"] and result["native_exit"] == 23
