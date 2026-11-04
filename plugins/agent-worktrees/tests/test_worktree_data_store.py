"""Synthetic new-home admission and checkout-independent passive discovery."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import project_state, registry_paths, tracking
from agent_worktrees import worktree_data_store as store
from agent_worktrees.worktree_data_contract import (
    MAX_DESCRIPTOR_BYTES,
    MAX_PLACEMENTS,
    WorktreeDataError,
)

PROJECT = "sample-project"
WORKTREE = "machine-20300101-123456-abcd"
NONCE = "0123456789abcdef"
STORE = "a" * 32


def _record(root: Path, *, path_worktree_id: str = WORKTREE, **changes: Any) -> Path:
    directory = root / "worktrees"
    directory.mkdir(parents=True, exist_ok=True)
    value = {
        "worktree_id": WORKTREE,
        "creation_nonce": NONCE,
        "repo": PROJECT,
        "branch": "worktree/sample",
        "worktree_path": "/absent-checkout",
        "machine": "sample-machine",
        "platform": "linux",
    }
    value.update(changes)
    path = directory / f"{path_worktree_id}.yaml"
    # JSON is a YAML subset accepted by the canonical record owner.
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    monkeypatch.setattr(
        project_state,
        "_normalized_repository_remote",
        lambda repository: f"network:example.invalid/{repository}",
    )
    root = cfg.project_dir(PROJECT)
    _record(root)
    return root


def _allocate(**changes: Any) -> store.WorktreeDataPlacement:
    arguments = dict(
        project=PROJECT,
        worktree_id=WORKTREE,
        creation_nonce=NONCE,
        domain="agendas",
        lane="main",
        store_id=STORE,
    )
    arguments.update(changes)
    return store.allocate_worktree_data(**arguments)


def _descriptor(root: Path) -> Path:
    return root / "worktree-state" / WORKTREE / "descriptor.json"


def _change(root: Path, change: Any) -> None:
    path = _descriptor(root)
    value = json.loads(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def _snapshot(root: Path) -> dict[str, tuple[bytes | None, int, int]]:
    result = {}
    for path in root.rglob("*"):
        metadata = path.stat()
        result[str(path.relative_to(root))] = (
            path.read_bytes() if path.is_file() else None,
            metadata.st_mtime_ns,
            metadata.st_ino,
        )
    return result


def test_allocate_retry_and_store_only_discovery(owner: Path) -> None:
    placement = _allocate()
    before = _snapshot(owner)
    assert _allocate() == placement
    result = store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert result is not None
    assert result.placements == (placement,)
    assert result.canonical_record == "matching"
    assert not result.live_authority
    assert placement.state_home_allocated
    assert not placement.store_content_validated
    assert not placement.live_authority
    assert list(Path(placement.state_home).iterdir()) == []
    assert _snapshot(owner) == before
    assert json.loads(json.dumps(result.to_dict()))["placements"][0]["state_home"]
    assert asdict(result)["scope"]["repository"] == PROJECT
    with pytest.raises(FrozenInstanceError):
        placement.lane = "other"  # type: ignore[misc]


def test_inventory_and_store_selection_do_not_require_lane(owner: Path) -> None:
    first = _allocate()
    second = _allocate(lane="other", store_id="b" * 32)
    result = store.read_worktree_data(PROJECT, WORKTREE)
    assert result is not None and result.placements == (first, second)
    selected = store.read_worktree_data(PROJECT, WORKTREE, store_id="b" * 32)
    assert selected is not None and selected.placements == (second,)
    assert store.read_worktree_data(PROJECT, WORKTREE, store_id="c" * 32) is None


def test_project_inventory_includes_all_stores_and_missing_canonical_records(
    owner: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees.worktree_data_inventory import read_project_worktree_data

    first = _allocate()
    second = _allocate(lane="other", store_id="b" * 32)
    other = "retired-worktree"
    _record(
        owner, path_worktree_id=other, worktree_id=other, creation_nonce="other-incarnation",
    )
    third = _allocate(
        worktree_id=other, creation_nonce="other-incarnation", store_id="c" * 32,
    )
    (owner / "worktrees" / f"{other}.yaml").unlink()
    before = _snapshot(owner)
    monkeypatch.setattr(tracking, "_RecordLock", lambda *_a, **_kw: pytest.fail("read locked"))
    monkeypatch.setattr(
        project_state, "ensure_project_state", lambda *_a: pytest.fail("read initialized"),
    )
    result = read_project_worktree_data(PROJECT)
    assert result.repository_identity == f"network:example.invalid/{PROJECT}"
    assert result.scope == first.scope
    assert [entry.worktree_id for entry in result.worktrees] == [WORKTREE, other]
    assert result.worktrees[0].placements == (first, second)
    assert result.worktrees[1].placements == (third,)
    assert result.worktrees[1].canonical_record == "missing"
    assert not result.live_authority
    assert _snapshot(owner) == before


def test_empty_project_inventory_is_passive_and_owner_qualified(
    owner: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees.worktree_data_inventory import read_project_worktree_data

    before = _snapshot(owner)
    result = read_project_worktree_data(PROJECT)
    assert result.worktrees == () and result.scope.project == PROJECT
    absent = read_project_worktree_data("absent-project")
    assert absent.worktrees == () and absent.scope is None
    assert absent.repository_identity == "network:example.invalid/absent-project"
    assert not cfg.project_dir("absent-project").exists()
    assert _snapshot(owner) == before
    monkeypatch.setattr(
        project_state, "_normalized_repository_remote",
        lambda *_a: (_ for _ in ()).throw(ValueError("unregistered")),
    )
    with pytest.raises(WorktreeDataError) as failed:
        read_project_worktree_data("unregistered")
    assert failed.value.code == "owner-unavailable"


@pytest.mark.parametrize("damage", ["partial", "alias", "incarnation", "foreign", "bad-name"])
def test_project_inventory_refuses_incomplete_or_foreign_members(
    owner: Path, damage: str,
) -> None:
    from agent_worktrees.worktree_data_inventory import read_project_worktree_data

    _allocate()
    base = owner / "worktree-state"
    if damage == "partial":
        (base / "interrupted").mkdir()
    elif damage == "alias":
        if os.name == "nt":
            pytest.skip("native reparse fixture requires separate privilege qualification")
        (base / "alias").symlink_to(base / WORKTREE, target_is_directory=True)
    elif damage == "incarnation":
        _record(owner, creation_nonce="new-incarnation")
    elif damage == "foreign":
        _change(owner, lambda document: document["scope"].update(repository="foreign"))
    else:
        (base / "not portable").mkdir()
    with pytest.raises(WorktreeDataError):
        read_project_worktree_data(PROJECT)


@pytest.mark.parametrize("budget", ["count", "bytes"])
def test_project_inventory_refuses_overflow_instead_of_truncating(
    owner: Path, monkeypatch: pytest.MonkeyPatch, budget: str,
) -> None:
    from agent_worktrees import worktree_data_inventory as inventory

    _allocate()
    monkeypatch.setattr(
        inventory, "MAX_PROJECT_WORKTREES" if budget == "count" else "MAX_INVENTORY_BYTES", 0,
    )
    with pytest.raises(WorktreeDataError) as failed:
        inventory.read_project_worktree_data(PROJECT)
    assert failed.value.code == "inventory-limit"


def test_project_inventory_enumerates_only_registration_directories(
    owner: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import worktree_data_inventory as inventory

    worktrees = [WORKTREE, "second", "third"]
    for index, worktree in enumerate(worktrees):
        _record(owner, path_worktree_id=worktree, worktree_id=worktree)
        _allocate(worktree_id=worktree, store_id=f"{index:032x}")
    scans = []
    real_scan = os.scandir

    def counted(path):
        scans.append(Path(path))
        return real_scan(path)

    monkeypatch.setattr(os, "scandir", counted)
    assert len(inventory.read_project_worktree_data(PROJECT).worktrees) == 3
    base = owner / "worktree-state"
    assert sorted(scans) == sorted([
        base, *(base / worktree for worktree in worktrees),
        *(base / worktree / "agendas" for worktree in worktrees),
    ])


@pytest.mark.parametrize("change", ["descriptor", "membership", "canonical", "remote", "base"])
def test_project_inventory_rejects_changes_during_owner_read(
    owner: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_worktrees import worktree_data_inventory as inventory

    _allocate()
    real_read = store.read_worktree_data

    def changing_read(*args, **kwargs):
        result = real_read(*args, **kwargs)
        if change == "descriptor":
            _change(owner, lambda document: document.update(creation_nonce="changed"))
        elif change == "membership":
            (owner / "worktree-state" / "new-registration").mkdir()
        elif change == "canonical":
            _record(owner, creation_nonce="changed")
        elif change == "remote":
            monkeypatch.setattr(
                project_state, "_normalized_repository_remote",
                lambda *_a: "network:example.invalid/changed",
            )
        else:
            base = owner / "worktree-state"
            base.rename(owner / "retained-original")
            base.mkdir()
        return result

    monkeypatch.setattr(store, "read_worktree_data", changing_read)
    with pytest.raises(WorktreeDataError) as failed:
        inventory.read_project_worktree_data(PROJECT)
    assert failed.value.code == "identity-conflict"


def test_retired_checkout_and_missing_canonical_record_retain_discovery(owner: Path) -> None:
    placement = _allocate()
    _record(owner, status="archived", reaped_at="2030-01-02T00:00:00")
    assert store.read_worktree_data(PROJECT, WORKTREE).canonical_record == "matching"
    (owner / "worktrees" / f"{WORKTREE}.yaml").unlink()
    before = _snapshot(owner)
    result = store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE, creation_nonce=NONCE)
    assert result is not None and result.placements == (placement,)
    assert result.canonical_record == "missing" and not result.live_authority
    assert _snapshot(owner) == before
    with pytest.raises(WorktreeDataError, match="canonical tracking"):
        _allocate()


def test_absent_reads_do_not_create_directories_or_record_locks(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _snapshot(owner)
    monkeypatch.setattr(tracking, "_RecordLock", lambda *_a, **_kw: pytest.fail("read locked"))
    monkeypatch.setattr(tracking, "_atomic_write", lambda *_a, **_kw: pytest.fail("read wrote"))
    monkeypatch.setattr(
        project_state,
        "ensure_project_state",
        lambda *_a: pytest.fail("read initialized"),
    )
    assert store.read_worktree_data(PROJECT, WORKTREE) is None
    assert store.read_worktree_data("absent-project", WORKTREE) is None
    assert not cfg.project_dir("absent-project").exists()
    assert _snapshot(owner) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"creation_nonce": "new-incarnation"},
        {"worktree_id": "abcd"},
        {"domain": "credentials"},
        {"lane": "../escape"},
        {"lane": "MixedCase"},
        {"lane": "con"},
        {"store_id": "A" * 32},
        {"store_id": "a" * 31},
        {"store_id": "../other"},
    ],
)
def test_invalid_or_noncanonical_allocation_is_effect_free(owner: Path, changes: dict) -> None:
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError):
        _allocate(**changes)
    # Identity disagreement is decided under a lock, whose owner may create its sidecar.
    after = _snapshot(owner)
    assert {
        key: (value[0], value[2]) for key, value in after.items() if not key.endswith(".lock")
    } == {key: (value[0], value[2]) for key, value in before.items()}
    assert not (owner / "worktree-state").exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"store_id": "b" * 32},
        {"lane": "other"},
        {"creation_nonce": "other-nonce"},
    ],
)
def test_allocation_conflicts_never_make_another_copy(owner: Path, changes: dict) -> None:
    _allocate()
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate(**changes)
    assert failure.value.code == "identity-conflict"
    assert _snapshot(owner) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"creation_nonce": "new-incarnation"},
        {"worktree_id": "suffix-only"},
        {"repo": "other-project"},
    ],
)
def test_current_record_mismatch_refuses_retained_descriptor(
    owner: Path,
    changes: dict,
) -> None:
    _allocate()
    _record(owner, **changes)
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert failure.value.code == "identity-conflict"
    assert _snapshot(owner) == before


def test_initial_allocation_refuses_another_registered_repository(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _record(owner, repo="other-project")
    looked_up = []
    monkeypatch.setattr(
        project_state,
        "_normalized_repository_remote",
        lambda repository: looked_up.append(repository) or f"network:example.invalid/{repository}",
    )
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate()
    assert failure.value.code == "identity-conflict"
    assert not looked_up
    assert _snapshot(owner) == before


def test_retained_mixed_project_repository_scope_refuses_without_canonical_record(
    owner: Path,
) -> None:
    import hashlib

    _allocate()
    repository_id = (
        "repo-"
        + hashlib.sha256(
            b"network:example.invalid/other-project",
        ).hexdigest()[:24]
    )
    _change(
        owner,
        lambda document: document["scope"].update(
            repository="other-project",
            repository_id=repository_id,
        ),
    )
    (owner / "worktrees" / f"{WORKTREE}.yaml").unlink()
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert failure.value.code == "identity-conflict"
    assert _snapshot(owner) == before


@pytest.mark.skipif(os.name == "nt", reason="directory durability backend is POSIX-only")
def test_allocation_flushes_new_directory_links_and_descriptor_before_success(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []
    real_sync = os.fsync
    real_replace = tracking._replace_with_retry

    def synced(fd: int) -> None:
        metadata = os.fstat(fd)
        kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
        real_sync(fd)
        events.append((kind, metadata.st_dev, metadata.st_ino))

    def published(source: str, target: str) -> None:
        metadata = Path(source).stat()
        assert ("file", metadata.st_dev, metadata.st_ino) in events
        assert json.loads(Path(source).read_bytes())["worktree_id"] == WORKTREE
        events.append(("replace",))
        real_replace(source, target)

    monkeypatch.setattr(os, "fsync", synced)
    monkeypatch.setattr(tracking, "_replace_with_retry", published)
    _allocate()
    publication = events.index(("replace",))
    directories = [
        owner / "worktree-state",
        owner / "worktree-state" / WORKTREE,
        owner / "worktree-state" / WORKTREE / "agendas",
        owner / "worktree-state" / WORKTREE / "agendas" / "main",
    ]
    for path in directories:
        child, parent = path.stat(), path.parent.stat()
        child_event = ("directory", child.st_dev, child.st_ino)
        parent_event = ("directory", parent.st_dev, parent.st_ino)
        child_index = events.index(child_event)
        assert parent_event in events[child_index + 1 : publication]
    parent = _descriptor(owner).parent.stat()
    assert ("directory", parent.st_dev, parent.st_ino) in events[publication + 1 :]


@pytest.mark.skipif(os.name == "nt", reason="directory durability backend is POSIX-only")
def test_descriptor_content_flush_failure_preserves_prior_registration(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _allocate()
    original = _descriptor(owner).read_bytes()
    real_sync = os.fsync

    def rejected(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("synthetic descriptor durability failure")
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", rejected)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate(lane="other", store_id="b" * 32)
    assert failure.value.code == "allocation-failed"
    assert _descriptor(owner).read_bytes() == original
    assert Path(first.state_home).is_dir()
    assert not list(_descriptor(owner).parent.glob("*.tmp"))
    with pytest.raises(WorktreeDataError) as refused:
        store.read_worktree_data(PROJECT, WORKTREE)
    assert refused.value.code == "partial-registration"


@pytest.mark.skipif(os.name == "nt", reason="directory durability backend is POSIX-only")
def test_publication_directory_flush_failure_cannot_succeed_on_unflushed_retry(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replaced = []
    real_replace, real_sync = tracking._replace_with_retry, os.fsync
    successful_syncs = []
    reject = [True]

    def published(source: str, target: str) -> None:
        real_replace(source, target)
        replaced.append(target)

    def synced(fd: int) -> None:
        metadata = os.fstat(fd)
        if replaced and stat.S_ISDIR(metadata.st_mode):
            if reject[0]:
                raise OSError("synthetic publication directory durability failure")
            successful_syncs.append("directory")
        elif stat.S_ISREG(metadata.st_mode):
            successful_syncs.append("file")
        real_sync(fd)

    monkeypatch.setattr(tracking, "_replace_with_retry", published)
    monkeypatch.setattr(os, "fsync", synced)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate()
    assert failure.value.code == "allocation-failed"
    original = _descriptor(owner).read_bytes()
    with pytest.raises(WorktreeDataError):
        _allocate()
    reject[0] = False
    successful_syncs.clear()
    placement = _allocate()
    assert Path(placement.state_home).is_dir()
    assert _descriptor(owner).read_bytes() == original
    assert "file" in successful_syncs
    assert successful_syncs[-1] == "directory"
    assert all(
        value == "directory" for value in successful_syncs[successful_syncs.index("file") + 1 :]
    )
    assert len(replaced) == 1


def test_unavailable_directory_durability_refuses_allocation_without_effects(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported(_path: Path) -> None:
        raise OSError("synthetic filesystem without a directory durability barrier")

    monkeypatch.setattr(store, "_sync_directory", unsupported)
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError) as failure:
        store.allocate_worktree_data(
            PROJECT,
            WORKTREE,
            creation_nonce=NONCE,
            domain="agendas",
            lane="main",
            store_id=STORE,
        )
    assert failure.value.code == "durability-unavailable"
    assert _snapshot(owner) == before
    assert store.read_worktree_data(PROJECT, WORKTREE) is None


@pytest.mark.parametrize("failure", [None, "open", "flush"])
def test_windows_directory_barrier_uses_owned_write_handle_and_closes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    import ctypes
    from ctypes import wintypes

    calls = []

    class Function:
        def __init__(self, name: str, result: int) -> None:
            self.name, self.result = name, result

        def __call__(self, *arguments: Any) -> int:
            calls.append((self.name, arguments))
            return self.result

    kernel = SimpleNamespace(
        CreateFileW=Function("open", wintypes.HANDLE(-1).value if failure == "open" else 7),
        FlushFileBuffers=Function("flush", 0 if failure == "flush" else 1),
        CloseHandle=Function("close", 1),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        ctypes,
        "WinError",
        lambda code: OSError(code, "synthetic native flush failure"),
        raising=False,
    )
    if failure:
        with pytest.raises(OSError, match="native flush failure"):
            store._windows_flush_directory(tmp_path)
    else:
        store._windows_flush_directory(tmp_path)
    assert calls[0] == ("open", (str(tmp_path), 0x40000000, 0x7, None, 3, 0x02000000, None))
    assert calls[1:] == ([] if failure == "open" else [("flush", (7,)), ("close", (7,))])


@pytest.mark.skipif(os.name == "nt", reason="POSIX-backed Windows adapter simulation")
@pytest.mark.parametrize("failure", [None, "replace", "directory-after-replace"])
def test_windows_publication_adapter_preserves_durability_and_retry_fences(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    from zdd import routing_persistence

    events = []
    replaced = []
    fail = [failure]
    real_sync, real_open = os.fsync, os.open
    retry_open_flags = []

    def synced(fd: int) -> None:
        metadata = os.fstat(fd)
        real_sync(fd)
        events.append(
            (
                "directory" if stat.S_ISDIR(metadata.st_mode) else "file",
                metadata.st_dev,
                metadata.st_ino,
            )
        )

    def flushed(path: Path) -> None:
        if replaced and fail[0] == "directory-after-replace":
            raise OSError("synthetic native post-publication barrier failure")
        fd = real_open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def published(source: Path, target: Path) -> None:
        metadata = source.stat()
        assert ("file", metadata.st_dev, metadata.st_ino) in events
        if fail[0] == "replace":
            raise OSError("synthetic native write-through failure")
        os.replace(source, target)
        replaced.append(target)

    def opened(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if Path(path) == _descriptor(owner):
            retry_open_flags.append(flags)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(store, "_WINDOWS", True)
    monkeypatch.setattr(store, "_windows_flush_directory", flushed)
    monkeypatch.setattr(routing_persistence, "windows_replace", published)
    monkeypatch.setattr(os, "fsync", synced)
    monkeypatch.setattr(os, "open", opened)
    if failure:
        with pytest.raises(WorktreeDataError) as refused:
            _allocate()
        assert refused.value.code == "allocation-failed"
        assert (_descriptor(owner).parent / "agendas" / "main").is_dir()
        if failure == "replace":
            assert not _descriptor(owner).exists()
            return
        with pytest.raises(WorktreeDataError):
            _allocate()
    else:
        _allocate()
    fail[0] = None
    events.clear()
    retry_open_flags.clear()
    result = _allocate()
    assert result.store_id == STORE and len(replaced) == 1
    assert any(flags & os.O_RDWR for flags in retry_open_flags)
    assert "file" in [event[0] for event in events]
    assert events[-1][0] == "directory"
    assert store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE) is not None


@pytest.mark.skipif(os.name != "nt", reason="requires actual native Windows directory flush")
def test_native_windows_allocation_and_retry_use_real_durability_barriers(owner: Path) -> None:
    first = _allocate()
    assert _allocate() == first
    selected = store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert selected is not None and selected.placements == (first,)


def test_final_project_root_barrier_failure_never_acknowledges_publication(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_sync = store._sync_directory
    fail = [True]
    flushed = []

    def synced(path: Path) -> None:
        if path == owner and _descriptor(owner).exists() and fail[0]:
            raise OSError("synthetic final project-root barrier failure")
        real_sync(path)
        flushed.append(path)

    monkeypatch.setattr(store, "_sync_directory", synced)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate()
    assert failure.value.code == "allocation-failed"
    original = _descriptor(owner).read_bytes()
    with pytest.raises(WorktreeDataError):
        _allocate()
    fail[0] = False
    flushed.clear()
    placement = _allocate()
    assert placement.store_id == STORE
    assert _descriptor(owner).read_bytes() == original
    assert flushed[-1] == owner


@pytest.mark.parametrize("raw", ["{", "[]", '{"worktree_id": 1}', "repo: []\nbranch: x\n"])
def test_malformed_canonical_record_is_not_absent(owner: Path, raw: str) -> None:
    (owner / "worktrees" / f"{WORKTREE}.yaml").write_text(raw, encoding="utf-8")
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError):
        store.read_worktree_data(PROJECT, WORKTREE)
    assert _snapshot(owner) == before


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("schema", "old-schema", "unsupported-schema"),
        ("version", 0, "unsupported-version"),
        ("version", 999, "unsupported-version"),
        ("version", True, "invalid-descriptor"),
        ("worktree_id", "another-worktree", "identity-conflict"),
        ("creation_nonce", "another-incarnation", "identity-conflict"),
        ("extra", None, "invalid-descriptor"),
    ],
)
def test_descriptor_schema_and_incarnation_refusals(
    owner: Path,
    field: str,
    value: Any,
    code: str,
) -> None:
    _allocate()
    _change(owner, lambda document: document.update({field: value}))
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE)
    assert failure.value.code == code
    assert _snapshot(owner) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project", "other-project"),
        ("repository", "other-repository"),
        ("repository_id", "repo-other"),
        ("marketplace_id", "other-marketplace"),
        ("project_root_device", 999999),
        ("project_root_inode", 999999),
    ],
)
def test_wrong_project_repository_or_cell_refuses(owner: Path, field: str, value: Any) -> None:
    _allocate()
    _change(owner, lambda document: document["scope"].update({field: value}))
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE)
    assert failure.value.code == "identity-conflict"


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1"])
def test_invalid_persisted_generation_refuses(owner: Path, value: Any) -> None:
    _allocate()
    _change(
        owner,
        lambda document: document["placements"][0].update(
            placement_generation=value,
        ),
    )
    with pytest.raises(WorktreeDataError):
        store.read_worktree_data(PROJECT, WORKTREE)


def test_requested_generation_and_incarnation_are_fences(owner: Path) -> None:
    _allocate()
    result = store.read_worktree_data(
        PROJECT,
        WORKTREE,
        creation_nonce=NONCE,
        store_id=STORE,
        placement_generation=1,
    )
    assert result is not None
    for arguments in (
        {"placement_generation": 2},
        {"placement_generation": True},
        {"creation_nonce": "other-nonce"},
    ):
        with pytest.raises(WorktreeDataError):
            store.read_worktree_data(PROJECT, WORKTREE, **arguments)


@pytest.mark.parametrize("location", ["worktree-state", WORKTREE, "agendas", "main", "descriptor"])
def test_child_symlink_alias_refuses(
    owner: Path,
    tmp_path: Path,
    location: str,
) -> None:
    placement = _allocate()
    directory = _descriptor(owner).parent
    path = {
        "worktree-state": owner / "worktree-state",
        WORKTREE: directory,
        "agendas": directory / "agendas",
        "main": Path(placement.state_home),
        "descriptor": _descriptor(owner),
    }[location]
    target = tmp_path / "moved-member"
    path.rename(target)
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE)
    assert failure.value.code == "unsafe-path"
    with pytest.raises(WorktreeDataError):
        _allocate()


def test_configured_project_root_alias_is_accepted(
    owner: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    placement = _allocate()
    descriptor = _descriptor(owner).read_bytes()
    alias = tmp_path / "configured-owner-alias"
    try:
        alias.symlink_to(owner, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    monkeypatch.setattr(cfg, "project_dir", lambda _project: alias)
    selected = store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert selected is not None
    assert os.path.samefile(selected.placements[0].state_home, placement.state_home)
    assert os.path.samefile(_allocate().state_home, placement.state_home)
    assert selected.placements == (placement,)
    assert _allocate() == placement
    assert _descriptor(owner).read_bytes() == descriptor


def test_initial_aliased_owner_returns_canonical_home_with_domain_content_and_retained_reads(
    owner: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias = tmp_path / "configured-owner-alias"
    try:
        alias.symlink_to(owner, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    monkeypatch.setattr(cfg, "project_dir", lambda _project: alias)
    placement = _allocate()
    expected = owner.resolve(strict=True) / "worktree-state" / WORKTREE / "agendas" / "main"
    assert placement.state_home == str(expected)
    domain_member = expected / "agenda.sqlite3"
    domain_member.write_bytes(b"opaque domain-owned contents")
    descriptor = _descriptor(owner).read_bytes()
    assert _allocate() == placement
    selected = store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert selected is not None and selected.placements == (placement,)
    (owner / "worktrees" / f"{WORKTREE}.yaml").unlink()
    retained = store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert retained is not None and retained.canonical_record == "missing"
    assert retained.placements == (placement,)
    assert _descriptor(owner).read_bytes() == descriptor
    assert domain_member.read_bytes() == b"opaque domain-owned contents"
    assert not retained.placements[0].store_content_validated


def test_admitted_root_alias_does_not_admit_a_domain_child_alias(
    owner: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    placement = _allocate()
    alias = tmp_path / "configured-owner-alias"
    home = Path(placement.state_home)
    foreign = tmp_path / "foreign-domain-home"
    try:
        alias.symlink_to(owner, target_is_directory=True)
        home.rename(foreign)
        home.symlink_to(foreign, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    monkeypatch.setattr(cfg, "project_dir", lambda _project: alias)
    for operation in (
        lambda: store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE),
        _allocate,
    ):
        with pytest.raises(WorktreeDataError) as failure:
            operation()
        assert failure.value.code == "unsafe-path"


@pytest.mark.parametrize(
    "relative", ["../outside", "/absolute", "agendas/main/../main", "agendas/other"]
)
def test_descriptor_paths_are_not_caller_controlled(owner: Path, relative: str) -> None:
    _allocate()
    _change(owner, lambda document: document["placements"][0].update(relative_home=relative))
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE)
    assert failure.value.code == "unsafe-path"


def test_duplicate_json_members_and_ambiguous_store_inventory_refuse(owner: Path) -> None:
    _allocate()
    path = _descriptor(owner)
    original = path.read_text(encoding="utf-8")
    path.write_text(
        original.replace('"version": 1', '"version": 1, "version": 1'), encoding="utf-8"
    )
    with pytest.raises(WorktreeDataError, match="duplicate"):
        store.read_worktree_data(PROJECT, WORKTREE)
    path.write_text(original, encoding="utf-8")
    _change(owner, lambda document: document["placements"].append(document["placements"][0]))
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE)
    assert failure.value.code == "identity-conflict"


@pytest.mark.parametrize(
    "partial", ["no-descriptor", "missing-home", "foreign-lane", "extra-file"]
)
def test_partial_or_foreign_registration_never_adopts_or_repairs(
    owner: Path,
    partial: str,
) -> None:
    placement = _allocate()
    if partial == "no-descriptor":
        _descriptor(owner).unlink()
    elif partial == "missing-home":
        Path(placement.state_home).rmdir()
    elif partial == "foreign-lane":
        (Path(placement.state_home).parent / "foreign").mkdir()
    else:
        (_descriptor(owner).parent / "unknown-member").write_text("retained", encoding="utf-8")
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE, store_id="f" * 32)
    expected = "descriptor-limit" if partial == "extra-file" else "partial-registration"
    assert failure.value.code == expected
    with pytest.raises(WorktreeDataError):
        _allocate()
    assert _snapshot(owner) == before


def test_interrupted_publication_preserves_unadmitted_home(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_path: Path, _content: bytes) -> None:
        raise OSError("synthetic publication interruption")

    monkeypatch.setattr(store, "_publish_descriptor", fail)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate()
    assert failure.value.code == "allocation-failed"
    assert (owner / "worktree-state" / WORKTREE / "agendas" / "main").is_dir()
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError) as repeated:
        _allocate()
    assert repeated.value.code == "partial-registration"
    assert _snapshot(owner) == before


def test_bounded_descriptor_and_member_inventory(owner: Path) -> None:
    _allocate()
    path = _descriptor(owner)
    path.write_bytes(b" " * (MAX_DESCRIPTOR_BYTES + 1))
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE)
    assert failure.value.code == "descriptor-limit"


def test_placement_inventory_limit_refuses_before_new_home(owner: Path) -> None:
    _allocate()
    for index in range(1, MAX_PLACEMENTS):
        _allocate(lane=f"lane-{index}", store_id=f"{index:032x}")
    before = _snapshot(owner)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate(lane="one-too-many", store_id="f" * 32)
    assert failure.value.code == "descriptor-limit"
    assert _snapshot(owner) == before
    assert len(store.read_worktree_data(PROJECT, WORKTREE).placements) == MAX_PLACEMENTS


def test_lock_failure_refuses_without_inprocess_fallback(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def busy(_path: Path, **kwargs: Any) -> None:
        assert kwargs["require_sidecar"] is True
        raise TimeoutError("synthetic held owner")

    monkeypatch.setattr(tracking, "_RecordLock", busy)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate()
    assert failure.value.code == "owner-busy"
    assert not (owner / "worktree-state").exists()


def test_actual_project_state_resolver_keeps_installation_cells_separate(
    owner: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = {"cellRoot": str(tmp_path / "cell-one"), "marketplaceId": "sample-marketplace"}
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(tmp_path / "synthetic-context.json"))
    monkeypatch.setattr(registry_paths, "installation_context", lambda: context.copy())
    monkeypatch.setattr(
        project_state,
        "ensure_project_state",
        lambda *_a: pytest.fail("implicit enrollment"),
    )

    def admitted_fixture_root() -> Path:
        identity = project_state._identity(PROJECT)
        identity.state_root.mkdir(parents=True)
        (identity.repository_root / "identity.json").write_text(
            json.dumps(project_state._receipt(identity)),
            encoding="utf-8",
        )
        _record(identity.state_root)
        assert cfg.project_dir(PROJECT) == identity.state_root
        return identity.state_root

    first = admitted_fixture_root()
    placement = _allocate()
    selected = store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert selected is not None and selected.scope.marketplace_id == "sample-marketplace"
    assert Path(placement.state_home).is_relative_to(first)
    assert not (owner / "worktree-state").exists()
    original = _descriptor(first).read_bytes()
    context["cellRoot"] = str(tmp_path / "cell-two")
    second = admitted_fixture_root()
    (second / "worktree-state" / WORKTREE / "agendas" / "main").mkdir(parents=True)
    _descriptor(second).write_bytes(original)
    before = _snapshot(second)
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE)
    assert failure.value.code == "identity-conflict"
    with pytest.raises(WorktreeDataError):
        _allocate()
    assert _descriptor(first).read_bytes() == original
    after = _snapshot(second)
    assert {
        key: (value[0], value[2]) for key, value in after.items() if not key.endswith(".lock")
    } == {key: (value[0], value[2]) for key, value in before.items()}


def test_canonical_nonce_is_rechecked_after_actual_lock_acquisition(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allocate()
    original = _descriptor(owner).read_bytes()
    real_lock = tracking._RecordLock

    @contextmanager
    def replaced_record(path: Path, **kwargs: Any) -> Any:
        with real_lock(path, **kwargs) as held:
            _record(owner, creation_nonce="new-incarnation")
            yield held

    monkeypatch.setattr(tracking, "_RecordLock", replaced_record)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate(lane="other", store_id="b" * 32)
    assert failure.value.code == "identity-conflict"
    assert _descriptor(owner).read_bytes() == original
    assert not (_descriptor(owner).parent / "agendas" / "other").exists()


def test_selected_project_root_is_rechecked_after_actual_lock_acquisition(
    owner: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = tmp_path / "replacement-project-root"
    _record(other)
    selected = [owner]
    monkeypatch.setattr(cfg, "project_dir", lambda _project: selected[0])
    real_lock = tracking._RecordLock

    @contextmanager
    def replaced_owner(path: Path, **kwargs: Any) -> Any:
        with real_lock(path, **kwargs) as held:
            selected[0] = other
            yield held

    monkeypatch.setattr(tracking, "_RecordLock", replaced_owner)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate()
    assert failure.value.code == "identity-conflict"
    assert not (owner / "worktree-state").exists()
    assert not (other / "worktree-state").exists()


def test_interrupted_additional_lane_is_not_hidden_by_prior_registration(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _allocate()
    descriptor = _descriptor(owner).read_bytes()

    def interrupted(_path: Path, _content: bytes) -> None:
        raise OSError("synthetic additional-publication interruption")

    monkeypatch.setattr(store, "_publish_descriptor", interrupted)
    with pytest.raises(WorktreeDataError) as failure:
        _allocate(lane="other", store_id="b" * 32)
    assert failure.value.code == "allocation-failed"
    assert _descriptor(owner).read_bytes() == descriptor
    assert Path(first.state_home).is_dir()
    assert (_descriptor(owner).parent / "agendas" / "other").is_dir()
    with pytest.raises(WorktreeDataError) as refused:
        store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert refused.value.code == "partial-registration"


def test_descriptor_read_failure_is_not_an_empty_inventory(
    owner: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allocate()
    descriptor = _descriptor(owner)
    before = _snapshot(owner)
    real_open = os.open

    def denied(path: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path) == descriptor:
            raise PermissionError("synthetic unreadable descriptor")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(WorktreeDataError) as failure:
        store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE)
    assert failure.value.code == "owner-unavailable"
    assert _snapshot(owner) == before


def _concurrent_allocate(
    root: str,
    store_id: str,
    ready: Any,
    go: Any,
    result: Any,
) -> None:
    from agent_worktrees import config as child_cfg
    from agent_worktrees import project_state as child_state
    from agent_worktrees import registry_paths as child_registry
    from agent_worktrees.worktree_data_store import allocate_worktree_data

    child_cfg.project_dir = lambda _project: Path(root)
    child_state._normalized_repository_remote = lambda repository: (
        f"network:example.invalid/{repository}"
    )
    child_registry.installation_context = lambda: None
    ready.put(True)
    if not go.wait(15):
        result.put("barrier-timeout")
        return
    try:
        allocated = allocate_worktree_data(
            PROJECT,
            WORKTREE,
            creation_nonce=NONCE,
            domain="agendas",
            lane="main",
            store_id=store_id,
        )
        result.put(allocated.store_id)
    except WorktreeDataError as error:
        result.put(error.code)


@pytest.mark.parametrize("conflicting", [False, True])
def test_real_process_allocation_serializes_one_admitted_location(
    owner: Path,
    conflicting: bool,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready, result = context.Queue(), context.Queue()
    go = context.Event()
    identities = [STORE, "b" * 32 if conflicting else STORE]
    processes = [
        context.Process(
            target=_concurrent_allocate, args=(str(owner), identity, ready, go, result)
        )
        for identity in identities
    ]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            assert ready.get(timeout=20) is True
        go.set()
        answers = [result.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        if conflicting:
            assert answers.count("identity-conflict") == 1
            assert len(set(answers) & set(identities)) == 1
        else:
            assert answers == [STORE, STORE]
        inventory = store.read_worktree_data(PROJECT, WORKTREE)
        assert inventory is not None and len(inventory.placements) == 1
        assert len(list((owner / "worktree-state" / WORKTREE / "agendas").iterdir())) == 1
    finally:
        go.set()
        for process in processes:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=10)
        for queue in (ready, result):
            queue.close()
            queue.join_thread()


def _allocate_across_shared_base(
    root: str,
    worktree: str,
    role: str,
    preflight_done: Any,
    creator_paused: Any,
    release_creator: Any,
    results: Any,
) -> None:
    cfg.project_dir = lambda _project: Path(root)
    project_state._normalized_repository_remote = lambda repository: (
        f"network:example.invalid/{repository}"
    )
    registry_paths.installation_context = lambda: None
    owner = Path(root)
    real_require, real_sync = store._require_durability, store._sync_directory
    real_publish = store._publish_descriptor
    published = False
    root_after_publication = False

    def require(path: Path) -> None:
        real_require(path)
        if role == "caller":
            preflight_done.set()
            if not creator_paused.wait(15):
                raise TimeoutError("shared-base creator did not pause")

    def sync(path: Path) -> None:
        nonlocal root_after_publication
        if role == "creator" and path == owner / "worktree-state":
            creator_paused.set()
            if not release_creator.wait(15):
                raise TimeoutError("shared-base creator was not released")
        real_sync(path)
        if published and path == owner:
            root_after_publication = True

    def publish(path: Path, raw: bytes) -> None:
        nonlocal published
        real_publish(path, raw)
        published = True

    store._require_durability = require
    store._sync_directory = sync
    store._publish_descriptor = publish
    try:
        if role == "creator" and not preflight_done.wait(15):
            raise TimeoutError("caller did not finish root preflight")
        placement = store.allocate_worktree_data(
            PROJECT,
            worktree,
            creation_nonce=NONCE,
            domain="agendas",
            lane="main",
            store_id=STORE,
        )
        results.put((role, placement.store_id, root_after_publication))
    except Exception as error:
        results.put((role, type(error).__name__, str(error)))


def test_real_process_shared_base_is_durable_without_waiting_for_other_worktree(
    owner: Path,
) -> None:
    other_worktree = WORKTREE + "-other"
    record = json.loads((owner / "worktrees" / f"{WORKTREE}.yaml").read_text())
    record["worktree_id"] = other_worktree
    (owner / "worktrees" / f"{other_worktree}.yaml").write_text(json.dumps(record))
    context = multiprocessing.get_context("spawn")
    preflight_done, creator_paused, release_creator = (
        context.Event(),
        context.Event(),
        context.Event(),
    )
    results = context.Queue()
    processes = [
        context.Process(
            target=_allocate_across_shared_base,
            args=(
                str(owner),
                worktree,
                role,
                preflight_done,
                creator_paused,
                release_creator,
                results,
            ),
        )
        for worktree, role in ((WORKTREE, "caller"), (other_worktree, "creator"))
    ]
    try:
        for process in processes:
            process.start()
        caller = results.get(timeout=25)
        # The creator still owns its different record lock and has not flushed
        # the shared base's parent link; caller acknowledgment must stand alone.
        assert creator_paused.is_set()
        release_creator.set()
        creator = results.get(timeout=25)
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert caller == ("caller", STORE, True)
        assert creator == ("creator", STORE, True)
        assert store.read_worktree_data(PROJECT, WORKTREE, store_id=STORE) is not None
        assert store.read_worktree_data(PROJECT, other_worktree, store_id=STORE) is not None
    finally:
        release_creator.set()
        for process in processes:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=10)
        results.close()
        results.join_thread()
