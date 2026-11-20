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
from test_watch_transition_recovery import prepared as prepared
from zdd.cutover_lock import CutoverLock

from agent_pull_requests import watch_transition_recovery, watch_windows_sealing
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_private import windows_move_no_replace, write_private
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


@pytest.mark.parametrize("boundary", ["none", "staging", "release", "move", "barrier", "acquisition", "sealed"])
def test_current_sealer_progresses_real_v2_preparation_and_recovers_native_crash_windows(
    prepared: tuple[WatchInstallation, WatchInstallation, Document],
    monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    old, current, origin = prepared
    paths = watch_state_paths(current.state_root)
    paths.legacy_subscriptions.write_bytes(b" [] \n")
    owner = _writer(current.state_root)
    publish, move = write_private, windows_move_no_replace
    acquire = watch_windows_sealing._acquire_barrier_lease
    release = owner._lease.release

    def interrupted_write(path: Path, document: Document, **kwargs: Any) -> None:
        publish(path, document, **kwargs)
        if (document["phase"] == "prepared") == (boundary == "staging"):
            raise StateCommitUncertain("controlled v2 sealing publication acknowledgement loss")

    def interrupted_move(source: Path, destination: Path) -> None:
        move(source, destination)
        if destination == (paths.retained_lock if boundary == "move" else paths.historical_lock):
            raise OSError("controlled v2 sealing move acknowledgement loss")

    def interrupted_acquire(*args: Any) -> None:
        acquire(*args)
        raise OSError("controlled v2 sealing acquisition acknowledgement loss")

    def interrupted_release() -> None:
        release()
        raise OSError("controlled v2 sealing release acknowledgement loss")

    try:
        with current.transaction(timeout=3, environment={}) as guard:
            owner.recover_prepared_state_transition(timeout=10, installation=current, guard=guard)
            if boundary == "none":
                owner.seal_recovered_state_transition(timeout=10, installation=current, guard=guard)
            else:
                with monkeypatch.context() as failed:
                    if boundary in ("staging", "sealed"):
                        failed.setattr(watch_windows_sealing, "write_private", interrupted_write)
                    elif boundary == "release":
                        failed.setattr(owner._lease, "release", interrupted_release)
                    elif boundary == "acquisition":
                        failed.setattr(watch_windows_sealing, "_acquire_barrier_lease", interrupted_acquire)
                    else:
                        failed.setattr(watch_windows_sealing, "windows_move_no_replace", interrupted_move)
                    with pytest.raises(OSError, match="controlled v2 sealing"):
                        owner.seal_recovered_state_transition(timeout=10, installation=current, guard=guard)
    finally:
        owner.close(timeout=3)
    successor = _writer(current.state_root)
    acquisitions: list[Path] = []
    native_acquire = successor._lease.acquire

    def observed(*, expected_file_identity: tuple[int, int] | None = None) -> None:
        acquisitions.append(successor._lease.lock_path)
        native_acquire(expected_file_identity=expected_file_identity)

    monkeypatch.setattr(successor._lease, "acquire", observed)
    try:
        with current.transaction(timeout=3, environment={}) as guard:
            result = successor.seal_recovered_state_transition(
                timeout=10, installation=current, guard=guard,
            )
        assert result["version"] == 2 and result["phase"] == "sealed-legacy"
        assert result["origin"] == origin and result["origin"]["phase"] == "prepared"
        assert result["controller"]["installation"] == current.evidence()
        assert all(result[field] == origin[field] for field in ("schema", "binding", "lease"))
        assert paths.retained_lock not in acquisitions and acquisitions[-1] == paths.writer_lock
        assert successor.writer_held and successor._lease.lock_path == paths.writer_lock
        assert not successor.read_ready and successor.daemon.quiescent
        assert not successor.daemon.effects_enabled and not old.payload_root.exists()
        assert paths.legacy_subscriptions.read_bytes() == b" [] \n"
        assert not paths.staged_barrier.exists() and not paths.isolated_subscriptions.parent.exists()
        serialized = paths.journal.read_bytes()
        with pytest.raises(PermissionError):
            watch_windows_sealing.read_sealing(current.state_root, origin["binding"])
        with pytest.raises(ValueError, match="guarded recovery"):
            read_subscriptions_state(current.state_root)
        with pytest.raises(OSError, match="guarded recovery"):
            write_subscriptions_state([], current.state_root)
        with pytest.raises(RuntimeError, match="read-ready"):
            successor.activate(timeout=3)
        assert paths.journal.read_bytes() == serialized
    finally:
        successor.close(timeout=3)


@pytest.mark.parametrize("custody", ["missing", "unheld", "foreign"])
def test_current_sealer_requires_exact_native_installation_guard_before_effects(
    prepared: tuple[WatchInstallation, WatchInstallation, Document],
    tmp_path: Path, custody: str,
) -> None:
    _, current, _ = prepared
    owner = _writer(current.state_root)
    try:
        with current.transaction(timeout=3, environment={}) as guard:
            owner.recover_prepared_state_transition(timeout=10, installation=current, guard=guard)
    finally:
        owner.close(timeout=3)
    guard = CutoverLock(tmp_path / "foreign" if custody == "foreign" else current.run_root)
    if custody == "foreign":
        guard.acquire()
    successor = _writer(current.state_root)
    paths = watch_state_paths(current.state_root)
    prior = paths.journal.read_bytes()
    try:
        with pytest.raises((PermissionError, RuntimeError)):
            successor.seal_recovered_state_transition(
                timeout=3, installation=current, guard=None if custody == "missing" else guard,
            )
        assert not successor.writer_held and paths.journal.read_bytes() == prior
        assert not paths.staged_barrier.exists() and not paths.retained_lock.exists()
    finally:
        guard.release()
        successor.close(timeout=3)


@pytest.mark.parametrize("boundary", ["staging", "sealed"])
def test_current_sealer_final_publication_rechecks_actual_native_transaction(
    prepared: tuple[WatchInstallation, WatchInstallation, Document],
    monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    _, current, _ = prepared
    owner = _writer(current.state_root)
    paths = watch_state_paths(current.state_root)
    publish = write_private
    before: list[bytes] = []
    try:
        with current.transaction(timeout=3, environment={}) as guard:
            owner.recover_prepared_state_transition(timeout=10, installation=current, guard=guard)

            def lost_guard(path: Path, document: Document, **kwargs: Any) -> None:
                if (document["phase"] == "prepared") == (boundary == "staging"):
                    before.append(paths.journal.read_bytes())
                    guard.release()
                publish(path, document, **kwargs)

            with monkeypatch.context() as failed:
                failed.setattr(watch_windows_sealing, "write_private", lost_guard)
                with pytest.raises((PermissionError, RuntimeError)):
                    owner.seal_recovered_state_transition(timeout=10, installation=current, guard=guard)
        assert before and paths.journal.read_bytes() == before[0]
        assert not owner.read_ready and not owner.daemon.effects_enabled
        assert not paths.isolated_subscriptions.parent.exists()
    finally:
        owner.close(timeout=3)


def test_later_current_sealer_recovers_interrupted_v2_without_original_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _installation(tmp_path)
    old.state_root.mkdir(parents=True)
    owner = _writer(old.state_root)
    paths = watch_state_paths(old.state_root)
    move = windows_move_no_replace

    def interrupted(source: Path, destination: Path) -> None:
        move(source, destination)
        raise OSError("controlled prior-controller move acknowledgement loss")

    try:
        owner.adopt(timeout=3)
        origin = owner.prepare_state_transition(timeout=3, installation=old)
        with old.transaction(timeout=3, environment={}) as guard:
            owner.recover_prepared_state_transition(timeout=10, installation=old, guard=guard)
            with monkeypatch.context() as failed:
                failed.setattr(watch_windows_sealing, "windows_move_no_replace", interrupted)
                with pytest.raises(OSError, match="controlled prior-controller"):
                    owner.seal_recovered_state_transition(timeout=10, installation=old, guard=guard)
    finally:
        owner.close(timeout=3)
    assert not paths.historical_lock.exists() and paths.retained_lock.is_file()
    staged = json.loads(paths.journal.read_bytes())
    advance = cast(Callable[[WatchInstallation, Path], WatchInstallation], _advance_installation)
    current = advance(old, tmp_path)
    old.payload_root.rmdir()
    successor = _writer(current.state_root)
    try:
        with current.transaction(timeout=3, environment={}) as guard:
            result = successor.seal_recovered_state_transition(
                timeout=10, installation=current, guard=guard,
            )
        assert result["origin"] == origin and result["seal"] == staged["seal"]
        assert result["controller"]["installation"] == current.evidence()
        assert result["binding"]["installation"] == old.evidence()
        assert result["phase"] == "sealed-legacy" and not old.payload_root.exists()
        assert successor._lease.lock_path == paths.writer_lock and successor.writer_held
        assert not successor.read_ready and not successor.daemon.effects_enabled
        assert not paths.isolated_subscriptions.parent.exists()
    finally:
        successor.close(timeout=3)


@pytest.mark.parametrize("overflow", ["seal", "phase"])
def test_current_sealer_size_refusal_preserves_actual_native_topology(
    prepared: tuple[WatchInstallation, WatchInstallation, Document],
    monkeypatch: pytest.MonkeyPatch, overflow: str,
) -> None:
    _, current, _ = prepared
    paths = watch_state_paths(current.state_root)
    owner = _writer(current.state_root)
    try:
        with current.transaction(timeout=3, environment={}) as guard:
            owner.recover_prepared_state_transition(timeout=10, installation=current, guard=guard)
            if overflow == "phase":
                def staged(*args: Any, **kwargs: Any) -> None:
                    write_private(*args, **kwargs)
                    raise StateCommitUncertain("controlled size fixture staging acknowledgement loss")

                with monkeypatch.context() as failed:
                    failed.setattr(watch_windows_sealing, "write_private", staged)
                    with pytest.raises(StateCommitUncertain):
                        owner.seal_recovered_state_transition(timeout=10, installation=current, guard=guard)
    finally:
        owner.close(timeout=3)
    prior = paths.journal.read_bytes()
    topology = sorted((path.name, path.lstat().st_ino) for path in current.state_root.iterdir())
    # Keep a valid prepared journal exactly within budget, without oversized fixture data.
    monkeypatch.setattr(watch_windows_sealing, "_MAX_METADATA_BYTES", len(prior))
    successor = _writer(current.state_root)
    try:
        with current.transaction(timeout=3, environment={}) as guard:
            with pytest.raises(ValueError, match="bounded size"):
                successor.seal_recovered_state_transition(timeout=10, installation=current, guard=guard)
        assert not successor.writer_held and not successor.read_ready
        assert paths.journal.read_bytes() == prior
        assert sorted((path.name, path.lstat().st_ino) for path in current.state_root.iterdir()) == topology
    finally:
        successor.close(timeout=3)


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


@pytest.mark.parametrize("origin_kind", ["prepared", "staged"])
def test_phase_progression_preserves_actual_prepared_receipt_under_current_native_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin_kind: str,
) -> None:
    original = _installation(tmp_path)
    original.state_root.mkdir(parents=True)
    owner = _writer(original.state_root)
    origins: dict[str, Document] = {}
    publish = write_private

    def capture(*args: Any, **kwargs: Any) -> None:
        publish(*args, **kwargs)
        if args[1]["phase"] == "prepared":
            origins["staged"] = json.loads(json.dumps(args[1]))

    try:
        owner.adopt(timeout=3)
        origins["prepared"] = owner.prepare_state_transition(timeout=3, installation=original)
        with monkeypatch.context() as recorded:
            recorded.setattr(watch_windows_sealing, "write_private", capture)
            sealed = owner.seal_state_transition(timeout=3, installation=original)
    finally:
        owner.close(timeout=3)
    origin = origins[origin_kind]
    paths = watch_state_paths(original.state_root)
    progressed = {**sealed, "version": 2, "origin": origin, "controller": origin["binding"]}
    write_private(paths.journal, progressed, create_parent=False, before_publish=lambda: None)
    advance = cast(Callable[[WatchInstallation, Path], WatchInstallation], _advance_installation)
    current = advance(original, tmp_path)
    original.payload_root.rmdir()
    legacy = b" [] \n"
    paths.legacy_subscriptions.write_bytes(legacy)
    receiver = _writer(current.state_root)
    try:
        with current.transaction(timeout=3, environment={}) as guard:
            result = receiver.recover_sealed_state_transition(
                timeout=10, installation=current, guard=guard,
            )
        assert result["origin"] == origin and result["origin"]["phase"] == "prepared"
        assert all(result[field] == progressed[field] for field in ("binding", "lease", "seal", "phase"))
        assert result["controller"]["installation"] == current.evidence()
        assert receiver._lease.lock_path == paths.writer_lock and receiver.writer_held
        assert not receiver.read_ready and not receiver.daemon.effects_enabled
        assert receiver.daemon.quiescent and not paths.isolated_subscriptions.parent.exists()
        assert paths.legacy_subscriptions.read_bytes() == legacy and not original.payload_root.exists()
        prior = paths.journal.read_bytes()
        with pytest.raises(PermissionError):
            watch_windows_sealing.read_sealing(current.state_root, origin["binding"])
        with pytest.raises(ValueError, match="guarded recovery"):
            read_subscriptions_state(current.state_root)
        with pytest.raises(OSError, match="guarded recovery"):
            write_subscriptions_state([], current.state_root)
        assert paths.journal.read_bytes() == prior
    finally:
        receiver.close(timeout=3)
    successor = _writer(current.state_root)
    try:
        with current.transaction(timeout=3, environment={}) as guard:
            assert successor.recover_sealed_state_transition(
                timeout=10, installation=current, guard=guard,
            ) == result
        assert successor._lease.lock_path == paths.writer_lock and successor.writer_held
        assert paths.journal.read_bytes() == prior
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
