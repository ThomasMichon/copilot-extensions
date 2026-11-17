"""Native private publication and first-touch local history collection."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from agent_procutil.owned_budget import OwnedExecutionBudget

from agent_logger.sync import worktree_state_archive as archive
from agent_logger.sync import worktree_state_pipeline as pipeline
from agent_logger.sync.publication_directory import publication_directory
from agent_logger.sync.targets.filesystem import LocalTarget
from agent_logger.aggregate import ResourceBinding
from agent_logger.sync.worktree_state import WorktreeStateError, admit_repository_collection

from .test_worktree_state import IDENTITY, Packet, collect, fake_owner, plan_for
from .test_worktree_state import packet as packet
from .test_worktree_state_pipeline import selection

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows filesystem contract")


def test_native_collection_is_private_immutable_and_freshly_read_back(packet: Packet) -> None:
    cfg, target, plan, source = packet
    before = {item.name: item.read_bytes() for item in source.iterdir()}
    receipt = collect(packet, sessions=("session-1", "session-2"))
    assert collect(packet, sessions=("session-3",)) == receipt
    revision = target._root() / IDENTITY.relative_directory
    with publication_directory(revision, create=False) as directory:
        directory.check_private()
    references = [json.loads(item.read_bytes()) for item in (revision / "references").iterdir()]
    assert {item["session"] for item in references} == {"session-1", "session-2", "session-3"}
    assert len(list(target._root().rglob("agenda.sqlite3"))) == 1
    assert {item.name: item.read_bytes() for item in source.iterdir()} == before
    assert archive.readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner) == receipt
    assert receipt.as_dict()["remote_archive_acknowledged"] is False
    assert receipt.as_dict()["reclamation_authorized"] is False
    (revision / "receipt.json").write_bytes(b"{}")
    with pytest.raises(WorktreeStateError):
        archive.readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner)


def test_native_staging_is_private_and_exactly_cleaned(packet: Packet) -> None:
    cfg, target, _, _ = packet
    _, _, placement = selection(cfg.home)
    with pipeline._export_directory(
        cfg, target._root(), placement,
        OwnedExecutionBudget.from_timeout(30, cleanup_reserve=5),
    ) as directory:
        with publication_directory(directory, create=False) as pinned:
            pinned.check_private()
        (directory / "agenda.sqlite3").write_bytes(b"synthetic owner export")
        (directory / "manifest.json").write_bytes(b"synthetic manifest")
    assert not directory.exists()
    assert (cfg.home / "worktree-state-exports").is_dir()


@pytest.mark.parametrize("outcome", ["partial", "unexpected-member"])
def test_native_failed_staging_is_retained(packet: Packet, outcome: str) -> None:
    cfg, target, _, _ = packet
    _, _, placement = selection(cfg.home)
    with pytest.raises((WorktreeStateError, RuntimeError)):
        with pipeline._export_directory(
            cfg, target._root(), placement,
            OwnedExecutionBudget.from_timeout(30, cleanup_reserve=5),
        ) as directory:
            (directory / "agenda.sqlite3").write_bytes(b"retained evidence")
            if outcome == "partial":
                raise RuntimeError("interrupted owner export")
            (directory / "manifest.json").write_bytes(b"synthetic manifest")
            (directory / "unknown-evidence").write_bytes(b"preserve")
    assert (directory / "agenda.sqlite3").read_bytes() == b"retained evidence"


def test_native_barrier_failure_cannot_publish_a_receipt(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.publication_directory import PublicationDirectory

    def failed(*args: object) -> None:
        raise OSError("injected native directory barrier failure")

    monkeypatch.setattr(PublicationDirectory, "sync", failed)
    with pytest.raises(OSError, match="barrier failure"):
        collect(packet)
    assert not list(packet[1]._root().rglob("receipt.json"))


def _broad_directory(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    from agent_procutil import _daemon_instance_owner as owner

    native_apis: Callable[[], tuple[Any, Any]] = getattr(owner, "_apis")
    advapi, kernel = native_apis()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    current = owner._current_sids(os.getpid())[0]
    sddl = f"O:{current}D:P(A;OICI;FA;;;{current})(A;OICI;GRGX;;;WD)"
    descriptor = ctypes.c_void_p()
    assert advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None,
    )
    try:
        attributes = owner._attributes_type()
        security = attributes(ctypes.sizeof(attributes), descriptor, 0)
        assert kernel.CreateDirectoryW(str(path), ctypes.byref(security))
    finally:
        kernel.LocalFree(descriptor)


def test_native_private_file_creation_does_not_inherit_broad_access(tmp_path: Path) -> None:
    from agent_logger.sync.publication_windows import check_private

    broad = tmp_path / "synthetic-shared-parent"
    _broad_directory(broad)
    inherited = broad / "inherited.txt"
    inherited.write_bytes(b"synthetic non-sensitive fixture")
    with pytest.raises(PermissionError):
        check_private(inherited)
    with publication_directory(broad, create=False) as directory:
        with directory.create("private.txt", mode=0o600) as stream:
            stream.write(b"synthetic private fixture")
        check_private(broad / "private.txt")


def test_native_existing_broad_staging_is_not_repaired(packet: Packet) -> None:
    cfg, target, _, _ = packet
    cfg.home.mkdir()
    base = cfg.home / "worktree-state-exports"
    _broad_directory(base)
    _, _, placement = selection(cfg.home)
    with pytest.raises(PermissionError):
        with pipeline._export_directory(
            cfg, target._root(), placement,
            OwnedExecutionBudget.from_timeout(30, cleanup_reserve=5),
        ):
            pytest.fail("broad staging was adopted")
    assert list(base.iterdir()) == []
    with publication_directory(base, create=False) as directory:
        with pytest.raises(PermissionError):
            directory.check_private()


def test_native_directory_pin_prevents_ancestry_replacement(tmp_path: Path) -> None:
    source = tmp_path / "owned"
    source.mkdir()
    with publication_directory(source, create=False):
        with pytest.raises(PermissionError):
            source.rename(tmp_path / "replacement")
    source.rename(tmp_path / "replacement")


def test_native_receipt_barrier_failure_is_retained_and_retry_reflushes(
    packet: Packet, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.publication_directory import PublicationDirectory

    original = PublicationDirectory.sync

    def failed(directory: PublicationDirectory) -> None:
        if (directory.path / "receipt.json").exists():
            raise OSError("injected final receipt barrier failure")
        original(directory)

    with monkeypatch.context() as patch:
        patch.setattr(PublicationDirectory, "sync", failed)
        with pytest.raises(OSError, match="final receipt barrier"):
            collect(packet)
    revision = packet[1]._root() / IDENTITY.relative_directory
    assert (revision / "export/agenda.sqlite3").is_file()
    assert collect(packet).identity == IDENTITY


def test_native_staging_allocation_barrier_failure_names_retained_directory(
    packet: Packet, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_logger.sync.publication_directory import PublicationDirectory

    cfg, target, _, _ = packet
    _, _, placement = selection(cfg.home)
    base = cfg.home / "worktree-state-exports"
    original = PublicationDirectory.sync

    def failed(directory: PublicationDirectory) -> None:
        if directory.path == base and directory.names():
            raise OSError("injected staging registration barrier failure")
        original(directory)

    monkeypatch.setattr(PublicationDirectory, "sync", failed)
    with pytest.raises(OSError, match="staging registration barrier"):
        with pipeline._export_directory(
            cfg, target._root(), placement,
            OwnedExecutionBudget.from_timeout(30, cleanup_reserve=5),
        ):
            pytest.fail("unacknowledged staging reached the exporter")
    retained = list(base.iterdir())
    assert len(retained) == 1 and retained[0].is_dir()
    assert str(retained[0]) in capsys.readouterr().err


@pytest.mark.parametrize("changed", ["configured", "claimed"])
def test_native_collection_requires_exact_declared_destination_spelling(
    packet: Packet, changed: str,
) -> None:
    cfg, target, plan, _ = packet
    differently_spelled = target._root().with_name(target._root().name.upper())
    if changed == "configured":
        cfg._data["sync"]["targets"]["local"]["path"] = str(differently_spelled)
    else:
        plan.claims = [replace(
            plan.claims[0], resource=ResourceBinding("filesystem", str(differently_spelled)),
        )]
    with pytest.raises(WorktreeStateError):
        admit_repository_collection(cfg, target, plan, IDENTITY.repository)
    assert not target._root().exists()


@pytest.mark.parametrize("route", ["source", "destination", "staging"])
def test_native_junctions_refuse_without_entering_the_target(
    packet: Packet, route: str,
) -> None:
    import _winapi

    cfg, target, plan, source = packet
    outside = source.parent / "junction-target"
    outside.mkdir()
    link = source.parent / "linked-export"
    if route == "destination":
        link = target._root()
    elif route == "staging":
        cfg.home.mkdir()
        link = cfg.home / "worktree-state-exports"
    create_junction: Callable[[str, str], None] = getattr(_winapi, "CreateJunction")
    create_junction(str(source if route == "source" else outside), str(link))
    assert getattr(link.lstat(), "st_file_attributes", 0) & 0x400
    with pytest.raises(OSError):
        if route == "source":
            archive.collect_worktree_state(
                cfg, target, plan, link, IDENTITY, ("session-1",), fake_owner,
            )
        elif route == "destination":
            collect(packet)
        else:
            _, _, placement = selection(cfg.home)
            with pipeline._export_directory(
                cfg, target._root(), placement,
                OwnedExecutionBudget.from_timeout(30, cleanup_reserve=5),
            ):
                pytest.fail("junction staging was entered")
    assert list(outside.iterdir()) == []


def test_native_private_collection_preserves_long_destination_paths(packet: Packet) -> None:
    cfg, _, _, source = packet
    root = source.parent / ("first-" * 20) / ("second-" * 20)
    root.parent.mkdir(parents=True)
    cfg._data["sync"]["targets"]["local"]["path"] = str(root)
    target, plan = LocalTarget({"path": str(root)}), plan_for(root)
    receipt = archive.collect_worktree_state(
        cfg, target, plan, source, IDENTITY, ("session-1",), fake_owner,
    )
    member = root / IDENTITY.relative_directory / "export/agenda.sqlite3"
    assert len(str(member)) > 260 and member.read_bytes() == (source / "agenda.sqlite3").read_bytes()
    assert archive.readback_worktree_state(cfg, target, plan, IDENTITY, fake_owner) == receipt


@pytest.mark.parametrize("level", ["root", "namespace", "key", "revision", "export", "references"])
def test_native_failed_parent_barrier_stays_blocking_on_retry(
    packet: Packet, monkeypatch: pytest.MonkeyPatch, level: str,
) -> None:
    from agent_logger.sync.publication_directory import PublicationDirectory

    root = packet[1]._root()
    revision = root / IDENTITY.relative_directory
    expected = {
        "root": root, "namespace": root / "worktree-state",
        "key": revision.parent, "revision": revision,
        "export": revision / "export", "references": revision / "references",
    }[level]
    original = PublicationDirectory.sync

    def failed(directory: PublicationDirectory) -> None:
        if directory.path == expected.parent and expected.exists():
            raise OSError("injected unresolved parent directory barrier")
        original(directory)

    with monkeypatch.context() as patch:
        patch.setattr(PublicationDirectory, "sync", failed)
        for _ in range(2):
            with pytest.raises(OSError, match="unresolved parent directory barrier"):
                collect(packet)
        assert expected.is_dir()
    if level == "export":
        with pytest.raises(WorktreeStateError, match="incomplete"):
            collect(packet)
    else:
        assert collect(packet).identity == IDENTITY


def test_native_missing_owner_root_parent_refuses_before_creating_ancestors(packet: Packet) -> None:
    cfg, _, _, source = packet
    root = source.parent / "unallocated-parent" / "collection"
    cfg._data["sync"]["targets"]["local"]["path"] = str(root)
    with pytest.raises(FileNotFoundError):
        archive.collect_worktree_state(
            cfg, LocalTarget({"path": str(root)}), plan_for(root),
            source, IDENTITY, ("session-1",), fake_owner,
        )
    assert not root.parent.exists()


@pytest.mark.parametrize(
    "member",
    ["export/agenda.sqlite3", "export/manifest.json", "collection.json", "receipt.json",
     "contributors.json", "reference"],
)
def test_native_broad_retained_members_refuse_without_permission_repair(
    packet: Packet, member: str,
) -> None:
    from agent_logger.sync.publication_windows import check_private
    from .test_worktree_state_contributors import projection, reader

    cfg, target, plan, source = packet
    owner = reader(projection(source))

    def publish() -> None:
        archive.collect_worktree_state(
            cfg, target, plan, source, IDENTITY, ("session-1",), fake_owner,
            contributors=owner,
        )

    publish()
    revision = target._root() / IDENTITY.relative_directory
    selected = (
        next((revision / "references").iterdir()) if member == "reference" else revision / member
    )
    original = selected.read_bytes()
    broad = source.parent / "synthetic-broad-files"
    _broad_directory(broad)
    restored = broad / "restored-member"
    restored.write_bytes(original)
    selected.unlink()
    restored.rename(selected)
    with pytest.raises(PermissionError):
        check_private(selected)
    if member != "reference":
        with pytest.raises(PermissionError):
            archive.readback_worktree_state(
                cfg, target, plan, IDENTITY, fake_owner, contributors=owner,
            )
    with pytest.raises((PermissionError, WorktreeStateError)):
        publish()
    assert selected.read_bytes() == original
    with pytest.raises(PermissionError):
        check_private(selected)


def test_native_private_export_accepts_its_current_token_owner(tmp_path: Path) -> None:
    from agent_procutil import _daemon_instance_owner as owner
    from agent_logger.sync.publication_windows import check_private, mkdir_private

    parent = tmp_path / "private-export"
    mkdir_private(parent)
    member = parent / "agenda.sqlite3"
    member.write_bytes(b"synthetic exported history with inherited private access")
    assert owner._path_owner_sid(member) in owner._current_sids(os.getpid())
    with publication_directory(parent, create=False, private=True) as directory:
        with directory.open(member.name, protected=True) as stream:
            assert stream.read() == member.read_bytes()
    check_private(member)


@pytest.mark.parametrize("stage", ["source-export", "collected-copy", "retained-readback"])
def test_native_contributor_read_pins_the_private_database(
    packet: Packet, stage: str,
) -> None:
    from .test_worktree_state_contributors import projection, reader

    cfg, target, plan, source = packet
    original = reader(projection(source))
    retained = target._root() / IDENTITY.relative_directory / "export"
    broad = source.parent / "synthetic-restore"
    _broad_directory(broad)
    restored = broad / "agenda.sqlite3"
    restored.write_bytes((source / "agenda.sqlite3").read_bytes())
    attempted: list[Path] = []

    def owner(directory: Path, **selection: Any) -> dict[str, Any]:
        value = original(directory, **selection)
        selected = source if stage == "source-export" else retained
        if directory == selected:
            attempted.append(directory)
            with pytest.raises(PermissionError):
                os.replace(restored, directory / "agenda.sqlite3")
        return value

    initial_reader = original if stage == "retained-readback" else owner
    receipt = archive.collect_worktree_state(
        cfg, target, plan, source, IDENTITY, (), fake_owner, contributors=initial_reader,
    )
    if stage == "retained-readback":
        assert archive.readback_worktree_state(
            cfg, target, plan, IDENTITY, fake_owner, contributors=owner,
        ) == receipt
    assert attempted and restored.is_file()
    assert (retained / "agenda.sqlite3").read_bytes() == restored.read_bytes()
