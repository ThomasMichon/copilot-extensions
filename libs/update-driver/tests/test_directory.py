"""Queue-directory creation is private and never rewrites an existing root."""

from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from update_driver._directory import create_queue_directory


def test_directory_creation_is_idempotent_and_preserves_existing_data(tmp_path: Path) -> None:
    root = tmp_path / "parent" / "queue"
    create_queue_directory(root)
    entry = root / "retained"
    entry.write_bytes(b"unchanged")
    before = root.stat()
    create_queue_directory(root)
    after = root.stat()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert entry.read_bytes() == b"unchanged"


def test_exclusive_creation_refuses_an_existing_directory(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    create_queue_directory(root, exist_ok=False)
    before = root.stat()
    with pytest.raises(FileExistsError):
        create_queue_directory(root, exist_ok=False)
    assert root.stat().st_ino == before.st_ino


def test_existing_regular_file_is_not_removed_or_adopted(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    root.write_bytes(b"unrelated")
    with pytest.raises(FileExistsError):
        create_queue_directory(root)
    assert root.read_bytes() == b"unrelated"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode semantics")
def test_posix_directory_is_private_and_existing_permissions_are_not_rewritten(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    create_queue_directory(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    root.chmod(0o750)
    create_queue_directory(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o750
