from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_procutil import wsl


@pytest.mark.parametrize("argv", [[], "", [""], [1], ["ok", "\0"]])
def test_request_refuses_invalid_command(argv):
    with pytest.raises(ValueError):
        wsl._request(argv, 1, None, True)


@pytest.mark.parametrize("timeout", [True, 0, -1, float("inf"), float("nan")])
def test_request_refuses_invalid_deadline(timeout):
    with pytest.raises(ValueError):
        wsl._request(["synthetic.exe"], timeout, None, True)


def test_request_preserves_text_and_bounds_serialized_input():
    value = wsl._request(["synthetic.exe", "a b"], 1, "one\ntwo", True)
    assert value["argv"] == ["synthetic.exe", "a b"]
    assert value["input_text"] == "one\ntwo"
    with pytest.raises(ValueError, match="byte bound"):
        wsl._request(["synthetic.exe"], 1, "a" * wsl._MAX_REQUEST, True)


def test_framing_requires_complete_single_request(monkeypatch):
    monkeypatch.setattr(wsl.os, "read", lambda *_: b'{"version":1}\nextra')
    with pytest.raises(ValueError, match="framing"):
        wsl._read_request()
    monkeypatch.setattr(wsl.os, "read", lambda *_: b"")
    with pytest.raises(ValueError, match="disconnected"):
        wsl._read_request()


def test_framing_validates_schema_before_execution(monkeypatch):
    request = wsl._request(["synthetic.exe"], 1, None, True)
    monkeypatch.setattr(wsl.os, "read", lambda *_: json.dumps(request).encode() + b"\n")
    assert wsl._read_request() == request
    request["version"] = True
    with pytest.raises(ValueError, match="Unsupported"):
        wsl._read_request()


def test_non_wsl_refuses_before_launch(monkeypatch):
    monkeypatch.setattr(wsl.platform, "release", lambda: "portable-test-kernel")
    with pytest.raises(RuntimeError, match="requires WSL"):
        wsl.run_windows_owned(["synthetic.exe"], windows_python="pythonw.exe", timeout=1)


@pytest.mark.parametrize("body", [
    b"", b"{", b"[]", b'{"version":true}',
    b'{"version":1,"returncode":true,"stdout":"","stderr":""}',
    b'{"version":1,"returncode":0,"stdout":[],"stderr":""}',
    b'{"version":1,"returncode":0,"stdout":"","stderr":"","extra":0}',
])
def test_incomplete_or_malformed_completion_cannot_be_replayed(body):
    with pytest.raises(RuntimeError, match="do not replay"):
        wsl._completed(["synthetic.exe"], 1, body, 0)


def test_native_result_keeps_exit_status_and_streams_distinct():
    body = json.dumps({
        "version": 1, "returncode": 513, "stdout": "one", "stderr": "two",
    }).encode()
    result = wsl._completed(["synthetic.exe"], 1, body, 0)
    assert (result.returncode, result.stdout, result.stderr) == (513, "one", "two")
    with pytest.raises(RuntimeError, match="unconfirmed"):
        wsl._completed(["synthetic.exe"], 1, body, 125)


def test_timeout_and_refused_owner_are_errors_not_completed_commands():
    with pytest.raises(subprocess.TimeoutExpired):
        wsl._completed(["synthetic.exe"], 1, b'{"version":1,"error":"timeout"}', 0)
    with pytest.raises(RuntimeError, match="do not replay"):
        wsl._completed(["synthetic.exe"], 1, b'{"version":1,"error":"owner-job-unavailable"}', 1)


def test_owner_job_precedes_request_and_target(monkeypatch):
    import agent_procutil

    steps = []
    marker = object()

    def own(pid, *, allow_breakaway):
        assert pid == 123 and allow_breakaway is False
        steps.append("own")
        return marker

    def request():
        steps.append("request")
        return wsl._request(["synthetic.exe"], 1, None, True)

    def run(*args, **kwargs):
        assert steps == ["own", "request", "watch"]
        steps.append("target")
        return subprocess.CompletedProcess(args[0], 0, "", "")

    class Finished(Exception):
        pass

    monkeypatch.setattr(agent_procutil, "_assign_suspended_to_kill_on_close_job", own)
    monkeypatch.setattr(wsl, "os", SimpleNamespace(
        name="nt", getpid=lambda: 123, _exit=lambda code: (_ for _ in ()).throw(Finished(code)),
    ))
    monkeypatch.setattr(wsl, "_read_request", request)
    monkeypatch.setattr(wsl, "run_owned", run)
    monkeypatch.setattr(wsl, "_emit", lambda result: steps.append("result"))
    monkeypatch.setattr(wsl, "threading", SimpleNamespace(
        Thread=lambda **kwargs: SimpleNamespace(start=lambda: steps.append("watch")),
    ))
    with pytest.raises(Finished):
        wsl._windows_main()
    assert steps == ["own", "request", "watch", "target", "result"]


def test_failed_job_refuses_before_reading_or_running_a_command(monkeypatch):
    import agent_procutil

    events = []

    class Finished(Exception):
        pass

    monkeypatch.setattr(
        agent_procutil, "_assign_suspended_to_kill_on_close_job", lambda *a, **k: None,
    )
    monkeypatch.setattr(wsl, "os", SimpleNamespace(
        name="nt", getpid=lambda: 123, _exit=lambda code: (_ for _ in ()).throw(Finished(code)),
    ))
    monkeypatch.setattr(wsl, "_emit", events.append)
    monkeypatch.setattr(wsl, "_read_request", lambda: pytest.fail("command admitted"))
    with pytest.raises(Finished):
        wsl._windows_main()
    assert events == [{"version": 1, "error": "owner-job-unavailable"}]


@pytest.mark.skipif(os.name == "nt", reason="real POSIX selector pipes")
def test_exchange_keeps_lifetime_pipe_open_until_owner_exits():
    script = (
        "import os,json,select;"
        "request=os.read(0,4096);"
        "assert request.endswith(b'\\n');"
        "assert not select.select([0],[],[],.05)[0];"
        "os.write(1,b'{\"version\":1,\"returncode\":0,\"stdout\":\"ok\",\"stderr\":\"err\"}')"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        result = wsl._exchange(process, b"request\n", time.monotonic() + 3)
        assert json.loads(result)["stdout"] == "ok"
        assert process.wait(timeout=1) == 0
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX selector pipes")
def test_exchange_deadline_does_not_wait_for_owner_output():
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys;sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        with pytest.raises(TimeoutError):
            wsl._exchange(process, b"request\n", time.monotonic() + .05)
    finally:
        process.stdin.close()
        process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX selector pipes")
def test_exchange_response_bound_is_enforced_while_reading(monkeypatch):
    monkeypatch.setattr(wsl, "_MAX_RESPONSE", 16)
    process = subprocess.Popen(
        [sys.executable, "-c", "import os;os.read(0,4096);os.write(1,b'x'*17)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        with pytest.raises(RuntimeError, match="byte bound"):
            wsl._exchange(process, b"request\n", time.monotonic() + 2)
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
