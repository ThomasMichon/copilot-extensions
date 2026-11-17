"""Synthetic final-compartment production and exact handover faults."""

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import secrets
from typing import Any, ContextManager

import pytest
import yaml

from agent_worktrees import artifact_output as output, claim_providers, config as cfg, finalize, tracking, tracking_write
from agent_worktrees import resource_producer
from agent_worktrees.artifact_output_policy import resolve_user_output
from agent_worktrees.artifact_root import AdmittedRoot, admit_root
from agent_worktrees.resource_delivery import MAX_BYTES
from test_resource_producer import owner as owner

Destination = tuple[Path, dict[str, Any], str]
Owner = tuple[Path, cfg.Config]


@pytest.fixture
def destination(owner: Owner, tmp_path: Path) -> Destination:
    path, config = owner
    config.repos["p"] = replace(config.repos["p"], anchor=str(tmp_path / "source"))
    root = tmp_path / "user-storage"
    with admit_root(root, forbidden=(), create=True):
        pass
    selection = resolve_user_output({}, topic="Report", owner_roots={"downloads": str(root)})
    return path, selection, secrets.token_urlsafe(24)


def manifest(files: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]


def produce(destination: Destination, files: dict[str, bytes] | None = None) -> dict[str, Any]:
    path, selection, operation_id = destination
    return output.produce_files(
        path, "m/p/owner", selection=selection,
        files={"report.txt": b"verified fixture"} if files is None else files,
        operation_id=operation_id,
    )


def metadata(path: Path) -> dict[str, Any]:
    value = tracking.load_record(path).resources[0].producer
    assert isinstance(value, dict)
    return value


def test_real_segmented_producer_delivers_then_ignores_later_user_move(
    destination: Destination, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, selection, _ = destination
    result = produce(destination)
    assert result["phase"] == "delivered"
    location = Path(selection["destination"])
    assert (location / "report.txt").read_bytes() == b"verified fixture"
    receipt = result["producer"]["closeout"]["producer_verification"]["delivery"]
    assert receipt["destination"] == str(location) and receipt["content"]["files"] == 1
    assert receipt["result"] == "verified-local"
    marker = (location / output.MARKER).read_text()
    assert result["creator"] not in marker and result["token"] not in marker
    assert result["producer"]["recovery"]["incarnation"] not in marker
    assert finalize._assert_obligations_settled(tracking.load_record(path), "owner", abandon=False)
    moved = location.with_name("User organized this")
    location.rename(moved)
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    monkeypatch.setattr(output, "_destination", lambda *a, **k: pytest.fail("no post-delivery storage access"))
    assert output.deliver(path, "m/p/owner", result["token"]) == result
    assert produce(destination) == result
    assert not location.exists() and moved.is_dir()


def test_reservation_and_binding_precede_writable_return(
    destination: Destination, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, selection, operation_id = destination
    original = output._invoke
    phases = []
    def invoke(path: Path, owner: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = original(path, owner, operation, payload)
        phases.append(operation)
        if operation in {"reserve", "begin"}:
            assert not Path(selection["destination"]).exists()
            assert tracking.load_record(path).resources[0].is_unsettled
        return response
    monkeypatch.setattr(output, "_invoke", invoke)
    result = output.allocate(
        path, "m/p/owner", selection=selection, manifest=manifest({"report.txt": b"fixture"}),
        operation_id=operation_id,
    )
    assert phases == ["reserve", "begin", "bind"] and result["phase"] == "bound"
    assert Path(result["path"]).is_dir()
    assert not finalize._assert_obligations_settled(tracking.load_record(path), "owner", abandon=False)


def test_existing_destination_is_never_overwritten_or_adopted(destination: Destination) -> None:
    path, selection, _ = destination
    existing = Path(selection["destination"])
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_bytes(b"existing user content")
    with pytest.raises(ValueError, match="already exists"):
        produce(destination)
    assert (existing / "keep.txt").read_bytes() == b"existing user content"
    claim = tracking.load_record(path).resources[0]
    assert metadata(path)["phase"] == "allocating" and claim.is_unsettled
    assert not (existing / output.MARKER).exists()


@pytest.mark.parametrize("stage", ["reserve", "begin", "bind", "deliver"])
def test_transaction_faults_keep_responsibility_without_reallocation(
    destination: Destination, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    path, selection, _ = destination
    original = output._invoke
    def interrupted(path: Path, owner: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation == stage:
            raise RuntimeError("fixture interrupted transaction")
        return original(path, owner, operation, payload)
    monkeypatch.setattr(output, "_invoke", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        produce(destination)
    record = tracking.load_record(path)
    if stage == "reserve":
        assert not record.resources and not Path(selection["destination"]).exists()
        return
    assert len(record.resources) == 1 and record.resources[0].is_unsettled
    token = metadata(path)["token"]
    monkeypatch.setattr(output, "_invoke", original)
    if stage in {"bind", "deliver"}:
        result = produce(destination)
        assert result["token"] == token and result["phase"] == "delivered"
        assert len(tracking.load_record(path).resources) == 1


def test_lost_delivery_reply_returns_original_receipt_without_storage_repair(
    destination: Destination, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, selection, _ = destination
    original = output._invoke
    receipt: list[dict[str, Any]] = []
    def lose_reply(path: Path, owner: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = original(path, owner, operation, payload)
        if operation == "deliver":
            receipt.append(result)
            raise RuntimeError("fixture lost successful reply")
        return result
    monkeypatch.setattr(output, "_invoke", lose_reply)
    with pytest.raises(RuntimeError, match="lost successful"):
        produce(destination)
    Path(selection["destination"]).rename(Path(selection["root"]) / "moved-by-user")
    monkeypatch.setattr(output, "_invoke", original)
    assert produce(destination) == receipt[0]
    assert len(tracking.load_record(path).resources) == 1


@pytest.mark.parametrize("change", ["missing", "content", "oversize", "extra", "directory", "unreadable", "marker"])
def test_provider_reads_reject_incomplete_or_changed_content(
    destination: Destination, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    path, selection, _ = destination
    original = output.deliver
    monkeypatch.setattr(output, "deliver", lambda *a: {"held": True})
    assert produce(destination) == {"held": True}
    monkeypatch.setattr(output, "deliver", original)
    directory = Path(selection["destination"])
    member = directory / "report.txt"
    if change == "missing":
        member.unlink()
    elif change == "content":
        member.write_bytes(b"changed content!")
    elif change == "oversize":
        member.write_bytes(b"x" * 1024)
    elif change == "extra":
        (directory / "unexpected.txt").write_bytes(b"not declared")
    elif change == "directory":
        member.unlink()
        member.mkdir()
    elif change == "unreadable":
        read = output._read_file
        def unreadable(directory: AdmittedRoot, name: str) -> ContextManager[int]:
            if name == "report.txt":
                raise PermissionError("fixture unreadable")
            return read(directory, name)
        monkeypatch.setattr(output, "_read_file", unreadable)
    else:
        (directory / output.MARKER).write_text("{}")
    before = path.read_bytes()
    token = metadata(path)["token"]
    with pytest.raises(ValueError, match="deliver refused"):
        original(path, "m/p/owner", token)
    assert path.read_bytes() == before and tracking.load_record(path).resources[0].is_unsettled


def test_partial_writes_require_reconciliation_not_overwrite(
    destination: Destination, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, selection, _ = destination
    original = output._create_file
    def fail_member(directory: AdmittedRoot, name: str, data: bytes) -> None:
        if name == "report.txt":
            raise OSError("fixture disk-full write")
        return original(directory, name, data)
    monkeypatch.setattr(output, "_create_file", fail_member)
    with pytest.raises(OSError, match="disk-full"):
        produce(destination)
    monkeypatch.setattr(output, "_create_file", original)
    with pytest.raises(ValueError, match="may have started"):
        produce(destination)
    assert len(tracking.load_record(path).resources) == 1
    assert not (Path(selection["destination"]) / "report.txt").exists()


def test_exact_operation_refuses_changed_payload(destination: Destination) -> None:
    path, _, _ = destination
    result = produce(destination)
    with pytest.raises(ValueError, match="changed the original"):
        produce(destination, {"report.txt": b"different fixture"})
    assert metadata(path)["token"] == result["token"]


@pytest.mark.parametrize("files", [
    {}, {"../escape": b"bad"}, {"NUL.txt": b"bad"},
    {"report.txt": b"a", "REPORT.TXT": b"b"}, {output.MARKER: b"bad"},
    {f"file-{index}.txt": b"x" for index in range(17)},
])
def test_invalid_manifest_is_refused_before_allocation(destination: Destination, files: dict[str, bytes]) -> None:
    path, selection, _ = destination
    with pytest.raises(ValueError):
        produce(destination, files)
    assert not tracking.load_record(path).resources
    assert not Path(selection["destination"]).exists()


def test_source_and_history_homes_are_not_user_output(destination: Destination, tmp_path: Path) -> None:
    path, _, operation_id = destination
    protected = resolve_user_output({}, topic="Report", owner_roots={},
                                    destination=str(path.parent / "not-user-output"))
    with pytest.raises(ValueError, match="protected storage"):
        output.produce_files(path, "m/p/owner", selection=protected,
                             files={"report.txt": b"fixture"}, operation_id=operation_id)
    assert not tracking.load_record(path).resources


def test_declared_byte_budget_is_checked_before_allocation(destination: Destination) -> None:
    path, selection, operation_id = destination
    with pytest.raises(ValueError, match="manifest integrity"):
        output.allocate(
            path, "m/p/owner", selection=selection,
            manifest=[{"name": "report.txt", "bytes": MAX_BYTES + 1, "sha256": "a" * 64}],
            operation_id=operation_id,
        )
    assert not tracking.load_record(path).resources and not Path(selection["destination"]).exists()


def test_hardlinked_member_cannot_be_handed_over(
    destination: Destination, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    path, selection, _ = destination
    original = output.deliver
    monkeypatch.setattr(output, "deliver", lambda *a: {"held": True})
    produce(destination)
    monkeypatch.setattr(output, "deliver", original)
    member = Path(selection["destination"]) / "report.txt"
    os.link(member, tmp_path / "another-name")
    token = metadata(path)["token"]
    assert not claim_providers.resolve_claim_status(tracking.load_record(path).resources[0].ref)["available"]
    with pytest.raises(ValueError):
        original(path, "m/p/owner", token)


def test_failed_delivery_publication_keeps_the_same_sealed_allocation(
    destination: Destination, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _, _ = destination
    saved = tracking.save_record
    def fail_delivery(record: tracking.WorktreeRecord, target: Path | None = None, **options: Any) -> None:
        if any(isinstance(c.producer, dict) and c.producer.get("phase") == "delivered" for c in record.resources):
            raise OSError("fixture delivery publication failure")
        saved(record, target, **options)
    with monkeypatch.context() as patch:
        patch.setattr(tracking, "save_record", fail_delivery)
        with pytest.raises(ValueError, match="deliver refused"):
            produce(destination)
    token = metadata(path)["token"]
    assert metadata(path)["phase"] == "bound"
    result = produce(destination)
    assert result["phase"] == "delivered" and result["token"] == token
    assert len(tracking.load_record(path).resources) == 1


def test_unmarked_partial_creation_cannot_allocate_again(
    destination: Destination, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, selection, _ = destination
    def fail_marker(*args: Any, **options: Any) -> None:
        raise OSError("fixture marker publication failure")
    with monkeypatch.context() as patch:
        patch.setattr(output, "_write_marker", fail_marker)
        with pytest.raises(OSError, match="marker publication"):
            produce(destination)
    token = metadata(path)["token"]
    assert Path(selection["destination"]).is_dir() and metadata(path)["phase"] == "allocating"
    with pytest.raises((OSError, ValueError)):
        produce(destination)
    assert metadata(path)["token"] == token and len(tracking.load_record(path).resources) == 1


def test_missing_lifetime_support_never_falls_back_to_unclaimed_storage(
    destination: Destination, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, selection, _ = destination
    operations: list[str] = []
    def older_owner(verb: str, args: dict[str, Any], **options: Any) -> dict[str, Any]:
        operations.append(args["operation"])
        return {"error": "unsupported producer lifetime"}
    monkeypatch.setattr(tracking_write, "run_direct", older_owner)
    with pytest.raises(ValueError, match="reserve refused"):
        produce(destination)
    assert operations == ["reserve"] and tracking.load_record(path).resources == []
    assert not Path(selection["destination"]).exists()


def test_linked_selected_root_is_refused_before_reservation(destination: Destination) -> None:
    path, selection, operation_id = destination
    root = Path(selection["root"])
    linked = root.with_name("linked-user-root")
    try:
        linked.symlink_to(root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"native symbolic-link creation unavailable: {error}")
    selected = resolve_user_output({}, topic="Report", owner_roots={"downloads": str(linked)})
    with pytest.raises(ValueError, match="links"):
        output.produce_files(path, "m/p/owner", selection=selected,
                             files={"report.txt": b"fixture"}, operation_id=operation_id)
    assert tracking.load_record(path).resources == []


def test_replaced_owner_cannot_receive_a_stale_allocation(
    destination: Destination, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, selection, _ = destination
    original = output._invoke
    def replace_before_reservation(
        path: Path, owner: str, operation: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        if operation == "reserve":
            raw: dict[str, Any] = yaml.safe_load(path.read_text())
            raw["creation_nonce"] = secrets.token_hex(8)
            path.write_text(yaml.safe_dump(raw))
        return original(path, owner, operation, payload)
    monkeypatch.setattr(output, "_invoke", replace_before_reservation)
    with pytest.raises(ValueError, match="reserve refused"):
        produce(destination)
    assert tracking.load_record(path).resources == [] and not Path(selection["destination"]).exists()


@pytest.mark.parametrize("damage", ["members", "root_identity", "content", "created_at"])
def test_malformed_native_recovery_cannot_reach_user_storage(
    destination: Destination, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    path, selection, operation_id = destination
    result = output.allocate(
        path, "m/p/owner", selection=selection, manifest=manifest({"report.txt": b"fixture"}),
        operation_id=operation_id,
    )
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    claim = raw["resources"][0]
    recovery = claim["producer"]["recovery"]
    recovery[damage] = {
        "members": ["folder"] * 17,
        "root_identity": {"device": False, "inode": 1},
        "content": {"files": True, "bytes": 7, "sha256": "a" * 64},
        "created_at": "2026-01-02",
    }[damage]
    claim["ref"] = resource_producer.claim_ref(claim["producer"])
    path.write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(output, "_destination", lambda *a, **k: pytest.fail("invalid recovery traversed storage"))
    assert not claim_providers.resolve_claim_status(claim["ref"])["available"]
    assert metadata(path)["token"] == result["token"]
    assert tracking.load_record(path).resources[0].is_unsettled


def test_delivered_receipt_survives_record_retirement(destination: Destination) -> None:
    from agent_worktrees import launch_seed_state

    path, selection, _ = destination
    result = produce(destination)
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    with pytest.raises(ValueError, match="retained artifact metadata"):
        launch_seed_state._apply_remove({
            "yaml_path": str(path), "worktree_id": "owner", "remove_record": True,
        })
    assert tracking.retire_record(record, path.parent)
    assert tracking.load_record(path).status == "archived"
    assert metadata(path)["closeout"] == result["producer"]["closeout"]
    assert output.deliver(path, "m/p/owner", result["token"]) == result
    assert (Path(selection["destination"]) / "report.txt").read_bytes() == b"verified fixture"


def test_failed_retirement_cannot_delete_a_delivery_receipt(
    destination: Destination, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, selection, _ = destination
    produce(destination)
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    before = path.read_bytes()
    def failed(*args: Any, **options: Any) -> None:
        raise OSError("fixture tombstone write failure")
    monkeypatch.setattr(tracking, "save_record", failed)
    assert tracking.retire_record(record, path.parent) is False
    assert path.read_bytes() == before and Path(selection["destination"]).is_dir()


def test_paired_record_retirement_keeps_delivered_history(destination: Destination) -> None:
    from agent_worktrees import retirement_safety

    path, _, _ = destination
    result = produce(destination)
    sibling_path = path.with_name("consumer.yaml")
    sibling = tracking.load_record(sibling_path)
    sibling.status, sibling.reaped_at = "finalized", tracking._now_iso()
    tracking.save_record(sibling, sibling_path)
    assert retirement_safety.delete_retired_pair(tracking.load_record(path), sibling, path, sibling_path)
    assert path.exists() and sibling_path.exists()
    assert metadata(path)["closeout"] == result["producer"]["closeout"]


def test_prior_delivery_marker_cannot_be_adopted_by_a_new_allocation(destination: Destination) -> None:
    path, selection, _ = destination
    delivered = produce(destination)
    with pytest.raises(ValueError, match="already exists"):
        output.allocate(
            path, "m/p/owner", selection=selection, manifest=manifest({"report.txt": b"verified fixture"}),
            operation_id=secrets.token_urlsafe(24),
        )
    claims = tracking.load_record(path).resources
    assert len(claims) == 2 and not claims[0].is_unsettled and claims[1].is_unsettled
    assert not claim_providers.resolve_claim_status(claims[1].ref)["available"]
    assert output.deliver(path, "m/p/owner", delivered["token"]) == delivered
