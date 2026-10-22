"""Pending producer proofs authorize withdrawal, never completed publication."""

import copy
import json
import os
import subprocess
import sys

import pytest

from fleet_manager import client_activation as activation, client_construction as construction
from fleet_manager import client_deactivation as deactivation, client_frontdoor as frontdoor
from fleet_manager.client_command_removal import unregister_client_frontdoor
from fleet_manager.planning import PlanError, digest
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate, completed_candidate
from test_client_admission import database_bytes, identity, independently_sign, retained
from test_client_construction import admit, signed_wheel as signed_wheel, target  # noqa: F401
from test_client_distribution import FLAGS, wheel as wheel  # noqa: F401
from test_client_frontdoor import register, selected as selected, states  # noqa: F401


def unregister(root, directory):
    return unregister_client_frontdoor(root, identity(), command_directory=directory)


def deactivate(root):
    return deactivation.deactivate_client_runtime(root, identity())


class _ProducerDeath(BaseException):
    pass


def _unfinished_publication(selected, primitive, monkeypatch, *, before_copy=False):
    root, fixture, staged, trusted, directory, namespace, _slot = selected
    publication = primitive._runtime_component("versioned_runtime_publication")

    def unfinished(receipt, _outcome):
        receipt.stream.close()
        raise _ProducerDeath

    def before_stage(*_args, **_kwargs):
        raise _ProducerDeath

    with monkeypatch.context() as patch:
        patch.setattr(publication.Receipt, "finish", unfinished)
        if before_copy:
            patch.setattr(publication, "_stage", before_stage)
        with pytest.raises(_ProducerDeath):
            register(root, fixture, staged, trusted, directory)
    assert not (namespace / frontdoor.REGISTERED).exists()
    value = construction._read(namespace / frontdoor.PENDING)
    return namespace / frontdoor.HISTORY / value["publication"] / "copy-forward.jsonl"


@pytest.mark.parametrize("boundary", ["header", "unfinished", "partial-finish", "partial-next-record"])
def test_pending_created_prefix_is_withdrawn_without_certifying_or_rewriting_it(
    selected, primitive, monkeypatch, boundary,
):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    proof = _unfinished_publication(selected, primitive, monkeypatch, before_copy=boundary == "header")
    if boundary in ("partial-finish", "partial-next-record"):
        with proof.open("ab") as stream:
            stream.write(b'{"kind":"finish",' if boundary == "partial-finish" else b'{"kind":"created",')
            stream.flush()
            os.fsync(stream.fileno())
    commands = frontdoor._commands(directory, slot)
    before, floor = states([proof, slot / "candidate-bytes"]), database_bytes(root)
    selectors = states([namespace / name for name in ("current-version", "last-known-good", "client-active.json")])
    with pytest.raises(PlanError, match="unknown"):
        register(root, fixture, staged, trusted, directory)

    def forbidden(*_args):
        raise AssertionError("pending withdrawal must not execute or authenticate a client")

    with monkeypatch.context() as patch:
        patch.setattr(construction, "_health", forbidden)
        patch.setattr(frontdoor, "_health", forbidden)
        result = unregister(root, directory)
    assert result["commands_absent"] and result["pending_registration_withdrawn"]
    assert not any(path.exists() for path in commands)
    assert states(before) == before and states(selectors) == selectors and database_bytes(root) == floor
    assert not (namespace / frontdoor.PENDING).exists()
    assert register(root, fixture, staged, trusted, directory)["frontdoor_registered"]


@pytest.mark.parametrize("fault", ["header", "created-record", "same-byte-receipt", "same-byte-command"])
def test_pending_prefix_does_not_adopt_missing_or_foreign_ownership(
    selected, primitive, monkeypatch, fault,
):
    root, _fixture, _staged, _trusted, directory, namespace, slot = selected
    proof = _unfinished_publication(selected, primitive, monkeypatch)
    commands = frontdoor._commands(directory, slot)
    if fault in ("header", "created-record"):
        data = proof.read_bytes()
        proof.write_bytes(data[:20] if fault == "header" else data.split(b"\n", 1)[0] + b"\n")
    else:
        original = proof if fault == "same-byte-receipt" else next(iter(commands))
        peer = original.with_name("foreign-replacement")
        peer.write_bytes(original.read_bytes())
        peer.chmod(original.stat().st_mode)
        os.replace(peer, original)
    before, floor = states([proof, *commands]), database_bytes(root)
    with pytest.raises(PlanError):
        unregister(root, directory)
    assert states(before) == before and database_bytes(root) == floor
    assert (namespace / frontdoor.PENDING).exists()
    assert not (namespace / frontdoor.REMOVING).exists()


@pytest.mark.timeout(90)
def test_abrupt_native_copy_producer_is_withdrawn_by_a_fresh_installed_owner(selected, tmp_path):
    root, fixture, _staged, _trusted, _directory, namespace, _slot = selected
    floor = database_bytes(root)
    user = root / "preserved-user-config"
    user.write_bytes(b"keep supplied client configuration\n")
    directory = tmp_path / "interrupted commands \u00e9\u4e2d"
    directory.mkdir(mode=0o700)
    area = tmp_path / "independent-producer"
    area.mkdir(mode=0o700)
    program = """
import hashlib,json,os,sys,uuid
from pathlib import Path
from fleet_manager import artifact_staging,client_admission,client_construction as construction
from fleet_manager import client_frontdoor as frontdoor
value=json.loads(sys.stdin.readline())
with client_admission._owned(Path(value["root"])) as (root,database,unchanged):
    with client_admission._connection(root,database_root=database,readonly=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        client_admission._read_state(connection,value["identity"])
        namespace=root/construction.NAMESPACE
        construction._namespace(root,value["identity"])
        active=construction._read(namespace/"client-active.json")
        directory=Path(value["directory"])
        commands=frontdoor._commands(directory,namespace/"versions"/active["candidate"])
        history=namespace/frontdoor.HISTORY
        history.mkdir(mode=0o700,exist_ok=True)
        intent={"schema":"fleet-manager.client-frontdoor","version":1,"active":active,
                "directory":str(directory),"publication":uuid.uuid4().hex,
                "target_digest":value["target_digest"]}
        workspace=history/intent["publication"]
        workspace.mkdir(mode=0o700)
        construction._write(workspace/"intent.json",intent)
        with artifact_staging._primitive() as primitive:
            publication=primitive._runtime_component("versioned_runtime_publication")
            bindings=[]
            for index,(command,content) in enumerate(commands.items()):
                source=workspace/("command."+str(index))
                with source.open("xb") as stream:
                    os.chmod(source,0o600)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                bindings.append(publication.CopyBinding(
                    source,command,hashlib.sha256(content).hexdigest(),executable=os.name!="nt",
                ))
            construction._write(namespace/frontdoor.PENDING,intent)
            for path in (workspace,history,namespace):
                client_admission._sync_directory(path)
            original=os.replace
            def stop_after_command(old,new,*args,**kwargs):
                result=original(old,new,*args,**kwargs)
                if Path(new).parent==directory:
                    os._exit(47)
                return result
            os.replace=stop_after_command
            with primitive.retention_lease(namespace,active["candidate"]),primitive._publication_lease(namespace):
                publication.CopyPublication(namespace,workspace,bindings,unchanged).publish()
"""
    request = {
        "root": str(root), "identity": identity(), "directory": str(directory),
        "target_digest": digest(target(fixture)),
    }
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program], cwd=area,
        input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=45, **FLAGS,
    )
    assert result.returncode == 47, result.stderr
    pending = construction._read(namespace / frontdoor.PENDING)
    proof = namespace / frontdoor.HISTORY / pending["publication"] / "copy-forward.jsonl"
    rows = [json.loads(line) for line in proof.read_bytes().splitlines()]
    assert rows[-1]["kind"] == "created" and not (namespace / frontdoor.REGISTERED).exists()
    commands = frontdoor._command_paths(directory)
    assert commands[0].is_file()
    before = states([proof, user, *[namespace / name for name in (
        "current-version", "last-known-good", "client-active.json",
    )]])
    retry = """
import json,sys
from pathlib import Path
from fleet_manager.client_command_removal import unregister_client_frontdoor
value=json.loads(sys.stdin.readline())
print(json.dumps(unregister_client_frontdoor(
    Path(value["root"]),value["identity"],command_directory=Path(value["directory"]),
)),flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", retry], cwd=area,
        input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=20, **FLAGS,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["commands_absent"] and report["pending_registration_withdrawn"]
    assert not any(path.exists() for path in commands)
    assert states(before) == before and database_bytes(root) == floor


def _unfinished_selector_publication(selected, primitive, monkeypatch, *, header_only=False):
    root, fixture, staged, trusted, _directory, namespace, _slot = selected
    deactivate(root)
    publication = primitive._runtime_component("versioned_runtime_publication")

    def unfinished(receipt, _outcome):
        receipt.stream.close()
        raise _ProducerDeath

    def before_created(*_args, **_kwargs):
        raise _ProducerDeath

    with monkeypatch.context() as patch:
        patch.setattr(publication.Receipt, "finish", unfinished)
        if header_only:
            patch.setattr(publication.Receipt, "created", before_created)
        with pytest.raises(_ProducerDeath):
            activate(root, fixture, staged, trusted)
    assert not (namespace / activation.ACTIVE).exists()
    value = construction._read(namespace / activation.PENDING)
    return namespace / activation.HISTORY / value["publication"] / "publication.jsonl"


def test_header_only_pending_command_attempt_retains_strict_registered_owner_proofs(
    selected, primitive, monkeypatch,
):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    paths = frontdoor._command_paths(directory)
    previous = construction._read(namespace / frontdoor.REGISTERED)
    before, floor = states(paths), database_bytes(root)
    changed_target = target(fixture)
    changed_target["revision"] += 1
    publication = primitive._runtime_component("versioned_runtime_publication")

    def unfinished(output, _outcome):
        output.stream.close()
        raise _ProducerDeath

    def before_created(*_args, **_kwargs):
        raise _ProducerDeath

    with monkeypatch.context() as patch:
        patch.setattr(publication.Receipt, "finish", unfinished)
        patch.setattr(publication.Receipt, "created", before_created)
        with pytest.raises(_ProducerDeath):
            register(root, fixture, staged, trusted, directory, current_target=lambda: changed_target)
    attempt = construction._read(namespace / frontdoor.PENDING)
    assert attempt != previous and construction._read(namespace / frontdoor.REGISTERED) == previous
    proof = namespace / frontdoor.HISTORY / attempt["publication"] / "copy-forward.jsonl"
    rows = [json.loads(line) for line in proof.read_bytes().splitlines()]
    assert len(rows) == 1 and rows[0]["kind"] == "header" and states(paths) == before
    evidence = states([proof, slot / "candidate-bytes"])
    result = unregister(root, directory)
    assert result["pending_registration_withdrawn"]
    assert not any(path.exists() for path in paths)
    assert states(evidence) == evidence and database_bytes(root) == floor


def test_header_only_pending_selector_upgrade_retains_strict_active_owner_proofs(
    selected, primitive, monkeypatch, tmp_path,
):
    root, fixture, _staged, _trusted, _directory, namespace, old_slot = selected
    previous = construction._read(namespace / activation.ACTIVE)
    paths = activation._targets(namespace)
    before = states(paths)
    newer = copy.deepcopy(fixture)
    signed = independently_sign([{**fixture["proof"]["payload"],
                                 "sequence": fixture["proof"]["payload"]["sequence"] + 1}], tmp_path)
    newer["proof"] = {**newer["proof"], **signed["proofs"][0]}
    newer["policy"]["keys"][0]["public_key"] = signed["public_key"]
    publication = primitive._runtime_component("versioned_runtime_publication")

    def unfinished(output, _outcome):
        output.stream.close()
        raise _ProducerDeath

    def before_created(*_args, **_kwargs):
        raise _ProducerDeath

    with retained(newer, tmp_path) as (staged, trusted):
        admit(root, newer, staged, trusted)
        _, new_slot = completed_candidate(root, newer, staged, trusted, primitive)
        floor = database_bytes(root)
        with monkeypatch.context() as patch:
            patch.setattr(publication.Receipt, "finish", unfinished)
            patch.setattr(publication.Receipt, "created", before_created)
            with pytest.raises(_ProducerDeath):
                activate(root, newer, staged, trusted)
        attempt = construction._read(namespace / activation.PENDING)
        assert attempt != previous and construction._read(namespace / activation.ACTIVE) == previous
        proof = namespace / activation.HISTORY / attempt["publication"] / "publication.jsonl"
        rows = [json.loads(line) for line in proof.read_bytes().splitlines()]
        assert len(rows) == 1 and rows[0]["kind"] == "header" and states(paths) == before
        evidence = states([proof, old_slot / "candidate-bytes", new_slot / "candidate-bytes"])
        result = deactivate(root)
        assert result["pending_activation_withdrawn"] and result["selectors_absent"]
        assert not any(path.exists() for path in paths)
        assert states(evidence) == evidence and database_bytes(root) == floor


@pytest.mark.parametrize("boundary", ["header", "unfinished", "partial-finish"])
def test_pending_selector_prefix_is_withdrawal_only_and_preserves_original_evidence(
    selected, primitive, monkeypatch, boundary,
):
    root, fixture, staged, trusted, _directory, namespace, slot = selected
    proof = _unfinished_selector_publication(
        selected, primitive, monkeypatch, header_only=boundary == "header",
    )
    if boundary == "partial-finish":
        with proof.open("ab") as stream:
            stream.write(b'{"kind":"finish",')
            stream.flush()
            os.fsync(stream.fileno())
    before, floor = states([proof, slot / "candidate-bytes"]), database_bytes(root)
    selectors = states(path for path in activation._targets(namespace) if path.exists())
    with pytest.raises(PlanError):
        activate(root, fixture, staged, trusted)
    assert states(before) == before and states(selectors) == selectors

    def forbidden(*_args):
        raise AssertionError("selector withdrawal must not execute or authenticate a runtime")

    with monkeypatch.context() as patch:
        patch.setattr(construction, "_health", forbidden)
        result = deactivate(root)
    assert result["selectors_absent"] and result["pending_activation_withdrawn"]
    assert not any(path.exists() for path in activation._targets(namespace))
    assert states(before) == before and database_bytes(root) == floor
    assert activate(root, fixture, staged, trusted)["active"]


@pytest.mark.parametrize("fault", ["missing-created-proof", "same-byte-selector"])
def test_pending_selector_prefix_preserves_every_unproved_present_target(
    selected, primitive, monkeypatch, fault,
):
    root, _fixture, _staged, _trusted, _directory, namespace, _slot = selected
    proof = _unfinished_selector_publication(selected, primitive, monkeypatch)
    if fault == "missing-created-proof":
        proof.write_bytes(proof.read_bytes().split(b"\n", 1)[0] + b"\n")
    else:
        target = activation._targets(namespace)[0]
        peer = namespace / "foreign-selector"
        peer.write_bytes(target.read_bytes())
        peer.chmod(target.stat().st_mode)
        os.replace(peer, target)
    before, floor = states([proof, *activation._targets(namespace)]), database_bytes(root)
    with pytest.raises(PlanError):
        deactivate(root)
    assert states(before) == before and database_bytes(root) == floor
    assert (namespace / activation.PENDING).exists() and not (namespace / activation.REMOVING).exists()


def test_acknowledged_active_receipt_stays_strict_even_with_matching_pending_reference(selected):
    root, _fixture, _staged, _trusted, _directory, namespace, _slot = selected
    active = construction._read(namespace / activation.ACTIVE)
    construction._write(namespace / activation.PENDING, active)
    proof = namespace / activation.HISTORY / active["publication"] / "publication.jsonl"
    proof.write_bytes(proof.read_bytes()[:-3])
    before = states([proof, *activation._targets(namespace)])
    with pytest.raises(PlanError):
        deactivate(root)
    assert states(before) == before and not (namespace / activation.REMOVING).exists()


@pytest.mark.timeout(90)
def test_abrupt_native_selector_producer_is_withdrawn_by_a_fresh_installed_owner(selected, tmp_path):
    root, _fixture, _staged, _trusted, _directory, namespace, slot = selected
    active = construction._read(namespace / activation.ACTIVE)
    deactivate(root)
    floor = database_bytes(root)
    program = """
import json,os,sys,uuid
from pathlib import Path
from fleet_manager import artifact_staging,client_admission,client_activation as activation
from fleet_manager import client_construction as construction
value=json.loads(sys.stdin.readline())
with client_admission._owned(Path(value["root"])) as (root,database,unchanged):
    with client_admission._connection(root,database_root=database,readonly=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        client_admission._read_state(connection,value["identity"])
        namespace=root/construction.NAMESPACE
        construction._namespace(root,value["identity"])
        active={**value["active"],"publication":uuid.uuid4().hex}
        activation._state(active,value["identity"])
        with artifact_staging._primitive() as primitive:
            publication=primitive._runtime_component("versioned_runtime_publication")
            with primitive.retention_lease(namespace,active["candidate"]):
                with publication.target_leases(namespace,activation._targets(namespace)):
                    unchanged()
                    workspace=activation._prepare(namespace,active,None,publication)
                    original=os.replace
                    def stop_after_selector(old,new,*args,**kwargs):
                        result=original(old,new,*args,**kwargs)
                        if Path(new)==namespace/"current-version":
                            os._exit(47)
                        return result
                    os.replace=stop_after_selector
                    publication.publish_selectors(
                        namespace,active["candidate"],workspace/"publication.jsonl",lambda *_args:None,
                    )
"""
    request = {"root": str(root), "identity": identity(), "active": active}
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program], cwd=tmp_path,
        input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=30, **FLAGS,
    )
    assert result.returncode == 47, result.stderr
    pending = construction._read(namespace / activation.PENDING)
    proof = namespace / activation.HISTORY / pending["publication"] / "publication.jsonl"
    assert json.loads(proof.read_bytes().splitlines()[-1])["kind"] == "created"
    assert (namespace / "current-version").is_file() and not (namespace / activation.ACTIVE).exists()
    before = states([proof, slot / "candidate-bytes"])
    retry = """
import json,sys
from pathlib import Path
from fleet_manager.client_deactivation import deactivate_client_runtime
value=json.loads(sys.stdin.readline())
print(json.dumps(deactivate_client_runtime(Path(value["root"]),value["identity"])),flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", retry], cwd=tmp_path,
        input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=20, **FLAGS,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["selectors_absent"] and report["pending_activation_withdrawn"]
    assert not any(path.exists() for path in activation._targets(namespace))
    assert states(before) == before and database_bytes(root) == floor
