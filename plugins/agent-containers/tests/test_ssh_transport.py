"""Tests for the trusted-container OpenSSH transport."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_containers import docker_proxy
from agent_containers import ssh_transport as transport


def test_bootstrap_commands_never_inherit_acp_stdin(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transport.subprocess, "run", fake_run)
    transport._run(["docker", "inspect", "repo-1"])
    assert seen["stdin"] is subprocess.DEVNULL
    assert "input" not in seen


def test_staged_input_is_binary_to_preserve_lf(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            args=args,
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

    monkeypatch.setattr(transport.subprocess, "run", fake_run)
    transport._run(["docker", "exec", "-i", "repo-1"], input_text="value\n")
    assert seen["input"] == b"value\n"
    assert "text" not in seen


def test_prepare_ssh_config_uses_docker_directly_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(transport, "_SSH_DIR", tmp_path)
    monkeypatch.setattr(
        transport,
        "_ensure_host_key",
        lambda: (tmp_path / "id_ed25519", "ssh-ed25519 AAAA"),
    )
    monkeypatch.setattr(transport, "_install_authorized_key", lambda *args: None)
    monkeypatch.setattr(transport, "_container_id", lambda name: "a" * 64)
    monkeypatch.setattr(transport, "_is_windows", lambda: False)

    config = transport.prepare_ssh_config("repo-1", "vscode")

    text = Path(config.config_file).read_text(encoding="utf-8")
    assert (
        "ProxyCommand docker exec -i -u root repo-1 "
        "/usr/sbin/sshd -i -e -o GatewayPorts=no"
    ) in text
    assert "StrictHostKeyChecking accept-new" in text
    assert config.host_alias.endswith("-" + ("a" * 12))
    assert config.user == "vscode"


def test_prepare_ssh_config_uses_windowless_broker_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(transport, "_SSH_DIR", tmp_path)
    monkeypatch.setattr(
        transport,
        "_ensure_host_key",
        lambda: (tmp_path / "id_ed25519", "ssh-ed25519 AAAA"),
    )
    monkeypatch.setattr(transport, "_install_authorized_key", lambda *args: None)
    monkeypatch.setattr(transport, "_container_id", lambda name: "a" * 64)
    monkeypatch.setattr(transport, "_is_windows", lambda: True)
    monkeypatch.setattr(docker_proxy, "ensure_broker", lambda *args: 54321)

    config = transport.prepare_ssh_config("repo-1", "vscode")

    text = Path(config.config_file).read_text(encoding="utf-8")
    assert "HostName 127.0.0.1" in text
    assert "Port 54321" in text
    assert "HostKeyAlias agent-container-repo-1-aaaaaaaaaaaa" in text
    assert "ProxyCommand docker exec" not in text


def test_docker_broker_uses_binary_pipes_and_suppresses_window(monkeypatch):
    seen = {}
    process = SimpleNamespace(
        stdin=SimpleNamespace(),
        stdout=SimpleNamespace(),
        wait=lambda: 17,
        poll=lambda: 17,
    )

    def fake_popen(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return process

    monkeypatch.setattr(docker_proxy.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        docker_proxy,
        "no_window_kwargs",
        lambda: {"creationflags": 0x08000000},
    )
    monkeypatch.setattr(
        docker_proxy.threading,
        "Thread",
        lambda **kwargs: SimpleNamespace(
            start=lambda: None,
            join=lambda timeout=None: None,
        ),
    )
    connection = SimpleNamespace(close=lambda: None)

    docker_proxy._serve_connection("repo-1", connection)

    assert seen["args"] == [
        "docker",
        "exec",
        "-i",
        "-u",
        "root",
        "repo-1",
        "/usr/sbin/sshd",
        "-i",
        "-e",
        "-o",
        "GatewayPorts=no",
    ]
    assert seen["kwargs"] == {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "creationflags": 0x08000000,
    }


def test_docker_broker_health_accepts_fragmented_control_response(monkeypatch):
    class Connection:
        def __init__(self):
            self.responses = iter((b"po", b"ng\n"))
            self.sent = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def sendall(self, data):
            self.sent.append(data)

        def recv(self, _size):
            return next(self.responses)

    connection = Connection()
    monkeypatch.setattr(
        docker_proxy.socket,
        "create_connection",
        lambda *_args, **_kwargs: connection,
    )
    endpoint = {
        "schema_version": 1,
        "container": "repo-1",
        "container_id": "a" * 64,
        "runtime": str(Path(docker_proxy.__file__).resolve()),
        "control_port": 54321,
    }

    assert docker_proxy._healthy_endpoint(endpoint, "repo-1", "a" * 64)
    assert connection.sent == [b"ping\n"]


def test_docker_broker_control_exits_when_listener_closes():
    listener = SimpleNamespace(
        accept=lambda: (_ for _ in ()).throw(OSError("closed")),
    )

    assert docker_proxy._serve_control(listener) is None


def test_docker_broker_failed_start_retires_process_and_endpoint(
    monkeypatch,
    tmp_path,
):
    endpoint_file = tmp_path / "proxy.json"

    class Process:
        pid = 123

        def __init__(self):
            self.running = True
            self.terminated = False
            self.waited = False

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.terminated = True
            self.running = False

        def wait(self, timeout):
            self.waited = True
            return 0

    process = Process()

    def fake_popen(args, **_kwargs):
        endpoint_file.write_text(json.dumps({"pid": process.pid}), encoding="utf-8")
        return process

    monkeypatch.setattr(docker_proxy.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(docker_proxy, "_healthy_endpoint", lambda *_args: False)
    monkeypatch.setattr(docker_proxy, "windowless_python", lambda value: value)
    monkeypatch.setattr(docker_proxy, "detached_kwargs", lambda **_kwargs: {})

    with pytest.raises(RuntimeError, match="did not become ready"):
        docker_proxy.ensure_broker(
            "repo-1",
            "a" * 64,
            endpoint_file,
            timeout=0,
        )

    assert process.terminated
    assert process.waited
    assert not endpoint_file.exists()


@pytest.mark.parametrize("container,user", [
    ("repo-1;whoami", "vscode"),
    ("repo-1", "vscode;whoami"),
])
def test_prepare_ssh_config_rejects_shell_metacharacters(container, user):
    with pytest.raises(RuntimeError, match="Unsafe"):
        transport.prepare_ssh_config(container, user)


def test_build_remote_command_references_env_file_not_values():
    command = transport.build_remote_command(
        "copilot --acp --stdio",
        "/home/vscode/.agent-containers/launch/abc.env",
    )
    assert ". /home/vscode/.agent-containers/launch/abc.env" in command
    assert "rm -f /home/vscode/.agent-containers/launch/abc.env" in command
    assert "copilot --acp --stdio" in command


def test_authorized_key_is_sent_over_attached_stdin(monkeypatch):
    calls = []

    def fake_run(args, *, input_text=None, timeout=30.0):
        calls.append((args, input_text))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transport, "_run", fake_run)
    transport._install_authorized_key("repo-1", "vscode", "ssh-ed25519 AAAA")

    install_args, key_input = calls[-1]
    assert install_args[:4] == ["docker", "exec", "-i", "-u"]
    assert key_input == "ssh-ed25519 AAAA\n"
    assert all("ssh-ed25519 AAAA" not in arg for arg in install_args)


def test_container_environment_preserves_path_without_session_identity(monkeypatch):
    monkeypatch.setattr(
        transport,
        "_run",
        lambda args: SimpleNamespace(
            returncode=0,
            stdout="PATH=/custom/bin\0HOME=/home/vscode\0TOKEN=value\0",
            stderr="",
        ),
    )
    assert transport.container_environment("repo-1", "vscode") == {
        "PATH": "/custom/bin",
        "TOKEN": "value",
    }


def test_write_remote_env_sends_secrets_over_stdin(monkeypatch):
    seen = {}

    def fake_run(args, *, input_text=None, timeout=30.0):
        seen["args"] = args
        seen["input"] = input_text
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transport, "_run", fake_run)
    monkeypatch.setattr(transport, "_remote_home", lambda *args: "/home/vscode")
    monkeypatch.setattr(transport.uuid, "uuid4", lambda: SimpleNamespace(hex="abc"))

    path = transport.write_remote_env(
        "repo-1",
        "vscode",
        {"GH_TOKEN": "secret-value", "LC_GIT_CREDENTIAL_RELAY": "54321"},
    )

    assert path == "/home/vscode/.agent-containers/launch/abc.env"
    assert "secret-value" in seen["input"]
    assert all("secret-value" not in arg for arg in seen["args"])


def test_build_ssh_command_uses_shared_builder(monkeypatch):
    config = SimpleNamespace()
    seen = {}

    def fake_build(cfg, cmd, *, reverse_forwards=None):
        seen["reverse_forwards"] = reverse_forwards
        return ["ssh", "target", cmd]

    monkeypatch.setattr(transport.shutil, "which", lambda name: "ssh")
    monkeypatch.setattr(
        transport,
        "build_remote_exec_args",
        fake_build,
    )
    assert transport.build_ssh_command(
        config,
        "echo ok",
        reverse_forwards=["9857:127.0.0.1:61234"],
    ) == [
        "ssh", "target", "echo ok"
    ]
    assert seen["reverse_forwards"] == ["9857:127.0.0.1:61234"]
