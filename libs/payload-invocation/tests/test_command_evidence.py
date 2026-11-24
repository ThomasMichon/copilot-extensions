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
            "if kind=='reported':\n"
            "    sys._command_error_recorder.record('selection-failed',"
            " 'Dependency selection failed', exit_code=0)\n"
            "if kind=='redaction':\n"
            "    sys._command_error_recorder.record('safe-error',"
            " 'password=credential-example', exit_code=0)\n"
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
    assert "private request" not in json.dumps(record)


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
def test_actual_generated_shim_uses_same_interpreter_evidence(
    payload: tuple[Path, dict[str, str]], tmp_path: Path, host_name: str,
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
    declaration["module"] = "_command_evidence.fixture"
    manifest.write_text(json.dumps(declaration))
    (manifest.parent / "plugin.json").write_text('{"name":"agent-example"}')
    assert generator.process_manifest(manifest, check=False) == []
    scripts = manifest.parent / "scripts"
    shutil.copyfile(payload[0], scripts / "command-errors.pyz")
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
        env=payload[1], timeout=20,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert result.returncode == 37, result.stderr
    assert b"progressive output" in result.stdout
    assert b"ordinary stderr" in result.stderr
    record, = _recent(payload)["records"]
    assert record["command"] == "agent-example"
    assert record["exit_code"] == 37
