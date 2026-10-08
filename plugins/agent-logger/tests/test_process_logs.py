from __future__ import annotations

import gzip
import os
import stat
import zipfile
from pathlib import Path

import pytest

from agent_logger.process_logs import ProcessLogRef, iter_process_log_refs

pytestmark = pytest.mark.contract("agent_logger.process_logs.evidence")

LOG_NAME = "process-1000-123.log"
PAYLOAD = 'header\r\n{"total_nano_aiu":123,"model":"example-model"}\nlast line'


def _write_log(root: Path, codec: str, payload: bytes) -> ProcessLogRef:
    if codec == "raw":
        path = root / LOG_NAME
        path.write_bytes(payload)
        return ProcessLogRef(path)
    if codec == "gzip":
        path = root / (LOG_NAME + ".gz")
        path.write_bytes(gzip.compress(payload))
        return ProcessLogRef(path)
    path = root / "logs.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(LOG_NAME, payload)
        archive.writestr("manifest.json", "{}")
    return ProcessLogRef(path, LOG_NAME)


@pytest.mark.parametrize("codec", ["raw", "gzip", "zip"])
def test_archive_transparent_stream_preserves_evidence(tmp_path: Path, codec: str) -> None:
    ref = _write_log(tmp_path, codec, PAYLOAD.encode())
    assert list(iter_process_log_refs(tmp_path)) == [ref]
    assert ref.logical_name == LOG_NAME
    assert "".join(ref.iter_lines()) == PAYLOAD
    assert not (tmp_path / "session-state").exists()


@pytest.mark.parametrize("codec", ["raw", "gzip", "zip"])
def test_line_bound_applies_to_uncompressed_bytes(tmp_path: Path, codec: str) -> None:
    ref = _write_log(tmp_path, codec, b"x" * 10)
    assert list(ref.iter_lines(max_line_bytes=10)) == ["x" * 10]
    with pytest.raises(ValueError, match="line exceeds 9 bytes"):
        list(ref.iter_lines(max_line_bytes=9))
    ref = _write_log(tmp_path, codec, b"\xff")
    with pytest.raises(UnicodeDecodeError):
        list(ref.iter_lines())


def test_aliases_are_observations_not_new_log_identities(tmp_path: Path) -> None:
    for codec in ("raw", "gzip", "zip"):
        _write_log(tmp_path, codec, PAYLOAD.encode())
    refs = list(iter_process_log_refs(tmp_path))
    assert len(refs) == 3
    assert {ref.logical_name for ref in refs} == {LOG_NAME}
    assert {"".join(ref.iter_lines()) for ref in refs} == {PAYLOAD}


def test_missing_source_is_not_an_empty_success(tmp_path: Path) -> None:
    assert list(iter_process_log_refs(tmp_path)) == []
    with pytest.raises(FileNotFoundError):
        list(iter_process_log_refs(tmp_path / "missing"))
    with pytest.raises(ValueError, match="not a directory"):
        list(iter_process_log_refs(_write_log(tmp_path, "raw", b"").path))


@pytest.mark.parametrize("name", ["../process-1.log", "/process-1.log", "logs/process-1.log"])
def test_zip_rejects_nonflat_log_members(tmp_path: Path, name: str) -> None:
    path = tmp_path / "logs.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"evidence")
    with pytest.raises(ValueError, match="members must be flat"):
        list(iter_process_log_refs(tmp_path))


def test_zip_rejects_ambiguous_or_symlink_members(tmp_path: Path) -> None:
    path = tmp_path / "logs.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(LOG_NAME, b"one")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr(LOG_NAME, b"two")
    with pytest.raises(ValueError, match="duplicate"):
        list(ProcessLogRef(path, LOG_NAME).iter_lines())
    link = zipfile.ZipInfo(LOG_NAME)
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(link, b"target")
    with pytest.raises(ValueError, match="symlink"):
        list(iter_process_log_refs(tmp_path))


def test_corrupt_and_missing_members_fail_loudly(tmp_path: Path) -> None:
    path = tmp_path / (LOG_NAME + ".gz")
    path.write_bytes(b"not gzip")
    with pytest.raises(gzip.BadGzipFile):
        list(ProcessLogRef(path).iter_lines())
    path = tmp_path / "logs.zip"
    path.write_bytes(b"not zip")
    with pytest.raises(zipfile.BadZipFile):
        list(iter_process_log_refs(tmp_path))
    with zipfile.ZipFile(path, "w"):
        pass
    with pytest.raises(ValueError, match="member is missing"):
        list(ProcessLogRef(path, LOG_NAME).iter_lines())


@pytest.mark.skipif(os.name == "nt", reason="native symlink creation requires host privileges")
def test_source_symlinks_are_not_followed(tmp_path: Path) -> None:
    source = tmp_path / "evidence"
    source.write_bytes(b"private")
    path = tmp_path / LOG_NAME
    path.symlink_to(source)
    with pytest.raises(ValueError, match="not a regular file"):
        list(ProcessLogRef(path).iter_lines())
    root = tmp_path / "alias"
    root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="not a directory"):
        list(iter_process_log_refs(root))


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_line_limits_are_errors(tmp_path: Path, limit: int) -> None:
    ref = _write_log(tmp_path, "raw", b"data")
    with pytest.raises(ValueError, match="positive integer"):
        list(ref.iter_lines(max_line_bytes=limit))
