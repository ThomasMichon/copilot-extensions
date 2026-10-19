"""Current-vote and revision-round transitions through the actual shared waiter."""

import base64
from dataclasses import replace
import json

import pytest

from agent_worktrees.pr_contract import Baseline, PRSnapshot, Review, compute_events, default_until, merge_readiness
from agent_worktrees.pr_observation import ApprovalEvent, Discussion, Gate, Observation, Policy, Vote
from agent_worktrees.pr_observation_events import cursor_for, read_cursor
from agent_worktrees.pr_snapshot_payload import snapshot_payload
from agent_worktrees.pr_watch import run_wait


def snapshot(*, value=10, event_id=1, discussion=None, policy=None, human=False, head="head"):
    gates = (Gate("approval", "review", "blocked" if human else "satisfied", "human", "Approval"),)
    evidence = Observation(
        "azure-devops", "Project/repository", 7, "https://dev.azure.com/example", head,
        votes=(Vote("reviewer", "Reviewer", value),),
        approval_events=(ApprovalEvent(str(event_id), "reviewer", value, "2026-01-01T10:01:00Z"),),
        discussions=(discussion,) if discussion else (),
        policies=(policy,) if policy else (),
        gates=gates, complete=True,
    )
    return PRSnapshot(head_sha=head, reviews=(Review(1, "APPROVED" if value > 0 else "CHANGES_REQUESTED",
                                                   "reviewer"),),
                      author="author", observation=evidence)


def names(before, after, until=("any",)):
    base = Baseline.from_cursor(Baseline.from_snapshot(before).to_cursor())
    return [event["event"] for event in compute_events(base, after, until)]


def test_same_reviewer_vote_change_cannot_hide_behind_unchanged_synthetic_id():
    before, after = snapshot(), snapshot(value=-5, event_id=2)
    assert before.reviews[0].id == after.reviews[0].id
    assert "changes_requested" in names(before, after)
    assert "review_changed" in names(before, after)


def test_nonblocking_defaults_retain_review_ci_and_native_discussion_transitions():
    selected = default_until(False)
    assert {"approved", "commented", "changes_requested", "checks_succeeded"} <= set(selected)
    assert "changes_requested" in names(snapshot(), snapshot(value=-5, event_id=2), selected)
    original = Discussion("5", "active", 1, "1", "2026-01-01T10:00:00Z")
    assert "threads_changed" in names(snapshot(discussion=original),
                                     snapshot(discussion=replace(original, status="fixed")), selected)


def test_fresh_same_actor_approval_wakes_even_when_positive_vote_did_not_change():
    before, after = snapshot(), snapshot(event_id=2)
    assert before.reviews == after.reviews
    assert names(before, after, ("approved",)) == ["approved"]
    assert names(after, after, ("approved",)) == []


@pytest.mark.parametrize("vote", [-5, 0, 10])
def test_generic_review_wait_observes_fresh_native_event_with_unchanged_vote(vote):
    before, after = snapshot(value=vote), snapshot(value=vote, event_id=2)
    result = _wait_for([after], Baseline.from_snapshot(before), ["review_changed"])
    assert result.matched
    assert result.payload["transitions"] == ["review_changed"]


def test_generic_review_transition_is_not_duplicated_by_vote_and_history_changes():
    before, after = snapshot(), snapshot(value=0, event_id=2)
    events = compute_events(Baseline.from_snapshot(before), after, ("any",))
    assert [event["event"] for event in events].count("review_changed") == 1


def test_thread_resolution_and_new_reply_are_observable_without_review_verdict():
    original = Discussion("5", "active", 1, "1", "2026-01-01T10:00:00Z")
    resolved = replace(original, status="fixed")
    replied = replace(original, comment_id="2")
    assert names(snapshot(discussion=original), snapshot(discussion=resolved),
                 ("threads_changed",)) == ["threads_changed"]
    assert names(snapshot(discussion=original), snapshot(discussion=replied),
                 ("threads_changed",)) == ["threads_changed"]


def test_policy_change_wakes_without_changing_the_overall_check_rollup():
    first = Policy("1", "Build", "running", True)
    second = replace(first, state="rejected")
    before, after = snapshot(policy=first), snapshot(policy=second)
    assert before.checks_state == after.checks_state
    assert names(before, after, ("policies_changed",)) == ["policies_changed"]


def test_new_head_and_human_checkpoint_are_separate_events():
    before, after = snapshot(), snapshot(human=True, head="new-head")
    observed = names(before, after)
    assert "pushed" in observed and "ready_for_human" in observed
    assert "approved" not in observed


def test_target_bound_cursor_refuses_foreign_repo_or_provider():
    first, other = snapshot(), snapshot()
    other = replace(other, observation=replace(other.observation, repo="Other/repository"))
    with pytest.raises(ValueError, match="another target"):
        names(first, other)


def test_native_observation_cursor_roundtrip_preserves_legacy_fields():
    first = snapshot()
    base = replace(Baseline.from_snapshot(first), consent_update="ack")
    restored = Baseline.from_cursor(base.to_cursor())
    assert restored.observation_cursor == base.observation_cursor
    assert restored.head_sha == first.head_sha and restored.consent_update == "ack"
    assert Baseline.from_cursor("r123.mc.hold.kpending").observation_cursor == ""
    assert read_cursor(restored.observation_cursor)["last_event"] == 1


@pytest.mark.parametrize("value", ["bad", "x" * 4097])
def test_corrupt_observation_cursor_does_not_silently_rebaseline(value):
    with pytest.raises(ValueError):
        read_cursor(value)


def test_present_but_empty_observation_cursor_is_not_a_legacy_baseline():
    with pytest.raises(ValueError):
        Baseline.from_cursor("r0.....o")


def test_one_shot_payload_is_the_same_normalized_evidence_not_another_driver():
    first = snapshot()
    payload = snapshot_payload(first, "Project/repository", 7, "azure-devops",
                               "https://dev.azure.com/example")
    assert payload["schema"] == "agent-worktrees.pr-snapshot"
    assert payload["observation"] == first.observation.to_dict()
    assert Baseline.from_cursor(payload["cursor"]).observation_cursor
    assert payload["reviews"][0]["commit_id"] == ""


def test_armed_native_autocomplete_is_not_claimed_as_clear_to_merge_with_human_gates():
    first = replace(snapshot(human=True), labels=("auto-complete",), mergeable=True)
    report = merge_readiness(first, automerge_label="auto-complete")
    assert report["consent_present"] and report["consent_action"] == "already"
    assert not report["clear_to_merge"]
    assert report["ready_for_human"]
    assert not report["required_gates_satisfied"]


@pytest.mark.parametrize("operation", ["consent", "direct"])
@pytest.mark.parametrize("human", [False, True])
def test_operation_readiness_preserves_native_gate_requirements(operation, human):
    current = replace(snapshot(human=human), labels=("auto-complete",), mergeable=True)
    report = merge_readiness(
        current, automerge_label="auto-complete", approval_required=False, operation=operation,
    )
    assert report["operation"] == operation
    assert report["clear_to_merge"] is not human
    assert report["required_gates_satisfied"] is not human
    assert report["ready_for_human"] is human
    if operation == "direct":
        assert report["consent_action"] == "not-applicable"
        assert report["ready_for_direct_merge"] is not human
        assert report["eligible"] is not human
        if human:
            assert report["reason"] == "required native gates are not satisfied"
    else:
        assert report["consent_action"] == "already"


def _wait_for(snapshots, baseline, until):
    remaining = iter(snapshots)
    ticks = iter(range(100))
    return run_wait(
        repo="Project/repository", pr=7, until=until, baseline=baseline,
        fetch=lambda: next(remaining), timeout=len(snapshots), interval=1,
        now=lambda: next(ticks), sleep=lambda _: None,
    )


@pytest.mark.parametrize("wake_on_gate", [False, True])
@pytest.mark.parametrize("vote", [10, -5])
def test_wait_returned_cursor_preserves_history_watermark_through_unavailable_read(wake_on_gate, vote):
    complete = snapshot(value=vote, event_id=7)
    unavailable = replace(complete, observation=replace(
        complete.observation, approval_events=(), complete=False,
        errors=("threads_unavailable",),
        gates=(Gate("history", "review", "unknown", "external", "History unavailable"),),
    ))
    result = _wait_for(
        [unavailable], Baseline.from_snapshot(complete),
        ["gates_changed"] if wake_on_gate else ["approved", "changes_requested"],
    )
    assert result.matched is wake_on_gate
    returned = Baseline.from_cursor(result.payload["cursor"])
    assert read_cursor(returned.observation_cursor)["last_event"] == 7

    recovered = _wait_for([complete], returned, ["approved", "changes_requested"])
    assert not recovered.matched
    fresh = _wait_for(
        [snapshot(value=vote, event_id=8)],
        Baseline.from_cursor(recovered.payload["cursor"]), ["approved", "changes_requested"],
    )
    assert fresh.matched
    assert fresh.payload["transitions"] == ["approved" if vote > 0 else "changes_requested"]


def test_wait_known_empty_history_does_not_reset_a_previous_watermark():
    complete = snapshot(event_id=7)
    empty = replace(complete, observation=replace(complete.observation, approval_events=()))
    assert read_cursor(Baseline.from_snapshot(empty).observation_cursor)["last_event"] == 0
    result = _wait_for([empty], Baseline.from_snapshot(complete), ["approved"])
    assert not result.matched
    assert read_cursor(Baseline.from_cursor(result.payload["cursor"]).observation_cursor)["last_event"] == 7


def test_wait_retains_watermark_observed_during_polling_before_history_becomes_unavailable():
    first = snapshot(event_id=7)
    newer = snapshot(event_id=8)
    unavailable = replace(newer, observation=replace(
        newer.observation, approval_events=(), complete=False, errors=("threads_unavailable",),
    ))
    result = _wait_for([newer, unavailable], Baseline.from_snapshot(first), ["pushed"])
    assert not result.matched
    assert read_cursor(Baseline.from_cursor(result.payload["cursor"]).observation_cursor)["last_event"] == 8


@pytest.mark.parametrize("field,value", [
    ("repo", "Other/repository"), ("provider", "github"), ("number", 8),
    ("api_base", "https://dev.azure.com/other"),
])
def test_wait_watermark_carry_refuses_foreign_target(field, value):
    first = snapshot()
    foreign = replace(first, observation=replace(first.observation, **{field: value}))
    with pytest.raises(ValueError, match="another target"):
        _wait_for([foreign], Baseline.from_snapshot(first), ["approved"])


def test_watermark_carry_refuses_corrupt_cursor():
    with pytest.raises(ValueError, match="invalid PR observation cursor"):
        cursor_for(snapshot().observation, "bad")


def test_known_empty_and_unavailable_history_remain_distinct_without_inventing_events():
    first = snapshot()
    empty = replace(first.observation, approval_events=())
    unavailable = replace(empty, complete=False, errors=("threads_unavailable",))
    for observation in (empty, unavailable):
        assert read_cursor(cursor_for(observation))["last_event"] == 0
        assert observation.votes[0].vote == 10
    assert empty.to_dict()["complete"] is True
    assert unavailable.to_dict()["complete"] is False
    assert unavailable.to_dict()["errors"] == ("threads_unavailable",)


def _with_votes(*votes, events=()):
    first = snapshot()
    return replace(first, observation=replace(
        first.observation, votes=tuple(Vote(actor, actor, vote) for actor, vote in votes),
        approval_events=events,
    ))


@pytest.mark.parametrize("before_votes,after_votes", [
    ([("a", -5), ("b", 0)], [("a", -5), ("b", 10)]),
    ([("a", -5), ("b", -10)], [("a", -5)]),
])
def test_rejection_only_wait_ignores_unrelated_vote_changes(before_votes, after_votes):
    before, after = _with_votes(*before_votes), _with_votes(*after_votes)
    result = _wait_for([after], Baseline.from_snapshot(before), ["changes_requested"])
    assert not result.matched
    assert result.payload["transitions"] == []
    assert names(before, after, ("review_changed",)) == ["review_changed"]


@pytest.mark.parametrize("before_votes,after_votes", [
    ([("a", -5), ("b", 0)], [("a", -5), ("b", -5)]),
    ([("a", -5)], [("a", -10)]),
    ([], [("a", -5)]),
])
def test_rejection_only_wait_detects_new_actor_bound_negative_vote(before_votes, after_votes):
    before, after = _with_votes(*before_votes), _with_votes(*after_votes)
    result = _wait_for([after], Baseline.from_snapshot(before), ["changes_requested"])
    assert result.matched
    assert result.payload["transitions"] == ["changes_requested"]


@pytest.mark.parametrize("complete", [False, True])
def test_negative_native_history_event_takes_precedence_over_vote_fallback(complete):
    before = _with_votes(("a", 0))
    after = _with_votes(
        ("a", -5), events=(ApprovalEvent("2", "a", -5, "2026-01-01T10:01:00Z"),),
    )
    after = replace(after, observation=replace(after.observation, complete=complete))
    result = _wait_for([after], Baseline.from_snapshot(before), ["changes_requested"])
    assert result.payload["transitions"] == ["changes_requested"]
    assert result.payload["events"][0]["review"]["id"] == "2"


@pytest.mark.parametrize("had_known_baseline", [False, True])
def test_unavailable_votes_do_not_invent_rejection_when_existing_vote_recovers(had_known_baseline):
    recovered = _with_votes(("a", -5))
    unavailable = replace(recovered, observation=replace(
        recovered.observation, votes=(), complete=False, errors=("reviews_unavailable",),
    ))
    first = _wait_for([unavailable], Baseline.from_snapshot(
        recovered if had_known_baseline else unavailable), ["changes_requested"])
    resumed = Baseline.from_cursor(first.payload["cursor"])
    result = _wait_for([recovered], resumed, ["changes_requested"])
    assert not first.matched and not result.matched
    assert read_cursor(resumed.observation_cursor)["negative_votes"] is None


def test_negative_vote_metadata_change_does_not_become_new_rejection():
    before = _with_votes(("a", -5))
    after = replace(before, observation=replace(
        before.observation, votes=(Vote("a", "Renamed reviewer", -5, required=True),),
    ))
    assert names(before, after, ("changes_requested",)) == []
    assert names(before, after, ("review_changed",)) == ["review_changed"]


def test_legacy_observation_cursor_without_actor_evidence_does_not_infer_rejection():
    before, after = _with_votes(("a", -5), ("b", 0)), _with_votes(("a", -5), ("b", 10))
    base = Baseline.from_snapshot(before)
    data = read_cursor(base.observation_cursor)
    data.pop("negative_votes")
    legacy = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    restored = Baseline.from_cursor(replace(base, observation_cursor=legacy).to_cursor())
    result = _wait_for([after], restored, ["changes_requested"])
    assert not result.matched
    assert read_cursor(Baseline.from_cursor(result.payload["cursor"]).observation_cursor)["negative_votes"]


@pytest.mark.parametrize("before_count,after_count", [(16, 17), (17, 16), (17, 18)])
def test_over_capacity_negative_vote_evidence_is_unknown_not_truncated(before_count, after_count):
    before = _with_votes(*[(str(i), -5) for i in range(before_count)])
    after = _with_votes(*[(str(i), -5) for i in range(after_count)])
    for snap, count in ((before, before_count), (after, after_count)):
        cursor = cursor_for(snap.observation)
        evidence = read_cursor(cursor)["negative_votes"]
        assert len(cursor) <= 4096
        assert (evidence is None) is (count > 16)
        if evidence is not None:
            assert len(evidence) == count
    assert names(before, after, ("changes_requested",)) == []
    assert names(before, after, ("review_changed",)) == ["review_changed"]


def test_cursor_omits_optional_actor_evidence_to_keep_large_target_within_limit():
    snap = _with_votes(*[(str(i), -5) for i in range(16)])
    evidence = replace(snap.observation, repo="r" * 1800)
    cursor = cursor_for(evidence)
    assert len(cursor) <= 4096
    assert "negative_votes" not in read_cursor(cursor)
    assert read_cursor(cursor)["target"][2] == evidence.repo
    with pytest.raises(ValueError, match="oversized"):
        cursor_for(replace(evidence, repo="r" * 5000))


@pytest.mark.parametrize("negative_votes", ["bad", ["bad"], ["a" * 64] * 2, ["a" * 64] * 17])
def test_corrupt_actor_bound_vote_evidence_is_refused(negative_votes):
    data = read_cursor(cursor_for(snapshot().observation))
    data["negative_votes"] = negative_votes
    cursor = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    with pytest.raises(ValueError, match="negative votes"):
        read_cursor(cursor)
