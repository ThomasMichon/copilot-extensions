"""Real stdlib error reports retain metadata without archiving message values."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_command_evidence import _recent, _run
from test_command_evidence import payload as command_payload

payload = command_payload


def _script(
    payload: tuple[Path, dict[str, str]], tmp_path: Path, source: str, *arguments: str,
) -> tuple[subprocess.CompletedProcess[bytes], subprocess.CompletedProcess[bytes]]:
    script = tmp_path / "report-fixture.py"
    script.write_text(source, encoding="utf-8")
    direct = subprocess.run(
        [sys.executable, "-I", "-B", str(script), *arguments],
        env=payload[1], capture_output=True, timeout=15, check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    wrapped = _run(
        payload, "run-script", "fixture-command", "fixture-source", str(script), "--", *arguments,
    )
    assert wrapped.returncode == direct.returncode
    assert wrapped.stdout == direct.stdout
    assert wrapped.stderr == direct.stderr
    return direct, wrapped


def test_parser_failure_keeps_actual_streams_and_excludes_private_argument_values(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    direct, _ = _script(
        payload, tmp_path,
        "import argparse\n"
        "parser=argparse.ArgumentParser(prog='fixture-command')\n"
        "parser.add_argument('--number',type=int)\n"
        "parser.parse_args()\n",
        "--number", "private-argument-canary",
    )
    assert direct.returncode == 2 and b"private-argument-canary" in direct.stderr
    evidence = _recent(payload)
    parser, = [record for record in evidence["records"] if record["phase"] == "parser"]
    assert parser["code"] == "parser-invalid-argument"
    assert parser["exit_code"] is None and parser["completeness"] == "metadata-only"
    assert any(record["exit_code"] == 2 for record in evidence["records"])
    assert "private-argument-canary" not in json.dumps(evidence)


def test_nonpropagating_error_log_at_exit_zero_is_retained_without_message_content(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    direct, _ = _script(
        payload, tmp_path,
        "import logging,sys\n"
        "logger=logging.getLogger('private-logger-canary')\n"
        "logger.propagate=False\n"
        "logger.addHandler(logging.StreamHandler(sys.stderr))\n"
        "logger.error('private-message-canary %s','private-format-canary')\n",
    )
    assert direct.returncode == 0 and b"private-message-canary" in direct.stderr
    evidence = _recent(payload)
    record, = evidence["records"]
    assert record["code"] == "error-log-record" and record["phase"] == "application"
    assert record["exit_code"] is None and record["completeness"] == "metadata-only"
    assert all(value not in json.dumps(evidence) for value in (
        "private-logger-canary", "private-message-canary", "private-format-canary",
    ))


@pytest.mark.parametrize("report", [
    "logging.warning('ordinary warning')",
    "logger=logging.getLogger('filtered');logger.addFilter(lambda record:False);logger.error('excluded')",
])
def test_nonerror_or_filtered_logs_do_not_create_error_storage(
    payload: tuple[Path, dict[str, str]], tmp_path: Path, report: str,
) -> None:
    _script(payload, tmp_path, "import logging\n" + report + "\n")
    evidence = _recent(payload)
    assert evidence["records"] == [] and evidence["exists"] is False


def test_error_report_in_shutdown_hook_keeps_the_containing_command(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    _script(
        payload, tmp_path,
        "import atexit,logging\n"
        "atexit.register(lambda:logging.error('private-shutdown-canary'))\n",
    )
    record, = _recent(payload)["records"]
    assert record["command"] == "fixture-command" and record["code"] == "error-log-record"
    assert "private-shutdown-canary" not in json.dumps(record)


def test_preloaded_logging_keeps_original_reports_and_marks_partial_shutdown_coverage(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    launcher = tmp_path / "preloaded-logging.py"
    launcher.write_text(
        "import logging,runpy,sys\n"
        "archive=sys.argv.pop(1)\n"
        "runpy.run_path(archive,run_name='__main__')\n",
        encoding="utf-8",
    )
    script = tmp_path / "application.py"
    script.write_text(
        "import logging\nlogging.error('private-preloaded-canary')\n", encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(launcher), str(payload[0]), "run-script",
         "fixture-command", "fixture-source", str(script), "--"],
        env=payload[1], capture_output=True, timeout=15, check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert result.returncode == 0 and result.stdout == b"", result.stderr
    assert result.stderr == ("ERROR:root:private-preloaded-canary" + os.linesep).encode()
    records = _recent(payload)["records"]
    assert {record["code"] for record in records} == {
        "report-shutdown-unavailable", "error-log-record",
    }
    assert "private-preloaded-canary" not in json.dumps(records)


def test_worker_thread_exception_is_retained_without_changing_exit_zero(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    direct, _ = _script(
        payload, tmp_path,
        "import threading\n"
        "def fail(): raise ValueError('private-thread-canary')\n"
        "thread=threading.Thread(target=fail,name='fixture-thread')\n"
        "thread.start()\n"
        "thread.join()\n",
    )
    assert direct.returncode == 0 and b"private-thread-canary" in direct.stderr
    record, = _recent(payload)["records"]
    assert record["code"] == "thread-exception" and record["exit_code"] is None
    assert "private-thread-canary" not in json.dumps(record)


def test_logging_from_a_storage_audit_callback_does_not_reenter_the_writer(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    script = tmp_path / "audit-fixture.py"
    script.write_text(
        "import logging,sys\n"
        "def observe(event,args):\n"
        "    if event=='open': logging.error('private-audit-canary')\n"
        "sys.addaudithook(observe)\n"
        "logging.error('private-primary-canary')\n"
        "print('original command completed')\n",
        encoding="utf-8",
    )
    result = _run(
        payload, "run-script", "fixture-command", "fixture-source", str(script), "--",
    )
    assert result.returncode == 0
    assert result.stdout == ("original command completed" + os.linesep).encode()
    assert b"private-audit-canary" in result.stderr
    record, = _recent(payload)["records"]
    assert record["code"] == "error-log-record"
    assert "private-audit-canary" not in json.dumps(record)
    assert "private-primary-canary" not in json.dumps(record)


def test_observer_does_not_preload_a_shadowed_argument_parser(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    (tmp_path / "argparse.py").write_text("MARKER='custom application parser'\n", encoding="utf-8")
    script = tmp_path / "custom-parser.py"
    script.write_text("import argparse\nprint(argparse.MARKER)\n", encoding="utf-8")
    results = []
    for arguments in (
        [str(script)],
        [str(payload[0]), "run-script", "fixture-command", "fixture-source", str(script), "--"],
    ):
        results.append(subprocess.run(
            [sys.executable, "-B", *arguments], env=payload[1],
            capture_output=True, timeout=15, check=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        ))
    direct, wrapped = results
    assert direct.returncode == wrapped.returncode == 0
    assert direct.stdout == wrapped.stdout and direct.stderr == wrapped.stderr == b""
    record, = _recent(payload)["records"]
    assert record["code"] == "report-adapter-unavailable"
    assert record["phase"] == "entry" and record["exit_code"] is None


def test_unraisable_exception_keeps_original_hook_output_and_excludes_object_values(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    script = tmp_path / "unraisable-fixture.py"
    script.write_text(
        "class PrivateObject:\n"
        "    def __del__(self): raise ValueError('private-unraisable-canary')\n"
        "PrivateObject.__del__.__qualname__='private-object-canary'\n"
        "obj=PrivateObject()\n"
        "del obj\n",
        encoding="utf-8",
    )
    direct = subprocess.run(
        [sys.executable, "-I", "-B", str(script)], env=payload[1],
        capture_output=True, timeout=15, check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    result = _run(
        payload, "run-script", "fixture-command", "fixture-source", str(script), "--",
    )
    assert result.returncode == 0 and not result.stdout
    assert b"private-unraisable-canary" in result.stderr
    assert b"private-object-canary" in result.stderr
    assert result.returncode == direct.returncode and result.stdout == direct.stdout
    assert re.sub(rb"0x[0-9a-fA-F]+", b"<address>", result.stderr) == re.sub(
        rb"0x[0-9a-fA-F]+", b"<address>", direct.stderr,
    )
    record, = _recent(payload)["records"]
    assert record["code"] == "unraisable-exception" and record["exit_code"] is None
    assert "private-unraisable-canary" not in json.dumps(record)
    assert "private-object-canary" not in json.dumps(record)


def _owner_script(
    payload: tuple[Path, dict[str, str]], tmp_path: Path, source: str,
) -> subprocess.CompletedProcess[bytes]:
    script = tmp_path / "report-owner-fixture.py"
    script.write_text(
        "import logging,threading,time,sys,json,os\n"
        "from pathlib import Path\n"
        "from _command_evidence.process_telemetry import command_entry as entry\n"
        "from _command_evidence.process_telemetry.command_errors import CommandErrorRecorder,recent_errors\n"
        "owner=entry._REPORT_OWNER\n"
        "assert owner is not None\n"
        + source,
        encoding="utf-8",
    )
    return _run(payload, "run-script", "fixture-command", "fixture-source", str(script), "--")


def test_slow_writer_does_not_run_on_callback_and_keeps_observed_identity_and_time(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "ready=threading.Event()\n"
        "release=threading.Event()\n"
        "def pause():\n"
        "    ready.set()\n"
        "    assert release.wait(5)\n"
        "owner.writer._on_iteration=pause\n"
        "assert ready.wait(5)\n"
        "directory=Path(__file__).parent/'nested-evidence'\n"
        "nested=CommandErrorRecorder('nested-command','nested-source',directory=directory)\n"
        "before=time.time()\n"
        "try:\n"
        "    with nested.activate():\n"
        "        logging.error('private-buffered-canary')\n"
        "    assert owner.writer.stats()['accepted']==1\n"
        "    assert not directory.exists()\n"
        "    assert time.time()-before<.2\n"
        "    time.sleep(.05)\n"
        "    released=time.time()\n"
        "finally: release.set()\n"
        "owner.close()\n"
        "record,=recent_errors(directory)['records']\n"
        "from datetime import datetime\n"
        "assert before-.000001<=datetime.fromisoformat(record['ts']).timestamp()<released\n"
        "assert record['command']=='nested-command'\n"
        "assert record['source']=='nested-source'\n"
        "assert record['correlation_id']==nested.correlation_id\n"
        "assert record['parent_id']==owner.recorder.correlation_id\n"
        "assert 'private-buffered-canary' not in json.dumps(record)\n"
        "assert 'directory' not in record\n",
    )
    assert result.returncode == 0, result.stderr
    assert _recent(payload)["exists"] is False


def test_full_and_contended_queues_report_incomplete_without_callback_storage(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "ready=threading.Event()\n"
        "release=threading.Event()\n"
        "def pause():\n"
        "    ready.set()\n"
        "    assert release.wait(5)\n"
        "owner.writer._on_iteration=pause\n"
        "assert ready.wait(5)\n"
        "try:\n"
        "    with owner.writer._lock:\n"
        "        entry._report('synthetic-error','Synthetic failure',phase='application')\n"
        "    for _ in range(129):\n"
        "        entry._report('synthetic-error','Synthetic failure',phase='application')\n"
        "    counts=owner.writer.stats()\n"
        "    assert counts['accepted']==128 and counts['full']==1 and counts['contended']==1\n"
        "    assert not owner.recorder.directory.exists()\n"
        "finally: release.set()\n"
        "owner.close()\n"
        "assert owner.writer.join(.25)\n",
    )
    assert result.returncode == 0, result.stderr
    assert b"Automatic command evidence incomplete" in result.stderr


def test_failed_writer_is_not_counted_as_persisted_or_replaced(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "ready=threading.Event()\n"
        "release=threading.Event()\n"
        "def pause():\n"
        "    ready.set()\n"
        "    assert release.wait(5)\n"
        "owner.writer._on_iteration=pause\n"
        "assert ready.wait(5)\n"
        "def fail(event): raise OSError('private-writer-canary')\n"
        "owner.writer._sink=fail\n"
        "worker=owner.writer._thread\n"
        "try: entry._report('synthetic-error','Synthetic failure',phase='application')\n"
        "finally: release.set()\n"
        "owner.close()\n"
        "counts=owner.writer.stats()\n"
        "assert counts['accepted']==1 and counts['delivered']==0 and counts['sink_failed']==1\n"
        "assert owner.writer._thread is worker\n",
    )
    assert result.returncode == 0, result.stderr
    assert b"Automatic command evidence incomplete" in result.stderr
    assert b"private-writer-canary" not in result.stderr
    assert _recent(payload)["exists"] is False


def test_shutdown_abandons_a_blocked_owner_within_its_existing_bounds(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "ready=threading.Event()\n"
        "release=threading.Event()\n"
        "def pause():\n"
        "    ready.set()\n"
        "    assert release.wait(5)\n"
        "owner.writer._on_iteration=pause\n"
        "assert ready.wait(5)\n"
        "try:\n"
        "    entry._report('synthetic-error','Synthetic failure',phase='application')\n"
        "    started=time.monotonic()\n"
        "    owner.close()\n"
        "    assert time.monotonic()-started<.75\n"
        "    assert owner.writer.stats()['delivered']==0\n"
        "    assert not owner.writer.join(0)\n"
        "finally: release.set()\n"
        "assert owner.writer.join(.25)\n"
        "assert owner.writer.stats()['abandoned']==1\n",
    )
    assert result.returncode == 0, result.stderr
    assert b"Automatic command evidence incomplete" in result.stderr
    assert _recent(payload)["exists"] is False


def test_unavailable_automatic_storage_keeps_primary_error_and_explicit_degradation(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "owner.recorder.directory.parent.mkdir(mode=0o700,parents=True,exist_ok=True)\n"
        "owner.recorder.directory.write_text('fixture storage obstacle')\n"
        "logging.error('private-unavailable-canary')\n"
        "owner.close()\n"
        "counts=owner.writer.stats()\n"
        "assert counts['accepted']==1 and counts['delivered']==0 and counts['sink_failed']==1\n",
    )
    assert result.returncode == 0, result.stderr
    assert b"ERROR:root:private-unavailable-canary" in result.stderr
    assert b"command evidence unavailable" in result.stderr
    assert b"Automatic command evidence incomplete" in result.stderr


def test_logging_handler_close_reports_are_flushed_after_logging_shutdown(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "class Reporter(logging.Handler):\n"
        "    def close(self):\n"
        "        entry._report('handler-close-error','Logging handler close failed',phase='completion')\n"
        "        super().close()\n"
        "handler=Reporter()\n",
    )
    assert result.returncode == 0 and result.stderr == b"", result.stderr
    record, = _recent(payload)["records"]
    assert record["code"] == "handler-close-error"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Native POSIX fork")
def test_single_threaded_application_fork_keeps_original_warning_stream(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    _script(
        payload, tmp_path,
        "import os\n"
        "child=os.fork()\n"
        "if child==0: os._exit(0)\n"
        "assert os.waitpid(child,0)[1]==0\n",
    )


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Native POSIX fork")
def test_fork_starts_fresh_writer_without_replaying_parent_queue(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "ready=threading.Event()\n"
        "release=threading.Event()\n"
        "def pause():\n"
        "    ready.set()\n"
        "    assert release.wait(5)\n"
        "owner.writer._on_iteration=pause\n"
        "assert ready.wait(5)\n"
        "entry._report('parent-error','Synthetic parent failure',phase='application')\n"
        "assert owner.writer.stats()['accepted']==1 and owner.writer.stats()['delivered']==0\n"
        "release.set()\n"
        "child=os.fork()\n"
        "if child==0:\n"
        "    import signal\n"
        "    signal.alarm(3)\n"
        "    child_owner=entry._REPORT_OWNER\n"
        "    assert child_owner is not owner and child_owner.pid==os.getpid()\n"
        "    child_ready=threading.Event()\n"
        "    child_release=threading.Event()\n"
        "    def child_pause():\n"
        "        child_ready.set()\n"
        "        assert child_release.wait(2)\n"
        "    child_owner.writer._on_iteration=child_pause\n"
        "    assert child_ready.wait(2)\n"
        "    try: entry._report('child-error','Synthetic child failure',phase='application')\n"
        "    finally: child_release.set()\n"
        "    child_owner.close()\n"
        "    os._exit(0)\n"
        "try:\n"
        "    _,status=os.waitpid(child,0)\n"
        "    assert status==0\n"
        "finally: release.set()\n"
        "owner.close()\n"
        "records=recent_errors()['records']\n"
        "assert sorted(record['code'] for record in records)==['child-error','parent-error'],(records,owner.writer.stats())\n"
        "assert {record['pid'] for record in records}=={os.getpid(),child}\n",
    )
    assert result.returncode == 0 and result.stderr == b"", result.stderr
