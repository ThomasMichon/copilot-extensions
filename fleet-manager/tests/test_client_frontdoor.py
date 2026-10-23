"""Real created-file registration proofs and installed retained client commands."""

import copy
import codecs
import os
from pathlib import Path

import pytest

from fleet_manager import client_frontdoor as frontdoor, client_construction as construction
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate, completed_candidate
from test_client_admission import database_bytes, identity, independently_sign, retained, root_at, selection
from test_client_construction import (
    admit, built_client as built_client, signed_wheel as signed_wheel, target,
)
from test_client_distribution import FLAGS, wheel as wheel  # noqa: F401


def register(root, fixture, staged, trusted, directory, **kwargs):
    return frontdoor.register_client_frontdoor(
        root, identity(), selection(fixture), staged, command_directory=directory,
        current_policy=kwargs.pop("current_policy", lambda: trusted),
        current_target=kwargs.pop("current_target", lambda: target(fixture)),
        clock=lambda: 150, **kwargs,
    )


def command_directory(parent, name="commands"):
    directory = parent / name
    directory.mkdir(mode=0o700)
    return directory


@pytest.fixture
def selected(tmp_path, primitive, signed_wheel, monkeypatch):
    root = root_at(tmp_path)
    directory = command_directory(tmp_path)
    fixture = signed_wheel
    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        namespace, slot = completed_candidate(
            root, fixture, staged, trusted, primitive,
            prepare=lambda slot: construction._bootstrap(slot, construction.artifact_staging.diagnose_client()),
        )
        monkeypatch.setattr(construction, "_health", lambda *_args: {"unit_health_stub": True})
        monkeypatch.setattr(frontdoor, "_health", lambda *_args: None)
        activate(root, fixture, staged, trusted)
        yield root, fixture, staged, trusted, directory, namespace, slot


def states(paths):
    return {path: (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes()) for path in paths}


def test_created_command_proofs_and_fresh_exact_noop(selected, primitive):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    floor = database_bytes(root)
    selectors = states([*namespace.glob("*version"), namespace / "last-known-good"])
    environment = os.environ.copy()
    result = register(root, fixture, staged, trusted, directory)
    assert result["frontdoor_registered"] and result["active"] and not result["reused"]
    assert result["mutations_performed"] and not result["path_modified"]
    assert not result["live_readiness_verified"]
    commands = list(frontdoor._commands(directory, slot))
    assert result["commands"] == [str(path) for path in commands]
    assert all(str(construction._python(slot)).encode() in path.read_bytes()
               for path in commands if path.suffix != ".cmd")
    if os.name == "nt":
        assert (directory / "fleet-manager.ps1").read_bytes().startswith(codecs.BOM_UTF8)
        assert (directory / "fleet-manager.cmd").read_bytes().isascii()
        assert b"%~dp0fleet-manager.ps1" in (directory / "fleet-manager.cmd").read_bytes()
        assert b"-ExecutionPolicy Bypass" in (directory / "fleet-manager.cmd").read_bytes()
    receipt = construction._read(namespace / frontdoor.REGISTERED)
    workspace = namespace / frontdoor.HISTORY / receipt["publication"]
    publication = primitive._runtime_component("versioned_runtime_publication")
    publication.check_owned(
        workspace / "copy-context.json", [workspace / "copy-forward.jsonl"],
        commands, frontdoor._expected(frontdoor._commands(directory, slot)),
    )
    before = states([*commands, namespace / frontdoor.REGISTERED])
    repeated = register(root, fixture, staged, trusted, directory)
    assert repeated["reused"] and not repeated["mutations_performed"]
    assert states(before) == before
    assert database_bytes(root) == floor and states(selectors) == selectors
    assert os.environ == environment
    assert not (namespace / frontdoor.PENDING).exists()


@pytest.mark.parametrize("content", [b"", b'{"unfinished":', b'{"complete":true}'])
def test_registration_does_not_adopt_or_remove_abandoned_pending_journal(selected, content):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    abandoned = namespace / (frontdoor.PENDING + ".tmp")
    abandoned.write_bytes(content)
    before = states([abandoned])
    floor = database_bytes(root)
    assert register(root, fixture, staged, trusted, directory)["frontdoor_registered"]
    assert states([abandoned]) == before and database_bytes(root) == floor
    assert all(path.is_file() for path in frontdoor._commands(directory, slot))
    assert not (namespace / frontdoor.PENDING).exists()


@pytest.mark.parametrize("change", [
    "unattributed", "same-byte-peer", "modified", "pending", "proof", "directory",
])
def test_foreign_or_unknown_registration_refuses_without_execution(selected, monkeypatch, change, tmp_path):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    commands = frontdoor._commands(directory, slot)
    command = next(iter(commands))
    if change == "unattributed":
        command.write_bytes(b"foreign\n")
    else:
        register(root, fixture, staged, trusted, directory)
        receipt = construction._read(namespace / frontdoor.REGISTERED)
        if change == "same-byte-peer":
            peer = directory / "peer"
            peer.write_bytes(command.read_bytes())
            peer.chmod(command.stat().st_mode)
            os.replace(peer, command)
        elif change == "modified":
            command.write_bytes(b"foreign edits\n")
        elif change == "pending":
            construction._write(namespace / frontdoor.PENDING, {"unknown": True})
        elif change == "proof":
            (namespace / frontdoor.HISTORY / receipt["publication"] / "copy-forward.jsonl").write_bytes(b"{}\n")
        else:
            directory = command_directory(tmp_path, "different")
    floor = database_bytes(root)
    before = states(path for path in commands if path.exists())

    def forbidden(*_args):
        raise AssertionError("foreign registration executed a candidate")

    monkeypatch.setattr(construction, "_health", forbidden)
    with pytest.raises(PlanError):
        register(root, fixture, staged, trusted, directory)
    assert states(before) == before and database_bytes(root) == floor


@pytest.mark.parametrize("change", ["revoked", "incompatible", "runtime", "target-revision"])
def test_fresh_inputs_and_health_cannot_reuse_a_saved_receipt(selected, monkeypatch, change):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    before = states([*frontdoor._commands(directory, slot), namespace / frontdoor.REGISTERED])
    floor = database_bytes(root)
    kwargs = {}
    if change == "revoked":
        policy = copy.deepcopy(trusted)
        policy["keys"][0]["revoked"] = True
        kwargs["current_policy"] = lambda: policy
    elif change == "incompatible":
        incompatible = target(fixture)
        incompatible["components"]["cli"]["api"] = 65535
        kwargs["current_target"] = lambda: incompatible
    elif change == "runtime":
        (slot / "candidate-bytes").write_bytes(b"foreign runtime bytes\n")
    else:
        def changed(*_args):
            requested["revision"] += 1
            return {"unit_health_stub": True}
        requested = target(fixture)
        kwargs["current_target"] = lambda: requested
        monkeypatch.setattr(construction, "_health", changed)
    with pytest.raises(PlanError):
        register(root, fixture, staged, trusted, directory, **kwargs)
    assert states(before) == before and database_bytes(root) == floor


@pytest.mark.parametrize("boundary", ["command", "health", "receipt", "ack"])
def test_interrupted_registration_is_explicit_unknown_and_preserves_recovery(selected, monkeypatch, boundary):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    replace = os.replace
    unlink = Path.unlink
    commands = frontdoor._commands(directory, slot)

    def failed_replace(old, new):
        if new == (next(iter(commands)) if boundary == "command" else namespace / frontdoor.REGISTERED):
            raise OSError("injected registration write failure")
        return replace(old, new)

    def failed_unlink(path, *args, **kwargs):
        if path == namespace / frontdoor.PENDING:
            raise OSError("injected registration acknowledgement failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if boundary in ("command", "receipt"):
            patch.setattr(os, "replace", failed_replace)
        elif boundary == "health":
            def failed(*_args):
                raise PlanError("injected registered-command health failure")
            patch.setattr(frontdoor, "_health", failed)
        else:
            patch.setattr(Path, "unlink", failed_unlink)
        with pytest.raises(PlanError, match="outcome is unknown"):
            register(root, fixture, staged, trusted, directory)
    assert (namespace / frontdoor.PENDING).is_file()
    assert list((namespace / frontdoor.HISTORY).glob("*/copy-forward.jsonl"))
    with pytest.raises(PlanError, match="outcome is unknown"):
        register(root, fixture, staged, trusted, directory)
    if boundary in ("health", "command"):
        assert not any(path.exists() for path in commands)
    else:
        assert all(path.exists() for path in commands)


def test_fresh_foreign_command_at_copy_boundary_is_preserved(selected, monkeypatch):
    root, fixture, staged, trusted, directory, _namespace, slot = selected
    command = next(iter(frontdoor._commands(directory, slot)))

    def health(*_args):
        command.write_bytes(b"peer command\n")
        return {"unit_health_stub": True}

    monkeypatch.setattr(construction, "_health", health)
    with pytest.raises(PlanError):
        register(root, fixture, staged, trusted, directory)
    assert command.read_bytes() == b"peer command\n"


def test_same_byte_foreign_replacement_after_publication_is_not_rolled_back(selected, monkeypatch):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    command = next(iter(frontdoor._commands(directory, slot)))
    peer_state = None

    def replaced(*_args):
        nonlocal peer_state
        peer = directory / "peer"
        peer.write_bytes(command.read_bytes())
        peer.chmod(command.stat().st_mode)
        os.replace(peer, command)
        peer_state = states([command])

    monkeypatch.setattr(frontdoor, "_health", replaced)
    with pytest.raises(PlanError, match="outcome is unknown"):
        register(root, fixture, staged, trusted, directory)
    assert states([command]) == peer_state
    assert (namespace / frontdoor.PENDING).is_file()
    assert not (namespace / frontdoor.REGISTERED).exists()


def test_higher_active_candidate_updates_only_attributed_commands(selected, primitive, tmp_path):
    root, fixture, staged, trusted, directory, namespace, old_slot = selected
    first = register(root, fixture, staged, trusted, directory)
    newer = copy.deepcopy(fixture)
    payload = {**fixture["proof"]["payload"], "sequence": first["admitted"]["sequence"] + 1}
    signed = independently_sign([payload], tmp_path)
    newer["proof"] = {**newer["proof"], **signed["proofs"][0]}
    newer["policy"]["keys"][0]["public_key"] = signed["public_key"]
    from fleet_manager.client_admission import admit_client_release

    with retained(newer, tmp_path) as (next_stage, policy):
        admit_client_release(root, identity(), selection(newer), next_stage, policy, clock=lambda: 150)
        _, next_slot = completed_candidate(
            root, newer, next_stage, policy, primitive,
            prepare=lambda slot: construction._bootstrap(slot, construction.artifact_staging.diagnose_client()),
        )
        activate(root, newer, next_stage, policy)
        floor = database_bytes(root)
        result = register(root, newer, next_stage, policy, directory)
        assert result["candidate"] == next_slot.name and not result["reused"]
        assert all(str(construction._python(next_slot)).encode() in path.read_bytes()
                   for path in frontdoor._commands(directory, next_slot) if path.suffix != ".cmd")
        assert old_slot.is_dir() and (old_slot / primitive.COMPLETE_MARKER).is_file()
        assert database_bytes(root) == floor
