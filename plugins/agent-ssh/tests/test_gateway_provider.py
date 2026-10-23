import base64
import builtins
import json
from pathlib import Path
import shutil
import struct
import subprocess
from unittest.mock import Mock

import pytest
from agent_procutil import no_window_kwargs
from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy

from agent_ssh import fragment_registry, gateway_provider
from agent_ssh.__main__ import main


def key(number):
    wire = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([number]) * 32
    return "ssh-ed25519 " + base64.b64encode(wire).decode()


def fixture_files(root: Path, *, revision=1, outbound=True):
    policy = {
        "schema": "fleet-manager.gateway-ssh-policy", "version": 1, "revision": "policy-a",
        "controllers": ["gateway-a"],
        "machines": [{
            "machine": name, "environment": "linux", "driver": "gateway", "role": "interactive",
            "inbound": True, "outbound": outbound if index == 0 else True,
            "controller": "gateway-a", "gateway_egress": True,
        } for index, name in enumerate(("worker-a", "worker-b"))],
    }
    directory = {
        "schema": "fleet-manager.gateway-ssh-directory", "version": 1,
        "revision": revision, "controller": "gateway-a",
        "policy_digest": GatewaySSHPolicy.from_dict(policy).policy_digest,
        "machines": [{
            "machine": name, "environment": "linux", "alias": name, "user": "owner",
            "public_key": key(index + 1), "host_key": key(index + 10), "reverse_port": 24001 + index,
        } for index, name in enumerate(("worker-a", "worker-b"))],
    }
    policy_path, directory_path = root / "policy.json", root / "directory.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    directory_path.write_text(json.dumps(directory), encoding="utf-8")
    identity = root / "approved identity"
    if not identity.exists():
        identity.write_bytes(b"opaque existing identity; this command never reads it")
    public = root / "identity.pub"
    public.write_text(key(1) + "\n", encoding="ascii")
    arguments = [
        "gateway-provider", "--policy", str(policy_path), "--directory", str(directory_path),
        "--machine", "worker-a", "--environment", "linux", "--gateway", "selected-gateway",
        "--identity-file", str(identity), "--public-key-file", str(public),
        "--config-d", str(root / "ssh" / "config.d"), "--ssh-config", str(root / "ssh" / "config"),
        "--json",
    ]
    return arguments, directory


def test_cli_preview_does_not_write_profile_or_state(tmp_path, capsys):
    arguments, _ = fixture_files(tmp_path)
    assert main(arguments) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["profiles"] == ["worker-a", "worker-b"] and not value["applied"]
    assert not value["enrollment_performed"] and not value["connection_attempted"]
    assert not (tmp_path / "ssh").exists()


def test_actual_cli_emitter_repeat_and_native_ssh_resolution(tmp_path, capsys):
    arguments, _ = fixture_files(tmp_path)
    assert main(arguments + ["--apply"]) == 0
    value = json.loads(capsys.readouterr().out)
    fragment = Path(value["fragment"])
    original = fragment.read_bytes()
    sources = fragment.parent.parent / ".agent-ssh-sources"
    retained = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sources.rglob("*")
        if path.is_file() and (path.suffix == ".json" or path.name == "known_hosts")
    }
    assert main(arguments + ["--apply"]) == 0
    capsys.readouterr()
    assert fragment.read_bytes() == original
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == before for path, before in retained.items())
    report = fragment_registry.FragmentRegistry(fragment.parent).refresh()
    assert report.permits_probe("worker-b")
    executable = shutil.which("ssh")
    if not executable:
        pytest.skip("Native OpenSSH configuration consumer unavailable")
    result = subprocess.run(
        [executable, "-G", "-F", str(tmp_path / "ssh" / "config"), "worker-b"],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0
    assert "proxyjump selected-gateway" in result.stdout.lower()
    assert "port 24002" in result.stdout and "stricthostkeychecking true" in result.stdout


@pytest.mark.parametrize("change", ["older", "equal-different", "wrong-key", "wrong-policy"])
def test_stale_or_unapproved_directory_preserves_existing_profile(tmp_path, capsys, change):
    arguments, directory = fixture_files(tmp_path, revision=2)
    assert main(arguments + ["--apply"]) == 0
    fragment = Path(json.loads(capsys.readouterr().out)["fragment"])
    before = fragment.read_bytes()
    if change == "older":
        directory["revision"] = 1
    elif change == "equal-different":
        directory["machines"][1]["reverse_port"] += 1
    elif change == "wrong-key":
        directory["machines"][0]["public_key"] = key(9)
    else:
        directory["policy_digest"] = "0" * 64
    (tmp_path / "directory.json").write_text(json.dumps(directory), encoding="utf-8")
    assert main(arguments + ["--apply"]) == 2
    output = capsys.readouterr()
    assert "[FAIL]" in output.err and fragment.read_bytes() == before


def test_new_directory_and_outbound_policy_converge_through_same_writer(tmp_path, capsys):
    arguments, _ = fixture_files(tmp_path)
    assert main(arguments + ["--apply"]) == 0
    fragment = Path(json.loads(capsys.readouterr().out)["fragment"])
    arguments, _ = fixture_files(tmp_path, revision=2, outbound=False)
    assert main(arguments + ["--apply"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["applied"] and value["profiles"] == []
    assert b"Host worker-" not in fragment.read_bytes()
    report = fragment_registry.FragmentRegistry(fragment.parent).refresh()
    assert "worker-b" not in report.active_aliases


def test_missing_sdk_is_an_actionable_failure_without_writes(tmp_path, monkeypatch, capsys):
    arguments, _ = fixture_files(tmp_path)
    monkeypatch.setattr(
        gateway_provider, "_sdk",
        Mock(side_effect=RuntimeError("Gateway provider setup requires a current agent-ssh[fleet] installation")),
    )
    assert main(arguments + ["--apply"]) == 2
    assert "agent-ssh[fleet]" in capsys.readouterr().err
    assert not (tmp_path / "ssh").exists()


def test_real_missing_sdk_import_does_not_break_standalone_profiles(tmp_path, monkeypatch, capsys):
    arguments, directory = fixture_files(tmp_path)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({
        "schema": "agent-ssh.gateway-profile", "version": 1, "gateway": "selected-gateway",
        "identity_file": str(tmp_path / "approved identity"),
        "machines": [{
            "name": row["alias"], "user": row["user"],
            "reverse_port": row["reverse_port"], "host_key": row["host_key"],
        } for row in directory["machines"]],
    }), encoding="utf-8")
    original = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name.startswith("fleet_manager"):
            raise ImportError("fixture selected SDK unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sdk)
    assert main([
        "gateway-profile", str(legacy), "--config-d", str(tmp_path / "standalone" / "config.d"),
        "--ssh-config", str(tmp_path / "standalone" / "config"), "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "configured"
    assert main(arguments + ["--apply"]) == 2
    assert "agent-ssh[fleet]" in capsys.readouterr().err
    assert not (tmp_path / "ssh").exists()


def test_corrupt_revision_state_is_not_reset(tmp_path, capsys):
    arguments, _ = fixture_files(tmp_path)
    assert main(arguments + ["--apply"]) == 0
    fragment = Path(json.loads(capsys.readouterr().out)["fragment"])
    before = fragment.read_bytes()
    floor = next(
        path for path in (tmp_path / "ssh" / ".agent-ssh-sources").glob("gateway-provider-*.json")
        if not path.name.startswith("gateway-provider-input-")
    )
    corrupted = json.loads(floor.read_text(encoding="utf-8"))
    corrupted["directory_digest"] = "z" * 64
    floor.write_text(json.dumps(corrupted), encoding="utf-8")
    assert main(arguments + ["--apply"]) == 2
    assert "revision state is invalid" in capsys.readouterr().err
    assert json.loads(floor.read_text(encoding="utf-8")) == corrupted
    assert fragment.read_bytes() == before


def test_failed_emission_retains_accepted_revision_for_exact_retry(tmp_path, monkeypatch, capsys):
    arguments, _ = fixture_files(tmp_path)
    assert main(arguments + ["--apply"]) == 0
    fragment = Path(json.loads(capsys.readouterr().out)["fragment"])
    before = fragment.read_bytes()
    arguments, directory = fixture_files(tmp_path, revision=2)
    directory["machines"].pop()
    (tmp_path / "directory.json").write_text(json.dumps(directory), encoding="utf-8")
    original = gateway_provider.gateway_profile.emit_profile
    monkeypatch.setattr(
        gateway_provider.gateway_profile, "emit_profile",
        Mock(side_effect=OSError("fixture interrupted before profile publication")),
    )
    assert main(arguments + ["--apply"]) == 2
    assert "fixture interrupted" in capsys.readouterr().err
    assert fragment.read_bytes() == before
    monkeypatch.setattr(gateway_provider.gateway_profile, "emit_profile", original)
    assert main(arguments + ["--apply"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["directory_revision"] == 2 and value["profiles"] == ["worker-a"]
