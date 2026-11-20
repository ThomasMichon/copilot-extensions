"""Atomic, registration-scoped SSE samples retain actual replay/ACK ownership."""

from __future__ import annotations

import json
import importlib
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_pull_requests.repository_stream import RepositoryStream, chunks
from agent_pull_requests.repository_subscriptions import RepositoryHistoryGap, RepositoryRequestConflict

fixtures = importlib.import_module("test_repository_reviewer")
observe, request, setup = fixtures.observe, fixtures.request, fixtures.setup


def test_stream_resume_current_views_duplicate_delivery_expiry_and_scope(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected)
    subscription = request(selected, "subscribe", request_id="sse", ttl=60, after=0)["subscription"]
    payload = {"registration_id": subscription["registration_id"], "reviewer": "reviewer", "limit": 16}
    stream = RepositoryStream(selected[-1], selected[2].key, "consumer", payload, None)
    first = stream.sample(expires_at=10000)
    assert [name for name, _ in first["frames"]] == ["status", "pulls", "next-actions"]
    status = first["frames"][0][1]["view"]
    assert status["events"] and status["acknowledged"] == 0 and status["delivered"] > 0
    before = selected[1].path.read_bytes()
    duplicate = RepositoryStream(selected[-1], selected[2].key, "consumer", payload, None)
    assert duplicate.sample(expires_at=10000)["cursor"] == first["cursor"]
    assert selected[1].path.read_bytes() == before
    resumed = RepositoryStream(selected[-1], selected[2].key, "consumer", payload, first["cursor"])
    second = resumed.sample(expires_at=10000)
    assert not second["frames"][0][1]["view"]["events"]
    for changed in ({"reviewer": "other"}, {"limit": 2}):
        with pytest.raises(RepositoryRequestConflict):
            RepositoryStream(selected[-1], selected[2].key, "consumer", payload | changed, first["cursor"])
    with pytest.raises(KeyError):
        RepositoryStream(selected[-1], selected[2].key, "other", payload, None).sample(expires_at=10000)
    selected[0][0] += 61
    observe(selected, head_sha="d" * 40)
    ended = resumed.sample(expires_at=10000)
    assert ended["status"] == "expired" and ended["after"] == first["after"]
    assert not ended["frames"][0][1]["view"]["events"]
    retained = json.loads(selected[1].path.read_bytes())["repository_cache"]["registrations"][0]
    assert retained["acknowledged"] == 0 and retained["expires_at"] == subscription["expires_at"]


def test_stream_stale_or_future_resume_never_rebinds_or_advances_ack(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected)
    row = request(selected, "subscribe", request_id="resume", ttl=60, after=0)["subscription"]
    payload = {"registration_id": row["registration_id"], "reviewer": "reviewer"}
    stream = RepositoryStream(selected[-1], selected[2].key, "consumer", payload, None)
    first = stream.sample(expires_at=10000)
    for sequence in (999999,):
        invalid = f"v1.{stream.scope}.{sequence}"
        with pytest.raises(RepositoryHistoryGap):
            RepositoryStream(selected[-1], selected[2].key, "consumer", payload, invalid).sample(expires_at=10000)
    last = first["frames"][0][1]["view"]["events"][-1]
    request(selected, "acknowledge", registration_id=row["registration_id"], event_id=last["event_id"])
    before = selected[1].path.read_bytes()
    with pytest.raises(RepositoryHistoryGap):
        RepositoryStream(selected[-1], selected[2].key, "consumer", payload,
                         f"v1.{stream.scope}.0").sample(expires_at=10000)
    assert selected[1].path.read_bytes() == before


def test_stream_reprojects_freshness_without_a_new_durable_event(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected)
    row = request(selected, "subscribe", request_id="freshness", ttl=3600)["subscription"]
    stream = RepositoryStream(selected[-1], selected[2].key, "consumer", {
        "registration_id": row["registration_id"], "reviewer": "reviewer", "max_age_seconds": 2,
    }, None)
    first = stream.sample(expires_at=10000)
    assert first["frames"][2][1]["view"]["next_actions"]
    before = selected[1].path.read_bytes()
    selected[0][0] += 3
    second = stream.sample(expires_at=10000)
    assert second["cursor"] == first["cursor"]
    assert not second["frames"][2][1]["view"]["next_actions"]
    assert second["frames"][2][1]["view"]["unavailable"]
    assert selected[1].path.read_bytes() == before


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("history_state", ["APPROVED", "DISMISSED"])
def test_mixed_history_stream_preserves_evidence_and_existing_delivery_owner(
    tmp_path: Path, reverse: bool, history_state: str,
) -> None:
    selected = setup(tmp_path, provider="github" if history_state == "DISMISSED" else "gitea")
    reviews = [
        fixtures.verdict(dismissed=True, state=history_state), fixtures.verdict(id=10),
    ]
    if reverse:
        reviews.reverse()
    observe(selected, head_sha=fixtures.OLD, reviews=reviews)
    row = request(selected, "subscribe", request_id="mixed", ttl=3600, after=0)["subscription"]
    payload = {"registration_id": row["registration_id"], "reviewer": "reviewer"}
    first = RepositoryStream(selected[-1], selected[2].key, "consumer", payload, None).sample(
        expires_at=10000,
    )
    prior = first["frames"][2][1]["view"]
    assert prior["unavailable"] == [{"pr": 7, "reason": "reviewed-base-unknown"}]
    selected[0][0] = 1100
    observe(selected, reviews=reviews)
    before = json.loads(selected[1].path.read_bytes())
    resumed = RepositoryStream(selected[-1], selected[2].key, "consumer", payload, first["cursor"])
    current = resumed.sample(expires_at=10000)
    view = current["frames"][2][1]["view"]
    assert view["next_actions"][0]["action"] == "new_verdict"
    assert view["next_actions"][0]["review"]["id"] == 10 and not view["unavailable"]
    after = json.loads(selected[1].path.read_bytes())
    original_registration = before["repository_cache"]["registrations"][0]
    registration = after["repository_cache"]["registrations"][0]
    assert original_registration["delivered"] < registration["delivered"]
    original_registration["delivered"] = registration["delivered"]
    assert before == after and registration["acknowledged"] == 0
    stable = selected[1].path.read_bytes()
    repeated = resumed.sample(expires_at=10000)
    assert repeated["cursor"] == current["cursor"]
    assert repeated["frames"][2][1]["view"] == view
    assert selected[1].path.read_bytes() == stable
    with pytest.raises(ValueError, match="page changed"):
        request(selected, cursor=prior["cursor"])


@pytest.mark.parametrize("interval", [1, 300])
def test_backlogged_events_preserve_the_full_sample_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interval: int,
) -> None:
    from agent_pull_requests import repository_stream

    selected = setup(tmp_path)
    observe(selected)
    row = request(selected, "subscribe", request_id="cadence", ttl=3600, after=0)["subscription"]
    stream = RepositoryStream(selected[-1], selected[2].key, "consumer", {
        "registration_id": row["registration_id"], "reviewer": "reviewer", "limit": 1,
        "interval": interval, "seconds": interval * 2 + 1,
    }, None)
    initial = stream.sample(expires_at=10000)
    assert initial["more"]
    clock = [0.0]
    samples: list[float] = []
    sleep = asyncio.sleep

    async def advance(seconds: float) -> None:
        clock[0] += seconds
        await sleep(0)

    def sample() -> dict[str, object]:
        samples.append(clock[0])
        return stream.sample(expires_at=10000)

    monkeypatch.setattr(repository_stream, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(asyncio, "sleep", advance)

    async def scenario() -> None:
        frames = [frame async for frame in chunks(stream, initial, sample, lambda: None)]
        assert b'"reason":"window_elapsed"' in frames[-1]

    asyncio.run(scenario())
    assert samples == pytest.approx([interval, interval * 2])
    assert request(selected, "inspect", registration_id=row["registration_id"])[
        "subscription"
    ]["acknowledged"] == 0


def test_cancelled_stream_joins_its_started_sample_before_releasing_owner(tmp_path: Path) -> None:
    selected = setup(tmp_path)
    observe(selected)
    row = request(selected, "subscribe", request_id="joined", ttl=60)["subscription"]
    stream = RepositoryStream(selected[-1], selected[2].key, "consumer", {
        "registration_id": row["registration_id"], "reviewer": "reviewer",
    }, None)
    initial = stream.sample(expires_at=10000)
    entered, release = threading.Event(), threading.Event()

    def blocked() -> dict[str, object]:
        entered.set()
        assert release.wait(5)
        return initial

    async def scenario() -> None:
        iterator = chunks(stream, initial, blocked, lambda: None)
        for _ in range(3):
            await anext(iterator)
        pending = asyncio.create_task(anext(iterator))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            pending.cancel()
            await asyncio.sleep(.05)
            assert not pending.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await iterator.aclose()

    asyncio.run(scenario())
