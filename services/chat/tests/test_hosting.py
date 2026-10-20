from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import socket
import threading
import time

import pytest

from agent_chat_service import hosting


TOKEN = "synthetic-local-key-" + "x" * 32
SCHEME = "Bear" + "er "


def configuration(tmp_path):
    token_file = tmp_path / "key"
    token_file.write_text(TOKEN, encoding="ascii")
    token_file.chmod(0o600)
    return {
        "schema": "agent-chat.host", "version": 1,
        "data_dir": str(tmp_path / "data"),
        "installation_id": "example-installation", "token_file": str(token_file),
    }


def connection(host):
    if host.transport == "tcp":
        name, port = host.endpoint.rsplit(":", 1)
        return http.client.HTTPConnection(name, int(port), timeout=10)

    class UnixConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect(host.endpoint)

    return UnixConnection("localhost", timeout=10)


def rpc(host, request, *, token=TOKEN, path="/api/v1/chat"):
    client = connection(host)
    try:
        client.request("POST", path, json.dumps(request).encode("utf-8"), {
            "Content-Type": "application/json", "Authorization": SCHEME + token,
        })
        response = client.getresponse()
        return response.status, json.loads(response.read())
    finally:
        client.close()


@contextmanager
def running(config):
    with hosting.HostedChat(config) as host:
        stop = threading.Event()
        thread = threading.Thread(target=host.serve, args=(stop,))
        thread.start()
        try:
            yield host
        finally:
            stop.set()
            thread.join(10)
            assert not thread.is_alive()


def test_two_participants_auth_read_progress_and_restart(tmp_path):
    config = configuration(tmp_path)
    with running(config) as host:
        assert host.transport == ("tcp" if os.name == "nt" else "unix")
        if os.name == "nt":
            assert host.endpoint.startswith("127.0.0.1:")
            assert int(host.endpoint.rsplit(":", 1)[1]) > 0
        from endpoint_rendezvous import read_endpoint
        advertised = read_endpoint(host.run_dir)
        assert advertised.transport == host.transport
        assert advertised.address == host.endpoint
        request = {"op": "join", "channel": "room", "actor": {"codename": "agent-a"}}
        assert rpc(host, request, token="wrong")[0] == 401
        assert rpc(host, request, path="/wrong")[0] == 404
        assert rpc(host, request)[0] == 200
        assert rpc(host, {**request, "actor": {"codename": "agent-b"}})[0] == 200
        post = {"op": "post", "channel": "room", "codename": "agent-a",
                "text": "useful installed conversation", "request_id": "host-request"}
        status, value = rpc(host, post)
        assert status == 200
        event = value["event"]
        assert rpc(host, post)[1] == {"event": event, "duplicate": True}
        read = {"op": "read", "channel": "room", "codename": "agent-b"}
        page = rpc(host, read)[1]
        assert page["cursor"] == 0 and page["messages"] == [event]
        scan = rpc(host, {"op": "scan", "codename": "agent-b", "epoch": None,
                          "after": [{"channel": "room", "event_id": 0}], "timeout": 0})[1]
        assert scan["events"][0]["id"] == event["id"]
        assert "text" not in scan["events"][0]
        assert rpc(host, read)[1] == page
        assert rpc(host, {"op": "cursor", "channel": "room", "codename": "agent-b",
                          "expected_cursor": 0, "next_cursor": page["next_cursor"]})[0] == 200
        assert rpc(host, read)[1]["messages"] == []
        run_dir = host.run_dir
    assert not (run_dir / "endpoint.json").exists()
    with running(config) as host:
        assert rpc(host, post)[1] == {"event": event, "duplicate": True}
        assert rpc(host, read)[1]["messages"] == []
        for actor in ("agent-a", "agent-b"):
            assert rpc(host, {"op": "leave", "channel": "room", "codename": actor})[0] == 200


def test_real_singleton_refuses_second_host_before_store_open(tmp_path):
    config = configuration(tmp_path)
    with running(config) as host:
        endpoint = (host.run_dir / "endpoint.json").read_bytes()
        with pytest.raises(hosting.HostError, match="host_already_running"):
            hosting.HostedChat(config)
        assert (host.run_dir / "endpoint.json").read_bytes() == endpoint
        assert rpc(host, {"op": "members", "channel": "room"})[0] == 404


@pytest.mark.parametrize("waiters", [1, 3])
def test_held_wait_drain_and_health_keep_independent_requests_responsive(tmp_path, waiters):
    with running(configuration(tmp_path)) as host:
        assert rpc(host, {"op": "join", "channel": "room",
                          "actor": {"codename": "agent-a"}})[0] == 200
        with ThreadPoolExecutor(max_workers=4) as pool:
            waits = [pool.submit(rpc, host, {
                "op": "wait", "channel": "room", "codename": "agent-a", "timeout": 300,
            }) for _ in range(waiters)]
            deadline = time.monotonic() + 5
            with host.store._changed:
                while host.store._active_waiters < waiters and time.monotonic() < deadline:
                    host.store._changed.wait(.01)
                assert host.store._active_waiters == waiters
            client = connection(host)
            try:
                client.request("GET", "/health", headers={"Authorization": SCHEME + TOKEN})
                response = client.getresponse()
                assert response.status == 200 and json.loads(response.read())["accepting"]
            finally:
                client.close()
            assert rpc(host, {"op": "read", "channel": "room", "codename": "agent-a"})[0] == 200
            assert host.drain()["state"] in {"draining", "drained"}
            assert all(wait.result(5)[0] == 503 for wait in waits)
        assert rpc(host, {"op": "post", "channel": "room", "codename": "agent-a",
                          "request_id": "after-drain", "text": "must refuse"})[0] == 503
        client = connection(host)
        try:
            client.request("GET", "/health", headers={"Authorization": SCHEME + TOKEN})
            response = client.getresponse()
            assert response.status == 503 and not json.loads(response.read())["accepting"]
        finally:
            client.close()


@pytest.mark.parametrize("header", [
    b"Content-Length: 1\r\nContent-Length: 1",
    b"Content-Length: 65537",
    b"Content-Length: 1\r\nTransfer-Encoding: chunked",
    b"Content-Length: -1",
    b"Content-Length: 1\r\nAuthorization: extra",
])
def test_request_framing_and_duplicate_auth_refuse_before_dispatch(tmp_path, header):
    with running(configuration(tmp_path)) as host:
        client = connection(host)
        client.connect()
        try:
            credential = (SCHEME + TOKEN).encode("ascii")
            client.sock.sendall(
                b"POST /api/v1/chat HTTP/1.1\r\nHost: localhost\r\nAuthorization: "
                + credential + b"\r\nContent-Type: application/json\r\n" + header + b"\r\n\r\n{}"
            )
            response = http.client.HTTPResponse(client.sock)
            response.begin()
            assert response.status in {400, 401, 413}
            assert "messages" not in json.loads(response.read())
        finally:
            client.close()
        assert rpc(host, {"op": "members", "channel": "room"})[0] == 404


@pytest.mark.parametrize("stage", ["headers", "body"])
def test_absolute_ingress_deadline_retires_slow_input_and_releases_admission(
    tmp_path, monkeypatch, stage,
):
    monkeypatch.setattr(hosting, "IO_SECONDS", .3)
    with running(configuration(tmp_path)) as host:
        client = connection(host)
        client.connect()
        start = time.monotonic()
        try:
            initial = b"POST /api/v1/chat HTTP/1.1\r\n"
            if stage == "headers":
                initial += b"X-Slow: "
            else:
                initial += (
                    b"Authorization: " + (SCHEME + TOKEN).encode("ascii")
                    + b"\r\nContent-Type: application/json\r\nContent-Length: 64\r\n\r\n"
                )
            client.sock.sendall(initial)
            for _ in range(20):
                time.sleep(.04)
                try:
                    client.sock.sendall(b"x")
                except OSError:
                    break
            try:
                assert client.sock.recv(1) == b""
            except (ConnectionAbortedError, ConnectionResetError):
                pass
            assert time.monotonic() - start < 2
        finally:
            client.close()
        assert rpc(host, {"op": "join", "channel": "room",
                          "actor": {"codename": "agent-a"}})[0] == 200


def test_aggregate_headers_refuse_without_reflecting_input(tmp_path):
    with running(configuration(tmp_path)) as host:
        client = connection(host)
        client.connect()
        try:
            client.sock.sendall(
                b"POST /api/v1/chat HTTP/1.1\r\n" + b"X: " + b"z" * 20000 + b"\r\n\r\n"
            )
            response = http.client.HTTPResponse(client.sock)
            response.begin()
            assert response.status == 431
            body = response.read()
            assert b"zzzz" not in body and json.loads(body)["error"] == "http_request_invalid"
        finally:
            client.close()


def test_worker_admission_is_bounded_and_overflow_does_not_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(hosting, "MAX_CONNECTIONS", 2)
    with running(configuration(tmp_path)) as host:
        clients = [connection(host) for _ in range(3)]
        try:
            for client in clients[:2]:
                client.connect()
                client.sock.sendall(b"POST /api/v1/chat HTTP/1.1\r\n")
            deadline = time.monotonic() + 3
            while host.server.slots._value and time.monotonic() < deadline:
                time.sleep(.01)
            assert host.server.slots._value == 0
            clients[2].connect()
            assert clients[2].sock.recv(1) == b""
            assert host.server.slots._value == 0
        finally:
            for client in clients:
                client.close()


@pytest.mark.parametrize("change", [
    {"version": True}, {"extra": 1}, {"data_dir": "relative"},
    {"token_file": "relative"}, {"installation_id": ""},
    {"installation_id": "\ud800"}, {"data_dir": "C:\\invalid\x00path"},
])
def test_configuration_refuses_without_echoing_values(tmp_path, change):
    config = {**configuration(tmp_path), **change}
    path = tmp_path / "host.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(hosting.HostError, match="^host_configuration_invalid$"):
        hosting.load_config(str(path))
    with pytest.raises(hosting.HostError, match="^host_configuration_invalid$"):
        hosting.HostedChat(config)
    assert not (tmp_path / "data").exists()


def test_small_or_non_ascii_secret_refuses_before_data_creation(tmp_path):
    config = configuration(tmp_path)
    Path(config["token_file"]).write_text("short", encoding="ascii")
    with pytest.raises(hosting.HostError, match="host_token_file_invalid"):
        hosting.HostedChat(config)
    assert not Path(config["data_dir"]).exists()


def test_maximum_key_with_file_line_ending_is_used_without_masking(tmp_path):
    config = configuration(tmp_path)
    credential = "y" * 1024
    Path(config["token_file"]).write_bytes(credential.encode("ascii") + b"\r\n")
    with running(config) as host:
        assert rpc(host, {"op": "join", "channel": "room",
                          "actor": {"codename": "agent-a"}}, token=credential)[0] == 200


def test_failed_endpoint_publication_retires_host_and_removes_own_attempt(tmp_path, monkeypatch):
    import endpoint_rendezvous

    config = configuration(tmp_path)
    publish = endpoint_rendezvous.write_endpoint

    def fail_after_publish(*args, **kwargs):
        publish(*args, **kwargs)
        raise OSError("synthetic publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(endpoint_rendezvous, "write_endpoint", fail_after_publish)
        with pytest.raises(OSError, match="synthetic publication failure"):
            hosting.HostedChat(config)
    assert not (Path(config["data_dir"]) / "run" / "endpoint.json").exists()
    with running(config) as host:
        assert rpc(host, {"op": "join", "channel": "room",
                          "actor": {"codename": "agent-a"}})[0] == 200
