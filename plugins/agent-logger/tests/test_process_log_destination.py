"""Process-log payload creation and publication stay beneath pinned directories."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import BinaryIO

import pytest

from agent_logger.sync.publication_directory import PublicationDirectory, publication_directory
from agent_logger.sync.provenance import windows_extended_path
from agent_logger.sync.targets import filesystem

pytestmark = [
    pytest.mark.guard,
    pytest.mark.contract("agent_logger.process_logs.destination"),
]

NAME = "process-1000-123.log"


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    (root / NAME).write_bytes(b"new evidence\n")
    return root


@pytest.mark.skipif(os.name == "nt", reason="POSIX mkdir/openat ancestor races")
@pytest.mark.parametrize("timing", ["before_open", "after_open", "before_mkdir"])
def test_process_log_destination_creation_stays_in_pinned_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timing: str,
) -> None:
    source = _source(tmp_path)
    root = tmp_path / "destination"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    parked = tmp_path / "parked"
    original_open, original_mkdir = os.open, os.mkdir
    swapped = False

    def swap() -> None:
        nonlocal swapped
        root.rename(parked)
        root.symlink_to(outside, target_is_directory=True)
        swapped = True

    def raced_open(
        path: str | os.PathLike[str], flags: int, mode: int = 0o777,
        *, dir_fd: int | None = None,
    ) -> int:
        trigger = path == "destination" and dir_fd is not None and not swapped
        if trigger and timing == "before_open":
            swap()
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        if trigger and timing == "after_open":
            swap()
        return fd

    def raced_mkdir(
        path: str | os.PathLike[str], mode: int = 0o777,
        *, dir_fd: int | None = None,
    ) -> None:
        if path == "example" and dir_fd is not None and timing == "before_mkdir":
            swap()
        original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", raced_open)
    monkeypatch.setattr(os, "mkdir", raced_mkdir)
    result = filesystem.LocalTarget({"path": str(root)}).push_process_logs(source, "example")
    assert swapped
    assert list(outside.iterdir()) == []
    if timing == "before_open":
        assert not result.ok
        assert list(parked.iterdir()) == []
    else:
        assert result.ok and result.file_count == 1
        assert (parked / "example" / "logs" / NAME).read_bytes() == b"new evidence\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX rename during held directory I/O")
@pytest.mark.parametrize("phase", ["create", "metadata", "replace", "stat", "unlink"])
def test_process_log_destination_payload_operations_are_directory_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    source = _source(tmp_path)
    root = tmp_path / "destination"
    dest = root / "example" / "logs"
    dest.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "example" / "logs").mkdir(parents=True)
    sentinel = outside / "example" / "logs" / NAME
    sentinel.write_bytes(b"outside evidence\n")
    parked = tmp_path / "parked"
    original = getattr(PublicationDirectory, phase)
    swapped = False

    def raced(
        directory: PublicationDirectory, *args: object, **kwargs: object,
    ) -> object:
        nonlocal swapped
        if not swapped:
            root.rename(parked)
            root.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original(directory, *args, **kwargs)

    monkeypatch.setattr(PublicationDirectory, phase, raced)
    copied, nbytes, locked = filesystem._copy_process_logs(source, dest)
    assert swapped and copied == 1 and nbytes == len(b"new evidence\n") and not locked
    assert (parked / "example" / "logs" / NAME).read_bytes() == b"new evidence\n"
    assert sentinel.read_bytes() == b"outside evidence\n"
    assert sorted(p.name for p in sentinel.parent.iterdir()) == [NAME]
    assert sorted(p.name for p in (parked / "example" / "logs").iterdir()) == [NAME]


@pytest.mark.parametrize("phase", ["create", "metadata", "replace", "stat"])
@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.ENOENT])
def test_process_log_destination_errors_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, code: int,
) -> None:
    source = _source(tmp_path)
    root = tmp_path / "destination"
    dest = root / "example" / "logs"
    dest.mkdir(parents=True)
    retained = dest / NAME
    retained.write_bytes(b"previous good copy\n")

    def failed(*args: object, **kwargs: object) -> None:
        raise OSError(code, f"destination {phase} failed")

    monkeypatch.setattr(PublicationDirectory, phase, failed)
    result = filesystem.LocalTarget({"path": str(root)}).push_process_logs(source, "example")
    assert not result.ok and f"destination {phase} failed" in result.detail
    expected = b"new evidence\n" if phase == "stat" and code == errno.ENOENT else b"previous good copy\n"
    assert retained.read_bytes() == expected
    assert sorted(p.name for p in dest.iterdir()) == [NAME]


def test_process_log_destination_replace_failure_keeps_previous_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    dest = tmp_path / "destination"
    dest.mkdir()
    (dest / NAME).write_bytes(b"previous good copy\n")
    original_copy = filesystem.shutil.copyfileobj

    def changed(stream: BinaryIO, target: BinaryIO, length: int) -> None:
        original_copy(stream, target, length)
        (source / NAME).write_bytes(b"source changed while copying\n")

    monkeypatch.setattr(filesystem.shutil, "copyfileobj", changed)
    copied, nbytes, locked = filesystem._copy_process_logs(source, dest)
    assert (copied, nbytes) == (0, 0) and locked == [source / NAME]
    assert (dest / NAME).read_bytes() == b"previous good copy\n"
    assert sorted(p.name for p in dest.iterdir()) == [NAME]


@pytest.mark.skipif(os.name != "nt", reason="real Windows ancestor handles")
def test_process_log_source_roots_native_destination_handles_cover_creation_and_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    root = tmp_path / "destination"
    root.mkdir()
    original_mkdir = os.mkdir
    original_copy = filesystem.shutil.copyfileobj
    denied = []

    def attempt() -> None:
        with pytest.raises(PermissionError):
            root.rename(tmp_path / "parked")
        denied.append(True)

    def observed_mkdir(
        path: str | os.PathLike[str], mode: int = 0o777,
        *, dir_fd: int | None = None,
    ) -> None:
        if Path(path).name == "example":
            attempt()
        original_mkdir(path, mode, dir_fd=dir_fd)

    def observed_copy(stream: BinaryIO, target: BinaryIO, length: int) -> None:
        attempt()
        original_copy(stream, target, length)

    monkeypatch.setattr(os, "mkdir", observed_mkdir)
    monkeypatch.setattr(filesystem.shutil, "copyfileobj", observed_copy)
    result = filesystem.LocalTarget({"path": str(root)}).push_process_logs(source, "example")
    assert result.ok and result.file_count == 1 and len(denied) == 2
    root.rename(tmp_path / "parked")
    assert (tmp_path / "parked" / "example" / "logs" / NAME).read_bytes() == b"new evidence\n"


def test_process_log_destination_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="unsafe publication"):
        with publication_directory(tmp_path / "child" / ".." / "outside"):
            pytest.fail("unsafe boundary entered")
    assert not (tmp_path / "child").exists()


def test_process_log_destination_failed_create_does_not_delete_existing_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    dest = tmp_path / "destination"
    dest.mkdir()
    temporary = dest / f".{NAME}.collision.tmp"
    temporary.write_bytes(b"not this transfer's file\n")
    monkeypatch.setattr(filesystem, "short_unique_id", lambda: "collision")
    with pytest.raises(FileExistsError):
        filesystem._copy_process_logs(source, dest)
    assert temporary.read_bytes() == b"not this transfer's file\n"
    assert not (dest / NAME).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX redirected cleanup of an unlanded temporary")
def test_process_log_destination_source_rejection_cleans_only_pinned_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    dest = tmp_path / "destination"
    dest.mkdir()
    retained = dest / NAME
    retained.write_bytes(b"previous good copy\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    temporary = f".{NAME}.fixed.tmp"
    (outside / temporary).write_bytes(b"outside temporary\n")
    parked = tmp_path / "parked"
    original_copy = filesystem.shutil.copyfileobj
    original_unlink = PublicationDirectory.unlink

    def changed(stream: BinaryIO, target: BinaryIO, length: int) -> None:
        original_copy(stream, target, length)
        (source / NAME).write_bytes(b"source changed while copying\n")

    def redirected_cleanup(directory: PublicationDirectory, name: str) -> None:
        dest.rename(parked)
        dest.symlink_to(outside, target_is_directory=True)
        original_unlink(directory, name)

    monkeypatch.setattr(filesystem, "short_unique_id", lambda: "fixed")
    monkeypatch.setattr(filesystem.shutil, "copyfileobj", changed)
    monkeypatch.setattr(PublicationDirectory, "unlink", redirected_cleanup)
    assert filesystem._copy_process_logs(source, dest)[:2] == (0, 0)
    assert (parked / NAME).read_bytes() == b"previous good copy\n"
    assert sorted(p.name for p in parked.iterdir()) == [NAME]
    assert (outside / temporary).read_bytes() == b"outside temporary\n"


@pytest.mark.skipif(os.name != "nt", reason="real Windows read-only replacement failure")
def test_process_log_source_roots_native_readonly_destination_retains_good_copy(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    dest = tmp_path / "destination"
    dest.mkdir()
    retained = dest / NAME
    retained.write_bytes(b"previous good copy\n")
    retained.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            filesystem._copy_process_logs(source, dest)
        assert retained.read_bytes() == b"previous good copy\n"
        assert sorted(p.name for p in dest.iterdir()) == [NAME]
    finally:
        retained.chmod(stat.S_IREAD | stat.S_IWRITE)
    dest.rename(tmp_path / "parked")


def test_process_log_source_roots_readonly_source_rejection_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    evidence = source / NAME
    evidence.chmod(stat.S_IREAD)
    dest = tmp_path / "destination"
    dest.mkdir()
    retained = dest / NAME
    retained.write_bytes(b"previous good copy\n")
    original_copy = filesystem.shutil.copyfileobj

    def changed(stream: BinaryIO, target: BinaryIO, length: int) -> None:
        original_copy(stream, target, length)
        evidence.chmod(stat.S_IREAD | stat.S_IWRITE)
        evidence.write_bytes(b"source changed while copying\n")

    monkeypatch.setattr(filesystem.shutil, "copyfileobj", changed)
    try:
        copied, nbytes, locked = filesystem._copy_process_logs(source, dest)
        assert (copied, nbytes) == (0, 0) and locked == [evidence]
        assert retained.read_bytes() == b"previous good copy\n"
        assert sorted(p.name for p in dest.iterdir()) == [NAME]
    finally:
        evidence.chmod(stat.S_IREAD | stat.S_IWRITE)


@pytest.mark.skipif(os.name == "nt", reason="POSIX parent directory durability")
@pytest.mark.parametrize("creation", ["retry", "concurrent"])
def test_process_log_destination_parent_flush_cannot_be_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, creation: str,
) -> None:
    source = _source(tmp_path)
    dest = tmp_path / "new-destination"
    parent_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    original_fsync, original_mkdir = os.fsync, os.mkdir
    failing = True
    barriers = 0
    raced = False

    def concurrent_mkdir(
        path: str | os.PathLike[str], mode: int = 0o777,
        *, dir_fd: int | None = None,
    ) -> None:
        nonlocal raced
        original_mkdir(path, mode, dir_fd=dir_fd)
        if creation == "concurrent" and path == dest.name and not raced:
            raced = True
            raise FileExistsError(errno.EEXIST, "concurrent creator won")

    def failed_flush(fd: int) -> None:
        nonlocal barriers
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) == parent_identity:
            barriers += 1
            if failing:
                raise OSError(errno.EIO, "parent durability failed")
        original_fsync(fd)

    monkeypatch.setattr(os, "mkdir", concurrent_mkdir)
    monkeypatch.setattr(os, "fsync", failed_flush)
    for _ in range(2):
        with pytest.raises(OSError, match="parent durability failed"):
            filesystem._copy_process_logs(source, dest)
        assert dest.is_dir() and list(dest.iterdir()) == []
    assert barriers == 2
    assert raced == (creation == "concurrent")
    failing = False
    assert filesystem._copy_process_logs(source, dest) == (1, len(b"new evidence\n"), [])
    assert barriers == 3
    assert (dest / NAME).read_bytes() == b"new evidence\n"


@pytest.mark.skipif(os.name != "nt", reason="native Windows extended destination paths")
def test_process_log_source_roots_native_long_destination_inspection_and_accounting(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    root = tmp_path / ("destination-" + "a" * 90) / ("nested-" + "b" * 90)
    dest = root / "example" / "logs"
    assert len(str(dest / NAME)) > 260
    target = filesystem.LocalTarget({"path": str(root)})
    first = target.push_process_logs(source, "example")
    assert first.ok and first.file_count == 1 and first.byte_count == len(b"new evidence\n")
    with open(windows_extended_path(dest / NAME), "rb") as stream:
        assert stream.read() == b"new evidence\n"
    second = target.push_process_logs(source, "example")
    assert second.ok and second.file_count == 0 and second.byte_count == 0
