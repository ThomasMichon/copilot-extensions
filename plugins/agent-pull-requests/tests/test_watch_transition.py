"""Preparation retains the original native writer and never activates migration."""

import json
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_storage import read_subscriptions_state, watch_state_paths
from agent_pull_requests.watch_writer import WatchWriter

pytestmark = pytest.mark.contract("agent_pull_requests.watch.writer_handoff")


@pytest.fixture
def writer(tmp_path: Path) -> Iterator[WatchWriter]:
    owner = WatchWriter(
        root=tmp_path, authorize=lambda _: None,
        fetch=lambda *_: PRSnapshot(pr_state="open"),
    )
    yield owner
    owner.close(timeout=3)


def test_preparation_drains_but_retains_original_lease_and_legacy_bytes(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    paths = watch_state_paths(tmp_path)
    paths.legacy_subscriptions.write_text(" [ ] \n", encoding="utf-8")
    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    result = writer.prepare_state_transition(timeout=1)
    assert result["phase"] == "prepared"
    assert writer.writer_held and not writer.read_ready
    assert writer.daemon.quiescent and not writer.daemon.effects_enabled
    contender = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; from single_instance_lease import SingleInstance, AlreadyRunningError\n"
            "try: SingleInstance(sys.argv[1], service='agent-pull-requests-watch').acquire()\n"
            "except AlreadyRunningError: raise SystemExit(0)\n"
            "raise SystemExit(3)\n",
            str(tmp_path),
        ],
        capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    assert contender.returncode == 0, contender.stderr
    assert paths.historical_lock.is_file()
    assert not paths.retained_lock.exists()
    assert not paths.staged_barrier.exists()
    assert not paths.isolated_subscriptions.parent.exists()
    original = paths.legacy_subscriptions.read_bytes()
    with pytest.raises(ValueError, match="guarded recovery"):
        read_subscriptions_state(tmp_path)
    with pytest.raises(RuntimeError, match="read-ready"):
        writer.activate(timeout=1)
    assert paths.legacy_subscriptions.read_bytes() == original


def test_prepared_recovery_reuses_only_original_inode_and_retains_latest_legacy(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    paths = watch_state_paths(tmp_path)
    writer.adopt(timeout=1)
    first = writer.prepare_state_transition(timeout=1)
    writer.close(timeout=1)
    # A historical writer can still publish before sealing. Preparation is not import.
    paths.legacy_subscriptions.write_bytes(b"[]\n")
    recovered = WatchWriter(
        root=tmp_path, authorize=lambda _: None,
        fetch=lambda *_: pytest.fail("preparation must not fetch"),
    )
    try:
        second = recovered.prepare_state_transition(timeout=1)
        assert second == first
        assert recovered.writer_held and not recovered.read_ready
        assert paths.legacy_subscriptions.read_bytes() == b"[]\n"
        assert not paths.isolated_subscriptions.parent.exists()
    finally:
        recovered.close(timeout=1)


@pytest.mark.parametrize("stage", ["authority", "drain", "commit"])
def test_failed_preparation_retains_custody_and_publishes_no_marker(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    from agent_pull_requests import watch_transition

    writer.adopt(timeout=1)
    writer.activate(timeout=1)

    def refused(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("controlled preparation refusal")

    with monkeypatch.context() as failed:
        if stage == "authority":
            failed.setattr(writer, "authorize", refused)
        elif stage == "drain":
            failed.setattr(writer.daemon, "quiesce", refused)
        else:
            failed.setattr(watch_transition, "write_private", refused)
        with pytest.raises(PermissionError, match="controlled"):
            writer.prepare_state_transition(timeout=1)
    assert writer.writer_held
    assert not watch_state_paths(tmp_path).journal.exists()
    if stage == "commit":
        assert watch_state_paths(tmp_path).preparation_pin.exists()
        with pytest.raises(ValueError, match="no journal"):
            read_subscriptions_state(tmp_path)


def test_preparation_does_not_create_a_missing_historical_root(tmp_path: Path) -> None:
    root = tmp_path / "not-yet-a-legacy-root"
    owner = WatchWriter(root=root, authorize=lambda _: None, fetch=lambda *_: pytest.fail("must not fetch"))
    try:
        with pytest.raises(FileNotFoundError):
            owner.prepare_state_transition(timeout=1)
        assert not owner.writer_held
        assert not root.exists()
    finally:
        owner.close(timeout=1)


def test_durability_uncertainty_retains_marker_and_same_owner_can_recover(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition
    from agent_pull_requests.watch_storage import StateCommitUncertain

    writer.adopt(timeout=1)
    persist = watch_transition.write_private

    def uncertain(*args: Any, **kwargs: Any) -> None:
        persist(*args, **kwargs)
        raise StateCommitUncertain("controlled acknowledgement loss")

    with monkeypatch.context() as failed:
        failed.setattr(watch_transition, "write_private", uncertain)
        with pytest.raises(StateCommitUncertain, match="acknowledgement loss"):
            writer.prepare_state_transition(timeout=1)
    original = watch_state_paths(tmp_path).journal.read_bytes()
    assert writer.writer_held and not writer.read_ready
    assert writer.prepare_state_transition(timeout=1)["phase"] == "prepared"
    assert watch_state_paths(tmp_path).journal.read_bytes() == original


def test_final_guard_refuses_lost_native_custody_without_reacquiring(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition

    writer.adopt(timeout=1)
    persist = watch_transition.write_private

    def released(path: Path, document: dict[str, Any], **kwargs: Any) -> None:
        writer._lease.release()
        return persist(path, document, **kwargs)

    monkeypatch.setattr(watch_transition, "write_private", released)
    with pytest.raises(PermissionError, match="not held"):
        writer.prepare_state_transition(timeout=1)
    assert not writer.writer_held
    assert not watch_state_paths(tmp_path).journal.exists()


@pytest.mark.parametrize("change", ["binding", "inode", "phase", "extra"])
def test_prepared_recovery_refuses_conflicting_evidence(
    writer: WatchWriter, tmp_path: Path, change: str,
) -> None:
    paths = watch_state_paths(tmp_path)
    writer.adopt(timeout=1)
    writer.prepare_state_transition(timeout=1)
    writer.close(timeout=1)
    document = json.loads(paths.journal.read_text(encoding="utf-8"))
    if change == "binding":
        document["binding"]["stateRoot"] += "-foreign"
    elif change == "inode":
        document["lease"]["inode"] += 1
    elif change == "phase":
        document["phase"] = "sealed-legacy"
    else:
        document["unexpected"] = True
    paths.journal.write_text(json.dumps(document), encoding="utf-8")
    original = paths.journal.read_bytes()
    recovered = WatchWriter(
        root=tmp_path, authorize=lambda _: None,
        fetch=lambda *_: pytest.fail("must not fetch"),
    )
    try:
        with pytest.raises((ValueError, PermissionError)):
            recovered.prepare_state_transition(timeout=1)
        assert not recovered.writer_held
        assert paths.journal.read_bytes() == original
    finally:
        recovered.close(timeout=1)


def test_preparation_rechecks_authority_before_atomic_publication(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition

    writer.adopt(timeout=1)
    persist = watch_transition.write_private
    calls: list[str] = []

    def changed(path: Path, document: dict[str, Any], **kwargs: Any) -> None:
        def refused(_action: str) -> None:
            calls.append("refused")
            raise PermissionError("authority changed before publication")

        writer.authorize = refused
        return persist(path, document, **kwargs)

    monkeypatch.setattr(watch_transition, "write_private", changed)
    with pytest.raises(PermissionError, match="changed before publication"):
        writer.prepare_state_transition(timeout=1)
    assert calls == ["refused"]
    assert writer.writer_held
    assert not watch_state_paths(tmp_path).journal.exists()


@pytest.mark.parametrize("change", ["missing", "replacement"])
def test_preparation_pins_prechecked_inode_before_acquisition(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    paths = watch_state_paths(tmp_path)
    paths.historical_lock.write_bytes(b"original")
    acquire = writer._lease.acquire

    def raced(**kwargs: Any) -> None:
        paths.historical_lock.rename(tmp_path / "displaced.lock")
        if change == "replacement":
            paths.historical_lock.write_bytes(b"replacement")
        acquire(**kwargs)

    monkeypatch.setattr(writer._lease, "acquire", raced)
    with pytest.raises((OSError, PermissionError)):
        writer.prepare_state_transition(timeout=1)
    assert not writer.writer_held
    assert not paths.journal.exists()
    assert (tmp_path / "displaced.lock").read_bytes() == b"original"
    if change == "missing":
        assert not paths.historical_lock.exists()
    else:
        assert paths.historical_lock.read_bytes() == b"replacement"


@pytest.mark.parametrize("boundary", ["before-drain", "after-workers"])
def test_preparation_preserves_malformed_legacy_entries(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    paths = watch_state_paths(tmp_path)
    malformed = b"[17]\n"
    if boundary == "before-drain":
        paths.legacy_subscriptions.write_bytes(malformed)
    else:
        stop = writer.daemon._stop_workers

        def changed(*args: Any, **kwargs: Any) -> None:
            stop(*args, **kwargs)
            paths.legacy_subscriptions.write_bytes(malformed)

        monkeypatch.setattr(writer.daemon, "_stop_workers", changed)
    with pytest.raises((ValueError, OSError)):
        writer.prepare_state_transition(timeout=1)
    assert paths.legacy_subscriptions.read_bytes() == malformed
    assert not paths.journal.exists()


def test_preparation_rechecks_native_custody_at_drain_replacement(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_storage

    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    paths = watch_state_paths(tmp_path)
    paths.legacy_subscriptions.write_bytes(b" [ ] \n")
    persist = watch_storage._atomic_write_json

    def released(path: Path, data: object, **kwargs: Any) -> None:
        writer._lease.release()
        persist(path, data, **kwargs)

    monkeypatch.setattr(watch_storage, "_atomic_write_json", released)
    with pytest.raises(PermissionError, match="not held"):
        writer.prepare_state_transition(timeout=1)
    assert paths.legacy_subscriptions.read_bytes() == b" [ ] \n"
    assert not paths.journal.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows prevents moving an open lease")
@pytest.mark.parametrize("boundary", ["before-drain", "before-replace"])
def test_preparation_refuses_displaced_custody_before_any_drain_write(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    from agent_pull_requests import watch_storage

    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    paths = watch_state_paths(tmp_path)
    paths.legacy_subscriptions.write_bytes(b" [ ] \n")

    def displace() -> None:
        paths.historical_lock.rename(tmp_path / "displaced.lock")
        paths.historical_lock.write_bytes(b"replacement-owner")

    if boundary == "before-drain":
        displace()
    else:
        persist = watch_storage._atomic_write_json

        def changed(path: Path, data: object, **kwargs: Any) -> None:
            displace()
            persist(path, data, **kwargs)

        monkeypatch.setattr(watch_storage, "_atomic_write_json", changed)
    with pytest.raises(PermissionError):
        writer.prepare_state_transition(timeout=1)
    assert paths.legacy_subscriptions.read_bytes() == b" [ ] \n"
    assert paths.historical_lock.read_bytes() == b"replacement-owner"
    assert not paths.journal.exists()


def test_preparation_timeout_during_drain_can_resume_retained_work(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer.adopt(timeout=1)
    writer.activate(timeout=1)

    def unfinished(*_args: Any, **_kwargs: Any) -> None:
        raise TimeoutError("controlled unfinished drain")

    with monkeypatch.context() as blocked:
        blocked.setattr(writer.daemon, "_stop_workers", unfinished)
        with pytest.raises(TimeoutError, match="unfinished drain"):
            writer.prepare_state_transition(timeout=1)
    assert writer.writer_held and writer.read_ready
    assert not writer.daemon.quiescent
    assert not watch_state_paths(tmp_path).journal.exists()
    writer.resume(timeout=1)
    assert writer.daemon.effects_enabled


def test_preparation_deadline_is_checked_after_final_publication_reads(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_transition

    writer.adopt(timeout=1)
    expired = False
    publishing = False
    read = watch_transition.read_preparation
    publish = watch_transition.write_private

    def remaining(_deadline: float) -> float:
        if expired:
            raise TimeoutError("controlled final-read deadline")
        return 1.0

    def late_read(*args: Any, **kwargs: Any) -> Any:
        nonlocal expired
        result = read(*args, **kwargs)
        if publishing:
            expired = True
        return result

    def about_to_publish(*args: Any, **kwargs: Any) -> None:
        nonlocal publishing
        publishing = True
        publish(*args, **kwargs)

    with monkeypatch.context() as late:
        late.setattr(writer, "_remaining", remaining)
        late.setattr(watch_transition, "read_preparation", late_read)
        late.setattr(watch_transition, "write_private", about_to_publish)
        with pytest.raises(TimeoutError, match="final-read deadline"):
            writer.prepare_state_transition(timeout=1)
    assert not watch_state_paths(tmp_path).journal.exists()
    assert not writer.read_ready


def test_prepared_recovery_retains_the_original_inode_across_unlink_recreate(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    paths = watch_state_paths(tmp_path)
    pin = tmp_path / "watch-state-transition.pin"
    writer.adopt(timeout=1)
    prepared = writer.prepare_state_transition(timeout=1)
    assert pin.is_file()
    identity = (prepared["lease"]["device"], prepared["lease"]["inode"])
    assert (pin.stat().st_dev, pin.stat().st_ino) == identity
    writer.close(timeout=1)
    paths.historical_lock.unlink()
    paths.historical_lock.write_bytes(b"replacement")
    assert (pin.stat().st_dev, pin.stat().st_ino) == identity
    assert (paths.historical_lock.stat().st_dev, paths.historical_lock.stat().st_ino) != identity
    recovered = WatchWriter(root=tmp_path, authorize=lambda _: None, fetch=lambda *_: PRSnapshot())
    try:
        with pytest.raises(PermissionError):
            recovered.prepare_state_transition(timeout=1)
        assert not recovered.writer_held
        assert paths.historical_lock.read_bytes() == b"replacement"
    finally:
        recovered.close(timeout=1)


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_preparation_rejects_nonplain_legacy_source_before_drain(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    paths = watch_state_paths(tmp_path)
    paths.legacy_subscriptions.write_bytes(b" [ ] \n")
    lstat = Path.lstat

    def selected(path: Path) -> Any:
        info = lstat(path)
        if path == paths.legacy_subscriptions:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if kind == "symlink" else info.st_mode,
                st_file_attributes=0x400 if kind == "reparse" else 0,
            )
        return info

    with monkeypatch.context() as linked:
        linked.setattr(Path, "lstat", selected)
        with pytest.raises(PermissionError, match="non-link"):
            writer.prepare_state_transition(timeout=1)
    assert paths.legacy_subscriptions.read_bytes() == b" [ ] \n"
    assert not paths.journal.exists()
    assert not writer.daemon.effects_enabled and not writer.read_ready


def test_rejected_state_remains_protected_after_preparation_returns(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    path = watch_state_paths(tmp_path).legacy_subscriptions
    path.write_bytes(b"[17]\n")
    with pytest.raises((ValueError, OSError)):
        writer.prepare_state_transition(timeout=1)
    assert not writer.daemon.effects_enabled and not writer.read_ready
    with pytest.raises((ValueError, OSError)):
        writer.daemon._persist()
    assert path.read_bytes() == b"[17]\n"
    assert writer.writer_held


@pytest.mark.parametrize("when", ["during-drain", "before-resume"])
def test_failed_drain_rechecks_custody_before_retaining_abort_readiness(
    writer: WatchWriter, monkeypatch: pytest.MonkeyPatch, when: str,
) -> None:
    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    displaced = False
    identity = writer._lease.held_file_identity

    def custody() -> tuple[int, int]:
        if displaced:
            raise PermissionError("controlled displaced inode")
        return identity()

    def unfinished(*_args: Any, **_kwargs: Any) -> None:
        nonlocal displaced
        displaced = when == "during-drain"
        raise TimeoutError("controlled drain timeout")

    with monkeypatch.context() as race:
        race.setattr(writer._lease, "held_file_identity", custody)
        race.setattr(writer.daemon, "_stop_workers", unfinished)
        with pytest.raises(TimeoutError, match="drain timeout"):
            writer.prepare_state_transition(timeout=1)
        assert writer.read_ready is (when == "before-resume")
        displaced = True
        with pytest.raises((PermissionError, RuntimeError)):
            writer.resume(timeout=1)
        assert not writer.daemon.effects_enabled and not writer.read_ready


def test_rejected_state_stops_a_real_poll_worker_without_releasing_its_lease(
    tmp_path: Path,
) -> None:
    entered, proceed = threading.Event(), threading.Event()
    notified: list[Any] = []

    def fetch(_repo: str, _number: int) -> PRSnapshot:
        entered.set()
        assert proceed.wait(3)
        return PRSnapshot(merged=True, pr_state="closed")

    owner = WatchWriter(
        root=tmp_path, authorize=lambda _: None, fetch=fetch,
        notify=lambda event: notified.append(event), poll_interval=0.01,
    )
    path = watch_state_paths(tmp_path).legacy_subscriptions
    try:
        owner.adopt(timeout=1)
        owner.activate(timeout=1)
        assert owner.compute("register", {
            "repo": "example/project", "number": 1, "subscriber_id": "waiting",
            "until": ["merged"], "notify": {"argv": ["fixture-callback"]},
        })["registered"]
        assert entered.wait(1)
        path.write_bytes(b"[17]\n")
        with pytest.raises(ValueError):
            owner.prepare_state_transition(timeout=0.2)
        assert owner.writer_held and not owner.read_ready
        assert not owner.daemon.effects_enabled and not owner.daemon.quiescent
    finally:
        proceed.set()
        owner.close(timeout=3)
    assert not notified
    assert path.read_bytes() == b"[17]\n"


def test_storage_refusal_requires_readoption_before_resuming_a_repair(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_storage

    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    path = watch_state_paths(tmp_path).legacy_subscriptions
    publish = watch_storage._atomic_write_json

    def corrupted(*args: Any, **kwargs: Any) -> None:
        path.write_bytes(b"{")
        publish(*args, **kwargs)

    with monkeypatch.context() as race:
        race.setattr(watch_storage, "_atomic_write_json", corrupted)
        with pytest.raises(OSError):
            writer.prepare_state_transition(timeout=1)
    assert path.read_bytes() == b"{" and not writer.read_ready
    path.write_bytes(b"[]")
    with pytest.raises(RuntimeError, match="read-ready"):
        writer.resume(timeout=1)
    writer.adopt(timeout=1)
    assert writer.read_ready


@pytest.mark.parametrize("stage", ["drain", "marker"])
def test_preparation_never_recreates_a_moved_historical_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    from agent_pull_requests import watch_storage, watch_transition

    root = tmp_path / "historical"
    moved = tmp_path / "moved"
    owner = WatchWriter(root=root, authorize=lambda _: None, fetch=lambda *_: PRSnapshot())
    owner.adopt(timeout=1)
    if stage == "drain":
        owner.activate(timeout=1)
    module, name = (
        (watch_storage, "_atomic_write_json") if stage == "drain"
        else (watch_transition, "write_private")
    )
    publish = getattr(module, name)

    def moved_before_write(*args: Any, **kwargs: Any) -> None:
        owner._lease.release()
        root.rename(moved)
        publish(*args, **kwargs)

    try:
        monkeypatch.setattr(module, name, moved_before_write)
        with pytest.raises((OSError, PermissionError)):
            owner.prepare_state_transition(timeout=1)
        assert moved.is_dir() and not root.exists()
    finally:
        owner.close(timeout=1)
