import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import threading
from types import SimpleNamespace

import pytest

from agent_index import source_account_relay as relay


FIELDS = {
    "source": "github:example/repo", "account": "fixture-account",
    "recipient": "fixture-index", "host": "github.com", "action": "get-github-token",
}
CAPABILITY = "synthetic-namespace-capability-0123456789"
RESPONSE = b"protocol=https\nhost=github.com\nusername=fixture-account\ntoken=synthetic-token\n\n"


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
def test_namespace_identity_is_checked_before_capability_bytes(tmp_path, monkeypatch, failure):
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
            return RESPONSE

    connection = Connection()
    monkeypatch.setattr(Path, "stat", observed)
    monkeypatch.setattr(socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    monkeypatch.setattr(socket, "socket", lambda *args: connection)
    transport = {"socket_descriptor": str(metadata), "owner_uid": 1002}
    if failure:
        with pytest.raises(RuntimeError, match="source-account-relay-unavailable"):
            relay.exchange(transport, FIELDS, CAPABILITY)
        assert connection.sent == []
    else:
        assert relay.exchange(transport, FIELDS, CAPABILITY) == "synthetic-token"
        assert CAPABILITY.encode() in connection.sent[0]


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
