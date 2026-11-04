"""Creation-time privacy and coexistence with ordinary fleet health."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import stat
from threading import Event, current_thread
from collections.abc import Iterator
from typing import Any

import pytest

from agent_logger.sync import worktree_state_namespace as namespace
from agent_logger.source_roots import iter_archive_sources
from agent_logger.sync.meta import write_sync_meta
from agent_logger.sync.targets import filesystem
from .test_worktree_state import Packet, collect, plan_for, packet as packet

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX collection adapter")


def test_every_publication_file_is_private_at_the_actual_creation_syscall(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = os.open
    created: set[str] = set()

    def open_private(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if flags & os.O_CREAT:
            assert mode == 0o600
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if flags & os.O_CREAT:
            assert stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077 == 0
            created.add(str(path))
        return descriptor

    monkeypatch.setattr(os, "open", open_private)
    collect(packet)
    assert {"agenda.sqlite3", "manifest.json", "receipt.json", "collection.json"} <= created


def test_retained_revisions_never_enter_the_fleet_directory_budget(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target, _, _ = packet
    collect(packet)
    machine = target._root() / "worker"
    machine.mkdir()
    write_sync_meta(machine, "worker", "local", "ok", 1)
    typed = target._root() / namespace.DIRECTORY
    for number in range(12):
        (typed / "retained" / str(number) / "export").mkdir(parents=True)
    monkeypatch.setattr(filesystem, "_MAX_FLEET_DIRECTORIES", 8)
    real_scandir = os.scandir

    def no_typed_traversal(path: Any) -> Any:
        if not isinstance(path, int):
            assert Path(path) != typed
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", no_typed_traversal)
    fleet = target.fleet_sync_status()
    assert fleet.error == "" and set(fleet.machines) == {"worker"}
    assert fleet.machines["worker"].metadata["status"] == "ok"


def test_existing_machine_with_the_same_name_is_not_hidden_or_adopted(packet: Packet) -> None:
    _, target, _, _ = packet
    machine = target._root() / namespace.DIRECTORY
    machine.mkdir(parents=True)
    write_sync_meta(machine, namespace.DIRECTORY, "local", "ok", 1)
    before = (machine / "sync-meta.json").read_bytes()
    assert set(target.fleet_sync_status().machines) == {namespace.DIRECTORY}
    assert {item.key for item in iter_archive_sources(target._root())} == {namespace.DIRECTORY}
    with pytest.raises(ValueError, match="unowned"):
        collect(packet)
    assert (machine / "sync-meta.json").read_bytes() == before
    assert not namespace.has_namespace(target._root())


@pytest.mark.parametrize("machine", ["worktree-state", "worktree-state/nested"])
def test_session_push_cannot_enter_an_owned_typed_namespace(packet: Packet, machine: str) -> None:
    _, target, _, source = packet
    collect(packet)
    pushed = target.push(source, machine)
    assert not pushed.ok and "typed-state namespace" in pushed.detail
    assert not (target._root() / namespace.DIRECTORY / "sync-meta.json").exists()


@pytest.mark.parametrize("leg", ["sessions", "logs", "archives"])
def test_ordinary_publication_and_collection_claim_share_one_domain_guard(
    packet: Packet, monkeypatch: pytest.MonkeyPatch, leg: str,
) -> None:
    _, target, _, source = packet
    entered, attempted, release = Event(), Event(), Event()
    names = {
        "sessions": ("push", "_push_sessions"),
        "logs": ("push_process_logs", "_push_process_logs"),
        "archives": ("push_archives", "_push_archives"),
    }
    public, private = names[leg]
    real_push, real_lock = getattr(target, private), namespace.sync_lock
    writer_source = source
    if leg == "logs":
        writer_source = source.parent / "producer-logs"
        writer_source.mkdir(mode=0o700)
        (writer_source / "process-101-1.log").write_bytes(b"synthetic producer log\n")

    def paused_push(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(5)
        return real_push(*args, **kwargs)

    @contextmanager
    def observed_lock(*args: Any, **kwargs: Any) -> Iterator[bool]:
        if current_thread().name.startswith("collector"):
            attempted.set()
        with real_lock(*args, **kwargs) as acquired:
            yield acquired

    monkeypatch.setattr(target, private, paused_push)
    monkeypatch.setattr(namespace, "sync_lock", observed_lock)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="publisher") as publisher,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="collector") as collector,
    ):
        ordinary = publisher.submit(getattr(target, public), writer_source, namespace.DIRECTORY)
        try:
            assert entered.wait(5)
            typed = collector.submit(collect, packet)
            assert attempted.wait(5)
        finally:
            release.set()
        result = ordinary.result(timeout=5)
        assert result.ok
        if leg == "logs":
            assert result.file_count == 1
        with pytest.raises(ValueError, match="unowned"):
            typed.result(timeout=5)
    assert not namespace.has_namespace(target._root())
    if leg == "logs":
        assert (target._root() / namespace.DIRECTORY / "logs/process-101-1.log").read_bytes() == (
            b"synthetic producer log\n"
        )
    else:
        assert set(target.fleet_sync_status().machines) == {namespace.DIRECTORY}


def test_native_case_distinct_machine_root_remains_visible_beside_typed_state(packet: Packet) -> None:
    _, target, _, source = packet
    collect(packet)
    alternate = "Worktree-State"
    if (target._root() / alternate).exists():
        pytest.skip("fixture filesystem is case-insensitive")
    assert target.push(source, alternate).ok
    assert set(target.fleet_sync_status().machines) == {alternate}
    assert {item.key for item in iter_archive_sources(target._root())} == {alternate}


def test_native_case_alias_is_refused_before_any_session_publication(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target, _, source = packet
    collect(packet)
    real_samefile = os.path.samefile

    def case_alias(first: Any, second: Any) -> bool:
        if Path(first).name == "Worktree-State" and Path(second).name == namespace.DIRECTORY:
            return True
        return real_samefile(first, second)

    monkeypatch.setattr(os.path, "samefile", case_alias)
    result = target.push(source, "Worktree-State")
    assert not result.ok and "typed-state namespace" in result.detail
    assert not (target._root() / "Worktree-State").exists()


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_non_directory_namespace_entry_cannot_discard_healthy_fleet_results(
    packet: Packet, kind: str,
) -> None:
    _, target, _, source = packet
    machine = target._root() / "worker"
    machine.mkdir(parents=True)
    write_sync_meta(machine, "worker", "local", "ok", 1)
    entry = target._root() / namespace.DIRECTORY
    if kind == "file":
        entry.write_bytes(b"not a collection directory")
    else:
        entry.symlink_to(source, target_is_directory=True)
    fleet = target.fleet_sync_status()
    assert fleet.error == "" and set(fleet.machines) == {"worker"}


@pytest.mark.parametrize("leg", ["logs", "missing-logs", "archives", "heartbeat",
                               "prune", "reconcile", "compact"])
def test_every_auxiliary_mutation_leg_preserves_owned_collections(packet: Packet, leg: str) -> None:
    _, target, _, source = packet
    collect(packet)
    typed = target._root() / namespace.DIRECTORY
    before = {item.relative_to(typed): item.read_bytes() for item in typed.rglob("*") if item.is_file()}
    if leg in {"logs", "missing-logs", "archives"}:
        supplied = source / "missing" if leg == "missing-logs" else source
        method = target.push_archives if leg == "archives" else target.push_process_logs
        result = method(supplied, namespace.DIRECTORY)
        assert not result.ok and "typed-state namespace" in result.detail
    elif leg == "heartbeat":
        target.heartbeat(namespace.DIRECTORY)
    else:
        with pytest.raises(OSError, match="typed-state namespace"):
            if leg == "prune":
                target.prune(namespace.DIRECTORY, 1)
            elif leg == "reconcile":
                target.reconcile_hub(namespace.DIRECTORY)
            else:
                target.compact_backlog(namespace.DIRECTORY, 1, "tar.gz")
    assert {item.relative_to(typed): item.read_bytes()
            for item in typed.rglob("*") if item.is_file()} == before


def test_read_only_maintenance_does_not_allocate_a_missing_destination(packet: Packet) -> None:
    _, target, _, _ = packet
    assert target.reconcile_hub(namespace.DIRECTORY, dry_run=True) == 0
    assert target.compact_backlog(namespace.DIRECTORY, 1, "tar.gz", dry_run=True) == 0
    assert not target._root().exists()


def test_compaction_preview_does_not_create_archive_or_guard_files(packet: Packet) -> None:
    _, target, _, _ = packet
    state = target._root() / namespace.DIRECTORY / "session-state"
    state.mkdir(parents=True)
    before = set(target._root().rglob("*"))
    assert target.compact_backlog(namespace.DIRECTORY, 1, "tar.gz", dry_run=True) == 0
    assert set(target._root().rglob("*")) == before


@pytest.mark.parametrize("layout", ["flat", "host.containers"])
def test_typed_collections_are_not_classified_as_empty_session_archive_sources(
    packet: Packet, layout: str,
) -> None:
    cfg, target, _, source = packet
    corpus = target._root()
    if layout != "flat":
        group = corpus / layout
        target.options["path"] = str(group)
        cfg._data["sync"]["targets"]["local"]["path"] = str(group)
        packet = cfg, target, plan_for(group), source
    collect(packet)
    machine = target._root() / "worker"
    (machine / "session-state").mkdir(parents=True)
    expected = "worker" if layout == "flat" else f"{layout}/worker"
    assert [item.key for item in iter_archive_sources(corpus)] == [expected]
