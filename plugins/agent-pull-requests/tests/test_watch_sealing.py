"""Contained native sealing; no boot crossing or migration activation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from single_instance_lease import SingleInstance

from agent_pull_requests.watch_storage import watch_state_paths
from agent_pull_requests.watch_writer import WatchWriter
from test_watch_transition import writer as writer

pytestmark = pytest.mark.contract("agent_pull_requests.watch.writer_handoff")

_ABRUPT_WINDOWS_CONTROLLER = r"""
import json, os, sys
from pathlib import Path
from agent_pull_requests import watch_windows_sealing
from agent_pull_requests.watch_storage import watch_state_paths
from agent_pull_requests.watch_writer import WatchWriter

root, stage, adapted = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "v2"
paths = watch_state_paths(root)
def forbidden(*args):
    raise AssertionError("sealing must not fetch or notify")
owner = WatchWriter(root=root, authorize=lambda _: None, fetch=forbidden, notify=forbidden)
owner.adopt(timeout=3)
owner.prepare_state_transition(timeout=3)
if adapted:
    owner.recover_prepared_state_transition(timeout=3)
def leave():
    print(json.dumps(dict(stage=stage, held=owner.writer_held,
                          lock=str(owner._lease.lock_path), ready=owner.read_ready,
                          effects=owner.daemon.effects_enabled)), flush=True)
    os._exit(73)
if stage == "prepared":
    leave()
confirm = watch_windows_sealing.confirm_private
def confirmed(path):
    confirm(path)
    if stage == "staged":
        leave()
watch_windows_sealing.confirm_private = confirmed
release = owner._lease.release
def released():
    release()
    if stage == "released":
        leave()
owner._lease.release = released
move = watch_windows_sealing.windows_move_no_replace
def moved(source, destination):
    move(source, destination)
    if (stage == "moved" and destination == paths.retained_lock) or (
        stage == "barrier-installed" and destination == paths.historical_lock
    ):
        leave()
watch_windows_sealing.windows_move_no_replace = moved
seal = owner.seal_recovered_state_transition if adapted else owner.seal_state_transition
seal(timeout=3)
assert stage == "sealed"
leave()
"""


def test_unavailable_exchange_backend_preserves_prepared_custody(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer.adopt(timeout=1)
    writer.prepare_state_transition(timeout=1)
    paths = watch_state_paths(tmp_path)
    prior = paths.journal.read_bytes()
    identity = writer._lease.held_file_identity()
    with monkeypatch.context() as unsupported:
        unsupported.setattr(sys, "platform", "unsupported")
        with pytest.raises(NotImplementedError, match="exchange backend"):
            writer.seal_state_transition(timeout=1)
    assert writer.writer_held and writer._lease.held_file_identity() == identity
    assert paths.journal.read_bytes() == prior and not paths.retained_lock.exists()
    assert not paths.isolated_subscriptions.parent.exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux exchange qualification")
def test_native_exchange_retains_delayed_historical_inode_and_blocks_cold_open(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        pytest.skip("native Linux exchange qualification")
    import fcntl

    paths = watch_state_paths(tmp_path)
    paths.legacy_subscriptions.write_bytes(b" [] \n")
    writer.adopt(timeout=2)
    writer.prepare_state_transition(timeout=2)
    legacy = paths.legacy_subscriptions.read_bytes()
    identity = writer._lease.held_file_identity()
    delayed = os.open(paths.historical_lock, os.O_RDWR)
    try:
        record = writer.seal_state_transition(timeout=2)
        assert record["phase"] == "sealed-legacy"
        assert writer._lease.held_file_identity() == identity
        assert writer._lease.lock_path == paths.retained_lock
        with pytest.raises(BlockingIOError):
            fcntl.flock(delayed, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(IsADirectoryError):
            SingleInstance(tmp_path, service="agent-pull-requests-watch").acquire()
        assert not writer.read_ready and not writer.daemon.effects_enabled
        assert paths.legacy_subscriptions.read_bytes() == legacy
        assert not paths.isolated_subscriptions.parent.exists()
        writer.close(timeout=2)
        fcntl.flock(delayed, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert os.fstat(delayed).st_ino == identity[1]
    finally:
        os.close(delayed)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows sharing qualification")
def test_native_windows_sealing_blocks_historical_open_and_retains_legacy_authority(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    paths = watch_state_paths(tmp_path)
    paths.legacy_subscriptions.write_bytes(b" [] \n")
    writer.adopt(timeout=3)
    prepared = writer.prepare_state_transition(timeout=3)
    record = writer.seal_state_transition(timeout=3)
    assert record["phase"] == "sealed-legacy" and record["seal"]["boot"] is None
    assert record["lease"] == prepared["lease"]
    assert writer.writer_held and writer._lease.lock_path == paths.writer_lock
    assert not writer.read_ready and not writer.daemon.effects_enabled
    assert paths.historical_lock.is_dir() and not paths.staged_barrier.exists()
    assert paths.legacy_subscriptions.read_bytes() == b" [] \n"
    assert not paths.isolated_subscriptions.parent.exists()
    with pytest.raises(PermissionError):
        SingleInstance(tmp_path, service="agent-pull-requests-watch").acquire()
    assert writer.seal_state_transition(timeout=3) == record
    with pytest.raises(RuntimeError, match="read-ready"):
        writer.activate(timeout=1)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows sharing qualification")
def test_native_windows_open_handle_blocks_move_until_it_closes(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    paths = watch_state_paths(tmp_path)
    writer.adopt(timeout=3)
    original = writer._lease.held_file_identity()
    writer.prepare_state_transition(timeout=3)
    delayed = os.open(paths.historical_lock, os.O_RDWR)
    try:
        with pytest.raises(PermissionError) as error:
            writer.seal_state_transition(timeout=3)
        assert getattr(error.value, "winerror", None) == 32
        assert not writer.writer_held and paths.historical_lock.is_file()
        assert not paths.retained_lock.exists() and not paths.writer_lock.exists()
        assert (os.fstat(delayed).st_dev, os.fstat(delayed).st_ino) == original
    finally:
        os.close(delayed)
    assert writer.seal_state_transition(timeout=3)["phase"] == "sealed-legacy"
    assert writer._lease.lock_path == paths.writer_lock


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows sharing qualification")
def test_native_windows_raced_creation_preserves_both_files_and_blocks_recovery(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_private, watch_windows_sealing

    paths = watch_state_paths(tmp_path)
    writer.adopt(timeout=3)
    writer.prepare_state_transition(timeout=3)
    move = watch_private.windows_move_no_replace
    racer = SingleInstance(tmp_path, service="agent-pull-requests-watch")

    def raced(source: Path, destination: Path) -> None:
        move(source, destination)
        if destination == paths.retained_lock:
            racer.acquire()

    monkeypatch.setattr(watch_windows_sealing, "windows_move_no_replace", raced)
    try:
        with pytest.raises(PermissionError, match="raced historical"):
            writer.seal_state_transition(timeout=3)
        original, raced_bytes = paths.retained_lock.read_bytes(), paths.historical_lock.read_bytes()
        with pytest.raises(PermissionError, match="raced historical"):
            writer.seal_state_transition(timeout=3)
        assert not writer.writer_held and not paths.writer_lock.exists()
        assert paths.retained_lock.read_bytes() == original
        assert paths.historical_lock.read_bytes() == raced_bytes
        assert paths.staged_barrier.is_dir()
    finally:
        racer.release()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows sharing qualification")
@pytest.mark.parametrize("destination_name", ["retained_lock", "historical_lock"])
def test_native_windows_cold_recovery_never_opens_retained_inode(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination_name: str,
) -> None:
    from agent_pull_requests import watch_private, watch_windows_sealing

    paths = watch_state_paths(tmp_path)
    writer.adopt(timeout=3)
    writer.prepare_state_transition(timeout=3)
    move = watch_private.windows_move_no_replace
    acquire = SingleInstance.acquire
    acquisitions: list[Path] = []

    def interrupted(source: Path, destination: Path) -> None:
        move(source, destination)
        if destination == getattr(paths, destination_name):
            raise OSError("controlled interrupted Windows move return")

    def observed(
        instance: SingleInstance, *, expected_file_identity: tuple[int, int] | None = None,
    ) -> None:
        acquisitions.append(instance.lock_path)
        acquire(instance, expected_file_identity=expected_file_identity)

    with monkeypatch.context() as failed:
        failed.setattr(watch_windows_sealing, "windows_move_no_replace", interrupted)
        with pytest.raises(OSError, match="interrupted Windows move"):
            writer.seal_state_transition(timeout=3)
    writer.close(timeout=3)
    monkeypatch.setattr(SingleInstance, "acquire", observed)
    recovered = WatchWriter(
        root=tmp_path, authorize=lambda _: None,
        fetch=lambda *_: pytest.fail("sealing recovery must not fetch"),
    )
    try:
        assert recovered.seal_state_transition(timeout=3)["phase"] == "sealed-legacy"
        assert acquisitions == [paths.writer_lock]
        assert not recovered.read_ready and not recovered.daemon.effects_enabled
        assert not paths.isolated_subscriptions.parent.exists()
    finally:
        recovered.close(timeout=3)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows controller-exit qualification")
@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize(
    "stage", ["prepared", "staged", "released", "moved", "barrier-installed", "sealed"],
)
def test_native_windows_abrupt_controller_exit_recovers_without_legacy_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, version: str,
) -> None:
    paths = watch_state_paths(tmp_path)
    legacy = b" [] \n"
    paths.legacy_subscriptions.write_bytes(legacy)
    result = subprocess.run(
        [sys.executable, "-B", "-c", _ABRUPT_WINDOWS_CONTROLLER, str(tmp_path), stage, version],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 73, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["stage"] == stage and not receipt["ready"] and not receipt["effects"]
    assert receipt["held"] == (stage in ("prepared", "staged", "sealed"))
    assert receipt["lock"] == str(paths.writer_lock if stage == "sealed" else paths.historical_lock)
    interrupted = json.loads(paths.journal.read_bytes())
    assert interrupted["version"] == (2 if version == "v2" else 1)
    assert interrupted["phase"] == ("sealed-legacy" if stage == "sealed" else "prepared")
    original = (interrupted["lease"]["device"], interrupted["lease"]["inode"])
    retained = stage in ("moved", "barrier-installed", "sealed")
    original_path = paths.retained_lock if retained else paths.historical_lock
    info = original_path.stat()
    assert (info.st_dev, info.st_ino) == original
    assert paths.historical_lock.is_dir() == (stage in ("barrier-installed", "sealed"))
    assert paths.staged_barrier.exists() == (stage in ("staged", "released", "moved"))
    assert paths.legacy_subscriptions.read_bytes() == legacy
    assert not paths.isolated_subscriptions.parent.exists()
    acquisitions: list[Path] = []
    acquire = SingleInstance.acquire

    def observed(
        instance: SingleInstance, *, expected_file_identity: tuple[int, int] | None = None,
    ) -> None:
        acquisitions.append(instance.lock_path)
        acquire(instance, expected_file_identity=expected_file_identity)

    monkeypatch.setattr(SingleInstance, "acquire", observed)
    recovered = WatchWriter(
        root=tmp_path, authorize=lambda _: None,
        fetch=lambda *_: pytest.fail("sealing recovery must not fetch"),
        notify=lambda *_: pytest.fail("sealing recovery must not notify"),
    )
    try:
        seal = recovered.seal_recovered_state_transition if version == "v2" else recovered.seal_state_transition
        record = seal(timeout=3)
        expected = [paths.writer_lock] if retained else [paths.historical_lock, paths.writer_lock]
        assert acquisitions == expected and paths.retained_lock not in acquisitions
        assert record["phase"] == "sealed-legacy" and record["lease"] == interrupted["lease"]
        if version == "v2":
            assert record["origin"] == interrupted["origin"]
            assert record["origin"]["phase"] == "prepared"
            assert record["controller"] == interrupted["controller"]
        assert recovered.writer_held and not recovered.read_ready
        assert not recovered.daemon.effects_enabled and recovered.daemon.quiescent
        assert paths.historical_lock.is_dir() and not paths.staged_barrier.exists()
        assert paths.legacy_subscriptions.read_bytes() == legacy
        assert not paths.isolated_subscriptions.parent.exists()
        with pytest.raises(PermissionError):
            SingleInstance(tmp_path, service="agent-pull-requests-watch").acquire()
        with pytest.raises(RuntimeError, match="read-ready"):
            recovered.activate(timeout=1)
    finally:
        recovered.close(timeout=3)
