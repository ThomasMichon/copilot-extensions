import ctypes
import os
from types import SimpleNamespace
import pytest
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_ssh_service import hosting
from agent_ssh_service.config import ServiceConfig
from fleet_manager.planning import PlanError
from fleet_manager.private_state import create_private_file, private_directory, windows_private

from conftest import document


class StoppedConnector:
    def __init__(self, **_):
        pass

    async def run(self, stop):
        stop.set()


def test_native_private_creation_and_preserving_existing_refusal(tmp_path):
    root = tmp_path / "private-service"
    private_directory(root)
    private_directory(root)
    if os.name == "nt":
        windows_private(root)
    else:
        assert root.stat().st_mode & 0o077 == 0 and root.stat().st_uid == os.getuid()


def shared_windows_acl(path):
    from ctypes import wintypes as w

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), w.LPVOID,
    ]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    security.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, w.LPVOID]
    security.SetFileSecurityW.restype = w.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [w.LPVOID], w.LPVOID
    descriptor = w.LPVOID()
    assert security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "D:P(A;OICI;FA;;;WD)", 1, ctypes.byref(descriptor), None,
    )
    try:
        assert security.SetFileSecurityW(str(path), 4 | 0x80000000, descriptor)
    finally:
        kernel.LocalFree(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="Actual native Windows security descriptors")
@pytest.mark.parametrize("shared", [
    "root", "directory.sqlite3", "agent-ssh-service.lock", "directory.sqlite3-journal",
    "directory.sqlite3-wal", "directory.sqlite3-shm",
])
async def test_shared_windows_state_refuses_before_credentials_lease_or_sqlite(
    tmp_path, monkeypatch, shared,
):
    root = tmp_path / "selected-service"
    private_directory(root)
    selected = root if shared == "root" else root / shared
    if selected != root:
        with os.fdopen(create_private_file(selected), "wb") as stream:
            stream.write(b"preserve-existing-state")
    shared_windows_acl(selected)
    before = {entry.name: entry.read_bytes() for entry in root.iterdir()}
    monkeypatch.setattr(hosting, "load_service_credentials",
                        lambda **_: pytest.fail("unsafe state reached credentials"))
    monkeypatch.setattr(hosting, "SingleInstance",
                        lambda *_a, **_k: pytest.fail("unsafe state reached writer lease"))
    with pytest.raises(PlanError, match="shared access grant"):
        await hosting.run(ServiceConfig.from_dict(document(root)))
    assert {entry.name: entry.read_bytes() for entry in root.iterdir()} == before
    with pytest.raises(PlanError, match="shared access grant"):
        windows_private(selected)


@pytest.mark.skipif(os.name != "nt", reason="Actual native Windows owner and ACL inheritance")
async def test_actual_host_restart_retains_token_user_owned_database_and_writer_lease(
    tmp_path, monkeypatch,
):
    root = tmp_path / "restartable-service"
    selected = ServiceConfig.from_dict(document(root))

    monkeypatch.setattr(hosting, "ServiceConnector", StoppedConnector)
    monkeypatch.setattr(hosting, "load_service_credentials",
                        lambda **_: SimpleNamespace(access_token=None, proof_key=None))
    for iteration in range(2):
        await hosting.run(selected)
        windows_private(root)
        windows_private(root / "directory.sqlite3")
        windows_private(root / "agent-ssh-service.lock")
        if iteration == 0:
            # Model SQLite's default-owner recovery artifact, not an ownership repair.
            journal = root / "directory.sqlite3-journal"
            journal.write_bytes(b"")
            windows_private(journal, privileged_owner=True)


async def test_interrupted_first_start_retries_with_only_its_validated_lease_file(
    config, monkeypatch,
):
    from pathlib import Path

    root = Path(config.to_dict()["data_dir"])
    original = hosting.DirectoryStore
    monkeypatch.setattr(hosting, "ServiceConnector", StoppedConnector)
    monkeypatch.setattr(hosting, "load_service_credentials",
                        lambda **_: SimpleNamespace(access_token=None, proof_key=None))

    def interrupted(_config):
        raise OSError("synthetic interruption before SQLite")

    monkeypatch.setattr(hosting, "DirectoryStore", interrupted)
    with pytest.raises(OSError, match="interruption before SQLite"):
        await hosting.run(config)
    assert {entry.name for entry in root.iterdir()} == {"agent-ssh-service.lock"}
    monkeypatch.setattr(hosting, "DirectoryStore", original)
    await hosting.run(config)
    assert (root / "directory.sqlite3").is_file()


async def test_lease_only_retry_does_not_displace_an_actual_live_writer(config, monkeypatch):
    from pathlib import Path

    root = Path(config.to_dict()["data_dir"])
    os.close(create_private_file(root / "agent-ssh-service.lock"))
    owner = SingleInstance(str(root), service="agent-ssh-service")
    monkeypatch.setattr(hosting, "ServiceConnector", StoppedConnector)
    monkeypatch.setattr(hosting, "load_service_credentials",
                        lambda **_: SimpleNamespace(access_token=None, proof_key=None))
    owner.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            await hosting.run(config)
        assert owner.held and not (root / "directory.sqlite3").exists()
    finally:
        owner.release()
    await hosting.run(config)
    assert (root / "directory.sqlite3").is_file()


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX ownership/mode boundary")
async def test_shared_posix_directory_is_not_repaired_or_used(tmp_path, monkeypatch):
    root = tmp_path / "shared-service"
    root.mkdir(mode=0o777)
    root.chmod(0o777)
    monkeypatch.setattr(hosting, "load_service_credentials",
                        lambda **_: pytest.fail("unsafe state reached credentials"))
    with pytest.raises(PlanError, match="private"):
        await hosting.run(ServiceConfig.from_dict(document(root)))
    assert root.stat().st_mode & 0o777 == 0o777 and list(root.iterdir()) == []
