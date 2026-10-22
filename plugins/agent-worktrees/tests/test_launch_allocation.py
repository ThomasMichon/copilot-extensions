"""The actual launch writer retains one complete allocation across retries."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sqlite3

import pytest
import yaml

import agent_worktrees.__main__ as m
from agent_worktrees import creation_allocation, launch_request, tracking
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity
from test_launch_request import _intent
from test_launch_request import admission_config as admission_config, authority as authority
from test_tuple_creation import _setup


@pytest.mark.parametrize("physical,platform,execution,prefix", [
    ("desk", "windows", "native-space", "desk-win"),
    ("desk", "wsl", "guest-space", "desk-wsl"),
    ("desk", "linux", "linux-space", "desk-linux"),
    ("desk-wsl", "wsl", "guest-space", "desk-wsl-wsl"),
])
@pytest.mark.parametrize("first_mode", ["legacy", "tuple-v1"])
def test_complete_daemon_admission_replays_original_representation(
    tmp_path, monkeypatch, authority, physical, platform, execution, prefix, first_mode,
):
    config = _setup(tmp_path, monkeypatch, physical=physical, platform=platform, execution=execution)
    intent = _intent("new")
    dispatch = launch_request._dispatch

    def lose_response(verb, args):
        result = dispatch(verb, args)
        if verb == "launch_request_admit":
            raise OSError("lost admission response")
        return result

    monkeypatch.setattr(launch_request, "_dispatch", lose_response)
    with pytest.raises(OSError, match="lost admission"):
        launch_request.execute(config, intent, identity_mode=first_mode)
    receipt = launch_request._read_admission(tmp_path / "tracking", intent["request_id"])
    allocation = creation_allocation.AdmittedAllocation.from_fields(receipt["allocation"])
    with pytest.raises(FrozenInstanceError):
        allocation.worktree_id = "replacement"
    expected = WorktreeMachineIdentity(physical, platform, execution) if first_mode == "tuple-v1" else None
    assert allocation.machine_identity == expected
    assert allocation.worktree_id.startswith(f"{prefix if expected else execution + '-' + ('win' if platform == 'windows' else platform)}-")
    assert not receipt.get("creation_started")
    assert not list((tmp_path / "tracking").glob("*.yaml"))
    monkeypatch.setattr(launch_request, "_dispatch", dispatch)
    result = launch_request.execute(
        config, intent, identity_mode="legacy" if first_mode == "tuple-v1" else "tuple-v1",
    )
    assert result["worktree_id"] == allocation.worktree_id
    record = tracking.load_record(tmp_path / "tracking" / f"{allocation.worktree_id}.yaml")
    assert record.machine_identity == expected
    allocation.require_record(record)
    complete = launch_request._read_admission(tmp_path / "tracking", intent["request_id"])
    assert complete["allocation"] == receipt["allocation"]
    assert complete["creation_nonce"] == record.creation_nonce
    before = record.yaml_path.read_bytes()
    receipt_before = launch_request._admission_path(tmp_path / "tracking", intent["request_id"]).read_bytes()
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: pytest.fail("retry must not allocate"))
    assert launch_request.execute(config, intent, identity_mode=first_mode) == result
    assert record.yaml_path.read_bytes() == before
    assert launch_request._admission_path(tmp_path / "tracking", intent["request_id"]).read_bytes() == receipt_before


@pytest.mark.parametrize("field", ["worktree_id", "branch", "path", "project", "machine", "platform",
                                  "environment", "execution_machine", "timestamp", "suffix"])
def test_corrupt_complete_receipt_refuses_without_allocation(
    tmp_path, monkeypatch, authority, field,
):
    config = _setup(tmp_path, monkeypatch)
    intent = _intent("new")
    launch_request.execute(config, intent, identity_mode="tuple-v1")
    directory = tmp_path / "tracking"
    path = launch_request._admission_path(directory, intent["request_id"])
    receipt = json.loads(path.read_text())
    receipt["allocation"][field] = "foreign"
    path.write_text(json.dumps(receipt))
    before = path.read_bytes()
    record = directory / f"{receipt['worktree_id']}.yaml"
    record_before = record.read_bytes()
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: pytest.fail("must not allocate"))
    with pytest.raises((ValueError, RuntimeError)):
        launch_request.execute(config, intent, identity_mode="tuple-v1")
    assert record.read_bytes() == record_before and path.read_bytes() == before


@pytest.mark.parametrize("changed", ["root", "anchor", "project", "machine", "platform", "mapping"])
def test_unstarted_complete_admission_conflict_never_reformats_allocation(
    tmp_path, monkeypatch, authority, changed,
):
    config = _setup(tmp_path, monkeypatch)
    intent = _intent("new")
    dispatch = launch_request._dispatch

    def lose_response(verb, args):
        result = dispatch(verb, args)
        if verb == "launch_request_admit":
            raise OSError("lost admission response")
        return result

    monkeypatch.setattr(launch_request, "_dispatch", lose_response)
    with pytest.raises(OSError):
        launch_request.execute(config, intent, identity_mode="tuple-v1")
    path = launch_request._admission_path(tmp_path / "tracking", intent["request_id"])
    before = path.read_bytes()
    if changed in ("root", "anchor"):
        repo = replace(config.default_repo, **{
            "worktree_root" if changed == "root" else "anchor": str(tmp_path / "elsewhere"),
        })
        config = replace(config, repos={config.repo_name: repo})
    elif changed == "project":
        config = replace(config, repo_name="other", repos={"other": config.default_repo})
    elif changed == "machine":
        config = replace(config, machine="renamed-key")
    elif changed == "platform":
        monkeypatch.setattr(launch_request.cfg, "detect_platform", lambda: "windows")
    else:
        (Path(config.default_repo.anchor) / "machines.yaml").write_text(
            "machines:\n  guest-space: {physical_host: other, execution_platform: wsl}\n",
        )
    monkeypatch.setattr(launch_request, "_dispatch", dispatch)
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: pytest.fail("must not allocate"))
    with pytest.raises((ValueError, RuntimeError), match="allocation|identity|execution|platform"):
        launch_request.execute(config, intent, identity_mode="legacy")
    assert path.read_bytes() == before
    assert not json.loads(before).get("creation_started")
    assert not list((tmp_path / "tracking").glob("*.yaml"))


@pytest.mark.parametrize("field,value", [
    ("creation_nonce", "replacement"), ("branch", "worktree/replacement"),
    ("machine", "elsewhere"), ("pair_ref", "guest-space/knowledge/replacement"),
])
def test_completed_admission_validates_retained_record_before_seed_write(
    tmp_path, monkeypatch, authority, field, value,
):
    config = _setup(tmp_path, monkeypatch)
    intent = _intent("new")
    result = launch_request.execute(config, intent, identity_mode="tuple-v1")
    directory = tmp_path / "tracking"
    path = directory / f"{result['worktree_id']}.yaml"
    data = yaml.safe_load(path.read_text())
    data[field] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    before = path.read_bytes()
    receipt_path = launch_request._admission_path(directory, intent["request_id"])
    receipt_before = receipt_path.read_bytes()
    with pytest.raises((ValueError, RuntimeError), match="allocation|identity"):
        launch_request.execute(config, intent)
    with pytest.raises((ValueError, RuntimeError), match="allocation|identity"):
        launch_request.inspect(intent["request_id"])
    assert path.read_bytes() == before and receipt_path.read_bytes() == receipt_before


def test_admission_writer_ignores_caller_allocation_spelling(tmp_path, monkeypatch, authority):
    config = _setup(tmp_path, monkeypatch)
    result = launch_request._dispatch("launch_request_admit", {
        "tracking_dir": str(tmp_path / "tracking"), "request_id": "a" * 32,
        "fingerprint": "f" * 64, "kind": "new", "worktree_id": "attacker-selected",
        "timestamp": "20261021-010203", "suffix": "abcd",
        "allocation_request": {"project": config.repo_name, "identity_mode": "tuple-v1"},
    })
    assert result["worktree_id"] == "desk-wsl-20261021-010203-abcd"
    assert result["allocation"]["path"] == str(Path(config.default_repo.worktree_root) / result["worktree_id"])


@pytest.mark.parametrize("defect", ["checkout", "nonce", "evidence", "retired"])
def test_completed_allocation_cannot_recreate_removed_or_unproven_result(
    tmp_path, monkeypatch, authority, defect,
):
    config = _setup(tmp_path, monkeypatch)
    intent = _intent("new")
    result = launch_request.execute(config, intent, identity_mode="tuple-v1")
    directory = tmp_path / "tracking"
    receipt_path = launch_request._admission_path(directory, intent["request_id"])
    record_path = directory / f"{result['worktree_id']}.yaml"
    receipt = json.loads(receipt_path.read_text())
    if defect == "checkout":
        Path(receipt["allocation"]["path"]).rmdir()
    elif defect == "retired":
        data = yaml.safe_load(record_path.read_text())
        data["status"] = "finalized"
        record_path.write_text(yaml.safe_dump(data, sort_keys=False))
    else:
        receipt.pop("creation_nonce" if defect == "nonce" else "record_identity")
        receipt_path.write_text(json.dumps(receipt))
    before = record_path.read_bytes(), receipt_path.read_bytes()
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: pytest.fail("must not recreate"))
    with pytest.raises(ValueError, match="allocation|nonce"):
        launch_request.execute(config, intent)
    with pytest.raises(ValueError, match="allocation|nonce"):
        launch_request.inspect(intent["request_id"])
    assert (record_path.read_bytes(), receipt_path.read_bytes()) == before


def test_core_revalidates_complete_namespace_after_source_preparation(tmp_path, monkeypatch, authority):
    config = _setup(tmp_path, monkeypatch)
    admitted = creation_allocation.admit(config, "20261021-010203", "abcd", "tuple-v1")
    changed = replace(config, repos={
        config.repo_name: replace(config.default_repo, worktree_root=str(tmp_path / "elsewhere")),
    })

    def prepare(*_args, **_kwargs):
        monkeypatch.setattr(launch_request.cfg, "load_config", lambda **_kwargs: changed)
        return type("Prepared", (), {"start_point": "HEAD"})()

    monkeypatch.setattr(m, "_prepare_worktree_source", prepare)
    effects = []
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: effects.append("git"))
    with pytest.raises(ValueError, match="namespace"):
        m._create_worktree_core(
            config, admitted_allocation=admitted, creation_intent=lambda _row: effects.append("reserve"),
        )
    assert effects == []
    assert not list((tmp_path / "tracking").glob("*.yaml"))


@pytest.mark.parametrize("first_mode", ["legacy", "tuple-v1"])
@pytest.mark.parametrize("field,value", [
    ("owner_ref", "guest-space/demo-repo/other"),
    ("pair_id", "20261021-010203-abcd"), ("pair_role", "knowledge"),
    ("pair_ref", "guest-space/knowledge/other"), ("pair_kind", "anchor"),
])
def test_new_creation_key_receipt_retains_original_owner_and_pair(
    tmp_path, monkeypatch, first_mode, field, value,
):
    from agent_worktrees.creation_requests import CreationRequestFailure

    config = _setup(tmp_path, monkeypatch)
    result = m._create_worktree_core(config, creation_key="intent", identity_mode=first_mode)
    path = tmp_path / "tracking" / f"{result['worktree']['id']}.yaml"
    data = yaml.safe_load(path.read_text())
    data[field] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    before = path.read_bytes()
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: pytest.fail("must not allocate"))
    with pytest.raises(CreationRequestFailure, match="owner or pair identity") as caught:
        m._create_worktree_core(config, creation_key="intent", identity_mode="tuple-v1")
    assert caught.value.status == "conflict"
    assert path.read_bytes() == before


def test_old_creation_key_receipt_replays_without_identity_backfill(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch)
    first = m._create_worktree_core(config, creation_key="intent")
    journal = tmp_path / "workspace-creation.sqlite3"
    with sqlite3.connect(journal) as connection:
        receipt = json.loads(connection.execute("SELECT worktree FROM creations").fetchone()[0])
        receipt.pop("record_identity")
        encoded = json.dumps(receipt, sort_keys=True)
        connection.execute("UPDATE creations SET worktree=?", (encoded,))
    path = tmp_path / "tracking" / f"{first['worktree']['id']}.yaml"
    before = path.read_bytes()
    second = m._create_worktree_core(config, creation_key="intent", identity_mode="tuple-v1")
    assert second["worktree"] == first["worktree"] and second["creation"]["replayed"]
    with sqlite3.connect(journal) as connection:
        assert connection.execute("SELECT worktree FROM creations").fetchone()[0] == encoded
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("machine", "elsewhere"), ("platform", "linux")])
def test_old_key_receipt_still_checks_its_available_original_owner_projection(
    tmp_path, monkeypatch, field, value,
):
    from agent_worktrees.creation_requests import CreationRequestFailure

    config = _setup(tmp_path, monkeypatch)
    first = m._create_worktree_core(config, creation_key="intent")
    journal = tmp_path / "workspace-creation.sqlite3"
    with sqlite3.connect(journal) as connection:
        receipt = json.loads(connection.execute("SELECT worktree FROM creations").fetchone()[0])
        receipt.pop("record_identity")
        encoded = json.dumps(receipt, sort_keys=True)
        connection.execute("UPDATE creations SET worktree=?", (encoded,))
    path = tmp_path / "tracking" / f"{first['worktree']['id']}.yaml"
    data = yaml.safe_load(path.read_text())
    data[field] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    before = path.read_bytes()
    with pytest.raises(CreationRequestFailure, match="original owner"):
        m._create_worktree_core(config, creation_key="intent", identity_mode="tuple-v1")
    assert path.read_bytes() == before
    with sqlite3.connect(journal) as connection:
        assert connection.execute("SELECT worktree FROM creations").fetchone()[0] == encoded


def test_finish_retry_cannot_reseal_a_replacement_record(tmp_path, monkeypatch, authority):
    config = _setup(tmp_path, monkeypatch)
    intent = _intent("new")
    result = launch_request.execute(config, intent, identity_mode="tuple-v1")
    directory = tmp_path / "tracking"
    receipt_path = launch_request._admission_path(directory, intent["request_id"])
    before = receipt_path.read_bytes()
    receipt = json.loads(before)
    record_path = directory / f"{result['worktree_id']}.yaml"
    data = yaml.safe_load(record_path.read_text())
    data["creation_nonce"] = "replacement"
    record_path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError, match="original record identity"):
        launch_request._dispatch("launch_request_creation_finish", {
            "tracking_dir": str(directory), "request_id": intent["request_id"],
            "fingerprint": receipt["fingerprint"],
        })
    assert receipt_path.read_bytes() == before


def test_direct_stage_cannot_accept_an_incomplete_allocation(tmp_path, monkeypatch, authority):
    config = _setup(tmp_path, monkeypatch)
    directory = tmp_path / "tracking"
    receipt = launch_request._dispatch("launch_request_admit", {
        "tracking_dir": str(directory), "request_id": "a" * 32,
        "fingerprint": "f" * 64, "kind": "new", "worktree_id": "ignored",
        "timestamp": "20261021-010203", "suffix": "abcd",
        "allocation_request": {"project": config.repo_name, "identity_mode": "tuple-v1"},
    })
    allocation = creation_allocation.AdmittedAllocation.from_fields(receipt["allocation"])
    tracking.create_new_record(
        allocation.worktree_id, allocation.branch, allocation.path, allocation.project,
        allocation.machine, allocation.platform, directory, machine_identity=allocation.machine_identity,
    )
    with pytest.raises(ValueError, match="creation is incomplete"):
        launch_request._dispatch("launch_request_stage", {
            "tracking_dir": str(directory), "request_id": "a" * 32,
            "fingerprint": "f" * 64, "text": "must not stage",
        })
    assert "accepted" not in launch_request._read_admission(directory, "a" * 32)


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("missing", ["machine_identity_version", "environment", "execution_machine"])
def test_incomplete_tuple_receipt_cannot_downgrade_a_lost_admission(
    tmp_path, monkeypatch, authority, platform, missing,
):
    config = _setup(tmp_path, monkeypatch, physical="desk", execution="desk", platform=platform)
    intent = _intent("new")
    dispatch = launch_request._dispatch

    def lose_response(verb, args):
        result = dispatch(verb, args)
        if verb == "launch_request_admit":
            raise OSError("lost admission response")
        return result

    monkeypatch.setattr(launch_request, "_dispatch", lose_response)
    with pytest.raises(OSError, match="lost admission"):
        launch_request.execute(config, intent, identity_mode="tuple-v1")
    directory = tmp_path / "tracking"
    path = launch_request._admission_path(directory, intent["request_id"])
    receipt = json.loads(path.read_text())
    receipt["allocation"].pop(missing)
    path.write_text(json.dumps(receipt))
    before = path.read_bytes()
    monkeypatch.setattr(launch_request, "_dispatch", dispatch)
    with pytest.raises(ValueError):
        launch_request.execute(config, intent, identity_mode="legacy")
    with pytest.raises(ValueError):
        launch_request.inspect(intent["request_id"])
    assert path.read_bytes() == before
    assert not list(directory.glob("*.yaml"))
    assert not Path(config.default_repo.worktree_root).exists()


@pytest.mark.parametrize("boundary", ["allocation", "record-evidence", "old-result"])
@pytest.mark.parametrize("missing", ["machine_identity_version", "environment", "execution_machine"])
def test_all_receipt_owner_boundaries_refuse_partial_tuple_metadata(
    tmp_path, monkeypatch, boundary, missing,
):
    config = _setup(tmp_path, monkeypatch, physical="desk", execution="desk", platform="linux")
    result = m._create_worktree_core(config, creation_key="snapshot", identity_mode="tuple-v1")
    record = tracking.load_record(tmp_path / "tracking" / f"{result['worktree']['id']}.yaml")
    if boundary == "allocation":
        fields = creation_allocation.admit(config, "20261021-010203", "abcd", "tuple-v1").to_fields()
        validate = creation_allocation.AdmittedAllocation.from_fields
    elif boundary == "record-evidence":
        fields = creation_allocation.record_evidence(record)
        validate = creation_allocation.validate_record_evidence
    else:
        fields = result["worktree"].copy()
        validate = lambda value: creation_allocation.require_result_identity(record, value)
    fields.pop(missing)
    with pytest.raises(ValueError, match="Incomplete tuple identity"):
        validate(fields)
