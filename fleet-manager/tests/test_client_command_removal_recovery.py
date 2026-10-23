"""Pending command publications and interrupted owned withdrawal recovery."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fleet_manager import client_command_removal as removal
from fleet_manager import client_frontdoor as frontdoor, client_construction as construction
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate, completed_candidate
from test_client_admission import database_bytes, identity, independently_sign, retained
from test_client_construction import admit, signed_wheel as signed_wheel  # noqa: F401
from test_client_distribution import FLAGS
from test_client_frontdoor import register, selected as selected, states  # noqa: F401


def unregister(root, directory, **kwargs):
    return removal.unregister_client_frontdoor(
        root, kwargs.pop("identity", identity()), command_directory=directory, **kwargs,
    )


@pytest.mark.parametrize("boundary", ["command", "health", "receipt", "ack"])
def test_pending_attempt_can_be_withdrawn_without_rollback_or_reexecution(selected, monkeypatch, boundary):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    commands = frontdoor._commands(directory, slot)
    original_replace, original_unlink = os.replace, Path.unlink

    def failed_replace(old, new):
        if new == (next(iter(commands)) if boundary == "command" else namespace / frontdoor.REGISTERED):
            raise OSError("injected native command or registration receipt failure")
        return original_replace(old, new)

    def failed_unlink(path, *args, **kwargs):
        if path == namespace / frontdoor.PENDING:
            raise OSError("injected acknowledgement failure")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if boundary in ("command", "receipt"):
            patch.setattr(os, "replace", failed_replace)
        elif boundary == "ack":
            patch.setattr(Path, "unlink", failed_unlink)
        else:
            def failed_health(*_args):
                raise PlanError("injected local health failure")
            patch.setattr(frontdoor, "_health", failed_health)
        with pytest.raises(PlanError, match="outcome is unknown"):
            register(root, fixture, staged, trusted, directory)
    floor = database_bytes(root)
    assert (namespace / frontdoor.PENDING).is_file()
    result = unregister(root, directory)
    assert result["commands_absent"] and result["mutations_performed"]
    assert not any(path.exists() for path in commands)
    assert database_bytes(root) == floor
    # A new explicit registration can follow withdrawal; no floor reset or downgrade.
    assert register(root, fixture, staged, trusted, directory)["frontdoor_registered"]


@pytest.mark.parametrize("boundary", ["command", "registered", "pending", "marker"])
def test_removal_interruptions_retain_intent_block_registration_and_retry(selected, monkeypatch, boundary):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    value = construction._read(namespace / frontdoor.REGISTERED)
    construction._write(namespace / frontdoor.PENDING, value)
    commands = frontdoor._commands(directory, slot)
    unlink = Path.unlink
    target = {
        "command": next(iter(commands)), "registered": namespace / frontdoor.REGISTERED,
        "pending": namespace / frontdoor.PENDING, "marker": namespace / frontdoor.REMOVING,
    }[boundary]

    def interrupted(path, *args, **kwargs):
        if path == target:
            raise OSError("injected removal interruption")
        return unlink(path, *args, **kwargs)

    floor = database_bytes(root)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            unregister(root, directory)
    assert (namespace / frontdoor.REMOVING).is_file()
    with pytest.raises(PlanError, match="removal is unsettled"):
        register(root, fixture, staged, trusted, directory)
    assert unregister(root, directory)["commands_absent"]
    assert not any(path.exists() for path in commands)
    assert database_bytes(root) == floor


def test_foreign_replacement_after_partial_removal_is_preserved_on_retry(selected, monkeypatch):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    command = next(iter(frontdoor._commands(directory, slot)))
    unlink = Path.unlink

    def interrupted(path, *args, **kwargs):
        if path == namespace / frontdoor.REGISTERED:
            raise OSError("injected late removal failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            unregister(root, directory)
    command.write_bytes(b"new foreign owner\n")
    before = states([command])
    with pytest.raises(PlanError, match="unattributed"):
        unregister(root, directory)
    assert states([command]) == before and (namespace / frontdoor.REMOVING).is_file()


@pytest.mark.parametrize("boundary", ["health", "receipt"])
def test_pending_upgrade_withdraws_both_proved_generations_without_restoring_old_client(
    selected, primitive, tmp_path, monkeypatch, boundary,
):
    root, fixture, staged, trusted, directory, namespace, old_slot = selected
    first = register(root, fixture, staged, trusted, directory)
    newer = copy.deepcopy(fixture)
    payload = {**fixture["proof"]["payload"], "sequence": first["admitted"]["sequence"] + 1}
    signed = independently_sign([payload], tmp_path)
    newer["proof"] = {**newer["proof"], **signed["proofs"][0]}
    newer["policy"]["keys"][0]["public_key"] = signed["public_key"]
    with retained(newer, tmp_path) as (next_stage, policy):
        admit(root, newer, next_stage, policy)
        _, next_slot = completed_candidate(
            root, newer, next_stage, policy, primitive,
            prepare=lambda slot: construction._bootstrap(slot, construction.artifact_staging.diagnose_client()),
        )
        activate(root, newer, next_stage, policy)
        replace = os.replace

        def fail_ack(old, new):
            if new == namespace / frontdoor.REGISTERED:
                raise OSError("injected update acknowledgement failure")
            return replace(old, new)

        def fail_health(*_args):
            raise PlanError("injected update command health failure")

        with monkeypatch.context() as patch:
            if boundary == "health":
                patch.setattr(frontdoor, "_health", fail_health)
            else:
                patch.setattr(os, "replace", fail_ack)
            with pytest.raises(PlanError, match="outcome is unknown"):
                register(root, newer, next_stage, policy, directory)
        floor = database_bytes(root)
        assert unregister(root, directory)["commands_absent"]
        assert not any(path.exists() for path in frontdoor._commands(directory, next_slot))
        assert old_slot.is_dir() and next_slot.is_dir()
        assert primitive.current_version(namespace) == next_slot.name
        assert database_bytes(root) == floor
        assert register(root, newer, next_stage, policy, directory)["frontdoor_registered"]


@pytest.mark.timeout(45)
def test_process_death_after_first_unlink_is_recovered_by_a_distinct_installed_owner(selected, tmp_path):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    commands = frontdoor._commands(directory, slot)
    floor = database_bytes(root)
    area = tmp_path / "independent-removal-owner"
    area.mkdir(mode=0o700)
    program = """
import json,os,sys
from pathlib import Path
from fleet_manager.client_command_removal import unregister_client_frontdoor
request=json.loads(sys.stdin.readline())
if request["interrupt"]:
    original=Path.unlink
    def stop_after_command(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path.parent==Path(request["directory"]):
            os._exit(47)
        return result
    Path.unlink=stop_after_command
print(json.dumps(unregister_client_frontdoor(
    Path(request["root"]),request["identity"],command_directory=Path(request["directory"]),
)),flush=True)
"""
    for interrupted in (True, False):
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program], cwd=area,
            input=json.dumps({
                "root": str(root), "identity": identity(), "directory": str(directory),
                "interrupt": interrupted,
            }) + "\n", capture_output=True, text=True, timeout=20, **FLAGS,
        )
        if interrupted:
            assert result.returncode == 47, result.stderr
            assert (namespace / frontdoor.REMOVING).is_file()
            assert not next(iter(commands)).exists()
        else:
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["commands_absent"]
    assert not any(path.exists() for path in commands)
    assert database_bytes(root) == floor
