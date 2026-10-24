import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_ssh import gateway_sshd as ingress
from test_gateway_provider import key


def config(root: Path) -> dict:
    return {
        "schema": "agent-ssh.gateway-native-ingress", "version": 1,
        "policy": root / "policy.json", "directory": root / "directory.json",
        "machine": "worker-a", "environment": "linux",
        "public_key_file": root / "identity.pub", "host_key_file": root / "host",
        "state_dir": root / "ingress", "sshd": root / "sshd",
        "ssh_keygen": root / "ssh-keygen", "port": 24107,
    }


def test_wrong_login_user_refuses_before_host_key_or_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ingress.authorization, "projection", Mock(return_value={"user": "owner"}))
    monkeypatch.setattr(ingress, "_account", lambda: "different-owner")
    derive = Mock()
    monkeypatch.setattr(ingress, "_host_key", derive)
    with pytest.raises(PermissionError, match="login user"):
        ingress.prepare(config(tmp_path))
    derive.assert_not_called()
    assert not (tmp_path / "ingress").exists()


@pytest.mark.parametrize("value", ["relative/path", "C:\\bad\npath", "/bad%h", '/bad"path'])
def test_native_paths_are_literal_and_absolute(value):
    with pytest.raises(ValueError):
        ingress._absolute(value)


def test_config_is_standalone_and_never_enables_forwarding(tmp_path):
    text = ingress._native_config(config(tmp_path), "owner", tmp_path / "authorized")
    assert "AllowUsers owner\n" in text
    assert "DisableForwarding yes\n" in text
    assert "StrictModes yes\n" in text
    assert "PasswordAuthentication no\n" in text
    assert "KbdInteractiveAuthentication no\n" in text
    assert "PermitUserRC no\n" in text
    assert "Port 24107\n" in text and "ListenAddress 127.0.0.1\n" in text
    assert "Include " not in text


def test_native_entrypoint_preserves_protocol_stdout_on_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ingress, "load", Mock(side_effect=ValueError("fixture invalid")))
    assert ingress.main(["--config", str(tmp_path / "selected.json")]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "[FAIL]" in captured.err


def test_host_pin_is_compared_with_the_private_keys_actual_public_value(tmp_path, monkeypatch):
    selected = config(tmp_path)
    monkeypatch.setattr(ingress.authorization, "_private", Mock())
    monkeypatch.setattr(ingress, "_executable", Mock())
    run = Mock(return_value=Mock(returncode=0, stdout=(key(3) + " public-comment\n").encode()))
    monkeypatch.setattr(ingress.subprocess, "run", run)
    with pytest.raises(ValueError, match="admitted target pin"):
        ingress._host_key(selected, key(4))
    ingress._host_key(selected, key(3))
    assert run.call_args.kwargs["stdin"] == ingress.subprocess.DEVNULL
    assert run.call_args.kwargs["timeout"] == 10


def test_configuration_fields_are_closed_and_environment_is_native(tmp_path, monkeypatch):
    selected = config(tmp_path)
    selected = {name: str(value) if isinstance(value, Path) else value for name, value in selected.items()}
    path = tmp_path / "config.json"
    monkeypatch.setattr(ingress.authorization, "_private", Mock())
    monkeypatch.setattr(ingress, "_environment", lambda: "linux")
    path.write_text(json.dumps(selected), encoding="utf-8")
    assert ingress.load(path)["state_dir"] == tmp_path / "ingress"
    selected["extra"] = True
    path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(ValueError, match="configuration"):
        ingress.load(path)
    selected.pop("extra")
    selected["environment"] = "windows"
    path.write_text(json.dumps(selected), encoding="utf-8")
    with pytest.raises(ValueError, match="environment"):
        ingress.load(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows OpenSSH descriptor inheritance")
def test_new_native_root_removes_only_ancestor_descriptor_state(monkeypatch):
    carrier = "C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE"
    monkeypatch.setenv(carrier, "synthetic-ancestor-state")
    monkeypatch.setenv(carrier + "_OTHER", "preserve")
    monkeypatch.setenv("SSH_AUTH_SOCK", "preserve-agent")
    result = ingress._process_environment()
    assert not any(name.casefold() == carrier.casefold() for name in result)
    assert result[carrier + "_OTHER"] == "preserve"
    assert result["SSH_AUTH_SOCK"] == "preserve-agent"
    assert os.environ[carrier] == "synthetic-ancestor-state"


def test_native_authentication_probe_never_requests_a_command_shell(tmp_path, monkeypatch):
    import native_ssh_auth

    child = Mock()
    child.poll.return_value = None
    child.wait.return_value = 1

    def start(argv, **kwargs):
        kwargs["stderr"].write(b'Authenticated to fixture using "publickey".\n')
        kwargs["stderr"].flush()
        return child

    spawn = Mock(side_effect=start)
    monkeypatch.setattr(native_ssh_auth.subprocess, "Popen", spawn)
    result = native_ssh_auth.authenticate(["ssh", "-F", "empty-config"], tmp_path)
    assert spawn.call_args.args[0] == [
        "ssh", "-F", "empty-config", "-v", "-N", "-T", "127.0.0.1",
    ]
    assert result.authenticated and result.native_exit == 1
    child.terminate.assert_called_once()
