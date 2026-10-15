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


@pytest.mark.skipif(os.name == "nt", reason="real POSIX directory-fd rename/symlink race")
def test_process_log_copy_uses_pinned_directory_after_root_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger import process_logs
    from agent_logger.sync.archive_directory import ArchiveDirectory
    from agent_logger.sync.targets import filesystem

    root = tmp_path / "logs"
    _evidence(root, "raw", PAYLOAD)
    outside = tmp_path / "outside"
    _evidence(outside, "raw", b"outside evidence\n")
    dest = tmp_path / "destination"
    dest.mkdir()
    original_names = ArchiveDirectory.names
    swapped = False

    def names(directory: ArchiveDirectory) -> list[str]:
        nonlocal swapped
        found = original_names(directory)
        assert directory.fd is not None
        root.rename(tmp_path / "retained")
        root.symlink_to(outside, target_is_directory=True)
        assert directory.stat(NAME).st_ino == (tmp_path / "retained" / NAME).stat().st_ino
        swapped = True
        return found

    def forbidden_reopen(path: Path) -> None:
        pytest.fail("pinned process-log copy reopened the source directory")

    monkeypatch.setattr(ArchiveDirectory, "names", names)
    monkeypatch.setattr(process_logs, "open_root_dir", forbidden_reopen)
    with pytest.raises(filesystem._SourceChangedDuringCopy, match="root changed during copy"):
        filesystem._copy_process_logs(root, dest)
    assert swapped
    assert list(dest.iterdir()) == []
    assert (tmp_path / "retained" / NAME).read_bytes() == PAYLOAD
    assert (outside / NAME).read_bytes() == b"outside evidence\n"


@pytest.mark.skipif(os.name == "nt", reason="real POSIX root churn during an admitted transfer")
@pytest.mark.parametrize("timing", ["before_open", "before_commit"])
def test_process_log_copy_root_churn_stops_before_further_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timing: str,
) -> None:
    from agent_logger.sync.archive_directory import ArchiveDirectory
    from agent_logger.sync.targets import filesystem

    root = tmp_path / "logs"
    _evidence(root, "raw", PAYLOAD)
    (root / "process-z.log").write_bytes(PAYLOAD)
    outside = tmp_path / "outside"
    _evidence(outside, "raw", b"outside\n")
    dest = tmp_path / "destination"
    dest.mkdir()
    (dest / NAME).write_bytes(b"previously landed evidence\n")
    original_needs_copy = filesystem._needs_copy_from_stat
    original_copy = filesystem.shutil.copyfileobj
    original_open = ArchiveDirectory.open
    opened: list[str] = []
    swapped = False

    def swap() -> None:
        nonlocal swapped
        root.rename(tmp_path / "retained")
        root.symlink_to(outside, target_is_directory=True)
        swapped = True

    def needs_copy(before: os.stat_result, path: Path) -> bool:
        result = original_needs_copy(before, path)
        if timing == "before_open" and not swapped:
            swap()
        return result

    def copied(source: BinaryIO, target: BinaryIO, length: int) -> None:
        original_copy(source, target, length)
        if timing == "before_commit" and not swapped:
            swap()

    def observed_open(
        directory: ArchiveDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        opened.append(name)
        return original_open(directory, name, protected=protected)

    monkeypatch.setattr(filesystem, "_needs_copy_from_stat", needs_copy)
    monkeypatch.setattr(filesystem.shutil, "copyfileobj", copied)
    monkeypatch.setattr(ArchiveDirectory, "open", observed_open)
    with pytest.raises(filesystem._SourceChangedDuringCopy, match="root changed during copy"):
        filesystem._copy_process_logs(root, dest)
    assert swapped
    assert opened == ([] if timing == "before_open" else [NAME])
    assert (dest / NAME).read_bytes() == b"previously landed evidence\n"
    assert sorted(path.name for path in dest.iterdir()) == [NAME]


def test_process_log_copy_hands_off_same_live_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.targets import filesystem

    original_directory = filesystem.archive_directory
    boundaries: list[filesystem.ArchiveDirectory] = []
    live = False

    @contextmanager
    def observed_directory(path: Path) -> Iterator[filesystem.ArchiveDirectory]:
        nonlocal live
        with original_directory(path) as directory:
            boundaries.append(directory)
            live = True
            try:
                yield directory
            finally:
                live = False

    def observed_copy(
        directory: filesystem.ArchiveDirectory, dest: Path, before: os.stat_result,
    ) -> tuple[int, int, list[Path]]:
        assert live
        assert directory is boundaries[0]
        assert (before.st_dev, before.st_ino) == (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
        return 0, 0, []

    monkeypatch.setattr(filesystem, "archive_directory", observed_directory)
    monkeypatch.setattr(filesystem, "_copy_process_logs_pinned", observed_copy)
    assert filesystem._copy_process_logs(tmp_path, tmp_path / "destination") == (0, 0, [])
    assert not live


@pytest.mark.skipif(os.name == "nt", reason="POSIX incremental enumeration contract")
@pytest.mark.parametrize("codec", ["raw", "zip"])
def test_process_log_posix_enumeration_yields_before_later_archive_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codec: str,
) -> None:
    from agent_logger import process_logs

    root = tmp_path / "logs"
    first = _evidence(root, codec, PAYLOAD)
    if codec == "zip":
        first.path.rename(root / "a.zip")
        first = ProcessLogRef(root / "a.zip", NAME)
    (root / "z.zip").write_bytes(b"corrupt later archive")
    created: list[ProcessLogRef] = []
    original_ref = process_logs.ProcessLogRef

    def observed_ref(*args: object, **kwargs: object) -> ProcessLogRef:
        ref = original_ref(*args, **kwargs)
        created.append(ref)
        return ref

    monkeypatch.setattr(process_logs, "ProcessLogRef", observed_ref)
    iterator = iter_process_log_refs(root)
    try:
        assert next(iterator) == first
        assert created == [first]
        with pytest.raises(zipfile.BadZipFile):
            next(iterator)
    finally:
        iterator.close()


@pytest.mark.parametrize("codec", ["raw", "gzip", "zip"])
def test_process_log_relative_reference_and_enumerated_path_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codec: str,
) -> None:
    first = _evidence(tmp_path / "first" / "logs", codec, PAYLOAD)
    _evidence(tmp_path / "second" / "logs", codec, b"second working directory\n")
    relative = Path("logs") / first.path.name
    direct = ProcessLogRef(relative, first.member)
    monkeypatch.chdir(tmp_path / "first")
    assert direct.path == relative and not direct.path.is_absolute()
    assert direct.verified_root is None
    enumerated = list(iter_process_log_refs(Path("logs")))[0]
    assert enumerated.path == first.path and enumerated.path.is_absolute()
    monkeypatch.chdir(tmp_path / "second")
    assert "".join(direct.iter_lines()).encode() == b"second working directory\n"
    assert "".join(enumerated.iter_lines()).encode() == PAYLOAD
    assert direct.path == relative


@pytest.mark.parametrize("operation", ["direct", "enumerated", "zip"])
def test_process_log_entry_becoming_directory_after_stat_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    from agent_logger.sync.archive_directory import ArchiveDirectory

    ref = _evidence(tmp_path / "logs", "zip" if operation == "zip" else "raw", PAYLOAD)
    if operation == "enumerated":
        ref = list(iter_process_log_refs(ref.path.parent))[0]
    original_open = ArchiveDirectory.open
    raced = False

    def opened(
        directory: ArchiveDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        nonlocal raced
        if name == ref.path.name and not raced:
            ref.path.rename(tmp_path / "retained-evidence")
            ref.path.mkdir()
            raced = True
        return original_open(directory, name, protected=protected)

    monkeypatch.setattr(ArchiveDirectory, "open", opened)
    expected = OSError if os.name == "nt" else ValueError
    with pytest.raises(expected) as caught:
        if operation == "zip":
            list(iter_process_log_refs(ref.path.parent))
        else:
            list(ref.iter_lines())
    assert raced and ref.path.is_dir()
    assert (tmp_path / "retained-evidence").is_file()
    if os.name != "nt":
        assert "process-log evidence is not a regular file" in str(caught.value)
        assert ref.path.name in str(caught.value)
        assert isinstance(caught.value.__cause__, ValueError)
        assert "archive entry" in str(caught.value.__cause__)


@pytest.mark.parametrize(
    "reader",
    ["path", pytest.param("fd", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX fd API"))],
)
def test_process_log_entry_open_value_error_is_normalized_only_at_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str,
) -> None:
    from agent_logger import process_logs
    from agent_logger.sync.archive_directory import ArchiveDirectory

    ref = _evidence(tmp_path / "logs", "raw", PAYLOAD)
    origin = ValueError("archive entry is not a regular file")

    def rejected_open(
        directory: ArchiveDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        raise origin

    monkeypatch.setattr(ArchiveDirectory, "open", rejected_open)
    with pytest.raises(ValueError, match="process-log evidence is not a regular file") as caught:
        if reader == "path":
            list(ref.iter_lines())
        else:
            with open_root_dir(ref.path.parent) as fd:
                with process_logs.open_regular_at(fd, ref.path.name):
                    pytest.fail("non-regular entry was admitted")
    assert caught.value.__cause__ is origin
    assert ref.path.name in str(caught.value)


@pytest.mark.parametrize(
    "reader",
    [
        "path", "directory",
        pytest.param("fd", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX fd API")),
    ],
)
def test_process_log_entry_consumer_value_error_is_not_normalized(
    tmp_path: Path, reader: str,
) -> None:
    from agent_logger import process_logs
    from agent_logger.sync.archive_directory import archive_directory

    ref = _evidence(tmp_path / "logs", "raw", PAYLOAD)
    consumer = ValueError("consumer failure")
    with pytest.raises(ValueError, match="consumer failure") as caught:
        if reader == "path":
            with process_logs._open_regular(ref.path):
                raise consumer
        else:
            with archive_directory(ref.path.parent) as directory:
                boundary = (
                    process_logs._open_regular_in(directory, ref.path.name)
                    if reader == "directory"
                    else process_logs.open_regular_at(directory.fd, ref.path.name)
                )
                with boundary:
                    raise consumer
    assert caught.value is consumer
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("operation", ["read", "copy"])
def test_process_log_root_identity_error_preserves_reason_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    from agent_logger import process_logs
    from agent_logger.sync import archive_directory as directory_module
    from agent_logger.sync.targets import filesystem

    def changed_directory(path: Path) -> None:
        raise directory_module.ArchiveRootChangedError("archive root changed while opening")

    target = directory_module if operation == "read" else filesystem
    monkeypatch.setattr(target, "archive_directory", changed_directory)
    expected = (
        directory_module.ArchiveRootChangedError
        if operation == "read" else filesystem._SourceChangedDuringCopy
    )
    with pytest.raises(expected, match="root changed while opening") as caught:
        if operation == "read":
            with process_logs._process_log_directory(tmp_path):
                pytest.fail("changed root was admitted")
        else:
            filesystem._copy_process_logs(tmp_path, tmp_path / "destination")
    assert str(tmp_path) in str(caught.value)
    assert "not a directory" not in str(caught.value)
    assert isinstance(caught.value.__cause__, directory_module.ArchiveRootChangedError)


def test_archive_directory_identity_check_uses_distinct_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.archive_directory import ArchiveRootChangedError, archive_directory

    original_lstat = Path.lstat

    def changed_identity(path: Path, *args: object, **kwargs: object) -> os.stat_result:
        metadata = original_lstat(path, *args, **kwargs)
        if path == tmp_path:
            fields = list(metadata)
            fields[1] += 1
            return os.stat_result(fields)
        return metadata

    monkeypatch.setattr(Path, "lstat", changed_identity)
    with pytest.raises(ArchiveRootChangedError, match="root changed while opening"):
        with archive_directory(tmp_path):
            pytest.fail("changed root was admitted")


def test_process_log_transfer_root_change_diagnostic_is_not_bad_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.archive_directory import ArchiveRootChangedError
    from agent_logger.sync.targets import filesystem

    def changed_directory(path: Path) -> None:
        raise ArchiveRootChangedError("archive root changed while opening")

    monkeypatch.setattr(filesystem, "archive_directory", changed_directory)
    result = LocalTarget({"path": str(tmp_path / "destination")}).push_process_logs(
        tmp_path, "example",
    )
    assert not result.ok
    assert "root changed while opening" in result.detail
    assert str(tmp_path) in result.detail
    assert "not a directory" not in result.detail


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


@pytest.mark.parametrize("codec", ["raw", "gzip", "zip"])
def test_process_log_configured_path_identity_does_not_redirect_reader_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codec: str,
) -> None:
    from agent_logger import process_logs

    monkeypatch.setattr(process_logs, "supports_dir_fd", lambda: False)
    original = _evidence(tmp_path / "original" / "logs", codec, PAYLOAD)
    _evidence(tmp_path / "outside" / "logs", codec, b"outside\n")
    alias = tmp_path / "configured"
    try:
        alias.symlink_to(tmp_path / "original", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    requested = alias / "logs"
    refs = list(iter_process_log_refs(requested))
    assert refs == [ProcessLogRef(requested / original.path.name, original.member)]
    assert str(refs[0].path) == str(requested / original.path.name)
    assert refs[0].verified_root == original.path.parent.resolve()
    alias.rename(tmp_path / "retained-alias")
    alias.symlink_to(tmp_path / "outside", target_is_directory=True)
    assert "".join(refs[0].iter_lines()).encode() == PAYLOAD


@pytest.mark.skipif(os.name != "nt", reason="native Windows case-alias reference identity")
@pytest.mark.parametrize("codec", ["raw", "gzip", "zip"])
def test_process_log_source_roots_native_preserves_configured_path_spelling(
    tmp_path: Path, codec: str,
) -> None:
    original = _evidence(tmp_path / "logs", codec, PAYLOAD)
    requested = tmp_path / "LOGS"
    canonical = requested.resolve(strict=True)
    assert str(canonical) != str(requested)
    refs = list(iter_process_log_refs(requested))
    assert str(refs[0].path) == str(requested / original.path.name)
    assert refs[0].verified_root == canonical
    assert "".join(refs[0].iter_lines()).encode() == PAYLOAD


@pytest.mark.skipif(os.name != "nt", reason="native Windows single ancestor-pin operation")
def test_process_log_zip_enumeration_reuses_one_native_directory_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync import archive_directory as directory_module

    root = tmp_path / "logs"
    first = _evidence(root, "zip", PAYLOAD)
    with zipfile.ZipFile(root / "second.zip", "w") as archive:
        archive.writestr(NAME, PAYLOAD)
    original_directory = directory_module.archive_directory
    original_open = directory_module.ArchiveDirectory.open
    pins: list[Path] = []
    protections: list[bool] = []

    @contextmanager
    def observed_directory(path: Path) -> Iterator[directory_module.ArchiveDirectory]:
        pins.append(path)
        with original_directory(path) as directory:
            yield directory

    def opened(
        directory: directory_module.ArchiveDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        protections.append(protected)
        return original_open(directory, name, protected=protected)

    monkeypatch.setattr(directory_module, "archive_directory", observed_directory)
    monkeypatch.setattr(directory_module.ArchiveDirectory, "open", opened)
    iterator = iter_process_log_refs(root)
    assert next(iterator) == first
    assert pins == [root]
    assert protections == [True, True]
    root.rename(tmp_path / "retained")
    iterator.close()


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
