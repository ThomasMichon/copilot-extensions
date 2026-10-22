"""Owner failures reach normal Bridge logging, not only returned exceptions."""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_bridge import acquisition_authority as authority
from agent_bridge.acquisition_contract import AcquisitionRefused
from test_acquisition_authority import owner_process as owner_process

pytestmark = pytest.mark.contract("agent_bridge.acquisition.worktree_owner")


@pytest.fixture
def durable_log(tmp_path, monkeypatch, request):
    path = tmp_path / "agent-bridge-err.log"
    handler = logging.FileHandler(
        path, encoding=getattr(request, "param", "utf-8"), errors="backslashreplace",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logger = logging.getLogger("agent-bridge")
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "level", logging.INFO)
    monkeypatch.setattr(logger, "disabled", False)
    try:
        yield path
    finally:
        handler.close()


@pytest.mark.asyncio
async def test_owner_failure_is_on_disk_after_caller_returns(owner_process, durable_log):
    owner_process(
        json.dumps({"error": "synthetic owner rejection", "stage": "bind", "code": "denied"}).encode(),
        b"optional owner warning",
    )
    with pytest.raises(AcquisitionRefused):
        await authority.worktree_command(["bind-session"], "project-a")
    text = durable_log.read_text(encoding="utf-8")
    assert "synthetic owner rejection" in text
    assert "optional owner warning" in text
    assert "exit_code" in text


def failure_record(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert len(lines[0].encode("utf-8")) < 8192
    record = json.loads(lines[0].split("worktree_owner_failure ", 1)[1])
    assert len(record["correlation_id"]) == 32
    assert int(record["correlation_id"], 16) >= 0
    assert record["duration_ms"] >= 0
    assert set(record) == {
        "operation", "correlation_id", "reason", "exit_code", "duration_ms",
        "capture_complete", "stdout_bytes", "stderr_bytes", "owner_error", "stderr",
    }
    return record


@pytest.mark.asyncio
@pytest.mark.parametrize("stdout", [
    b"opaque never-echo-opaque",
    b'{"error": "never-echo-malformed"',
    b'["never-echo-array"]',
    b'{"environment": {"TOKEN": "never-echo-environment"}}',
    json.dumps({"error": "never-echo-oversized" + "x" * 65536}).encode(),
    b"\xff\xfeopaque",
], ids=["opaque", "malformed", "nonobject", "envelope", "oversized", "invalid-utf8"])
async def test_durable_log_does_not_echo_unstructured_stdout(owner_process, durable_log, stdout):
    owner_process(stdout, b"safe warning")
    with pytest.raises(AcquisitionRefused):
        await authority.worktree_command(["record", "--private-option", "never-echo-argument"], "project-a")
    record = failure_record(durable_log)
    assert record["owner_error"] is None and record["stderr"] == "safe warning"
    assert record["stdout_bytes"] == len(stdout) and record["stderr_bytes"] == 12
    assert record["capture_complete"] is True and record["exit_code"] == 7
    assert "never-echo" not in durable_log.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_durable_log_redacts_before_bounds_and_separates_stderr(owner_process, durable_log):
    secret = "synthetic-sensitive-" + "s" * 900
    owner_process(
        json.dumps({"error": {
            "message": "safe failure " + secret,
            "stage": "b" * 200, "code": "c" * 200,
            "environment": {"TOKEN": "never-echo-environment"},
        }}).encode(),
        ("\x1b[31mwarning " + secret + " cookie=never-echo-cookie\x1b[0m\r\n").encode(),
        environment={"DIAGNOSTIC_TEST_TOKEN": secret},
    )
    with pytest.raises(AcquisitionRefused):
        await authority.worktree_command(["record"], "project-a")
    record = failure_record(durable_log)
    assert "safe failure [REDACTED]" in record["owner_error"]
    assert "stage=" in record["owner_error"] and "code=" in record["owner_error"]
    assert "warning [REDACTED]" in record["stderr"]
    text = durable_log.read_text(encoding="utf-8")
    assert not any(value in text for value in (
        secret, "s" * 32, "never-echo-environment", "never-echo-cookie", "\x1b", "\r",
    ))


@pytest.mark.asyncio
async def test_durable_log_bounds_wide_unicode_fields(owner_process, durable_log):
    owner_process(
        json.dumps({"error": "😀" * 2000, "stage": "😀" * 200, "code": "😀" * 200}).encode(),
        ("😀" * 2000).encode(),
    )
    with pytest.raises(AcquisitionRefused):
        await authority.worktree_command(["record"], "project-a")
    record = failure_record(durable_log)
    assert len(record["owner_error"]) < 1000 and len(record["stderr"]) <= 403
    assert len(json.dumps(record["owner_error"], ensure_ascii=True)) <= 4608
    assert len(json.dumps(record["stderr"], ensure_ascii=True)) <= 2048
    assert "stage=" in record["owner_error"] and "code=" in record["owner_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("durable_log", ["cp1252", "ascii"], indirect=True, ids=["cp1252", "ascii"])
async def test_legacy_sink_preserves_json_and_encoded_record_bound(owner_process, durable_log):
    owner_process(
        json.dumps({"error": "owner é " + "😀" * 2000, "stage": "bind", "code": "denied"}).encode(),
        ("warning é " + "😀" * 2000).encode(),
    )
    with pytest.raises(AcquisitionRefused):
        await authority.worktree_command(["record"], "project-a")
    raw = durable_log.read_bytes()
    encoding = logging.getLogger("agent-bridge").handlers[0].encoding
    try:
        record = json.loads(raw.decode(encoding).split("worktree_owner_failure ", 1)[1])
    except json.JSONDecodeError:
        record = None
    observations = {"valid_json": isinstance(record, dict), "bytes": len(raw), "ascii_only": raw.isascii()}
    assert observations["valid_json"] and observations["bytes"] < 8192 and observations["ascii_only"], observations
    assert "owner é " in record["owner_error"]
    assert "stage=bind" in record["owner_error"] and "code=denied" in record["owner_error"]
    assert "warning é " in record["stderr"] and record["exit_code"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("args,payload,exit_code,code", [
    (["create", "--bridge-plugin-request", "request.json", "--worktree-id", "worktree-a"],
     {"plugin_preparation_version": 1, "native_launch_started": False,
      "worktree_id": "worktree-a", "error": "synthetic preparation refused"},
     1, "plugin_preparation_failed"),
    (["create", "--creation-key", "never-echo-creation-key"],
     {"creation": {"version": 1, "status": "pending"}}, 3, "owner_operation_pending"),
    (["bind-session", "--session-id", "native-a", "--worktree-id", "worktree-a"],
     {"bound": False}, 0, "native_binding_unproven"),
    (["record"], [], 0, "owner_response_invalid"),
    (["record"], b"opaque invalid json", 0, "owner_response_invalid"),
])
async def test_durable_log_preserves_typed_failure_and_head_checks(
    owner_process, durable_log, args, payload, exit_code, code,
):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    owner_process(raw, b"optional warning", returncode=exit_code)
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(args, "project-a")
    assert refused.value.code == code and refused.value.effect is None
    record = failure_record(durable_log)
    assert record["reason"] == code and record["exit_code"] == exit_code
    assert record["capture_complete"] is True and record["stderr"] == "optional warning"
    text = durable_log.read_text(encoding="utf-8")
    assert "never-echo-creation-key" not in text and "native-a" not in text
    assert "worktree-a" not in text and "request.json" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("error,reason,observed_exit", [
    (TimeoutError, "owner_operation_pending", None),
    (TimeoutError, "owner_operation_pending", 0),
    (asyncio.CancelledError, "capture_cancelled", None),
    (OSError, "capture_failed", None),
])
async def test_durable_log_unconfirmed_capture_keeps_uncertainty(
    owner_process, durable_log, error, reason, observed_exit,
):
    process, factory = owner_process(returncode=observed_exit)
    process.communicate.side_effect = error("never-echo-unavailable-capture")
    expected = AcquisitionRefused if error is TimeoutError else error
    with pytest.raises(expected) as refused:
        await authority.worktree_command(["bind-session"], "project-a")
    if error is TimeoutError:
        assert refused.value.code == "owner_operation_pending" and refused.value.effect is None
    record = failure_record(durable_log)
    assert record["reason"] == reason and record["capture_complete"] is False
    assert record["exit_code"] is observed_exit
    assert all(record[key] is None for key in ("stdout_bytes", "stderr_bytes", "owner_error", "stderr"))
    assert "never-echo-unavailable-capture" not in durable_log.read_text(encoding="utf-8")
    process.communicate.assert_awaited_once()
    factory.assert_awaited_once()


@pytest.mark.asyncio
async def test_durable_log_does_not_echo_unknown_operation(owner_process, durable_log):
    owner_process(b'{"error":"safe rejection"}')
    with pytest.raises(AcquisitionRefused):
        await authority.worktree_command(["never-echo-operation"], "never-echo-project")
    assert failure_record(durable_log)["operation"] == "other"
    assert "never-echo" not in durable_log.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_success_has_no_failure_or_stdout_dump(owner_process, durable_log):
    payload = {"ok": True, "environment": {"TOKEN": "never-echo-success"}}
    owner_process(json.dumps(payload).encode(), b"never-echo-success-stderr", returncode=0)
    assert await authority.worktree_command(["record"], "project-a") == payload
    assert durable_log.read_bytes() == b""


@pytest.mark.asyncio
async def test_sink_failure_uses_existing_handler_error_handling(owner_process, durable_log, monkeypatch):
    owner_process(b'{"error":"safe rejection"}')
    handler = logging.getLogger("agent-bridge").handlers[0]
    with monkeypatch.context() as patch:
        handling = Mock()
        patch.setattr(handler, "handleError", handling)
        patch.setattr(handler, "stream", SimpleNamespace(write=Mock(side_effect=OSError("synthetic full sink"))))
        with pytest.raises(AcquisitionRefused) as refused:
            await authority.worktree_command(["record"], "project-a")
        assert refused.value.code == "owner_refused" and refused.value.effect is None
        handling.assert_called_once()
    assert durable_log.read_bytes() == b""


@pytest.mark.asyncio
async def test_real_child_failure_is_read_from_disk_by_fresh_process(monkeypatch, tmp_path, durable_log):
    script = tmp_path / "synthetic_owner.py"
    script.write_text(
        "import json, sys\n"
        "print(json.dumps({'error': {'message': 'synthetic durable rejection "
        "password=never-echo-password', 'stage': 'bind', 'code': 'denied'}, "
        "'environment': {'TOKEN': 'never-echo-environment'}}))\n"
        "sys.stderr.write('\\x1b[31msynthetic warning cookie=never-echo-cookie\\x1b[0m\\n')\n"
        "raise SystemExit(7)\n", encoding="utf-8",
    )
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix",
                        lambda: [sys.executable, "-I", str(script)])
    monkeypatch.setattr(authority, "_agent_worktrees_environment", lambda: os.environ.copy())
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(["bind-session"], "project-a")
    assert refused.value.code == "owner_refused" and refused.value.effect is None
    reader = run_caller_owned([
        sys.executable, "-I", "-c",
        "import hashlib,json,os,pathlib,sys;"
        "data=pathlib.Path(sys.argv[1]).read_bytes();"
        "print(json.dumps({'reader_pid':os.getpid(),"
        "'sha256':hashlib.sha256(data).hexdigest(),'text':data.decode('utf-8')}))",
        str(durable_log),
    ], timeout=10, max_stdout_bytes=32768, max_stderr_bytes=4096)
    assert reader.returncode == 0 and not reader.stderr
    proof = json.loads(reader.stdout)
    assert proof["reader_pid"] != os.getpid()
    record = json.loads(proof["text"].split("worktree_owner_failure ", 1)[1])
    assert record["exit_code"] == 7 and record["capture_complete"] is True
    assert record["owner_error"] == "synthetic durable rejection password=[REDACTED] (stage=bind, code=denied)"
    assert record["stderr"] == "synthetic warning cookie=[REDACTED]"
    assert "never-echo" not in proof["text"] and "\x1b" not in proof["text"]
    if report := os.environ.get("DIAGNOSTIC_LOG_PROOF_REPORT"):
        Path(report).write_text(json.dumps({
            **proof, "caller_pid": os.getpid(), "caller_returned_before_read": True,
            "log_source": str(authority.__file__), "shared_service_used": False,
        }, indent=2), encoding="utf-8")


@pytest.mark.parametrize("encoding", ["utf-8", "cp1252"], ids=["utf8", "legacy-cp1252"])
def test_default_cli_logging_reaches_redirected_service_file(tmp_path, encoding):
    log_path = tmp_path / "agent-bridge-err.log"
    owner = tmp_path / "synthetic_owner.py"
    message = "synthetic default-logging rejection"
    if encoding == "cp1252":
        message += " é " + "😀" * 2000
    owner.write_text(
        "import json,sys\n"
        f"print(json.dumps({{'error': {message!r}, 'stage': 'bind', 'code': 'denied'}}))\n"
        "sys.stderr.write('synthetic default-logging warning\\n')\n"
        "raise SystemExit(7)\n", encoding="utf-8",
    )
    producer = tmp_path / "synthetic_caller.py"
    producer.write_text(
        "import asyncio,contextlib,json,logging,os,pathlib,sys\n"
        "sys.path[:0]=json.loads(sys.argv[1])\n"
        "from agent_bridge import __main__ as cli,acquisition_authority as authority\n"
        "from agent_bridge.acquisition_contract import AcquisitionRefused\n"
        "assert pathlib.Path(authority.__file__).resolve()==pathlib.Path(sys.argv[4]).resolve()\n"
        "authority._agent_worktrees_launch_prefix=lambda:[sys.executable,'-I',sys.argv[3]]\n"
        "authority._agent_worktrees_environment=lambda:os.environ.copy()\n"
        "assert not logging.getLogger().handlers\n"
        "with open(sys.argv[2],'a',encoding=sys.argv[5],errors='backslashreplace') as output,contextlib.redirect_stderr(output):\n"
        " cli.main([])\n"
        " assert logging.getLogger().handlers[0].stream is output\n"
        " try: asyncio.run(authority.worktree_command(['bind-session'],'project-a'))\n"
        " except AcquisitionRefused as exc: assert exc.code=='owner_refused' and exc.effect is None\n"
        " else: raise AssertionError('synthetic owner must refuse')\n"
        " logging.shutdown()\n", encoding="utf-8",
    )
    result = run_caller_owned([
        sys.executable, "-I", str(producer), json.dumps(sys.path),
        str(log_path), str(owner), str(authority.__file__), encoding,
    ], timeout=15, max_stdout_bytes=65536, max_stderr_bytes=4096)
    assert result.returncode == 0 and not result.stderr
    reader = run_caller_owned([
        sys.executable, "-I", "-c",
        "import hashlib,json,os,pathlib,sys;"
        "data=pathlib.Path(sys.argv[1]).read_bytes();"
        "print(json.dumps({'reader_pid':os.getpid(),'sha256':hashlib.sha256(data).hexdigest(),"
        "'text':data.decode('utf-8')}))", str(log_path),
    ], timeout=10, max_stdout_bytes=32768, max_stderr_bytes=4096)
    assert reader.returncode == 0 and not reader.stderr
    proof = json.loads(reader.stdout)
    assert "synthetic default-logging rejection" in proof["text"]
    assert "synthetic default-logging warning" in proof["text"]
    record = failure_record(log_path)
    assert record["exit_code"] == 7
    assert "stage=bind" in record["owner_error"] and "code=denied" in record["owner_error"]
    assert log_path.read_bytes().isascii()
    if report := os.environ.get("DIAGNOSTIC_STDIO_PROOF_REPORT"):
        report_path = Path(report)
        report_path = report_path.with_stem(report_path.stem + "-" + encoding)
        report_path.write_text(json.dumps({
            **proof, "producer_returned_before_read": True, "production_cli_logging": True,
            "shared_service_used": False, "sink_encoding": encoding,
        }, indent=2), encoding="utf-8")
