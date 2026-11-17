"""Synthetic-byte filesystem controls, separate from actor-free promotion CI."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys
from types import ModuleType
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src/agent_vault"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor custody")


@pytest.fixture
def files(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    package = ModuleType("exclusive_export_files_fixture")
    package.__dict__["__path__"] = []
    monkeypatch.setitem(sys.modules, package.__name__, package)
    loaded: dict[str, ModuleType] = {}
    for leaf in ("attachment_import", "attachment_export_files"):
        spec = importlib.util.spec_from_file_location(
            package.__name__ + "." + leaf, SOURCE / (leaf + ".py"),
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        spec.loader.exec_module(module)
        loaded[leaf] = module
    return loaded["attachment_export_files"]


def destination(tmp_path: Path) -> Path:
    path = tmp_path / "destination"
    path.mkdir(mode=0o700)
    return path


def test_actual_exclusive_pair_persists_initial_custody_and_closes_descriptors(
    files: ModuleType, tmp_path: Path,
) -> None:
    path = destination(tmp_path)
    pair = files.ExclusivePair(str(path), "example.key")
    descriptors = list(pair._directories)
    with pair:
        pair.materialize(b"synthetic-private", b"synthetic-public")
        descriptors.extend(created.descriptor for created in pair._files)
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    for name, value in (("example.key", b"synthetic-private"), ("example.key.pub", b"synthetic-public")):
        result = path / name
        assert result.read_bytes() == value
        assert stat.S_IMODE(result.stat().st_mode) == 0o600
        assert result.stat().st_nlink == 1


@pytest.mark.parametrize("kind", ["file", "symlink", "hardlink"])
def test_actual_existing_destinations_never_change(
    files: ModuleType, tmp_path: Path, kind: str,
) -> None:
    path = destination(tmp_path)
    retained = tmp_path / "retained"
    retained.write_bytes(b"untouched")
    public = path / "example.key.pub"
    if kind == "symlink":
        public.symlink_to(retained)
    elif kind == "hardlink":
        os.link(retained, public)
    else:
        public.write_bytes(b"untouched")
    with pytest.raises(files.ExportFileError, match="destination_exists"):
        files.ExclusivePair(str(path), "example.key")
    assert retained.read_bytes() == b"untouched"
    assert public.read_bytes() == b"untouched"
    assert not (path / "example.key").exists()


@pytest.mark.parametrize("kind", ["symlink", "writable"])
def test_actual_unsafe_ancestry_refuses_before_file_creation(
    files: ModuleType, tmp_path: Path, kind: str,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    path = parent / "destination"
    path.mkdir(mode=0o700)
    if kind == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        selected = alias / "destination"
    else:
        parent.chmod(0o777)
        selected = path
    with pytest.raises((OSError, files.ExportFileError)):
        files.ExclusivePair(str(selected), "example.key")
    assert list(path.iterdir()) == []


def test_actual_second_collision_retains_empty_first_file_without_private_write(
    files: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = destination(tmp_path)
    pair = files.ExclusivePair(str(path), "example.key")
    original = os.open

    def opening(name: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if name == "example.key.pub" and flags & os.O_CREAT:
            (path / "example.key.pub").write_bytes(b"racing-owner")
        return original(name, flags, mode, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", opening)
        with pytest.raises(FileExistsError), pair:
            pair.materialize(b"synthetic-private", b"synthetic-public")
    assert pair.effect_possible is True
    assert (path / "example.key").read_bytes() == b""
    assert (path / "example.key.pub").read_bytes() == b"racing-owner"


@pytest.mark.parametrize("failure", ["write", "sync", "close", "interrupt"])
def test_actual_failure_keeps_files_and_settles_owned_descriptors(
    files: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    path = destination(tmp_path)
    pair = files.ExclusivePair(str(path), "example.key")
    descriptors = list(pair._directories)
    original_close = os.close

    def close(descriptor: int) -> None:
        regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
        descriptors.append(descriptor)
        original_close(descriptor)
        if failure == "close" and regular:
            raise OSError("injected post-close uncertainty")

    def interrupted(*_args: Any) -> None:
        if failure == "interrupt":
            raise KeyboardInterrupt
        raise OSError("injected file failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "close", close)
        if failure in ("write", "interrupt"):
            patch.setattr(os, "write", interrupted)
        if failure == "sync":
            patch.setattr(os, "fsync", interrupted)
        with pytest.raises((OSError, KeyboardInterrupt)), pair:
            pair.materialize(b"synthetic-private", b"synthetic-public")
    assert pair.effect_possible is True
    assert set(item.name for item in path.iterdir()) == {"example.key", "example.key.pub"}
    for descriptor in set(descriptors):
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("substitution", ["file", "directory"])
def test_actual_namespace_substitution_never_overwrites_the_replacement(
    files: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, substitution: str,
) -> None:
    path = destination(tmp_path)
    pair = files.ExclusivePair(str(path), "example.key")
    victim = tmp_path / "victim"
    victim.write_bytes(b"untouched")
    original = os.write
    substituted = False

    def writing(descriptor: int, data: bytes) -> int:
        nonlocal substituted
        count = original(descriptor, data)
        if not substituted:
            substituted = True
            if substitution == "directory":
                path.rename(tmp_path / "retained-directory")
                path.mkdir(mode=0o700)
            else:
                (path / "example.key").rename(path / "retained-private")
                (path / "example.key").symlink_to(victim)
        return count

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", writing)
        with pytest.raises(files.ExportFileError, match="output_custody_invalid"), pair:
            pair.materialize(b"synthetic-private", b"synthetic-public")
    assert victim.read_bytes() == b"untouched"
    retained = (
        tmp_path / "retained-directory/example.key"
        if substitution == "directory" else path / "retained-private"
    )
    assert retained.read_bytes() == b"synthetic-private"
