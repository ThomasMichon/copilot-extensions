"""Ordinary session disposal never reclaims retained or uncertain collections."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import time
from typing import Any

import pytest

from agent_logger import sessions
from agent_logger.config import Config
from agent_logger.sync.targets import filesystem
from agent_logger.sync.targets.filesystem import LocalTarget
from agent_logger.sync import worktree_state_preservation as preservation
from agent_logger.sync.worktree_state import WorktreeStateError
from agent_logger.sync.protection import ProtectionSettings
from agent_logger.sync.worktree_state_archive import collect_worktree_state
from .test_worktree_state import IDENTITY, Packet, collect, packet as packet, plan_for

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX collection fixtures")


def files(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def legacy_collection(packet: Packet) -> tuple[LocalTarget, Path]:
    _, target, _, _ = packet
    collect(packet)
    live = target._root() / "worker/session-state/old"
    live.mkdir(parents=True)
    (live / "events.jsonl").write_bytes(b'{"type":"fixture"}\n')
    (live / "workspace.yaml").write_text(
        "updated_at: 2020-01-01T00:00:00Z\ncwd: /fixture\n", encoding="utf-8",
    )
    shutil.copytree(target._root() / "worktree-state", live / "worktree-state")
    return target, live


@pytest.mark.parametrize("relative", [
    "worker/session-state/old", "session-state", "worker/provenance/old.json",
    "worker/archived/store", "worker/.session-sync-replacement/transaction/new",
])
def test_new_collection_placement_refuses_disposable_payload_ancestry(
    packet: Packet, relative: str,
) -> None:
    cfg, target, _, source = packet
    root = target._root() / relative
    data = cfg.as_dict()
    data["sync"]["targets"]["local"]["path"] = str(root)
    unsafe = Config(data, home=cfg.home)
    sink = LocalTarget({"path": str(root)})

    def forbidden(*args: Any, **kwargs: Any) -> dict:
        pytest.fail("unsafe placement reached the owner")

    with pytest.raises(WorktreeStateError, match="disposable"):
        collect_worktree_state(
            unsafe, sink, plan_for(root), source, IDENTITY, ("session-1",), forbidden,
        )
    assert not target._root().exists()


def test_force_removal_and_prune_preserve_nested_collection_bytes_and_modes(packet: Packet) -> None:
    target, live = legacy_collection(packet)
    old = time.time() - 20 * 86400
    for path in (live, *live.rglob("*")):
        os.utime(path, (old, old))
    live.chmod(0o500)
    before, mode = files(live), stat.S_IMODE(live.stat().st_mode)
    assert not sessions.force_rmtree(live)
    assert target.prune("worker", 1) == 0
    assert files(live) == before and stat.S_IMODE(live.stat().st_mode) == mode


def test_verified_session_archive_does_not_authorize_nested_collection_reconciliation(
    packet: Packet,
) -> None:
    target, live = legacy_collection(packet)
    template = live.parents[3] / "template/old"
    template.mkdir(parents=True)
    (template / "events.jsonl").write_bytes(b'{"type":"fixture"}\n')
    ref = sessions.archive_session(template, target._root() / "worker/archived")
    assert sessions.verify_archive(ref)
    before = files(live)
    assert target.reconcile_hub("worker") == 0
    with pytest.raises(OSError, match="worktree-state"):
        target.reconcile_hub("worker", dry_run=True)
    assert files(live) == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_hub_compaction_refuses_typed_state_instead_of_archiving_it_as_a_session(
    packet: Packet, dry_run: bool,
) -> None:
    target, live = legacy_collection(packet)
    before = files(live)
    with pytest.raises(OSError, match="worktree-state"):
        target.compact_backlog("worker", 1, "targz", dry_run=dry_run)
    assert files(live) == before
    assert not list((target._root() / "worker/archived").glob("*.tar.gz"))


def test_recursive_cleanup_checks_preservation_before_deleting_any_member(packet: Packet) -> None:
    _, live = legacy_collection(packet)
    (live / "aaa-ordinary-file").write_bytes(b"must remain when disposal is blocked")
    before = files(live)
    with pytest.raises(OSError, match="worktree-state"):
        filesystem._remove_tree_checked(live)
    assert files(live) == before


def test_filtered_session_replacement_does_not_move_or_erase_nested_collections(packet: Packet) -> None:
    target, live = legacy_collection(packet)
    source = live.parents[3] / "replacement-source"
    session = source / "session-state/old"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_bytes(b'{"type":"replacement"}\n')
    before = files(live)
    result = target.push(source, "worker", {"old"})
    assert not result.ok and "worktree-state" in result.detail
    assert files(live) == before


def test_ordinary_session_transfer_never_copies_typed_namespace_payloads(packet: Packet) -> None:
    target, live = legacy_collection(packet)
    source = live.parents[1]
    receiver = LocalTarget({"path": str(live.parents[3] / "receiver")})
    result = receiver.push(source, "worker")
    assert not result.ok and "worktree-state" in result.detail
    assert not (receiver._root() / "worker/session-state/old/worktree-state").exists()


@pytest.mark.parametrize("redacted", [False, True])
def test_ordinary_transfer_cannot_overwrite_an_existing_collection_member(
    packet: Packet, redacted: bool,
) -> None:
    target, live = legacy_collection(packet)
    victim = next((live / "worktree-state").rglob("manifest.json"))
    before = files(live)
    source = live.parents[3] / "unmarked-source"
    replacement = source / "session-state/old" / victim.relative_to(live)
    replacement.parent.mkdir(parents=True)
    replacement.write_bytes(b"{}")
    if redacted:
        target.protection = ProtectionSettings(redact_secrets=True)
    result = target.push(source, "worker")
    assert not result.ok and "worktree-state" in result.detail
    assert files(live) == before


def test_generic_session_archiver_cannot_publish_inside_a_collection(packet: Packet) -> None:
    _, target, _, source = packet
    collect(packet)
    template = source.parent / "generic-session"
    template.mkdir()
    (template / "events.jsonl").write_bytes(b'{"type":"fixture"}\n')
    destination = target._root() / "worktree-state"
    before = files(destination)
    with pytest.raises(OSError, match="typed-state namespace"):
        sessions.archive_session(template, destination)
    assert files(destination) == before


def test_scan_budget_failure_retains_the_complete_candidate(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target, _, source = packet
    candidate = source.parent / "unproven-disposal"
    candidate.mkdir()
    (candidate / "one").write_bytes(b"one")
    (candidate / "two").write_bytes(b"two")
    before = files(candidate)
    monkeypatch.setattr(preservation, "_MAX_ENTRIES", 1)
    assert not sessions.force_rmtree(candidate)
    assert files(candidate) == before and not target._root().exists()


def test_force_removal_does_not_change_permissions_through_an_external_symlink(packet: Packet) -> None:
    _, _, _, source = packet
    outside = source.parent / "outside-private"
    outside.write_bytes(b"private fixture")
    outside.chmod(0o400)
    candidate = source.parent / "linked-disposal"
    candidate.mkdir()
    (candidate / "reference").symlink_to(outside)
    assert sessions.force_rmtree(candidate)
    assert outside.read_bytes() == b"private fixture"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o400
