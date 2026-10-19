"""Real local stream fixtures: one operation cannot reconnect onto daemon B."""
from __future__ import annotations

import io
import json
import os
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from docker_resource_producer import Endpoint, Identity, VerificationError, birth_labels
from docker_resource_producer.local_session import LocalDaemonAdmission, LocalDaemonSession
from docker_resource_producer.models import LABEL_TOKEN
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
        self.volumes: dict[str, dict] = {}
        self.failure_route: str | None = None
        self.behavior = ""
        self.warning: object = None
        self.connection: socket.socket | None = None
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
                self.connection = connection
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
                        if route == self.failure_route:
                            connection.sendall(b"HTTP/1.1 500 Error\r\nContent-Length: 0\r\n\r\n")
                            continue
                        if self.behavior == "stall" and route != "/info":
                            self.stopping.wait(0.2)
                            break
                        if self.behavior == "disconnect" and route != "/info":
                            break
                        if route == "/info":
                            result = {"ID": "daemon-a" if self.accepts == 1 else "daemon-b"}
                        elif route == "/containers/json":
                            result = self.inventory(target, "container")
                        elif route == "/volumes":
                            result = {"Volumes": self.inventory(target, "volume"), "Warnings": []}
                            if self.behavior == "custom-warning":
                                result["Warnings"] = self.warning
                            elif self.behavior == "warning":
                                result["Warnings"] = ["incomplete"]
                        elif method == "DELETE":
                            identifier = route.split("/")[2]
                            resource = self.resources.get(identifier) or next((
                                value for value in self.resources.values()
                                if value.get("Name") == "/" + identifier
                            ), None)
                            if resource is not None:
                                self.resources.pop(resource["Id"])
                            connection.sendall(b"HTTP/1.1 204 No Content\r\n\r\n")
                            continue
                        else:
                            result = (
                                self.volumes[route.removeprefix("/volumes/")]
                                if route.startswith("/volumes/") else self.resources[route.split("/")[2]]
                            )
                            if self.behavior == "name-collision":
                                self.resources.pop(IDENTIFIER)
                        payload = json.dumps(result).encode()
                        if self.behavior == "unsolicited" and route == "/info":
                            first = f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload
                            connection.sendall(first + b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]")
                            continue
                        if (self.behavior == "nested-info" and route == "/info") or (
                            self.behavior == "nested-inventory" and route == "/containers/json"
                        ):
                            payload = b"[" * 10000 + b"0" + b"]" * 10000
                        if self.behavior == "padding" and route == "/info":
                            first = f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload
                            connection.sendall(first + b"X" * (8192 - len(first))
                                               + b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]")
                            continue
                        if self.behavior == "mismatch" and route == "/info":
                            payload = b'{"ID":"daemon-b"}'
                        if route != "/info" and self.behavior in {
                            "close", "oversize", "duplicate", "truncated", "redirect", "malformed",
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
            expected_rejection = self.behavior in {"padding", "unsolicited"} and isinstance(
                error, (BrokenPipeError, ConnectionResetError),
            )
            if not self.stopping.is_set() and not expected_rejection:
                self.error = error

    def inventory(self, target: str, kind: str) -> list[dict]:
        filters = json.loads(parse_qs(urlsplit(target).query).get("filters", ["{}"])[0])
        records = self.resources if kind == "container" else self.volumes
        result = []
        for record in records.values():
            labels = record.get("Config", {}).get("Labels", {}) if kind == "container" else record.get("Labels", {})
            if all(labels.get(key) == value for key, value in (
                expression.split("=", 1) for expression in filters.get("label", [])
            )):
                result.append(record)
        return result

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
        fixture = FixtureDaemon(Path(directory).resolve() / "d.sock")
        try:
            yield fixture
        finally:
            fixture.close()


def verifier(daemon: FixtureDaemon, *, timeout: float = 2) -> LocalDockerVerifier:
    host = "unix://" + str(daemon.path)
    return LocalDockerVerifier(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host), timeout=timeout)


def identity() -> Identity:
    return Identity("container", IDENTIFIER, CREATED, "daemon-a", TOKEN, CREATOR, 1)


@pytest.fixture
def symlinked_temp_root(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if os.name != "posix":
        pytest.skip("POSIX fixture path canonicalization")
    with tempfile.TemporaryDirectory(prefix="ce-l-") as directory:
        root = Path(directory).resolve()
        target = root / "real"
        target.mkdir(mode=0o700)
        alias = root / "alias"
        alias.symlink_to(target, target_is_directory=True)
        monkeypatch.setattr(tempfile, "tempdir", str(alias))
        yield


def test_fixture_admits_canonical_socket_under_symlinked_temp_root(
    symlinked_temp_root: None, request: pytest.FixtureRequest,
) -> None:
    daemon = request.getfixturevalue("daemon")
    assert daemon.path == daemon.path.resolve()
    assert verifier(daemon).probe(identity()) is True
    assert daemon.accepts == 1


def test_inventory_and_birth_inspection_share_one_connection(daemon: FixtureDaemon) -> None:
    assert verifier(daemon).probe(identity()) is True
    assert IDENTIFIER in daemon.resources
    assert daemon.accepts == 1
    assert daemon.calls == [
        ("GET", "/info"), ("GET", "/containers/json?all=1"),
        ("GET", f"/containers/{IDENTIFIER}/json"),
    ]


def test_complete_empty_inventory_proves_absence_without_inspection(daemon: FixtureDaemon) -> None:
    daemon.resources.clear()
    assert verifier(daemon).probe(identity()) is False
    assert daemon.accepts == 1
    assert daemon.calls == [("GET", "/info"), ("GET", "/containers/json?all=1")]


def reservation_resources(daemon: FixtureDaemon) -> list[Identity]:
    expected = [identity()]
    for identifier, token, epoch in [("b" * 64, TOKEN, 2), ("c" * 64, "x" * 43, 1)]:
        daemon.resources[identifier] = {
            "Id": identifier, "Created": CREATED, "Config": {"Labels": birth_labels(token, CREATOR, epoch)},
        }
        if token == TOKEN:
            expected.append(replace(identity(), resource_id=identifier, generation=epoch))
    for name, token, epoch in [("volume-one", TOKEN, 3), ("volume-two", TOKEN, 4), ("other", "x" * 43, 1)]:
        daemon.volumes[name] = {"Name": name, "CreatedAt": CREATED, "Labels": birth_labels(token, CREATOR, epoch)}
        if token == TOKEN:
            expected.append(Identity("volume", name, CREATED, "daemon-a", token, CREATOR, epoch))
    return expected


def test_reservation_recovery_filters_both_kinds_and_preserves_all_generations(daemon: FixtureDaemon) -> None:
    expected = reservation_resources(daemon)
    assert verifier(daemon).find_reservation(TOKEN, CREATOR) == expected
    assert daemon.accepts == 1
    inventories = [target for _method, target in daemon.calls if urlsplit(target).path in {
        "/containers/json", "/volumes",
    }]
    assert len(inventories) == 2
    for target in inventories:
        assert json.loads(parse_qs(urlsplit(target).query)["filters"][0]) == {
            "label": [f"{LABEL_TOKEN}={TOKEN}"],
        }
    assert len(daemon.calls) == 7


def test_reservation_recovery_returns_empty_only_after_both_filtered_inventories(daemon: FixtureDaemon) -> None:
    reservation_resources(daemon)
    assert verifier(daemon).find_reservation("z" * 43, CREATOR) == []
    assert daemon.accepts == 1
    assert [urlsplit(target).path for _method, target in daemon.calls] == [
        "/info", "/containers/json", "/volumes",
    ]


@pytest.mark.parametrize("failure_route", [f"/containers/{'b' * 64}/json", "/volumes", "/volumes/volume-two"])
def test_reservation_recovery_never_returns_partial_coverage(
    daemon: FixtureDaemon, failure_route: str,
) -> None:
    expected = reservation_resources(daemon)
    daemon.failure_route = failure_route
    with pytest.raises(VerificationError):
        verifier(daemon).find_reservation(TOKEN, CREATOR)
    assert daemon.accepts == 1
    paths = [urlsplit(target).path for _method, target in daemon.calls]
    assert f"/containers/{IDENTIFIER}/json" in paths
    assert paths[-1] == failure_route
    assert all(method == "GET" for method, _target in daemon.calls)
    assert len(daemon.resources) == 3 and len(daemon.volumes) == 3 and len(expected) == 4


def test_id_name_fallback_race_refuses_without_any_mutation(daemon: FixtureDaemon) -> None:
    daemon.behavior = "name-collision"
    collision = "b" * 64
    daemon.resources[collision] = {"Id": collision, "Name": "/" + IDENTIFIER}
    with pytest.raises(VerificationError, match="ID-only"):
        verifier(daemon).remove(identity(), authorized=True)
    assert daemon.accepts == 0 and daemon.calls == []
    assert set(daemon.resources) == {IDENTIFIER, collision}


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
        verifier(daemon).probe(identity())
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


def test_read_ahead_padding_cannot_be_discarded_to_expose_fake_absence(daemon: FixtureDaemon) -> None:
    daemon.behavior = "padding"
    with pytest.raises(VerificationError):
        verifier(daemon).probe(identity())
    assert daemon.accepts == 1 and IDENTIFIER in daemon.resources


def test_unsolicited_buffered_response_cannot_prove_absence(daemon: FixtureDaemon) -> None:
    daemon.behavior = "unsolicited"
    with pytest.raises(VerificationError):
        verifier(daemon).probe(identity())
    assert daemon.accepts == 1 and IDENTIFIER in daemon.resources
    assert all(method != "DELETE" for method, _target in daemon.calls)


def test_unsolicited_socket_bytes_refuse_before_next_request(daemon: FixtureDaemon) -> None:
    host = "unix://" + str(daemon.path)
    with LocalDaemonSession(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host)) as session:
        daemon.behavior = "unsolicited"
        assert daemon.connection is not None
        daemon.connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]")
        with pytest.raises(VerificationError):
            session.request("GET", "/containers/json?all=1")
        assert session.connection is None and session.reader is None
    assert daemon.accepts == 1 and IDENTIFIER in daemon.resources
    assert daemon.calls == [("GET", "/info")]


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(9), RuntimeError("interrupted")])
def test_interrupted_admission_closes_every_resource_and_propagates(
    daemon: FixtureDaemon, monkeypatch: pytest.MonkeyPatch, interruption: BaseException,
) -> None:
    from docker_resource_producer import local_session

    host = "unix://" + str(daemon.path)
    session = LocalDaemonSession(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host))
    resources: list[tuple[socket.socket | None, io.BufferedReader | None, io.RawIOBase | None]] = []
    read = local_session.read_response

    def interrupt(source: io.BufferedReader) -> tuple[int, bytes]:
        read(source)
        resources.append((session.connection, session.reader, session.raw_reader))
        raise interruption

    monkeypatch.setattr(local_session, "read_response", interrupt)
    try:
        with pytest.raises(type(interruption)) as caught:
            with session:
                pytest.fail("Interrupted admission must not complete")
        assert caught.value is interruption
        assert session.connection is None and session.reader is None and session.raw_reader is None
        assert len(resources) == 1
        connection, reader, raw_reader = resources[0]
        assert connection is not None and connection.fileno() == -1
        assert reader is not None and reader.closed
        assert raw_reader is not None and raw_reader.closed
        assert daemon.accepts == 1 and IDENTIFIER in daemon.resources
    finally:
        session.close()


@pytest.mark.parametrize("behavior", ["nested-info", "nested-inventory"])
def test_nested_json_closes_all_session_resources_without_reconnect(
    daemon: FixtureDaemon, monkeypatch: pytest.MonkeyPatch, behavior: str,
) -> None:
    from docker_resource_producer import local_session

    daemon.behavior = behavior
    host = "unix://" + str(daemon.path)
    session = LocalDaemonSession(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host))
    decode = local_session._json
    resources: list[tuple[socket.socket | None, io.BufferedReader | None, io.RawIOBase | None]] = []

    def capture_decode(raw: str) -> object:
        resources.append((session.connection, session.reader, session.raw_reader))
        return decode(raw)

    monkeypatch.setattr(local_session, "_json", capture_decode)
    with pytest.raises(VerificationError):
        with session:
            session.request("GET", "/containers/json?all=1")
    assert session.connection is None and session.reader is None and session.raw_reader is None
    assert resources
    for connection, reader, raw_reader in resources:
        assert connection is not None and connection.fileno() == -1
        assert reader is not None and reader.closed
        assert raw_reader is not None and raw_reader.closed
    assert daemon.accepts == 1 and IDENTIFIER in daemon.resources
    assert all(method != "DELETE" for method, _target in daemon.calls)


def test_expiry_during_json_decode_closes_before_return(daemon: FixtureDaemon, monkeypatch) -> None:
    from docker_resource_producer import local_session
    decode = local_session._json
    host = "unix://" + str(daemon.path)
    with LocalDaemonSession(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host)) as session:
        def expired_decode(raw: str):
            result = decode(raw)
            session.deadline = time.monotonic() - 1
            return result
        monkeypatch.setattr(local_session, "_json", expired_decode)
        with pytest.raises(VerificationError):
            session.request("GET", "/containers/json?all=1")
        assert session.connection is None and session.reader is None
    assert daemon.accepts == 1


@pytest.mark.parametrize("method", ["DELETE", "POST", "PUT", "PATCH"])
def test_session_refuses_mutating_requests_before_transport(daemon: FixtureDaemon, method: str) -> None:
    host = "unix://" + str(daemon.path)
    with LocalDaemonSession(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host)) as session:
        with pytest.raises(VerificationError, match="Invalid daemon request"):
            session.request(method, f"/containers/{IDENTIFIER}?force=1")
    assert daemon.accepts == 1 and IDENTIFIER in daemon.resources
    assert daemon.calls == [("GET", "/info")]


def test_expiry_during_inventory_projection_cannot_return_absence(daemon: FixtureDaemon, monkeypatch) -> None:
    client = verifier(daemon)
    def expired_inventory(session, _kind):
        session.deadline = time.monotonic() - 1
        return []
    monkeypatch.setattr(client, "_inventory", expired_inventory)
    with pytest.raises(VerificationError):
        client.probe(identity())
    assert daemon.accepts == 1 and IDENTIFIER in daemon.resources


def test_volume_search_warning_is_not_complete_absence(daemon: FixtureDaemon) -> None:
    daemon.behavior = "warning"
    host = "unix://" + str(daemon.path)
    with LocalDaemonSession(Endpoint(host, "daemon-a"), LocalDaemonAdmission(host)) as session:
        with pytest.raises(VerificationError, match="volume inventory"):
            verifier(daemon)._inventory(session, "volume")


@pytest.mark.parametrize("warning", [False, 0, "", {}, True, 1, "warning", ["warning"]])
def test_malformed_volume_warning_cannot_prove_absence(daemon: FixtureDaemon, warning: object) -> None:
    daemon.behavior = "custom-warning"
    daemon.warning = warning
    volume = replace(identity(), resource_type="volume", resource_id="absent-volume")
    with pytest.raises(VerificationError, match="volume inventory"):
        verifier(daemon).probe(volume)
    assert daemon.accepts == 1


@pytest.mark.parametrize("warning", [None, []])
def test_only_null_or_empty_volume_warnings_allow_complete_inventory(
    daemon: FixtureDaemon, warning: object,
) -> None:
    daemon.behavior = "custom-warning"
    daemon.warning = warning
    volume = replace(identity(), resource_type="volume", resource_id="absent-volume")
    assert verifier(daemon).probe(volume) is False
    assert daemon.accepts == 1


def test_retained_container_refuses_cleanup_before_connecting(daemon: FixtureDaemon) -> None:
    with pytest.raises(VerificationError, match="ID-only"):
        verifier(daemon).remove(identity(), authorized=True)
    assert daemon.accepts == 0 and IDENTIFIER in daemon.resources


def test_changed_birth_identity_never_deletes(daemon: FixtureDaemon) -> None:
    with pytest.raises(VerificationError, match="birth identity"):
        verifier(daemon).probe(replace(identity(), created="2026-10-14T00:00:00Z"))
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
