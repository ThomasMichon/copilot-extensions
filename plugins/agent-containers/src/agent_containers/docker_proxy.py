"""Windowless localhost broker for trusted-container OpenSSH transport."""

from __future__ import annotations

from agent_procutil.daemon_instances import DaemonInstanceSpec, daemon_entrypoint

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO

from agent_procutil import spawn_daemon

from .docker_proxy_connection import Connection, Worker, close_socket, settle

_IDLE_EXIT_SECONDS = 120.0


def _docker_command(container: str) -> list[str]:
    return [
        "docker",
        "exec",
        "-i",
        "-u",
        "root",
        container,
        "/usr/sbin/sshd",
        "-i",
        "-e",
        "-o",
        "GatewayPorts=no",
    ]


def _socket_to_stream(source: socket.socket, destination: BinaryIO) -> None:
    try:
        while data := source.recv(65536):
            _write_stream(destination, data)
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError):
            destination.close()


def _stream_to_socket(source: BinaryIO, destination: socket.socket) -> None:
    try:
        while data := _read_stream(source):
            destination.sendall(data)
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError):
            destination.shutdown(socket.SHUT_WR)


def _read_stream(source: BinaryIO) -> bytes:
    try:
        return os.read(source.fileno(), 65536)
    except (AttributeError, OSError):
        read1 = getattr(source, "read1", None)
        return read1(65536) if read1 is not None else source.read(65536)


def _write_stream(destination: BinaryIO, data: bytes) -> None:
    try:
        fileno = destination.fileno()
    except (AttributeError, OSError):
        destination.write(data)
        destination.flush()
        return
    view = memoryview(data)
    while view:
        written = os.write(fileno, view)
        if written <= 0:
            raise BrokenPipeError("container SSH broker pipe closed")
        view = view[written:]


def _serve_connection(container: str, connection: socket.socket) -> None:
    owned = Connection(
        connection, _docker_command(container), _socket_to_stream, _stream_to_socket,
    )
    owned.run()
    if owned.error is not None:
        raise owned.error


def _recv_line(connection: socket.socket, *, limit: int = 16) -> bytes:
    data = bytearray()
    while len(data) < limit:
        chunk = connection.recv(limit - len(data))
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in chunk:
            break
    return bytes(data)


def _serve_control(listener: socket.socket, stopping: threading.Event | None = None) -> None:
    while True:
        try:
            connection, _address = listener.accept()
        except OSError:
            return
        with connection:
            try:
                if stopping is not None:
                    connection.settimeout(0.2)
                    if stopping.is_set():
                        return
                if _recv_line(connection) == b"ping\n":
                    connection.sendall(b"pong\n")
            except (ConnectionError, OSError):
                pass


@daemon_entrypoint(
    "agent-containers-docker-proxy", source=__file__, purpose="production",
    runtime_root=lambda container, container_id, endpoint_file: Path(os.path.abspath(endpoint_file)).parent,
)
def serve(container: str, container_id: str, endpoint_file: Path) -> int:
    endpoint_file = Path(os.path.abspath(endpoint_file))
    listeners: list[socket.socket] = []
    connections: list[Connection] = []
    control: Worker | None = None
    stopping = threading.Event()
    temporary = endpoint_file.with_name(f".{endpoint_file.name}.{os.getpid()}.tmp")
    last_connection = time.monotonic()
    try:
        for _ in range(2):
            listener = socket.socket()
            listeners.append(listener)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
        data_listener, control_listener = listeners
        data_listener.settimeout(1.0)
        endpoint_file.parent.mkdir(parents=True, exist_ok=True)
        endpoint = {
            "schema_version": 1,
            "container": container,
            "container_id": container_id,
            "runtime": str(Path(__file__).resolve()),
            "pid": os.getpid(),
            "port": data_listener.getsockname()[1],
            "control_port": control_listener.getsockname()[1],
        }
        temporary.write_text(json.dumps(endpoint), encoding="utf-8")
        os.replace(temporary, endpoint_file)
        control = Worker(
            lambda: _serve_control(control_listener, stopping), "container-ssh-control",
        )
        control.start()
        while True:
            completed = [owned for owned in connections if owned.worker.done.is_set()]
            for owned in completed:
                settle(owned.worker.join)
                connections.remove(owned)
                last_connection = time.monotonic()
            try:
                connection, _address = data_listener.accept()
            except TimeoutError:
                if (
                    not connections
                    and time.monotonic() - last_connection >= _IDLE_EXIT_SECONDS
                ):
                    return 0
                continue
            try:
                owned = Connection(
                    connection, _docker_command(container), _socket_to_stream, _stream_to_socket,
                )
            except BaseException:
                settle(lambda: close_socket(connection))
                raise
            connections.append(owned)
            last_connection = time.monotonic()
            owned.worker.start()
    finally:
        def cleanup() -> None:
            stopping.set()
            for listener in listeners:
                settle(lambda listener=listener: close_socket(listener))
            for owned in connections:
                settle(owned.stop)
            for owned in connections:
                settle(owned.worker.join)
            if control is not None:
                settle(control.join)
            temporary.unlink(missing_ok=True)

        settle(cleanup)
        try:
            published = json.loads(endpoint_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            published = None
        if isinstance(published, dict) and published.get("pid") == os.getpid():
            endpoint_file.unlink(missing_ok=True)


def _healthy_endpoint(endpoint: object, container: str, container_id: str) -> bool:
    if (
        not isinstance(endpoint, dict)
        or endpoint.get("schema_version") != 1
        or endpoint.get("container") != container
        or endpoint.get("container_id") != container_id
        or endpoint.get("runtime") != str(Path(__file__).resolve())
    ):
        return False
    try:
        port = int(endpoint["port"])
        control_port = int(endpoint["control_port"])
        if not (1 <= port <= 65535 and 1 <= control_port <= 65535):
            return False
        with socket.create_connection(
            ("127.0.0.1", control_port),
            timeout=0.5,
        ) as connection:
            connection.sendall(b"ping\n")
            return _recv_line(connection) == b"pong\n"
    except (KeyError, TypeError, ValueError, OSError):
        return False


def ensure_broker(
    container: str,
    container_id: str,
    endpoint_file: Path,
    *,
    timeout: float = 10.0,
) -> int:
    endpoint_file = Path(os.path.abspath(endpoint_file))
    try:
        current = json.loads(endpoint_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        current = None
    if _healthy_endpoint(current, container, container_id):
        return int(current["port"])

    process = spawn_daemon(
        [
            sys.executable,
            "-m",
            "agent_containers.docker_proxy",
            "serve",
            container,
            container_id,
            str(endpoint_file),
        ],
        instance=DaemonInstanceSpec(
            "agent-containers-docker-proxy", endpoint_file.parent, Path(__file__).resolve(),
        ),
        log_path=endpoint_file.parent / "docker-proxy.log",
        # Never the caller's ambient cwd: this is a long-lived detached
        # proxy daemon that may outlive whatever repo/worktree checkout the
        # caller happened to be running from (service-lifecycle-
        # supervision's "nothing pins the plugin payload" rule, generalized
        # to every deletable checkout). It locates everything it needs via
        # absolute arguments (`endpoint_file`), so its cwd is irrelevant to
        # its job -- root it at HOME.
        cwd=os.path.expanduser("~"),
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            current = json.loads(endpoint_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            current = None
        if _healthy_endpoint(current, container, container_id):
            return int(current["port"])
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    try:
        published = json.loads(endpoint_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        published = None
    if isinstance(published, dict) and published.get("pid") == process.pid:
        endpoint_file.unlink(missing_ok=True)
    raise RuntimeError("container SSH broker did not become ready")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve")
    serve_parser.add_argument("container")
    serve_parser.add_argument("container_id")
    serve_parser.add_argument("endpoint_file", type=Path)
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(args.container, args.container_id, args.endpoint_file)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
