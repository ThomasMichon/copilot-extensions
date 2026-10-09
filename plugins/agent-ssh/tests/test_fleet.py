from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from agent_procutil import no_window_flags
from fleet_contracts import ContractError, DriverSnapshot, decode_json

from agent_ssh import fleet

pytestmark = pytest.mark.guard


def sources(tmp_path: Path):
    registry = tmp_path / "registry.yaml"
    module = tmp_path / "module.yaml"
    registry.write_text(
        "transport: direct\nmachines:\n"
        "  - {name: Worker-A, hostname: hidden.example, identity_file: private-key}\n"
        "  - {name: worker-b, hostname: another.example}\n", encoding="utf-8",
    )
    module.write_text("module: direct\n", encoding="utf-8")
    return registry, module


def test_describe_exact_selection_without_connecting_or_enrolling(tmp_path, monkeypatch):
    registry, module = sources(tmp_path)
    from agent_ssh import probe, ssh_profile

    def forbidden(*args, **kwargs):
        raise AssertionError("fleet description must not execute, probe or emit a profile")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(probe, "probe_alias", forbidden)
    monkeypatch.setattr(ssh_profile, "write_fragment", forbidden)
    result = fleet.describe(registry, module, provider_instance="ssh-a", selected=["WORKER-A"])
    assert result.driver == "static-ssh"
    assert result.provider_instance == "ssh-a"
    assert asdict(result.targets[0]) == {
        "target_id": "Worker-A", "state": "configured", "capabilities": ("ssh",), "reason": None,
    }
    assert len(result.targets) == 1
    assert "hidden.example" not in json.dumps(result.to_dict())
    assert "private-key" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("selection", [
    [], ["missing"], ["Worker-A", "worker-a"], ["*"], ["-oProxyCommand=x"],
    ["x"] * 257, ["worker-a\nworker-b"],
])
def test_static_driver_rejects_implicit_ambiguous_or_invalid_selection(tmp_path, selection):
    registry, module = sources(tmp_path)
    with pytest.raises(ContractError):
        fleet.describe(registry, module, provider_instance="ssh-a", selected=selection)


def test_source_revision_changes_with_either_authoritative_input(tmp_path):
    registry, module = sources(tmp_path)
    kwargs = {"provider_instance": "ssh-a", "selected": ["Worker-A"]}
    before = fleet.describe(registry, module, **kwargs)
    module.write_text(module.read_text() + "# revised\n", encoding="utf-8")
    recipe_changed = fleet.describe(registry, module, **kwargs)
    registry.write_text(registry.read_text() + "# revised\n", encoding="utf-8")
    both_changed = fleet.describe(registry, module, **kwargs)
    assert len({item.source_revision for item in (before, recipe_changed, both_changed)}) == 3


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
@pytest.mark.parametrize("source", ["registry", "module"])
def test_source_encoding_matches_the_emitters_utf8_contract(tmp_path, encoding, source):
    registry, module = sources(tmp_path)
    path = registry if source == "registry" else module
    path.write_bytes(path.read_text(encoding="utf-8").encode(encoding))
    from agent_ssh import ssh_profile

    with pytest.raises(UnicodeError):
        ssh_profile.load_file(path)
    with pytest.raises(UnicodeError):
        fleet.describe(registry, module, provider_instance="ssh-a", selected=["Worker-A"])


@pytest.mark.parametrize("bad_source", ["duplicate", "module-mismatch", "oversized", "root"])
def test_static_driver_preserves_emitter_validation_and_resource_bounds(tmp_path, bad_source):
    registry, module = sources(tmp_path)
    if bad_source == "duplicate":
        registry.write_text(
            "transport: direct\nmachines: [{name: Worker-A}, {name: worker-a}]\n",
            encoding="utf-8",
        )
    elif bad_source == "module-mismatch":
        module.write_text("module: dtssh\n", encoding="utf-8")
    elif bad_source == "oversized":
        registry.write_bytes(b" " * (fleet.MAX_SOURCE_BYTES + 1))
    else:
        registry.write_text("[]", encoding="utf-8")
    with pytest.raises(ContractError):
        fleet.describe(registry, module, provider_instance="ssh-a", selected=["Worker-A"])


def test_real_cli_roundtrip_and_failure_are_read_only(tmp_path):
    registry, module = sources(tmp_path)
    command = [
        sys.executable, "-m", "agent_ssh", "fleet-targets", str(registry),
        "--module", str(module), "--provider-instance", "ssh-a", "--target", "worker-a",
    ]
    env = {**os.environ, "HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home")}
    result = subprocess.run(
        command, capture_output=True, timeout=15, env=env, creationflags=no_window_flags(),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8")
    parsed = DriverSnapshot.from_dict(decode_json(result.stdout.strip()))
    assert parsed.targets[0].target_id == "Worker-A"
    assert not (tmp_path / "home").exists()
    command[-1] = "missing"
    failed = subprocess.run(
        command, capture_output=True, timeout=15, env=env, creationflags=no_window_flags(),
    )
    assert failed.returncode == 2
    assert not failed.stdout
    assert b"absent from the authoritative registry" in failed.stderr
    assert not (tmp_path / "home").exists()
