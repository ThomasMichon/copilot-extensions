"""Real admitted broker, TCP peers and native stdio children; never Docker."""

import contextlib
import json
import os
import socket
import sys
import threading
import time

import pytest
from agent_procutil import daemon_instances as instances

from agent_containers import docker_proxy as proxy
from agent_containers import docker_proxy_connection as custody


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "owned fixture did not settle"
        time.sleep(0.01)


def line(peer):
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = peer.recv(1)
        assert chunk
        data.extend(chunk)
    return bytes(data).replace(b"\r\n", b"\n")


@pytest.fixture
def broker(tmp_path, monkeypatch):
    endpoint = tmp_path / "proxy" / "endpoint.json"
    stop = threading.Event()
    returned = threading.Event()
    guards, processes, results, peers, listeners = [], [], [], [], []
    real_admit = instances.admit_daemon_instance
    real_spawn = custody.spawn_sync_in_kill_on_close_job
    real_accept = socket.socket.accept
    real_bind = socket.socket.bind
    failure = [KeyboardInterrupt]

    def admit(*args, **kwargs):
        guard = real_admit(*args, **kwargs)
        guards.append(guard)
        return guard

    def spawn(*args, **kwargs):
        process, job = real_spawn(*args, **kwargs)
        processes.append((process, job))
        return process, job

    def accept(listener):
        if threading.current_thread().name == "broker-test" and stop.is_set():
            raise failure[0]("synthetic listener failure")
        return real_accept(listener)

    def bind(listener, address):
        if threading.current_thread().name == "broker-test":
            listeners.append(listener)
        return real_bind(listener, address)

    def serve():
        try:
            results.append(proxy.serve("synthetic", "id", endpoint))
        except BaseException as error:
            results.append(error)
        finally:
            returned.set()

    monkeypatch.setattr(instances, "admit_daemon_instance", admit)
    monkeypatch.setattr(custody, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(socket.socket, "accept", accept)
    monkeypatch.setattr(socket.socket, "bind", bind)
    server = threading.Thread(target=serve, name="broker-test", daemon=True)

    def start(code, *, error=KeyboardInterrupt):
        failure[0] = error
        monkeypatch.setattr(proxy, "_docker_command", lambda _: [
            sys.executable, "-u", "-c", code,
        ])
        server.start()
        wait_for(lambda: endpoint.exists() or returned.is_set())
        assert endpoint.exists(), results
        return json.loads(endpoint.read_text())

    def connect(record, control=False):
        peer = socket.create_connection(
            ("127.0.0.1", record["control_port" if control else "port"]), timeout=15,
        )
        peers.append(peer)
        return peer

    def state():
        guard = guards[0]
        return next(
            row["state"]
            for row in instances.inspect_instances(guard.receipt.registry_root)
            if row["instance_id"] == guard.receipt.instance_id
        )

    def finished():
        assert returned.wait(12), "broker released neither child nor admission"
        server.join(2)
        assert not server.is_alive()
        assert state() == "finished"
        assert all(process.poll() is not None for process, _ in processes)
        assert all(job is None or not job.handle for _, job in processes)
        assert listeners and all(listener.fileno() == -1 for listener in listeners)
        assert not any(
            thread.name.startswith("container-ssh-") for thread in threading.enumerate()
        )

    yield start, connect, stop, finished, state, results, endpoint, processes
    stop.set()
    for peer in peers:
        with contextlib.suppress(OSError):
            peer.shutdown(socket.SHUT_RDWR)
        peer.close()
    # Exact fixture handles only; containment remains the final failure boundary.
    for process, job in processes:
        if job is not None and job.handle:
            job.terminate_and_wait(2)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    if server.ident is not None:
        assert returned.wait(12)
        server.join(2)


@pytest.mark.parametrize("error", [KeyboardInterrupt, OSError])
def test_interrupt_settles_actual_accepted_child_and_all_pumps(broker, error):
    start, connect, stop, finished, state, results, endpoint, _ = broker
    record = start("import time; print('ready',flush=True); time.sleep(60)", error=error)
    peer = connect(record)
    assert line(peer) == b"ready\n"
    stalled_control = connect(record, control=True)
    stalled_control.sendall(b"pi")
    assert state() == "running"
    stop.set()
    finished()
    assert isinstance(results[0], error)
    assert not endpoint.exists()
    assert peer.recv(1) == b""


def test_normal_stream_completion_drains_bytes_and_then_idles_out(broker, monkeypatch):
    start, connect, _, finished, _, results, endpoint, _ = broker
    monkeypatch.setattr(proxy, "_IDLE_EXIT_SECONDS", 0.05)
    record = start(
        "import sys; data=sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(data+b'EOF'); sys.stdout.buffer.flush()"
    )
    peer = connect(record)
    payload = b"ssh-binary\x00\xff" * 20000
    peer.sendall(payload)
    peer.shutdown(socket.SHUT_WR)
    data = bytearray()
    while chunk := peer.recv(65536):
        data.extend(chunk)
    assert data == payload + b"EOF"
    finished()
    assert results == [0]
    assert not endpoint.exists()


def test_replaced_endpoint_survives_shutdown(broker):
    start, connect, stop, finished, _, _, endpoint, _ = broker
    record = start("import time; print('ready',flush=True); time.sleep(60)")
    assert line(connect(record)) == b"ready\n"
    replacement = {"pid": os.getpid() + 100000, "port": 42}
    endpoint.write_text(json.dumps(replacement))
    stop.set()
    finished()
    assert json.loads(endpoint.read_text()) == replacement


def test_repeated_cleanup_interruptions_keep_admission_running(broker, monkeypatch):
    start, connect, stop, finished, state, _, _, _ = broker
    record = start("import time; print('ready',flush=True); time.sleep(60)")
    assert line(connect(record)) == b"ready\n"
    original_close = proxy.close_socket
    attempts = []

    def close(connection):
        if len(attempts) < 3:
            attempts.append(state())
            raise KeyboardInterrupt("cleanup interrupted")
        original_close(connection)

    monkeypatch.setattr(proxy, "close_socket", close)
    stop.set()
    finished()
    assert attempts == ["running"] * 3


def test_native_wait_errors_retry_until_dead_child_is_confirmed(broker, monkeypatch):
    start, connect, stop, finished, state, _, _, processes = broker
    record = start("import time; print('ready',flush=True); time.sleep(60)")
    assert line(connect(record)) == b"ready\n"
    process, _ = processes[0]
    original_wait = process.wait
    attempts = []

    def wait(timeout=None):
        if timeout == 2 and len(attempts) < 3:
            attempts.append(state())
            raise (KeyboardInterrupt, OSError, RuntimeError)[len(attempts) - 1](
                "synthetic wait interruption"
            )
        return original_wait(timeout=timeout)

    monkeypatch.setattr(process, "wait", wait)
    stop.set()
    finished()
    assert attempts == ["running"] * 3


def test_repeated_close_after_finished_connection_is_idempotent(broker):
    start, connect, stop, finished, _, _, _, _ = broker
    record = start("import time; print('ready',flush=True); time.sleep(60)")
    assert line(connect(record)) == b"ready\n"
    stop.set()
    finished()
    left, right = socket.socketpair()
    owned = custody.Connection(left, [], lambda *_: None, lambda *_: None)
    try:
        owned.stop()
        owned.stop()
        assert left.fileno() == -1
    finally:
        right.close()


@pytest.mark.parametrize("failure", [
    "spawn", "retained", "container-ssh-inbound", "container-ssh-outbound",
])
def test_connection_startup_failure_settles_known_child(broker, monkeypatch, failure):
    start, connect, stop, finished, _, _, _, processes = broker
    original_start = threading.Thread.start

    def launch(thread):
        if thread.name == failure:
            raise RuntimeError("synthetic pump startup failure")
        original_start(thread)

    if failure == "spawn":
        def refuse(*args, **kwargs):
            raise OSError("synthetic child creation refusal")

        monkeypatch.setattr(custody, "spawn_sync_in_kill_on_close_job", refuse)
    elif failure == "retained":
        original_spawn = custody.spawn_sync_in_kill_on_close_job

        def retain(*args, **kwargs):
            process, job = original_spawn(*args, **kwargs)
            raise custody.OwnedCleanupError(process, job)

        monkeypatch.setattr(custody, "spawn_sync_in_kill_on_close_job", retain)
    else:
        monkeypatch.setattr(threading.Thread, "start", launch)
    record = start("import time; time.sleep(60)")
    peer = connect(record)
    assert peer.recv(1) == b""
    stop.set()
    finished()
    assert len(processes) == (0 if failure == "spawn" else 1)


@pytest.mark.parametrize("name", [
    "container-ssh-connection", "container-ssh-inbound", "container-ssh-outbound",
])
def test_interrupt_after_actual_worker_start_waits_for_its_target(broker, monkeypatch, name):
    start, connect, stop, finished, _, _, _, _ = broker
    original_start = threading.Thread.start

    def launch(thread):
        original_start(thread)
        if thread.name == name:
            raise KeyboardInterrupt("synthetic post-start interruption")

    monkeypatch.setattr(threading.Thread, "start", launch)
    record = start("import time; time.sleep(60)")
    assert connect(record).recv(1) == b""
    stop.set()
    finished()


@pytest.mark.parametrize("failure", ["publish", "container-ssh-control", "container-ssh-connection"])
def test_listener_or_worker_startup_failure_closes_intake(broker, monkeypatch, failure):
    start, connect, _, finished, _, results, endpoint, _ = broker
    if failure == "publish":
        original_replace = proxy.os.replace

        def refuse(source, destination):
            if destination == endpoint:
                raise OSError("synthetic publish failure")
            return original_replace(source, destination)

        monkeypatch.setattr(proxy.os, "replace", refuse)
        with pytest.raises(AssertionError):
            start("pass")
    else:
        original_start = threading.Thread.start

        def launch(thread):
            if thread.name == failure:
                raise RuntimeError("synthetic worker startup failure")
            original_start(thread)

        monkeypatch.setattr(threading.Thread, "start", launch)
        if failure.endswith("control"):
            with contextlib.suppress(AssertionError):
                start("pass")
        else:
            record = start("pass")
            assert connect(record).recv(1) == b""
    finished()
    assert isinstance(results[0], (OSError, RuntimeError))
    assert not endpoint.exists()
    assert not list(endpoint.parent.glob("*.tmp"))


def test_windows_job_retires_pipe_holding_descendant_after_leader_exit(broker):
    if os.name != "nt":
        pytest.skip("Windows strict Job owns descendants; POSIX retains inherited group")
    start, connect, _, finished, _, results, _, _ = broker
    code = (
        "import subprocess,sys;"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "creationflags=subprocess.CREATE_NO_WINDOW);"
        "print(p.pid,flush=True)"
    )
    record = start(code)
    peer = connect(record)
    # The descendant inherits the leader's stdout pipe; a dead leader alone
    # cannot unblock the outbound pump. Empty Job confirmation must do so.
    line(peer)
    wait_for(lambda: peer.recv(1) == b"")
    broker[2].set()
    finished()
    assert isinstance(results[0], KeyboardInterrupt)


def test_posix_inherited_pipe_descendant_must_finish_before_admission(broker, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX inherited-group boundary")
    start, connect, _, finished, state, results, _, _ = broker
    monkeypatch.setattr(proxy, "_IDLE_EXIT_SECONDS", 0.05)
    record = start(
        "import subprocess,sys;"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(0.5)']);"
        "print(p.pid,flush=True)"
    )
    peer = connect(record)
    line(peer)
    assert state() == "running"
    assert peer.recv(1) == b""
    finished()
    assert results == [0]
