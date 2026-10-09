"""Native source-publication contracts for handle-owned Windows claim files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_logger.sync.provenance import open_regular_no_follow, windows_extended_path
from agent_logger.sync.targets.windows_claim_file import WindowsClaimFile

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows file handles")

_REPLACEMENT_PROBE = """
import json
import os
import sys

candidate, held = sys.argv[1:]
results = {}
try:
    fd = os.open(held, os.O_RDWR | os.O_BINARY)
except OSError as error:
    results["write_open"] = error.winerror
else:
    os.close(fd)
    results["write_open"] = None
try:
    os.replace(candidate, held)
except OSError as error:
    results["replace"] = error.winerror
else:
    results["replace"] = None
print(json.dumps(results))
"""


def _assert_replacement_blocked(candidate: Path, held: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _REPLACEMENT_PROBE,
            windows_extended_path(candidate),
            windows_extended_path(held),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    failures = json.loads(result.stdout)
    assert failures["write_open"] in (5, 32), result.stdout
    assert failures["replace"] in (5, 32), result.stdout
    assert candidate.read_bytes() == b"replacement"


def test_unpublished_claim_is_deleted_by_kernel_on_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_path_cleanup(*args: object, **kwargs: object) -> None:
        pytest.fail("claim cleanup must not unlink a pathname")

    with monkeypatch.context() as guard:
        guard.setattr(os, "unlink", reject_path_cleanup)
        guard.setattr(os, "remove", reject_path_cleanup)
        with WindowsClaimFile(tmp_path) as claim:
            claim.stream.write(b"unpublished")
            claim.stream.flush()
            temporary = list(tmp_path.glob(".claim-*.tmp"))
            assert len(temporary) == 1
            opened = os.fstat(claim.stream.fileno())
            assert claim.file_id == (opened.st_dev, opened.st_ino)
        assert claim.stream.closed
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("marker_name", ["marker.json", "marker-\U0001f680.json"])
def test_published_claim_survives_close_with_original_identity(
    tmp_path: Path, marker_name: str
) -> None:
    directory = tmp_path / "claims-\U0001f680"
    directory.mkdir()
    destination = directory / marker_name
    with WindowsClaimFile(directory) as claim:
        claim.stream.write(b"published")
        original_id = claim.file_id
        claim.publish(destination)
        assert not list(directory.glob(".claim-*.tmp"))
        assert not claim.stream.closed
        assert claim.file_id == original_id
        with open_regular_no_follow(destination) as reader:
            opened = os.fstat(reader.fileno())
            assert (opened.st_dev, opened.st_ino) == original_id
            assert reader.read() == b"published"
    assert claim.stream.closed
    with open_regular_no_follow(destination) as reader:
        opened = os.fstat(reader.fileno())
        assert (opened.st_dev, opened.st_ino) == original_id
        assert reader.read() == b"published"


def test_existing_marker_is_preserved_on_file_exists_error(tmp_path: Path) -> None:
    destination = tmp_path / "marker.json"
    destination.write_bytes(b"existing")
    with open_regular_no_follow(destination) as reader:
        opened = os.fstat(reader.fileno())
        existing_id = (opened.st_dev, opened.st_ino)
    with WindowsClaimFile(tmp_path) as claim:
        claim.stream.write(b"contender")
        with pytest.raises(FileExistsError):
            claim.publish(destination)
        assert not claim.stream.closed
        assert len(list(tmp_path.glob(".claim-*.tmp"))) == 1
        with open_regular_no_follow(destination) as reader:
            opened = os.fstat(reader.fileno())
            assert (opened.st_dev, opened.st_ino) == existing_id
            assert reader.read() == b"existing"
    assert claim.stream.closed
    assert not list(tmp_path.glob(".claim-*.tmp"))
    with open_regular_no_follow(destination) as reader:
        opened = os.fstat(reader.fileno())
        assert (opened.st_dev, opened.st_ino) == existing_id
        assert reader.read() == b"existing"


@pytest.mark.parametrize("phase", ["delete-pending", "publication-window"])
def test_separate_process_cannot_replace_held_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    candidate = tmp_path / "replacement.tmp"
    candidate.write_bytes(b"replacement")
    destination = tmp_path / "marker.json"
    with WindowsClaimFile(tmp_path) as claim:
        claim.stream.write(b"original")
        claim.stream.flush()
        original_id = claim.file_id
        temporary = list(tmp_path.glob(".claim-*.tmp"))
        assert len(temporary) == 1
        held = temporary[0]

        def check_held_file() -> None:
            _assert_replacement_blocked(candidate, held)
            assert claim.file_id == original_id
            assert list(tmp_path.glob(".claim-*.tmp")) == [held]
            claim.stream.seek(0)
            assert claim.stream.read() == b"original"

        if phase == "delete-pending":
            check_held_file()
        else:
            original_rename = claim._rename_by_handle
            checked = False

            def check_then_rename(target: Path) -> None:
                nonlocal checked
                assert not claim._delete_pending
                check_held_file()
                checked = True
                original_rename(target)

            monkeypatch.setattr(claim, "_rename_by_handle", check_then_rename)
            claim.publish(destination)
            assert checked
            assert claim.file_id == original_id
    assert not list(tmp_path.glob(".claim-*.tmp"))
    assert candidate.read_bytes() == b"replacement"
    if phase == "publication-window":
        assert destination.read_bytes() == b"original"
    else:
        assert not destination.exists()
