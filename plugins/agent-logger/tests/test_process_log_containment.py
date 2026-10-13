"""Canonical source pinning must never read evidence through a raced ancestor."""

from __future__ import annotations

import gzip
import os
import zipfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

import pytest

from agent_logger.process_logs import ProcessLogRef, iter_process_log_refs, open_root_dir
from agent_logger.sync.targets.filesystem import LocalTarget

pytestmark = [
    pytest.mark.guard,
    pytest.mark.contract("agent_logger.process_logs.containment"),
]

NAME = "process-1000-123.log"
PAYLOAD = b"original evidence\n"


def _evidence(root: Path, codec: str, payload: bytes) -> ProcessLogRef:
    root.mkdir(parents=True)
    if codec == "zip":
        path = root / "logs.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(NAME, payload)
        return ProcessLogRef(path, NAME)
    path = root / (NAME + (".gz" if codec == "gzip" else ""))
    path.write_bytes(gzip.compress(payload) if codec == "gzip" else payload)
    return ProcessLogRef(path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-walk race injection")
@pytest.mark.parametrize("codec", ["raw", "gzip", "zip"])
@pytest.mark.parametrize("operation", ["enumerate", "read", "sync"])
@pytest.mark.parametrize("timing", ["before", "after"])
def test_process_log_ancestor_swap_is_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    codec: str, operation: str, timing: str,
) -> None:
    ancestor = tmp_path / "ancestor"
    root = ancestor / "logs"
    ref = _evidence(root, codec, PAYLOAD)
    outside = tmp_path / "outside"
    _evidence(outside / "logs", codec, b"outside evidence\n")
    (outside / "logs" / "process-outside.log").write_bytes(b"outside")
    parked = tmp_path / "parked"
    real_open = os.open
    swapped = False

    def swap() -> None:
        nonlocal swapped
        swapped = True
        ancestor.rename(parked)
        ancestor.symlink_to(outside, target_is_directory=True)

    def raced_open(
        path: str | os.PathLike[str], flags: int, mode: int = 0o777,
        *, dir_fd: int | None = None,
    ) -> int:
        trigger = path == "ancestor" and dir_fd is not None and not swapped
        if trigger and timing == "before":
            swap()
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if trigger and timing == "after":
            swap()
        return fd

    monkeypatch.setattr(os, "open", raced_open)
    destination = tmp_path / "destination"
    if operation == "sync":
        result = LocalTarget({"path": str(destination)}).push_process_logs(root, "example")
        assert not result.ok
        assert not list(destination.rglob("process-*"))
    elif timing == "before":
        with pytest.raises((OSError, ValueError)):
            if operation == "read":
                list(ref.iter_lines())
            else:
                list(iter_process_log_refs(root))
    elif operation == "read":
        assert "".join(ref.iter_lines()).encode() == PAYLOAD
    else:
        refs = list(iter_process_log_refs(root))
        assert refs == [ref]
    assert swapped
    assert (parked / "logs" / ref.path.name).is_file()
    assert (outside / "logs" / "process-outside.log").read_bytes() == b"outside"


@pytest.mark.skipif(os.name == "nt", reason="native symlink creation needs privileges")
@pytest.mark.parametrize("codec", ["raw", "gzip", "zip"])
def test_process_log_intentionally_relocated_ancestor_is_supported(
    tmp_path: Path, codec: str,
) -> None:
    ref = _evidence(tmp_path / "real" / "logs", codec, PAYLOAD)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "real", target_is_directory=True)
    root = alias / "logs"
    assert "".join(ProcessLogRef(root / ref.path.name, ref.member).iter_lines()).encode() == PAYLOAD
    assert len(list(iter_process_log_refs(root))) == 1
    destination = tmp_path / "destination"
    result = LocalTarget({"path": str(destination)}).push_process_logs(root, "example")
    assert result.ok and result.file_count == 1
    assert (destination / "example" / "logs" / ref.path.name).read_bytes() == ref.path.read_bytes()


def test_process_log_directory_body_errors_are_not_relabelled(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX directory descriptor API")
    with pytest.raises(ValueError, match="consumer failure"):
        with open_root_dir(tmp_path):
            raise ValueError("consumer failure")


@pytest.mark.parametrize("error", [NotADirectoryError, ValueError])
def test_process_log_copy_normalizes_directory_entry_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception],
) -> None:
    from agent_logger.sync.targets import filesystem

    @contextmanager
    def rejected_directory(path: Path) -> Iterator[None]:
        raise error("directory rejected")
        yield

    monkeypatch.setattr(filesystem, "archive_directory", rejected_directory)
    with pytest.raises(ValueError, match="not a directory") as caught:
        filesystem._copy_process_logs(tmp_path, tmp_path / "destination")
    assert type(caught.value.__cause__) is error


def test_process_log_copy_preserves_consumer_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.targets import filesystem

    def failed_copy(*args: object) -> None:
        raise NotADirectoryError("consumer failure")

    monkeypatch.setattr(filesystem, "_copy_process_logs_pinned", failed_copy)
    with pytest.raises(NotADirectoryError, match="consumer failure"):
        filesystem._copy_process_logs(tmp_path, tmp_path / "destination")


def test_process_log_invalid_source_diagnostic_names_path(tmp_path: Path) -> None:
    source = tmp_path / "not-a-directory"
    source.write_bytes(b"not a directory")
    result = LocalTarget({"path": str(tmp_path / "destination")}).push_process_logs(
        source, "example",
    )
    assert not result.ok
    assert str(source) in result.detail
    assert "not a directory" in result.detail


def test_process_log_enumeration_orders_entries_and_zip_members(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    (root / "process-z.log").write_bytes(PAYLOAD)
    with zipfile.ZipFile(root / "archive.zip", "w") as archive:
        archive.writestr("process-z.log", PAYLOAD)
        archive.writestr("process-a.log", PAYLOAD)
    (root / "process-a.log.gz").write_bytes(gzip.compress(PAYLOAD))
    from agent_logger.sync.archive_directory import archive_directory

    with archive_directory(root) as directory:
        assert directory.names() == ["archive.zip", "process-a.log.gz", "process-z.log"]
    assert [(ref.path.name, ref.member) for ref in iter_process_log_refs(root)] == [
        ("archive.zip", "process-a.log"),
        ("archive.zip", "process-z.log"),
        ("process-a.log.gz", None),
        ("process-z.log", None),
    ]


@pytest.mark.skipif(os.name != "nt", reason="directory fd API is unsupported on Windows")
def test_process_log_directory_fd_read_refuses_unsupported_platform() -> None:
    from agent_logger.process_logs import open_regular_at

    with pytest.raises(OSError, match="requires POSIX"):
        with open_regular_at(-1, NAME):
            pytest.fail("unsupported directory fd read was admitted")


@pytest.mark.skipif(os.name == "nt", reason="POSIX safety flag contract")
@pytest.mark.parametrize("flag", ["O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"])
def test_process_log_missing_safety_flags_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str,
) -> None:
    ref = _evidence(tmp_path / "logs", "raw", PAYLOAD)
    monkeypatch.delattr(os, flag, raising=False)
    with pytest.raises(OSError, match=flag):
        list(ref.iter_lines())


@pytest.mark.skipif(os.name != "nt", reason="native Windows rename-sharing guarantee")
@pytest.mark.parametrize("operation", ["enumerate", "read", "sync"])
def test_process_log_source_roots_native_ancestor_rename_is_denied_during_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    from agent_logger.sync import archive_directory as directory_module

    root = tmp_path / "ancestor" / "logs"
    ref = _evidence(root, "raw", PAYLOAD)
    native_directory = directory_module._windows_directory
    blocked = False

    @contextmanager
    def checked_directory(path: Path) -> Iterator[None]:
        nonlocal blocked
        with native_directory(path):
            if path == root.resolve():
                with pytest.raises(PermissionError):
                    root.parent.rename(tmp_path / "renamed")
                blocked = True
            yield

    monkeypatch.setattr(directory_module, "_windows_directory", checked_directory)
    if operation == "enumerate":
        assert list(iter_process_log_refs(root)) == [ref]
    elif operation == "read":
        assert "".join(ref.iter_lines()).encode() == PAYLOAD
    else:
        result = LocalTarget({"path": str(tmp_path / "destination")}).push_process_logs(
            root, "example",
        )
        assert result.ok and result.file_count == 1
    assert blocked
    assert ref.path.read_bytes() == PAYLOAD
    root.parent.rename(tmp_path / "renamed")


@pytest.mark.skipif(os.name != "nt", reason="native Windows handle lifetime")
@pytest.mark.parametrize("codec", ["raw", "gzip", "zip"])
def test_process_log_source_roots_native_enumeration_yield_releases_handles(
    tmp_path: Path, codec: str,
) -> None:
    root = tmp_path / "ancestor" / "logs"
    ref = _evidence(root, codec, PAYLOAD)
    iterator = iter_process_log_refs(root)
    assert next(iterator) == ref
    ref.path.rename(root / "parked-evidence")
    root.parent.rename(tmp_path / "renamed")
    iterator.close()


@pytest.mark.skipif(os.name != "nt", reason="native Windows listing-to-ZIP boundary")
def test_process_log_zip_replacement_between_validation_and_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.archive_directory import ArchiveDirectory

    root = tmp_path / "logs"
    original = _evidence(root, "zip", PAYLOAD)
    replacement = _evidence(tmp_path / "replacement", "zip", b"replacement\n")
    original_open = ArchiveDirectory.open
    replaced = False

    def opened(
        directory: ArchiveDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        nonlocal replaced
        if directory.path == root.resolve() and name == original.path.name and not replaced:
            original.path.rename(root / "retained.zip")
            replacement.path.rename(original.path)
            replaced = True
        return original_open(directory, name, protected=protected)

    monkeypatch.setattr(ArchiveDirectory, "open", opened)
    with pytest.raises(ValueError, match="changed while opening"):
        list(iter_process_log_refs(root))
    assert replaced


@pytest.mark.skipif(os.name != "nt", reason="native Windows listing-to-ZIP boundary")
def test_process_log_source_roots_native_zip_scan_remains_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.archive_directory import ArchiveDirectory

    root = tmp_path / "ancestor" / "logs"
    ref = _evidence(root, "zip", PAYLOAD)
    original_open = ArchiveDirectory.open
    checked: list[str] = []

    def opened(
        directory: ArchiveDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        with pytest.raises(PermissionError):
            root.parent.rename(tmp_path / "renamed")
        checked.append(name)
        return original_open(directory, name, protected=protected)

    monkeypatch.setattr(ArchiveDirectory, "open", opened)
    iterator = iter_process_log_refs(root)
    assert next(iterator) == ref
    assert checked == ["logs.zip"]
    root.parent.rename(tmp_path / "renamed")
    iterator.close()
