"""Synthetic new-home admission and checkout-independent passive discovery."""

from __future__ import annotations

import json
import multiprocessing
import os
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
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


def _record(root: Path, **changes: Any) -> Path:
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
    path = directory / f"{WORKTREE}.yaml"
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

    monkeypatch.setattr(tracking, "_atomic_write", fail)
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

    monkeypatch.setattr(tracking, "_atomic_write", interrupted)
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
