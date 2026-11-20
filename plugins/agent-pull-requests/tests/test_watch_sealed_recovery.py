"""Native Windows receiver adoption; no import, activation or boot probe."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from test_watch_installation import _installation
from test_watch_runtime import _advance_installation
from test_watch_transition_recovery import _writer
from zdd.cutover_lock import CutoverLock

from agent_pull_requests import watch_transition_recovery, watch_windows_sealing
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_storage import (
    StateCommitUncertain, read_subscriptions_state, read_transition_phase, watch_state_paths,
    write_subscriptions_state,
)
from agent_pull_requests.watch_writer import WatchWriter

pytestmark = [
    pytest.mark.contract("agent_pull_requests.watch.writer_handoff"),
    pytest.mark.skipif(sys.platform != "win32", reason="native Windows sealed receiver"),
]
Document = dict[str, Any]


@pytest.fixture
def sealed(tmp_path: Path) -> tuple[WatchInstallation, WatchInstallation, Document]:
    original = _installation(tmp_path)
    original.state_root.mkdir(parents=True)
    owner = _writer(original.state_root)
    try:
        owner.adopt(timeout=3)
        owner.prepare_state_transition(timeout=3, installation=original)
        document = owner.seal_state_transition(timeout=3, installation=original)
    finally:
        owner.close(timeout=3)
    advance = cast(Callable[[WatchInstallation, Path], WatchInstallation], _advance_installation)
    current = advance(original, tmp_path)
    original.payload_root.rmdir()
    return original, current, document


@pytest.fixture
def receiver(sealed: tuple[WatchInstallation, WatchInstallation, Document]) -> Iterator[WatchWriter]:
    owner = _writer(sealed[1].state_root)
    yield owner
    owner.close(timeout=3)


def test_current_sealed_receiver_keeps_origin_and_native_barrier_without_old_executor(
    sealed: tuple[WatchInstallation, WatchInstallation, Document], receiver: WatchWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old, current, origin = sealed
    paths = watch_state_paths(current.state_root)
    legacy = b" [] \n"
    paths.legacy_subscriptions.write_bytes(legacy)
    acquire = receiver._lease.acquire
    acquisitions: list[Path] = []

    def observed(*, expected_file_identity: tuple[int, int] | None = None) -> None:
        acquisitions.append(receiver._lease.lock_path)
        acquire(expected_file_identity=expected_file_identity)

    monkeypatch.setattr(receiver._lease, "acquire", observed)
    with current.transaction(timeout=3, environment={}) as guard:
        result = receiver.recover_sealed_state_transition(
            timeout=10, installation=current, guard=guard,
        )
    assert result["version"] == 2 and result["phase"] == "sealed-legacy"
    assert result["origin"] == origin
    assert all(result[field] == origin[field] for field in ("binding", "lease", "seal"))
    assert result["controller"]["installation"] == current.evidence()
    assert acquisitions == [paths.writer_lock] and receiver.writer_held
    assert not old.payload_root.exists()
    assert not receiver.read_ready and receiver.daemon.quiescent
    assert not receiver.daemon.effects_enabled
    assert paths.legacy_subscriptions.read_bytes() == legacy
    assert not paths.isolated_subscriptions.parent.exists()
    assert read_transition_phase(current.state_root) == "sealed-legacy"
    serialized = paths.journal.read_bytes()
    with pytest.raises(PermissionError):
        watch_windows_sealing.read_sealing(current.state_root, origin["binding"])
    with pytest.raises(ValueError, match="guarded recovery"):
        read_subscriptions_state(current.state_root)
    with pytest.raises(OSError, match="guarded recovery"):
        write_subscriptions_state([], current.state_root)
    with pytest.raises(RuntimeError, match="read-ready"):
        receiver.activate(timeout=3)
    assert paths.journal.read_bytes() == serialized
    receiver.close(timeout=3)
    successor = _writer(current.state_root)
    try:
        with current.transaction(timeout=3, environment={}) as guard:
            assert successor.recover_sealed_state_transition(
                timeout=10, installation=current, guard=guard,
            ) == result
        assert paths.journal.read_bytes() == serialized
        assert successor.writer_held and not successor.read_ready
    finally:
        successor.close(timeout=3)


@pytest.mark.parametrize("custody", ["missing", "unheld", "foreign"])
def test_sealed_receiver_requires_exact_current_transaction_before_native_custody(
    sealed: tuple[WatchInstallation, WatchInstallation, Document], receiver: WatchWriter,
    tmp_path: Path, custody: str,
) -> None:
    _, current, _ = sealed
    guard = CutoverLock(tmp_path / "foreign" if custody == "foreign" else current.run_root)
    if custody == "foreign":
        guard.acquire()
    paths = watch_state_paths(current.state_root)
    prior = paths.journal.read_bytes()
    try:
        with pytest.raises((PermissionError, RuntimeError)):
            receiver.recover_sealed_state_transition(
                timeout=3, installation=current, guard=None if custody == "missing" else guard,
            )
    finally:
        guard.release()
    assert not receiver.writer_held and paths.journal.read_bytes() == prior


def test_sealed_receiver_uncertain_write_keeps_native_custody_and_reconciles_origin(
    sealed: tuple[WatchInstallation, WatchInstallation, Document], receiver: WatchWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, current, origin = sealed
    publish = watch_transition_recovery.write_private

    def uncertain(*args: Any, **kwargs: Any) -> None:
        publish(*args, **kwargs)
        raise StateCommitUncertain("controlled sealed publication acknowledgement loss")

    paths = watch_state_paths(current.state_root)
    with current.transaction(timeout=3, environment={}) as guard:
        with monkeypatch.context() as failed:
            failed.setattr(watch_transition_recovery, "write_private", uncertain)
            with pytest.raises(StateCommitUncertain):
                receiver.recover_sealed_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
        prior = paths.journal.read_bytes()
        assert receiver.writer_held and not receiver.read_ready
        result = receiver.recover_sealed_state_transition(
            timeout=10, installation=current, guard=guard,
        )
    assert result["origin"] == origin and paths.journal.read_bytes() == prior
    assert not paths.isolated_subscriptions.parent.exists()


@pytest.mark.parametrize("change", ["admission", "native", "journal", "source", "barrier"])
def test_sealed_receiver_final_publication_rechecks_actual_admission_and_evidence(
    sealed: tuple[WatchInstallation, WatchInstallation, Document], receiver: WatchWriter,
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    _, current, _ = sealed
    paths = watch_state_paths(current.state_root)
    prior = paths.journal.read_bytes()
    publish = watch_transition_recovery.write_private

    def raced(*args: Any, **kwargs: Any) -> None:
        if change == "admission":
            policy = current.os_profile / ".copilot-extensions" / "installation-mode.json"
            value = json.loads(policy.read_text(encoding="utf-8"))
            value["installationMode"]["enabled"] = False
            policy.write_text(json.dumps(value), encoding="utf-8")
        elif change == "native":
            receiver._lease.release()
        elif change == "journal":
            paths.journal.write_bytes(prior + b"\n")
        elif change == "source":
            paths.legacy_subscriptions.write_bytes(b"[17]")
        else:
            receiver._lease.release()
            paths.writer_lock.unlink()
            paths.historical_lock.rmdir()
            paths.historical_lock.mkdir()
        publish(*args, **kwargs)

    with current.transaction(timeout=3, environment={}) as guard:
        with monkeypatch.context() as failed:
            failed.setattr(watch_transition_recovery, "write_private", raced)
            with pytest.raises((PermissionError, ValueError)):
                receiver.recover_sealed_state_transition(
                    timeout=10, installation=current, guard=guard,
                )
    assert paths.journal.read_bytes() == prior + (b"\n" if change == "journal" else b"")
    assert not receiver.read_ready and not receiver.daemon.effects_enabled
