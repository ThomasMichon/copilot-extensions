"""Real selector receipts and fail-closed publication; no live registrations."""

import copy
import json
import os
import subprocess
import sys

import pytest

from fleet_manager import client_activation as activation
from fleet_manager import client_construction as construction
from fleet_manager.planning import PlanError, digest
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_admission import database_bytes, identity, independently_sign, retained, root_at, selection
from test_client_construction import (
    admit, built_client as built_client, candidate, signed_wheel as signed_wheel, target,
)
from test_client_distribution import FLAGS, wheel as wheel  # noqa: F401


def activate(root, fixture, staged, trusted, **kwargs):
    try:
        return activation.activate_client_runtime(
            root, identity(), selection(fixture), staged,
            current_policy=kwargs.pop("current_policy", lambda: trusted),
            current_target=kwargs.pop("current_target", lambda: target(fixture)),
            clock=lambda: 150, **kwargs,
        )
    except PlanError as error:
        if error.__context__ is not None:
            print("CLIENT_ACTIVATION_CAUSE " + str(error.__context__), flush=True)
        raise


def completed_candidate(root, fixture, staged, trusted, primitive, *, prepare=None):
    namespace = construction._namespace(root, identity())
    _, _, binding = construction._request(
        identity(), selection(fixture), staged, lambda: trusted, lambda: target(fixture),
    )
    slot = candidate(root, fixture, primitive)
    with primitive.construction_lease(namespace, slot.name):
        construction._construction_intent(namespace, slot.name, slot, binding)
        primitive.slot(namespace, slot.name)
        (slot / "candidate-bytes").write_bytes(b"unit candidate, not a real interpreter\n")
        if prepare is not None:
            prepare(slot)
        completed = {
            "schema": "fleet-manager.client-constructed", "version": 1,
            "input": binding, "runtime_sha256": construction._tree(primitive, slot),
        }
        construction._write(slot / construction.COMPLETION, completed)
        primitive.mark_complete(namespace, slot.name, payload_hash=digest(completed))
    return namespace, slot


@pytest.fixture
def selected(tmp_path, primitive, signed_wheel, monkeypatch):
    root = root_at(tmp_path)
    fixture = signed_wheel
    with retained(fixture, tmp_path) as (staged, trusted):
        admit(root, fixture, staged, trusted)
        namespace, slot = completed_candidate(
            root, fixture, staged, trusted, primitive,
        )

        def health(_slot, checkpoint, _descriptor, _evidence):
            checkpoint()
            return {"unit_health_stub": True}

        monkeypatch.setattr(construction, "_health", health)
        yield root, fixture, staged, trusted, namespace, slot


def test_real_selectors_receipts_and_immutable_noop(selected, primitive):
    root, fixture, staged, trusted, namespace, slot = selected
    floor = database_bytes(root)
    result = activate(root, fixture, staged, trusted)
    assert result["active"] and not result["reused"] and result["mutations_performed"]
    assert not result["frontdoor_registered"] and not result["live_readiness_verified"]
    assert primitive.current_version(namespace) == slot.name
    assert not (namespace / "current").exists()
    assert not (namespace / activation.PENDING).exists()
    active = construction._read(namespace / activation.ACTIVE)
    publication = primitive._runtime_component("versioned_runtime_publication")
    directory = namespace / activation.HISTORY / active["publication"]
    publication.check_owned(
        directory / "context.json", [directory / "publication.jsonl"],
        activation._targets(namespace), activation._expected(namespace, slot.name),
    )
    before = {path: (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
              for path in [*activation._targets(namespace), namespace / activation.ACTIVE]}
    retried = activate(root, fixture, staged, trusted)
    assert retried["active"] and retried["reused"] and not retried["mutations_performed"]
    assert {path: (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
            for path in before} == before
    assert database_bytes(root) == floor


@pytest.mark.parametrize("change", ["unattributed", "same-byte-peer", "foreign-owner", "pending"])
def test_foreign_or_uncertain_selectors_refuse_before_health(selected, monkeypatch, change):
    root, fixture, staged, trusted, namespace, _slot = selected
    if change == "unattributed":
        (namespace / "current-version").write_bytes(b"foreign\n")
    else:
        activate(root, fixture, staged, trusted)
        if change == "same-byte-peer":
            path = namespace / "current-version"
            peer = namespace / "peer"
            peer.write_bytes(path.read_bytes())
            os.replace(peer, path)
        elif change == "foreign-owner":
            value = construction._read(namespace / activation.ACTIVE)
            value["identity"]["installation"] = "another-installation"
            construction._write(namespace / activation.ACTIVE, value)
        else:
            construction._write(namespace / activation.PENDING, {"uncertain": True})
    floor = database_bytes(root)
    selectors = {path: path.read_bytes() for path in activation._targets(namespace) if path.exists()}

    def no_health(*args):
        raise AssertionError("foreign or pending selectors must refuse before execution")

    monkeypatch.setattr(construction, "_health", no_health)
    with pytest.raises(PlanError):
        activate(root, fixture, staged, trusted)
    assert {path: path.read_bytes() for path in selectors} == selectors
    assert database_bytes(root) == floor


@pytest.mark.parametrize("change", ["revoked", "target", "tampered", "health"])
def test_failed_revalidation_preserves_existing_active_and_floor(selected, monkeypatch, change):
    root, fixture, staged, trusted, namespace, slot = selected
    activate(root, fixture, staged, trusted)
    before = {path: path.read_bytes()
              for path in [*activation._targets(namespace), namespace / activation.ACTIVE]}
    floor = database_bytes(root)
    kwargs = {}
    if change == "revoked":
        revoked = copy.deepcopy(trusted)
        revoked["keys"][0]["revoked"] = True
        kwargs["current_policy"] = lambda: revoked
    elif change == "target":
        incompatible = target(fixture)
        incompatible["components"]["cli"]["api"] = 65535
        kwargs["current_target"] = lambda: incompatible
    elif change == "tampered":
        (slot / "candidate-bytes").write_bytes(b"changed after completion\n")
    else:
        def failed(*args):
            raise PlanError("injected health failure")
        monkeypatch.setattr(construction, "_health", failed)
    with pytest.raises(PlanError):
        activate(root, fixture, staged, trusted, **kwargs)
    assert {path: path.read_bytes() for path in before} == before
    assert not (namespace / activation.PENDING).exists()
    assert database_bytes(root) == floor


@pytest.mark.parametrize("boundary", ["first-selector", "second-selector", "active-receipt"])
def test_interrupted_owned_publication_retries_forward_with_fresh_proofs(selected, monkeypatch, boundary):
    root, fixture, staged, trusted, namespace, _slot = selected
    original = os.replace
    target_name = {
        "first-selector": "current-version", "second-selector": "last-known-good",
        "active-receipt": activation.ACTIVE,
    }[boundary]

    def interrupted(old, new):
        if new == namespace / target_name:
            raise OSError("injected publication interruption")
        return original(old, new)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            activate(root, fixture, staged, trusted)
    pending = construction._read(namespace / activation.PENDING)
    directory = namespace / activation.HISTORY / pending["publication"]
    assert (directory / "context.json").is_file()
    assert (directory / "intent.json").is_file()
    assert (directory / "publication.jsonl").is_file()
    recovered = activate(root, fixture, staged, trusted)
    assert recovered["active"] and not recovered["reused"]
    assert not (namespace / activation.PENDING).exists()
    assert (directory / "publication.1.jsonl").is_file()


@pytest.mark.parametrize("change", ["truncated-proof", "same-byte-peer", "revoked", "changed-target"])
def test_unknown_or_foreign_pending_output_cannot_authorize_retry(selected, monkeypatch, change):
    root, fixture, staged, trusted, namespace, _slot = selected
    original = os.replace

    def interrupted(old, new):
        if new == namespace / activation.ACTIVE:
            raise OSError("injected interruption after selector publication")
        return original(old, new)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            activate(root, fixture, staged, trusted)
    active = construction._read(namespace / activation.PENDING)
    directory = namespace / activation.HISTORY / active["publication"]
    kwargs = {}
    if change == "truncated-proof":
        receipt = directory / "publication.jsonl"
        receipt.write_bytes(receipt.read_bytes()[:-1])
    elif change == "same-byte-peer":
        path = namespace / "current-version"
        peer = namespace / "peer"
        peer.write_bytes(path.read_bytes())
        os.replace(peer, path)
    elif change == "revoked":
        revoked = copy.deepcopy(trusted)
        revoked["keys"][0]["revoked"] = True
        kwargs["current_policy"] = lambda: revoked
    else:
        changed = target(fixture)
        changed["revision"] += 1
        kwargs["current_target"] = lambda: changed
    before = {path: (path.stat().st_ino, path.read_bytes())
              for path in activation._targets(namespace)}
    floor = database_bytes(root)
    with pytest.raises(PlanError):
        activate(root, fixture, staged, trusted, **kwargs)
    assert {path: (path.stat().st_ino, path.read_bytes()) for path in before} == before
    assert database_bytes(root) == floor
    assert (namespace / activation.PENDING).exists()


def test_policy_revoked_by_health_cannot_publish(selected, monkeypatch):
    root, fixture, staged, trusted, namespace, _slot = selected
    policy = copy.deepcopy(trusted)

    def health(_slot, checkpoint, _descriptor, _evidence):
        checkpoint()
        policy["keys"][0]["revoked"] = True
        return {"unit_health_stub": True}

    monkeypatch.setattr(construction, "_health", health)
    with pytest.raises(PlanError, match="revoked"):
        activate(root, fixture, staged, trusted, current_policy=lambda: policy)
    assert not any(path.exists() for path in activation._targets(namespace))
    assert not (namespace / activation.PENDING).exists()


@pytest.mark.parametrize("boundary", ["before-publication", "after-publication"])
def test_compatible_target_cannot_change_under_a_recorded_publication_intent(
    selected, primitive, monkeypatch, boundary,
):
    root, fixture, staged, trusted, namespace, _slot = selected
    current = target(fixture)
    publication = primitive._runtime_component("versioned_runtime_publication")
    owner, name = ((activation, "_prepare") if boundary == "before-publication"
                   else (publication, "publish_selectors"))
    original = getattr(owner, name)

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        current["revision"] += 1
        return result

    monkeypatch.setattr(owner, name, changed)
    with pytest.raises(PlanError, match="outcome is unknown"):
        activate(root, fixture, staged, trusted, current_target=lambda: current)
    assert (namespace / activation.PENDING).is_file()
    assert not (namespace / activation.ACTIVE).exists()
    assert any(path.exists() for path in activation._targets(namespace)) == (
        boundary == "after-publication"
    )


def test_higher_admitted_activation_preserves_prior_candidate_and_refuses_downgrade(
    selected, primitive, tmp_path,
):
    root, fixture, staged, trusted, namespace, old_slot = selected
    first = activate(root, fixture, staged, trusted)
    newer = copy.deepcopy(fixture)
    payload = {**fixture["proof"]["payload"], "sequence": first["admitted"]["sequence"] + 1}
    signed = independently_sign([payload], tmp_path)
    newer["proof"] = {**newer["proof"], **signed["proofs"][0]}
    newer["policy"]["keys"][0]["public_key"] = signed["public_key"]
    from fleet_manager.client_admission import admit_client_release

    with retained(newer, tmp_path) as (next_stage, next_policy):
        admit_client_release(root, identity(), selection(newer), next_stage, next_policy, clock=lambda: 150)
        _, new_slot = completed_candidate(root, newer, next_stage, next_policy, primitive)
        floor = database_bytes(root)
        result = activate(root, newer, next_stage, next_policy)
        assert result["active"] and not result["reused"] and result["candidate"] != first["candidate"]
        assert primitive.current_version(namespace) == new_slot.name
        active = construction._read(namespace / activation.ACTIVE)
        intent = construction._read(namespace / activation.HISTORY / active["publication"] / "intent.json")
        assert intent["previous"]["candidate"] == old_slot.name
        assert old_slot.is_dir() and (old_slot / primitive.COMPLETE_MARKER).is_file()
        with pytest.raises(PlanError, match="exact durably admitted"):
            activate(root, fixture, staged, trusted)
        assert primitive.current_version(namespace) == new_slot.name
        assert database_bytes(root) == floor


@pytest.mark.timeout(90)
def test_independent_installed_owners_serialize_real_health_and_selector_publication(
    primitive, built_client, tmp_path,
):
    root, fixture, staged, trusted, user, floor, construction_report = built_client
    program = """
import json,sys
from pathlib import Path
from fleet_manager.artifact_staging import stage_authenticated_artifacts
from fleet_manager.client_activation import activate_client_runtime
from fleet_manager.planning import canonical
value=json.loads(sys.stdin.readline())
with stage_authenticated_artifacts(
    canonical(value["manifest"]),value["proof"],value["policy"],value["sources"],
    authority=value["identity"]["authority"],installation=value["identity"]["installation"],
    role="client",staging_parent=Path(value["stage"]),clock=lambda:150,
) as staged:
    print(json.dumps(activate_client_runtime(
        Path(value["root"]),value["identity"],value["selection"],staged,
        current_policy=lambda:value["policy"],current_target=lambda:value["target"],
        clock=lambda:150,
    )),flush=True)
"""
    processes = []
    try:
        for index in range(2):
            area = tmp_path / str(index)
            area.mkdir(mode=0o700)
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", program], cwd=area,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **FLAGS,
            )
            processes.append(process)
            process.stdin.write(json.dumps({
                **fixture, "policy": trusted, "root": str(root), "identity": identity(),
                "selection": selection(fixture), "target": target(fixture), "stage": str(area),
            }) + "\n")
            process.stdin.close()
            process.stdin = None
        reports = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            assert process.returncode == 0, stderr
            reports.append(json.loads(stdout))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    assert sorted(report["reused"] for report in reports) == [False, True]
    result = reports[0]
    assert result["health"]["status"] == "installed"
    assert result["active"] and result["runtime_sha256"] == construction_report["runtime_sha256"]
    assert primitive.current_version(root / construction.NAMESPACE) == result["candidate"]
    repeated = activate(root, fixture, staged, trusted)
    assert repeated["reused"] and repeated["health"]["status"] == "installed"
    assert user.read_bytes() == b"preserved configuration\n"
    assert database_bytes(root) == floor
