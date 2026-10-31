"""Physically proved selector withdrawal and retry, without deleting runtimes."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fleet_manager import client_activation as activation, client_construction as construction
from fleet_manager import client_deactivation as deactivation, client_frontdoor as frontdoor
from fleet_manager.client_command_removal import unregister_client_frontdoor
from fleet_manager.planning import PlanError
from test_artifact_staging import primitive as primitive  # noqa: F401
from test_client_activation import activate, completed_candidate
from test_client_admission import database_bytes, identity, independently_sign, retained
from test_client_construction import admit, signed_wheel as signed_wheel  # noqa: F401
from test_client_distribution import FLAGS
from test_client_frontdoor import register, selected as selected, states  # noqa: F401


def deactivate(root, **kwargs):
    return deactivation.deactivate_client_runtime(root, kwargs.pop("identity", identity()), **kwargs)


def test_owned_deactivation_and_noop_preserve_candidates_floor_config_and_history(selected, monkeypatch):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    user = root / "user-config"
    user.write_bytes(b"custom configuration")
    preserved = states([
        user, *[path for path in slot.rglob("*") if path.is_file()],
        *[path for path in (namespace / activation.HISTORY).rglob("*") if path.is_file()],
        *[path for path in (namespace / "client-inputs").rglob("*") if path.is_file()],
    ])
    floor = database_bytes(root)

    def forbidden(*_args):
        raise AssertionError("withdrawal must not execute a runtime or authenticate a release")

    with monkeypatch.context() as patch:
        patch.setattr(construction, "_health", forbidden)
        for reused in (False, True):
            result = deactivate(root)
            assert result["selectors_absent"] and not result["active"]
            assert result["reused"] == reused and result["mutations_performed"] != reused
            for flag in ("runtime_removed", "installation_retired", "frontdoor_registered",
                         "path_modified", "live_readiness_verified"):
                assert not result[flag]
            assert not any((namespace / name).exists() for name in (
                *activation.SELECTORS, activation.ACTIVE, activation.PENDING, activation.REMOVING,
            ))
            assert states(preserved) == preserved and database_bytes(root) == floor
    # Re-activation is still a fresh explicit admission/policy/health transition.
    assert activate(root, fixture, staged, trusted)["active"]


@pytest.mark.parametrize("registration", ["registered", "pending", "removing"])
def test_every_command_registration_state_blocks_selector_effects(selected, registration):
    root, fixture, staged, trusted, directory, namespace, slot = selected
    register(root, fixture, staged, trusted, directory)
    value = construction._read(namespace / frontdoor.REGISTERED)
    if registration != "registered":
        construction._write(namespace / (
            frontdoor.PENDING if registration == "pending" else frontdoor.REMOVING
        ), value)
    before = states([*activation._targets(namespace), *frontdoor._command_paths(directory)])
    with pytest.raises(PlanError, match="unregistration first"):
        deactivate(root)
    assert states(before) == before
    if registration == "registered":
        unregister_client_frontdoor(root, identity(), command_directory=directory)
        assert deactivate(root)["selectors_absent"] and slot.is_dir()


@pytest.mark.parametrize("change", ["same-byte-peer", "edited", "proof", "identity", "current", "marker"])
def test_unowned_or_corrupt_selector_withdrawal_refuses_before_any_deletion(selected, change):
    root, _fixture, _staged, _trusted, _directory, namespace, _slot = selected
    target = namespace / activation.SELECTORS[0]
    kwargs = {}
    if change == "same-byte-peer":
        peer = namespace / "peer-selector"
        peer.write_bytes(target.read_bytes())
        peer.chmod(target.stat().st_mode)
        os.replace(peer, target)
    elif change == "edited":
        target.write_bytes(b"foreign-selector\n")
    elif change == "proof":
        value = construction._read(namespace / activation.ACTIVE)
        proof = namespace / activation.HISTORY / value["publication"] / "publication.jsonl"
        proof.write_bytes(proof.read_bytes()[:-3])
    elif change == "identity":
        wrong = copy.deepcopy(identity())
        wrong["installation"] = "other-installation"
        kwargs["identity"] = wrong
    elif change == "current":
        (namespace / "current").write_bytes(b"foreign runtime reference\n")
    else:
        construction._write(namespace / activation.REMOVING, {"unknown": True})
    before, floor = states(activation._targets(namespace)), database_bytes(root)
    with pytest.raises(PlanError):
        deactivate(root, **kwargs)
    assert states(before) == before and database_bytes(root) == floor


@pytest.mark.parametrize("boundary", ["selector", "active", "pending", "marker"])
def test_interrupted_deactivation_blocks_republication_and_retries_owned_absence(
    selected, monkeypatch, boundary,
):
    root, fixture, staged, trusted, directory, namespace, _slot = selected
    construction._write(namespace / activation.PENDING, construction._read(namespace / activation.ACTIVE))
    target = {
        "selector": activation._targets(namespace)[1], "active": namespace / activation.ACTIVE,
        "pending": namespace / activation.PENDING, "marker": namespace / activation.REMOVING,
    }[boundary]
    unlink = Path.unlink

    def interrupted(path, *args, **kwargs):
        if path == target:
            raise OSError("injected deactivation failure")
        return unlink(path, *args, **kwargs)

    floor = database_bytes(root)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            deactivate(root)
    assert (namespace / activation.REMOVING).is_file()
    for operation in (
        lambda: activate(root, fixture, staged, trusted),
        lambda: register(root, fixture, staged, trusted, directory),
    ):
        with pytest.raises(PlanError, match="removal is unsettled"):
            operation()
    assert deactivate(root)["selectors_absent"]
    assert database_bytes(root) == floor


def test_foreign_replacement_after_partial_removal_is_preserved(selected, monkeypatch):
    root, _fixture, _staged, _trusted, _directory, namespace, _slot = selected
    unlink = Path.unlink

    def interrupted(path, *args, **kwargs):
        if path == namespace / activation.ACTIVE:
            raise OSError("injected late deactivation failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(PlanError, match="outcome is unknown"):
            deactivate(root)
    target = activation._targets(namespace)[0]
    target.write_bytes(b"new foreign owner\n")
    before = states([target])
    with pytest.raises(PlanError, match="client selectors could not establish owned withdrawal"):
        deactivate(root)
    assert states(before) == before and (namespace / activation.REMOVING).is_file()


@pytest.mark.parametrize("boundary", ["before-publication", "selector", "receipt"])
def test_first_activation_can_be_withdrawn_without_inventing_previous_ownership(
    selected, primitive, monkeypatch, boundary,
):
    root, fixture, staged, trusted, _directory, namespace, slot = selected
    deactivate(root)
    publication = primitive._runtime_component("versioned_runtime_publication")
    replace = os.replace

    def fail_publication(*_args, **_kwargs):
        raise OSError("injected failure before first native producer receipt")

    def fail_replace(old, new):
        if new == (activation._targets(namespace)[0] if boundary == "selector"
                   else namespace / activation.ACTIVE):
            raise OSError("injected first selector publication failure")
        return replace(old, new)

    with monkeypatch.context() as patch:
        if boundary == "before-publication":
            patch.setattr(publication, "publish_selectors", fail_publication)
        else:
            patch.setattr(os, "replace", fail_replace)
        expected = ("could not establish owned selectors"
                    if boundary == "before-publication" else "outcome is unknown")
        with pytest.raises(PlanError, match=expected):
            activate(root, fixture, staged, trusted)
    assert (namespace / activation.PENDING).is_file() == (boundary != "before-publication")
    if boundary == "before-publication":
        assert not any(path.exists() for path in activation._targets(namespace))
    floor = database_bytes(root)
    assert deactivate(root)["selectors_absent"]
    assert slot.is_dir() and database_bytes(root) == floor


def test_selector_withdrawal_does_not_release_live_runtime_retention(selected, primitive):
    root, _fixture, _staged, _trusted, _directory, namespace, slot = selected
    with primitive.retention_lease(namespace, slot.name):
        assert deactivate(root)["selectors_absent"]
        with primitive._cleanup_lease(namespace, slot.name) as cleanup:
            assert cleanup is False
        assert slot.is_dir()


@pytest.mark.parametrize("boundary", ["selector", "receipt", "ack"])
def test_pending_upgrade_withdraws_proved_old_and_new_selectors_without_runtime_rollback(
    selected, tmp_path, primitive, monkeypatch, boundary,
):
    root, fixture, _staged, _trusted, _directory, namespace, old_slot = selected
    newer = copy.deepcopy(fixture)
    signed = independently_sign([{**fixture["proof"]["payload"],
                                 "sequence": fixture["proof"]["payload"]["sequence"] + 1}], tmp_path)
    newer["proof"] = {**newer["proof"], **signed["proofs"][0]}
    newer["policy"]["keys"][0]["public_key"] = signed["public_key"]
    with retained(newer, tmp_path) as (staged, trusted):
        admit(root, newer, staged, trusted)
        _, new_slot = completed_candidate(root, newer, staged, trusted, primitive)
        replace, unlink = os.replace, Path.unlink

        def fail_publication(old, new):
            if new == (activation._targets(namespace)[1] if boundary == "selector"
                       else namespace / activation.ACTIVE):
                raise OSError("injected selector publication failure")
            return replace(old, new)

        def fail_ack(path, *args, **kwargs):
            if path == namespace / activation.PENDING:
                raise OSError("injected activation acknowledgement failure")
            return unlink(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            if boundary == "ack":
                patch.setattr(Path, "unlink", fail_ack)
            else:
                patch.setattr(os, "replace", fail_publication)
            with pytest.raises(PlanError, match="outcome is unknown"):
                activate(root, newer, staged, trusted)
        floor = database_bytes(root)
        assert deactivate(root)["selectors_absent"]
        assert old_slot.is_dir() and new_slot.is_dir() and database_bytes(root) == floor
        assert activate(root, newer, staged, trusted)["active"]


@pytest.mark.timeout(45)
def test_separate_installed_process_retries_after_owner_death(selected, tmp_path):
    root, _fixture, _staged, _trusted, _directory, namespace, slot = selected
    program = """
import json,os,sys
from pathlib import Path
from fleet_manager.client_deactivation import deactivate_client_runtime
request=json.loads(sys.stdin.readline())
if request["interrupt"]:
    original=Path.unlink
    def die_after_selector(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path.name=="current-version":
            os._exit(47)
        return result
    Path.unlink=die_after_selector
print(json.dumps(deactivate_client_runtime(Path(request["root"]),request["identity"])),flush=True)
"""
    floor = database_bytes(root)
    for interrupted in (True, False):
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program], cwd=tmp_path,
            input=json.dumps({"root": str(root), "identity": identity(), "interrupt": interrupted}) + "\n",
            capture_output=True, text=True, timeout=20, **FLAGS,
        )
        if interrupted:
            assert result.returncode == 47, result.stderr
            assert (namespace / activation.REMOVING).is_file()
            assert not (namespace / "current-version").exists()
        else:
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["selectors_absent"]
    assert slot.is_dir() and database_bytes(root) == floor


@pytest.mark.timeout(45)
def test_independently_installed_owner_without_optional_dependencies_can_deactivate(selected, wheel, tmp_path):
    import shutil

    root, _fixture, _staged, _trusted, _directory, _namespace, slot = selected
    package = tmp_path / "dependency-free-owner"
    uv = shutil.which("uv")
    assert uv, "no-dependency owner installation requires the runner's uv"
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
         "client-state", "deactivate-runtime", "--root", str(root), "--identity", str(inputs)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, **FLAGS,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["selectors_absent"] and slot.is_dir()
    assert database_bytes(root) == floor
