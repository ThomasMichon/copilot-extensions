"""Inert split-credential observations; no elevation, UID change or native child."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_manager import artifacts, komodo_native, private_state
from fleet_manager.komodo_native import KomodoTooling
from fleet_manager.planning import PlanError

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX effective-owner contract")


@pytest.fixture
def split_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    client = tmp_path / "client"
    client.mkdir(mode=0o700)
    config = client / "config.json"
    config.write_bytes(b"{}")
    config.chmod(0o600)
    docker = tmp_path / "docker"
    docker.write_bytes(b"not-executed")
    docker.chmod(0o700)
    owners = {client: 0, config: 0}
    state = SimpleNamespace(client=client, config=config, docker=docker, opened_uid=0)
    original_stat, original_lstat, original_fstat = Path.stat, Path.lstat, os.fstat

    def owned(info: Any, uid: int) -> SimpleNamespace:
        return SimpleNamespace(
            st_uid=uid,
            **{
                name: getattr(info, name)
                for name in (
                    "st_mode", "st_dev", "st_ino", "st_gid", "st_nlink", "st_size",
                    "st_mtime_ns", "st_ctime_ns",
                )
            },
        )

    def path_stat(path: Path, *, follow_symlinks: bool = True) -> Any:
        info = original_stat(path, follow_symlinks=follow_symlinks)
        return owned(info, owners[path]) if path in owners else info

    def path_lstat(path: Path) -> Any:
        info = original_lstat(path)
        return owned(info, owners[path]) if path in owners else info

    monkeypatch.setattr(Path, "stat", path_stat)
    monkeypatch.setattr(Path, "lstat", path_lstat)
    monkeypatch.setattr(private_state, "os", SimpleNamespace(
        name="posix", getuid=lambda: 1234, geteuid=lambda: 0,
        fstat=lambda descriptor: owned(original_fstat(descriptor), state.opened_uid),
    ))
    monkeypatch.setattr(komodo_native, "os", SimpleNamespace(
        getuid=lambda: 1234, geteuid=lambda: 0,
        access=os.access, X_OK=os.X_OK, scandir=os.scandir,
    ))
    state.owners = owners
    return state


def test_split_credentials_retain_root_private_file_directory_and_inert_client(
    split_owner: SimpleNamespace,
) -> None:
    private_state.validate_private_directory(split_owner.client)
    assert private_state.read_private_file(split_owner.config, 4096) == b"{}"

    def no_native(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("private custody validation cannot execute a native child")

    tooling = KomodoTooling(
        split_owner.docker, split_owner.client, "example-daemon", no_native,
    )
    tooling.check_client()
    assert private_state.os.getuid() == 1234
    assert private_state.os.geteuid() == 0
    assert split_owner.owners == {split_owner.client: 0, split_owner.config: 0}


@pytest.mark.parametrize("fault", [
    "named-owner", "opened-owner", "directory-owner", "file-shared", "directory-shared",
])
def test_split_credentials_reject_real_uid_owned_or_shared_state(
    split_owner: SimpleNamespace, fault: str,
) -> None:
    if fault == "named-owner":
        split_owner.owners[split_owner.config] = 1234
    elif fault == "opened-owner":
        split_owner.opened_uid = 1234
    elif fault == "directory-owner":
        split_owner.owners[split_owner.client] = 1234
    elif fault == "file-shared":
        split_owner.config.chmod(0o640)
    else:
        split_owner.client.chmod(0o750)
    with pytest.raises(PlanError):
        private_state.validate_private_directory(split_owner.client)
        private_state.read_private_file(split_owner.config, 4096)


def test_split_credentials_reject_opened_file_substitution(
    split_owner: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"{}")
    replacement.chmod(0o600)
    monkeypatch.setattr(
        artifacts, "_open_artifact", lambda _path: os.open(replacement, os.O_RDONLY),
    )
    with pytest.raises(PlanError, match="changed"):
        private_state.read_private_file(split_owner.config, 4096)


def test_unprivileged_private_reader_remains_compatible(tmp_path: Path) -> None:
    directory = tmp_path / "ordinary"
    private_state.private_directory(directory)
    config = directory / "config.json"
    config.write_bytes(b"{}")
    config.chmod(0o600)
    private_state.validate_private_directory(directory)
    assert private_state.read_private_file(config, 4096) == b"{}"
