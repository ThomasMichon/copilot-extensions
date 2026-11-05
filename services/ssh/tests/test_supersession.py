"""Outgoing-only retirement from exact current-fence native readiness evidence."""
import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from pathlib import Path
from collections.abc import Callable
from typing import Any, TYPE_CHECKING, cast
from contextlib import closing, contextmanager
import sqlite3
import threading
import time

import pytest

from agent_ssh_service import local_control, source_start
from agent_ssh_service.config import Document, ServiceConfig
from fleet_manager.private_state import private_directory
from zdd.private_protocol import Deadline, PeerIdentity
if TYPE_CHECKING:
    from services.ssh.tests.conftest import enrollment
else:
    from conftest import enrollment


@pytest.fixture
def owners(config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
           request: pytest.FixtureRequest) -> SimpleNamespace:
    root = tmp_path / "runtime"
    root.mkdir()
    version = getattr(request, "param", "2.0")
    selected: list[str | None] = [version]
    monkeypatch.setattr(source_start, "selection_reader",
                        lambda _: lambda: selected[0], raising=False)
    peers = iter([PeerIdentity(100, "1000", "fixture"), PeerIdentity(200, "2000", "fixture")])
    monkeypatch.setattr(local_control, "current_peer", lambda: next(peers))
    monkeypatch.setattr(local_control, "lock_data_is_live", lambda _: True)

    def binding(version: str) -> SimpleNamespace:
        slot = root / "versions" / version
        slot.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(
            root=root, slot=slot,
            identity=SimpleNamespace(document=lambda: {"fixture": version}),
        )

    outgoing = local_control.LocalControl(binding("1.0"), config, selected=lambda _: None)
    first_store = outgoing.writer.activate()
    first_store.enroll("principal-worker-a", enrollment())
    incoming = local_control.LocalControl(binding(version), config, selected=lambda _: None)
    store = incoming.writer.activate()
    queued: list[Callable[[], Any]] = []
    health_calls: list[Document] = []
    outgoing.loop = cast(Any, SimpleNamespace(
        call_soon_threadsafe=lambda callback: queued.append(callback)))
    outgoing.stop = asyncio.Event()
    outgoing.initialized = asyncio.Event()
    outgoing.initialized.set()
    outgoing.read_selection = lambda: selected[0]
    cell = local_control.cell_directory(config)
    private_directory(cell)

    def record(control: local_control.LocalControl) -> Document:
        return {
            "peer": asdict(control.peer), "root": str(root), "slot": control.binding.slot.name,
            "source": control.binding.identity.document(), "config_identity": control.identity,
        }

    candidate = record(incoming)
    incoming_record = cell / "owner-incoming.json"
    local_control.publish_new(incoming_record, candidate)
    outgoing.record = cell / "owner-outgoing.json"
    outgoing.record_data = record(outgoing)
    local_control.publish_new(outgoing.record, outgoing.record_data)

    def health(_config: ServiceConfig, actual: Document, operation: str, **_: Any) -> Document:
        assert actual == candidate and operation == "health"
        health_calls.append(actual)
        return {
            "pid": incoming.peer.pid, "process_start_time": incoming.peer.start_time,
            "version": incoming.binding.slot.name, "config_identity": incoming.identity,
            "ready": True,
        }

    monkeypatch.setattr(local_control, "request", health)
    return SimpleNamespace(
        outgoing=outgoing, incoming=incoming, store=store, selected=selected, queued=queued,
        calls=health_calls, candidate=candidate, candidate_path=incoming_record, root=root,
        health=health,
    )


async def apply_queued(owners: SimpleNamespace) -> None:
    task = owners.queued.pop()()
    if task is not None:
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["health", "preparation", "queued-retirement"])
async def test_control_sqlite_contention_keeps_loop_responsive_and_drains(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, consumer: str,
) -> None:
    callback: Callable[[], Any] | None = None
    if consumer == "queued-retirement":
        owners.outgoing._observe_supersession()
        callback = owners.queued.pop()
        control = owners.outgoing
    else:
        control = owners.incoming
        control.initialized = asyncio.Event()
        control.initialized.set()
        connected = asyncio.Event()
        connected.set()
        control.service = cast(Any, SimpleNamespace(closed=False))
        control.connector = cast(Any, SimpleNamespace(connected=connected))
        control.store = owners.store
    started, finished = threading.Event(), threading.Event()
    observed = cast(Callable[[], str | None], control.writer.observed_owner)
    timing: list[float] = []
    thread_ids: list[int] = []
    loop_thread = threading.get_ident()
    def observe() -> str | None:
        thread_ids.append(threading.get_ident())
        timing.append(time.monotonic())
        started.set()
        try:
            return observed()
        finally:
            timing.append(time.monotonic())
            finished.set()
    monkeypatch.setattr(control.writer, "observed_owner", observe)
    async def operation() -> Any:
        if consumer == "queued-retirement":
            assert callback is not None
            result = callback()
            if isinstance(result, asyncio.Future):
                return await result
            return result
        return await control._operate("health" if consumer == "health" else "prepare")
    async def progress() -> None:
        deadline = time.monotonic() + 4
        while not started.is_set():
            assert time.monotonic() < deadline, "actual control probe did not start"
            await asyncio.sleep(0.005)
        assert not finished.is_set(), "control SQLite probe blocked another coroutine"
    with closing(sqlite3.connect(control.writer.root / "directory.sqlite3")) as blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        monitor = asyncio.create_task(progress())
        task = asyncio.create_task(operation())
        outcome: object
        try:
            await asyncio.wait_for(monitor, 4)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not finished.is_set(), "caller must retain its pending real SQL worker"
            task.cancel()
        finally:
            task.cancel()
            outcome, = await asyncio.gather(task, return_exceptions=True)
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            blocker.rollback()
    assert isinstance(outcome, asyncio.CancelledError)
    assert finished.is_set() and len(thread_ids) == 1
    assert thread_ids[0] != loop_thread and timing[-1] - timing[0] >= 0.9
    assert not control.stop.is_set() and not owners.incoming.stop.is_set()
    await control.close()
    assert not control.operations


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["health", "prepare"])
@pytest.mark.parametrize("changed", ["stopped", "closed", "service-closed", "foreign-fence"])
async def test_control_probe_rechecks_authority_after_await(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, operation: str, changed: str,
) -> None:
    control = owners.incoming
    control.initialized = asyncio.Event()
    control.initialized.set()
    connected = asyncio.Event()
    connected.set()
    control.service = cast(Any, SimpleNamespace(closed=False))
    control.connector = cast(Any, SimpleNamespace(connected=connected))
    control.store = owners.store
    started = threading.Event()
    observed = cast(Callable[[], str | None], control.writer.observed_owner)
    def observe() -> str | None:
        started.set()
        return observed()
    monkeypatch.setattr(control.writer, "observed_owner", observe)
    with closing(sqlite3.connect(control.writer.root / "directory.sqlite3")) as blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        task = asyncio.create_task(control._operate(operation))
        closing_task: asyncio.Task[None] | None = None
        try:
            deadline = time.monotonic() + 4
            while not started.is_set():
                assert time.monotonic() < deadline, "actual control probe did not start"
                await asyncio.sleep(0.005)
            if changed == "stopped":
                control.stop.set()
            elif changed == "closed":
                closing_task = asyncio.create_task(control.close())
                await asyncio.sleep(0)
                assert control.closed.is_set()
            elif changed == "service-closed":
                control.service.closed = True
            else:
                blocker.execute("UPDATE service_writer SET owner='foreign-control-owner'")
            blocker.commit()
            result: object
            result, = await asyncio.gather(task, return_exceptions=True)
            if changed == "closed":
                assert isinstance(result, asyncio.CancelledError)
            else:
                assert isinstance(result, dict)
                assert not result["ready" if operation == "health" else "prepared"]
        finally:
            blocker.rollback()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if closing_task is not None:
                await closing_task
    if changed != "closed":
        await control.close()
    assert not control.operations


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    "stopped", "closed", "selection", "selection-after-observation", "foreign-fence", "cancelled",
])
async def test_actual_queued_retirement_has_no_effect_after_guard_changes(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    control = owners.outgoing
    control._observe_supersession()
    callback = owners.queued.pop()
    started = threading.Event()
    observed = cast(Callable[[], str | None], control.writer.observed_owner)
    def observe() -> str | None:
        started.set()
        return observed()
    monkeypatch.setattr(control.writer, "observed_owner", observe)
    original_set = control.stop.set
    effects: list[bool] = []
    def stop() -> None:
        effects.append(True)
        original_set()
    monkeypatch.setattr(control.stop, "set", stop)
    if changed == "selection-after-observation":
        loop = asyncio.get_running_loop()
        main_thread = threading.get_ident()
        reader = cast(Callable[[], str | None], control.read_selection)
        def selection() -> str | None:
            value = reader()
            if threading.get_ident() != main_thread:
                loop.call_soon_threadsafe(owners.selected.__setitem__, 0, "1.0")
            return value
        control.read_selection = selection
    with closing(sqlite3.connect(control.writer.root / "directory.sqlite3")) as blocker:
        blocker.execute("BEGIN EXCLUSIVE")
        task = callback()
        assert isinstance(task, asyncio.Task)
        closing_task: asyncio.Task[None] | None = None
        try:
            deadline = time.monotonic() + 4
            while not started.is_set():
                assert time.monotonic() < deadline, "actual retirement probe did not start"
                await asyncio.sleep(0.005)
            if changed == "stopped":
                original_set()
            elif changed == "closed":
                closing_task = asyncio.create_task(control.close())
                await asyncio.sleep(0)
                assert control.closed.is_set()
            elif changed == "selection":
                owners.selected[0] = "1.0"
            elif changed == "foreign-fence":
                blocker.execute("UPDATE service_writer SET owner='foreign-control-owner'")
            elif changed == "cancelled":
                task.cancel()
                await asyncio.sleep(0)
            blocker.commit()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            blocker.rollback()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if closing_task is not None:
                await closing_task
    assert not effects
    assert not owners.incoming.stop.is_set()
    if changed != "closed":
        await control.close()
    assert not control.operations


@pytest.mark.asyncio
async def test_retirement_keeps_original_writer_guard_until_loop_effect(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from single_instance_lease import AlreadyRunningError, SingleInstance

    control = owners.outgoing
    original_set = control.stop.set
    def stop() -> None:
        lease = SingleInstance(str(control.writer.root), service="agent-ssh-service")
        try:
            with pytest.raises(AlreadyRunningError):
                lease.acquire()
        finally:
            lease.release()
        original_set()
    monkeypatch.setattr(control.stop, "set", stop)
    control._observe_supersession()
    await apply_queued(owners)
    assert control.stop.is_set() and owners.incoming.writer.current()


@pytest.mark.asyncio
async def test_retirement_abandoned_callback_cannot_apply_after_guard_release(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh_service.writer import FencedWriter

    control = owners.outgoing
    control._observe_supersession()
    callback = owners.queued.pop()
    loop = asyncio.get_running_loop()
    enqueue = loop.call_soon_threadsafe
    held: list[Callable[[], None]] = []
    delivered = asyncio.Event()
    def delay_apply(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if callback.__name__ == "apply":
            held.append(callback)
            return enqueue(lambda: None)
        return enqueue(callback, *args, **kwargs)
    monkeypatch.setattr(loop, "call_soon_threadsafe", delay_apply)
    guard = control.writer.guard
    @contextmanager
    def release_before_delivery() -> Any:
        try:
            with guard():
                yield
        finally:
            FencedWriter(control.config, "foreign-before-delayed-retirement").activate()
            assert len(held) == 1
            def deliver() -> None:
                held.pop()()
                delivered.set()
            enqueue(deliver)
    monkeypatch.setattr(control.writer, "guard", release_before_delivery)
    control.writer.timeout = 0.05
    await callback()
    assert delivered.is_set() and not held
    assert not control.stop.is_set()
    assert not owners.incoming.stop.is_set()
    assert not control.operations


@pytest.mark.asyncio
async def test_retirement_pending_cancel_precedes_its_drain_handler(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = owners.outgoing
    control._observe_supersession()
    callback = owners.queued.pop()
    loop = asyncio.get_running_loop()
    enqueue = loop.call_soon_threadsafe
    delivered = asyncio.Event()
    task: asyncio.Task[None]
    def cancel_before_apply(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if callback.__name__ == "apply":
            def deliver() -> None:
                task.cancel()
                callback()
                delivered.set()
            return enqueue(deliver)
        return enqueue(callback, *args, **kwargs)
    monkeypatch.setattr(loop, "call_soon_threadsafe", cancel_before_apply)
    task = callback()
    result, = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result, asyncio.CancelledError) and delivered.is_set()
    assert not control.stop.is_set()
    assert not owners.incoming.stop.is_set()
    assert not control.operations


@pytest.mark.asyncio
async def test_health_final_fence_read_follows_actual_snapshot(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_ssh_service.writer import FencedWriter

    control = owners.incoming
    connected = asyncio.Event()
    connected.set()
    control.service = cast(Any, SimpleNamespace(closed=False))
    control.connector = cast(Any, SimpleNamespace(connected=connected))
    control.store = owners.store
    snapshot = owners.store.snapshot
    def changed_snapshot() -> Document:
        value: Document = snapshot()
        FencedWriter(control.config, "foreign-after-snapshot").activate()
        return value
    monkeypatch.setattr(owners.store, "snapshot", changed_snapshot)
    result = await control._attest()
    assert not result["ready"]


@pytest.mark.asyncio
async def test_ready_successor_eventually_stops_only_outgoing_and_preserves_successor(
    owners: SimpleNamespace,
) -> None:
    before = owners.store.snapshot()
    registration = owners.candidate_path.read_bytes()
    owners.outgoing._observe_supersession()
    assert not owners.outgoing.stop.is_set()
    assert len(owners.queued) == 1
    await apply_queued(owners)
    assert owners.outgoing.stop.is_set()
    assert owners.incoming.writer.current()
    await owners.outgoing.close()
    assert owners.candidate_path.read_bytes() == registration
    assert owners.store.snapshot() == before


@pytest.mark.parametrize("owners", ["1.0"], indirect=True)
@pytest.mark.asyncio
async def test_ready_new_native_owner_in_same_selected_slot_retires_old_process(
    owners: SimpleNamespace,
) -> None:
    owners.outgoing._observe_supersession()
    await apply_queued(owners)
    assert owners.outgoing.stop.is_set()
    assert owners.incoming.writer.current()


@pytest.mark.parametrize("root_state", ["missing", "inaccessible"])
@pytest.mark.asyncio
async def test_uninspectable_selected_record_does_not_hide_ready_successor(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, root_state: str,
) -> None:
    retained_root = owners.root.parent / "retained-runtime"
    if root_state == "inaccessible":
        retained_root.mkdir()
        original = Path.samefile
        def samefile(path: Path, other: Any) -> bool:
            if path == retained_root:
                raise PermissionError("fixture-retained-root-inaccessible")
            return original(path, other)
        monkeypatch.setattr(Path, "samefile", samefile)
    retained = {
        **owners.candidate, "root": str(retained_root),
        "peer": {**owners.candidate["peer"], "pid": 300, "start_time": "3000"},
    }
    path = local_control.cell_directory(owners.outgoing.config) / "owner-aaa-retained.json"
    local_control.publish_new(path, retained)
    original_bytes = path.read_bytes()
    successor_bytes = owners.candidate_path.read_bytes()
    owners.outgoing._observe_supersession()
    assert owners.calls == [owners.candidate]
    assert len(owners.queued) == 1
    await apply_queued(owners)
    assert owners.outgoing.stop.is_set() and owners.incoming.writer.current()
    assert path.read_bytes() == original_bytes
    assert owners.candidate_path.read_bytes() == successor_bytes


@pytest.mark.parametrize("evidence", ["not-ready", "unavailable", "wrong-native", "wrong-root",
                                    "wrong-birth", "wrong-config", "wrong-principal",
                                    "selection-unavailable", "DB-unavailable"])
def test_unqualified_successor_evidence_cannot_stop_outgoing(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, evidence: str,
) -> None:
    if evidence == "selection-unavailable":
        owners.selected[0] = None
    elif evidence == "DB-unavailable":
        monkeypatch.setattr(owners.outgoing.writer, "observed_owner", lambda: None, raising=False)
    elif evidence in ("wrong-root", "wrong-principal"):
        changed = dict(owners.candidate)
        if evidence == "wrong-root":
            foreign = owners.root.parent / "foreign"
            foreign.mkdir()
            changed["root"] = str(foreign)
        else:
            changed["peer"] = {**changed["peer"], "principal": "foreign"}
        monkeypatch.setattr(local_control, "records", lambda _: [changed])
    else:
        def health(*args: Any, **kwargs: Any) -> Document:
            if evidence == "unavailable":
                raise TimeoutError("fixture-native-health-unavailable")
            result: Document = owners.health(*args, **kwargs)
            result["ready"] = evidence != "not-ready"
            if evidence == "wrong-native":
                result["pid"] += 1
            elif evidence == "wrong-birth":
                result["process_start_time"] = "9999"
            elif evidence == "wrong-config":
                result["config_identity"] = "foreign-config"
            return result
        monkeypatch.setattr(local_control, "request", health)
    owners.outgoing._observe_supersession()
    assert not owners.outgoing.stop.is_set()
    assert owners.queued == []


def test_current_incoming_owner_never_observes_or_waits_for_predecessor(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = owners.incoming
    incoming.initialized = asyncio.Event()
    incoming.initialized.set()
    incoming.stop = asyncio.Event()
    incoming.loop = SimpleNamespace(call_soon_threadsafe=lambda _: pytest.fail("scheduled stop"))
    incoming.read_selection = lambda: pytest.fail("queried selected predecessor")
    monkeypatch.setattr(local_control, "request", lambda *a, **k: pytest.fail("contacted old owner"))
    incoming._observe_supersession()
    assert incoming.writer.current() and not incoming.stop.is_set()


@pytest.mark.parametrize("race", ["own-fence-restored", "different-fence", "own-reselection"])
def test_changed_fence_or_selection_during_health_cannot_queue_stale_stop(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, race: str,
) -> None:
    def health(*args: Any, **kwargs: Any) -> Document:
        result: Document = owners.health(*args, **kwargs)
        if race == "own-fence-restored":
            owners.outgoing.writer.activate()
        elif race == "different-fence":
            from agent_ssh_service.writer import FencedWriter
            FencedWriter(owners.incoming.config, "different-owner").activate()
        else:
            owners.selected[0] = "1.0"
        return result
    monkeypatch.setattr(local_control, "request", health)
    owners.outgoing._observe_supersession()
    assert owners.queued == []
    assert not owners.outgoing.stop.is_set()


@pytest.mark.parametrize("race", ["own-fence-restored", "different-fence", "own-reselection"])
@pytest.mark.asyncio
async def test_queued_stop_rechecks_on_existing_event_loop(owners: SimpleNamespace, race: str) -> None:
    owners.outgoing._observe_supersession()
    assert len(owners.queued) == 1
    if race == "own-fence-restored":
        owners.outgoing.writer.activate()
    elif race == "different-fence":
        from agent_ssh_service.writer import FencedWriter
        FencedWriter(owners.incoming.config, "different-owner").activate()
    else:
        owners.selected[0] = "1.0"
    await apply_queued(owners)
    assert not owners.outgoing.stop.is_set()


@pytest.mark.asyncio
async def test_readiness_unavailable_then_ready_needs_no_incoming_reply_ack(
    owners: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = [False]
    def health(*args: Any, **kwargs: Any) -> Document:
        result: Document = owners.health(*args, **kwargs)
        result["ready"] = ready[0]
        return result
    monkeypatch.setattr(local_control, "request", health)
    owners.outgoing._observe_supersession()
    assert owners.queued == [] and not owners.outgoing.stop.is_set()
    ready[0] = True
    owners.outgoing._observe_supersession()
    await apply_queued(owners)
    assert owners.outgoing.stop.is_set()


def test_own_request_sharing_cleanup_uses_existing_deadline_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sharing = PermissionError("fixture-reader-still-open")
    cast(Any, sharing).winerror = 32
    calls = []

    def unlink(**_: Any) -> None:
        calls.append("own-request")
        if len(calls) == 1:
            raise sharing

    waits: list[float] = []
    monkeypatch.setattr(local_control, "time", SimpleNamespace(sleep=waits.append), raising=False)
    local_control.remove_request(
        cast(Path, SimpleNamespace(unlink=unlink)),
        cast(Deadline, SimpleNamespace(remaining=lambda: 1)),
    )
    assert calls == ["own-request", "own-request"]
    assert len(waits) == 1


def test_request_cleanup_does_not_hide_unrelated_denial_or_expired_deadline() -> None:
    def denied(**_: Any) -> None:
        raise PermissionError("fixture-not-a-sharing-error")
    with pytest.raises(PermissionError, match="not-a-sharing-error"):
        local_control.remove_request(
            cast(Path, SimpleNamespace(unlink=denied)),
            cast(Deadline, SimpleNamespace(remaining=lambda: 1)),
        )

    sharing = PermissionError("fixture-sharing")
    cast(Any, sharing).winerror = 32
    def busy(**_: Any) -> None:
        raise sharing
    def expired() -> None:
        raise TimeoutError("fixture-existing-request-deadline")
    with pytest.raises(TimeoutError, match="existing-request-deadline"):
        local_control.remove_request(
            cast(Path, SimpleNamespace(unlink=busy)),
            cast(Deadline, SimpleNamespace(remaining=expired)),
        )
