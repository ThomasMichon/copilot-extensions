"""Real-file ownership contracts for canonical and in-package consumers."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType

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
    assert "Endpoint cleanup failed" in caplog.text
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
