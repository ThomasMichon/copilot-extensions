"""Explicit real-reader qualification, outside the actor-free unit packet."""
from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import versioned_runtime_fingerprint as fingerprint


pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
    pytest.mark.timeout(30),
]


def _expected_digest(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(contents):
        for field in (name.encode("utf-8"), b"file", contents[name]):
            digest.update(len(field).to_bytes(8, "big"))
            digest.update(field)
    return digest.hexdigest()


def _fingerprint(
    paths: Sequence[Path], *,
    label: Callable[[Path], str] = lambda path: path.name,
    file_hashes: MutableMapping[Path, str] | None = None,
    on_file: Callable[[Path, bytes], None] | None = None,
    on_progress: Callable[[], None] | None = None,
    io_path: Callable[[Path], Path] = lambda path: path,
    reported_path: Callable[[Path], Path] = lambda path: path,
) -> str:
    return fingerprint.fingerprint_entries(
        {path: "file" for path in paths}, label=label,
        root_label=lambda path: path.as_posix(), roots={path.parent for path in paths},
        normalize_link=lambda target: target, path_type=Path,
        file_hashes=file_hashes, on_file=on_file, on_progress=on_progress,
        io_path=io_path, reported_path=reported_path,
    )


class _GatedReads:
    def __init__(
        self, root: Path, monkeypatch: pytest.MonkeyPatch, *, fail_first: bool = False,
    ) -> None:
        self.paths = tuple(root / f"{index}.py" for index in range(6))
        self.contents = {path.name: path.name.encode("utf-8") + b"\0" for path in self.paths}
        for path in self.paths:
            path.write_bytes(self.contents[path.name])
        self.started = {path: threading.Event() for path in self.paths}
        self.release = {path: threading.Event() for path in self.paths[:4]}
        self.initial_started = threading.Event()
        self.completed: set[Path] = set()
        self.active = 0
        self.peak = 0
        lock = threading.Lock()
        original_read = Path.read_bytes

        def read(path: Path) -> bytes:
            if path not in self.started:
                return original_read(path)
            with lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.started[path].set()
                if all(self.started[entry].is_set() for entry in self.paths[:4]):
                    self.initial_started.set()
            try:
                gate = self.release.get(path)
                if gate is not None:
                    assert gate.wait(10), f"fixture did not release {path.name}"
                if fail_first and path == self.paths[0]:
                    raise OSError("fixture read refusal")
                return original_read(path)
            finally:
                with lock:
                    self.active -= 1
                    self.completed.add(path)

        monkeypatch.setattr(Path, "read_bytes", read)

    def release_all(self) -> None:
        for event in self.release.values():
            event.set()


def test_cached_labels_preserve_framing_serial_callbacks_and_fresh_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk = tmp_path / "physical"
    disk.mkdir()
    (disk / "a.py").write_bytes(b"a\0")
    (disk / "b.py").write_bytes(b"before callback")
    paths = (tmp_path / "declared" / "b.py", tmp_path / "declared" / "a.py")
    reported = tmp_path / "reported"
    owner = threading.get_ident()
    calls: dict[Path, int] = {}
    observations: list[tuple[Path, bytes]] = []
    readers: list[int] = []
    hashes: dict[Path, str] = {}
    mutate = True
    original_read = Path.read_bytes

    def read(path: Path) -> bytes:
        if path.parent == disk:
            readers.append(threading.get_ident())
        return original_read(path)

    def label(path: Path) -> str:
        calls[path] = calls.get(path, 0) + 1
        return path.name

    def observe(path: Path, data: bytes) -> None:
        assert threading.get_ident() == owner
        observations.append((path, data))
        if mutate and path.name == "a.py":
            (disk / "b.py").write_bytes(b"after callback")

    monkeypatch.setattr(Path, "read_bytes", read)
    actual = _fingerprint(
        paths, label=label, file_hashes=hashes, on_file=observe,
        io_path=lambda path: disk / path.name,
        reported_path=lambda path: reported / path.name,
    )
    expected = {"a.py": b"a\0", "b.py": b"after callback"}
    assert actual == _expected_digest(expected)
    assert calls == dict.fromkeys(paths, 1)
    assert observations == [(reported / name, expected[name]) for name in sorted(expected)]
    assert hashes == {
        reported / name: hashlib.sha256(data).hexdigest() for name, data in expected.items()
    }
    assert readers == [owner, owner]

    mutate = False
    calls.clear()
    observations.clear()
    hashes.clear()
    (disk / "b.py").write_bytes(b"fresh content")
    fresh = _fingerprint(
        paths, label=label, file_hashes=hashes, on_file=observe,
        io_path=lambda path: disk / path.name,
        reported_path=lambda path: reported / path.name,
    )
    assert fresh == _expected_digest({"a.py": b"a\0", "b.py": b"fresh content"})
    assert fresh != actual
    assert calls == dict.fromkeys(paths, 1)
    assert readers == [owner] * 4


def test_read_window_refills_before_previous_window_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = _GatedReads(tmp_path, monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as callers:
        result = callers.submit(_fingerprint, reads.paths)
        try:
            assert reads.initial_started.wait(5), "initial four readers did not start"
            reads.release[reads.paths[0]].set()
            assert reads.started[reads.paths[4]].wait(5), "reader window did not refill"
            assert all(not gate.is_set() for path, gate in reads.release.items() if path != reads.paths[0])
        finally:
            reads.release_all()
        assert result.result(timeout=10) == _expected_digest(reads.contents)
    assert reads.peak == 4
    assert reads.active == 0
    assert reads.completed == set(reads.paths)


@pytest.mark.parametrize("failure", ["read", "progress"])
def test_fingerprint_error_settles_outstanding_readers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    reads = _GatedReads(tmp_path, monkeypatch, fail_first=failure == "read")
    shutdown_started = threading.Event()

    class ObservedReaders(ThreadPoolExecutor):
        def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
            shutdown_started.set()
            super().shutdown(wait=wait, cancel_futures=cancel_futures)

    def progress() -> None:
        raise RuntimeError("fixture progress refusal")

    monkeypatch.setattr(fingerprint, "ThreadPoolExecutor", ObservedReaders)
    with ThreadPoolExecutor(max_workers=1) as callers:
        result = callers.submit(
            _fingerprint, reads.paths, on_progress=progress if failure == "progress" else None,
        )
        try:
            assert reads.initial_started.wait(5), "initial four readers did not start"
            reads.release[reads.paths[0]].set()
            assert shutdown_started.wait(5), "failure did not enter reader settlement"
            assert not result.done(), "fingerprint returned before outstanding readers settled"
        finally:
            reads.release_all()
        error = OSError if failure == "read" else RuntimeError
        with pytest.raises(error, match=f"fixture {failure} refusal"):
            result.result(timeout=10)
    assert reads.peak == 4
    assert reads.active == 0
    assert reads.completed == set(reads.paths[:4])
