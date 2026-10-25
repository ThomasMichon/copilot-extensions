import builtins
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

from agent_procutil import no_window_kwargs
from fleet_manager.planning import canonical
from fleet_manager.ssh_directory import DirectoryUpdate
import pytest

from agent_ssh import gateway_directory
from test_gateway_provider import fixture_files, key


def selected(root, directory):
    policy = json.loads((root / "policy.json").read_text("utf-8"))
    return DirectoryUpdate.parse(canonical({
        "schema": "agent-ssh.service-directory", "version": 1,
        "policy": policy, "directory": directory,
    }))


def arguments(root):
    return {
        "policy_path": root / "policy.json", "machine": "worker-a", "environment": "linux",
        "gateway": "selected-gateway", "controller": "gateway-a",
        "identity_file": root / "approved identity", "public_key_file": root / "identity.pub",
        "authorization_dir": root / "authorization",
        "config_d": root / "ssh" / "config.d", "ssh_config": root / "ssh" / "config",
    }


def test_later_directory_rebuilds_real_profiles_and_native_login_keys_without_restart(tmp_path):
    _, directory = fixture_files(tmp_path)
    later = json.loads(json.dumps(directory))
    directory["machines"].pop()
    first = gateway_directory.apply_update(selected(tmp_path, directory), **arguments(tmp_path))
    authorized = Path(first["authorization_file"])
    assert key(2) not in authorized.read_text("utf-8")
    later["revision"] = 2
    second = gateway_directory.apply_update(selected(tmp_path, later), **arguments(tmp_path))
    assert key(2) in authorized.read_text("utf-8")
    fragment = Path(second["fragment"])
    assert "Host worker-b" in fragment.read_text("utf-8")
    before = authorized.read_bytes(), authorized.stat().st_mtime_ns
    repeated = gateway_directory.apply_update(selected(tmp_path, later), **arguments(tmp_path))
    assert not repeated["authorization_changed"]
    assert (authorized.read_bytes(), authorized.stat().st_mtime_ns) == before
    with pytest.raises(ValueError, match="older"):
        gateway_directory.apply_update(selected(tmp_path, directory), **arguments(tmp_path))
    assert authorized.read_bytes() == before[0]
    executable = shutil.which("ssh")
    if not executable:
        pytest.skip("Native OpenSSH configuration consumer is unavailable")
    result = subprocess.run(
        [executable, "-G", "-F", str(tmp_path / "ssh" / "config"), "worker-b"],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0
    assert "proxyjump selected-gateway" in result.stdout.lower()
    assert "port 24002" in result.stdout


@pytest.mark.parametrize("change", ["controller", "local-policy", "missing-authorization", "local-key"])
def test_remote_notification_cannot_expand_local_write_authority(tmp_path, monkeypatch, change):
    _, directory = fixture_files(tmp_path)
    update = selected(tmp_path, directory)
    config = arguments(tmp_path)
    if change == "controller":
        config["controller"] = "another-controller"
    elif change == "local-policy":
        value = json.loads((tmp_path / "policy.json").read_text("utf-8"))
        value["revision"] = "new-local-policy"
        (tmp_path / "policy.json").write_text(json.dumps(value), encoding="utf-8")
    elif change == "missing-authorization":
        config["authorization_dir"] = None
    else:
        (tmp_path / "identity.pub").write_text(key(9), encoding="ascii")
    profiles, authorizations = Mock(), Mock()
    monkeypatch.setattr(gateway_directory.gateway_provider, "apply_projection", profiles)
    monkeypatch.setattr(gateway_directory.gateway_authorization, "apply_projection", authorizations)
    with pytest.raises((PermissionError, ValueError)):
        gateway_directory.apply_update(update, **config)
    profiles.assert_not_called()
    authorizations.assert_not_called()


def test_public_update_parser_does_not_require_service_or_crypto_extras(tmp_path, monkeypatch):
    _, directory = fixture_files(tmp_path)
    original = builtins.__import__

    def core_only(name, *args, **kwargs):
        if name.startswith(("aiohttp", "joserfc", "cryptography")):
            raise ImportError("Fixture selected only the thin SDK")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", core_only)
    update = selected(tmp_path, directory)
    assert update.revision == 1
