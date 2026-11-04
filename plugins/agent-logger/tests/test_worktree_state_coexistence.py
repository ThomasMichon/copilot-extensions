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
from agent_logger import source_roots
from agent_logger.corpus_inventory import inventory_corpus
from agent_logger.source_roots import iter_archive_sources
from agent_logger.sync.meta import write_sync_meta
from agent_logger.sync.targets import filesystem
from agent_logger.sync.targets.filesystem import LocalTarget
from agent_logger.config import Config
from .test_worktree_state import Packet, collect, plan_for, packet as packet

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX collection adapter")


def collection_at(packet: Packet, layout: str) -> tuple[Packet, LocalTarget, Path, str]:
    cfg, target, _, source = packet
    corpus = target._root()
    machine = namespace.DIRECTORY
    if layout != "flat":
        group = corpus / layout
        target = LocalTarget({"path": str(group)})
        cfg._data["sync"]["targets"]["local"]["path"] = str(group)
        packet = cfg, target, plan_for(group), source
        machine = f"{layout}/{machine}"
    return packet, LocalTarget({"path": str(corpus)}), corpus, machine


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


@pytest.mark.parametrize("layout", ["flat", "host.containers"])
def test_retained_revisions_never_enter_the_fleet_directory_budget(
    packet: Packet, monkeypatch: pytest.MonkeyPatch, layout: str,
) -> None:
    packet, viewer, _, _ = collection_at(packet, layout)
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
    fleet = viewer.fleet_sync_status()
    key = "worker" if layout == "flat" else f"{layout}/worker"
    assert fleet.error == "" and set(fleet.machines) == {key}
    assert fleet.machines[key].metadata["status"] == "ok"


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
@pytest.mark.parametrize("layout", ["flat", "host.containers"])
def test_session_push_cannot_enter_an_owned_typed_namespace(
    packet: Packet, machine: str, layout: str,
) -> None:
    packet, ordinary, _, _ = collection_at(packet, layout)
    _, target, _, source = packet
    collect(packet)
    machine = machine if layout == "flat" else f"{layout}/{machine}"
    pushed = ordinary.push(source, machine)
    assert not pushed.ok and "typed-state namespace" in pushed.detail
    assert not (target._root() / namespace.DIRECTORY / "sync-meta.json").exists()


@pytest.mark.parametrize("leg", ["sessions", "logs", "archives"])
@pytest.mark.parametrize("layout", ["flat", "host.containers"])
def test_ordinary_publication_and_collection_claim_share_one_domain_guard(
    packet: Packet, monkeypatch: pytest.MonkeyPatch, leg: str, layout: str,
) -> None:
    packet, ordinary_target, _, machine = collection_at(packet, layout)
    _, target, _, source = packet
    entered, attempted, release = Event(), Event(), Event()
    names = {
        "sessions": ("push", "_push_sessions"),
        "logs": ("push_process_logs", "_push_process_logs"),
        "archives": ("push_archives", "_push_archives"),
    }
    public, private = names[leg]
    real_push, real_lock = getattr(ordinary_target, private), namespace.sync_lock
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
            with real_lock(*args, **{**kwargs, "timeout": 0, "wait": False}) as probe:
                attempted.set()
                assert not probe, "ordinary publication did not hold the namespace's guard"
        with real_lock(*args, **kwargs) as acquired:
            yield acquired

    monkeypatch.setattr(ordinary_target, private, paused_push)
    monkeypatch.setattr(namespace, "sync_lock", observed_lock)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="publisher") as publisher,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="collector") as collector,
    ):
        ordinary = publisher.submit(getattr(ordinary_target, public), writer_source, machine)
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
        assert set(ordinary_target.fleet_sync_status().machines) == {machine}


def test_native_case_distinct_machine_root_remains_visible_beside_typed_state(packet: Packet) -> None:
    _, target, _, source = packet
    collect(packet)
    alternate = "Worktree-State"
    if (target._root() / alternate).exists():
        pytest.skip("fixture filesystem is case-insensitive")
    assert target.push(source, alternate).ok
    assert set(target.fleet_sync_status().machines) == {alternate}
    assert {item.key for item in iter_archive_sources(target._root())} == {alternate}


def test_collection_claim_excludes_a_later_ordinary_publisher(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target, _, source = packet
    entered, attempted, release = Event(), Event(), Event()
    real_claim, real_lock = namespace._claim_namespace, namespace.sync_lock

    def paused_claim(root: Path) -> None:
        entered.set()
        assert release.wait(5)
        real_claim(root)

    @contextmanager
    def observed_lock(*args: Any, **kwargs: Any) -> Iterator[bool]:
        if current_thread().name.startswith("publisher"):
            with real_lock(*args, **{**kwargs, "timeout": 0, "wait": False}) as probe:
                attempted.set()
                assert not probe, "collection claim did not hold the publisher's guard"
        with real_lock(*args, **kwargs) as acquired:
            yield acquired

    monkeypatch.setattr(namespace, "_claim_namespace", paused_claim)
    monkeypatch.setattr(namespace, "sync_lock", observed_lock)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="collector") as collector,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="publisher") as publisher,
    ):
        typed = collector.submit(collect, packet)
        try:
            assert entered.wait(5)
            ordinary = publisher.submit(target.push, source, namespace.DIRECTORY)
            assert attempted.wait(5)
        finally:
            release.set()
        assert typed.result(timeout=5).as_dict()["local_readback_verified"]
        result = ordinary.result(timeout=5)
        assert not result.ok and "typed-state namespace" in result.detail


def test_native_case_alias_is_refused_before_any_session_publication(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target, _, source = packet
    collect(packet)
    real_directory = namespace.publication_directory

    @contextmanager
    def native_case_directory(path: Path, *, create: bool = True) -> Iterator[Any]:
        if path.name == "Worktree-State":
            path = path.with_name(namespace.DIRECTORY)
        with real_directory(path, create=create) as directory:
            yield directory

    monkeypatch.setattr(namespace, "publication_directory", native_case_directory)
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
@pytest.mark.parametrize("layout", ["flat", "host.containers"])
def test_every_auxiliary_mutation_leg_preserves_owned_collections(
    packet: Packet, leg: str, layout: str,
) -> None:
    packet, ordinary, _, machine = collection_at(packet, layout)
    _, target, _, source = packet
    collect(packet)
    typed = target._root() / namespace.DIRECTORY
    before = {item.relative_to(typed): item.read_bytes() for item in typed.rglob("*") if item.is_file()}
    if leg in {"logs", "missing-logs", "archives"}:
        supplied = source / "missing" if leg == "missing-logs" else source
        method = ordinary.push_archives if leg == "archives" else ordinary.push_process_logs
        result = method(supplied, machine)
        assert not result.ok and "typed-state namespace" in result.detail
    elif leg == "heartbeat":
        ordinary.heartbeat(machine)
    else:
        with pytest.raises(OSError, match="typed-state namespace"):
            if leg == "prune":
                ordinary.prune(machine, 1)
            elif leg == "reconcile":
                ordinary.reconcile_hub(machine)
            else:
                ordinary.compact_backlog(machine, 1, "tar.gz")
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


@pytest.mark.parametrize("relative", ["", "nested-root"])
@pytest.mark.parametrize("renamed", [False, True])
def test_targets_and_collection_claims_inside_owned_collections_are_refused(
    packet: Packet, relative: str, renamed: bool,
) -> None:
    cfg, target, _, source = packet
    collect(packet)
    typed = target._root() / namespace.DIRECTORY
    if renamed:
        typed = typed.rename(target._root() / "retained-exports")
    before = {item.relative_to(typed): item.read_bytes() for item in typed.rglob("*") if item.is_file()}
    root = typed / relative
    ordinary = LocalTarget({"path": str(root)})
    result = ordinary.push(source, "worker")
    assert not result.ok and "typed-state namespace" in result.detail
    with pytest.raises(OSError, match="typed-state namespace"):
        ordinary.reconcile_hub("worker", dry_run=True)
    with pytest.raises(OSError, match="typed-state namespace"):
        ordinary.compact_backlog("worker", 1, "tar.gz", dry_run=True)
    data = cfg.as_dict()
    data["sync"]["targets"]["local"]["path"] = str(root)
    nested = Config(data, home=cfg.home)
    with pytest.raises(ValueError, match="overlaps"):
        collect((nested, ordinary, plan_for(root), source))
    assert {item.relative_to(typed): item.read_bytes()
            for item in typed.rglob("*") if item.is_file()} == before
    if relative:
        assert not root.exists()


def test_partial_namespace_markers_do_not_admit_ordinary_mutations(packet: Packet) -> None:
    _, target, _, source = packet
    typed = target._root() / namespace.DIRECTORY
    typed.mkdir(parents=True)
    (typed / ".collection-namespace.json").write_bytes(b"")
    result = target.push(source, namespace.DIRECTORY)
    assert not result.ok and "typed-state namespace" in result.detail
    assert not (typed / "sync-meta.json").exists()


def test_namespace_prefix_budget_refuses_before_creating_destinations(packet: Packet) -> None:
    _, target, _, source = packet
    result = target.push(source, "/".join([namespace.DIRECTORY] * 33))
    assert not result.ok and "too many namespace prefixes" in result.detail
    assert not target._root().exists()


def test_unmarked_ordinary_ancestors_do_not_require_out_of_root_guard_writes(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target, _, source = packet
    root = target._root() / namespace.DIRECTORY / "ordinary-root"
    root.mkdir(parents=True)
    ordinary = LocalTarget({"path": str(root)})

    @contextmanager
    def forbidden_guard(parent: Path) -> Iterator[None]:
        pytest.fail("an existing ordinary ancestor caused an out-of-root guard write")
        yield

    monkeypatch.setattr(namespace, "_domain_lock", forbidden_guard)
    assert ordinary.push(source, "worker").ok
    assert not (target._root() / ".worktree-state-domain.lock").exists()


def test_writable_namespace_named_root_needs_no_write_to_its_read_only_parent(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target, _, source = packet
    parent = target._root()
    root = parent / namespace.DIRECTORY
    root.mkdir(parents=True)
    parent.chmod(0o500)
    real_lock = namespace._domain_lock
    locked = []

    @contextmanager
    def contained_guard(directory: Path) -> Iterator[None]:
        assert directory == root, "publication attempted to write its read-only parent"
        locked.append(directory)
        with real_lock(directory):
            yield

    monkeypatch.setattr(namespace, "_domain_lock", contained_guard)
    try:
        assert LocalTarget({"path": str(root)}).push(source, "worker").ok
        assert locked == [root]
        assert not (parent / ".worktree-state-domain.lock").exists()
    finally:
        parent.chmod(0o700)


def test_renamed_collection_remains_excluded_from_session_and_fleet_discovery(packet: Packet) -> None:
    _, target, _, _ = packet
    collect(packet)
    (target._root() / namespace.DIRECTORY).rename(target._root() / "retained-exports")
    assert target.fleet_sync_status().machines == {}
    assert list(iter_archive_sources(target._root())) == []


@pytest.mark.parametrize("name", ["host.containers", "host.codespaces",
                                ".codespaces", ".codespaces-live"])
def test_renamed_collection_is_excluded_before_provider_group_expansion(
    packet: Packet, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    _, target, _, _ = packet
    collect(packet)
    typed = (target._root() / namespace.DIRECTORY).rename(target._root() / name)
    for number in range(12):
        (typed / f"retained-{number}").mkdir()
    (target._root() / "worker" / "session-state").mkdir(parents=True)
    monkeypatch.setattr(source_roots, "MAX_SOURCE_ENTRIES", 2)
    assert [item.key for item in iter_archive_sources(target._root())] == ["worker"]


@pytest.mark.parametrize("name", ["host.containers", "host.codespaces",
                                ".codespaces", ".codespaces-live"])
@pytest.mark.parametrize("kind", ["empty", "directory", "symlink", "future"])
def test_invalid_provider_group_marker_reports_the_entry_and_keeps_healthy_peers(
    packet: Packet, name: str, kind: str,
) -> None:
    _, target, _, source = packet
    group = target._root() / name
    group.mkdir(parents=True)
    marker = group / ".collection-namespace.json"
    if kind == "directory":
        marker.mkdir()
    elif kind == "symlink":
        marker.symlink_to(source / "manifest.json")
    else:
        marker.write_bytes(b"" if kind == "empty" else b'{"version":2}')
    (target._root() / "worker" / "session-state").mkdir(parents=True)
    errors: list[str] = []
    assert [item.key for item in iter_archive_sources(
        target._root(), on_error=lambda key, error: errors.append(key),
    )] == ["worker"]
    assert errors == [name]
    with pytest.raises(OSError, match="namespace"):
        list(iter_archive_sources(target._root()))
    inventory = inventory_corpus(target._root())
    assert not inventory["complete"]
    rows = {row["source_key"]: row for row in inventory["sources"]}
    assert set(rows) == {name, "worker"}
    assert rows[name]["error"] and rows["worker"]["error"] is None


def test_provider_group_enumeration_budget_still_aborts_with_an_error_callback(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, target, _, _ = packet
    for number in range(3):
        (target._root() / "host.containers" / f"worker-{number}").mkdir(parents=True)
    monkeypatch.setattr(source_roots, "MAX_SOURCE_ENTRIES", 2)
    errors: list[str] = []
    with pytest.raises(source_roots.SourceLayoutError, match="entry budget"):
        list(iter_archive_sources(
            target._root(), on_error=lambda key, error: errors.append(key),
        ))
    assert errors == []


@pytest.mark.parametrize("kind", ["empty", "directory", "symlink", "future"])
def test_invalid_namespace_evidence_is_reported_without_hiding_healthy_peers(
    packet: Packet, kind: str,
) -> None:
    _, target, _, source = packet
    machine = target._root() / "worker"
    machine.mkdir(parents=True)
    write_sync_meta(machine, "worker", "local", "ok", 1)
    typed = target._root() / namespace.DIRECTORY
    typed.mkdir()
    marker = typed / ".collection-namespace.json"
    if kind == "directory":
        marker.mkdir()
    elif kind == "symlink":
        marker.symlink_to(source / "manifest.json")
    else:
        marker.write_bytes(b"" if kind == "empty" else b'{"version":2}')
    fleet = target.fleet_sync_status()
    assert fleet.error == "" and set(fleet.machines) == {"worker", namespace.DIRECTORY}
    assert fleet.machines["worker"].metadata["status"] == "ok"
    assert "namespace" in fleet.machines[namespace.DIRECTORY].error
    errors: list[str] = []
    assert [item.key for item in iter_archive_sources(
        target._root(), on_error=lambda key, error: errors.append(key),
    )] == ["worker"]
    assert errors == [namespace.DIRECTORY]
