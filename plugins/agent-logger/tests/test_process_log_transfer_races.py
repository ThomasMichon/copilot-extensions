"""Per-entry source races must not suppress unrelated transfer errors."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO

import pytest

from agent_logger.sync.archive_directory import ArchiveDirectory
from agent_logger.sync.targets.filesystem import LocalTarget

pytestmark = [
    pytest.mark.guard,
    pytest.mark.contract("agent_logger.process_logs.transfer_races"),
    pytest.mark.skipif(os.name == "nt", reason="POSIX no-follow entry race"),
]


def test_process_log_transfer_skips_symlink_race_and_copies_other_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    victim = root / "process-a.log"
    victim.write_bytes(b"original")
    survivor = root / "process-b.log"
    survivor.write_bytes(b"survivor")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside evidence")
    destination = tmp_path / "destination"
    retained = destination / "example" / "logs" / victim.name
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"prior good evidence")
    original_open = ArchiveDirectory.open
    swapped = False

    def raced_open(
        directory: ArchiveDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        nonlocal swapped
        if name == victim.name and not swapped:
            victim.rename(root / "retained-original")
            victim.symlink_to(outside)
            swapped = True
        return original_open(directory, name, protected=protected)

    monkeypatch.setattr(ArchiveDirectory, "open", raced_open)
    result = LocalTarget({"path": str(destination)}).push_process_logs(root, "example")
    assert swapped and result.ok and result.file_count == 1
    assert retained.read_bytes() == b"prior good evidence"
    assert (retained.parent / survivor.name).read_bytes() == b"survivor"
    assert outside.read_bytes() == b"outside evidence"


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO])
def test_process_log_transfer_does_not_hide_other_open_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int,
) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    (root / "process-a.log").write_bytes(b"original")
    original_error = OSError(code, "source entry open failed")

    def failed_open(
        directory: ArchiveDirectory, name: str, *, protected: bool = False,
    ) -> BinaryIO:
        raise original_error

    monkeypatch.setattr(ArchiveDirectory, "open", failed_open)
    result = LocalTarget({"path": str(tmp_path / "destination")}).push_process_logs(
        root, "example",
    )
    assert not result.ok and "source entry open failed" in result.detail


def test_process_log_transfer_does_not_hide_destination_symlink_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.targets import filesystem

    root = tmp_path / "logs"
    root.mkdir()
    (root / "process-a.log").write_bytes(b"original")

    def rejected_publication(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ELOOP, "destination redirected")

    monkeypatch.setattr(filesystem, "_copy_stream_replace", rejected_publication)
    result = LocalTarget({"path": str(tmp_path / "destination")}).push_process_logs(
        root, "example",
    )
    assert not result.ok and "destination redirected" in result.detail
