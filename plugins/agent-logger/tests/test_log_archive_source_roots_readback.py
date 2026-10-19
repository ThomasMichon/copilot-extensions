"""Full ZIP readback is independent of transfer success, stat deltas and caches."""

from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from pathlib import Path
from typing import BinaryIO

import pytest

from agent_logger.sync import log_archives
from agent_logger.sync.log_archive_readback import (
    LogArchiveRevision, ProcessLogReadback, verify_required_log_archives,
)
from agent_logger.sync.publication_directory import PublicationDirectory
from agent_logger.sync.targets import build_target

pytestmark = [pytest.mark.guard, pytest.mark.contract("agent_logger.process_logs.readback")]
NAME = "process-1000-123.log"
CONTENT = b"preserved process evidence\n"
REVISION = LogArchiveRevision(NAME, hashlib.sha256(CONTENT).hexdigest(), len(CONTENT))


def _zip(
    root: Path, content: bytes = CONTENT, *, name: str = NAME,
    revision: LogArchiveRevision = REVISION,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / revision.archive_name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, content)
    return path


def _readback(root: Path) -> ProcessLogReadback:
    return build_target("local", {"path": str(root)}).process_log_readback("example")


def test_source_roots_readback_verifies_every_target_and_rechecks_each_pass(
    tmp_path: Path,
) -> None:
    targets = (_readback(tmp_path / "first"), _readback(tmp_path / "second"))
    for target in targets:
        assert target.directory is not None
        _zip(target.directory)
    result = verify_required_log_archives(targets, REVISION)
    assert result.verified and len(result.results) == 2
    assert all(item.revision == REVISION and item.supported for item in result.results)
    second = targets[1].directory
    assert second is not None
    _zip(second, b"x" * len(CONTENT))
    changed = verify_required_log_archives(targets, REVISION)
    assert not changed.verified
    assert [item.verified for item in changed.results] == [True, False]
    assert not verify_required_log_archives((), REVISION).verified


@pytest.mark.parametrize("transport", ["ssh", "ssh-tunnel", "ingest"])
def test_source_roots_readback_unsupported_is_not_success(transport: str) -> None:
    result = build_target(transport).process_log_readback("example").verify(REVISION)
    assert not result.supported and not result.verified


@pytest.mark.parametrize("machine", ["", ".", "..", "namespace/example", "x\\y", "x:y"])
def test_source_roots_readback_filesystem_namespace_is_rejected(
    tmp_path: Path, machine: str,
) -> None:
    with pytest.raises(ValueError, match="local machine"):
        build_target("local", {"path": str(tmp_path)}).process_log_readback(machine)


@pytest.mark.parametrize("existing", [False, True])
def test_source_roots_readback_missing_target_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    target = _readback(tmp_path / "absent")
    if existing:
        assert target.directory is not None
        _zip(target.directory)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("readback attempted a directory creation or durability write")

    monkeypatch.setattr(os, "mkdir", forbidden)
    monkeypatch.setattr(os, "fsync", forbidden)
    result = target.verify(REVISION)
    assert result.supported and result.verified == existing
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before
    assert "absent" not in result.detail
    if not existing:
        assert "FileNotFoundError" in result.detail


def test_source_roots_readback_onedrive_resolves_stored_bytes_once(tmp_path: Path) -> None:
    target = build_target("onedrive", {"root": str(tmp_path), "subfolder": "sessions"})
    resolved = target.process_log_readback("example")
    assert resolved.directory == tmp_path / "sessions" / "example" / "logs"
    _zip(resolved.directory)
    target.options["root"] = str(tmp_path / "changed")
    assert resolved.verify(REVISION).verified
    assert not (tmp_path / "changed").exists()


def test_source_roots_readback_does_not_trust_identical_size_and_mtime(tmp_path: Path) -> None:
    path = _zip(tmp_path)
    before = path.stat()
    _zip(tmp_path, b"x" * len(CONTENT))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    result = ProcessLogReadback("local", tmp_path).verify(REVISION)
    assert not result.verified and "ValueError" in result.detail


@pytest.mark.parametrize("kind", [
    "member", "size", "truncated", "duplicate", "codec", "symlink_member", "crc",
])
def test_source_roots_readback_rejects_corrupt_or_inexact_archives(
    tmp_path: Path, kind: str,
) -> None:
    path = _zip(tmp_path)
    if kind in {"member", "size"}:
        _zip(tmp_path, CONTENT + b"x" if kind == "size" else CONTENT,
             name="process-2-3.log" if kind == "member" else NAME)
    elif kind == "truncated":
        path.write_bytes(path.read_bytes()[:25])
    elif kind == "duplicate":
        with zipfile.ZipFile(path, "a") as archive, pytest.warns(UserWarning):
            archive.writestr(NAME, CONTENT)
    elif kind == "crc":
        data = path.read_bytes()
        path.write_bytes(data.replace(CONTENT, b"x" * len(CONTENT)))
    else:
        with zipfile.ZipFile(path, "w") as archive:
            info = zipfile.ZipInfo(NAME)
            if kind == "codec":
                info.compress_type = zipfile.ZIP_BZIP2
            else:
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, CONTENT)
    assert not ProcessLogReadback("local", tmp_path).verify(REVISION).verified
    assert path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits truncating an opened readback file")
@pytest.mark.parametrize("operation", ["readback", "snapshot"])
def test_source_roots_readback_member_truncation_is_a_per_revision_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    content = b"x" * (3 * 1024 * 1024)
    revision = LogArchiveRevision(NAME, hashlib.sha256(content).hexdigest(), len(content))
    first, second = tmp_path / "first", tmp_path / "second"
    path = _zip(first, content, revision=revision)
    _zip(second, content, revision=revision)
    opened: list[str] = []
    original = zipfile.ZipFile.open

    def truncate_opened(
        self: zipfile.ZipFile, name: str | zipfile.ZipInfo, mode: str = "r",
        pwd: bytes | None = None, *, force_zip64: bool = False,
    ) -> BinaryIO:
        member = original(self, name, mode, pwd, force_zip64=force_zip64)
        if not opened:
            # Large stored data prevents header buffering from hiding this EOF.
            with path.open("r+b") as writable:
                writable.truncate(0)
        opened.append(mode)
        return member

    monkeypatch.setattr(zipfile.ZipFile, "open", truncate_opened)
    if operation == "readback":
        result = verify_required_log_archives((
            ProcessLogReadback("local", first), ProcessLogReadback("onedrive", second),
        ), revision)
        assert not result.verified
        assert [item.verified for item in result.results] == [False, True]
        assert "EOFError" in result.results[0].detail
        assert len(opened) == 2
    else:
        source = first / NAME
        source.write_bytes(content)
        os.utime(source, (1, 1))
        monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)
        snapshot = log_archives.archive_settled_process_logs(first, min_quiet_seconds=1)
        assert len(snapshot.failed) == 1 and snapshot.scanned == 1
        assert source.read_bytes() == content
    assert path.exists() and path.stat().st_size == 0


def test_source_roots_readback_reuses_snapshot_archive_naming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / NAME).write_bytes(CONTENT)
    os.utime(tmp_path / NAME, (1, 1))
    monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)
    assert log_archives.archive_settled_process_logs(
        tmp_path, min_quiet_seconds=1,
    ).archived == 1
    assert ProcessLogReadback("local", tmp_path).verify(REVISION).verified
    assert (tmp_path / NAME).read_bytes() == CONTENT


@pytest.mark.skipif(os.name == "nt", reason="POSIX entry replacement during a pinned read")
def test_source_roots_readback_rejects_entry_swap_during_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _zip(tmp_path)
    original = log_archives._verify_zip_stream

    def swapped(raw: BinaryIO, name: str, digest: str, size: int) -> None:
        original(raw, name, digest, size)
        path.rename(tmp_path / "parked.zip")
        _zip(tmp_path)

    monkeypatch.setattr(log_archives, "_verify_zip_stream", swapped)
    assert not ProcessLogReadback("local", tmp_path).verify(REVISION).verified
    assert path.exists() and (tmp_path / "parked.zip").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory descriptors permit ancestor rename")
@pytest.mark.parametrize("replacement", ["directory", "symlink"])
def test_source_roots_readback_rejects_ancestor_swap_during_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    destination = tmp_path / "destination"
    target = _readback(destination)
    assert target.directory is not None
    _zip(target.directory)
    parked = tmp_path / "parked"
    outside = tmp_path / "outside"
    outside.mkdir()
    original = log_archives._verify_zip_stream

    def swapped(raw: BinaryIO, name: str, digest: str, size: int) -> None:
        original(raw, name, digest, size)
        destination.rename(parked)
        if replacement == "directory":
            destination.mkdir()
        else:
            destination.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(log_archives, "_verify_zip_stream", swapped)
    result = target.verify(REVISION)
    assert not result.verified
    assert (parked / "example" / "logs" / REVISION.archive_name).is_file()
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink ancestor and entry rejection")
@pytest.mark.parametrize("kind", ["ancestor", "entry"])
def test_source_roots_readback_rejects_symlink_redirects(tmp_path: Path, kind: str) -> None:
    outside = tmp_path / "outside"
    good = _zip(outside)
    inside = tmp_path / "inside"
    if kind == "ancestor":
        inside.symlink_to(outside, target_is_directory=True)
    else:
        inside.mkdir()
        (inside / REVISION.archive_name).symlink_to(good)
    assert not ProcessLogReadback("local", inside).verify(REVISION).verified
    assert ProcessLogReadback("local", outside).verify(REVISION).verified


@pytest.mark.skipif(os.name != "nt", reason="native Windows ZIP sharing exclusion")
def test_source_roots_readback_windows_pins_ancestors_and_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _zip(tmp_path / "logs")
    original = PublicationDirectory.open

    def checked(
        self: PublicationDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        raw = original(self, name, protected=protected)
        try:
            with pytest.raises(OSError):
                self.path.rename(tmp_path / "renamed")
            with pytest.raises(OSError):
                path.rename(path.with_suffix(".moved"))
            return raw
        except BaseException:
            raw.close()
            raise

    monkeypatch.setattr(PublicationDirectory, "open", checked)
    assert ProcessLogReadback("local", path.parent).verify(REVISION).verified
    path.rename(path.with_suffix(".moved"))


@pytest.mark.parametrize("name,digest,size", [
    ("../" + NAME, REVISION.sha256, 1), (NAME + ".gz", REVISION.sha256, 1),
    (NAME, "x" * 64, 1), (NAME, REVISION.sha256, True), (NAME, REVISION.sha256, -1),
])
def test_source_roots_readback_invalid_revision_is_an_error(
    name: str, digest: str, size: int,
) -> None:
    with pytest.raises(ValueError):
        LogArchiveRevision(name, digest, size)
