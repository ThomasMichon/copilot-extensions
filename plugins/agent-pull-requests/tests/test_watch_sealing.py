"""Contained native sealing; no boot crossing or migration activation."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from single_instance_lease import SingleInstance

from agent_pull_requests.watch_storage import watch_state_paths
from agent_pull_requests.watch_writer import WatchWriter
from test_watch_transition import writer as writer

pytestmark = pytest.mark.contract("agent_pull_requests.watch.writer_handoff")


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
