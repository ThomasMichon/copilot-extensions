"""Actual production POSIX owner path inside the runner's outer containment."""

from __future__ import annotations

import asyncio
import json
import os
import select
import subprocess
import sys
from threading import Event, Thread, enumerate as threads
import time

from agent_procutil import contained_test_mode
import pytest

from worktree_manager import agenda_transport as transport
from worktree_manager import engine_posix_process as posix
from worktree_manager import mux_companion as companion
from worktree_manager.agenda_contract import AgendaFeed, AgendaUnavailable
from test_agenda_contract import publication
from test_companion_agenda_live import current_data, until, widget

pytestmark = pytest.mark.skipif(os.name == "nt", reason="production POSIX group-owner path")


@pytest.fixture
def reserved_owners(monkeypatch):
    assert contained_test_mode(), "these production-path controls require the outer contained runner"
    # This local selection does not change agent-procutil's containment marker,
    # the supervisor, Docker boundary or descendants' inherited test policy.
    monkeypatch.setattr(transport, "contained_test_mode", lambda: False)
    actual_spawn, actual_kill = subprocess.Popen, os.killpg
    actual_read = posix.read_completion_receipt
    owners = {}

    def spawn(*args, **kwargs):
        process = actual_spawn(*args, **kwargs)
        if kwargs.get("pass_fds") and kwargs.get("start_new_session"):
            state = {"process": process, "cleanup": False, "receipts": []}
            owners[process.pid] = state
            poll, wait, communicate = process.poll, process.wait, process.communicate

            def guarded_poll():
                assert state["cleanup"], "reserved owner was polled before group cleanup"
                return poll()

            def guarded_wait(*args, **kwargs):
                assert state["cleanup"], "reserved owner was reaped before group cleanup"
                return wait(*args, **kwargs)

            def guarded_communicate(*args, **kwargs):
                assert state["cleanup"], "reserved owner was communicated/reaped before cleanup"
                return communicate(*args, **kwargs)

            process.poll, process.wait, process.communicate = (
                guarded_poll, guarded_wait, guarded_communicate,
            )
        return process

    def kill(pid, signal):
        assert pid in owners, "cleanup targeted an unowned process group"
        state = owners[pid]
        assert state["process"].returncode is None, "owner identity was released before cleanup"
        os.kill(pid, 0)
        state["cleanup"] = True
        return actual_kill(pid, signal)

    def read(output, **kwargs):
        value = actual_read(output, **kwargs)
        state = next(item for item in owners.values() if item["process"].stdout is output)
        if not state["cleanup"]:
            assert state["process"].returncode is None
            os.kill(state["process"].pid, 0)
        else:
            assert kwargs["cancelled"] is not None and kwargs["cancelled"].is_set()
        state["receipts"].append(value)
        return value

    monkeypatch.setattr(posix.subprocess, "Popen", spawn)
    monkeypatch.setattr(posix.os, "killpg", kill)
    monkeypatch.setattr(posix, "read_completion_receipt", read)
    monkeypatch.setattr(transport, "read_completion_receipt", read)
    yield owners
    assert owners
    assert all(item["cleanup"] and item["process"].returncode is not None for item in owners.values())
    assert not any(thread.name.startswith("agenda-feed-") for thread in threads())


@pytest.fixture
def provider(tmp_path, monkeypatch):
    script = tmp_path / "provider.py"
    started = tmp_path / "started"

    def configure(body):
        script.write_text(
            "import json,sys,time\nfrom pathlib import Path\n"
            "if '--resident-health' in sys.argv:\n"
            "    print(json.dumps({'ok':True,'health':{'capabilities':['worktree-agendas-v1'],"
            "'worktrees':{'status':'ready','generation':'a'*32}}}))\n"
            "    sys.exit(0)\n"
            f"Path({str(started)!r}).write_text(str(time.monotonic()))\n"
            + body + "\n", encoding="utf-8",
        )
        monkeypatch.setattr(companion.ec, "engine_base_command", lambda: [sys.executable, str(script)])
        monkeypatch.setattr(transport, "health_argv", lambda base: [*base, "--resident-health"])
        return started

    return configure


def test_noncontained_fatal_exit_drains_small_stderr_without_retry(
    reserved_owners, provider, monkeypatch, tmp_path,
):
    started = provider("sys.stderr.write('fatal fixture configuration\\n'); sys.stderr.flush(); sys.exit(1)")
    monkeypatch.setattr(companion, "_load_current_worktree", lambda *args, **kwargs: current_data())
    monkeypatch.setattr(companion.ca, "read_session_attention", lambda *args, **kwargs: {
        "attention": None, "error": "fixture-unavailable",
    })

    async def check():
        app = companion.MuxCompanionApp(cwd=str(tmp_path))
        async with app.run_test() as pilot:
            await until(pilot, lambda: "exited 1: fatal fixture configuration" in widget(app))
            assert time.monotonic() - float(started.read_text()) < 2
            await app._agenda_worker.wait()
            assert "deadline" not in widget(app) and "reconnecting" not in widget(app)
            assert len(reserved_owners) == 1
            assert next(iter(reserved_owners.values()))["receipts"] == [{"returncode": 1}]
            assert app._data.agenda is None

    asyncio.run(check())


def test_noncontained_success_eof_is_disconnect_even_with_warning(
    reserved_owners, provider, tmp_path,
):
    frame = publication()
    provider(
        f"print({json.dumps(frame)!r},flush=True)\n"
        "sys.stderr.write('small fixture warning\\n'); sys.stderr.flush(); sys.exit(0)"
    )
    stream = transport.frames("example", "selected", "head-a", cancelled=Event(), cwd=str(tmp_path))
    assert next(stream) == frame
    with pytest.raises(AgendaUnavailable, match="disconnected") as error:
        next(stream)
    assert error.value.retry and error.value.code == "disconnected"
    assert "small fixture warning" in str(error.value)
    assert next(iter(reserved_owners.values()))["receipts"] == [{"returncode": 0}]


def test_noncontained_structured_error_precedes_exit_status(
    reserved_owners, provider, tmp_path,
):
    frame = publication()
    error_frame = {
        "type": "error", "ok": False, "contract_version": 1,
        "error": {"code": "disconnected", "message": "actual child reported disconnection"},
    }
    provider(
        f"print({json.dumps(frame)!r},flush=True)\n"
        f"print({json.dumps(error_frame)!r},flush=True)\n"
        "sys.exit(1)"
    )
    receiver = AgendaFeed("example", "selected", "head-a")
    stream = transport.frames("example", "selected", "head-a", cancelled=Event(), cwd=str(tmp_path))
    try:
        receiver.receive(next(stream))
        with pytest.raises(AgendaUnavailable, match="actual child reported") as error:
            receiver.receive(next(stream))
        assert error.value.retry
    finally:
        stream.close()


def test_noncontained_spawn_failure_has_explicit_receipt(
    reserved_owners, provider, monkeypatch, tmp_path,
):
    provider("sys.exit(0)")
    missing = str(tmp_path / "missing-executable")
    monkeypatch.setattr(transport, "feed_argv", lambda *args, **kwargs: [missing])
    stream = transport.frames("example", "selected", "head-a", cancelled=Event(), cwd=str(tmp_path))
    with pytest.raises(AgendaUnavailable, match="could not start.*No such file") as error:
        next(stream)
    assert not error.value.retry
    assert error.value.code == "spawn-failed"
    assert "error" in next(iter(reserved_owners.values()))["receipts"][0]


@pytest.mark.parametrize("spawn_error", [False, True])
def test_owner_copy_does_not_keep_feed_writer_open_before_cleanup(
    reserved_owners, tmp_path, spawn_error,
):
    argv = (
        [str(tmp_path / "missing")]
        if spawn_error else [sys.executable, "-c", "pass"]
    )
    with transport._owned_process(argv, str(tmp_path)) as (output, _stderr, completion):
        receipt = completion(Event())
        assert ("error" in receipt) if spawn_error else receipt == {"returncode": 0}
        state = next(iter(reserved_owners.values()))
        assert not state["cleanup"] and state["process"].returncode is None
        os.kill(state["process"].pid, 0)
        ready, _, _ = select.select([output], [], [], 0.5)
        assert ready, "the live reserved owner retained a feed writer after spawn/child completion"
        assert os.read(output.fileno(), 1) == b""
        assert not state["cleanup"], "EOF observation must not release the reserved owner"


def test_stderr_drains_incrementally_while_child_and_owner_are_alive(
    reserved_owners, provider, monkeypatch, tmp_path,
):
    frame = publication()
    provider(
        "sys.stderr.write('incremental diagnostic\\n'); sys.stderr.flush()\n"
        f"print({json.dumps(frame)!r},flush=True)\n"
        "time.sleep(60)"
    )
    observed = Event()
    actual_read = os.read

    def read(fd, size):
        chunk = actual_read(fd, size)
        if chunk == b"incremental diagnostic\n":
            observed.set()
        return chunk

    monkeypatch.setattr(transport.os, "read", read)
    cancelled = Event()
    stream = transport.frames("example", "selected", "head-a", cancelled=cancelled, cwd=str(tmp_path))
    try:
        assert next(stream) == frame
        assert observed.wait(1), "small stderr waited for a full buffer or owner-held EOF"
        state = next(iter(reserved_owners.values()))
        assert not state["cleanup"] and not state["receipts"]
    finally:
        cancelled.set()
        stream.close()


def test_noncontained_stderr_io_failure_is_not_hidden_by_exit(
    reserved_owners, provider, monkeypatch, tmp_path,
):
    provider("sys.stderr.write('diagnostic'); sys.stderr.flush(); sys.exit(1)")
    actual_read = os.read

    def read(fd, size):
        if any(
            item["process"].stderr is not None and item["process"].stderr.fileno() == fd
            for item in reserved_owners.values()
        ):
            raise OSError("injected stderr read failure")
        return actual_read(fd, size)

    monkeypatch.setattr(transport.os, "read", read)
    stream = transport.frames("example", "selected", "head-a", cancelled=Event(), cwd=str(tmp_path))
    with pytest.raises(AgendaUnavailable, match="injected stderr read failure") as error:
        next(stream)
    assert not error.value.retry


@pytest.mark.parametrize("fault", ["malformed", "truncated", "oversized", "boolean"])
def test_noncontained_completion_receipt_faults_are_explicit(
    reserved_owners, provider, monkeypatch, tmp_path, fault,
):
    provider("sys.exit(0)")
    output = {
        "malformed": "'not-json\\n'",
        "truncated": "'{\"returncode\":0}'",
        "oversized": f"'x'*{posix.MAX_RECEIPT_BYTES + 1}+'\\n'",
        "boolean": "'{\"returncode\":true}\\n'",
    }[fault]
    owner = posix._OWNER.replace(
        'print(json.dumps({"returncode": child.wait()}), flush=True)',
        f"child.wait(); sys.stdout.write({output}); sys.stdout.flush()",
    )
    monkeypatch.setattr(transport, "_OWNER", owner)
    stream = transport.frames("example", "selected", "head-a", cancelled=Event(), cwd=str(tmp_path))
    start = time.monotonic()
    with pytest.raises(AgendaUnavailable, match="completion receipt") as error:
        next(stream)
    assert not error.value.retry
    assert time.monotonic() - start < 2.5


def test_noncontained_partial_stdout_cancellation_settles_all_readers(
    reserved_owners, provider, tmp_path,
):
    started = provider("sys.stdout.write('{'); sys.stdout.flush(); time.sleep(60)")
    cancelled, finished = Event(), Event()
    result = []
    stream = transport.frames("example", "selected", "head-a", cancelled=cancelled, cwd=str(tmp_path))

    def read():
        try:
            result.append(next(stream, None))
        finally:
            finished.set()

    reader = Thread(target=read, name="fixture-agenda-reader")
    reader.start()
    try:
        deadline = time.monotonic() + 3
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        cancelled.set()
        assert finished.wait(3)
        reader.join(timeout=1)
        assert result == [None]
    finally:
        cancelled.set()
        reader.join(timeout=3)
        stream.close()


@pytest.mark.parametrize("mode", ["output", "spawn-error", "timeout"])
def test_shared_owner_synchronous_run_preserves_output_error_and_timeout(
    reserved_owners, tmp_path, mode,
):
    if mode == "output":
        result = posix.run(
            [sys.executable, "-c", "import sys; print('payload'); print('diagnostic',file=sys.stderr);sys.exit(2)"],
            timeout=3, text=True, env=dict(os.environ), stderr=subprocess.PIPE,
        )
        assert result.returncode == 2
        assert result.stdout == "payload\n" and result.stderr == "diagnostic\n"
    elif mode == "spawn-error":
        with pytest.raises(OSError, match="No such file"):
            posix.run([str(tmp_path / "missing")], timeout=3, text=True,
                      env=dict(os.environ), stderr=subprocess.PIPE)
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            posix.run([sys.executable, "-c", "import time; time.sleep(60)"],
                      timeout=0.2, text=True, env=dict(os.environ), stderr=subprocess.PIPE)


def test_synchronous_partial_receipt_retains_deadline_and_reserved_owner(
    reserved_owners, monkeypatch,
):
    owner = posix._OWNER.replace(
        'print(json.dumps({"returncode": child.wait()}), flush=True)',
        """child.wait(); sys.stdout.write('{"returncode":'); sys.stdout.flush()""",
    )
    monkeypatch.setattr(posix, "_OWNER", owner)
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        posix.run([sys.executable, "-c", "print('output')"], timeout=0.2,
                  text=True, env=dict(os.environ), stderr=subprocess.PIPE)
    assert time.monotonic() - start < 1.5
