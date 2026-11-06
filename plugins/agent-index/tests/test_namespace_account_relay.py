import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agent_index import source_account_relay as relay


FIELDS = {
    "source": "github:example/repo", "account": "fixture-account",
    "recipient": "fixture-index", "host": "github.com", "action": "get-github-token",
}
CAPABILITY = "synthetic-namespace-capability-0123456789"
RESPONSE = b"protocol=https\nhost=github.com\nusername=fixture-account\ntoken=synthetic-token\n\n"
SUPPORT = (
    b"contract=credential-relay.account-source-lease/v1\n"
    b"operation=get-account-source-lease\n\n"
)


def lease_response(issued, expires, **changes):
    value = {
        "protocol": "https", "host": "github.com", "username": "fixture-account",
        "token": "synthetic-token", "lease_contract": relay.SOURCE_LEASE_CONTRACT,
        "lease_id": "c" * 32, "lease_issued_at": str(issued), "lease_expires_at": str(expires),
    }
    value.update(changes)
    return value


def descriptor(endpoint, uid, device, inode):
    return {
        "schema": "credential-relay.namespace-endpoint", "version": 1,
        "socket": endpoint.name, "device": device, "inode": inode, "uid": uid,
        "publication": "a" * 32, "source_generation": "b" * 32,
    }


@pytest.mark.parametrize("failure", [
    None, "descriptor-owner", "descriptor-mode", "socket-owner",
    "stale-inode", "replaced-inode", "peer-uid",
])
@pytest.mark.parametrize("source_lease", [False, True])
def test_namespace_identity_is_checked_before_capability_bytes(
    tmp_path, monkeypatch, failure, source_lease,
):
    endpoint = tmp_path / ("account-" + "a" * 32 + ".sock")
    metadata = tmp_path / "active.json"
    metadata.write_text(json.dumps(descriptor(endpoint, 1002, 3, 7)), encoding="utf-8")
    original_stat = Path.stat
    endpoint_reads = 0

    def observed(path, *args, **kwargs):
        nonlocal endpoint_reads
        if path == metadata:
            return SimpleNamespace(
                st_uid=1003 if failure == "descriptor-owner" else 1002,
                st_mode=stat.S_IFREG | (0o622 if failure == "descriptor-mode" else 0o640),
            )
        if path == endpoint:
            endpoint_reads += 1
            changed = failure == "stale-inode" or (
                failure == "replaced-inode" and endpoint_reads > 1
            )
            return SimpleNamespace(
                st_uid=1003 if failure == "socket-owner" else 1002,
                st_mode=stat.S_IFSOCK | 0o600, st_dev=3, st_ino=8 if changed else 7,
            )
        return original_stat(path, *args, **kwargs)

    class Connection:
        sent = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            pass

        def connect(self, address):
            assert address == str(endpoint)

        def getsockopt(self, *args):
            return struct.pack("3i", 17, 1003 if failure == "peer-uid" else 1002, 1002)

        def sendall(self, value):
            self.sent.append(value)

        def recv(self, size):
            if self.sent[-1] == b"describe-account-relay\n\n":
                return SUPPORT
            if source_lease:
                now = time.time()
                return ("\n".join(
                    f"{key}={value}" for key, value in lease_response(now, now + 60).items()
                ) + "\n\n").encode()
            return RESPONSE

    connection = Connection()
    monkeypatch.setattr(Path, "stat", observed)
    monkeypatch.setattr(socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    monkeypatch.setattr(socket, "socket", lambda *args: connection)
    transport = {"socket_descriptor": str(metadata), "owner_uid": 1002}
    exchange = relay.exchange_source_lease if source_lease else relay.exchange
    if failure:
        with pytest.raises(RuntimeError, match="source-account-relay-unavailable"):
            exchange(transport, FIELDS, CAPABILITY)
        assert connection.sent == []
    else:
        result = exchange(transport, FIELDS, CAPABILITY)
        assert (result.token if source_lease else result) == "synthetic-token"
        assert CAPABILITY.encode() in connection.sent[-1]


@pytest.mark.parametrize("support", [
    b"", b"contract=older\n\n", SUPPORT.replace(b"\n\n", b"\nextra=unknown\n\n"),
])
def test_lease_client_refuses_unsupported_live_peer_before_reading_capability(tmp_path, support):
    captured = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        policy = tmp_path / "worker.json"
        policy.write_text(json.dumps({
            "schema": "credential-relay.account-worker", "version": 1,
            "transport": {"host": "127.0.0.1", "port": listener.getsockname()[1]},
            "bindings": [dict(FIELDS, capability_file=str(tmp_path / "absent-capability"))],
        }))

        def serve():
            with listener.accept()[0] as client:
                client.settimeout(5)
                captured.append(client.recv(4096))
                if support:
                    client.sendall(support)

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            with pytest.raises(RuntimeError, match="source-account-relay-lease-unsupported"):
                relay.acquire_source_lease(policy, source=FIELDS["source"], account=FIELDS["account"])
        finally:
            worker.join(timeout=6)
            assert not worker.is_alive()
    assert captured == [b"describe-account-relay\n\n"]


@pytest.mark.parametrize("failure", [
    None, "expired", "latency", "rollback", "future-issued", "nan", "too-long",
    "contract", "lease-id", "account", "token-only",
])
def test_lease_client_bounds_duration_and_rejects_invalid_evidence(monkeypatch, failure):
    clock = SimpleNamespace(wall=1000.0, monotonic_value=50.0)
    monkeypatch.setattr(relay, "time", SimpleNamespace(
        time=lambda: clock.wall, monotonic=lambda: clock.monotonic_value,
    ))
    requests = []

    def roundtrip(transport, wire, *, timeout):
        requests.append((wire, timeout))
        assert 0 < timeout <= 20
        if wire == "describe-account-relay\n\n":
            clock.monotonic_value += 2
            clock.wall += 2
            return {"contract": relay.SOURCE_LEASE_CONTRACT, "operation": "get-account-source-lease"}
        clock.monotonic_value += 3
        clock.wall += 3
        value = lease_response(1002, 1062)
        if failure == "expired":
            value = lease_response(940, 1000)
        elif failure == "latency":
            clock.monotonic_value = 120
        elif failure == "rollback":
            clock.wall = 999
        elif failure == "future-issued":
            value = lease_response(1010, 1070)
        elif failure == "nan":
            value["lease_expires_at"] = "nan"
        elif failure == "too-long":
            value["lease_expires_at"] = "5000"
        elif failure == "contract":
            value["lease_contract"] = "unknown"
        elif failure == "lease-id":
            value["lease_id"] = "invalid"
        elif failure == "account":
            value["username"] = "other-account"
        elif failure == "token-only":
            value = {key: value[key] for key in ("protocol", "host", "username", "token")}
        return value

    monkeypatch.setattr(relay, "_roundtrip", roundtrip)
    if failure:
        with pytest.raises(RuntimeError, match="source-account-relay-unavailable"):
            relay.exchange_source_lease({}, FIELDS, CAPABILITY)
    else:
        result = relay.exchange_source_lease({}, FIELDS, CAPABILITY)
        assert result.token == "synthetic-token"
        assert "synthetic-token" not in repr(result)
        assert result.remaining_seconds() == 55
        clock.monotonic_value += 54
        assert result.remaining_seconds() == 1
        clock.monotonic_value += 1
        assert result.remaining_seconds() == 0
        clock.monotonic_value = 55
        assert result.remaining_seconds() == 0  # Old evidence cannot revive after expiry.
    assert len(requests) == 2
    assert requests[1][0].startswith("get-account-source-lease\n")
    assert requests[1][1] == 18


@pytest.mark.parametrize("clock_name", ["wall", "monotonic_value"])
def test_observed_clock_rollback_invalidates_even_above_the_initial_reading(monkeypatch, clock_name):
    clock = SimpleNamespace(wall=1020.0, monotonic_value=70.0)
    monkeypatch.setattr(relay, "time", SimpleNamespace(
        time=lambda: clock.wall, monotonic=lambda: clock.monotonic_value,
    ))
    lease = relay.SourceLease("synthetic-token", "c" * 32, 1000, 1060, 1000, 50, 60)
    assert lease.remaining_seconds() == 40
    setattr(clock, clock_name, getattr(clock, clock_name) - 5)
    assert lease.remaining_seconds() == 0
    setattr(clock, clock_name, getattr(clock, clock_name) + 10)
    assert lease.remaining_seconds() == 0


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("AGENT_INDEX_TEST_NAMESPACE_RELAY") != "1",
    reason="explicit Linux Unix-socket integration lane",
)
def test_linux_namespace_roundtrip(tmp_path_factory):
    if os.getuid() == 0:
        pytest.skip("integration lane requires its declared non-root consumer identity")
    directory = tmp_path_factory.mktemp("u")
    endpoint = directory / ("account-" + "a" * 32 + ".sock")
    assert len(os.fsencode(endpoint)) < 104, "select a short integration sandbox"
    captured = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(endpoint))
        listener.listen(1)
        listener.settimeout(5)
        value = endpoint.stat()
        metadata = directory / "active.json"
        metadata.write_text(json.dumps(descriptor(
            endpoint, os.getuid(), value.st_dev, value.st_ino,
        )), encoding="utf-8")
        metadata.chmod(0o600)

        def serve():
            with listener.accept()[0] as client:
                client.settimeout(5)
                data = bytearray()
                while b"\n\n" not in data:
                    part = client.recv(4096)
                    if not part:
                        break
                    data.extend(part)
                captured.append(bytes(data))
                client.sendall(RESPONSE)

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            assert relay.exchange({
                "socket_descriptor": str(metadata), "owner_uid": os.getuid(),
            }, FIELDS, CAPABILITY, timeout=5) == "synthetic-token"
        finally:
            worker.join(timeout=6)
            assert not worker.is_alive()
    assert CAPABILITY.encode() in captured[0]
