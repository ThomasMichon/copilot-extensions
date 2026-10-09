"""Native source-publication contracts for handle-owned Windows claim files."""

from __future__ import annotations

import errno
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
import ctypes
import json
import msvcrt
import os
import sys
from ctypes import wintypes


def failure(error: OSError) -> dict[str, object]:
    return {
        "succeeded": False,
        "errno": error.errno,
        "winerror": getattr(error, "winerror", None),
    }


def probe_write(fd: int) -> dict[str, object]:
    opened = os.fstat(fd)
    result = {
        "attempted": True,
        "file_id": [opened.st_dev, opened.st_ino],
    }
    try:
        written = os.write(fd, b"tampered")
    except OSError as error:
        result.update(failure(error))
    else:
        result.update({"succeeded": True, "bytes_written": written})
    return result

candidate, held = sys.argv[1:]
results = {
    "write": {"attempted": False},
    "native_write": {"attempted": False},
}
try:
    fd = os.open(held, os.O_RDWR | os.O_BINARY)
except OSError as error:
    results["write_open"] = failure(error)
else:
    try:
        results["write_open"] = {"succeeded": True}
        results["write"] = probe_write(fd)
    finally:
        os.close(fd)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
create_file = kernel32.CreateFileW
create_file.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
create_file.restype = wintypes.HANDLE
close_handle = kernel32.CloseHandle
close_handle.argtypes = [wintypes.HANDLE]
close_handle.restype = wintypes.BOOL

# Share the owner's write/delete access so only its share mode denies this open.
handle = create_file(held, 0x80000000 | 0x40000000, 7, None, 3, 0x80 | 0x00200000, None)
if handle == wintypes.HANDLE(-1).value:
    results["native_write_open"] = failure(ctypes.WinError(ctypes.get_last_error()))
else:
    fd = None
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        results["native_write_open"] = {"succeeded": True}
        results["native_write"] = probe_write(fd)
    finally:
        if fd is not None:
            os.close(fd)
        elif not close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

try:
    os.replace(candidate, held)
except OSError as error:
    results["replace"] = failure(error)
else:
    results["replace"] = {"succeeded": True}
print(json.dumps(results))
"""


def _assert_replacement_blocked(candidate: Path, held: Path, original_id: tuple[int, int]) -> None:
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
    for operation in ("write", "native_write"):
        write = failures[operation]
        if write["attempted"]:
            assert tuple(write["file_id"]) == original_id, result.stdout
            assert not write["succeeded"], result.stdout
    assert not failures["write_open"]["succeeded"], result.stdout
    assert failures["write_open"]["errno"] in (errno.EACCES, errno.EPERM), result.stdout
    assert not failures["native_write_open"]["succeeded"], result.stdout
    assert failures["native_write_open"]["winerror"] in (5, 32), result.stdout
    assert not failures["replace"]["succeeded"], result.stdout
    assert failures["replace"]["winerror"] in (5, 32), result.stdout
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
            _assert_replacement_blocked(candidate, held, original_id)
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
