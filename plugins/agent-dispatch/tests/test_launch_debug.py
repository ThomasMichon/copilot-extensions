"""Opt-in observation never becomes another Bridge launcher."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_dispatch import launch_debug
from agent_dispatch.procutil import no_window_kwargs

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


def configure(monkeypatch, tmp_path, value=True):
    root = tmp_path / "install"
    root.mkdir(exist_ok=True)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", raising=False)
    (root / "launch-debug.json").write_text(
        json.dumps({"bridge_cli_trace": value}), encoding="utf-8",
    )
    return root


def records(root):
    files = list((root / "logs" / "bridge-cli").glob("*.jsonl"))
    return [json.loads(line) for file in files for line in file.read_text().splitlines()]


def test_default_off_has_no_sink(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", raising=False)
    assert launch_debug.begin(["python", "-m", "bridge"], ["--json", "status", "a"]) is None
    assert not (tmp_path / "install" / "logs").exists()


@pytest.mark.parametrize("value", [False, True])
def test_observation_does_not_mutate_arguments_or_result(tmp_path, monkeypatch, value):
    root = configure(monkeypatch, tmp_path, value)
    prefix = ["python", "-m", "bridge"]
    argv = ["--json", "send", "bridge-a", "--native-invocation-file", "-"]
    payload = '{"prompt":"private assignment","binding":{"task_id":"task-a"}}'
    trace = launch_debug.begin(prefix, argv, prompt_input=payload, input_reference="reservation-a")
    result = subprocess.CompletedProcess([*prefix, *argv], 17, '{"reason":"native_receipt_missing"}', "connection refused\n")
    before = (prefix[:], argv[:], payload, result.stdout, result.stderr, result.returncode)
    if trace:
        trace.finish(result=result)
    assert (prefix, argv, payload, result.stdout, result.stderr, result.returncode) == before
    if value:
        started, completed = records(root)
        assert started["prefix"] == prefix and started["argv"] == argv
        assert started["argv_exact"] is True
        assert started["cwd"] == os.getcwd()
        assert started["stdin"]["reference"] == "reservation-a"
        assert started["stdin"]["bytes"] == len(payload.encode("utf-8"))
        assert len(started["stdin"]["sha256"]) == 64
        assert completed["returncode"] == 17
        assert completed["stdout"]["diagnostics"] == ["reason=native_receipt_missing"]
        assert completed["stderr"]["diagnostics"] == ["connection refused"]
        assert completed["elapsed_seconds"] >= 0
        assert "private assignment" not in str(records(root))
    else:
        assert not (root / "logs").exists()


def test_timeout_keeps_unknown_outcome_and_partial_streams(tmp_path, monkeypatch):
    root = configure(monkeypatch, tmp_path)
    trace = launch_debug.begin(["bridge"], ["--json", "create", "example@local"])
    error = subprocess.TimeoutExpired(["bridge"], 0.1, output=b'{"status":"idle"}', stderr=b"timed out")
    trace.finish(error=error)
    result = records(root)[1]
    assert result["outcome"] == "unknown_timeout"
    assert result["returncode"] is None
    assert result["stdout"]["bytes"] > 0
    assert result["stderr"]["diagnostics"] == ["timed out"]
    assert error.output == b'{"status":"idle"}'


def test_no_opaque_output_prompt_charter_or_known_credentials(tmp_path, monkeypatch):
    root = configure(monkeypatch, tmp_path)
    secret = "synthetic-credential-value"
    monkeypatch.setenv("EXAMPLE_TOKEN", secret)
    trace = launch_debug.begin(
        ["bridge"], ["--json", "create", secret, "--charter", "TOKEN=private-charter-secret"],
        prompt_input="private prompt", input_reference="reservation-a",
    )
    trace.finish(result=subprocess.CompletedProcess([], 1,
        '{"error":"private prompt","env":{"EXAMPLE_TOKEN":"' + secret + '"}}',
        "opaque private prompt " + secret))
    text = json.dumps(records(root))
    for forbidden in (secret, "private prompt", "private-charter-secret", "EXAMPLE_TOKEN"):
        assert forbidden not in text
    assert records(root)[0]["argv_exact"] is False
    assert records(root)[1]["stderr"]["content"] == "redacted"


@pytest.mark.parametrize("content", [
    '{', '[]', '{"bridge_cli_trace":"yes"}', '{"unexpected":true}', " " * 8193,
])
def test_malformed_config_warns_without_veto(tmp_path, monkeypatch, caplog, content):
    root = configure(monkeypatch, tmp_path)
    (root / "launch-debug.json").write_text(content)
    with caplog.at_level(logging.WARNING, logger="agent-dispatch.launch_debug"):
        assert launch_debug.begin(["bridge"], ["status"]) is None
    assert "launch-debug config invalid" in caplog.text


def test_env_override_and_invalid_override(tmp_path, monkeypatch, caplog):
    root = configure(monkeypatch, tmp_path, False)
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "1")
    trace = launch_debug.begin(["bridge"], ["status"])
    assert trace
    trace.finish(result=subprocess.CompletedProcess([], 0, "", ""))
    assert len(records(root)) == 2
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "0")
    assert launch_debug.begin(["bridge"], ["status"]) is None
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "incorrect")
    assert launch_debug.begin(["bridge"], ["status"]) is None
    assert "launch-debug override invalid" in caplog.text


def test_sink_failure_warns_without_veto(tmp_path, monkeypatch, caplog):
    root = configure(monkeypatch, tmp_path)
    (root / "logs").write_text("not a directory")
    assert launch_debug.begin(["bridge"], ["status"]) is None
    assert "launch-debug sink unavailable" in caplog.text


def test_finish_sink_failure_does_not_change_result(tmp_path, monkeypatch, caplog):
    configure(monkeypatch, tmp_path)
    trace = launch_debug.begin(["bridge"], ["status"])
    trace.path.unlink()
    trace.path.mkdir()
    result = subprocess.CompletedProcess([], 0, "unchanged", "")
    trace.finish(result=result)
    assert result.stdout == "unchanged"
    assert "launch-debug sink unavailable" in caplog.text


def test_removed_sink_is_not_recreated_with_default_permissions(tmp_path, monkeypatch, caplog):
    configure(monkeypatch, tmp_path)
    trace = launch_debug.begin(["bridge"], ["status"])
    trace.path.unlink()
    trace.finish(result=subprocess.CompletedProcess([], 0, "", ""))
    assert not trace.path.exists()
    assert "launch-debug sink unavailable" in caplog.text


def test_known_credentials_are_not_selected_as_diagnostics(tmp_path, monkeypatch):
    root = configure(monkeypatch, tmp_path)
    monkeypatch.setenv("EXAMPLE_TOKEN", "idle")
    trace = launch_debug.begin(["bridge"], ["status"])
    trace.finish(result=subprocess.CompletedProcess([], 0, '{"status":"idle"}', "opaque"))
    assert records(root)[1]["stdout"]["diagnostics"] == []


def test_os_error_is_metadata_only(tmp_path, monkeypatch):
    root = configure(monkeypatch, tmp_path)
    trace = launch_debug.begin(["bridge"], ["status"])
    error = OSError(2, "private exception diagnostic")
    trace.finish(error=error)
    row = records(root)[1]
    assert row["outcome"] == "os_error" and row["errno"] == 2
    assert "private exception" not in json.dumps(row)


def test_oversized_and_malformed_streams_are_omitted(tmp_path, monkeypatch):
    root = configure(monkeypatch, tmp_path)
    trace = launch_debug.begin(["bridge"], ["status"])
    trace.finish(result=subprocess.CompletedProcess([], 2, "x" * 65537, '{"error":"private diagnostic"'))
    row = records(root)[1]
    assert row["stdout"]["diagnostics_omitted"] == "oversized"
    assert row["stderr"]["diagnostics"] == []
    assert "private diagnostic" not in json.dumps(row)


def test_real_child_trace_survives_observing_caller_exit(tmp_path, monkeypatch):
    root = configure(monkeypatch, tmp_path)
    source = Path(launch_debug.__file__).resolve().parents[1]
    child = tmp_path / "bounded_child.py"
    child.write_text(
        "import sys\nprint('opaque stdout')\nprint('connection refused',file=sys.stderr)\nsys.exit(7)\n",
        encoding="utf-8",
    )
    script = (
        "import sys,subprocess\n"
        f"sys.path.insert(0,{str(source)!r})\n"
        "from agent_dispatch import launch_debug\n"
        "from agent_dispatch.procutil import no_window_kwargs\n"
        "prefix=[sys.executable]\n"
        f"argv=[{str(child)!r}]\n"
        "trace=launch_debug.begin(prefix,argv,timeout=5)\n"
        "result=subprocess.run([*prefix,*argv],capture_output=True,text=True,encoding='utf-8',"
        "timeout=5,check=False,**no_window_kwargs())\n"
        "trace.finish(result=result)\n"
        "assert result.returncode==7 and result.stdout=='opaque stdout\\n'\n"
    )
    caller = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        timeout=15, check=False, **no_window_kwargs(),
    )
    assert caller.returncode == 0, caller.stderr
    started, completed = records(root)
    assert started["event"] == "started"
    assert completed["returncode"] == 7
    assert completed["stderr"]["diagnostics"] == ["connection refused"]
    assert completed["stdout"]["content"] == "omitted"


def test_real_timeout_is_not_replayed(tmp_path, monkeypatch):
    root = configure(monkeypatch, tmp_path)
    child = tmp_path / "bounded_timeout.py"
    child.write_text("import time;print('opaque partial output',flush=True);time.sleep(2)", encoding="utf-8")
    prefix = [sys.executable]
    argv = [str(child)]
    trace = launch_debug.begin(prefix, argv, timeout=0.2)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        try:
            subprocess.run(
                [*prefix, *argv], capture_output=True, text=True, encoding="utf-8",
                check=False, timeout=0.2, **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            trace.finish(error=exc)
            raise
    assert caught.value.timeout == 0.2
    rows = records(root)
    assert len(rows) == 2
    assert rows[1]["outcome"] == "unknown_timeout" and rows[1]["returncode"] is None
    assert "opaque partial output" not in json.dumps(rows)
