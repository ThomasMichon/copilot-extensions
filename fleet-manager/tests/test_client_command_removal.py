"""Real physical ownership, pending withdrawal and resumable removal boundaries."""

import copy
import json
import os
import subprocess
import sys

import pytest

from fleet_manager import client_command_removal as removal
from fleet_manager import client_frontdoor as frontdoor, client_construction as construction
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_admission import database_bytes, identity
from test_client_construction import signed_wheel as signed_wheel  # noqa: F401
from test_client_distribution import FLAGS, wheel as wheel  # noqa: F401
from test_client_frontdoor import register, selected as selected, states  # noqa: F401


def unregister(root, directory, **kwargs):
    return removal.unregister_client_frontdoor(
        root, kwargs.pop("identity", identity()), command_directory=directory, **kwargs,
    )


def test_owned_unregister_and_fresh_noop_preserve_runtime_floor_config_and_evidence(selected, monkeypatch):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    commands = frontdoor._commands(directory, slot)
    preserved = [
        namespace / "current-version", namespace / "last-known-good",
        namespace / "client-active.json",
    ]
    # Retained runtime, user state and even unrelated command-directory content survive.
    user = root / "user-config"
    user.write_bytes(b"keep custom user configuration")
    peer = directory / "foreign-tool"
    peer.write_bytes(b"not ours")
    preserved.extend([user, peer, slot / "candidate-bytes"])
    preserved = [path for path in preserved if path.exists()]
    before, floor = states(preserved), database_bytes(root)
    history = states(path for path in (namespace / frontdoor.HISTORY).rglob("*") if path.is_file())

    def forbidden(*_args):
        raise AssertionError("withdrawal must not execute or authenticate a candidate")

    monkeypatch.setattr(construction, "_health", forbidden)
    monkeypatch.setattr(frontdoor, "_health", forbidden)
    result = unregister(root, directory)
    assert result["commands_absent"] and not result["frontdoor_registered"]
    assert result["mutations_performed"] and not result["reused"]
    for flag in ("runtime_selectors_modified", "runtime_removed", "installation_retired",
                 "path_modified", "live_readiness_verified"):
        assert not result[flag]
    assert not any(path.exists() for path in commands)
    assert states(before) == before and states(history) == history
    assert database_bytes(root) == floor
    assert not any((namespace / name).exists() for name in (
        frontdoor.REGISTERED, frontdoor.PENDING, frontdoor.REMOVING,
    ))
    again = unregister(root, directory)
    assert again["reused"] and not again["mutations_performed"]
    assert states(before) == before and states(history) == history and database_bytes(root) == floor


@pytest.mark.parametrize("change", ["same-byte-peer", "edited", "proof", "identity", "directory", "marker"])
def test_foreign_or_corrupt_removal_refuses_before_any_command_deletion(selected, tmp_path, change):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    commands = frontdoor._commands(directory, slot)
    command = next(iter(commands))
    kwargs = {}
    if change == "same-byte-peer":
        peer = directory / "peer"
        peer.write_bytes(command.read_bytes())
        peer.chmod(command.stat().st_mode)
        os.replace(peer, command)
    elif change == "edited":
        command.write_bytes(b"foreign modification\n")
    elif change == "proof":
        value = construction._read(namespace / frontdoor.REGISTERED)
        (namespace / frontdoor.HISTORY / value["publication"] / "copy-forward.jsonl").write_bytes(b"{}\n")
    elif change == "identity":
        wrong = copy.deepcopy(identity())
        wrong["installation"] = "other-installation"
        kwargs["identity"] = wrong
    elif change == "directory":
        directory = tmp_path / "different-commands"
        directory.mkdir(mode=0o700)
    else:
        construction._write(namespace / frontdoor.REMOVING, {"unknown": True})
    before, floor = states(commands), database_bytes(root)
    with pytest.raises(PlanError):
        unregister(root, directory, **kwargs)
    assert states(before) == before and database_bytes(root) == floor


def test_truncated_pending_producer_proof_never_becomes_deletion_authority(selected):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    value = construction._read(namespace / frontdoor.REGISTERED)
    construction._write(namespace / frontdoor.PENDING, value)
    proof = namespace / frontdoor.HISTORY / value["publication"] / "copy-forward.jsonl"
    proof.write_bytes(proof.read_bytes()[:-3])
    before = states(frontdoor._commands(directory, slot))
    with pytest.raises(PlanError):
        unregister(root, directory)
    assert states(before) == before and not (namespace / frontdoor.REMOVING).exists()


@pytest.mark.timeout(45)
def test_independently_installed_owner_without_optional_dependencies_can_withdraw(selected, wheel, tmp_path):
    import shutil

    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    package = tmp_path / "dependency-free-owner"
    uv = shutil.which("uv")
    assert uv, "real no-dependency owner installation requires the runner's uv"
    installed = subprocess.run(
        [uv, "pip", "install", "--python", sys.executable, "--target", str(package),
         "--no-index", "--no-deps", "--link-mode", "copy", str(wheel[0])],
        capture_output=True, text=True, timeout=20, **FLAGS,
    )
    assert installed.returncode == 0, installed.stderr
    inputs = tmp_path / "identity.json"
    inputs.write_text(json.dumps(identity()), encoding="utf-8")
    program = """
import importlib.util,sys
sys.path.insert(0,sys.argv.pop(1))
assert importlib.util.find_spec("cryptography") is None
assert importlib.util.find_spec("pip") is None
from importlib.metadata import distribution
entry=next(value for value in distribution("fleet-manager").entry_points
           if value.group=="console_scripts" and value.name=="fleet-manager")
raise SystemExit(entry.load()())
"""
    floor = database_bytes(root)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program, str(package),
         "client-state", "unregister-commands", "--root", str(root),
         "--identity", str(inputs), "--command-directory", str(directory)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, **FLAGS,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["commands_absent"]
    assert not any(path.exists() for path in frontdoor._commands(directory, slot))
    assert database_bytes(root) == floor and slot.is_dir()
