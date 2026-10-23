"""Actual local continuation boundary, driven only by synthetic CLI processes."""

import logging
import sys
import time

import pytest

from agent_dispatch import bridge, startup_process
from agent_procutil.startup import PROGRESS_PREFIX, StartupClock, StartupExpiry, StartupTimeouts


def target(tmp_path, monkeypatch, body, *, accelerated=True):
    """Short-clock cases isolate the operation from cold native interpreter birth.

    The real/default-policy control below deliberately does not use this fixture
    clock. Production's clock always includes native process creation and imports.
    """
    birth = tmp_path / "interpreter-born"
    if accelerated:
        cold_deadline = time.monotonic() + 15

        def operation_time():
            try:
                born = float(birth.read_text())
            except (OSError, ValueError):
                # A broken synthetic process must still have a finite outer budget.
                return 30.0 if time.monotonic() >= cold_deadline else 0.0
            return max(0.0, time.monotonic() - born)

        monkeypatch.setattr(
            startup_process, "StartupClock",
            lambda policy: StartupClock(policy, monotonic=operation_time),
        )
    script = tmp_path / "synthetic_bridge.py"
    script.write_text(
        "import json,os,subprocess,sys,time\n"
        "assert sys.argv[1:5] == ['send','session-test','--prompt-file','-']\n"
        "assert sys.stdin.read() == 'private prompt'\n"
        f"with open({str(birth)!r},'w') as sink: sink.write(str(time.monotonic()))\n"
        f"PREFIX = {PROGRESS_PREFIX!r}\n"
        "def emit(event, phase='loading'):\n"
        " print(PREFIX + json.dumps(dict(version=1,event=event,phase=phase)),"
        "file=sys.stderr,flush=True)\n"
        + body,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: [sys.executable, str(script)],
    )
    return script


def resume(**kwargs):
    return bridge.resume_worker("session-test", "private prompt", **kwargs)


def test_default_policy_covers_real_cold_native_birth(tmp_path, monkeypatch):
    target(tmp_path, monkeypatch,
           "assert sys.argv[-4:] == ['--startup-communication-timeout','30.0',"
           "'--startup-process-timeout','300.0']\nemit('ready')\n",
           accelerated=False)
    started = time.monotonic()
    assert resume()
    assert time.monotonic() - started < 60


def test_local_resume_wrapper_preserves_unknown_outcome(monkeypatch):
    error = startup_process.StartupOutcomeUnknown(
        StartupExpiry("communication", 30, 30, "plugin-sync"), StartupTimeouts(),
    )

    def uncertain(*args, **kwargs):
        raise error

    monkeypatch.setattr(bridge, "resume_worker", uncertain)
    with pytest.raises(startup_process.StartupOutcomeUnknown) as raised:
        bridge.resume_session("session-test", "retain original request")
    assert raised.value is error


@pytest.mark.parametrize("event", ["progress", "additive_future_event"])
def test_slow_startup_renews_from_known_and_unknown_frames(tmp_path, monkeypatch, caplog, event):
    target(tmp_path, monkeypatch,
           f"for _ in range(8):\n emit({event!r}); time.sleep(.06)\n"
           "emit('ready'); print('{\"result\":\"ok\"}',flush=True)\n")
    with caplog.at_level(logging.WARNING, logger=startup_process.__name__):
        assert resume(timeout=.25, startup_process=2)
    assert "timeout" not in caplog.text


def test_stdout_and_partial_stderr_keepalive_are_contact(tmp_path, monkeypatch, caplog):
    target(tmp_path, monkeypatch,
           "for _ in range(8):\n sys.stdout.write('.'); sys.stdout.flush()\n"
           " sys.stderr.write('keepalive'); sys.stderr.flush(); time.sleep(.06)\n"
           "print(file=sys.stderr,flush=True); emit('ready')\n")
    with caplog.at_level(logging.WARNING, logger=startup_process.__name__):
        assert resume(timeout=.25, startup_process=2)
    assert "timeout" not in caplog.text


def test_fragmented_progress_is_contact_before_the_newline(tmp_path, monkeypatch, caplog):
    target(tmp_path, monkeypatch,
           "line = PREFIX + json.dumps(dict(version=1,event='future',phase='plugins')) + '\\n'\n"
           "for part in [line[:20],line[20:40],line[40:60],line[60:]]:\n"
           " sys.stderr.write(part); sys.stderr.flush(); time.sleep(.1)\n"
           "emit('ready')\n")
    with caplog.at_level(logging.WARNING, logger=startup_process.__name__):
        assert resume(timeout=.25, startup_process=2)
    assert "timeout" not in caplog.text


@pytest.mark.parametrize("initial", ["", "emit('progress','plugins')\n"])
def test_silence_reports_without_killing_or_resetting_contact(
    tmp_path, monkeypatch, caplog, initial,
):
    target(tmp_path, monkeypatch, initial + "time.sleep(.6)\nemit('ready')\n")
    with caplog.at_level(logging.WARNING, logger=startup_process.__name__):
        assert resume(timeout=.2, startup_process=2)
    rows = [record.message for record in caplog.records if "communication timeout" in record.message]
    assert len(rows) == 1
    assert ("phase=plugins" if initial else "phase=startup") in rows[0]
    assert "elapsed=" in rows[0] and "silence=" in rows[0]


def test_process_ceiling_is_not_renewed_by_progress(tmp_path, monkeypatch):
    target(tmp_path, monkeypatch, "while True:\n emit('progress'); time.sleep(.04)\n")
    with pytest.raises(bridge.StartupOutcomeUnknown) as caught:
        resume(timeout=.3, startup_process=.6)
    assert caught.value.clock == "process"
    assert caught.value.elapsed >= .6
    assert caught.value.retryable is False
    assert "private prompt" not in str(caught.value)


def test_ready_ends_startup_cap_not_the_turn(tmp_path, monkeypatch, caplog):
    target(tmp_path, monkeypatch, "emit('ready'); time.sleep(.8)\n")
    with caplog.at_level(logging.WARNING, logger=startup_process.__name__):
        assert resume(wait=True, timeout=.2, startup_process=.5)
    assert "timeout" not in caplog.text


def test_after_deadline_ready_is_not_success(tmp_path, monkeypatch):
    target(tmp_path, monkeypatch, "time.sleep(.8); emit('ready')\n")
    with pytest.raises(bridge.StartupOutcomeUnknown, match="process timeout"):
        resume(timeout=.2, startup_process=.5)


@pytest.mark.parametrize("event,clock", [
    ("communication_timeout", "communication"), ("process_timeout", "process"),
])
def test_unknown_outcome_never_falls_through_to_replacement(
    tmp_path, monkeypatch, event, clock,
):
    target(tmp_path, monkeypatch, f"emit({event!r},'ssh'); sys.exit(70)\n")
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("must not replay"))
    with pytest.raises(bridge.StartupOutcomeUnknown) as caught:
        bridge.spawn_or_resume_worker(
            "task-test", worker_id="worker-test", prompt="private prompt",
            prior_session_id="session-test", liveness_fn=lambda _: "gone",
        )
    assert caught.value.clock == clock
    assert caught.value.phase == "ssh"


@pytest.mark.parametrize("code,stdout,stderr", [
    (69, '{"reason":"unsupported_protocol"}', ""),
    (69, "", '{"detail":{"reason":"unsupported_protocol"}}'),
    (2, "", "unrecognized arguments: --startup-communication-timeout 30"),
])
def test_incompatible_peer_is_not_proof_of_session_absence(
    tmp_path, monkeypatch, code, stdout, stderr,
):
    target(tmp_path, monkeypatch,
           f"print({stdout!r},flush=True)\n"
           f"print({stderr!r},file=sys.stderr,flush=True)\nsys.exit({code})\n")
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("must not replace"))
    with pytest.raises(bridge.BridgeUnavailable, match="absence is unproven") as caught:
        bridge.spawn_or_resume_worker(
            "task-test", worker_id="worker-test", prompt="private prompt",
            prior_session_id="session-test", liveness_fn=lambda _: "gone",
        )
    assert "private prompt" not in str(caught.value)


def test_known_not_found_still_permits_existing_replacement_contract(tmp_path, monkeypatch):
    target(tmp_path, monkeypatch,
           "print('{\"reason\":\"not_found\"}',file=sys.stderr,flush=True); sys.exit(1)\n")
    replacement = object()
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: replacement)
    assert bridge.spawn_or_resume_worker(
        "task-test", worker_id="worker-test", prompt="private prompt",
        prior_session_id="session-test", liveness_fn=lambda _: "gone",
    ) is replacement


@pytest.mark.parametrize("code", [0, 7, 70])
def test_exit_compatibility_and_single_private_stdin(tmp_path, monkeypatch, code):
    marker = tmp_path / "sent"
    target(tmp_path, monkeypatch,
           f"with open({str(marker)!r},'a') as sink: sink.write('once\\n')\n"
           f"sys.exit({code})\n")
    if code == 70:
        with pytest.raises(bridge.StartupOutcomeUnknown):
            resume()
    else:
        assert resume() is (code == 0)
    assert marker.read_text().splitlines() == ["once"]


@pytest.mark.parametrize("exit_parent", [False, True])
def test_owned_descendant_pipe_cleanup_is_bounded(tmp_path, monkeypatch, exit_parent):
    marker = tmp_path / "escaped"
    child = (
        "import pathlib,time; time.sleep(1.5); "
        f"pathlib.Path({str(marker)!r}).write_text('escaped')"
    )
    target(tmp_path, monkeypatch,
           f"subprocess.Popen([sys.executable,'-c',{child!r}])\n"
           + ("sys.exit(7)\n" if exit_parent else "time.sleep(60)\n"))
    if exit_parent:
        assert resume(timeout=.2, startup_process=.6) is False
    else:
        with pytest.raises(bridge.StartupOutcomeUnknown):
            resume(timeout=.2, startup_process=.6)
    operation_started = float((tmp_path / "interpreter-born").read_text())
    assert time.monotonic() - operation_started < 3
    time.sleep(1.6)
    assert not marker.exists()


def test_output_capture_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(startup_process, "CAPTURE_LIMIT", 1024)
    target(tmp_path, monkeypatch, "sys.stdout.write('x'*2048); sys.stdout.flush(); time.sleep(60)\n")
    with pytest.raises(bridge.StartupOutcomeUnknown) as caught:
        resume(timeout=.3, startup_process=2)
    assert caught.value.reason == "output_limit"
    assert caught.value.retryable is False


def test_final_json_and_stderr_are_preserved_separately(tmp_path, monkeypatch):
    target(tmp_path, monkeypatch,
           "emit('ready'); print('{\"result\":\"ok\"}',flush=True)\n"
           "print('diagnostic',file=sys.stderr,flush=True)\n")
    original = bridge.run_startup
    completed = []

    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        completed.append(result)
        return result

    monkeypatch.setattr(bridge, "run_startup", observe)
    assert resume()
    assert completed[0].stdout == '{"result":"ok"}\n'
    assert PROGRESS_PREFIX in completed[0].stderr
    assert completed[0].stderr.endswith("diagnostic\n")


@pytest.mark.parametrize("phase", [
    "launch/foo/bar", "lock: repository prep - waiting", "a" * 120,
])
def test_nested_and_named_phases_survive_unknown_diagnostics(tmp_path, monkeypatch, phase):
    target(tmp_path, monkeypatch, f"emit('communication_timeout',{phase!r}); sys.exit(70)\n")
    with pytest.raises(bridge.StartupOutcomeUnknown) as caught:
        resume()
    assert caught.value.phase == phase
    assert caught.value.scope == "bridge_target"
    assert phase in str(caught.value)


def test_inner_timeout_updates_are_outer_hop_contact(tmp_path, monkeypatch, caplog):
    target(tmp_path, monkeypatch,
           "for _ in range(8):\n"
           " emit('communication_timeout','nested/ssh'); time.sleep(.06)\n"
           " emit('process_timeout','nested/lock'); time.sleep(.06)\n"
           "sys.exit(70)\n")
    with caplog.at_level(logging.WARNING, logger=startup_process.__name__):
        with pytest.raises(bridge.StartupOutcomeUnknown) as caught:
            resume(timeout=.2, startup_process=2)
    assert caught.value.clock == "process"
    assert caught.value.scope == "bridge_target"
    assert caught.value.phase == "nested/lock"
    assert "scope=dispatch_cli" not in caplog.text


def test_lost_admission_ack_after_ready_is_not_a_local_clock_expiry(tmp_path, monkeypatch):
    target(tmp_path, monkeypatch,
           "emit('ready'); time.sleep(.8)\n"
           "print('{\"reason\":\"admission_unknown\"}',flush=True); sys.exit(70)\n")
    with pytest.raises(bridge.StartupOutcomeUnknown) as caught:
        resume(wait=True, timeout=.2, startup_process=.5)
    assert caught.value.clock == "outcome"
    assert caught.value.scope == "bridge_target"
    assert caught.value.reason == "admission_unknown"
    assert caught.value.retryable is False


@pytest.mark.parametrize("code", [0, 70])
def test_post_ready_stderr_volume_drains_without_turn_limit(
    tmp_path, monkeypatch, caplog, code,
):
    from tests.test_launch_debug import configure, records

    root = configure(monkeypatch, tmp_path)
    monkeypatch.setattr(startup_process, "CAPTURE_LIMIT", 1024)
    target(tmp_path, monkeypatch,
           "emit('ready'); time.sleep(.05)\n"
           "sys.stderr.write('x'*8192); sys.stderr.flush()\n"
           "time.sleep(.8); print('{\"result\":\"ok\"}',flush=True)\n"
           f"sys.exit({code})\n")
    completed = []
    original = bridge.run_startup

    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        completed.append(result)
        return result

    monkeypatch.setattr(bridge, "run_startup", observe)
    with caplog.at_level(logging.WARNING, logger=startup_process.__name__):
        if code == 70:
            with pytest.raises(bridge.StartupOutcomeUnknown) as caught:
                resume(wait=True, timeout=.2, startup_process=.5)
            assert caught.value.stderr is None
            assert caught.value.output == '{"result":"ok"}\n'
        else:
            assert resume(wait=True, timeout=.2, startup_process=.5)
            assert completed[0].stdout == '{"result":"ok"}\n'
            assert completed[0].stderr is None
            assert completed[0].capture_truncated == ("stderr",)
            assert len(completed[0].diagnostic_tails["stderr"].encode()) <= 1024
    assert "capture truncated after readiness" in caplog.text
    finished = records(root)[1]
    assert finished["stderr"]["capture"] == "unavailable"
    assert finished["outcome"] == ("unknown_timeout" if code else "exited")


def test_pre_ready_capture_limit_has_durable_unknown_trace(tmp_path, monkeypatch):
    from tests.test_launch_debug import configure, records

    root = configure(monkeypatch, tmp_path)
    monkeypatch.setattr(startup_process, "CAPTURE_LIMIT", 1024)
    target(tmp_path, monkeypatch,
           "sys.stdout.write('x'*2048); sys.stdout.flush(); time.sleep(60)\n")
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("must not replay"))
    with pytest.raises(bridge.StartupOutcomeUnknown) as caught:
        bridge.spawn_or_resume_worker(
            "task-test", worker_id="worker-test", prompt="private prompt",
            prior_session_id="session-test", liveness_fn=lambda _: "gone",
        )
    assert caught.value.reason == "output_limit"
    started, finished = records(root)
    assert finished["outcome"] == "unknown_timeout"
    assert finished["returncode"] is None
    assert "private prompt" not in str((started, finished))
