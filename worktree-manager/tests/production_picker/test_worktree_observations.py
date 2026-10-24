"""V2 parser-only coverage; loader and UI integration remain separate."""

from __future__ import annotations

import copy
import json

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


def near_limit_envelope(*, unicode=False, revision=1):
    value = envelope(revision)
    row = value["worktrees"][0]
    row["semantic_revision"] = revision
    row["closure"] = {}
    value["observations"][0]["derivations"] = {
        "closure": {"computed_at": "2026-10-23T00:00:00Z", "observation_revision": revision},
    }
    for index in range(15):
        row[f"padding{index}"] = "é" * 2048 if unicode else "x" * 4096
    row["tail"] = ""
    size = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    row["tail"] = "x" * (65536 - size)
    assert len(row["tail"]) <= 4096
    assert len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) == 65536
    return value


@pytest.mark.parametrize("unicode", [False, True])
@pytest.mark.parametrize("receiver_type", [CurrentSnapshot, CurrentFeed])
def test_near_limit_snapshot_counts_only_wire_bytes_at_done(unicode, receiver_type):
    value = near_limit_envelope(unicode=unicode)
    assert parse_current_rows(value)[0]["title"] == "Title"
    receiver = receiver_type()
    events = frames(value)
    for event in events[:-1]:
        assert receiver.receive(event) is None
        if isinstance(receiver, CurrentFeed):
            assert receiver.rows is None and receiver.head is None
    row = receiver.receive(events[-1])[0]
    assert row["tail"] == value["worktrees"][0]["tail"]
    assert row["_observations"]["session"]["observed_at"] == 10
    assert row["_observation_derivations"] == value["observations"][0]["derivations"]


@pytest.mark.parametrize("unicode", [False, True])
def test_near_limit_observation_only_update_counts_only_wire_bytes(unicode):
    value = near_limit_envelope(unicode=unicode)
    receiver = CurrentFeed()
    receive(receiver, frames(value))
    before = copy.deepcopy(receiver.rows)
    head = copy.deepcopy(receiver.head)
    update = envelope(2, at=20)
    update["observations"][0]["derivations"] = {
        "closure": {"computed_at": "2026-10-23T00:00:01Z", "observation_revision": 2},
    }
    events = frames(update, initial=False, semantic=False)
    assert not any(event["type"] == "worktree" for event in events)
    for event in events[:-1]:
        assert receiver.receive(event) is None
        assert receiver.rows == before and receiver.head == head
    row = receiver.receive(events[-1])[0]
    assert row["semantic_revision"] == 1
    assert row["tail"] == before[0]["tail"]
    assert row["_projection"]["revision"] == receiver.head["revision"] == 2
    assert row["_observations"]["session"]["observed_at"] == 20
    assert row["_observation_derivations"] == update["observations"][0]["derivations"]


@pytest.mark.parametrize("field", [
    "tail", "_projection", "_availability", "_observations", "_observation_derivations",
])
@pytest.mark.parametrize("initial", [False, True])
def test_oversize_raw_wire_metadata_never_bypasses_utf8_byte_bound(field, initial):
    value = near_limit_envelope(unicode=True, revision=1 if initial else 2)
    row = value["worktrees"][0]
    row[field] = row.get(field, "") + "é"
    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded) < 65536 < len(encoded.encode("utf-8"))
    with pytest.raises(ValueError, match="byte bound"):
        parse_current_rows(value)
    receiver = CurrentFeed()
    if not initial:
        receive(receiver, frames(envelope()))
    before, head = copy.deepcopy(receiver.rows), copy.deepcopy(receiver.head)
    events = frames(value, initial=initial)
    receiver.receive(events[0])
    with pytest.raises(ValueError, match="byte bound"):
        receiver.receive(events[1])
    assert receiver.rows == before and receiver.head == head


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
