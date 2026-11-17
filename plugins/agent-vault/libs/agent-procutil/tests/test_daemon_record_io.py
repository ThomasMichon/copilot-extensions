"""Registry snapshots must not deny a concurrent canonical record replacement."""

import os
from pathlib import Path
import threading

import pytest

from agent_procutil import _daemon_instance_store as store
from agent_procutil._daemon_instance_io import _open_snapshot
from agent_procutil._daemon_instance_owner import mkdir_private


@pytest.mark.parametrize("cursor", [False, True])
def test_record_reader_allows_atomic_replacement_while_its_snapshot_is_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cursor: bool,
) -> None:
    root = tmp_path / "records"
    mkdir_private(root)
    key = "a" * 32
    path = root / (key + ".instance")
    before = {"key": key} if cursor else {"schema": "1", "instance_id": key, "state": "reserved"}
    after = {"key": "b" * 32} if cursor else {**before, "state": "running"}

    def read() -> dict[str, str]:
        return {"key": store._read_cursor(root)} if cursor else store._read(path)

    def write(record: dict[str, str]) -> None:
        if cursor:
            store._write_cursor(root, record["key"])
        else:
            store._write(path, record)

    write(before)
    opened, release = threading.Event(), threading.Event()
    reader_identity: list[int] = []
    snapshots: list[dict[str, str]] = []
    failures: list[BaseException] = []
    original_fstat = os.fstat

    def hold_open_snapshot(descriptor: int) -> os.stat_result:
        info = original_fstat(descriptor)
        if reader_identity and threading.get_ident() == reader_identity[0]:
            opened.set()
            if not release.wait(5):
                raise TimeoutError("record reader release was not observed")
        return info

    def snapshot() -> None:
        reader_identity.append(threading.get_ident())
        try:
            snapshots.append(read())
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(os, "fstat", hold_open_snapshot)
    reader = threading.Thread(target=snapshot)
    reader.start()
    try:
        assert opened.wait(5), "the record snapshot was not opened"
        write(after)
    finally:
        release.set()
        reader.join(timeout=5)
        assert not reader.is_alive(), "the owned record reader did not settle"
    assert not failures
    assert snapshots == [before]
    assert read() == after


@pytest.mark.parametrize("suffix", ["a", "ab", "abc", "abcd", "\u00e9\U0001f600"])
def test_record_publication_preserves_the_exact_target_name(tmp_path: Path, suffix: str) -> None:
    root = tmp_path / suffix
    mkdir_private(root)
    key = "a" * 32
    path = root / (key + ".instance")
    record = {"schema": "1", "instance_id": key}
    store._write(path, record)
    assert set(root.iterdir()) == {path}
    assert store._read(path) == record
    store._write(path, {**record, "state": "running"})
    assert set(root.iterdir()) == {path}
    assert store._read(path) == {**record, "state": "running"}


def test_record_snapshot_is_read_only_and_noninheritable(tmp_path: Path) -> None:
    path = tmp_path / "snapshot"
    path.write_bytes(b"snapshot")
    with _open_snapshot(path) as stream:
        assert stream.read() == b"snapshot"
        assert not os.get_inheritable(stream.fileno())
        with pytest.raises(OSError):
            os.write(stream.fileno(), b"changed")
    assert path.read_bytes() == b"snapshot"


@pytest.mark.skipif(os.name != "nt", reason="actual Windows denied delete sharing")
def test_record_replacement_does_not_override_a_reader_denying_delete(tmp_path: Path) -> None:
    key = "a" * 32
    path = tmp_path / (key + ".instance")
    record = {"schema": "1", "instance_id": key, "state": "reserved"}
    store._write(path, record)
    with path.open("rb"):
        with pytest.raises(PermissionError):
            store._write(path, {**record, "state": "running"})
    assert store._read(path) == record
    assert not list(tmp_path.glob("*.pending"))
