"""Exact-ZIP fanout never transfers neighboring evidence or replaces a ZIP."""

from __future__ import annotations

import hashlib
import os
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import pytest

from agent_logger.sync import log_archive_publication as publication
from agent_logger.sync.log_archive_readback import (
    LogArchiveReadbackResult, LogArchiveRevision, ProcessLogReadback,
)
from agent_logger.sync.publication_directory import PublicationDirectory
from agent_logger.sync.targets.claim_file import ClaimFile, LinuxClaimFile

pytestmark = [pytest.mark.guard, pytest.mark.contract("agent_logger.process_logs.publication")]
NAME = "process-1000-123.log"
CONTENT = b"preserved process evidence\n"
REVISION = LogArchiveRevision(NAME, hashlib.sha256(CONTENT).hexdigest(), len(CONTENT))


def _zip(root: Path, content: bytes = CONTENT) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / REVISION.archive_name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(NAME, content)
    return path


def test_source_roots_publication_copies_exact_zip_only_and_retries(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = _zip(source)
    for name in [NAME, NAME + ".gz", "other.zip", "events.jsonl"]:
        (source / name).write_bytes(b"not selected")
    targets = tuple(
        ProcessLogReadback(name, tmp_path / name / "machine" / "logs")
        for name in ["first", "second"]
    )
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    results = publication.publish_required_log_archives(source, targets, REVISION)
    assert len(results) == 2
    assert all(item.supported and item.published and item.verified for item in results)
    assert all(item.revision == REVISION for item in results)
    identities = []
    for target in targets:
        assert target.directory is not None
        assert [path.name for path in target.directory.iterdir()] == [REVISION.archive_name]
        copied = target.directory / REVISION.archive_name
        assert copied.read_bytes() == archive.read_bytes()
        identities.append(copied.stat().st_ino)
    again = publication.publish_required_log_archives(source, targets, REVISION)
    assert all(item.verified and not item.published for item in again)
    assert identities == [
        (target.directory / REVISION.archive_name).stat().st_ino for target in targets
        if target.directory is not None
    ]
    assert before == {path.name: path.read_bytes() for path in source.iterdir()}
    assert publication.publish_required_log_archives(source, (), REVISION) == ()


@pytest.mark.parametrize("existing", [False, True])
def test_source_roots_publication_dry_run_has_no_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    source = tmp_path / "source"
    _zip(source)
    destination = tmp_path / "missing" / "logs"
    if existing:
        _zip(destination)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("dry run attempted mkdir, fsync or claim creation")

    monkeypatch.setattr(os, "mkdir", forbidden)
    monkeypatch.setattr(os, "fsync", forbidden)
    monkeypatch.setattr(publication, "create_claim_file", forbidden)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION, dry_run=True,
    )
    assert result.verified == existing and not result.published
    assert before == sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))


@pytest.mark.parametrize("kind", ["source", "target"])
def test_source_roots_publication_corruption_never_replaces_evidence(
    tmp_path: Path, kind: str,
) -> None:
    source, destination = tmp_path / "source", tmp_path / "target"
    _zip(source, b"x" * len(CONTENT) if kind == "source" else CONTENT)
    if kind == "target":
        _zip(destination, b"x" * len(CONTENT))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.zip")}
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert not result.verified and not result.published
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*.zip")}


def test_source_roots_publication_failure_does_not_skip_later_targets(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _zip(source)
    offline = tmp_path / "offline"
    offline.write_bytes(b"not a directory")
    targets = (
        ProcessLogReadback("unsupported"),
        ProcessLogReadback("offline", offline / "logs"),
        ProcessLogReadback("good", tmp_path / "good"),
    )
    results = publication.publish_required_log_archives(source, targets, REVISION)
    assert [item.supported for item in results] == [False, True, True]
    assert [item.verified for item in results] == [False, False, True]
    assert [item.published for item in results] == [False, False, True]
    assert str(tmp_path) not in repr(results)
    assert offline.read_bytes() == b"not a directory"


@pytest.mark.parametrize("suffix", ["", "nested"])
def test_source_roots_publication_rejects_source_overlap(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / "source"
    _zip(source)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", source / suffix), REVISION,
    )
    assert not result.verified and not result.published
    assert sorted(path.name for path in source.iterdir()) == [REVISION.archive_name]


@pytest.mark.skipif(os.name == "nt", reason="POSIX claim publication seam")
def test_source_roots_publication_no_replace_on_concurrent_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination = tmp_path / "source", tmp_path / "target"
    _zip(source)
    original = LinuxClaimFile.publish

    def raced(self: LinuxClaimFile, path: Path) -> None:
        path.write_bytes(b"concurrent evidence")
        original(self, path)

    monkeypatch.setattr(LinuxClaimFile, "publish", raced)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert not result.verified
    assert (destination / REVISION.archive_name).read_bytes() == b"concurrent evidence"
    assert sorted(path.name for path in destination.iterdir()) == [REVISION.archive_name]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits opened ZIP mutation")
@pytest.mark.parametrize("mutation", ["rewrite", "truncate", "replace"])
def test_source_roots_publication_source_changes_cancel_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    source, destination = tmp_path / "source", tmp_path / "target"
    archive = _zip(source)
    original = publication._hash_stream
    calls = 0

    def changed(raw: BinaryIO, size: int) -> str:
        nonlocal calls
        result = original(raw, size)
        calls += 1
        if calls == 1:
            if mutation == "replace":
                archive.rename(source / "parked.zip")
                _zip(source)
            else:
                archive.write_bytes(b"x" * size if mutation == "rewrite" else b"x")
        return result

    monkeypatch.setattr(publication, "_hash_stream", changed)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert not result.verified and not result.published
    assert not (destination / REVISION.archive_name).exists()
    assert archive.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits pinned ancestor replacement")
@pytest.mark.parametrize("which", ["source", "target"])
@pytest.mark.parametrize("valid_replacement", [False, True])
def test_source_roots_publication_ancestor_swap_is_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, valid_replacement: bool,
) -> None:
    source, destination = tmp_path / "source", tmp_path / "target"
    _zip(source)
    original = LinuxClaimFile.publish
    parked = tmp_path / "parked"
    changed = source if which == "source" else destination

    def swapped(self: LinuxClaimFile, path: Path) -> None:
        changed.rename(parked)
        changed.mkdir()
        if valid_replacement:
            _zip(changed)
        original(self, path)

    monkeypatch.setattr(LinuxClaimFile, "publish", swapped)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert not result.verified
    assert (parked / REVISION.archive_name).is_file()
    assert sorted(path.name for path in changed.iterdir()) == (
        [REVISION.archive_name] if valid_replacement else []
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits replacement between directory pins")
def test_source_roots_publication_binds_readback_guard_to_original_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, parked = (
        tmp_path / "source", tmp_path / "target", tmp_path / "parked",
    )
    _zip(source)
    original = publication.publication_directory

    @contextmanager
    def swapped(
        path: Path, *, create: bool = True,
    ) -> Iterator[PublicationDirectory]:
        if path == destination and not create:
            destination.rename(parked)
            _zip(destination)
        with original(path, create=create) as root:
            yield root

    monkeypatch.setattr(publication, "publication_directory", swapped)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert not result.verified and not result.published
    assert list(parked.iterdir()) == []
    assert (destination / REVISION.archive_name).is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink rejection")
@pytest.mark.parametrize("which", ["source", "target", "entry"])
def test_source_roots_publication_rejects_symlink_redirects(
    tmp_path: Path, which: str,
) -> None:
    source, destination, outside = (
        tmp_path / "source", tmp_path / "target", tmp_path / "outside",
    )
    _zip(source)
    good = _zip(outside)
    if which == "entry":
        (source / REVISION.archive_name).rename(source / "parked.zip")
        (source / REVISION.archive_name).symlink_to(good)
    elif which == "source":
        source.rename(tmp_path / "parked")
        source.symlink_to(outside, target_is_directory=True)
    else:
        destination.symlink_to(outside, target_is_directory=True)
    before = good.read_bytes()
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert not result.verified and good.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="native Windows protected ZIP and ancestors")
def test_source_roots_publication_windows_releases_claim_before_fresh_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination = tmp_path / "source", tmp_path / "target"
    archive = _zip(source)
    original = publication._publish_opened

    def checked(
        root: PublicationDirectory, raw: BinaryIO, selected: os.stat_result,
        output: PublicationDirectory, revision: LogArchiveRevision,
    ) -> bool:
        for path in [root.path, output.path, archive]:
            with pytest.raises(OSError):
                path.rename(tmp_path / "blocked")
        with pytest.raises(OSError):
            archive.open("r+b")
        return original(root, raw, selected, output, revision)

    monkeypatch.setattr(publication, "_publish_opened", checked)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert result.published and result.verified
    archive.rename(source / "released.zip")
    destination.rename(tmp_path / "released")


@pytest.mark.parametrize("after_commit", [False, True])
def test_source_roots_publication_durability_failure_preserves_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_commit: bool,
) -> None:
    source, destination = tmp_path / "source", tmp_path / "target"
    archive = _zip(source)
    if os.name == "nt":
        from agent_logger.sync.targets.windows_claim_file import WindowsClaimFile
        claim_type = WindowsClaimFile
    else:
        claim_type = LinuxClaimFile
    original = claim_type.publish

    def failed(self: ClaimFile, path: Path) -> None:
        if after_commit:
            original(self, path)
        raise OSError("synthetic durability failure")

    monkeypatch.setattr(claim_type, "publish", failed)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert not result.verified
    assert archive.exists()
    assert list(destination.glob(".claim-*")) == []
    assert (destination / REVISION.archive_name).exists() == after_commit


def test_source_roots_publication_requires_post_claim_fresh_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination = tmp_path / "source", tmp_path / "target"
    _zip(source)
    original = ProcessLogReadback.verify

    def corrupt(
        self: ProcessLogReadback, revision: LogArchiveRevision,
    ) -> LogArchiveReadbackResult:
        copied = destination / revision.archive_name
        assert copied.exists()
        # This also proves no destination claim still prevents writable opens.
        copied.write_bytes(b"corrupt after publication")
        return original(self, revision)

    monkeypatch.setattr(ProcessLogReadback, "verify", corrupt)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert result.published and not result.verified
    assert "BadZipFile" in result.detail
    assert (destination / REVISION.archive_name).read_bytes() == b"corrupt after publication"
    assert (source / REVISION.archive_name).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits mutation of protected source")
def test_source_roots_publication_source_mutation_after_copy_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination = tmp_path / "source", tmp_path / "target"
    archive = _zip(source)
    original = publication._verify_zip_stream
    calls = 0

    def append_after_copy(raw: BinaryIO, name: str, digest: str, size: int) -> None:
        nonlocal calls
        original(raw, name, digest, size)
        calls += 1
        if calls == 2:
            with archive.open("ab") as writer:
                writer.write(b"late evidence")

    monkeypatch.setattr(publication, "_verify_zip_stream", append_after_copy)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), REVISION,
    )
    assert calls == 2 and not result.verified and not result.published
    assert archive.read_bytes().endswith(b"late evidence")
    assert list(destination.iterdir()) == []


def test_source_roots_publication_large_archive_copy_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"evidence\n" * (400 * 1024)
    revision = LogArchiveRevision(NAME, hashlib.sha256(content).hexdigest(), len(content))
    source, destination = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    archive = source / revision.archive_name
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(NAME, content)
    original = publication.create_claim_file
    sizes: list[int] = []

    @contextmanager
    def measured(
        directory: Path, *, directory_fd: int | None = None, mode: int = 0o644,
    ) -> Iterator[ClaimFile]:
        with original(directory, directory_fd=directory_fd, mode=mode) as claim:
            write = claim.stream.write

            def checked(data: bytes) -> int:
                sizes.append(len(data))
                assert len(data) <= 1024 * 1024
                return write(data)

            monkeypatch.setattr(claim.stream, "write", checked)
            yield claim

    monkeypatch.setattr(publication, "create_claim_file", measured)
    result = publication.publish_log_archive(
        source, ProcessLogReadback("target", destination), revision,
    )
    assert result.verified and len(sizes) >= 4
    assert sum(sizes) == archive.stat().st_size
    assert (destination / revision.archive_name).read_bytes() == archive.read_bytes()
