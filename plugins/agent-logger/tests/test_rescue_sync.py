"""Tests for validated provider-rescue ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_logger import sessions
from agent_logger.chronicle.source import ReservationStore, SyncedSessionSource
from agent_logger.config import Config, load_config
from agent_logger.sync import engine, rescue
from agent_logger.sync.provenance import MAX_PROVENANCE_BYTES, rescue_snapshot_path
from agent_logger.sync.rescue_validation import RescueSourceError
from agent_logger.sync.targets import PushResult

SESSION_ID = "11111111-2222-4333-8444-555555555555"


def _cfg(
    tmp_path: Path,
    *,
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
    fail_closed: bool = False,
    target: str = "local",
) -> Config:
    home = tmp_path / "logger-home"
    data = load_config(home=home).as_dict()
    data["sync"]["targets"]["local"]["path"] = str(tmp_path / "target")
    data["sync"]["repo_allowlist"] = allowlist or []
    data["sync"]["repo_denylist"] = denylist or []
    data["sync"]["repo_allowlist_fail_closed"] = fail_closed
    data["sync"]["target"] = target
    return Config(data, home)


def _write_capture(
    root: Path,
    capture_id: str,
    *,
    container: str = "sandbox-1",
    session_id: str = SESSION_ID,
    events: bytes = b'{"type":"user.message"}\n',
    workspace: bytes = (
        b"repository: example/repo\n"
        b"interface: ACP\n"
        b"origin: Delegate\n"
        b"source: Agent Dispatch\n"
        b"model: example-model\n"
    ),
    origin: bytes | None = (
        b'{"schema_version":1,"source_repo":"example/repo","interface":"cli",'
        b'"origin":"user","source":"picker"}\n'
    ),
    status: str = "verified",
    completeness: str = "complete",
    metadata_overrides: dict | None = None,
    extra_members: dict[str, bytes] | None = None,
) -> Path:
    capture = root / container / capture_id
    session = capture / "sessions" / session_id
    session.mkdir(parents=True)
    payloads = {
        "events.jsonl": events,
        "workspace.yaml": workspace,
    }
    if origin is not None:
        payloads["origin.json"] = origin
    payloads.update(extra_members or {})
    members = {}
    for relative, payload in payloads.items():
        path = session / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        members[relative] = {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    metadata = {
        "schema_version": 1,
        "status": status,
        "completeness": completeness,
        "capture_id": capture_id,
        "captured_at": datetime.fromtimestamp(
            int(capture_id.split("-", 1)[0]), timezone.utc
        ).isoformat(),
        "container": container,
        "container_instance": f"sha256:{capture_id}",
        "container_generation": f"2026-08-28T00:00:{int(capture_id.split('-', 1)[0]) % 60:02d}Z",
        "fleet": "sandbox",
        "source_repo": "example/repo",
        "session_count": 1,
        "session_state": "present",
        "total_bytes": sum(item["bytes"] for item in members.values()),
        "sessions": {session_id: {"members": members}},
        "excluded": {},
        "restorable": False,
    }
    metadata.update(metadata_overrides or {})
    (capture / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    return capture


def _destination(tmp_path: Path) -> Path:
    return tmp_path / "target" / "container-sandbox-1"


def test_complete_capture_is_projected_with_generic_provenance(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    capture = _write_capture(root, "100-a")
    (root / ".capture-sandbox-1.lock").write_text("ignored", encoding="utf-8")
    (capture.parent / "status.json").write_text("{}", encoding="utf-8")
    (capture / ".pin-active.json").write_text("{}", encoding="utf-8")
    (capture.parent / ".staging-abandoned").mkdir()

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.accepted == 1
    assert summary.rejected == 0
    destination = _destination(tmp_path)
    assert (
        destination / "session-state" / SESSION_ID / "events.jsonl"
    ).read_bytes() == b'{"type":"user.message"}\n'
    assert not (destination / "session-state" / SESSION_ID / "origin.json").exists()
    assert (
        destination / "session-state" / SESSION_ID / "rescued-origin.json"
    ).is_file()
    provenance = json.loads(
        (destination / "provenance" / f"{SESSION_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assert provenance["schema_version"] == 1
    assert provenance["provider"] == "agent-containers"
    assert provenance["venue_kind"] == "container"
    assert provenance["venue_id"] == "container-sandbox-1"
    assert provenance["target_id"] == "container:sandbox-1"
    assert provenance["container_instance"] == "sha256:100-a"
    assert provenance["container_generation"].endswith("Z")
    assert provenance["fleet"] == "sandbox"
    assert provenance["capture_id"] == "100-a"
    assert provenance["repository"] == "example/repo"
    assert provenance["source_repo"] == "example/repo"
    assert provenance["interface"] == "cli"
    assert provenance["origin"] == "user"
    assert provenance["source"] == "picker"
    assert provenance["model"] == "example-model"
    assert provenance["billing_scope"] == "unknown"
    assert "rescued-origin.json" in provenance["members"]
    assert "origin.json" not in provenance["members"]
    assert "tokens" not in provenance
    assert "cost" not in provenance
    assert "account" not in provenance
    assert capture.is_dir()
    staging = tmp_path / "logger-home" / "rescue-sync" / "staging"
    assert list(staging.iterdir()) == []


def test_rescued_origin_cannot_override_host_routing_provenance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(
        root,
        "100-a",
        workspace=b"repository: attacker/claimed\n",
        origin=(
            b'{"source_repo":"attacker/claimed","repository":"attacker/claimed",'
            b'"origin":"user"}\n'
        ),
        metadata_overrides={"source_repo": "trusted/assignment"},
    )

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.accepted == 1
    destination = _destination(tmp_path)
    provenance = json.loads(
        (destination / "provenance" / f"{SESSION_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assert provenance["source_repo"] == "trusted/assignment"
    assert provenance["repository"] == "trusted/assignment"
    assert not (destination / "session-state" / SESSION_ID / "origin.json").exists()


def test_invalid_utf8_rescued_origin_does_not_abort_capture(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(
        root,
        "100-a",
        origin=b"\xff\xfe",
        metadata_overrides={"source_repo": "trusted/assignment"},
    )

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.accepted == 1
    provenance = json.loads(
        (
            _destination(tmp_path)
            / "provenance"
            / f"{SESSION_ID}.json"
        ).read_text(encoding="utf-8")
    )
    assert provenance["source_repo"] == "trusted/assignment"
    assert provenance["origin"] == "delegate"


def test_oversized_projected_provenance_rejects_venue(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    workspace = (
        b"repository: example/repo\nmodel: "
        + b"x" * (MAX_PROVENANCE_BYTES - 128)
        + b"\n"
    )
    _write_capture(root, "100-a", workspace=workspace)

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.accepted == 0
    assert summary.target_failures == 1
    assert any("projected provenance exceeds" in detail for detail in summary.details)
    assert not (_destination(tmp_path) / "session-state" / SESSION_ID).exists()


@pytest.mark.parametrize(
    ("status", "completeness", "overrides"),
    [
        ("failed", "complete", {}),
        ("abandoned", "complete", {}),
        ("verified", "complete", {"schema_version": 2}),
        ("verified", "complete", {"capture_id": "different"}),
        ("verified", "complete", {"session_state": "missing"}),
        ("verified", "complete", {"excluded": []}),
    ],
)
def test_non_complete_or_invalid_contract_is_rejected(
    tmp_path: Path,
    status: str,
    completeness: str,
    overrides: dict,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(
        root,
        "100-a",
        status=status,
        completeness=completeness,
        metadata_overrides=overrides,
    )
    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])
    assert summary.accepted == 0
    assert summary.rejected_captures == 1
    assert not (tmp_path / "target").exists()


def test_partial_capture_accepts_only_independently_complete_session(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rescues"
    capture = _write_capture(root, "100-a", completeness="partial")
    incomplete_id = "22222222-3333-4444-8555-666666666666"
    complete = capture / "sessions" / SESSION_ID
    incomplete = capture / "sessions" / incomplete_id
    incomplete.mkdir(parents=True)
    (incomplete / "workspace.yaml").write_text("repository: example/repo\n")
    metadata_path = capture / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    workspace = (incomplete / "workspace.yaml").read_bytes()
    metadata["sessions"][incomplete_id] = {
        "members": {
            "workspace.yaml": {
                "bytes": len(workspace),
                "sha256": hashlib.sha256(workspace).hexdigest(),
            }
        }
    }
    metadata["session_count"] = 2
    metadata["total_bytes"] += len(workspace)
    metadata["excluded"] = {
        "allowlisted": [],
        "missing_events": [incomplete_id],
    }
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.accepted == 1
    assert summary.rejected_sessions == 1
    destination = _destination(tmp_path) / "session-state"
    assert (destination / SESSION_ID / "events.jsonl").is_file()
    assert not (destination / incomplete_id).exists()
    assert complete.is_dir()


def test_malformed_metadata_is_rejected(tmp_path: Path) -> None:
    capture = tmp_path / "rescues" / "sandbox-1" / "100-a"
    capture.mkdir(parents=True)
    (capture / "metadata.json").write_text("{", encoding="utf-8")
    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[tmp_path / "rescues"])
    assert summary.rejected_captures == 1


def test_invalid_utf8_checkpoint_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    checkpoint = cfg.home / "rescue-sync" / "checkpoint.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"\xff\xfe")

    with pytest.raises(RescueSourceError, match="invalid rescue checkpoint"):
        rescue.push_rescues(cfg, rescue_roots=[root])


@pytest.mark.parametrize("field", ["bytes", "sha256"])
def test_member_size_or_hash_mismatch_is_rejected(tmp_path: Path, field: str) -> None:
    root = tmp_path / "rescues"
    capture = _write_capture(root, "100-a")
    metadata_path = capture / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    member = metadata["sessions"][SESSION_ID]["members"]["events.jsonl"]
    member[field] = member[field] + 1 if field == "bytes" else "0" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])
    assert summary.accepted == 0
    if field == "bytes":
        assert summary.rejected_captures == 1
    else:
        assert summary.rejected_sessions == 1


def test_capture_total_size_mismatch_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    capture = _write_capture(root, "100-a")
    metadata_path = capture / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["total_bytes"] += 1
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])
    assert summary.accepted == 0
    assert summary.rejected_captures == 1


def test_newest_complete_capture_wins_over_older_and_partial(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a", events=b'{"value":"old"}\n')
    _write_capture(root, "200-b", events=b'{"value":"new"}\n')
    _write_capture(
        root,
        "300-c",
        events=b'{"value":"partial"}\n',
        completeness="partial",
        metadata_overrides={
            "excluded": {
                "allowlisted": [],
                "missing_events": [SESSION_ID],
            }
        },
    )

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.accepted == 1
    assert summary.skipped == 1
    assert summary.rejected_sessions == 1
    assert (
        _destination(tmp_path) / "session-state" / SESSION_ID / "events.jsonl"
    ).read_bytes() == b'{"value":"new"}\n'
def test_newer_capture_updates_and_older_capture_never_rewinds(tmp_path: Path) -> None:
    older_root = tmp_path / "older"
    newer_root = tmp_path / "newer"
    _write_capture(older_root, "100-a", events=b'{"value":"old"}\n')
    cfg = _cfg(tmp_path)
    first = rescue.push_rescues(cfg, rescue_roots=[older_root])
    assert first.accepted == 1

    _write_capture(newer_root, "200-b", events=b'{"value":"new"}\n')
    second = rescue.push_rescues(cfg, rescue_roots=[older_root, newer_root])
    assert second.accepted == 1
    assert (
        _destination(tmp_path) / "session-state" / SESSION_ID / "events.jsonl"
    ).read_bytes() == b'{"value":"new"}\n'
    third = rescue.push_rescues(cfg, rescue_roots=[older_root])
    assert third.accepted == 0
    assert third.skipped == 1
    assert (
        _destination(tmp_path) / "session-state" / SESSION_ID / "events.jsonl"
    ).read_bytes() == b'{"value":"new"}\n'


def test_chronicler_capture_path_remains_immutable_after_newer_rescue(
    tmp_path: Path,
) -> None:
    older_root = tmp_path / "older"
    newer_root = tmp_path / "newer"
    _write_capture(older_root, "100-a", events=b'{"value":"old"}\n')
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[older_root]).accepted == 1
    source = SyncedSessionSource(
        tmp_path / "target",
        ReservationStore(tmp_path / "reservations.db"),
        settle_seconds=0,
    )
    first = source.scan()[0]
    assert first.session_path == rescue_snapshot_path(
        _destination(tmp_path),
        SESSION_ID,
        "100-a",
    )

    _write_capture(newer_root, "200-b", events=b'{"value":"new"}\n')
    assert rescue.push_rescues(
        cfg,
        rescue_roots=[older_root, newer_root],
    ).accepted == 1
    by_capture = {
        session.ref.parent_session_id.rsplit("@", 1)[-1]: session
        for session in source.scan()
    }
    second = by_capture["200-b"]

    assert first.session_path != second.session_path
    assert (first.session_path / "events.jsonl").read_bytes() == b'{"value":"old"}\n'
    assert (second.session_path / "events.jsonl").read_bytes() == b'{"value":"new"}\n'


def test_all_unjournaled_rescue_snapshots_are_discovered(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path / "other")
    _write_capture(tmp_path / "other-old", "100-a", events=b'{"value":"old"}\n')
    assert rescue.push_rescues(
        cfg,
        rescue_roots=[tmp_path / "other-old"],
    ).accepted == 1
    _write_capture(tmp_path / "other-new", "200-b", events=b'{"value":"new"}\n')
    assert rescue.push_rescues(
        cfg,
        rescue_roots=[tmp_path / "other-old", tmp_path / "other-new"],
    ).accepted == 1
    source = SyncedSessionSource(
        tmp_path / "other" / "target",
        ReservationStore(tmp_path / "other-reservations.db"),
        settle_seconds=0,
    )

    captures = {
        session.ref.parent_session_id.rsplit("@", 1)[-1]
        for session in source.scan()
    }

    assert captures == {"100-a", "200-b"}


def test_chronicler_rejects_symlinked_snapshot_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 1
    machine_root = _destination(tmp_path)
    snapshot = rescue_snapshot_path(machine_root, SESSION_ID, "100-a")
    outside = tmp_path / "outside-snapshots"
    snapshot.parent.rename(outside)
    try:
        snapshot.parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    source = SyncedSessionSource(
        tmp_path / "target",
        ReservationStore(tmp_path / "reservations.db"),
        settle_seconds=0,
    )

    assert source.scan() == []


def test_destination_high_water_blocks_stale_checkpoint_rewind(
    tmp_path: Path,
) -> None:
    older_root = tmp_path / "older"
    newer_root = tmp_path / "newer"
    _write_capture(older_root, "100-a", events=b'{"value":"old"}\n')
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[older_root]).accepted == 1
    checkpoint = cfg.home / "rescue-sync" / "checkpoint.json"
    stale_checkpoint = checkpoint.read_bytes()

    _write_capture(newer_root, "200-b", events=b'{"value":"new"}\n')
    assert rescue.push_rescues(
        cfg,
        rescue_roots=[older_root, newer_root],
    ).accepted == 1
    checkpoint.write_bytes(stale_checkpoint)

    summary = rescue.push_rescues(cfg, rescue_roots=[older_root])

    assert summary.accepted == 0
    assert summary.target_failures == 1
    assert any("refusing rescue rewind" in detail for detail in summary.details)
    assert (
        _destination(tmp_path) / "session-state" / SESSION_ID / "events.jsonl"
    ).read_bytes() == b'{"value":"new"}\n'


def test_destination_rejects_symlinked_high_water_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 1
    high_water = _destination(tmp_path) / ".session-sync-rescue-high-water"
    outside = tmp_path / "outside-high-water"
    high_water.rename(outside)
    try:
        high_water.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    summary = rescue.push_rescues(cfg, rescue_roots=[root])

    assert summary.accepted == 0
    assert summary.target_failures == 1
    assert any("destination directory is unsafe" in detail for detail in summary.details)


def test_destination_rejects_reused_capture_id_with_later_timestamp(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_capture(first_root, "100-a", events=b'{"value":"first"}\n')
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[first_root]).accepted == 1
    (cfg.home / "rescue-sync" / "checkpoint.json").unlink()
    _write_capture(
        second_root,
        "100-a",
        events=b'{"value":"other"}\n',
        metadata_overrides={"captured_at": "2026-08-29T00:00:00+00:00"},
    )

    summary = rescue.push_rescues(cfg, rescue_roots=[second_root])

    assert summary.accepted == 0
    assert summary.target_failures == 1
    assert any(
        (
            "capture identity changed at destination" in detail
            or "immutable rescue snapshot changed" in detail
        )
        for detail in summary.details
    )
    assert (
        _destination(tmp_path) / "session-state" / SESSION_ID / "events.jsonl"
    ).read_bytes() == b'{"value":"first"}\n'


def test_newer_projection_removes_stale_optional_members(tmp_path: Path) -> None:
    older_root = tmp_path / "older"
    newer_root = tmp_path / "newer"
    _write_capture(
        older_root,
        "100-a",
        extra_members={
            "context.json": b'{"old":true}\n',
            "checkpoints/index.md": b"# old\n",
        },
    )
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[older_root]).accepted == 1
    destination = _destination(tmp_path) / "session-state" / SESSION_ID
    assert (destination / "context.json").exists()
    assert (destination / "rescued-origin.json").exists()
    assert (destination / "checkpoints" / "index.md").exists()

    _write_capture(
        newer_root,
        "200-b",
        events=b'{"value":"new"}\n',
        origin=None,
    )
    assert rescue.push_rescues(
        cfg, rescue_roots=[older_root, newer_root]
    ).accepted == 1

    assert (destination / "events.jsonl").read_bytes() == b'{"value":"new"}\n'
    assert not (destination / "context.json").exists()
    assert not (destination / "rescued-origin.json").exists()
    assert not (destination / "checkpoints").exists()


def test_replacement_backup_is_outside_session_state_and_cleanup_failure_is_nonfatal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    older_root = tmp_path / "older"
    newer_root = tmp_path / "newer"
    _write_capture(older_root, "100-a")
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[older_root]).accepted == 1
    _write_capture(newer_root, "200-b", events=b'{"value":"new"}\n')

    original_force_rmtree = sessions.force_rmtree

    def fail_backup(path: Path) -> bool:
        if (
            path.name.endswith(".cleanup")
            and (path / "old").exists()
        ):
            return False
        return original_force_rmtree(path)

    monkeypatch.setattr(sessions, "force_rmtree", fail_backup)
    summary = rescue.push_rescues(
        cfg,
        rescue_roots=[older_root, newer_root],
    )

    assert summary.accepted == 1
    assert summary.target_failures == 0
    machine_root = _destination(tmp_path)
    session_state = machine_root / "session-state"
    assert not any(path.name.endswith(".old") for path in session_state.iterdir())
    backups = list(
        (machine_root / ".session-sync-replacement").glob("*.cleanup/old")
    )
    assert backups
    source = SyncedSessionSource(
        tmp_path / "target",
        ReservationStore(tmp_path / "reservations.db"),
        settle_seconds=0,
    )
    discovered = source.scan()
    assert [item.session_id for item in discovered] == [SESSION_ID, SESSION_ID]
    assert {
        item.ref.parent_session_id.rsplit("@", 1)[-1]
        for item in discovered
    } == {"100-a", "200-b"}

    monkeypatch.setattr(sessions, "force_rmtree", original_force_rmtree)
    _write_capture(newer_root, "300-c", events=b'{"value":"newer"}\n')
    assert rescue.push_rescues(
        cfg,
        rescue_roots=[older_root, newer_root],
    ).accepted == 1
    assert not (
        machine_root / ".session-sync-replacement"
    ).exists()


def test_filesystem_venue_failure_rolls_back_sessions_and_provenance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    other_id = "22222222-3333-4444-8555-666666666666"
    _write_capture(first_root, "100-a", events=b'{"value":"old-a"}\n')
    _write_capture(
        first_root,
        "101-b",
        session_id=other_id,
        events=b'{"value":"old-b"}\n',
    )
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[first_root]).accepted == 2

    _write_capture(second_root, "200-c", events=b'{"value":"new-a"}\n')
    _write_capture(
        second_root,
        "201-d",
        session_id=other_id,
        events=b'{"value":"new-b"}\n',
    )
    from agent_logger.sync.targets import filesystem

    original_replace = filesystem.os.replace

    def fail_second_provenance(src: Path, dst: Path) -> None:
        if (
            ".session-sync-replacement" in src.parts
            and "new" in src.parts
            and dst == _destination(tmp_path) / "provenance" / f"{other_id}.json"
        ):
            raise OSError("synthetic provenance failure")
        original_replace(src, dst)

    monkeypatch.setattr(filesystem.os, "replace", fail_second_provenance)
    summary = rescue.push_rescues(
        cfg,
        rescue_roots=[first_root, second_root],
    )

    assert summary.accepted == 0
    assert summary.target_failures == 1
    destination = _destination(tmp_path)
    assert (
        destination / "session-state" / SESSION_ID / "events.jsonl"
    ).read_bytes() == b'{"value":"old-a"}\n'
    assert (
        destination / "session-state" / other_id / "events.jsonl"
    ).read_bytes() == b'{"value":"old-b"}\n'
    first_provenance = json.loads(
        (destination / "provenance" / f"{SESSION_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    second_provenance = json.loads(
        (destination / "provenance" / f"{other_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert first_provenance["capture_id"] == "100-a"
    assert second_provenance["capture_id"] == "101-b"


def test_failed_rollback_retains_recovery_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    older_root = tmp_path / "older"
    newer_root = tmp_path / "newer"
    _write_capture(older_root, "100-a", events=b'{"value":"old"}\n')
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[older_root]).accepted == 1
    _write_capture(newer_root, "200-b", events=b'{"value":"new"}\n')
    from agent_logger.sync.targets import filesystem

    original_replace = filesystem.os.replace
    destination = _destination(tmp_path) / "session-state" / SESSION_ID

    def fail_publish_and_restore(src: Path, dst: Path) -> None:
        if dst == destination and (
            "new" in src.parts or "old" in src.parts
        ):
            raise OSError("synthetic session replacement failure")
        original_replace(src, dst)

    monkeypatch.setattr(filesystem.os, "replace", fail_publish_and_restore)
    summary = rescue.push_rescues(
        cfg,
        rescue_roots=[older_root, newer_root],
    )

    assert summary.accepted == 0
    assert summary.target_failures == 1
    assert any("rollback incomplete" in detail for detail in summary.details)
    recovery = list(
        (_destination(tmp_path) / ".session-sync-replacement").glob(
            "*.active/old/session-state/*"
        )
    )
    assert len(recovery) == 1
    assert (recovery[0] / "events.jsonl").read_bytes() == b'{"value":"old"}\n'

    monkeypatch.setattr(filesystem.os, "replace", original_replace)
    retry = rescue.push_rescues(
        cfg,
        rescue_roots=[older_root, newer_root],
    )
    assert retry.accepted == 1
    assert retry.target_failures == 0
    assert (
        _destination(tmp_path) / "session-state" / SESSION_ID / "events.jsonl"
    ).read_bytes() == b'{"value":"new"}\n'


def test_failure_to_mark_completed_transaction_is_target_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    from agent_logger.sync.targets import filesystem

    original_replace = filesystem.os.replace

    def fail_completion_mark(src: Path, dst: Path) -> None:
        if src.name.endswith(".active") and dst.name.endswith(".cleanup"):
            raise OSError("synthetic completion-mark failure")
        original_replace(src, dst)

    monkeypatch.setattr(filesystem.os, "replace", fail_completion_mark)
    cfg = _cfg(tmp_path)
    summary = rescue.push_rescues(cfg, rescue_roots=[root])

    assert summary.accepted == 0
    assert summary.target_failures == 1
    assert any(
        "cannot mark replacement complete" in detail
        for detail in summary.details
    )
    assert list(
        (_destination(tmp_path) / ".session-sync-replacement").glob("*.active")
    )
    assert not (cfg.home / "rescue-sync" / "checkpoint.json").exists()

    monkeypatch.setattr(filesystem.os, "replace", original_replace)
    retry = rescue.push_rescues(cfg, rescue_roots=[root])
    assert retry.accepted == 1
    assert not list(
        (_destination(tmp_path) / ".session-sync-replacement").glob("*.active")
    )


@pytest.mark.parametrize("mutation", ["metadata", "session", "member"])
def test_same_capture_id_manifest_mutation_rejects_entire_capture(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = tmp_path / "rescues"
    capture = _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 1
    metadata_path = capture / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if mutation == "metadata":
        metadata["fleet"] = "changed"
    elif mutation == "session":
        other = "22222222-3333-4444-8555-666666666666"
        events = b"new session\n"
        other_dir = capture / "sessions" / other
        other_dir.mkdir()
        (other_dir / "events.jsonl").write_bytes(events)
        metadata["sessions"][other] = {
            "members": {
                "events.jsonl": {
                    "bytes": len(events),
                    "sha256": hashlib.sha256(events).hexdigest(),
                }
            }
        }
        metadata["session_count"] = 2
        metadata["total_bytes"] += len(events)
    else:
        context = b'{"changed":true}\n'
        session = capture / "sessions" / SESSION_ID
        (session / "context.json").write_bytes(context)
        metadata["sessions"][SESSION_ID]["members"]["context.json"] = {
            "bytes": len(context),
            "sha256": hashlib.sha256(context).hexdigest(),
        }
        metadata["total_bytes"] += len(context)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    summary = rescue.push_rescues(cfg, rescue_roots=[root])

    assert summary.accepted == 0
    assert summary.rejected_captures == 1
    assert any("capture mutated" in detail for detail in summary.details)


def test_session_high_water_preserves_capture_identity_after_source_pruned(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rescues"
    capture = _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 1

    # Provider retention removes the source capture. The compact capture
    # tombstone and per-session high-water both remain.
    shutil.rmtree(capture)
    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 0
    checkpoint_path = cfg.home / "rescue-sync" / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert len(checkpoint["captures"]) == 1
    record = next(iter(checkpoint["sessions"].values()))
    assert record["capture_fingerprint"]

    # Reuse the same provider+venue+capture ID with altered metadata. The
    # surviving session record is still sufficient to reject the whole capture.
    _write_capture(
        root,
        "100-a",
        metadata_overrides={"fleet": "mutated"},
    )
    summary = rescue.push_rescues(cfg, rescue_roots=[root])

    assert summary.accepted == 0
    assert summary.rejected_captures == 1
    assert any("capture mutated" in detail for detail in summary.details)


def test_capture_tombstone_rejects_reused_id_with_entirely_new_sessions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rescues"
    original = _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 1
    shutil.rmtree(original)
    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 0
    checkpoint = json.loads(
        (cfg.home / "rescue-sync" / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert len(checkpoint["captures"]) == 1

    replacement_session = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    _write_capture(
        root,
        "100-a",
        session_id=replacement_session,
    )
    summary = rescue.push_rescues(cfg, rescue_roots=[root])

    assert summary.accepted == 0
    assert summary.rejected_captures == 1
    assert not (
        _destination(tmp_path) / "session-state" / replacement_session
    ).exists()

def test_invalid_session_id_and_missing_events_are_rejected(tmp_path: Path) -> None:
    bad_id_root = tmp_path / "bad-id"
    _write_capture(bad_id_root, "100-a", session_id="../escape")
    missing_root = tmp_path / "missing"
    capture = _write_capture(missing_root, "200-b")
    metadata_path = capture / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    events = metadata["sessions"][SESSION_ID]["members"].pop("events.jsonl")
    metadata["total_bytes"] -= events["bytes"]
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    summary = rescue.push_rescues(
        _cfg(tmp_path), rescue_roots=[bad_id_root, missing_root]
    )
    assert summary.accepted == 0
    assert summary.rejected_captures == 1
    assert summary.rejected_sessions == 1


def test_physically_missing_events_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    capture = _write_capture(root, "100-a")
    (capture / "sessions" / SESSION_ID / "events.jsonl").unlink()

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.accepted == 0
    assert summary.rejected_sessions == 1


@pytest.mark.parametrize(
    ("events", "reason"),
    [
        (b"\xff\xfe\n", "valid UTF-8"),
        (b'{"ok":true}\n{broken\n', "malformed JSON"),
        (b'{"ok":true}\n[1,2,3]\n', "not a JSON object"),
    ],
)
def test_invalid_events_jsonl_rejects_only_session(
    tmp_path: Path,
    events: bytes,
    reason: str,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a", events=events)

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.accepted == 0
    assert summary.rejected_sessions == 1
    assert any(reason in detail for detail in summary.details)


def test_symlink_and_special_member_are_rejected(tmp_path: Path) -> None:
    symlink_root = tmp_path / "symlink"
    capture = _write_capture(symlink_root, "100-a")
    events = capture / "sessions" / SESSION_ID / "events.jsonl"
    outside = tmp_path / "outside"
    outside.write_bytes(events.read_bytes())
    events.unlink()
    try:
        events.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    roots = [symlink_root]
    if hasattr(os, "mkfifo"):
        fifo_root = tmp_path / "fifo"
        fifo_capture = _write_capture(fifo_root, "200-b")
        fifo = fifo_capture / "sessions" / SESSION_ID / "events.jsonl"
        fifo.unlink()
        os.mkfifo(fifo)
        roots.append(fifo_root)

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=roots)
    assert summary.accepted == 0
    assert summary.rejected_sessions == len(roots)


def test_rescue_repo_allowlist_is_always_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(
        root,
        "100-a",
        workspace=b"cwd: /workspace/unclassified\n",
        origin=None,
        metadata_overrides={"source_repo": None},
    )
    summary = rescue.push_rescues(
        _cfg(tmp_path, allowlist=["allowed-repo"]),
        rescue_roots=[root],
    )
    assert summary.accepted == 0
    assert summary.rejected_sessions == 1
    assert not (tmp_path / "target").exists()


def test_repo_allowlist_uses_provider_assignment_not_rescued_claim(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(
        root,
        "100-a",
        workspace=b"repository: allowed-repo\n",
        origin=b'{"source_repo":"allowed-repo"}\n',
        metadata_overrides={"source_repo": "different-repo"},
    )
    summary = rescue.push_rescues(
        _cfg(tmp_path, allowlist=["allowed-repo"]), rescue_roots=[root]
    )
    assert summary.accepted == 0
    assert summary.rejected_sessions == 1


def test_repo_policy_uses_exact_classification_and_fail_closed(tmp_path: Path) -> None:
    substring_root = tmp_path / "substring"
    _write_capture(
        substring_root,
        "100-a",
        metadata_overrides={"source_repo": "example/repository-mirror"},
    )
    substring = rescue.push_rescues(
        _cfg(tmp_path, allowlist=["example/repository"]),
        rescue_roots=[substring_root],
    )
    assert substring.accepted == 0
    assert substring.rejected_sessions == 1

    unknown_root = tmp_path / "unknown"
    _write_capture(
        unknown_root,
        "200-b",
        metadata_overrides={"source_repo": None},
    )
    unknown = rescue.push_rescues(
        _cfg(tmp_path, fail_closed=True),
        rescue_roots=[unknown_root],
    )
    assert unknown.accepted == 0
    assert unknown.rejected_sessions == 1


def test_unknown_metadata_fields_are_ignored(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    capture = _write_capture(
        root,
        "100-a",
        metadata_overrides={"future_provider_field": {"version": 2}},
    )
    metadata_path = capture / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["sessions"][SESSION_ID]["members"]["events.jsonl"]["future"] = True
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])
    assert summary.accepted == 1


def test_unknown_declared_member_is_ignored_and_reported(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    capture = _write_capture(root, "100-a")
    metadata_path = capture / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["sessions"][SESSION_ID]["members"]["future.bin"] = {
        "bytes": 7,
        "sha256": "a" * 64,
    }
    metadata["total_bytes"] += 7
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root], verbose=True)

    assert summary.accepted == 1
    assert any("ignored unknown declared member" in item for item in summary.details)
    assert not (
        _destination(tmp_path) / "session-state" / SESSION_ID / "future.bin"
    ).exists()


@pytest.mark.parametrize(
    "excluded",
    [
        {"allowlisted": {}, "missing_events": []},
        {"allowlisted": [], "missing_events": {}},
        {
            "allowlisted": [
                {
                    "session_id": "../escape",
                    "member": "events.jsonl",
                    "reason": "missing",
                }
            ],
            "missing_events": [],
        },
    ],
)
def test_malformed_excluded_metadata_rejects_capture_but_other_venue_continues(
    tmp_path: Path,
    excluded: dict,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(
        root,
        "100-a",
        container="bad-worker",
        metadata_overrides={
            "completeness": "partial",
            "excluded": excluded,
        },
    )
    _write_capture(root, "200-b", container="good-worker")

    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.accepted == 1
    assert summary.rejected_captures == 1
    assert summary.venues_pushed == 1
    assert any("excluded" in detail or "session id" in detail for detail in summary.details)


def test_second_push_revalidates_destination_and_does_not_restore(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        sessions,
        "restore_session",
        lambda *_args, **_kwargs: pytest.fail("rescues must never be restored"),
    )

    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 1
    destination = _destination(tmp_path) / "session-state" / SESSION_ID
    before = destination.stat()
    second = rescue.push_rescues(cfg, rescue_roots=[root])

    assert second.accepted == 1
    assert second.skipped == 0
    after = destination.stat()
    assert after.st_ino == before.st_ino
    assert after.st_mtime_ns == before.st_mtime_ns


def test_retained_capture_repairs_missing_destination(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 1
    destination = _destination(tmp_path) / "session-state" / SESSION_ID
    shutil.rmtree(destination)

    summary = rescue.push_rescues(cfg, rescue_roots=[root])

    assert summary.accepted == 1
    assert (destination / "events.jsonl").is_file()


def test_retained_capture_repairs_same_size_newer_corruption(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[root]).accepted == 1
    destination = _destination(tmp_path) / "session-state" / SESSION_ID / "events.jsonl"
    original = destination.read_bytes()
    corrupted = original.replace(b"user", b"xxxx")
    assert len(corrupted) == len(original)
    destination.write_bytes(corrupted)
    future = destination.stat().st_mtime + 3600
    os.utime(destination, (future, future))

    summary = rescue.push_rescues(cfg, rescue_roots=[root])

    assert summary.accepted == 1
    assert destination.read_bytes() == original


def test_newer_capture_cannot_change_provider_assignment(tmp_path: Path) -> None:
    older_root = tmp_path / "older"
    newer_root = tmp_path / "newer"
    _write_capture(
        older_root,
        "100-a",
        metadata_overrides={"source_repo": "owner/original"},
    )
    cfg = _cfg(tmp_path)
    assert rescue.push_rescues(cfg, rescue_roots=[older_root]).accepted == 1
    _write_capture(
        newer_root,
        "200-b",
        metadata_overrides={"source_repo": "owner/reassigned"},
    )

    summary = rescue.push_rescues(
        cfg,
        rescue_roots=[older_root, newer_root],
    )

    assert summary.accepted == 0
    assert summary.rejected_captures == 1
    assert any("provider assignment changed" in detail for detail in summary.details)
    provenance = json.loads(
        (
            _destination(tmp_path)
            / "provenance"
            / f"{SESSION_ID}.json"
        ).read_text(encoding="utf-8")
    )
    assert provenance["source_repo"] == "owner/original"


def test_dry_run_validates_without_target_or_checkpoint(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)

    summary = rescue.push_rescues(cfg, rescue_roots=[root], dry_run=True)

    assert summary.accepted == 1
    assert summary.venues_pushed == 0
    assert not (tmp_path / "target").exists()
    assert not (cfg.home / "rescue-sync" / "checkpoint.json").exists()


def test_checkpoint_compacts_stale_sessions_but_retains_capture_tombstones(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    checkpoint = cfg.home / "rescue-sync" / "checkpoint.json"
    checkpoint.parent.mkdir(parents=True)
    stale_sessions = {
        f"agent-containers|old-venue|session-{index}": {
            "capture_id": f"old-{index}",
            "capture_order": [1.0, f"old-{index}"],
            "captured_at": "2026-01-01T00:00:00+00:00",
            "member_fingerprint": "a" * 64,
            "members": {"events.jsonl": "b" * 64},
            "target_fingerprint": "c" * 64,
        }
        for index in range(9000)
    }
    checkpoint.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "captures": {
                    f"agent-containers|old-venue|old-{index}": {
                        "capture_id": f"old-{index}",
                        "fingerprint": "d" * 64,
                    }
                    for index in range(9000)
                },
                "sessions": stale_sessions,
            }
        ),
        encoding="utf-8",
    )

    summary = rescue.push_rescues(cfg, rescue_roots=[root])

    assert summary.accepted == 1
    persisted = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert len(persisted["sessions"]) == 1
    assert len(persisted["captures"]) == 9001
    assert checkpoint.stat().st_size < rescue._MAX_CHECKPOINT_BYTES


def test_oversize_checkpoint_refuses_before_replacing_last_readable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text('{"schema_version":2,"captures":{},"sessions":{}}\n')
    monkeypatch.setattr(rescue, "_MAX_CHECKPOINT_BYTES", 100)
    payload = {
        "schema_version": 2,
        "captures": {
            f"capture-{index}": {
                "capture_id": f"capture-{index}",
                "fingerprint": "a" * 64,
            }
            for index in range(10)
        },
        "sessions": {},
    }

    with pytest.raises(RescueSourceError, match="would exceed"):
        rescue._write_checkpoint(path, payload)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "captures": {},
        "sessions": {},
    }
    assert not list(path.parent.glob(".*.tmp"))


def test_dotfiles_managed_home_ancestor_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    repository = tmp_path / "home"
    (repository / ".git").mkdir(parents=True)
    cfg = _cfg(tmp_path)
    cfg.home = repository / ".agent-logger"

    summary = rescue.push_rescues(cfg, rescue_roots=[root])

    assert summary.accepted == 1


def test_rescue_sync_state_itself_cannot_be_repository(tmp_path: Path) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    state = cfg.home / "rescue-sync"
    (state / ".git").mkdir(parents=True)

    with pytest.raises(RescueSourceError, match="must not be a repository"):
        rescue.push_rescues(cfg, rescue_roots=[root])


def test_projection_is_cleaned_after_target_failure(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    seen: list[Path] = []

    class FailingTarget:
        name = "synthetic"
        rescue_compare_and_set = True

        def push(self, source: Path, _machine: str, _include: set[str]) -> PushResult:
            seen.append(source)
            assert source.is_dir()
            assert source != root
            assert (source / "session-state" / SESSION_ID / "events.jsonl").is_file()
            assert (source / "provenance" / f"{SESSION_ID}.json").is_file()
            return PushResult(ok=False, detail="synthetic failure")

    monkeypatch.setattr(rescue, "build_target", lambda *_args: FailingTarget())
    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])
    assert summary.accepted == 0
    assert summary.rejected_sessions == 1
    assert any("synthetic failure" in item for item in summary.details)
    assert seen and not seen[0].exists()


def test_target_failure_continues_other_venues(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a", container="a-worker")
    _write_capture(root, "200-b", container="b-worker")

    class PartialTarget:
        name = "synthetic"
        rescue_compare_and_set = True

        def push(self, _source: Path, machine: str, _include: set[str]) -> PushResult:
            if machine == "container-a-worker":
                return PushResult(ok=False, detail="synthetic failure")
            return PushResult(ok=True, detail="ok", file_count=1)

    monkeypatch.setattr(rescue, "build_target", lambda *_args: PartialTarget())
    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.venues_pushed == 1
    assert summary.accepted == 1
    assert summary.rejected_sessions == 1
    assert any("synthetic failure" in detail for detail in summary.details)


def test_projection_failure_continues_other_venues(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a", container="a-worker")
    _write_capture(root, "200-b", container="b-worker")
    original_push = rescue.push_venue

    def fail_first(target, cfg, venue, selected):
        if venue == "container-a-worker":
            raise RescueSourceError("synthetic projection failure")
        return original_push(target, cfg, venue, selected)

    monkeypatch.setattr(rescue, "push_venue", fail_first)
    summary = rescue.push_rescues(_cfg(tmp_path), rescue_roots=[root])

    assert summary.venues_pushed == 1
    assert summary.accepted == 1
    assert summary.target_failures == 1
    assert any("synthetic projection failure" in detail for detail in summary.details)


@pytest.mark.parametrize("target", ["ssh", "onedrive"])
def test_rescue_push_rejects_target_without_compare_and_set(
    tmp_path: Path,
    target: str,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")

    with pytest.raises(RescueSourceError, match="compare-and-set"):
        rescue.push_rescues(
            _cfg(tmp_path, target=target),
            rescue_roots=[root],
        )


def test_mixed_target_success_failure_cli_is_nonzero(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a", container="a-worker")
    _write_capture(root, "200-b", container="b-worker")

    class PartialTarget:
        name = "synthetic"
        rescue_compare_and_set = True

        def push(self, _source: Path, machine: str, _include: set[str]) -> PushResult:
            return PushResult(
                ok=machine == "container-b-worker",
                detail="ok" if machine == "container-b-worker" else "failed",
            )

    monkeypatch.setattr(rescue, "build_target", lambda *_args: PartialTarget())

    rc = rescue.run_rescue_push(
        _cfg(tmp_path),
        rescue_roots=[str(root)],
        provider="agent-containers",
        target_prefix="container",
        dry_run=False,
        verbose=False,
    )

    assert rc == 1
    output = capsys.readouterr().out
    assert "accepted=1" in output
    assert "target_failures=1" in output


def test_all_rejected_cli_run_is_nonzero_and_prints_summary(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a", status="failed")

    rc = rescue.run_rescue_push(
        _cfg(tmp_path),
        rescue_roots=[str(root)],
        provider="agent-containers",
        target_prefix="container",
        dry_run=False,
        verbose=False,
    )

    assert rc == 1
    out = capsys.readouterr().out
    assert "accepted=0" in out
    assert "rejected=1" in out


def test_rejected_container_root_without_capture_is_nonzero(
    tmp_path: Path,
) -> None:
    root = tmp_path / "rescues"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "linked-container").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    rc = rescue.run_rescue_push(
        _cfg(tmp_path),
        rescue_roots=[str(root)],
        provider="agent-containers",
        target_prefix="container",
        dry_run=False,
        verbose=False,
    )

    assert rc == 1


def test_rescue_push_honors_global_sync_disable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "rescues"
    _write_capture(root, "100-a")
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("AGENT_LOGGER_SYNC_DISABLED", "1")

    rc = rescue.run_rescue_push(
        cfg,
        rescue_roots=[str(root)],
        provider="agent-containers",
        target_prefix="container",
        dry_run=False,
        verbose=False,
    )

    assert rc == 0
    assert not (tmp_path / "target").exists()
    assert not (cfg.home / "rescue-sync" / "checkpoint.json").exists()


def test_rescue_push_parser_wires_repeatable_roots() -> None:
    args = engine.build_parser().parse_args(
        [
            "rescue-push",
            "--rescue-root",
            "/provider/a/rescues",
            "--rescue-root",
            "/provider/b/rescues",
            "--target-prefix",
            "sandbox",
            "--dry-run",
            "--verbose",
        ]
    )
    assert args.command == "rescue-push"
    assert args.rescue_root == ["/provider/a/rescues", "/provider/b/rescues"]
    assert args.provider == "agent-containers"
    assert args.target_prefix == "sandbox"
    assert args.dry_run is True
    assert args.verbose is True
