"""Real-file ownership contracts for canonical and in-package consumers."""

from __future__ import annotations

import ast
import contextlib
import errno
import importlib
import importlib.util
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCES = {
    "canonical": "libs/endpoint-rendezvous/src/endpoint_rendezvous/rendezvous.py",
    "dispatch": "plugins/agent-dispatch/src/agent_dispatch/rendezvous.py",
    "index": "plugins/agent-index/src/agent_index/rendezvous.py",
    "vault": "plugins/agent-vault/src/agent_vault/rendezvous.py",
}


@pytest.fixture(params=SOURCES)
def rendezvous(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    name = f"ownership_contract_{request.param}"
    spec = importlib.util.spec_from_file_location(name, ROOT / SOURCES[request.param])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_successor_publication_waits_for_owned_cleanup(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    endpoint = rendezvous.endpoint_file(tmp_path)
    unlink = Path.unlink
    started = threading.Event()
    finished = threading.Event()
    errors: list[Exception] = []

    def publish() -> None:
        started.set()
        try:
            rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41002", pid=2002)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    successor = threading.Thread(target=publish, daemon=True)

    def pause_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == endpoint:
            successor.start()
            assert started.wait(5)
            assert not finished.wait(0.2), "successor replaced the checked record before unlink"
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", pause_unlink)
    try:
        rendezvous.clear_endpoint(tmp_path, owner_pid=1001)
    finally:
        if successor.ident is not None:
            successor.join(5)
    assert not successor.is_alive()
    assert not errors
    assert finished.is_set()
    current = rendezvous.read_endpoint(tmp_path)
    assert current is not None and current.pid == 2002


WRITER = """
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("ownership_writer", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
print("ready", flush=True)
sys.stdin.readline()
print("attempting", flush=True)
module.write_endpoint(sys.argv[2], "tcp", "127.0.0.1:41002", pid=2002)
print("published", flush=True)
"""


def test_reader_allows_independent_publication_and_keeps_its_snapshot(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    readers = [rendezvous.read_endpoint]
    if hasattr(rendezvous, "read_endpoint_strict"):
        readers.append(rendezvous.read_endpoint_strict)
    open_endpoint = rendezvous._open_endpoint
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    for reader in readers:
        runtime = tmp_path / reader.__name__
        rendezvous.write_endpoint(runtime, "tcp", "127.0.0.1:41001", pid=1001)

        @contextlib.contextmanager
        def publish_while_open(path: Path) -> Iterator[BinaryIO]:
            with open_endpoint(path) as handle:
                result = subprocess.run(
                    [sys.executable, "-c", WRITER, rendezvous.__file__, str(runtime)],
                    input="publish\n", capture_output=True, text=True,
                    creationflags=flags, timeout=5,
                )
                assert result.returncode == 0, result.stderr
                assert result.stdout.splitlines() == ["ready", "attempting", "published"]
                yield handle

        with monkeypatch.context() as patch:
            patch.setattr(rendezvous, "_open_endpoint", publish_while_open)
            snapshot = reader(runtime)
        assert snapshot is not None and snapshot.pid == 1001
        assert snapshot.address == "127.0.0.1:41001"
        current = reader(runtime)
        assert current is not None and current.pid == 2002
        assert current.address == "127.0.0.1:41002"


def test_publication_supports_relative_unicode_paths(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = Path("run space-\u03b1")
    rendezvous.write_endpoint(runtime, "tcp", "127.0.0.1:41001", pid=1001)
    rendezvous.write_endpoint(runtime, "tcp", "127.0.0.1:41002", pid=2002)
    current = rendezvous.read_endpoint(runtime)
    assert current is not None and current.pid == 2002


def test_missing_staged_file_preserves_the_advertisement(
    rendezvous: ModuleType, tmp_path: Path,
) -> None:
    endpoint = rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    original = endpoint.read_bytes()
    with pytest.raises(FileNotFoundError):
        rendezvous._replace_endpoint(tmp_path / "absent", endpoint)
    assert endpoint.read_bytes() == original


def test_publication_failure_is_not_retried_or_hidden(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    endpoint = rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    original = endpoint.read_bytes()
    calls = 0

    def refuse(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        assert source.is_file() and destination == endpoint
        raise PermissionError("injected replacement failure")

    monkeypatch.setattr(rendezvous, "_replace_endpoint", refuse)
    with pytest.raises(PermissionError, match="injected replacement failure"):
        rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41002", pid=2002)
    assert calls == 1
    assert endpoint.read_bytes() == original


@pytest.mark.parametrize("raw", [b"\xff", b"{bad json", b'{"schema":999}'])
def test_reader_error_classification_is_unchanged(
    rendezvous: ModuleType, tmp_path: Path, raw: bytes,
) -> None:
    endpoint = rendezvous.endpoint_file(tmp_path)
    assert rendezvous.read_endpoint(tmp_path) is None
    if hasattr(rendezvous, "read_endpoint_strict"):
        assert rendezvous.read_endpoint_strict(tmp_path) is None
    endpoint.write_bytes(raw)
    assert rendezvous.read_endpoint(tmp_path) is None
    if hasattr(rendezvous, "read_endpoint_strict"):
        with pytest.raises(rendezvous.EndpointStateError):
            rendezvous.read_endpoint_strict(tmp_path)


def test_reader_open_failure_keeps_tolerant_and_strict_classification(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def refuse(path: Path) -> BinaryIO:
        raise PermissionError("injected read failure")

    monkeypatch.setattr(rendezvous, "_open_endpoint", refuse)
    assert rendezvous.read_endpoint(tmp_path) is None
    if hasattr(rendezvous, "read_endpoint_strict"):
        with pytest.raises(rendezvous.EndpointStateError, match="unreadable endpoint state"):
            rendezvous.read_endpoint_strict(tmp_path)


def test_cleanup_serializes_with_an_independent_publisher(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    endpoint = rendezvous.endpoint_file(tmp_path)
    unlink = Path.unlink
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with subprocess.Popen(
        [sys.executable, "-c", WRITER, rendezvous.__file__, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=flags,
    ) as successor:
        assert successor.stdout is not None and successor.stdin is not None
        assert successor.stdout.readline().strip() == "ready"

        def pause_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == endpoint:
                successor.stdin.write("publish\n")
                successor.stdin.flush()
                assert successor.stdout.readline().strip() == "attempting"
                with pytest.raises(subprocess.TimeoutExpired):
                    successor.wait(timeout=0.2)
            unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", pause_unlink)
        try:
            rendezvous.clear_endpoint(tmp_path, owner_pid=1001)
        finally:
            try:
                stdout, stderr = successor.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                successor.kill()
                successor.communicate()
                raise
        assert successor.returncode == 0, stderr
        assert stdout.strip() == "published"
    current = rendezvous.read_endpoint(tmp_path)
    assert current is not None and current.pid == 2002


def test_contended_mutations_do_not_change_the_advertisement(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, tmp_path: Path,
) -> None:
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    monkeypatch.setattr(rendezvous, "_ENDPOINT_LOCK_TIMEOUT", 0.03)
    with rendezvous._endpoint_lock(tmp_path):
        with pytest.raises(TimeoutError, match="endpoint mutation lock timed out"):
            rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41002", pid=2002)
        rendezvous.clear_endpoint(tmp_path, owner_pid=1001)
        current = rendezvous.read_endpoint(tmp_path)
        assert current is not None and current.pid == 1001
    assert "Endpoint cleanup skipped: mutation lock timed out" in caplog.text
    assert "Endpoint cleanup failed" not in caplog.text
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41002", pid=2002)
    current = rendezvous.read_endpoint(tmp_path)
    assert current is not None and current.pid == 2002


def test_mutations_keep_the_lock_identity(
    rendezvous: ModuleType, tmp_path: Path,
) -> None:
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    lock = tmp_path / ".endpoint.lock"
    identity = lock.stat().st_ino
    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)
    assert lock.is_file() and lock.stat().st_ino == identity
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41002", pid=2002)
    assert lock.stat().st_ino == identity


def test_cleanup_does_not_create_an_absent_runtime(
    rendezvous: ModuleType, tmp_path: Path,
) -> None:
    absent = tmp_path / "absent"
    rendezvous.clear_endpoint(absent, owner_pid=1001)
    assert not absent.exists()


@pytest.mark.parametrize("error", [PermissionError, TimeoutError])
def test_cleanup_logs_runtime_stat_failure(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, tmp_path: Path, error: type[OSError],
) -> None:
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    endpoint = rendezvous.endpoint_file(tmp_path)
    original = endpoint.read_bytes()
    exists = Path.exists

    def fail_probe(path: Path) -> bool:
        if path == tmp_path:
            raise error("injected runtime stat failure")
        return exists(path)

    monkeypatch.setattr(Path, "exists", fail_probe)
    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)
    assert endpoint.read_bytes() == original
    assert "Endpoint cleanup failed" in caplog.text
    assert "injected runtime stat failure" in caplog.text
    assert "Endpoint cleanup skipped" not in caplog.text


def test_interrupted_native_acquisition_is_retried(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    native = importlib.import_module("msvcrt" if sys.platform == "win32" else "fcntl")
    operation = "locking" if sys.platform == "win32" else "flock"
    original = getattr(native, operation)
    interrupted = False

    def interrupt_once(*args: int) -> None:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise OSError(errno.EINTR, "interrupted acquisition")
        original(*args)

    monkeypatch.setattr(native, operation, interrupt_once)
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    assert interrupted
    current = rendezvous.read_endpoint(tmp_path)
    assert current is not None and current.pid == 1001
    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)
    assert not rendezvous.endpoint_file(tmp_path).exists()


def test_repeated_native_interruptions_respect_the_deadline(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, tmp_path: Path,
) -> None:
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    native = importlib.import_module("msvcrt" if sys.platform == "win32" else "fcntl")
    operation = "locking" if sys.platform == "win32" else "flock"
    calls = 0

    def interrupt(*args: int) -> None:
        nonlocal calls
        calls += 1
        raise OSError(errno.EINTR, "interrupted acquisition")

    monkeypatch.setattr(native, operation, interrupt)
    monkeypatch.setattr(rendezvous, "_ENDPOINT_LOCK_TIMEOUT", 0.03)
    with pytest.raises(TimeoutError, match="endpoint mutation lock timed out"):
        rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41002", pid=2002)
    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)
    assert calls >= 4
    current = rendezvous.read_endpoint(tmp_path)
    assert current is not None and current.pid == 1001
    assert "Endpoint cleanup skipped: mutation lock timed out" in caplog.text


@pytest.mark.parametrize("error", [PermissionError, TimeoutError])
def test_unlink_failure_is_not_reported_as_lock_contention(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, tmp_path: Path, error: type[OSError],
) -> None:
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    endpoint = rendezvous.endpoint_file(tmp_path)
    unlink = Path.unlink

    def fail_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == endpoint:
            raise error("injected unlink failure")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)
    current = rendezvous.read_endpoint(tmp_path)
    assert current is not None and current.pid == 1001
    assert "Endpoint cleanup failed" in caplog.text
    assert "injected unlink failure" in caplog.text
    assert "Endpoint cleanup skipped" not in caplog.text


def test_unlock_failure_does_not_claim_removal_failed(
    rendezvous: ModuleType, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, tmp_path: Path,
) -> None:
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    native = importlib.import_module("msvcrt" if sys.platform == "win32" else "fcntl")
    operation = "locking" if sys.platform == "win32" else "flock"
    unlock = native.LK_UNLCK if sys.platform == "win32" else native.LOCK_UN
    original = getattr(native, operation)

    def fail_unlock(*args: int) -> None:
        if args[1] == unlock:
            raise OSError(errno.EIO, "injected unlock failure")
        original(*args)

    monkeypatch.setattr(native, operation, fail_unlock)
    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)
    assert not rendezvous.endpoint_file(tmp_path).exists()
    assert "Endpoint removed but mutation lock release failed" in caplog.text
    assert "Endpoint cleanup failed" not in caplog.text


@pytest.mark.parametrize("source", ["dispatch", "index", "vault"])
def test_vendored_concurrency_helpers_match_the_canonical_source(source: str) -> None:
    def mutation_contract(path: str) -> list[str]:
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        return [
            ast.dump(node, include_attributes=False)
            for node in tree.body
            if (
                isinstance(node, ast.FunctionDef)
                and node.name in (
                    "_endpoint_lock", "clear_endpoint", "_open_endpoint", "_replace_endpoint",
                )
            ) or (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "_ENDPOINT_LOCK_TIMEOUT"
                    for target in node.targets
                )
            )
        ]

    canonical = mutation_contract(SOURCES["canonical"])
    assert len(canonical) == 5
    assert mutation_contract(SOURCES[source]) == canonical
