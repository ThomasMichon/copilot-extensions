"""Native exact-entry retirement proof, without enabling a sync deletion policy."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import BinaryIO

import pytest

from agent_logger.sync.archive_directory import archive_directory
from agent_logger.sync import log_retirement_file
from agent_logger.sync.log_archives import _fingerprint
from agent_logger.sync.log_retirement_file import LogRetirementRevision, retirement_file
from agent_logger.sync.provenance import windows_extended_path

pytestmark = [pytest.mark.guard, pytest.mark.contract("agent_logger.process_logs.retirement")]
native = pytest.mark.skipif(os.name != "nt", reason="native Windows sharing/disposition proof")
NAME = "process-1000-123.log"
CONTENT = b"preserved process evidence\n"


@native
def test_source_roots_retirement_policy_guard_runs_after_final_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, revision = _source(tmp_path)
    events = []
    original = log_retirement_file._hash_stream

    def hashed(raw: BinaryIO, size: int) -> str:
        digest = original(raw, size)
        events.append("hash")
        return digest

    @contextmanager
    def changed_policy() -> Iterator[None]:
        assert events == ["hash"]
        events.append("guard")
        if "hash" in events:
            raise ValueError("synthetic policy change during final hashing")
        yield

    monkeypatch.setattr(log_retirement_file, "_hash_stream", hashed)
    with archive_directory(tmp_path) as directory:
        with retirement_file(directory, NAME) as held:
            with pytest.raises(ValueError, match="policy change"):
                held.retire(revision, commit_guard=changed_policy())
            assert not held.committed
    assert events == ["hash", "guard"] and source.read_bytes() == CONTENT


def _source(tmp_path: Path) -> tuple[Path, LogRetirementRevision]:
    source = tmp_path / NAME
    source.write_bytes(CONTENT)
    return source, LogRetirementRevision(
        _fingerprint(source.stat()), hashlib.sha256(CONTENT).hexdigest(),
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX must not fall back to pathname deletion")
def test_source_roots_retirement_posix_is_explicitly_unsupported(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    with archive_directory(tmp_path) as directory:
        with pytest.raises(OSError, match="requires native Windows"):
            with retirement_file(directory, NAME):
                pytest.fail("unsupported retirement yielded a file")
    assert source.read_bytes() == CONTENT


@pytest.mark.parametrize("name", ["../process-1000-123.log", "process-1000-123.log.gz", ".tmp"])
def test_source_roots_retirement_rejects_nonproducer_names(tmp_path: Path, name: str) -> None:
    with archive_directory(tmp_path) as directory:
        with pytest.raises(ValueError, match="raw producer-log"):
            with retirement_file(directory, name):
                pytest.fail("invalid name yielded a file")


@native
def test_source_roots_retirement_abort_preserves_and_releases(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    with pytest.raises(RuntimeError, match="verification failed"):
        with archive_directory(tmp_path) as directory:
            with retirement_file(directory, NAME) as held:
                assert held.stream.read() == CONTENT
                raise RuntimeError("verification failed")
    assert source.read_bytes() == CONTENT
    source.rename(tmp_path / "retained.log")


@native
def test_source_roots_retirement_excludes_existing_writer(
    tmp_path: Path, native_file_writer: Callable[[Path], AbstractContextManager[int]],
) -> None:
    source, _ = _source(tmp_path)
    with native_file_writer(source) as writer:
        with archive_directory(tmp_path) as directory:
            with pytest.raises(OSError) as denied:
                with retirement_file(directory, NAME):
                    pytest.fail("retirement admitted an existing writable handle")
            assert denied.value.winerror == 32
        os.lseek(writer, 0, os.SEEK_END)
        os.write(writer, b"late")
    assert source.read_bytes() == CONTENT + b"late"
    with native_file_writer(source):
        pass


@native
def test_source_roots_retirement_excludes_delete_sharing_native_writer(
    tmp_path: Path, native_file_writer: Callable[[Path], AbstractContextManager[int]],
) -> None:
    source, _ = _source(tmp_path)
    with native_file_writer(source):
        pass
    with archive_directory(tmp_path) as directory:
        with retirement_file(directory, NAME):
            with pytest.raises(OSError) as denied:
                with native_file_writer(source):
                    pytest.fail("retirement admitted a DELETE-sharing writable handle")
            assert denied.value.winerror == 32
    with native_file_writer(source) as writer:
        os.lseek(writer, 0, os.SEEK_END)
        os.write(writer, b"late")
    assert source.read_bytes() == CONTENT + b"late"


@native
def test_source_roots_retirement_excludes_new_writers_and_name_changes(tmp_path: Path) -> None:
    source, revision = _source(tmp_path)
    with archive_directory(tmp_path) as directory:
        with retirement_file(directory, NAME) as held:
            for mode in ("ab", "wb", "r+b"):
                with pytest.raises(OSError):
                    with source.open(mode):
                        pytest.fail("retirement admitted a new writable handle")
            with pytest.raises(OSError):
                source.rename(tmp_path / "moved.log")
            with pytest.raises(OSError):
                source.unlink()
            held.retire(revision)
            with pytest.raises(RuntimeError, match="already committed"):
                held.retire(revision)
    assert not source.exists()


@native
def test_source_roots_retirement_pins_parent_until_abort(tmp_path: Path) -> None:
    root = tmp_path / "producer"
    root.mkdir()
    source, _ = _source(root)
    parked = tmp_path / "parked"
    with archive_directory(root) as directory:
        with retirement_file(directory, NAME):
            with pytest.raises(OSError):
                root.rename(parked)
    root.rename(parked)
    assert (parked / source.name).read_bytes() == CONTENT


@native
@pytest.mark.parametrize("change", ["identity", "bytes"])
def test_source_roots_retirement_rejects_changed_revision(tmp_path: Path, change: str) -> None:
    source, revision = _source(tmp_path)
    if change == "identity":
        source.rename(tmp_path / "previous.log")
        source.write_bytes(CONTENT)
    else:
        revision = LogRetirementRevision(revision.fingerprint, "0" * 64)
    with archive_directory(tmp_path) as directory:
        with retirement_file(directory, NAME) as held:
            with pytest.raises(ValueError, match="changed"):
                held.retire(revision)
    assert source.read_bytes() == CONTENT


@native
def test_source_roots_retirement_readonly_failure_preserves_attributes(tmp_path: Path) -> None:
    source, revision = _source(tmp_path)
    source.chmod(stat.S_IREAD)
    try:
        with pytest.raises(OSError):
            with archive_directory(tmp_path) as directory:
                with retirement_file(directory, NAME) as held:
                    held.retire(revision)
        assert source.read_bytes() == CONTENT
        assert source.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    finally:
        source.chmod(stat.S_IWRITE | stat.S_IREAD)


@native
def test_source_roots_retirement_rejects_hardlinks(tmp_path: Path) -> None:
    source, _ = _source(tmp_path)
    alias = tmp_path / "alias.log"
    os.link(source, alias)
    with archive_directory(tmp_path) as directory:
        with pytest.raises(ValueError, match="exactly one"):
            with retirement_file(directory, NAME):
                pytest.fail("hardlinked source admitted")
    assert source.read_bytes() == alias.read_bytes() == CONTENT


@native
def test_source_roots_retirement_disposition_failure_never_unlinks_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, revision = _source(tmp_path)

    def fail() -> None:
        raise OSError("disposition rejected")

    with archive_directory(tmp_path) as directory:
        with retirement_file(directory, NAME) as held:
            monkeypatch.setattr(held, "_delete_by_handle", fail)
            with pytest.raises(OSError, match="disposition rejected"):
                held.retire(revision)
    assert source.read_bytes() == CONTENT


@native
def test_source_roots_retirement_supports_long_source_path(tmp_path: Path) -> None:
    root = tmp_path.joinpath(*("long-process-evidence" for _ in range(16)))
    Path(windows_extended_path(root)).mkdir(parents=True)
    source = Path(windows_extended_path(root / NAME))
    source.write_bytes(CONTENT)
    revision = LogRetirementRevision(
        _fingerprint(source.stat()), hashlib.sha256(CONTENT).hexdigest(),
    )
    assert len(str(root / NAME)) > 260
    with archive_directory(root) as directory:
        with retirement_file(directory, NAME) as held:
            held.retire(revision)
    assert not source.exists()
