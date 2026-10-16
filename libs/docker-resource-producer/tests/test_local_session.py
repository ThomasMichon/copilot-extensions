"""Real local stream fixtures: one operation cannot reconnect onto daemon B."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from docker_resource_producer import Endpoint, Identity, VerificationError, birth_labels
from docker_resource_producer.local_session import LocalDaemonAdmission, LocalDaemonSession
from docker_resource_producer.verification import LocalDockerVerifier

TOKEN = "r" * 43
CREATOR = "machine/project/creator"
IDENTIFIER = "a" * 64
CREATED = "2026-10-13T00:00:00.123456789Z"


class FixtureDaemon:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.accepts = 0
        self.calls: list[tuple[str, str]] = []
        self.resources = {IDENTIFIER: {
            "Id": IDENTIFIER, "Created": CREATED, "Config": {"Labels": birth_labels(TOKEN, CREATOR, 1)},
        }}
        self.behavior = ""
        self.error: BaseException | None = None
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        path.chmod(0o600)
        self.listener.listen(2)
        self.listener.settimeout(0.1)
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        try:
            while not self.stopping.is_set():
                try:
                    connection, _ = self.listener.accept()
                except socket.timeout:
                    continue
                self.accepts += 1
                connection.settimeout(2)
                with connection, connection.makefile("rb") as source:
                    while not self.stopping.is_set():
                        line = source.readline(8192)
                        if not line:
                            break
                        method, target, _ = line.decode("ascii").split()
                        while source.readline(8192) != b"\r\n":
                            pass
                        self.calls.append((method, target))
                        route = urlsplit(target).path
                        if self.behavior == "stall" and route != "/info":
                            self.stopping.wait(0.2)
                            break
                        if self.behavior == "disconnect" and route != "/info":
                            break
                        if route == "/info":
                            result = {"ID": "daemon-a" if self.accepts == 1 else "daemon-b"}
                        elif route == "/containers/json":
                            result = list(self.resources.values())
                        elif route == "/volumes":
                            result = {"Volumes": [], "Warnings": []}
                        elif method == "DELETE":
                            if self.behavior != "retain":
                                self.resources.pop(IDENTIFIER, None)
                            connection.sendall(b"HTTP/1.1 204 No Content\r\n\r\n")
                            continue
                        else:
                            result = self.resources[IDENTIFIER]
                        payload = json.dumps(result).encode()
                        if self.behavior == "mismatch" and route == "/info":
                            payload = b'{"ID":"daemon-b"}'
                        if route != "/info" and self.behavior in {
                            "close", "oversize", "duplicate", "truncated", "redirect", "malformed", "warning",
                            "chunk-missing-trailer-end", "chunk-bad-delimiter",
                        }:
                            if self.behavior == "close":
                                connection.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\n[]")
                            elif self.behavior == "oversize":
                                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 99999999\r\n\r\n")
                            elif self.behavior == "duplicate":
                                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n[]")
                            elif self.behavior == "redirect":
                                connection.sendall(b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\n\r\n")
                            elif self.behavior == "malformed":
                                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\n{")
                            elif self.behavior == "warning":
                                payload = b'{"Volumes":[],"Warnings":["incomplete"]}'
                                connection.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
                            elif self.behavior == "chunk-missing-trailer-end":
                                connection.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n[]\r\n0\r\n")
                            elif self.behavior == "chunk-bad-delimiter":
                                connection.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n[]XX0\r\n\r\n")
                            else:
                                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n[]")
                            break
                        if self.behavior == "chunked":
                            connection.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                                               + f"{len(payload):x}\r\n".encode() + payload + b"\r\n0\r\n\r\n")
                        else:
                            connection.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
        except (OSError, KeyError, ValueError) as error:
            if not self.stopping.is_set():
                self.error = error

    def close(self) -> None:
        self.stopping.set()
        self.thread.join(timeout=3)
        self.listener.close()
        assert not self.thread.is_alive()
        assert self.error is None, self.error


@pytest.fixture
def daemon():
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        pytest.skip("Authoritative direct Unix adapter is POSIX-only; other routes fail closed")
    with tempfile.TemporaryDirectory(prefix="ce-d-") as directory:
        fixture = FixtureDaemon(Path(directory) / "d.sock")
        try:
            yield fixture
        finally:
            fixture.close()


def verifier(daemon: FixtureDaemon, *, timeout: float = 2) -> LocalDockerVerifier:
    host = "unix://" + str(daemon.path)
    return LocalDockerVerifier(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host), timeout=timeout)


def identity() -> Identity:
    return Identity("container", IDENTIFIER, CREATED, "daemon-a", TOKEN, CREATOR, 1)


def test_exact_removal_and_absence_share_one_connection(daemon: FixtureDaemon) -> None:
    assert verifier(daemon).remove(identity(), authorized=True) is True
    assert daemon.resources == {}
    assert daemon.accepts == 1
    assert daemon.calls == [
        ("GET", "/info"), ("GET", "/containers/json?all=1"),
        ("GET", f"/containers/{IDENTIFIER}/json"), ("DELETE", f"/containers/{IDENTIFIER}?force=1"),
        ("GET", "/containers/json?all=1"),
    ]


@pytest.mark.parametrize("chunked", [False, True])
def test_probe_keeps_daemon_a_despite_new_connections_routing_b(daemon: FixtureDaemon, chunked: bool) -> None:
    daemon.behavior = "chunked" if chunked else ""
    assert verifier(daemon).probe(identity()) is True
    assert daemon.accepts == 1
    with pytest.raises(VerificationError):
        verifier(daemon).probe(identity())
    assert daemon.accepts == 2


@pytest.mark.parametrize("behavior", ["disconnect", "close", "oversize", "duplicate", "truncated", "redirect", "malformed", "mismatch"])
def test_unknown_stream_never_reconnects_or_mutates(daemon: FixtureDaemon, behavior: str) -> None:
    daemon.behavior = behavior
    with pytest.raises(VerificationError):
        verifier(daemon).remove(identity(), authorized=True)
    assert daemon.accepts == 1
    assert IDENTIFIER in daemon.resources
    assert all(method != "DELETE" for method, _target in daemon.calls)


@pytest.mark.parametrize("behavior", ["chunk-missing-trailer-end", "chunk-bad-delimiter"])
def test_chunk_framing_failure_cannot_prove_absence(daemon: FixtureDaemon, behavior: str) -> None:
    daemon.behavior = behavior
    with pytest.raises(VerificationError):
        verifier(daemon).probe(identity())
    assert daemon.accepts == 1
    assert IDENTIFIER in daemon.resources


def test_volume_search_warning_is_not_complete_absence(daemon: FixtureDaemon) -> None:
    daemon.behavior = "warning"
    host = "unix://" + str(daemon.path)
    with LocalDaemonSession(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host)) as session:
        with pytest.raises(VerificationError, match="volume inventory"):
            verifier(daemon)._inventory(session, "volume")


def test_retained_container_refuses_cleanup_success(daemon: FixtureDaemon) -> None:
    daemon.behavior = "retain"
    with pytest.raises(VerificationError, match="did not establish"):
        verifier(daemon).remove(identity(), authorized=True)
    assert daemon.accepts == 1


def test_changed_birth_identity_never_deletes(daemon: FixtureDaemon) -> None:
    with pytest.raises(VerificationError, match="birth identity"):
        verifier(daemon).remove(replace(identity(), created="2026-10-14T00:00:00Z"), authorized=True)
    assert all(method != "DELETE" for method, _target in daemon.calls)


def test_authority_and_volume_refusal_precede_any_connection(daemon: FixtureDaemon) -> None:
    with pytest.raises(VerificationError, match="not authorized"):
        verifier(daemon).remove(identity())
    volume = replace(identity(), resource_type="volume", resource_id="retained-volume")
    with pytest.raises(VerificationError, match="birth-conditional"):
        verifier(daemon).remove(volume, authorized=True)
    assert daemon.accepts == 0


@pytest.mark.parametrize("host", ["tcp://localhost:2375", "ssh://example.invalid", "npipe:////./pipe/docker_engine", "unix://relative"])
def test_remote_proxy_and_unavailable_routes_are_not_admitted(host: str) -> None:
    with pytest.raises(VerificationError):
        LocalDaemonAdmission(host)


def test_claim_endpoint_cannot_supply_its_own_route_admission() -> None:
    with pytest.raises(VerificationError, match="does not match"):
        LocalDaemonSession(Endpoint("unix:///claim.sock", "daemon-a"), LocalDaemonAdmission("unix:///trusted.sock"))


def test_writable_socket_refuses_before_daemon_request(daemon: FixtureDaemon) -> None:
    daemon.path.chmod(0o666)
    with pytest.raises(VerificationError):
        verifier(daemon).probe(identity())
    assert daemon.accepts == 0


def test_absolute_deadline_closes_and_prevents_session_reuse(daemon: FixtureDaemon) -> None:
    daemon.behavior = "stall"
    started = time.monotonic()
    with pytest.raises(VerificationError):
        verifier(daemon, timeout=0.1).probe(identity())
    assert time.monotonic() - started < 1
    assert daemon.accepts == 1


def test_completed_session_cannot_reconnect(daemon: FixtureDaemon) -> None:
    host = "unix://" + str(daemon.path)
    session = LocalDaemonSession(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host))
    with session:
        assert session.request("GET", "/containers/json?all=1")
    with pytest.raises(VerificationError, match="cannot reconnect"):
        with session:
            pass
    assert daemon.accepts == 1


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf"), 31])
def test_invalid_deadline_refuses_without_a_connection(timeout: float) -> None:
    with pytest.raises(VerificationError, match="deadline"):
        LocalDaemonSession(Endpoint("unix:///trusted.sock", "daemon-a"),
                           LocalDaemonAdmission("unix:///trusted.sock"), timeout=timeout)


def test_symlink_socket_refuses_before_connect(daemon: FixtureDaemon) -> None:
    link = daemon.path.parent / "link.sock"
    link.symlink_to(daemon.path)
    host = "unix://" + str(link)
    with pytest.raises(VerificationError):
        LocalDockerVerifier(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host)).probe(identity())
    assert daemon.accepts == 0


def test_platform_without_admitted_adapter_refuses(monkeypatch) -> None:
    from docker_resource_producer import local_session
    session = LocalDaemonSession(Endpoint("unix:///trusted.sock", "daemon-a"), LocalDaemonAdmission("unix:///trusted.sock"))
    monkeypatch.setattr(local_session, "_DIRECT_LOCAL_SUPPORTED", False)
    with pytest.raises(VerificationError, match="unavailable on this platform"):
        with session:
            pass
