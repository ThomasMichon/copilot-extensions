"""Outgoing-only retirement from exact current-fence native readiness evidence."""
import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from pathlib import Path
from collections.abc import Callable
from typing import Any, TYPE_CHECKING, cast

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
    queued: list[Callable[[], None]] = []
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


def test_ready_successor_eventually_stops_only_outgoing_and_preserves_successor(
    owners: SimpleNamespace,
) -> None:
    before = owners.store.snapshot()
    registration = owners.candidate_path.read_bytes()
    owners.outgoing._observe_supersession()
    assert not owners.outgoing.stop.is_set()
    assert len(owners.queued) == 1
    owners.queued.pop()()
    assert owners.outgoing.stop.is_set()
    assert owners.incoming.writer.current()
    owners.outgoing.close()
    assert owners.candidate_path.read_bytes() == registration
    assert owners.store.snapshot() == before


@pytest.mark.parametrize("owners", ["1.0"], indirect=True)
def test_ready_new_native_owner_in_same_selected_slot_retires_old_process(
    owners: SimpleNamespace,
) -> None:
    owners.outgoing._observe_supersession()
    owners.queued.pop()()
    assert owners.outgoing.stop.is_set()
    assert owners.incoming.writer.current()


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
def test_queued_stop_rechecks_on_existing_event_loop(owners: SimpleNamespace, race: str) -> None:
    owners.outgoing._observe_supersession()
    assert len(owners.queued) == 1
    if race == "own-fence-restored":
        owners.outgoing.writer.activate()
    elif race == "different-fence":
        from agent_ssh_service.writer import FencedWriter
        FencedWriter(owners.incoming.config, "different-owner").activate()
    else:
        owners.selected[0] = "1.0"
    owners.queued.pop()()
    assert not owners.outgoing.stop.is_set()


def test_readiness_unavailable_then_ready_needs_no_incoming_reply_ack(
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
    owners.queued.pop()()
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
