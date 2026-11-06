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


@pytest.mark.parametrize("scope", [
    "fragments", "sources", "profile-locks", "root-locks", "authorization", "file-parent",
])
def test_writer_preflight_refuses_invalid_derived_directories_without_effects(tmp_path, scope):
    from agent_ssh import gateway_profile, ssh_profile

    config = arguments(tmp_path)
    config["ssh_config"] = tmp_path / "root-config" / "config"
    if scope == "file-parent":
        config["config_d"] = tmp_path / "regular-parent" / "config.d"
    destination = {
        "fragments": config["config_d"],
        "sources": config["config_d"].parent / gateway_profile._SOURCE_DIR,
        "profile-locks": config["config_d"].parent / ssh_profile._LOCK_DIR_NAME,
        "root-locks": config["ssh_config"].parent / ssh_profile._LOCK_DIR_NAME,
        "authorization": config["authorization_dir"],
        "file-parent": tmp_path / "regular-parent",
    }[scope]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"retained owner content")
    state = tmp_path / "consumer-state"
    with pytest.raises((ValueError, OSError), match="directory"):
        gateway_directory.validate_writer_destinations(
            config_d=config["config_d"], ssh_config=config["ssh_config"],
            authorization_dir=config["authorization_dir"],
            authorization_target={"machine": config["machine"], "environment": config["environment"]},
            authorization_controller=config["controller"],
        )
    assert destination.read_bytes() == b"retained owner content"
    assert not state.exists()


@pytest.mark.parametrize("overlap", [
    "authorization-fragments", "authorization-parent", "root-fragments",
    "fragments-sources", "root-locks",
])
def test_writer_preflight_rejects_conflicting_output_scopes_without_creating_them(tmp_path, overlap):
    from agent_ssh import gateway_profile, ssh_profile

    config = arguments(tmp_path)
    if overlap == "authorization-fragments":
        config["authorization_dir"] = config["config_d"]
    elif overlap == "authorization-parent":
        config["authorization_dir"] = config["config_d"].parent
    elif overlap == "root-fragments":
        config["ssh_config"] = config["config_d"] / "config"
    elif overlap == "fragments-sources":
        config["config_d"] = config["config_d"].parent / gateway_profile._SOURCE_DIR
    else:
        config["ssh_config"] = config["config_d"].parent / ssh_profile._LOCK_DIR_NAME / "config"
    with pytest.raises(ValueError, match="separate"):
        gateway_directory.validate_writer_destinations(
            config_d=config["config_d"], ssh_config=config["ssh_config"],
            authorization_dir=config["authorization_dir"],
            authorization_target={"machine": config["machine"], "environment": config["environment"]},
            authorization_controller=config["controller"],
        )
    assert not list(tmp_path.iterdir())


def test_writer_preflight_allows_the_profile_writer_to_create_a_shared_parent(tmp_path):
    config = arguments(tmp_path)
    config["authorization_dir"] = config["config_d"].parent / "authorization"
    gateway_directory.validate_writer_destinations(
        config_d=config["config_d"], ssh_config=config["ssh_config"],
        authorization_dir=config["authorization_dir"],
        authorization_target={"machine": config["machine"], "environment": config["environment"]},
        authorization_controller=config["controller"],
    )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["foreign-entry", "permissions", "missing-parent"])
def test_writer_preflight_validates_authorization_owner_without_creation_or_repair(tmp_path, failure):
    import os
    from agent_ssh import gateway_authorization

    config = arguments(tmp_path)
    path = config["authorization_dir"]
    if failure == "foreign-entry":
        gateway_authorization._root(path)
        (path / "retained").write_bytes(b"retained owner content")
    elif failure == "permissions":
        path.mkdir()
        if os.name != "nt":
            path.chmod(0o755)
    else:
        config["authorization_dir"] = path / "missing-parent" / "authorization"
    before = {entry: entry.read_bytes() for entry in tmp_path.rglob("*") if entry.is_file()}
    with pytest.raises((ValueError, PermissionError)):
        gateway_directory.validate_writer_destinations(
            config_d=config["config_d"], ssh_config=config["ssh_config"],
            authorization_dir=config["authorization_dir"],
            authorization_target={"machine": config["machine"], "environment": config["environment"]},
            authorization_controller=config["controller"],
        )
    assert all(entry.read_bytes() == value for entry, value in before.items())
    if failure == "missing-parent":
        assert not path.exists()


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
