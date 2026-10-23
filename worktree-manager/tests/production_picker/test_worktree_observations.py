"""V2 parser-only coverage; loader and UI integration remain separate."""

from __future__ import annotations

import copy

import pytest

from worktree_manager.worktree_feed_contract import CurrentFeed
from worktree_manager.worktree_rows_contract import CurrentSnapshot, parse_current_rows


def envelope(revision=1, *, title="Title", at=10, generation="a" * 32):
    return {
        "ok": True, "contract_version": 2, "project": "project",
        "projection": {
            "schema_version": 1, "quality": "current", "generation": generation,
            "revision": revision, "observed_at": at, "roster_count": 1, "selected_count": 1,
        },
        "worktrees": [{
            "id": "one", "title": title, "semantic_revision": 1,
            "fact_provenance": {"session": {"quality": "current"}},
        }],
        "observations": [{
            "id": "one", "facts": {"session": {
                "quality": "current", "observed_at": at, "observation_revision": revision,
            }},
        }],
    }


def frames(value, *, initial=True, semantic=True):
    head = value["projection"]
    result = [{
        "type": "begin" if initial else "update", "contract_version": 2,
        "project": "project",
        "projection": head, "cursor": head["revision"],
        "base_cursor": None if initial else head["revision"] - 1, "selected_count": 1,
    }]
    if semantic:
        result.append({"type": "worktree", "wt": value["worktrees"][0], "projection": head})
    result.append({
        "type": "observation", "observation": value["observations"][0], "projection": head,
    })
    if not initial:
        result.append({"type": "order", "ids": ["one"], "projection": head})
    result.append({
        "type": "done" if initial else "commit", "cursor": head["revision"], "projection": head,
    })
    return result


def receive(receiver, events):
    result = None
    for event in events:
        result = receiver.receive(event)
    return result


def test_snapshot_and_feed_observations_commit_atomically_without_semantic_frames():
    first = envelope()
    assert receive(CurrentSnapshot(), frames(first))[0]["_observations"]["session"][
        "observed_at"
    ] == 10
    receiver = CurrentFeed()
    receive(receiver, frames(first))
    before = copy.deepcopy(receiver.rows)
    events = frames(envelope(2, at=20), initial=False, semantic=False)
    for event in events[:-1]:
        assert receiver.receive(event) is None
        assert receiver.rows == before
    rows = receiver.receive(events[-1])
    assert rows[0]["semantic_revision"] == 1
    assert rows[0]["_projection"]["revision"] == 2
    assert rows[0]["_observations"]["session"]["observed_at"] == 20
    receiver.receive({"type": "heartbeat", "generation": "a" * 32, "cursor": 2})
    assert receiver.rows == rows
    with pytest.raises(ValueError, match="stale or out of order"):
        receiver.receive(events[0])


def test_v2_reset_requires_new_complete_connection_and_preserves_v1():
    v1 = envelope()
    v1["contract_version"] = 1
    v1.pop("observations")
    v1["worktrees"][0].pop("semantic_revision")
    v1["worktrees"][0]["fact_provenance"]["session"]["observed_at"] = 10
    assert parse_current_rows(v1)[0]["fact_provenance"]["session"]["observed_at"] == 10
    receiver = CurrentFeed()
    receive(receiver, frames(envelope(1)))
    with pytest.raises(ValueError, match="unexpected reset"):
        receiver.receive(frames(envelope(generation="b" * 32))[0])
    assert receiver.rows[0]["_projection"]["generation"] == "a" * 32
    replacement = CurrentFeed()
    assert receive(replacement, frames(envelope(generation="b" * 32)))[0][
        "_projection"
    ]["generation"] == "b" * 32


@pytest.mark.parametrize("damage", ["missing", "duplicate", "quality", "time", "future", "semantic"])
def test_v2_envelope_rejects_incomplete_or_untruthful_observations(damage):
    value = envelope()
    if damage == "missing":
        value.pop("observations")
    elif damage == "duplicate":
        value["observations"] *= 2
    elif damage == "quality":
        value["observations"][0]["facts"]["session"]["quality"] = "unavailable"
    elif damage == "time":
        value["observations"][0]["facts"]["session"]["observed_at"] = float("nan")
    elif damage == "future":
        value["observations"][0]["facts"]["session"]["observation_revision"] = 2
    else:
        value["worktrees"][0]["semantic_revision"] = True
    with pytest.raises(ValueError):
        parse_current_rows(value)


def test_missing_update_observation_rejects_changed_quality_without_mutating_committed_rows():
    receiver = CurrentFeed()
    receive(receiver, frames(envelope()))
    before = copy.deepcopy(receiver.rows)
    value = envelope(2)
    value["worktrees"][0]["semantic_revision"] = 2
    value["worktrees"][0]["fact_provenance"]["session"]["quality"] = "unavailable"
    for event in frames(value, initial=False):
        if event["type"] == "observation":
            continue
        if event["type"] == "commit":
            with pytest.raises(ValueError, match="observation does not match"):
                receiver.receive(event)
        else:
            receiver.receive(event)
    assert receiver.rows == before
