"""Disposable sealed runtimes exercise the actual owner callback and file removal."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from worktree_manager import historical_runtimes as subject

SOURCE = Path(__file__).resolve().parents[1]


@pytest.fixture
def owner(monkeypatch):
    if os.name != "nt":
        pytest.skip("Selected runtime disposal requires the qualified Windows boundary")
    value = subject._owner()
    monkeypatch.setattr(value.runtime, "_reliable_process_enumeration", lambda: True)
    monkeypatch.setattr(value.runtime, "_versions_with_live_process", lambda root: set())
    monkeypatch.setattr(value.runtime, "_running_pids", lambda root: set())
    return value


def owned_slot(root: Path, owner, version: str = "1.2.3") -> Path:
    runtime = owner.runtime
    with runtime.construction_lease(root, version):
        slot = root / "versions" / version
        payload = slot / "payload"
        (payload / "references").mkdir(parents=True)
        (payload / "pyproject.toml").write_text(
            '[project]\nname="copilot-extensions-worktree-manager"\n'
            f'version="{version}"\n',
            encoding="utf-8",
        )
        shutil.copyfile(
            SOURCE / "references" / "uninstall-contract.json",
            payload / "references" / "uninstall-contract.json",
        )
        executable = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        executable.parent.mkdir()
        shutil.copyfile(sys.executable, executable)
        owner.seal_inventory(slot, frontdoors={}, provider={})
        runtime.mark_complete(root, version, payload_hash="a" * 64)
    return slot


def finding(root: Path, name: str = "1.2.3") -> dict:
    return next(value for value in subject.inspect(root) if value["name"] == name)


def test_selected_owned_runtime_is_deleted_by_production_callback(tmp_path, owner):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    unrelated = root / "operator-data"
    unrelated.write_bytes(b"untouched")
    selected = finding(root)
    assert selected["status"] == "candidate"
    assert selected["detector"] == "manager-sealed-payload-v1"
    result = subject.apply(root, None, selected)
    assert result["status"] == "confirmed", result
    assert not slot.exists()
    assert unrelated.read_bytes() == b"untouched"
    assert not subject._journal_path(root, slot.name).exists()
    assert subject.inspect(root) == []


@pytest.mark.parametrize("barrier", ["current-version", "last-known-good", "reference", "live"])
def test_protected_generations_survive_inspection_and_changed_before_apply(
    tmp_path, monkeypatch, owner, barrier,
):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    selected = finding(root)
    assert selected["status"] == "candidate"
    if barrier == "reference":
        owner.runtime._leases.replace_runtime_references(
            root, "fixture/historical-runtime", [slot.name], expected=[],
        )
    elif barrier == "live":
        monkeypatch.setattr(owner.runtime, "_versions_with_live_process", lambda root: {slot.name})
    else:
        (root / barrier).write_text(slot.name + "\n", encoding="utf-8")
    assert finding(root)["status"] == "preserved"
    assert subject.apply(root, None, selected)["status"] == "preserved"
    assert slot.exists()
    assert not subject._journal_path(root, slot.name).exists()


def test_dev_unknown_and_foreign_are_never_disposal_authority(tmp_path, owner):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    foreign = root / "versions" / "9.9.9"
    foreign.mkdir()
    (foreign / "operator-data").write_bytes(b"foreign")
    dev = root / "versions" / "dev"
    dev.mkdir()
    (slot / "payload" / "pyproject.toml").write_text(
        '[project]\nname="another-package"\nversion="1.2.3"\n', encoding="utf-8",
    )
    values = subject.inspect(root)
    assert {value["name"] for value in values} == {slot.name, foreign.name, dev.name}
    assert all(value["status"] == "preserved" for value in values)
    assert all(subject.apply(root, None, value)["status"] == "preserved" for value in values)
    assert (foreign / "operator-data").read_bytes() == b"foreign"


def test_archived_recognition_is_not_owner_disposal_permission(tmp_path, owner):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    executable = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.write_bytes(b"edited interpreter, same historical metadata")
    assert subject._recognize(root, slot.name, owner) == "manager-sealed-payload-v1"
    selected = finding(root)
    assert selected["detector"] == "manager-sealed-payload-v1"
    assert selected["status"] == "preserved"
    assert subject.apply(root, None, selected)["status"] == "preserved"
    assert executable.read_bytes() == b"edited interpreter, same historical metadata"


@pytest.mark.parametrize("mutation", ["edit", "extra", "rename-replace", "completion", "inventory"])
def test_edited_or_unowned_evidence_preserves_runtime(tmp_path, owner, mutation):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    selected = finding(root)
    if mutation == "edit":
        (slot / "payload" / "pyproject.toml").write_text("edited", encoding="utf-8")
    elif mutation == "extra":
        (slot / "new-unowned-file").write_bytes(b"unknown")
    elif mutation == "rename-replace":
        original = slot / "payload" / "pyproject.toml"
        content = original.read_bytes()
        original.rename(original.with_suffix(".old"))
        original.write_bytes(content)
    else:
        control = owner.INVENTORY if mutation == "inventory" else owner.runtime.COMPLETE_MARKER
        (slot / control).write_text("{}", encoding="utf-8")
    assert subject.apply(root, None, selected)["status"] == "preserved"
    assert slot.exists()
    assert not subject._journal_path(root, slot.name).exists()


def test_revalidation_is_inside_real_slot_cleanup_custody(tmp_path, monkeypatch, owner):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    selected = finding(root)
    original = owner.runtime.remove_owned_candidate

    def race(root, version, *, remove):
        (slot / "concurrent-unowned-file").write_bytes(b"changed after outer assessment")
        return original(root, version, remove=remove)

    monkeypatch.setattr(owner.runtime, "remove_owned_candidate", race)
    assert subject.apply(root, None, selected)["status"] == "preserved"
    assert (slot / "concurrent-unowned-file").exists()
    assert not subject._journal_path(root, slot.name).exists()


def test_retention_and_construction_owners_refuse_selected_removal(tmp_path, owner):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    selected = finding(root)
    for lease in (owner.runtime.retention_lease, owner.runtime.construction_lease):
        with lease(root, slot.name):
            assert subject.apply(root, None, selected)["status"] == "preserved"
        assert slot.exists()
    assert not subject._journal_path(root, slot.name).exists()


def test_partial_delete_retains_exact_owner_evidence_and_resumes(tmp_path, monkeypatch, owner):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    selected = finding(root)
    original = subject._remove_entry
    deleted = []

    def interrupted(path, expected, actual_owner, **kwargs):
        original(path, expected, actual_owner, **kwargs)
        deleted.append(path)
        raise OSError("injected partial deletion")

    monkeypatch.setattr(subject, "_remove_entry", interrupted)
    result = subject.apply(root, None, selected)
    assert result["status"] == "failed"
    assert len(deleted) == 1 and not deleted[0].exists()
    journal = subject._journal_path(root, slot.name)
    assert journal.is_file()
    assert (slot / owner.runtime.COMPLETE_MARKER).is_file()
    resumed = finding(root)
    assert resumed["sha256"] == selected["sha256"]
    assert resumed["reason"] == "owned-partial-removal-resumable"
    monkeypatch.setattr(subject, "_remove_entry", original)
    assert subject.apply(root, None, resumed)["status"] == "confirmed"
    assert not slot.exists() and not journal.exists()


def test_partial_retry_preserves_new_or_edited_objects(tmp_path, monkeypatch, owner):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    selected = finding(root)
    original = subject._remove_entry

    def interrupted(path, expected, actual_owner, **kwargs):
        original(path, expected, actual_owner, **kwargs)
        raise OSError("injected interruption")

    monkeypatch.setattr(subject, "_remove_entry", interrupted)
    assert subject.apply(root, None, selected)["status"] == "failed"
    foreign = slot / "foreign-after-partial"
    foreign.write_bytes(b"preserve")
    monkeypatch.setattr(subject, "_remove_entry", original)
    assert finding(root)["status"] == "preserved"
    assert subject.apply(root, None, selected)["status"] == "preserved"
    assert foreign.read_bytes() == b"preserve"
    assert subject._journal_path(root, slot.name).exists()


def test_partial_journal_is_not_permission_for_replaced_slot(tmp_path, monkeypatch, owner):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    selected = finding(root)
    original = subject._remove_entry

    def interrupted(path, expected, actual_owner, **kwargs):
        original(path, expected, actual_owner, **kwargs)
        raise OSError("injected interruption")

    monkeypatch.setattr(subject, "_remove_entry", interrupted)
    assert subject.apply(root, None, selected)["status"] == "failed"
    slot.rename(slot.with_name("preserved-original"))
    slot.mkdir()
    foreign = slot / "operator-data"
    foreign.write_bytes(b"foreign replacement")
    monkeypatch.setattr(subject, "_remove_entry", original)
    assert subject.apply(root, None, selected)["status"] == "preserved"
    assert foreign.read_bytes() == b"foreign replacement"


def test_forged_finding_and_corrupt_journal_preserve(tmp_path, owner):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    selected = finding(root)
    for edited in (dict(selected, sha256="0" * 64),
                   dict(selected, resource=str(root / "operator-data")),
                   dict(selected, detector="future-detector"),
                   dict(selected, revision=True)):
        assert subject.apply(root, None, edited)["status"] == "preserved"
    journal = subject._journal_path(root, slot.name)
    journal.parent.mkdir()
    journal.write_text(json.dumps({"schema": "unknown", "name": slot.name}), encoding="utf-8")
    assert finding(root)["status"] == "preserved"
    assert subject.apply(root, None, selected)["status"] == "preserved"
    assert slot.exists()


def test_absent_root_is_read_only_and_unknown_live_evidence_preserves(
    tmp_path, monkeypatch, owner,
):
    root = tmp_path / "absent"
    assert subject.inspect(root) == []
    assert not root.exists()
    slot = owned_slot(root, owner)
    selected = finding(root)
    monkeypatch.setattr(owner.runtime, "_reliable_process_enumeration", lambda: False)
    assert finding(root)["status"] == "preserved"
    assert subject.apply(root, None, selected)["status"] == "preserved"
    assert slot.exists()


@pytest.mark.parametrize("substitution", ["file", "ancestor"])
def test_actual_replacement_after_final_validation_cannot_delete_foreign_objects(
    tmp_path, monkeypatch, owner, substitution,
):
    root = tmp_path / "installation"
    slot = owned_slot(root, owner)
    selected = finding(root)
    target = slot / "payload" / "references" / "uninstall-contract.json"
    foreign = root / "foreign-object"
    if substitution == "file":
        foreign.write_bytes(b"foreign replacement must survive")
    else:
        foreign.mkdir()
        (foreign / "operator-data").write_bytes(b"foreign ancestor must survive")
    original = subject.PinnedPath.dispose
    attempts = []

    def replace_after_validation(pinned):
        if pinned.path == target:
            attempts.append(pinned.path)
            if substitution == "file":
                try:
                    foreign.replace(target)
                except OSError:
                    pass
            else:
                try:
                    target.parent.rename(root / "moved-ancestor")
                    foreign.rename(target.parent)
                except OSError:
                    pass
        return original(pinned)

    monkeypatch.setattr(subject.PinnedPath, "dispose", replace_after_validation)
    result = subject.apply(root, None, selected)
    assert len(attempts) == 1
    if substitution == "file":
        remaining = foreign if foreign.exists() else target
        assert remaining.read_bytes() == b"foreign replacement must survive"
    else:
        remaining = foreign if foreign.exists() else target.parent
        assert (remaining / "operator-data").read_bytes() == b"foreign ancestor must survive"
    assert result["status"] in {"confirmed", "failed"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX refusal contract")
def test_posix_has_no_path_based_disposal_fallback(tmp_path):
    root = tmp_path / "installation"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    foreign = slot / "operator-data"
    foreign.write_bytes(b"preserve")
    report = {
        "detector": "manager-sealed-payload-v1", "revision": 1, "name": slot.name,
        "resource": str(slot), "sha256": "a" * 64, "status": "candidate",
    }
    result = subject.apply(root, None, report)
    assert result["status"] == "preserved"
    assert result["reason"] == "native-runtime-disposal-unsupported"
    assert foreign.read_bytes() == b"preserve"
