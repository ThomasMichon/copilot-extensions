"""Normalized owner acquisition into the existing journal, without provider writes."""

import copy
from dataclasses import replace
from datetime import datetime
import json

import pytest

from test_review_workspace import evidence as evidence
from test_review_workspace import section

from agent_pull_requests.attention import Cursor
from agent_pull_requests.conversation_journal import ConversationJournal
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.workspace_conversation import conversation_key, project_generation


def generation(evidence, second=0):
    value = copy.deepcopy(evidence)
    for data in value["sections"].values():
        data["observed_from"] = f"2026-10-30T00:00:{second:02d}.100001Z"
        data["observed_to"] = f"2026-10-30T00:00:{second:02d}.900001Z"
    return build_generation(value)["generation"]


def test_projection_is_body_free_and_preserves_native_conservative_bounds(evidence):
    value = project_generation(generation(evidence), "owner", None)
    assert value.candidate.head == "a" * 40 and value.candidate.base == "c" * 40
    assert value.observed_from == int(
        datetime.fromisoformat("2026-10-30T00:00:00+00:00").timestamp()
    )
    assert value.observed_to == value.observed_from + 1
    assert value.our_roles == ("reviewer",) and value.reviewers == ("reviewer-id",)
    assert value.verdict == "unknown" and value.review_rounds is None
    assert dict(value.counts) == {
        "comments": 2,
        "threads": 1,
        "checks": 1,
        "participants": 2,
        "reviews": None,
        "commits": None,
    }
    assert "Reply" not in json.dumps(value.document())
    assert "Review this change" not in json.dumps(value.document())


@pytest.mark.parametrize("change", ["reply", "edit", "delete", "status", "target"])
def test_projection_changes_same_head_dialogue_and_target_but_not_poll_times(evidence, change):
    journal = ConversationJournal()
    initial = project_generation(generation(evidence), "owner", None)
    journal.observe(initial, expected=None)
    poll = project_generation(generation(evidence, 2), "owner", initial)
    assert journal.observe(poll, expected=Cursor(1, 1))["status"] == "unchanged"
    thread = evidence["sections"]["discussions"]["data"][0]
    if change == "reply":
        thread["comments"].append({"id": 3, "content": "New reply"})
    elif change == "edit":
        thread["comments"][0]["content"] = "Edited question"
    elif change == "delete":
        thread["comments"][0] = {"id": 1, "isDeleted": True}
    elif change == "status":
        thread["status"] = "active"
    else:
        evidence["sections"]["pull_request"]["data"]["lastMergeTargetCommit"]["commitId"] = (
            "f" * 40
        )
    current = project_generation(generation(evidence, 4), "owner", poll)
    assert journal.observe(current, expected=Cursor(1, 1))["cursor"]["sequence"] == 2
    assert current.candidate.head == initial.candidate.head
    assert current.candidate.base == ("f" if change == "target" else "c") * 40


def test_rounding_does_not_invent_ordering_for_fractional_overlap(evidence):
    journal = ConversationJournal()
    first = project_generation(generation(evidence), "owner", None)
    journal.observe(first, expected=None)
    evidence["sections"]["discussions"]["data"][0]["status"] = "active"
    overlapping = project_generation(generation(evidence), "owner", first)
    assert journal.observe(overlapping, expected=Cursor(1, 1))["status"] == "stale_observation"
    later = project_generation(generation(evidence, 2), "owner", first)
    assert journal.observe(later, expected=Cursor(1, 1))["status"] == "observed"


def test_omitted_comments_or_participant_identity_are_not_known_zero(evidence):
    evidence["sections"]["discussions"]["data"][0].pop("comments")
    missing = project_generation(generation(evidence), "owner", None)
    assert dict(missing.counts)["comments"] is None
    assert dict(missing.counts)["participants"] is None
    evidence["sections"]["discussions"]["data"][0]["comments"] = []
    empty = project_generation(generation(evidence), "owner", None)
    assert dict(empty.counts)["comments"] == 0
    assert dict(empty.sections)["comments"] != dict(missing.sections)["comments"]
    evidence["sections"]["pull_request"]["data"].pop("createdBy")
    unknown_author = project_generation(generation(evidence), "owner", None)
    assert dict(unknown_author.counts)["participants"] is None


def test_partial_observation_preserves_relationships_and_unknown_rounds(evidence):
    previous = replace(
        project_generation(generation(evidence), "owner", None),
        review_rounds=3,
    )
    evidence["sections"]["reviewers"] = section([], complete=False, errors=["page_missing"])
    evidence["sections"]["discussions"] = section([], complete=False, errors=["read_failed"])
    evidence["sections"]["pull_request"]["data"].pop("createdBy")
    current = project_generation(generation(evidence, 2), "owner", previous)
    assert not current.complete
    assert current.author == previous.author
    assert current.our_roles == previous.our_roles and current.reviewers == previous.reviewers
    assert current.review_rounds == 3 and current.verdict == "unknown"
    assert dict(current.counts)["comments"] is None
    foreign = copy.deepcopy(evidence)
    foreign["scope"]["principal_id"] = "different-account"
    with pytest.raises(ValueError, match="scope"):
        project_generation(generation(foreign, 2), "owner", previous)


@pytest.mark.parametrize("field", ["status", "lastMergeSourceCommit", "lastMergeTargetCommit"])
def test_missing_candidate_or_state_is_not_empty_success(evidence, field):
    evidence["sections"]["pull_request"]["data"].pop(field)
    assert project_generation(generation(evidence), "owner", None) is None


def test_existing_store_ingest_restarts_compacts_and_reopens_without_erasing_replay(
    tmp_path, evidence
):
    from agent_pull_requests.watch_replay_store import ReplayStore

    store = ReplayStore(tmp_path.resolve(), "owner", clock=lambda: 1)
    store.provision()
    store.adopt_review_operations(lifecycle_checkpoint=lambda: None, expires_at=100)
    journal = ConversationStore(store)
    journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    initial = generation(evidence)
    key = conversation_key(initial["scope"], "owner")
    assert journal.observe_workspace(initial, expires_at=100)["cursor"] == {
        "epoch": 1,
        "sequence": 1,
    }
    assert journal.observe_workspace(initial, expires_at=100)["status"] == "unchanged"
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    assert (
        journal.observe_workspace(generation(evidence, 2), expires_at=100)["cursor"]["sequence"]
        == 2
    )
    evidence["sections"]["pull_request"]["data"]["status"] = "active"
    partial = copy.deepcopy(evidence)
    partial["sections"].pop("reviewers")
    assert journal.observe_workspace(generation(partial, 4), expires_at=100) == {
        "status": "unavailable",
        "reason": "terminal_requires_complete_observation",
    }
    assert journal.observe_workspace(generation(evidence, 6), expires_at=100)["cursor"] == {
        "epoch": 2,
        "sequence": 1,
    }
    reopened = ConversationStore(ReplayStore(tmp_path.resolve(), "owner", clock=lambda: 1))
    result = reopened.events(key, after=Cursor(1, 2), limit=16, expires_at=100)
    assert result["status"] == "resync_required" and result["previous_terminal"]["sections"] == {}
