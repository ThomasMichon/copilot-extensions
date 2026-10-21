import json
import os
from pathlib import Path
import sys

from endpoint_rendezvous import write_endpoint
import pytest

from agent_chat_service import Client, ClientError
from agent_chat_service.cli import configured
from agent_chat_service.transport import GatewayTransport, _http_exchange
from test_hosting import TOKEN, configuration, running


def local_config(root):
    return {
        "schema": "agent-chat.client", "version": 1,
        "endpoint_dir": str(root / "run"), "state_dir": str(root / "client"),
        "actor": {"codename": "agent-a"},
        "token_command": [sys.executable, "-I", "-c", "print('synthetic-credential')"],
    }


def test_local_endpoint_offline_status_never_resolves_or_executes(tmp_path):
    config = local_config(tmp_path)
    config["token_command"] = [str(tmp_path / "missing-provider")]
    path = tmp_path / "client.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    client = configured(str(path))
    assert client.status()["scope"] == "local_only"
    assert not Path(config["endpoint_dir"]).exists()
    assert not list(Path(config["state_dir"]).rglob("*.json"))


@pytest.mark.parametrize("mutation", ["both", "neither", "relative", "stdio"])
def test_local_endpoint_configuration_refuses_ambiguous_or_implicit_routes(tmp_path, mutation):
    config = local_config(tmp_path)
    if mutation == "both":
        config["endpoint"] = "http://localhost"
    elif mutation == "neither":
        del config["endpoint_dir"]
    elif mutation == "relative":
        config["endpoint_dir"] = "relative-run"
    else:
        config["request_command"] = config.pop("token_command")
    with pytest.raises(ClientError) as caught:
        GatewayTransport(config)
    assert caught.value.code == "invalid_configuration"


def test_local_endpoint_real_tcp_request_uses_canonical_record_and_stable_namespace(tmp_path, rpc_endpoint):
    config = local_config(tmp_path)
    root = Path(config["endpoint_dir"])
    root.mkdir()
    address = rpc_endpoint.endpoint.removeprefix("http://")
    write_endpoint(root, "tcp", address)
    client = Client(GatewayTransport(config), config["actor"], config["state_dir"])
    assert client.join("room")["membership"]["active"]
    assert rpc_endpoint.state.authenticated == 1
    original = client.directory
    write_endpoint(root, "tcp", "127.0.0.1:1")
    rebound = Client(GatewayTransport(config), config["actor"], config["state_dir"])
    assert rebound.directory == original
    write_endpoint(root, "pipe", "synthetic-unavailable-pipe", alt=[("tcp", address)])
    assert not rebound.leave("room")["active"]


@pytest.mark.parametrize("mutation", [
    "absent", "invalid-json", "oversized", "schema-bool", "pid-bool", "pid-overflow",
    "wrong-type", "bad-alternative", "remote", "pipe", "relative-unix", "foreign-unix",
    "missing-port", "empty-port", "missing-port-alt", "empty-port-alt",
    "dead-publisher", "dead-publisher-alt",
])
def test_local_endpoint_refusal_is_bounded_without_network_or_fallback(tmp_path, monkeypatch, mutation):
    import agent_chat_service.transport as module
    import endpoint_rendezvous.rendezvous as rendezvous

    root = tmp_path / "run"
    root.mkdir()
    path = root / "endpoint.json"
    record = {
        "schema": 1, "transport": "tcp", "endpoint": "127.0.0.1:1",
        "pid": os.getpid(), "started_at": "synthetic-start",
    }
    queried_pids = []
    if mutation == "absent":
        pass
    elif mutation == "invalid-json":
        path.write_text("{", encoding="utf-8")
    elif mutation == "oversized":
        path.write_text(" " * (module.MAX_ENDPOINT_BYTES + 1), encoding="utf-8")
    else:
        if mutation == "schema-bool":
            record["schema"] = True
        elif mutation == "pid-bool":
            record["pid"] = True
        elif mutation == "pid-overflow":
            record["pid"] = 1 << 64
        elif mutation == "wrong-type":
            record["transport"] = []
        elif mutation == "bad-alternative":
            record["alt"] = ["invalid"]
        elif mutation == "remote":
            record["endpoint"] = "example.invalid:443"
        elif mutation == "pipe":
            record.update(transport="pipe", endpoint="unsupported-pipe")
        elif mutation == "relative-unix":
            record.update(transport="unix", endpoint="relative.sock")
        elif mutation == "foreign-unix":
            record.update(transport="unix", endpoint=str(tmp_path / "foreign.sock"))
        elif mutation.startswith(("missing-port", "empty-port")):
            address = "127.0.0.1" + (":" if mutation.startswith("empty-port") else "")
            if mutation.endswith("-alt"):
                record.update(transport="pipe", endpoint="synthetic-unavailable-pipe",
                              alt=[{"transport": "tcp", "endpoint": address}])
            else:
                record["endpoint"] = address
        else:
            def dead_publisher(pid):
                queried_pids.append(pid)
                return False

            monkeypatch.setattr(rendezvous, "pid_alive", dead_publisher)
            if mutation.endswith("-alt"):
                record.update(transport="pipe", endpoint="synthetic-unavailable-pipe",
                              alt=[{"transport": "tcp", "endpoint": "127.0.0.1:1"}])
        path.write_text(json.dumps(record), encoding="utf-8")

    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid discovery reached a network connection")

    monkeypatch.setattr(module.http.client, "HTTPConnection", unexpected)
    with pytest.raises(ClientError) as caught:
        _http_exchange({
            "endpoint_dir": str(root),
            "credential": "synthetic-credential", "timeout": 1,
            "payload": {"op": "members", "channel": "room"},
        })
    assert caught.value.code in {"endpoint_unavailable", "invalid_configuration", "provider_unsupported"}
    assert "synthetic-credential" not in caught.value.message
    if mutation.startswith("dead-publisher"):
        assert queried_pids == [record["pid"]]


@pytest.mark.skipif(os.name == "nt", reason="The native POSIX host advertises a Unix socket")
def test_local_endpoint_real_unix_client_uses_the_published_host(tmp_path):
    config = local_config(tmp_path)
    host_config = configuration(tmp_path)
    config["endpoint_dir"] = str(Path(host_config["data_dir"]) / "run")
    config["token_command"] = [sys.executable, "-I", "-c", f"print({TOKEN!r})"]
    with running(host_config) as host:
        assert host.transport == "unix"
        client = Client(GatewayTransport(config), config["actor"], config["state_dir"])
        assert client.join("room")["membership"]["active"]
        event = client.post("room", "native Unix consumer", "unix-request")["event"]
        import io
        assert client.read("room", io.StringIO())["messages"] == [event]
        assert not client.leave("room")["active"]
