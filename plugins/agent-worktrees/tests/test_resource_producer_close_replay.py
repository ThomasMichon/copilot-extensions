"""Read-only close replay preserves frozen-owner and authority boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import activity, claim_history, claim_providers
from agent_worktrees import resource_producer as producer, tracking
from test_resource_producer import absence, bind, call, reserve
from test_resource_producer import owner as owner


def forbid_effect(*args: object, **kwargs: object) -> None:
    pytest.fail("read-only close replay attempted provider I/O or a write")


def freeze(path: Path, status: str) -> None:
    record = tracking.load_record(path)
    record.status = status
    tracking.save_record(record, path)


@pytest.mark.parametrize("status", sorted(producer.FROZEN_OWNER_STATUSES))
@pytest.mark.parametrize("bound", [False, True])
def test_closed_replay_on_frozen_owner_is_read_only(
    owner: tuple, monkeypatch: pytest.MonkeyPatch, status: str, bound: bool,
) -> None:
    path, _ = owner
    reservation = reserve(path)
    if bound:
        reservation = bind(path, reservation)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", absence)
    closed = call(path, "close", {"token": reservation["token"]})
    assert closed.get("ok"), closed
    freeze(path, status)
    before = path.read_bytes()
    monkeypatch.setattr(claim_providers, "resolve_claim_status", forbid_effect)
    monkeypatch.setattr(tracking, "save_record", forbid_effect)
    monkeypatch.setattr(activity, "log_event", forbid_effect)
    monkeypatch.setattr(claim_history, "record_event", forbid_effect)
    assert call(path, "close", {"token": reservation["token"]}) == closed
    assert call(path, "close", {"token": reservation["token"]}, ref="m/p/consumer")["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("status", sorted(producer.FROZEN_OWNER_STATUSES))
@pytest.mark.parametrize("phase", ["pending", "allocating", "bound"])
def test_frozen_owner_cannot_close_live_claim(
    owner: tuple, monkeypatch: pytest.MonkeyPatch, status: str, phase: str,
) -> None:
    path, _ = owner
    reservation = reserve(path)
    if phase == "allocating":
        assert call(path, "begin", {"token": reservation["token"]}).get("ok")
    elif phase == "bound":
        bind(path, reservation)
    freeze(path, status)
    before = path.read_bytes()
    monkeypatch.setattr(claim_providers, "resolve_claim_status", forbid_effect)
    result = call(path, "close", {"token": reservation["token"]})
    assert result["error"] == "rejected" and "frozen" in result["message"]
    assert path.read_bytes() == before
    assert tracking.load_record(path).resources[0].is_unsettled


def test_close_replays_concurrent_committed_close_and_finalization(
    owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = owner
    reservation = reserve(path)
    committed: dict = {}
    frozen_bytes: list[bytes] = []

    def concurrent_close(ref: str) -> dict:
        with monkeypatch.context() as context:
            context.setattr(claim_providers, "resolve_claim_status", absence)
            committed.update(call(path, "close", {"token": reservation["token"]}))
        assert committed.get("ok"), committed
        freeze(path, "finalized")
        frozen_bytes.append(path.read_bytes())
        monkeypatch.setattr(tracking, "save_record", forbid_effect)
        monkeypatch.setattr(activity, "log_event", forbid_effect)
        monkeypatch.setattr(claim_history, "record_event", forbid_effect)
        return absence(ref)

    monkeypatch.setattr(claim_providers, "resolve_claim_status", concurrent_close)
    assert call(path, "close", {"token": reservation["token"]}) == committed
    assert path.read_bytes() == frozen_bytes[0]


def test_owner_freezing_during_probe_refuses_live_close(
    owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = owner
    reservation = reserve(path)
    frozen_bytes: list[bytes] = []

    def freeze_during_probe(ref: str) -> dict:
        freeze(path, "finalizing")
        frozen_bytes.append(path.read_bytes())
        return absence(ref)

    monkeypatch.setattr(claim_providers, "resolve_claim_status", freeze_during_probe)
    result = call(path, "close", {"token": reservation["token"]})
    assert result["error"] == "rejected" and "frozen" in result["message"]
    assert path.read_bytes() == frozen_bytes[0]
    assert tracking.load_record(path).resources[0].is_unsettled
