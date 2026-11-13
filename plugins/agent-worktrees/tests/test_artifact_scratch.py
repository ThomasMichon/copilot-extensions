"""Native allocation faults retain exact responsibility rather than inventing absence."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest

from agent_worktrees import artifact_scratch as scratch, claim_providers, finalize, tracking
from test_resource_producer import owner as owner


@pytest.fixture
def allocation(owner, tmp_path):
    path, config = owner
    # The producer fixture uses tmp_path itself as a generic repository anchor;
    # physical scratch must instead be outside the actual selected anchor.
    config.repos["p"] = replace(config.repos["p"], anchor=str(tmp_path / "source"))
    return path, tmp_path / "scratch", secrets.token_urlsafe(24)


def create(allocation):
    path, root, operation_id = allocation
    return scratch.allocate(
        path, "m/p/owner", root_path=root, forbidden=(), operation_id=operation_id,
    )


def test_real_allocation_binding_settlement_and_consumed_removal(allocation):
    path, _, _ = allocation
    result = create(allocation)
    location = Path(result["path"])
    (location / "result.txt").write_text("consumed fixture")
    assert len(location.name) == 14
    current = tracking.load_record(path).resources[0]
    assert current.is_unsettled
    proof = claim_providers.resolve_claim_status(current.ref)
    assert proof["available"] and proof["exists"]
    assert proof["producer_verification"]["work_settled"] is False
    with pytest.raises(ValueError):
        scratch._invoke(path, "m/p/owner", "retain", {"token": result["token"]})
    retained = scratch.settle_after_cleanup(path, "m/p/owner", result["token"], forbidden=())
    assert retained["phase"] == "retained" and location.exists()
    assert finalize._assert_obligations_settled(tracking.load_record(path), "owner", abandon=False)
    removed = scratch.dispose_consumed(path, "m/p/owner", result["token"], forbidden=())
    assert removed["phase"] == "closed" and not location.exists()


def test_finalize_with_bytes_then_independent_previewed_cleanup(allocation):
    path, _, _ = allocation
    result = create(allocation)
    scratch.settle_after_cleanup(path, "m/p/owner", result["token"], forbidden=())
    with pytest.raises(ValueError, match="finalized"):
        scratch.preview_cleanup(path, "m/p/owner", result["token"], forbidden=())
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    ticket = scratch.preview_cleanup(path, "m/p/owner", result["token"], forbidden=())
    assert Path(result["path"]).exists()
    with pytest.raises(ValueError, match="preview changed"):
        scratch.cleanup(path, "m/p/owner", result["token"], preview="wrong", forbidden=())
    outcome = scratch.cleanup(
        path, "m/p/owner", result["token"], preview=ticket["preview"], forbidden=(),
    )
    assert outcome["outcome"] == "removed"
    assert tracking.load_record(path).status == "finalized"


def test_repeated_operation_id_does_not_allocate_another_directory(allocation):
    path, root, _ = allocation
    result = create(allocation)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="already recorded"):
        create(allocation)
    assert path.read_bytes() == before
    assert len([p for p in root.iterdir() if p.is_dir()]) == 1
    with pytest.raises(ValueError, match="may have started"):
        scratch.recover(path, "m/p/owner", result["token"], forbidden=())


@pytest.mark.parametrize("stage", ["before-create", "before-marker", "before-bind", "after-bind", "lost-reply"])
def test_allocation_interruptions_leave_exact_discoverable_custody(allocation, monkeypatch, stage):
    path, root, _ = allocation
    original_invoke, original_marker = scratch._invoke, scratch._write_marker
    def invoke(path, owner, operation, payload):
        if stage == "before-bind" and operation == "bind":
            raise RuntimeError("interrupted before bind")
        result = original_invoke(path, owner, operation, payload)
        if stage == "after-bind" and operation == "bind":
            raise RuntimeError("reply lost after bind")
        return result
    def write(fd, child, marker, **kwargs):
        if stage == "before-marker":
            raise RuntimeError("interrupted before marker")
        original_marker(fd, child, marker, **kwargs)
        if stage == "lost-reply" and marker["state"] == "active":
            raise RuntimeError("lost final reply")
    monkeypatch.setattr(scratch, "_invoke", invoke)
    monkeypatch.setattr(scratch, "_write_marker", write)
    if stage == "before-create":
        from agent_worktrees.artifact_root import AdmittedRoot
        monkeypatch.setattr(AdmittedRoot, "create_child", lambda *a: (_ for _ in ()).throw(RuntimeError("before create")))
    with pytest.raises(RuntimeError):
        create(allocation)
    claim = tracking.load_record(path).resources[0]
    assert claim.is_unsettled
    assert not finalize._assert_obligations_settled(tracking.load_record(path), "owner", abandon=False)
    monkeypatch.setattr(scratch, "_invoke", original_invoke)
    monkeypatch.setattr(scratch, "_write_marker", original_marker)
    proof = claim_providers.resolve_claim_status(claim.ref)
    if stage in {"before-bind", "after-bind"}:
        assert proof["available"] and proof["producer_verification"]["allocation_finished"]
        recovered = scratch.recover(path, "m/p/owner", claim.producer["token"], forbidden=())
        assert recovered["phase"] == "bound" and Path(recovered["path"]).exists()
    elif stage == "before-create":
        assert proof["available"] and not proof["exists"]
        completed = scratch._invoke(path, "m/p/owner", "complete", {"token": claim.producer["token"]})
        assert completed["phase"] == "pending"
    elif stage == "before-marker":
        assert not proof["available"]
    else:
        assert proof["available"] and not proof["producer_verification"]["work_settled"]
    assert root.exists()


def test_partial_removal_preserves_retained_custody_and_bytes(allocation, monkeypatch):
    path, _, _ = allocation
    result = create(allocation)
    scratch.settle_after_cleanup(path, "m/p/owner", result["token"], forbidden=())
    monkeypatch.setattr(scratch.scratch, "remove_scratch", lambda path, **kwargs: False)
    partial = scratch.dispose_consumed(path, "m/p/owner", result["token"], forbidden=())
    assert partial["outcome"] == "partial" and Path(result["path"]).exists()
    assert tracking.load_record(path).resources[0].producer["phase"] == "retained"


def test_missing_active_allocation_is_not_a_process_absence_proof(allocation):
    path, _, _ = allocation
    result = create(allocation)
    location = Path(result["path"])
    assert scratch.scratch.remove_scratch(location)
    current = tracking.load_record(path).resources[0]
    proof = claim_providers.resolve_claim_status(current.ref)
    assert not proof["available"]
    with pytest.raises(ValueError):
        scratch._invoke(path, "m/p/owner", "close", {"token": result["token"]})


def test_replaced_allocation_is_not_adopted_or_deleted(allocation):
    path, _, _ = allocation
    result = create(allocation)
    scratch.settle_after_cleanup(path, "m/p/owner", result["token"], forbidden=())
    location = Path(result["path"])
    location.rename(location.with_name("preserved-original"))
    from agent_procutil._daemon_instance_owner import mkdir_private
    mkdir_private(location)
    (location / "foreign.txt").write_text("not owned")
    with pytest.raises(ValueError):
        scratch.dispose_consumed(path, "m/p/owner", result["token"], forbidden=())
    assert (location / "foreign.txt").read_text() == "not owned"


def test_failure_evidence_is_allowlisted_bounded_and_labels_truncation(allocation):
    path, root, operation_id = allocation
    result = scratch.allocate(
        path, "m/p/owner", root_path=root, forbidden=(), operation_id=operation_id,
        failure_evidence={"max_files": 1, "max_bytes": 4},
    )
    location = Path(result["path"])
    (location / "arbitrary-sensitive-output.txt").write_text("not an admitted diagnostic")
    scratch.settle_after_cleanup(path, "m/p/owner", result["token"], forbidden=(), diagnostic=b"abcdefgh")
    assert (location / "runner-diagnostic.txt").read_bytes() == b"abcd"
    assert not (location / "arbitrary-sensitive-output.txt").exists()
    current = tracking.load_record(path).resources[0]
    proof = claim_providers.resolve_claim_status(current.ref)
    assert proof["available"] and proof["producer_verification"]["work_settled"]
    import json
    marker = json.loads((location / "scratch-owner.json").read_text())
    assert marker["diagnostics"] == {"files": 1, "bytes": 4, "truncated": True}
    (location / "unexpected.txt").write_text("added after settlement")
    assert not claim_providers.resolve_claim_status(current.ref)["available"]
    with pytest.raises(ValueError):
        scratch.dispose_consumed(path, "m/p/owner", result["token"], forbidden=())
    assert (location / "unexpected.txt").exists()


def test_failed_payload_pruning_cannot_become_a_settled_failure_bundle(allocation, monkeypatch):
    path, _, _ = allocation
    result = create(allocation)
    monkeypatch.setattr(scratch.scratch, "_clear", lambda *a, **k: False)
    with pytest.raises(ValueError, match="active responsibility"):
        scratch.settle_after_cleanup(path, "m/p/owner", result["token"], forbidden=(), diagnostic=b"failure")
    claim = tracking.load_record(path).resources[0]
    assert claim.is_unsettled
    proof = claim_providers.resolve_claim_status(claim.ref)
    assert proof["available"] and not proof["producer_verification"]["work_settled"]


def test_wrong_owner_and_changed_incarnation_cannot_settle(allocation):
    import yaml
    path, _, _ = allocation
    result = create(allocation)
    with pytest.raises(ValueError):
        scratch.settle_after_cleanup(path, "m/p/consumer", result["token"], forbidden=())
    raw = yaml.safe_load(path.read_text())
    raw["creation_nonce"] = "replacement-incarnation"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="incarnation"):
        scratch.settle_after_cleanup(path, "m/p/owner", result["token"], forbidden=())
    assert Path(result["path"]).exists()


@pytest.mark.parametrize("reply_lost", [False, True])
def test_removed_bytes_and_unknown_close_reconcile_the_same_token(allocation, monkeypatch, reply_lost):
    path, _, _ = allocation
    result = create(allocation)
    scratch.settle_after_cleanup(path, "m/p/owner", result["token"], forbidden=())
    original = scratch._invoke
    def interrupted(path, owner, operation, payload):
        if operation != "close":
            return original(path, owner, operation, payload)
        if reply_lost:
            original(path, owner, operation, payload)
        raise RuntimeError("close outcome unknown")
    monkeypatch.setattr(scratch, "_invoke", interrupted)
    with pytest.raises(RuntimeError, match="unknown"):
        scratch.dispose_consumed(path, "m/p/owner", result["token"], forbidden=())
    assert not Path(result["path"]).exists()
    monkeypatch.setattr(scratch, "_invoke", original)
    assert scratch.dispose_consumed(path, "m/p/owner", result["token"], forbidden=())["phase"] == "closed"
    assert len(tracking.load_record(path).resources) == 1
