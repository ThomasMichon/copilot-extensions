from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import signal
from pathlib import Path
import sys
import time

import pytest

from budget_guidance.cli import main
from budget_guidance.external_reader import CommandReader, acquire_config
from budget_guidance.models import ModelError, parse_config
from budget_guidance.posture import build_posture


def _config():
    return parse_config({
        "schema": "copilot-extensions.budget-guidance-config", "version": Decimal(1),
        "adapters": [{
            "type": "external-command", "id": "report", "authority": Decimal(10),
            "source": "account-reader",
        }],
    })


def _reading(**overrides):
    value = {
        "schema": "copilot-extensions.budget-reading", "version": 1,
        "source": "account-reader", "captured_at": "2026-01-01T00:00:00Z",
        "freshness_seconds": 86400, "availability": "available",
        "allowance": 100, "consumption": 40, "reset_at": "2026-01-03T00:00:00Z",
    }
    value.update(overrides)
    return value


def _reader(tmp_path, code, **limits):
    return CommandReader((sys.executable, "-I", "-S", "-c", code), tmp_path, **limits)


def _posture(config):
    return build_posture(config, datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.mark.guard
def test_stdlib_reader_fixture_does_not_load_ambient_site_packages(tmp_path):
    reader = _reader(
        tmp_path,
        "import sys; assert sys.flags.isolated and sys.flags.no_site; "
        f"print({json.dumps(_reading())!r})",
    )
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["availability"] == "available"


@pytest.mark.guard
def test_config_is_inert_and_missing_grant_never_runs(monkeypatch):
    import budget_guidance.external_reader as module

    def forbidden(*args, **kwargs):
        pytest.fail("inert configuration started a reader")

    monkeypatch.setattr(module, "_run", forbidden)
    posture = _posture(acquire_config(_config()))
    assert posture["calculated"] is None
    assert posture["adapters"][0]["availability"] == "unavailable"
    assert posture["adapters"][0]["source"] == "account-reader"
    assert "not authorized" in posture["adapters"][0]["error"]
    with pytest.raises(ModelError, match="acquired before"):
        _posture(_config())


@pytest.mark.guard
@pytest.mark.parametrize("field", ["argv", "command", "env", "cwd", "timeout_seconds"])
def test_config_cannot_grant_executable_or_environment_authority(field):
    value = {
        "schema": "copilot-extensions.budget-guidance-config", "version": Decimal(1),
        "adapters": [{
            "type": "external-command", "id": "report", "authority": Decimal(10),
            "source": "account-reader", field: "forbidden",
        }],
    }
    with pytest.raises(ModelError, match="unknown keys"):
        parse_config(value)


@pytest.mark.parametrize("limits", [
    {"timeout_seconds": 0}, {"timeout_seconds": 31}, {"timeout_seconds": float("nan")},
    {"timeout_seconds": True}, {"timeout_seconds": 10**1000},
    {"max_output_bytes": 0}, {"max_output_bytes": 65537},
    {"max_output_bytes": True},
])
def test_execution_limits_are_strict(tmp_path, limits):
    with pytest.raises(ModelError):
        _reader(tmp_path, "pass", **limits)


def test_grants_require_absolute_paths_and_refuse_batch(tmp_path):
    for argv in [("python", "-c", "pass"), ("/example/reader.cmd",), ("",), ()]:
        with pytest.raises(ModelError):
            CommandReader(argv, tmp_path)
    with pytest.raises(ModelError):
        CommandReader((sys.executable,), Path("relative"))
    with pytest.raises(ModelError, match="does not match"):
        acquire_config(_config(), {"other": _reader(tmp_path, "pass")})


@pytest.mark.parametrize("name", [
    "reader.cmd.", "reader.bat ", "reader.CMD.. ", "reader.BaT. .",
])
def test_batch_rejection_covers_windows_trailing_name_normalization(tmp_path, name):
    with pytest.raises(ModelError, match="batch executables"):
        CommandReader((str(tmp_path / name),), tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="native Windows executable stream semantics")
def test_windows_executable_streams_cannot_alias_batch_entrypoints(tmp_path):
    with pytest.raises(ModelError, match="executable streams"):
        CommandReader((str(tmp_path / "reader.cmd::$DATA"),), tmp_path)



@pytest.mark.guard
def test_real_reader_reuses_posture_and_keeps_capture(tmp_path):
    body = json.dumps(_reading())
    counter = tmp_path / "calls"
    reader = _reader(
        tmp_path, f"from pathlib import Path; Path({str(counter)!r}).write_text('once'); "
        f"print({body!r})",
    )
    config = acquire_config(_config(), {"report": reader})
    posture = _posture(config)
    assert counter.read_text() == "once"
    assert posture["calculated"]["remaining"] == "60"
    assert posture["fields"]["consumption"]["selected"]["source"] == "account-reader"
    assert posture["adapters"][0]["captured_at"] == "2026-01-01T00:00:00Z"
    assert posture["adapters"][0]["freshness_seconds"] == 86400
    later = build_posture(config, datetime(2026, 1, 2, 0, 0, 1, tzinfo=timezone.utc))
    assert later["availability"] == "stale"


@pytest.mark.parametrize(("code", "diagnostic"), [
    ("raise SystemExit(9)", "exited unsuccessfully"),
    ("print('secret-not-json')", "invalid budget reading"),
    ("print('{}')", "invalid budget reading"),
    ("print('{\"version\":1,\"version\":1}')", "invalid budget reading"),
    ("import sys; sys.stdout.buffer.write(b'\\xff')", "invalid budget reading"),
    ("print('x' * 65537)", "exceeds byte limit"),
    ("import time; time.sleep(10)", "deadline exceeded"),
])
def test_reader_failures_are_explicit_and_redacted(tmp_path, code, diagnostic):
    started = time.monotonic()
    reader = _reader(tmp_path, code, timeout_seconds=0.5)
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert time.monotonic() - started < 6
    assert posture["availability"] == "error"
    assert posture["calculated"] is None
    assert posture["fields"] == {}
    assert diagnostic in posture["adapters"][0]["error"]
    assert "secret-not-json" not in json.dumps(posture)


def test_stderr_flood_is_not_captured_or_forwarded(tmp_path):
    reader = _reader(
        tmp_path, "import sys; sys.stderr.write('secret' * 100000); "
        f"print({json.dumps(_reading())!r})",
    )
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["availability"] == "available"
    assert "secret" not in json.dumps(posture)


@pytest.mark.parametrize(("overrides", "diagnostic"), [
    ({"source": "other-account"}, "source does not match"),
    ({"captured_at": "9999-01-01T00:00:00Z"}, "capture time is in the future"),
])
def test_attribution_failures_withhold_values(tmp_path, overrides, diagnostic):
    reader = _reader(tmp_path, f"print({json.dumps(_reading(**overrides))!r})")
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["calculated"] is None
    assert diagnostic in posture["adapters"][0]["error"]


@pytest.mark.parametrize("availability", ["unavailable", "error"])
def test_reported_failure_preserves_state_without_opaque_error(tmp_path, availability):
    reading = _reading(
        availability=availability, allowance=None, consumption=None, reset_at=None,
        error="secret-account-detail",
    )
    reader = _reader(tmp_path, f"print({json.dumps(reading)!r})")
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["adapters"][0]["availability"] == availability
    assert posture["fields"] == {}
    assert "secret-account-detail" not in json.dumps(posture)


def test_exact_output_bound_and_literal_argv(tmp_path):
    body = json.dumps(_reading()).encode()
    reader = CommandReader(
        (sys.executable, "-c", "import sys; print(sys.argv[1], end='')", body.decode()),
        tmp_path, max_output_bytes=len(body),
    )
    assert _posture(acquire_config(_config(), {"report": reader}))["availability"] == "available"
    reader = CommandReader(reader.argv, tmp_path, max_output_bytes=len(body) - 1)
    assert _posture(acquire_config(_config(), {"report": reader}))["calculated"] is None


def test_external_partial_fields_keep_static_authority_and_contradictions(tmp_path):
    from budget_guidance.models import BudgetConfig, StaticAdapter, parse_reading

    static = json.loads(json.dumps(_reading(consumption=5)), parse_int=Decimal)
    config = BudgetConfig((
        StaticAdapter("manual", 1, parse_reading(static)), *_config().adapters,
    ))
    reader = _reader(tmp_path, f"print({json.dumps(_reading(allowance=None, reset_at=None))!r})")
    posture = _posture(acquire_config(config, {"report": reader}))
    consumption = posture["fields"]["consumption"]
    assert consumption["selected"]["value"] == "5"
    assert consumption["selected"]["adapter"] == "manual"
    assert consumption["contradictions"][0]["value"] == "40"
    assert posture["availability"] == "contradictory"


def test_valid_stdout_without_process_completion_still_times_out(tmp_path):
    code = (
        "import os,time; "
        f"print({json.dumps(_reading())!r}, flush=True); "
        "os.close(1); time.sleep(10)"
    )
    reader = _reader(tmp_path, code, timeout_seconds=0.5)
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["calculated"] is None
    assert posture["adapters"][0]["error"] == "reader deadline exceeded"


def test_launch_failure_never_exposes_executable_or_cwd(tmp_path):
    reader = CommandReader((str(tmp_path / "missing-secret-command"),), tmp_path)
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["calculated"] is None
    assert "secret-command" not in json.dumps(posture)


@pytest.mark.skipif(sys.platform != "linux", reason="native POSIX group identity witness")
@pytest.mark.parametrize("keep_pipe", [False, True])
def test_owned_descendants_are_reaped_even_after_root_exits(tmp_path, keep_pipe):
    pid_file = tmp_path / "child.pid"
    child = "import time; time.sleep(10)"
    code = (
        "import subprocess,sys; from pathlib import Path; "
        f"child=subprocess.Popen([sys.executable,'-c',{child!r}],"
        f"stdout={'None' if keep_pipe else 'subprocess.DEVNULL'}); "
        f"Path({str(pid_file)!r}).write_text(str(child.pid)); "
        f"print({json.dumps(_reading())!r})"
    )
    reader = _reader(tmp_path, code, timeout_seconds=0.5)
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["availability"] == ("error" if keep_pipe else "available")
    pid = int(pid_file.read_text())
    status = Path(f"/proc/{pid}/stat")
    for _ in range(50):
        try:
            state = status.read_text().split(") ", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            break
        if state == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail("reader descendant survived cleanup")


def test_cli_requires_explicit_authority_and_renders_same_posture(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "schema": "copilot-extensions.budget-guidance-config", "version": 1,
        "adapters": [{
            "type": "external-command", "id": "report", "authority": 10,
            "source": "account-reader",
        }],
    }))
    args = ["status", "--config", str(config), "--at", "2026-01-01T00:00:00Z", "--json"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["calculated"] is None
    assert main([*args, "--reader-id", "report"]) == 2
    assert "required together" in json.loads(capsys.readouterr().out)["error"]
    assert main([
        *args, "--reader-id", "report", "--reader-cwd", str(tmp_path),
        "--reader-command", sys.executable, "-c", f"print({json.dumps(_reading())!r})",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["calculated"]["remaining"] == "60"


@pytest.mark.skipif(os.name == "nt", reason="POSIX capability refusal")
@pytest.mark.parametrize("capability", [
    "posix_spawn", "POSIX_SPAWN_OPEN", "killpg", "waitid", "waitpid",
    "P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED",
])
def test_missing_posix_capability_is_a_redacted_error(tmp_path, monkeypatch, capability):
    import budget_guidance.external_reader as module

    monkeypatch.delattr(module.os, capability, raising=False)
    reader = _reader(tmp_path, f"print({json.dumps(_reading())!r})")
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["calculated"] is None
    assert posture["adapters"][0]["error"] == (
        "bounded reader execution is unsupported on this runtime"
    )


def test_invalid_reader_options_are_not_labeled_invalid_configuration(capsys):
    assert main(["status", "--reader-id", "report"]) == 2
    error = capsys.readouterr().err
    assert "invalid invocation: " in error
    assert "invalid configuration: " not in error


@pytest.mark.skipif(os.name == "nt", reason="POSIX status error")
def test_status_read_failure_is_explicit_not_a_raw_exception(tmp_path, monkeypatch):
    import budget_guidance.external_reader as module

    read_status = module._read_status

    def fail_status(stream, result, cleanup):
        result.put(module.ReaderFailure("private-status-detail"))
        discarded = module.Queue()
        read_status(stream, discarded, cleanup)

    monkeypatch.setattr(module, "_read_status", fail_status)
    reader = _reader(tmp_path, f"print({json.dumps(_reading())!r})")
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["calculated"] is None
    assert posture["adapters"][0]["error"] == "reader host status could not be read"
    assert "private-status-detail" not in json.dumps(posture)


def test_output_worker_start_failure_still_cleans_up_owned_process(tmp_path, monkeypatch):
    import budget_guidance.external_reader as module

    processes = []
    spawn = module.spawn_sync_in_kill_on_close_job

    def tracked_spawn(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        processes.append(process)
        return process, job

    def fail_start(self):
        raise RuntimeError("private-thread-detail")

    monkeypatch.setattr(module, "spawn_sync_in_kill_on_close_job", tracked_spawn)
    monkeypatch.setattr(module.Thread, "start", fail_start)
    reader = _reader(tmp_path, "import time; time.sleep(10)")
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["adapters"][0]["error"] == "reader output worker could not start"
    assert posture["calculated"] is None
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed


def test_unmatched_grant_counts_do_not_echo_potential_path_or_secret(tmp_path):
    with pytest.raises(ModelError) as raised:
        acquire_config(_config(), {"secret-invalid-path": _reader(tmp_path, "pass")})
    assert "1 unmatched; 1 configured" in str(raised.value)
    assert "secret-invalid-path" not in str(raised.value)


@pytest.mark.parametrize("options", [
    ["--reader-timeout", "10"], ["--reader-output-limit", "65536"],
])
def test_explicit_default_reader_options_still_require_full_grant(options, capsys):
    assert main(["status", "--json", *options]) == 2
    assert "reader options require" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX interpreter validation")
@pytest.mark.parametrize("executable", ["", "python", "/missing/operator/python"])
def test_unusable_host_interpreter_refuses_before_spawn(tmp_path, monkeypatch, executable):
    import budget_guidance.external_reader as module

    reader = _reader(tmp_path, "pass")
    monkeypatch.setattr(module.sys, "executable", executable)

    def forbidden_spawn(*args, **kwargs):
        pytest.fail("an unusable interpreter reached process creation")

    monkeypatch.setattr(module, "spawn_sync_in_kill_on_close_job", forbidden_spawn)
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["calculated"] is None
    assert posture["adapters"][0]["error"] == "reader host interpreter is unavailable"


def test_missing_config_does_not_silently_ignore_grant(tmp_path, capsys):
    assert main([
        "status", "--config", str(tmp_path / "missing.json"), "--json",
        "--reader-id", "report", "--reader-cwd", str(tmp_path),
        "--reader-command", sys.executable, "-c", "pass",
    ]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert "cannot validate reader grants without configuration" in error


@pytest.mark.parametrize(("code", "primary"), [
    ("print('x' * 65537)", "reader output exceeds byte limit"),
    ("raise SystemExit(9)", "reader exited unsuccessfully"),
    ("import time; time.sleep(10)", "reader deadline exceeded"),
    (f"print({json.dumps(_reading())!r})", None),
])
def test_cleanup_failure_keeps_primary_failure_and_redacts_native_detail(
    tmp_path, monkeypatch, code, primary,
):
    import budget_guidance.external_reader as module

    spawn = module.spawn_sync_in_kill_on_close_job

    def failing_cleanup_spawn(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        wait = process.wait

        def fail_after_wait(*args, **kwargs):
            wait(*args, **kwargs)
            raise OSError("secret-native-cleanup-detail")

        process.wait = fail_after_wait
        return process, job

    monkeypatch.setattr(module, "spawn_sync_in_kill_on_close_job", failing_cleanup_spawn)
    reader = _reader(tmp_path, code, timeout_seconds=0.5)
    posture = _posture(acquire_config(_config(), {"report": reader}))
    error = posture["adapters"][0]["error"]
    if primary is not None:
        assert primary in error
    assert "reader process cleanup could not be confirmed" in error
    assert "secret-native-cleanup-detail" not in json.dumps(posture)
    assert posture["calculated"] is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX host startup boundary")
def test_host_does_not_execute_ambient_sitecustomize_before_containment(tmp_path, monkeypatch):
    marker = tmp_path / "ambient-ran"
    (tmp_path / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('unexpected')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    reader = CommandReader(
        (sys.executable, "-I", "-S", "-c", f"print({json.dumps(_reading())!r})"), tmp_path,
    )
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["availability"] == "available"
    assert not marker.exists(), "host executed ambient code before establishing the owned group"


def test_completed_output_after_spawn_exhausts_deadline_is_not_success(tmp_path, monkeypatch):
    import budget_guidance.external_reader as module

    clock = [0.0]
    spawn = module.spawn_sync_in_kill_on_close_job
    thread = module.Thread

    def spawn_after_deadline(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        if os.name == "nt":
            process.wait(timeout=5)
        clock[0] = 2.0
        return process, job

    class ImmediateOutput:
        ident = 1

        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

        def join(self, timeout):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(module, "spawn_sync_in_kill_on_close_job", spawn_after_deadline)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module, "Thread",
        lambda **kwargs: (
            thread(**kwargs) if kwargs["target"] is module._read_status
            else ImmediateOutput(**kwargs)
        ),
    )
    reader = _reader(tmp_path, f"print({json.dumps(_reading())!r})", timeout_seconds=1)
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["calculated"] is None
    assert posture["adapters"][0]["error"] == "reader deadline exceeded"


@pytest.mark.skipif(os.name == "nt", reason="POSIX auto-reaping ownership")
def test_ignored_sigchld_never_causes_parent_numeric_pid_signaling(tmp_path, monkeypatch):
    import budget_guidance.external_reader as module

    def forbidden_signal(*args):
        pytest.fail("caller signaled a numeric PID/group instead of closing its owned pipe")

    monkeypatch.setattr(module.os, "kill", forbidden_signal)
    monkeypatch.setattr(module.os, "killpg", forbidden_signal)
    previous = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    try:
        reader = _reader(tmp_path, f"print({json.dumps(_reading())!r})")
        posture = _posture(acquire_config(_config(), {"report": reader}))
        assert posture["availability"] == "available"
    finally:
        signal.signal(signal.SIGCHLD, previous)


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux descendant witness")
def test_caller_closing_control_pipe_reaps_running_reader_and_descendant(tmp_path):
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'],"
        "stdout=subprocess.DEVNULL); "
        f"Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(10)"
    )
    reader = _reader(tmp_path, code, timeout_seconds=0.5)
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["adapters"][0]["error"] == "reader deadline exceeded"
    status = Path(f"/proc/{int(pid_file.read_text())}/stat")
    for _ in range(50):
        try:
            state = status.read_text().split(") ", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return
        if state == "Z":
            return
        time.sleep(0.01)
    pytest.fail("running reader descendant survived owned control-pipe cleanup")


@pytest.mark.skipif(os.name == "nt", reason="POSIX lost-root identity")
def test_lost_root_ownership_never_signals_a_numeric_pid(tmp_path, monkeypatch):
    import budget_guidance.external_reader as module

    spawn = module.spawn_sync_in_kill_on_close_job

    def spawn_with_reaped_observation(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        process.poll = lambda: 0
        return process, job

    def forbidden_signal(*args):
        pytest.fail("caller signaled a possibly reused numeric identity")

    monkeypatch.setattr(module, "spawn_sync_in_kill_on_close_job", spawn_with_reaped_observation)
    monkeypatch.setattr(module.os, "kill", forbidden_signal)
    monkeypatch.setattr(module.os, "killpg", forbidden_signal)
    reader = _reader(tmp_path, f"print({json.dumps(_reading())!r})")
    posture = _posture(acquire_config(_config(), {"report": reader}))
    assert posture["calculated"] is None
    assert "reader host ownership was lost" in posture["adapters"][0]["error"]
