"""Actual production POSIX owner path inside the runner's outer containment."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import os
from queue import Queue
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
from test_agenda_end_to_end import SOURCE_PATHS, chain as chain
from test_companion_agenda_live import current_data, until, widget

pytestmark = pytest.mark.skipif(os.name == "nt", reason="production POSIX group-owner path")
_MODULE_HEALTH_ARGV = transport.health_argv


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


def test_noncontained_paused_consumer_receives_queued_error_before_exit_fallback(
    reserved_owners, provider, tmp_path,
):
    first, update = publication(), publication(1, kind="update", revision=2)
    error_frame = {
        "type": "error", "ok": False, "contract_version": 1,
        "error": {"code": "unavailable", "message": "queued transient failure"},
    }
    provider(
        f"print({json.dumps(first)!r},flush=True)\n"
        f"print({json.dumps(update)!r},flush=True)\n"
        f"print({json.dumps(error_frame)!r},flush=True)\n"
        "sys.exit(1)"
    )
    stream = transport.frames("example", "selected", "head-a", cancelled=Event(), cwd=str(tmp_path))
    receiver = AgendaFeed("example", "selected", "head-a")
    try:
        assert receiver.receive(next(stream)) == first
        # Ensure the actual child completion is known before yielding the update.
        deadline = time.monotonic() + 2
        while not next(iter(reserved_owners.values()))["receipts"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert next(iter(reserved_owners.values()))["receipts"] == [{"returncode": 1}]
        for thread in threads():
            if thread.name == "agenda-feed-completion":
                thread.join(timeout=1)
                assert not thread.is_alive()
        assert receiver.receive(next(stream)) == update
        time.sleep(transport.EXIT_GRACE_SECONDS + 0.2)
        assert next(stream) == error_frame
        with pytest.raises(AgendaUnavailable, match="queued transient failure") as error:
            receiver.receive(error_frame)
        assert error.value.retry and error.value.code == "unavailable"
    finally:
        stream.close()


@pytest.mark.parametrize("cancel", [False, True])
def test_noncontained_backlog_stays_bounded_and_preserves_delayed_frames(
    reserved_owners, provider, monkeypatch, tmp_path, cancel,
):
    frames = [publication()] + [
        publication(sequence, kind="update", revision=sequence + 1)
        for sequence in range(1, 64)
    ]
    error_frame = {
        "type": "error", "ok": False, "contract_version": 1,
        "error": {"code": "disconnected", "message": "end of bounded backlog"},
    }
    provider(
        f"frames=json.loads({json.dumps(frames + [error_frame])!r})\n"
        "for frame in frames:\n    print(json.dumps(frame),flush=True)\n"
        "sys.exit(1)"
    )
    queues = []

    class BoundedQueue(Queue):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.high_watermark = 0
            queues.append(self)

        def put(self, *args, **kwargs):
            value = super().put(*args, **kwargs)
            self.high_watermark = max(self.high_watermark, self.qsize())
            return value

    actual_owned = transport._owned_process

    class DelayedReader:
        def __init__(self, output):
            self.output = output

        def readline(self, size):
            value = self.output.readline(size)
            time.sleep(0.01)
            return value

    @contextmanager
    def delayed_owned(*args, **kwargs):
        with actual_owned(*args, **kwargs) as (output, stderr, completion):
            yield DelayedReader(output), stderr, completion

    monkeypatch.setattr(transport, "Queue", BoundedQueue)
    monkeypatch.setattr(transport, "_owned_process", delayed_owned)
    cancelled = Event()
    stream = transport.frames("example", "selected", "head-a", cancelled=cancelled, cwd=str(tmp_path))
    try:
        assert next(stream) == frames[0]
        assert next(stream) == frames[1]
        time.sleep(transport.EXIT_GRACE_SECONDS + 0.2)
        assert len(queues) == 1 and queues[0].maxsize == 2 and queues[0].high_watermark == 2
        if cancel:
            cancelled.set()
            assert next(stream, None) is None
        else:
            for frame in frames[2:]:
                assert next(stream) == frame
            assert next(stream) == error_frame
        assert queues[0].high_watermark == 2
    finally:
        cancelled.set()
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


def test_actual_helper_module_cannot_dispatch_and_is_rejected_despite_healthy_resident(
    reserved_owners, chain, monkeypatch,
):
    base = [sys.executable, "-m", "agent_worktrees.current_roster_entry"]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(source) for source in SOURCE_PATHS)
    raw = posix.run(
        [*base, "agenda-feed", "--project", "example", "--worktree", "selected",
         "--session", "head-a", "--json"],
        timeout=3, text=True, env=env, stderr=subprocess.PIPE,
    )
    assert raw.returncode == 0 and raw.stdout == "" and raw.stderr == ""
    # The real private resident can prove support, but that does not make the
    # helper module an executable command entry.
    transport.require_capability(chain["argv"], cancelled=Event(), cwd=str(chain["workdir"]))
    probe_calls = []

    def healthy_probe(_base):
        probe_calls.append(True)
        return [*chain["argv"], "--resident-health"]

    monkeypatch.setattr(transport, "health_argv", healthy_probe)
    monkeypatch.setattr(companion.ec, "engine_base_command", lambda: list(base))
    admitted = []
    stream = transport.frames(
        "example", "selected", "head-a", cancelled=Event(), cwd=str(chain["workdir"]),
        on_supported=lambda: admitted.append(True),
    )
    with pytest.raises(AgendaUnavailable, match="unsupported owned agenda module entry") as error:
        next(stream)
    assert error.value.code == "unsupported-entry" and not error.value.retry
    assert not probe_calls and not admitted
    assert len(reserved_owners) == 1
    assert all(action == "health" for action, _payload in chain["actions"])


@pytest.fixture
def actual_module_runtime(chain, monkeypatch):
    from agent_worktrees import status_monitor_cutover

    bootstrap = chain["workdir"].parent / "module-imports"
    bootstrap.mkdir()
    runtime = chain["workdir"].parent / "module-route"
    runtime.mkdir()
    status_monitor_cutover.control_token_path(runtime).write_text(
        chain["server"].token, encoding="utf-8",
    )
    startup = f"""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]={list(map(str, SOURCE_PATHS))!r}
from agent_worktrees import agenda_cli,config,front_door_cli,invocation_project,tracking,worktrees_client
from agent_worktrees.status_monitor_cutover import ControlClient
def client():
    return ControlClient({chain["endpoint"]!r},token={chain["server"].token!r},timeout=2)
def project_guard(project,command):
    if project not in (None,"example"):
        raise ValueError("fixture project selection mismatch")
def project_discovery(args,project,*hooks):
    project_guard(project,None)
    return True
config.project_name=lambda:"example"
config.tracking_dir=lambda:Path({str(chain["record_path"].parent)!r})
config.install_dir=lambda:Path({str(runtime)!r})
front_door_cli._guard_project_scope=project_guard
front_door_cli.resolve_invocation_project=project_discovery
invocation_project.resolve_invocation_project=project_discovery
agenda_cli._control_client=client
worktrees_client._control_client=client
worktrees_client.resident_endpoint=lambda root:SimpleNamespace(base_url={chain["endpoint"]!r})
"""
    (bootstrap / "sitecustomize.py").write_text(startup, encoding="utf-8")
    actual_env = transport._engine_environment

    def environment():
        env = actual_env()
        env["PYTHONPATH"] = os.pathsep.join([str(bootstrap), *map(str, SOURCE_PATHS)])
        return env

    monkeypatch.setattr(transport, "_engine_environment", environment)
    monkeypatch.setattr(
        transport, "_HEALTH_PROBE",
        f"import sys; sys.path[:0]=[{str(bootstrap)!r}]; import sitecustomize\n" + transport._HEALTH_PROBE,
    )
    monkeypatch.setattr(transport, "health_argv", _MODULE_HEALTH_ARGV)
    return chain


@pytest.mark.parametrize("entry", ["agent_worktrees", "agent_worktrees.__main__", "console"])
def test_actual_executable_module_and_declared_console_dispatch_agenda(
    reserved_owners, actual_module_runtime, monkeypatch, entry,
):
    chain = actual_module_runtime
    if entry == "console":
        base = [
            sys.executable, "-c",
            "from agent_worktrees.current_roster_entry import console_entry; console_entry()",
        ]
        # This known fixture console callable declares its owned health argv;
        # production never guesses a probe from an opaque -c/script command.
        monkeypatch.setattr(
            transport, "health_argv",
            lambda selected: [selected[0], "-I", "-c", transport._HEALTH_PROBE],
        )
    else:
        base = [sys.executable, "-m", entry]
    monkeypatch.setattr(companion.ec, "engine_base_command", lambda: list(base))
    stream = transport.frames(
        "example", "selected", "head-a", cancelled=Event(), cwd=str(chain["workdir"]),
    )
    try:
        snapshot = next(stream)
        assert snapshot["type"] == "snapshot" and snapshot["availability"] == "not-published"
        assert snapshot["generation"] == chain["owner"].store.generation
        assert snapshot["project"] == "example" and snapshot["session_id"] == "head-a"
        assert chain["owner"].agendas.feed.active_count() == 1
    finally:
        stream.close()
    assert chain["owner"].agendas.feed.wait_closed(3)


def test_actual_declared_console_preserves_original_roster_feed(
    reserved_owners, actual_module_runtime,
):
    chain = actual_module_runtime
    argv = [
        sys.executable, "-c",
        "from agent_worktrees.current_roster_entry import console_entry; console_entry()",
        "--project", "example", "list", "--json", "--current", "--stream", "--follow",
    ]
    with transport._owned_process(argv, str(chain["workdir"])) as (output, _stderr, _completion):
        ready, _, _ = select.select([output], [], [], 3)
        assert ready, "the actual console roster entry did not dispatch"
        frame = json.loads(output.readline(80 * 1024 + 1))
        assert frame["type"] == "begin", frame
        assert frame["projection"]["generation"] == chain["owner"].store.generation


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
