"""Inert split-credential observations; no elevation, UID change or native child."""

from __future__ import annotations

import os
import stat
import sys
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
    state = SimpleNamespace(
        client=client, config=config, docker=docker, opened_uid=None, socket_identity=None,
    )
    original_stat, original_lstat, original_fstat = Path.stat, Path.lstat, os.fstat

    def owned(info: Any, uid: int) -> SimpleNamespace:
        observed = SimpleNamespace(
            st_uid=uid,
            **{
                name: getattr(info, name)
                for name in (
                    "st_mode", "st_dev", "st_ino", "st_gid", "st_nlink", "st_size",
                    "st_mtime_ns", "st_ctime_ns",
                )
            },
        )
        if (info.st_dev, info.st_ino) == state.socket_identity:
            observed.st_mode = stat.S_IFSOCK | 0o600
        return observed

    def owner_uid(path: Path, info: Any) -> int:
        return owners.get(path, 0 if path.is_relative_to(tmp_path) else info.st_uid)

    def path_stat(path: Path, *, follow_symlinks: bool = True) -> Any:
        info = original_stat(path, follow_symlinks=follow_symlinks)
        return owned(info, owner_uid(path, info))

    def path_lstat(path: Path) -> Any:
        info = original_lstat(path)
        return owned(info, owner_uid(path, info))

    def fstat(descriptor: int) -> SimpleNamespace:
        info = original_fstat(descriptor)
        uid = 0 if state.opened_uid is None else state.opened_uid
        for path, selected_uid in owners.items():
            try:
                named = original_stat(path)
            except FileNotFoundError:
                continue
            if (named.st_dev, named.st_ino) == (info.st_dev, info.st_ino):
                uid = selected_uid if state.opened_uid is None else state.opened_uid
                break
        return owned(info, uid)

    monkeypatch.setattr(Path, "stat", path_stat)
    monkeypatch.setattr(Path, "lstat", path_lstat)
    observations = SimpleNamespace(**{
        **vars(os), "getuid": lambda: 1234, "geteuid": lambda: 0, "fstat": fstat,
    })
    from fleet_manager import gateway_host, komodo_selection, komodo_state, ssh_machine

    for module in (
        private_state, komodo_native, komodo_selection, komodo_state, gateway_host, ssh_machine,
    ):
        monkeypatch.setattr(module, "os", observations)
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


def selection(owner: SimpleNamespace, root: Path) -> Any:
    from fleet_manager.komodo_declaration import KomodoDeclaration
    from fleet_manager.komodo_profile import DATA_NAMES, SECRET_NAMES, KomodoMaterialization
    from fleet_manager.komodo_selection import KomodoSelection

    paths = {}
    for name in DATA_NAMES:
        paths[name] = root / name
        paths[name].mkdir(mode=0o700)
    for name in SECRET_NAMES:
        paths[name] = root / name
        paths[name].write_bytes(("synthetic-material-" + name).encode())
        paths[name].chmod(0o600)
    socket = root / "docker-socket"
    socket.write_bytes(b"not-a-network-endpoint")
    info = socket.stat()
    owner.socket_identity = info.st_dev, info.st_ino
    material = KomodoMaterialization(docker_socket=socket, **paths)
    declared = KomodoDeclaration.from_dict({
        "schema": "fleet-manager.komodo-substrate", "version": 1,
        "provider": "manager", "instance": "core-a", "target": "host-a",
        "configuration_ref": "core-config", "revision": "config-1",
        "project": "example-manager", "architecture": "arm64", "core_port": 19120,
        "server_name": "example-host", "outfitting_seconds": 180,
        "references": {name: name + "-owner" for name in (
            "artifact", "credentials", "host", "runtime", "storage",
        )},
        "images": {
            "core": {"image": "ghcr.io/moghtech/komodo-core@sha256:" + "a" * 64, "version": "2.0.0"},
            "periphery": {"image": "ghcr.io/moghtech/komodo-periphery@sha256:" + "b" * 64, "version": "2.0.0"},
            "mongo": {"image": "docker.io/library/mongo@sha256:" + "c" * 64, "version": "8.0.0"},
        },
        "resources": {name: {"cpu_millis": 500, "memory_bytes": 512 * 1024**2}
                      for name in ("core", "mongo", "periphery")},
    })
    runtime = root / "host-runtime"
    runtime.mkdir(mode=0o700)

    def no_native(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("selection/state custody cannot execute a native child")

    return KomodoSelection(
        declared, "example-fleet", "example-installation", 1, "example-policy",
        2**32, "d" * 64, runtime,
        KomodoTooling(owner.docker, owner.client, "example-daemon", no_native), material,
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux host selection/state contract")
def test_split_credentials_join_real_selection_state_and_recovery(
    split_owner: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl
    from fleet_manager.komodo_state import KomodoState

    locks = []
    monkeypatch.setattr(fcntl, "flock", lambda descriptor, flags: locks.append(flags))
    selected = selection(split_owner, tmp_path)
    with KomodoState.open(
        selected.host_runtime, daemon=selected.tooling.daemon_id, project="example-manager",
    ) as state:
        state.save({"value": 1})
        pending = state.root / "selection.pending"
        pending.write_bytes(b'{"value":')
        pending.chmod(0o600)
        state.save({"value": 2})
        assert state.read() == {"value": 2}
    first_open = list(locks)
    with KomodoState.open(
        selected.host_runtime, daemon=selected.tooling.daemon_id,
        project="example-manager", existing_only=True,
    ) as recovered:
        assert recovered.read() == {"value": 2}
    for calls in (first_open, locks[len(first_open):]):
        acquisitions = [flags for flags in calls if not flags & fcntl.LOCK_UN]
        assert acquisitions and all(
            flags == fcntl.LOCK_EX | fcntl.LOCK_NB for flags in acquisitions
        )
    assert private_state.os.getuid() == 1234 and private_state.os.geteuid() == 0


@pytest.mark.skipif(sys.platform != "linux", reason="Linux host selection/state contract")
@pytest.mark.parametrize("target", ["runtime", "recovery", "lock", "pending"])
def test_split_credentials_keep_one_owner_through_selection_and_state(
    split_owner: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    import fcntl
    from fleet_manager.komodo_state import KomodoState

    monkeypatch.setattr(fcntl, "flock", lambda *_args: None)
    if target == "runtime":
        split_owner.owners[tmp_path / "host-runtime"] = 1234
        with pytest.raises(PlanError, match="host-owner custody"):
            selection(split_owner, tmp_path)
        return
    selected = selection(split_owner, tmp_path)
    with KomodoState.open(
        selected.host_runtime, daemon=selected.tooling.daemon_id, project="example-manager",
    ) as state:
        state.save({"value": 1})
        if target == "pending":
            pending = state.root / "selection.pending"
            pending.write_bytes(b'{"value":')
            pending.chmod(0o600)
            split_owner.owners[pending] = 1234
            with pytest.raises(PlanError, match="foreign custody"):
                state.save({"value": 2})
            assert state.read() == {"value": 1}
            return
        changed = state.root if target == "recovery" else state.root / "owner.lock"
    split_owner.owners[changed] = 1234
    with pytest.raises(PlanError):
        with KomodoState.open(
            selected.host_runtime, daemon=selected.tooling.daemon_id,
            project="example-manager", existing_only=True,
        ):
            pytest.fail("foreign recovery custody admitted")


def test_split_credentials_keep_gateway_evidence_and_ssh_lease_consistent(
    split_owner: SimpleNamespace,
) -> None:
    from fleet_manager.gateway_host import read_runtime_file
    from fleet_manager.ssh_machine import _validate_lease

    assert read_runtime_file(split_owner.config) == b"{}"
    _validate_lease(split_owner.config)
    split_owner.owners[split_owner.config] = 1234
    with pytest.raises(PlanError):
        read_runtime_file(split_owner.config)
    with pytest.raises(PlanError):
        _validate_lease(split_owner.config)
