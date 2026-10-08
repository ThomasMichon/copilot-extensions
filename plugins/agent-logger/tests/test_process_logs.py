from __future__ import annotations

import gzip
import os
import stat
import zipfile
from pathlib import Path

import pytest

from agent_logger.process_logs import ProcessLogRef, iter_process_log_refs

pytestmark = [pytest.mark.guard, pytest.mark.contract("agent_logger.process_logs.evidence")]

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


@pytest.mark.parametrize("name", ["notes.log", "logs/notes.log", "../notes.log"])
def test_zip_ignores_unrelated_log_members(tmp_path: Path, name: str) -> None:
    """A `.log` member whose leaf name doesn't match the process-log naming
    pattern is unrelated metadata, not a malformed/misplaced process log --
    ignore it regardless of nesting, rather than rejecting it as non-flat."""
    path = tmp_path / "logs.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"unrelated")
    assert list(iter_process_log_refs(tmp_path)) == []


@pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW directory pinning is POSIX-only")
def test_root_swapped_to_symlink_after_configuration_is_rejected(tmp_path: Path) -> None:
    """A deterministic stand-in for the race: once `log_root` names a symlink
    (whether swapped in after initial configuration or from the start), it
    must never be traversed -- enumeration is pinned to a directory handle
    opened with O_NOFOLLOW, not re-resolved by path for each entry."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = _write_log(outside, "raw", b"outside evidence")
    configured_root = tmp_path / "configured"
    configured_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="not a directory"):
        list(iter_process_log_refs(configured_root))
    # The underlying evidence is reachable directly, proving the rejection is
    # about traversal through the swapped root, not a missing/corrupt file.
    assert "".join(secret.iter_lines()) == "outside evidence"


@pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW directory pinning is POSIX-only")
def test_root_swapped_after_ref_obtained_is_rejected_on_read(tmp_path: Path) -> None:
    """A ref returned by `iter_process_log_refs` must re-verify the root at
    `iter_lines()` time too -- not only during the enumeration call that
    produced it -- since the two can be arbitrarily far apart in time."""
    root = tmp_path / "configured"
    root.mkdir()
    _write_log(root, "raw", PAYLOAD.encode())
    refs = list(iter_process_log_refs(root))
    assert len(refs) == 1
    outside = tmp_path / "outside"
    outside.mkdir()
    same_name_outside = outside / LOG_NAME
    same_name_outside.write_bytes(b"outside evidence")
    (root / LOG_NAME).unlink()
    root.rmdir()
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="not a directory"):
        list(refs[0].iter_lines())


@pytest.mark.skipif(os.name == "nt", reason="pinned absolute root is a POSIX-only guarantee")
def test_relative_root_is_pinned_against_a_later_chdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative `log_root` must resolve against the working directory at
    enumeration time, not whatever directory is current when a returned ref
    is later read -- an intervening chdir() must not redirect the read."""
    root = tmp_path / "configured"
    root.mkdir()
    _write_log(root, "raw", PAYLOAD.encode())
    monkeypatch.chdir(tmp_path)
    refs = list(iter_process_log_refs(Path("configured")))
    assert len(refs) == 1
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert "".join(refs[0].iter_lines()) == PAYLOAD


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


@pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW directory pinning is POSIX-only")
def test_open_regular_at_rejects_path_like_names(tmp_path: Path) -> None:
    """``open_regular_at`` is public -- shared with the sync path's
    process-log publication -- so it must enforce its own documented
    bare-filename contract itself, not merely rely on every caller to
    prefilter: an absolute name or a `../`-containing one would otherwise
    let `os.stat`/`os.open`'s `dir_fd` argument be silently ignored or
    bypassed, escaping the pinned directory."""
    from agent_logger.process_logs import open_regular_at, open_root_dir

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.log").write_bytes(b"private")
    root = tmp_path / "root"
    root.mkdir()
    (root / LOG_NAME).write_bytes(b"evidence")

    with open_root_dir(root) as root_fd:
        for bad_name in (
            str(outside / "secret.log"),  # absolute
            "../outside/secret.log",
            "sub/../../outside/secret.log",
            "sub\\..\\..\\outside\\secret.log",
        ):
            with pytest.raises(ValueError, match="bare filename"):
                with open_regular_at(root_fd, bad_name):
                    pass


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_line_limits_are_errors(tmp_path: Path, limit: int) -> None:
    ref = _write_log(tmp_path, "raw", b"data")
    with pytest.raises(ValueError, match="positive integer"):
        list(ref.iter_lines(max_line_bytes=limit))
