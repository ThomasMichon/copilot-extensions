"""Normal setup keeps provider identity and configuration ownership separate."""

import json
import os
from pathlib import Path

import pytest

from fleet_manager import continuity_bootstrap, gateway_host, ssh_setup
from fleet_manager.planning import PlanError, canonical
from fleet_manager.private_state import create_private_file, private_directory
from test_ssh_machine import continuity_config


def selected(tmp_path):
    value, _, _, _ = continuity_config(tmp_path)
    credential = value.pop("credential_source")
    request = tmp_path / "request.json"
    document = {
        "schema": ssh_setup.SCHEMA, "version": 1,
        "snapshot": str(tmp_path / "snapshot"), "root": str(tmp_path / "installation"),
        "selection": {
            "source_commit": "a" * 40, "source_tree": "b" * 40,
            "runtime_version": "0.1.0.source.abc.profile.def", "payload_hash": "c" * 64,
        },
        "authority": "authority-a", "installation": "installation-a", "ssh": value,
    }
    with os.fdopen(create_private_file(request), "wb") as stream:
        stream.write(canonical(document))
    arguments = {
        "source": Path(credential["source"]), "harness": credential["harness"],
        "state_root": Path(credential["state_root"]),
    }
    return request, document, arguments


def test_normal_setup_description_never_reads_identity_or_installs(tmp_path, monkeypatch):
    request, document, arguments = selected(tmp_path)
    monkeypatch.setattr(
        continuity_bootstrap, "_state",
        lambda *_args, **_kwargs: pytest.fail("public setup inspection read private machine state"),
    )
    before = request.read_bytes()
    description = ssh_setup.run(request, **arguments, apply=False)
    assert description["selection"] == document["selection"]
    assert "private_key" not in json.dumps(description)
    assert not Path(document["root"]).exists()
    assert request.read_bytes() == before


def test_setup_cli_keeps_projection_material_out_of_results_and_refusals(tmp_path, capsys):
    from fleet_manager.__main__ import main

    request, document, arguments = selected(tmp_path)
    command = [
        "ssh-setup", "--request", str(request), "--source", str(arguments["source"]),
        "--harness", arguments["harness"], "--state-root", str(arguments["state_root"]),
    ]
    assert main(command) == 0
    result = capsys.readouterr()
    assert json.loads(result.out)["selection"] == document["selection"]
    projection = json.loads(arguments["source"].read_bytes())
    assert projection["grant"] not in result.out + result.err
    command[command.index("--harness") + 1] = "other-harness"
    assert main(command) == 1
    refused = capsys.readouterr()
    assert not refused.out and "continuity-harness-mismatch" in refused.err
    assert projection["grant"] not in refused.err
    assert not Path(document["root"]).exists()


def test_setup_cli_reports_missing_optional_runtime_without_private_diagnostics(tmp_path, monkeypatch, capsys):
    from fleet_manager.__main__ import main

    request, document, arguments = selected(tmp_path)

    def unavailable(*_args, **_kwargs):
        raise ImportError("private diagnostic detail")

    monkeypatch.setattr(ssh_setup, "run", unavailable)
    assert main([
        "ssh-setup", "--request", str(request), "--source", str(arguments["source"]),
        "--harness", arguments["harness"], "--state-root", str(arguments["state_root"]),
    ]) == 1
    result = capsys.readouterr()
    assert not result.out and "runtime closure is unavailable" in result.err
    assert "private diagnostic detail" not in result.err
    assert not Path(document["root"]).exists()

def test_normal_setup_prepares_real_host_inputs_once_without_export_or_lease(tmp_path):
    request, document, arguments = selected(tmp_path)
    identity = arguments["state_root"] / "machine.json"
    before = identity.read_bytes(), identity.stat().st_mtime_ns
    description = ssh_setup.run(request, **arguments, apply=True)
    root = Path(document["root"])
    configuration = root / "ssh-client.json"
    actual = json.loads(configuration.read_bytes())
    assert actual["credential_source"] == {
        "provider": "local-projection-v1", **{name: str(value) for name, value in arguments.items()},
    }
    assert gateway_host.optional_selection(root) == description["host"]
    assert not Path(document["ssh"]["state_dir"]).exists()
    original = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (configuration, root / gateway_host.CONFIG)
    }
    assert ssh_setup.run(request, **arguments, apply=True) == description
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in original} == original
    assert (identity.read_bytes(), identity.stat().st_mtime_ns) == before
    assert not (tmp_path / "proof.pem").exists() and not (tmp_path / "access").exists()
    assert not (root / "current-version").exists()


def test_normal_setup_preserves_unknown_host_launch_before_identity_or_configuration(tmp_path, monkeypatch):
    request, document, arguments = selected(tmp_path)
    root = Path(document["root"])
    private_directory(root)
    private_directory(root / gateway_host.STATE)
    launch = root / gateway_host.STATE / "launch.json"
    original = canonical({"generation": "0.1.0", "token": "a" * 32})
    with os.fdopen(create_private_file(launch), "wb") as stream:
        stream.write(original)
    monkeypatch.setattr(
        continuity_bootstrap, "_state",
        lambda *_args, **_kwargs: pytest.fail("unknown launch read private machine state"),
    )
    with pytest.raises(PlanError, match="unresolved host launch"):
        ssh_setup.run(request, **arguments, apply=True)
    assert launch.read_bytes() == original
    assert not (root / "ssh-client.json").exists() and not (root / gateway_host.CONFIG).exists()


@pytest.mark.parametrize("change", [
    "scope", "foreign", "writer", "identity-overlap", "credential-override", "public-key",
])
def test_normal_setup_refuses_before_writing_and_preserves_existing_inputs(tmp_path, monkeypatch, change):
    request, document, arguments = selected(tmp_path)
    root = Path(document["root"])
    precious = None
    if change == "scope":
        document["ssh"]["authentication"]["principal"] = "other-principal"
    elif change == "writer":
        document["root"] = document["ssh"]["config_d"]
    elif change == "identity-overlap":
        document["root"] = str(arguments["state_root"] / "installation")
    elif change == "credential-override":
        document["ssh"]["credential_source"] = {"provider": "other"}
    elif change == "public-key":
        Path(document["ssh"]["public_key_file"]).write_text("not a public key")
    else:
        root.mkdir()
        precious = root / gateway_host.CONFIG
        with os.fdopen(create_private_file(precious), "wb") as stream:
            stream.write(b'{"precious":"foreign configuration"}')
    request.write_bytes(canonical(document))
    before = precious.read_bytes() if precious is not None else None
    monkeypatch.setattr(
        continuity_bootstrap, "_state",
        lambda *_args, **_kwargs: pytest.fail("invalid setup read private identity"),
    )
    with pytest.raises((PlanError, ValueError)):
        ssh_setup.run(request, **arguments, apply=True)
    assert not (root / "ssh-client.json").exists()
    if precious is not None:
        assert precious.read_bytes() == before
    else:
        assert not root.exists()
