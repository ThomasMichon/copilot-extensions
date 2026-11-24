"""Actual same-interpreter command errors, privacy and subsequent-process reads."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "command_evidence_producer", ROOT / "libs/payload-invocation/command_evidence.py",
)
assert spec and spec.loader
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)


@pytest.fixture
def payload(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    archive = tmp_path / "command-errors.pyz"
    archive.write_bytes(producer.archive(ROOT))
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("_command_evidence/fixture.py", (
            "import os,sys\n"
            "kind=sys.argv[1]\n"
            "if kind=='streams':\n"
            "    sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
            "    sys.stderr.buffer.write(b'ordinary stderr\\n')\n"
            "    raise SystemExit(37)\n"
            "if kind=='exception':\n"
            "    raise ValueError('private request content not to archive')\n"
            "if kind=='interrupt': raise KeyboardInterrupt\n"
            "if kind=='reported':\n"
            "    sys._command_error_recorder.record('selection-failed',"
            " 'Dependency selection failed', exit_code=0)\n"
            "if kind=='redaction':\n"
            "    sys._command_error_recorder.record('safe-error',"
            " 'password=credential-example', exit_code=0)\n"
            "if kind=='unicode':\n"
            "    sys._command_error_recorder.record('safe-error',"
            " 'Synthetic Unicode cause: 雪', exit_code=0)\n"
            "if kind=='maintenance':\n"
            "    sys._command_error_recorder.maintain(force=True)\n"
            "if kind=='shutdown-report':\n"
            "    import atexit\n"
            "    from _command_evidence.process_telemetry.command_errors import CommandErrorRecorder,current_recorder\n"
            "    owner = current_recorder()\n"
            "    with CommandErrorRecorder('nested-command','fixture-source').activate():\n"
            "        assert current_recorder() is not owner\n"
            "    assert current_recorder() is owner\n"
            "    atexit.register(lambda: current_recorder().record("
            "       'shutdown-error','Explicit shutdown failure',exit_code=0))\n"
            "if kind=='wide-reader':\n"
            "    import json\n"
            "    from _command_evidence.process_telemetry.command_errors import recent_errors\n"
            "    print(json.dumps(recent_errors(limit=1,max_bytes=4194304)))\n"
            "if kind.startswith('typed-exit-'):\n"
            "    class ExitInt(int):\n"
            "        def __int__(self): return 99\n"
            "    raise SystemExit({'typed-exit-false':False,'typed-exit-true':True,"
            "'typed-exit-subzero':ExitInt(0),'typed-exit-subseven':ExitInt(7)}[kind])\n"
            "if kind=='contended-report':\n"
            "    import threading,time\n"
            "    from _command_evidence.single_instance_lease.operation import OperationLock\n"
            "    recorder=sys._command_error_recorder\n"
            "    assert recorder.record('seed-error','Synthetic setup',exit_code=0)\n"
            "    ready=threading.Event()\n"
            "    def hold():\n"
            "        lock=OperationLock(recorder.directory/'.command-errors.lock')\n"
            "        lock.acquire()\n"
            "        try:\n"
            "            ready.set()\n"
            "            time.sleep(0.25)\n"
            "        finally: lock.release()\n"
            "    thread=threading.Thread(target=hold)\n"
            "    thread.start()\n"
            "    try:\n"
            "        assert ready.wait(5)\n"
            "        assert recorder.record('contended-error','Contended failure',exit_code=0)\n"
            "    finally:\n"
            "        thread.join(5)\n"
            "        assert not thread.is_alive()\n"
            "if kind=='threaded-results':\n"
            "    import threading,time\n"
            "    from concurrent.futures import ThreadPoolExecutor\n"
            "    from unittest.mock import patch\n"
            "    recorder=sys._command_error_recorder\n"
            "    assert recorder.record('seed-error','Synthetic setup',exit_code=0)\n"
            "    store=recorder._store\n"
            "    failed=threading.Event()\n"
            "    following=threading.Event()\n"
            "    def write(event):\n"
            "        if event['code']=='failed-error':\n"
            "            store.last_error='OSError'\n"
            "            failed.set()\n"
            "            assert following.wait(5)\n"
            "            time.sleep(0.1)\n"
            "        else: store.last_error=None\n"
            "    def next_call():\n"
            "        following.set()\n"
            "        return recorder.record('next-error','Synthetic success',exit_code=0)\n"
            "    with patch.object(store,'write_event',write),ThreadPoolExecutor(2) as pool:\n"
            "        first=pool.submit(recorder.record,'failed-error','Synthetic failure')\n"
            "        assert failed.wait(5)\n"
            "        second=pool.submit(next_call)\n"
            "        assert first.result(5) is False\n"
            "        assert second.result(5) is True\n"
            "if kind=='forked-report':\n"
            "    import threading,signal\n"
            "    recorder=sys._command_error_recorder\n"
            "    assert recorder.record('seed-error','Synthetic setup',exit_code=0)\n"
            "    ready=threading.Event()\n"
            "    release=threading.Event()\n"
            "    def hold():\n"
            "        with recorder._process_lock():\n"
            "            ready.set()\n"
            "            assert release.wait(10)\n"
            "    thread=threading.Thread(target=hold)\n"
            "    thread.start()\n"
            "    assert ready.wait(5)\n"
            "    child=os.fork()\n"
            "    if child==0:\n"
            "        signal.alarm(3)\n"
            "        result=recorder.record('fork-error','Synthetic child failure',exit_code=0)\n"
            "        recorder.maintain()\n"
            "        os._exit(0 if result else 1)\n"
            "    try:\n"
            "        _,status=os.waitpid(child,0)\n"
            "    finally:\n"
            "        release.set()\n"
            "        thread.join(5)\n"
            "    assert status==0 and not thread.is_alive()\n"
        ))
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COMMAND_TEST_TOKEN": "secret-environment-content",
    }
    return archive, environment


def _run(
    payload: tuple[Path, dict[str, str]], *arguments: str, data: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    archive, environment = payload
    return subprocess.run(
        [sys.executable, "-I", "-B", str(archive), *(
            "_command_evidence.fixture" if value == "fixture" else value
            for value in arguments
        )],
        env=environment, input=data, capture_output=True, timeout=15,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )


def _recent(payload: tuple[Path, dict[str, str]]) -> dict[str, Any]:
    completed = _run(payload, "recent")
    assert completed.returncode == 0, completed.stderr
    return cast(dict[str, Any], json.loads(completed.stdout))


def test_fresh_reader_missing_storage_is_read_only(payload: tuple[Path, dict[str, str]]) -> None:
    result = _recent(payload)
    assert result["exists"] is False
    assert result["records"] == [] and result["unavailable"] == []
    assert not (Path(payload[1]["HOME"]) / ".copilot-extensions").exists()


def test_native_streams_result_and_private_inputs(
    payload: tuple[Path, dict[str, str]],
) -> None:
    data = b"\x00\xffprivate stdin\r\n" + "雪".encode()
    result = _run(
        payload, "run", "fixture-command", "fixture-source", "fixture", "--",
        "streams", "private-argument-content", data=data,
    )
    assert result.returncode == 37
    assert result.stdout == data and result.stderr == b"ordinary stderr\n"
    evidence = _recent(payload)
    assert evidence["unavailable"] == [], evidence
    record, = evidence["records"]
    assert record["exit_code"] == 37 and record["code"] == "nonzero-exit"
    serialized = json.dumps(evidence)
    for private in ("private stdin", "private-argument-content", "secret-environment-content"):
        assert private not in serialized
    assert record["command"] == "fixture-command"
    assert record["source"] == "fixture-source"


def test_unshaped_exception_values_are_not_archived(
    payload: tuple[Path, dict[str, str]],
) -> None:
    result = _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", "exception")
    assert result.returncode == 1
    assert b"private request content" in result.stderr
    record, = _recent(payload)["records"]
    assert record["message"] == "Uncaught ValueError"
    assert record["completeness"] == "metadata-only"
    assert record["exit_code"] is None
    assert "private request" not in json.dumps(record)


def test_interruption_preserves_native_result_without_inventing_a_final_code(
    payload: tuple[Path, dict[str, str]],
) -> None:
    baseline = subprocess.run(
        [sys.executable, "-I", "-B", "-c", "raise KeyboardInterrupt"],
        env=payload[1], capture_output=True, timeout=15,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    result = _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", "interrupt")
    assert result.returncode == baseline.returncode
    record, = _recent(payload)["records"]
    assert record["code"] == "interrupted"
    assert record["exit_code"] is None and record["completeness"] == "incomplete"


def test_explicit_error_at_exit_zero_and_redaction(
    payload: tuple[Path, dict[str, str]],
) -> None:
    for action in ("reported", "redaction"):
        result = _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", action)
        assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""
    records = _recent(payload)["records"]
    assert len(records) == 2
    assert all(record["exit_code"] == 0 for record in records)
    assert any(record["message"] == "Dependency selection failed" for record in records)
    assert "credential-example" not in json.dumps(records)


def test_public_recorder_remains_available_to_explicit_exit_hook(
    payload: tuple[Path, dict[str, str]],
) -> None:
    result = _run(
        payload, "run", "fixture-command", "fixture-source", "fixture", "--", "shutdown-report",
    )
    assert result.returncode == 0 and not result.stdout and not result.stderr
    record, = _recent(payload)["records"]
    assert record["code"] == "shutdown-error" and record["exit_code"] == 0


def test_large_active_tail_applies_line_bound_newest_first(
    payload: tuple[Path, dict[str, str]],
) -> None:
    command = ("run", "fixture-command", "fixture-source", "fixture", "--")
    assert _run(payload, *command, "reported").returncode == 0
    evidence = _recent(payload)
    seed = evidence["records"][0]
    now = datetime.now(timezone.utc)
    lines = [
        json.dumps({**seed, "ts": (now + timedelta(microseconds=index)).isoformat(),
                    "message": f"safe event {index}"})
        for index in range(5000)
    ]
    (Path(evidence["directory"]) / "command-errors.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8",
    )
    result = _run(payload, *command, "wide-reader")
    assert result.returncode == 0 and not result.stderr
    recent = json.loads(result.stdout)
    assert recent["truncated"] is True
    assert recent["records"][0]["message"] == "safe event 4999"


def test_unavailable_store_preserves_primary_result(
    payload: tuple[Path, dict[str, str]],
) -> None:
    (Path(payload[1]["HOME"]) / ".copilot-extensions").write_text("not a directory")
    result = _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", "streams")
    assert result.returncode == 37
    assert b"command evidence unavailable" in result.stderr
    assert b"ordinary stderr" in result.stderr


def test_recorder_initialization_failure_does_not_prevent_application_entry(
    payload: tuple[Path, dict[str, str]],
) -> None:
    archive = payload[0]
    with zipfile.ZipFile(archive) as source:
        contents = [(info, source.read(info.filename)) for info in source.infolist()]
    replacement = (
        "from _command_evidence.process_telemetry import command_entry\n"
        "def unavailable(*args,**kwargs):\n"
        "    raise RuntimeError('private initialization cause')\n"
        "command_entry.CommandErrorRecorder=unavailable\n"
        "command_entry.main()\n"
    ).encode()
    with zipfile.ZipFile(archive, "w") as target:
        for info, data in contents:
            target.writestr(info, replacement if info.filename == "__main__.py" else data)
    data = b"unchanged binary stdin\x00\xff"
    result = _run(
        payload, "run", "fixture-command", "fixture-source", "fixture", "--", "streams", data=data,
    )
    assert result.returncode == 37 and result.stdout == data
    assert b"initialization unavailable (RuntimeError)" in result.stderr
    assert b"ordinary stderr" in result.stderr
    assert b"private initialization cause" not in result.stderr
    assert _recent(payload)["records"] == []


def test_concurrent_process_records_remain_complete(
    payload: tuple[Path, dict[str, str]],
) -> None:
    def report(_index: int) -> subprocess.CompletedProcess[bytes]:
        return _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", "reported")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(report, range(4)))
    assert all(result.returncode == 0 and not result.stderr for result in results), [
        (result.returncode, result.stderr) for result in results
    ]
    evidence = _recent(payload)
    assert evidence["unavailable"] == []
    assert len(evidence["records"]) == 4
    assert len({record["correlation_id"] for record in evidence["records"]}) == 4


def test_native_writer_waits_for_existing_short_operation_custody(
    payload: tuple[Path, dict[str, str]],
) -> None:
    result = _run(
        payload, "run", "fixture-command", "fixture-source", "fixture", "--", "contended-report",
    )
    assert result.returncode == 0 and not result.stderr
    evidence = _recent(payload)
    assert evidence["unavailable"] == []
    assert {record["code"] for record in evidence["records"]} == {"seed-error", "contended-error"}


def test_interrupted_tail_is_preserved_and_next_error_discoverable(
    payload: tuple[Path, dict[str, str]],
) -> None:
    assert _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", "reported").returncode == 0
    directory = Path(_recent(payload)["directory"])
    with (directory / "command-errors.jsonl").open("ab") as stream:
        stream.write(b'{"interrupted":')
    assert _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", "reported").returncode == 0
    evidence = _recent(payload)
    assert len(evidence["records"]) == 2
    assert any(item["reason"] == "invalid-record" for item in evidence["unavailable"])
    assert len(list(directory.glob("command-errors.*.jsonl"))) == 1


def test_current_errors_remain_discoverable_when_inventory_is_truncated(
    payload: tuple[Path, dict[str, str]],
) -> None:
    command = ("run", "fixture-command", "fixture-source", "fixture", "--", "reported")
    assert _run(payload, *command).returncode == 0
    directory = Path(_recent(payload)["directory"])
    (directory / "command-errors.jsonl").unlink()
    for index in range(260):
        (directory / f"unrelated-{index}").write_bytes(b"")
    assert _run(payload, *command).returncode == 0
    evidence = _recent(payload)
    assert evidence["truncated"] is True
    assert len(evidence["records"]) == 1


def test_fresh_entry_maintenance_prunes_mixed_records_and_preserves_private_write(
    payload: tuple[Path, dict[str, str]],
) -> None:
    command = ("run", "fixture-command", "fixture-source", "fixture", "--")
    assert _run(payload, *command, "reported").returncode == 0
    before = _recent(payload)
    directory = Path(before["directory"])
    active = directory / "command-errors.jsonl"
    live = before["records"][0]
    expired = {**live, "ts": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
               "message": "expired cause"}
    active.write_text(json.dumps(expired) + "\n" + json.dumps(live) + "\n", encoding="utf-8")
    result = _run(payload, *command, "maintenance")
    assert result.returncode == 0 and not result.stderr
    result = _run(payload, *command, "reported")
    assert result.returncode == 0 and not result.stderr
    evidence = _recent(payload)
    assert evidence["unavailable"] == []
    assert len(evidence["records"]) == 2
    assert "expired cause" not in json.dumps(evidence)
    if os.name != "nt":
        assert active.stat().st_mode & 0o777 == 0o600


def test_successful_fresh_entry_performs_due_retention_without_an_error_report(
    payload: tuple[Path, dict[str, str]],
) -> None:
    command = ("run", "fixture-command", "fixture-source", "fixture", "--")
    assert _run(payload, *command, "reported").returncode == 0
    before = _recent(payload)
    directory = Path(before["directory"])
    active = directory / "command-errors.jsonl"
    expired = {**before["records"][0],
               "ts": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()}
    active.write_text(json.dumps(expired) + "\n", encoding="utf-8")
    marker = directory / ".command-errors-maintenance"
    if marker.exists():
        marker.unlink()
    result = _run(payload, *command, "no-error")
    assert result.returncode == 0 and not result.stderr
    assert _recent(payload)["records"] == []


def test_due_maintenance_visits_one_segment_per_tick_and_wraps(
    payload: tuple[Path, dict[str, str]],
) -> None:
    command = ("run", "fixture-command", "fixture-source", "fixture", "--")
    assert _run(payload, *command, "reported").returncode == 0
    before = _recent(payload)
    directory = Path(before["directory"])
    expired = {**before["records"][0],
               "ts": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()}
    for index in range(2):
        segment = directory / f"command-errors.20200101T000000000000Z-{index:032x}.jsonl"
        segment.write_text(json.dumps(expired) + "\n", encoding="utf-8")
    marker = directory / ".command-errors-maintenance"
    for remaining in (1, 0):
        if marker.exists():
            os.utime(marker, (0, 0))
        result = _run(payload, *command, "no-error")
        assert result.returncode == 0 and not result.stderr
        assert len(list(directory.glob("command-errors.*.jsonl"))) == remaining
        assert len(_recent(payload)["records"]) == remaining + 1
    for _ in range(2):
        os.utime(marker, (0, 0))
        result = _run(payload, *command, "no-error")
        assert result.returncode == 0 and not result.stderr
    assert len(_recent(payload)["records"]) == 1


@pytest.mark.parametrize("overflow", ["segment", "inventory"])
def test_startup_maintenance_bounds_preserve_evidence_and_success(
    payload: tuple[Path, dict[str, str]], overflow: str,
) -> None:
    command = ("run", "fixture-command", "fixture-source", "fixture", "--")
    assert _run(payload, *command, "reported").returncode == 0
    before = _recent(payload)
    directory = Path(before["directory"])
    active = directory / "command-errors.jsonl"
    if overflow == "segment":
        with active.open("ab") as target:
            target.truncate(5_000_000 + 65537)
    else:
        for index in range(260):
            (directory / f"unrelated-{index}").write_bytes(b"")
    size = active.stat().st_size
    result = _run(payload, *command, "no-error")
    assert result.returncode == 0
    assert b"OverflowError" in result.stderr
    assert active.stat().st_size == size


def test_reader_unicode_json_survives_ascii_stdout(
    payload: tuple[Path, dict[str, str]],
) -> None:
    result = _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", "unicode")
    assert result.returncode == 0 and not result.stderr
    archive, environment = payload
    result = subprocess.run(
        [sys.executable, "-B", str(archive), "recent"],
        env={**environment, "PYTHONIOENCODING": "ascii"}, capture_output=True, timeout=15,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert result.returncode == 0 and not result.stderr
    evidence = json.loads(result.stdout.decode("ascii"))
    assert evidence["records"][0]["message"] == "Synthetic Unicode cause: 雪"


def test_shared_recorder_keeps_failed_and_successful_thread_results_separate(
    payload: tuple[Path, dict[str, str]],
) -> None:
    result = _run(
        payload, "run", "fixture-command", "fixture-source", "fixture", "--", "threaded-results",
    )
    assert result.returncode == 0 and not result.stderr


@pytest.mark.skipif(not hasattr(os, "fork"), reason="native fork semantics require POSIX")
def test_forked_child_does_not_inherit_other_threads_recorder_lock(
    payload: tuple[Path, dict[str, str]],
) -> None:
    result = _run(
        payload, "run", "fixture-command", "fixture-source", "fixture", "--", "forked-report",
    )
    assert result.returncode == 0, result.stderr
    records = _recent(payload)["records"]
    assert sorted(record["code"] for record in records) == ["fork-error", "seed-error"]
    assert len({record["pid"] for record in records}) == 2


def test_archive_is_deterministic_private_and_does_not_shadow_runtime_packages() -> None:
    data = producer.archive(ROOT)
    assert data == producer.archive(ROOT)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "process_telemetry/__init__.py" not in archive.namelist()
        assert "agent_procutil/__init__.py" not in archive.namelist()
        assert all(
            name == "__main__.py" or name.startswith("_command_evidence/")
            for name in archive.namelist()
        )


def test_archive_bytes_do_not_depend_on_the_producer_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as context:
        context.setattr(sys, "platform", "win32")
        windows = producer.archive(ROOT)
        context.setattr(sys, "platform", "linux")
        posix = producer.archive(ROOT)
    assert windows == posix


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("relative", [False, True])
def test_script_entry_preserves_direct_script_import_argv_and_cwd(
    payload: tuple[Path, dict[str, str]], tmp_path: Path, isolated: bool, relative: bool,
) -> None:
    directory = tmp_path / "script directory"
    directory.mkdir()
    script = directory / "hyphenated-tool.py"
    script.write_text(
        "import atexit,importlib.util,json,os,sys\n"
        "initial_main = sys.modules['__main__']\n"
        "atexit.register(lambda: print('same main',sys.modules['__main__'] is initial_main))\n"
        "def annotated(value:int)->list[str]: return []\n"
        "print(json.dumps([__name__,__file__,sys.argv,os.getcwd(),"
        "importlib.util.find_spec('sibling') is not None,"
        "repr(annotated.__annotations__)]))\n",
    )
    (directory / "sibling.py").write_text("value = 1\n")
    target = str(script.relative_to(tmp_path)) if relative else str(script)
    prefix = [sys.executable, "-B", *(["-I"] if isolated else [])]
    def invoke(arguments: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            arguments, env=payload[1], cwd=tmp_path, capture_output=True, timeout=15,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )

    direct = invoke([*prefix, target, "", "quote'\"雪"])
    wrapped = invoke([
        *prefix, str(payload[0]), "run-script", "fixture-command", "fixture-source",
        target, "--", "", "quote'\"雪",
    ])
    assert direct.returncode == wrapped.returncode == 0
    assert direct.stdout == wrapped.stdout
    assert direct.stderr == wrapped.stderr == b""


def test_missing_script_preserves_python_exit_and_excludes_path_from_evidence(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    missing = tmp_path / "private-script-name.py"
    result = _run(
        payload, "run-script", "fixture-command", "fixture-source", str(missing), "--",
    )
    assert result.returncode == 2 and not result.stdout
    assert b"can't open file" in result.stderr
    evidence = _recent(payload)
    assert any(record["code"] == "script-open-failed" for record in evidence["records"])
    assert "private-script-name" not in json.dumps(evidence)


@pytest.mark.parametrize(("kind", "code"), [
    ("typed-exit-false", 0), ("typed-exit-true", 1),
    ("typed-exit-subzero", 0), ("typed-exit-subseven", 7),
])
def test_exit_metadata_matches_boolean_and_integer_subclass_results(
    payload: tuple[Path, dict[str, str]], kind: str, code: int,
) -> None:
    result = _run(payload, "run", "fixture-command", "fixture-source", "fixture", "--", kind)
    assert result.returncode == code
    records = _recent(payload)["records"]
    if code:
        record, = records
        assert record["exit_code"] == code
    else:
        assert records == []


def test_symlink_script_preserves_sibling_imports(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    script = actual / "tool.py"
    script.write_text("import sibling;print(sibling.value)\n", encoding="utf-8")
    (actual / "sibling.py").write_text("value = 42\n", encoding="utf-8")
    link = tmp_path / "alias.py"
    try:
        link.symlink_to(script)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("native file symlink creation requires Windows privilege")
        raise
    prefix = [sys.executable, "-B"]
    commands = [
        [*prefix, str(link)],
        [*prefix, str(payload[0]), "run-script", "fixture-command", "fixture-source", str(link), "--"],
    ]
    results = [
        subprocess.run(
            command, env=payload[1], cwd=tmp_path, capture_output=True, timeout=15,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        for command in commands
    ]
    assert [result.returncode for result in results] == [0, 0]
    assert results[0].stdout == results[1].stdout
    assert results[0].stderr == results[1].stderr == b""


@pytest.mark.parametrize("host_name", ["pwsh", "powershell.exe", "sh"])
@pytest.mark.parametrize("evidence_present", [True, False])
def test_actual_generated_shim_uses_same_interpreter_evidence(
    payload: tuple[Path, dict[str, str]], tmp_path: Path, host_name: str, evidence_present: bool,
) -> None:
    if host_name == "sh" and os.name == "nt":
        pytest.skip("POSIX launch qualified in the native POSIX packet")
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} is unavailable")
    from test_generate import _manifest, generator

    (tmp_path / "generated").mkdir()
    manifest = _manifest(tmp_path / "generated")
    declaration = json.loads(manifest.read_text())
    declaration["module"] = "fixture_application"
    manifest.write_text(json.dumps(declaration))
    (manifest.parent / "plugin.json").write_text('{"name":"agent-example"}')
    assert generator.process_manifest(manifest, check=False) == []
    scripts = manifest.parent / "scripts"
    shutil.copyfile(payload[0], scripts / "command-errors.pyz")
    if not evidence_present:
        (scripts / "command-errors.pyz").unlink()
    (tmp_path / "fixture_application.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "with Path(__file__).with_name('application.calls').open('ab') as calls: calls.write(b'x')\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
        "sys.stderr.buffer.write(b'ordinary stderr\\n')\n"
        "raise SystemExit(37)\n",
        encoding="utf-8",
    )
    if host_name == "sh":
        import shlex

        (scripts / "resolve-runtime.sh").write_text(f"AGENT_RT_PY={shlex.quote(sys.executable)}\n")
        command = [host, str(manifest.parent / "bin/agent-example"), "streams"]
    else:
        (scripts / "resolve-runtime.ps1").write_text(
            "$AgentRtPy = '" + sys.executable.replace("'", "''") + "'\n",
        )
        command = [host, "-NoProfile", "-File", str(manifest.parent / "bin/agent-example.ps1"), "streams"]
    result = subprocess.run(
        command, input=b"progressive output\n", capture_output=True,
        env=payload[1], cwd=tmp_path, timeout=20,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert result.returncode == 37, result.stderr
    assert b"progressive output" in result.stdout
    assert b"ordinary stderr" in result.stderr
    assert (tmp_path / "application.calls").read_bytes() == b"x"
    records = _recent(payload)["records"]
    if evidence_present:
        record, = records
        assert record["command"] == "agent-example"
        assert record["exit_code"] == 37
    else:
        assert records == []
        assert b"evidence support unavailable" in result.stderr
