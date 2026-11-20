"""Preparation rejection stays fenced through writes, recovery and effects."""

from __future__ import annotations

import stat
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_pull_requests.watch_contract import MERGED, PRSnapshot
from agent_pull_requests.watch_registry import FiredEvent, WatchKey
from agent_pull_requests.watch_storage import watch_state_paths
from agent_pull_requests.watch_writer import WatchWriter
from test_watch_installation import _installation, _policy
from test_watch_transition import writer as writer

pytestmark = pytest.mark.contract("agent_pull_requests.watch.writer_handoff")


def _safe_timeout(owner: WatchWriter, monkeypatch: pytest.MonkeyPatch) -> None:
    def unfinished(*_args: Any, **_kwargs: Any) -> None:
        raise TimeoutError("controlled unfinished drain")

    with monkeypatch.context() as blocked:
        blocked.setattr(owner.daemon, "_stop_workers", unfinished)
        with pytest.raises(TimeoutError, match="unfinished drain"):
            owner.prepare_state_transition(timeout=1)
    assert owner.read_ready


def test_late_guarded_storage_refusal_invalidates_a_safe_abort(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_storage

    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    _safe_timeout(writer, monkeypatch)
    path = watch_state_paths(tmp_path).legacy_subscriptions
    publish = watch_storage._atomic_write_json

    def corrupted(*args: Any, **kwargs: Any) -> None:
        path.write_bytes(b"{")
        publish(*args, **kwargs)

    with monkeypatch.context() as race:
        race.setattr(watch_storage, "_atomic_write_json", corrupted)
        with pytest.raises(OSError):
            writer.daemon._persist()
    assert not writer.read_ready and not writer.daemon.effects_enabled
    assert path.read_bytes() == b"{"
    path.write_bytes(b"[]")
    with pytest.raises(RuntimeError, match="read-ready"):
        writer.resume(timeout=1)
    writer.adopt(timeout=1)
    assert writer.read_ready
    assert writer._preparation_guard is None
    writer.activate(timeout=1)
    with monkeypatch.context() as race:
        race.setattr(watch_storage, "_atomic_write_json", corrupted)
        with pytest.raises(OSError):
            writer.daemon._persist()
    assert not writer.read_ready and not writer.daemon.effects_enabled
    path.write_bytes(b"[]")
    writer.adopt(timeout=1)


@pytest.mark.parametrize("when", ["existing", "during-read"])
def test_readoption_cannot_clear_a_preparation_guard_over_linked_state(
    writer: WatchWriter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, when: str,
) -> None:
    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    _safe_timeout(writer, monkeypatch)
    writer.daemon.quiesce(timeout=1, persist=False)
    path = watch_state_paths(tmp_path).legacy_subscriptions
    path.write_bytes(b"[]")
    linked = when == "existing"
    lstat, read_text, reload_state = Path.lstat, Path.read_text, writer.daemon.reload_state

    def metadata(selected: Path) -> Any:
        info = lstat(selected)
        if selected == path and linked:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
        return info

    def unqualified(selected: Path, *args: Any, **kwargs: Any) -> str:
        if selected == path and linked:
            pytest.fail("guarded readoption must not follow the linked source")
        return read_text(selected, *args, **kwargs)

    def changed(*args: Any, **kwargs: Any) -> None:
        nonlocal linked
        linked = True
        reload_state(*args, **kwargs)

    with monkeypatch.context() as race:
        race.setattr(Path, "lstat", metadata)
        race.setattr(Path, "read_text", unqualified)
        if when == "during-read":
            race.setattr(writer.daemon, "reload_state", changed)
        with pytest.raises(PermissionError, match="non-link"):
            writer.adopt(timeout=1)
        assert not writer.read_ready
        assert writer._preparation_guard is not None
    writer.adopt(timeout=1)
    assert writer.read_ready
    with monkeypatch.context() as race:
        race.setattr(Path, "lstat", metadata)
        race.setattr(Path, "read_text", unqualified)
        with pytest.raises(PermissionError, match="non-link"):
            writer.activate(timeout=1)
        assert not writer.read_ready and not writer.daemon.effects_enabled


def test_activation_cannot_bypass_guarded_resume_authority(
    writer: WatchWriter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer.adopt(timeout=1)
    writer.activate(timeout=1)
    _safe_timeout(writer, monkeypatch)
    with pytest.raises(RuntimeError, match="preparation"):
        writer.activate(timeout=1)
    assert not writer.daemon.effects_enabled
    writer.resume(timeout=1)
    assert writer.daemon.effects_enabled


def test_cold_adoption_cannot_recreate_a_recorded_historical_lease(
    writer: WatchWriter, tmp_path: Path,
) -> None:
    writer.adopt(timeout=1)
    writer.prepare_state_transition(timeout=1)
    writer.close(timeout=1)
    paths = watch_state_paths(tmp_path)
    paths.historical_lock.unlink()
    reader = WatchWriter(root=tmp_path, authorize=lambda _: None, fetch=lambda *_: PRSnapshot())
    try:
        with pytest.raises(ValueError, match="guarded recovery"):
            reader.adopt(timeout=1)
        assert not reader.writer_held and not paths.historical_lock.exists()
    finally:
        reader.close(timeout=1)


def test_lost_custody_retains_queued_legacy_work_until_valid_readoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, finish_first, stopping = threading.Event(), threading.Event(), threading.Event()
    delivered: list[str] = []
    errors: list[BaseException] = []

    def notify(event: FiredEvent) -> int:
        identity = event.subscriber.subscriber_id
        delivered.append(identity)
        if identity == "first":
            entered.set()
            assert finish_first.wait(5)
        return 0

    owner = WatchWriter(
        root=tmp_path, authorize=lambda _: None, fetch=lambda *_: PRSnapshot(), notify=notify,
    )
    lost = False
    native_identity, stop = owner._lease.held_file_identity, owner.daemon._stop_workers

    def custody() -> tuple[int, int]:
        if lost:
            raise PermissionError("controlled lost custody")
        return native_identity()

    def observed_stop(*args: Any, **kwargs: Any) -> None:
        stopping.set()
        stop(*args, **kwargs)

    def prepare() -> None:
        try:
            owner.prepare_state_transition(timeout=2)
        except BaseException as error:
            errors.append(error)

    operation = threading.Thread(target=prepare)
    try:
        owner.adopt(timeout=1)
        owner.activate(timeout=1)
        deliveries = owner.daemon._deliveries
        deliveries.max_workers = 1
        key = WatchKey("example/project", 1)
        for identity in ("first", "second"):
            owner.daemon._registry.register(
                key, identity, until=(MERGED,), notify={"argv": ["fixture-callback"]},
            )
        events = owner.daemon._registry.apply_snapshot(
            key, PRSnapshot(merged=True, pr_state="closed"),
        )
        owner.daemon._persist()
        deliveries.legacy(events[0])
        assert entered.wait(1)
        deliveries.legacy(events[1])
        with monkeypatch.context() as race:
            race.setattr(owner._lease, "held_file_identity", custody)
            race.setattr(owner.daemon, "_stop_workers", observed_stop)
            lost = True
            operation.start()
            assert stopping.wait(1)
            finish_first.set()
            operation.join(3)
            assert not operation.is_alive()
            assert len(errors) == 1 and isinstance(errors[0], PermissionError)
            assert delivered == ["first"]
            assert [event.subscriber.subscriber_id for event in deliveries.legacy_queue] == ["second"]
            assert owner.writer_held and not owner.read_ready
        lost = False
        owner.adopt(timeout=1)
        assert owner.daemon._deliveries.max_workers == 1
        assert owner._preparation_guard is None
        with monkeypatch.context() as race:
            race.setattr(owner._lease, "held_file_identity", custody)
            lost = True
            with pytest.raises(PermissionError, match="lost custody"):
                owner.activate(timeout=1)
            assert delivered == ["first"] and not owner.read_ready
            assert owner.daemon.has_legacy_pending()
        lost = False
        owner.adopt(timeout=1)
        owner.activate(timeout=1)
    finally:
        lost = False
        finish_first.set()
        if operation.ident is not None:
            operation.join(3)
        if owner.daemon._deliveries.legacy_queue:
            owner.daemon.quiesce(timeout=3, persist=False, preserve_queued=True)
            owner.adopt(timeout=3)
            owner.activate(timeout=3)
        owner.close(timeout=3)
    assert delivered == ["first", "second"]


def test_preparation_records_a_real_pinned_installation(tmp_path: Path) -> None:
    installation = _installation(tmp_path)
    owner = WatchWriter(
        root=installation.state_root, authorize=lambda _: None, fetch=lambda *_: PRSnapshot(),
    )
    try:
        owner.adopt(timeout=1)
        owner.activate(timeout=1)
        record = owner.prepare_state_transition(timeout=3, installation=installation)
        assert record["binding"] == {
            "kind": "installation", "stateRoot": str(installation.state_root),
            "installation": installation.evidence(),
        }
    finally:
        owner.close(timeout=3)


def test_preparation_refuses_another_installation_root_before_creation(tmp_path: Path) -> None:
    installation = _installation(tmp_path)
    root = tmp_path / "foreign-state"
    owner = WatchWriter(root=root, authorize=lambda _: None, fetch=lambda *_: PRSnapshot())
    try:
        with pytest.raises(PermissionError, match="state root differs"):
            owner.prepare_state_transition(timeout=3, installation=installation)
        assert not root.exists() and not owner.writer_held
    finally:
        owner.close(timeout=3)


@pytest.mark.parametrize("boundary", ["drain", "marker"])
def test_installation_loss_refuses_the_affected_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    from agent_pull_requests import watch_transition

    installation = _installation(tmp_path)
    owner = WatchWriter(
        root=installation.state_root, authorize=lambda _: None, fetch=lambda *_: PRSnapshot(),
    )
    paths = watch_state_paths(installation.state_root)
    captured: list[bytes] = []

    def revoke() -> None:
        captured.append(paths.legacy_subscriptions.read_bytes())
        _policy(installation.os_profile, False)

    stop, publish = owner.daemon._stop_workers, watch_transition.write_private

    def lost_after_workers(*args: Any, **kwargs: Any) -> None:
        stop(*args, **kwargs)
        revoke()

    def lost_before_marker(*args: Any, **kwargs: Any) -> None:
        revoke()
        publish(*args, **kwargs)

    try:
        owner.adopt(timeout=1)
        owner.activate(timeout=1)
        paths.legacy_subscriptions.write_bytes(b" [ ] \n")
        with monkeypatch.context() as changed:
            if boundary == "drain":
                changed.setattr(owner.daemon, "_stop_workers", lost_after_workers)
            else:
                changed.setattr(watch_transition, "write_private", lost_before_marker)
            with pytest.raises(PermissionError):
                owner.prepare_state_transition(timeout=3, installation=installation)
        assert captured
        assert paths.legacy_subscriptions.read_bytes() == captured[0]
        assert not paths.journal.exists()
    finally:
        _policy(installation.os_profile)
        owner.close(timeout=3)


def _retain_unattempted_callback(owner: WatchWriter) -> None:
    owner.adopt(timeout=1)
    key = WatchKey("example/project", 1)
    owner.daemon._registry.register(
        key, "retained", until=(MERGED,), notify={"argv": ["fixture-callback"]},
    )
    event, = owner.daemon._registry.apply_snapshot(
        key, PRSnapshot(merged=True, pr_state="closed"),
    )
    owner.daemon._persist()
    owner._reject_preparation()
    owner.daemon._deliveries.legacy(event)
    owner.adopt(timeout=1)


@pytest.mark.parametrize("operation", ["close", "withdraw"])
def test_readoption_keeps_retained_callbacks_fenced_until_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    delivered: list[str] = []
    completed, idle = threading.Event(), threading.Event()

    def notify(event: FiredEvent) -> int:
        delivered.append(event.subscriber.subscriber_id)
        completed.set()
        return 0

    owner = WatchWriter(
        root=tmp_path, authorize=lambda _: None, fetch=lambda *_: PRSnapshot(),
        notify=notify,
    )
    try:
        _retain_unattempted_callback(owner)
        with pytest.raises(RuntimeError, match="legacy work"):
            getattr(owner, operation)(timeout=1)
        assert delivered == [] and owner.writer_held and owner.daemon.has_legacy_pending()
        deliveries = owner.daemon._deliveries
        wait, resume = deliveries.changed.wait, deliveries.resume

        def wait_fenced(timeout: float | None = None) -> bool:
            idle.set()
            return wait(timeout)

        def start_fenced() -> None:
            resume()
            assert idle.wait(1)

        monkeypatch.setattr(deliveries.changed, "wait", wait_fenced)
        monkeypatch.setattr(deliveries, "resume", start_fenced)
        owner.activate(timeout=1)
        assert completed.wait(1)
    finally:
        if not owner._closed:
            if not owner.daemon.effects_enabled:
                owner.activate(timeout=1)
            owner.daemon.quiesce(timeout=3)
            owner.close(timeout=3)
    assert delivered == ["retained"]


def test_preparation_cannot_strand_a_retained_callback_queue(tmp_path: Path) -> None:
    delivered: list[str] = []

    def notify(event: FiredEvent) -> int:
        delivered.append(event.subscriber.subscriber_id)
        return 0

    owner = WatchWriter(
        root=tmp_path, authorize=lambda _: None, fetch=lambda *_: PRSnapshot(),
        notify=notify,
    )
    paths = watch_state_paths(tmp_path)
    try:
        _retain_unattempted_callback(owner)
        state = paths.legacy_subscriptions.read_bytes()
        with pytest.raises(RuntimeError, match="queued legacy"):
            owner.prepare_state_transition(timeout=1)
        assert not paths.journal.exists() and not paths.preparation_pin.exists()
        assert paths.legacy_subscriptions.read_bytes() == state and delivered == []
        owner.adopt(timeout=1)
        owner.activate(timeout=1)
        owner.daemon.quiesce(timeout=3)
        owner.prepare_state_transition(timeout=1)
        assert paths.journal.exists() and delivered == ["retained"]
    finally:
        # Also settle the intentionally failing baseline's fixture-owned marker.
        paths.journal.unlink(missing_ok=True)
        paths.preparation_pin.unlink(missing_ok=True)
        owner.adopt(timeout=1)
        owner.activate(timeout=1)
        owner.daemon.quiesce(timeout=3)
        owner.close(timeout=3)


@pytest.mark.parametrize("operation", ["resume", "activate"])
def test_startup_worker_refusal_cannot_report_success_or_clear_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    installation = _installation(tmp_path)
    owner = WatchWriter(
        root=installation.state_root, authorize=lambda _: None, fetch=lambda *_: PRSnapshot(),
    )
    worker_errors: list[BaseException] = []
    custody_lost = False
    identity = owner._lease.held_file_identity

    def custody() -> tuple[int, int]:
        if custody_lost:
            raise PermissionError("controlled startup custody loss")
        return identity()

    def refuse() -> None:
        nonlocal custody_lost
        if operation == "resume":
            _policy(installation.os_profile, False)
        else:
            custody_lost = True
        try:
            owner.daemon._before_notify()
        except BaseException as error:
            worker_errors.append(error)

    def start(_key: WatchKey) -> None:
        worker = threading.Thread(target=refuse)
        worker.start()
        worker.join(2)
        assert not worker.is_alive()

    def unfinished(*_args: Any, **_kwargs: Any) -> None:
        raise TimeoutError("controlled unfinished drain")

    try:
        owner.adopt(timeout=1)
        owner.activate(timeout=1)
        with monkeypatch.context() as blocked:
            blocked.setattr(owner.daemon, "_stop_workers", unfinished)
            with pytest.raises(TimeoutError, match="unfinished drain"):
                owner.prepare_state_transition(timeout=1, installation=installation)
        owner.daemon.settle_workers(timeout=1, preserve_queued=True)
        if operation == "activate":
            owner.adopt(timeout=1)
        guard = owner._preparation_guard
        owner.daemon._registry.register(
            WatchKey("example/project", 1), "startup", until=(MERGED,),
            notify={"argv": ["fixture-callback"]},
        )
        with monkeypatch.context() as startup:
            startup.setattr(owner._lease, "held_file_identity", custody)
            startup.setattr(owner.daemon, "_ensure_poller", start)
            with pytest.raises((PermissionError, RuntimeError)):
                getattr(owner, operation)(timeout=3)
        assert len(worker_errors) == 1
        assert not owner.read_ready and not owner.daemon.effects_enabled
        assert owner._preparation_guard is guard
        if operation == "resume":
            assert guard is not None
            with pytest.raises(PermissionError):
                owner.adopt(timeout=1)
    finally:
        custody_lost = False
        _policy(installation.os_profile)
        owner.daemon.settle_workers(timeout=3, preserve_queued=True)
        owner.close(timeout=3)
