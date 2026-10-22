"""Native ADO vote/iteration/policy fixtures, through the configured driver."""

from copy import deepcopy
import base64
import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from agent_worktrees.pr_observation_config import ObservationConfig, PolicyRule
from agent_worktrees.pr_contract import Baseline, compute_events
from agent_worktrees.pr_observation_events import read_cursor
from agent_worktrees.providers import azure_observation as ado
from agent_worktrees.providers.azure_devops import AzureDevOpsProvider
from agent_worktrees.providers.azure_observation_reads import AdoReader, Collection
from agent_worktrees.providers.base import ProviderError


HEAD, MERGE = "a" * 40, "b" * 40
POSTED = "2026-01-01T10:00:00Z"
APPROVED = "2026-01-01T10:01:00Z"
EARLIER = "2026-01-01T09:59:00Z"


def vote(actor, value=10, *, required=False, group=False, groups=()):
    return {"id": actor, "displayName": actor, "vote": value, "isRequired": required,
            "isContainer": group, "votedFor": [{"id": member} for member in groups]}


def event(number=1, actor="reviewer", value=10, at=APPROVED):
    return {
        "id": number, "publishedDate": at, "comments": [{"commentType": "system"}],
        "properties": {
            "CodeReviewThreadType": {"$value": "VoteUpdate"},
            "CodeReviewVotedByTfId": {"$value": actor},
            "CodeReviewVoteResult": {"$value": str(value)},
        },
    }


def thread(status="active", *, iteration=1, actor="reviewer"):
    return {
        "id": 5, "status": status, "lastUpdatedDate": APPROVED,
        "comments": [{"id": 1, "commentType": "text", "author": {"id": actor},
                      "publishedDate": APPROVED, "lastUpdatedDate": APPROVED}],
        "pullRequestThreadContext": {"iterationContext": {"secondComparingIteration": iteration}},
        "threadContext": {"filePath": "/example.py", "rightFileStart": {"line": 5}},
    }


def policy(number=1, state="approved", *, name="Build", settings=None, source=HEAD, required=True):
    type_id = (ado.MINIMUM_APPROVAL_POLICY if settings and
               any(key in settings for key in ("minimumApproverCount", "requireVoteOnLastIteration",
                                               "requireVoteOnEachIteration"))
               else "type-" + str(number))
    return {
        "evaluationId": str(number), "status": state, "startedDate": APPROVED,
        "context": {"lastMergeSourceCommitId": source},
        "configuration": {
            "id": number, "isEnabled": True, "isBlocking": required,
            "type": {"displayName": name, "id": type_id},
            "settings": settings or {"displayName": name},
        },
    }


class Reader:
    def __init__(self):
        self.data = {
            "status": "active", "mergeStatus": "succeeded", "title": "Example",
            "createdBy": {"id": "author"}, "lastMergeSourceCommit": {"commitId": HEAD},
            "lastMergeCommit": {"commitId": MERGE}, "targetRefName": "refs/heads/main",
            "repository": {"id": "repository-id", "name": "repository",
                           "project": {"id": "project-id", "name": "Project"}},
            "reviewers": [vote("reviewer")],
        }
        self.iteration_rows = Collection(({
            "id": 2, "reason": "push", "sourceRefCommit": {"commitId": HEAD},
            "createdDate": POSTED, "push": {"date": POSTED},
        },))
        self.thread_rows = Collection((event(),))
        self.policy_rows = Collection((policy(),))
        self.status_rows = Collection()
        self.configuration_rows = None
        self.after = None
        self.calls = []

    def pr(self):
        self.calls.append("pr")
        return deepcopy(self.after if self.after and self.calls.count("pr") > 1 else self.data)

    def iterations(self):
        self.calls.append("iterations")
        return self.iteration_rows

    def threads(self):
        self.calls.append("threads")
        return self.thread_rows

    def statuses(self):
        self.calls.append("statuses")
        return self.status_rows

    def evaluations(self, project, number):
        self.calls.append("policies")
        assert project == "project-id" and number == 7
        return self.policy_rows

    def configurations(self, repository, ref):
        self.calls.append("configurations")
        assert repository == "repository-id" and ref == "refs/heads/main"
        if self.configuration_rows is not None:
            return self.configuration_rows
        return Collection(tuple(row["configuration"] for row in self.policy_rows.records))

    def build(self, number):
        self.calls.append("build")
        return {"sourceVersion": MERGE}

    def failed_tasks(self, number):
        self.calls.append("timeline")
        return ({"task": "Compile", "errors": ["Example compile failure"], "log": ""},)


def read(reader, config=None):
    return ado.get_snapshot(
        AzureDevOpsProvider(), "Project/repository", 7,
        api_base="https://dev.azure.com/example", token=None, observation=config,
        reader_factory=lambda *args: reader,
    )


def gates(snapshot):
    return {gate.id: gate for gate in snapshot.observation.gates}


def test_native_current_evidence_is_shared_with_status_and_wait():
    reader = Reader()
    result = read(reader)
    assert result.head_sha == HEAD and result.checks_state == "success"
    assert result.observation.to_dict()["all_required_satisfied"]
    assert not result.observation.to_dict()["ready_for_human"]
    assert reader.calls.count("pr") == 2
    assert result.observation.source_push_at == POSTED


@pytest.mark.parametrize("value", [-5, -10])
def test_enabling_reviews_does_not_invent_an_existing_negative_vote_event(value):
    reader = Reader()
    reader.data["reviewers"] = [vote("reviewer", value)]
    reader.thread_rows = Collection()
    skipped = read(reader, ObservationConfig(reviews=False))
    assert skipped.observation.complete
    assert not skipped.observation.to_dict()["votes_known"]
    baseline = Baseline.from_cursor(Baseline.from_snapshot(skipped).to_cursor())
    reader.calls.clear()
    recovered = read(reader)
    assert recovered.observation.to_dict()["votes_known"]
    assert compute_events(baseline, recovered, ("changes_requested",)) == []
    assert read_cursor(baseline.observation_cursor)["negative_votes"] is None
    known = Baseline.from_snapshot(recovered)
    assert read_cursor(known.observation_cursor)["negative_votes"]


@pytest.mark.parametrize("updated_field", ["content", "lastUpdatedDate", "isDeleted"])
def test_edit_before_last_reply_wakes_wait_without_persisting_comment_bodies(updated_field):
    reader = Reader()
    original = thread()
    original["comments"][0]["content"] = "Original earlier comment body"
    original["comments"].append({
        "id": 2, "commentType": "text", "author": {"id": "author"},
        "publishedDate": APPROVED, "lastUpdatedDate": APPROVED,
        "content": "Unchanged last reply body",
    })
    reader.thread_rows = Collection((event(), original))
    before = read(reader)
    baseline = Baseline.from_cursor(Baseline.from_snapshot(before).to_cursor())
    edited = deepcopy(original)
    edited["comments"][0][updated_field] = {
        "content": "Edited earlier comment body",
        "lastUpdatedDate": "2026-01-01T10:02:00Z",
        "isDeleted": True,
    }[updated_field]
    reader.thread_rows = Collection((event(), edited))
    after = read(reader)
    assert before.observation.discussions[0].comment_id == after.observation.discussions[0].comment_id
    assert before.observation.discussions[0].updated_at == after.observation.discussions[0].updated_at
    assert compute_events(baseline, after, ("threads_changed",)) == [{"event": "threads_changed"}]
    for result in (before, after):
        serialized = json.dumps(result.observation.to_dict())
        assert "earlier comment body" not in serialized
        assert "Unchanged last reply body" not in serialized


def test_persisted_approval_is_not_reset_but_does_not_establish_latest_push_approval():
    reader = Reader()
    reader.thread_rows = Collection((event(at=EARLIER),))
    result = read(reader)
    assert gates(result)["minimum_approvals"].state == "satisfied"
    assert gates(result)["latest_push_approval"].state == "blocked"
    assert result.observation.to_dict()["ready_for_human"]
    assert result.observation.votes[0].vote == 10


@pytest.mark.parametrize("requirement", ["requireVoteOnLastIteration", "requireVoteOnEachIteration"])
def test_current_native_latest_iteration_policy_can_prove_a_same_vote_reapproval(requirement):
    reader = Reader()
    reader.thread_rows = Collection((event(at=EARLIER),))
    reader.policy_rows = Collection((policy(
        name="Reviewers", settings={"minimumApproverCount": 1, requirement: True},
    ),))
    result = read(reader)
    assert gates(result)["latest_push_approval"].state == "satisfied"
    assert "native" in gates(result)["latest_push_approval"].summary
    stale = policy(name="Reviewers", settings={"requireVoteOnLastIteration": True}, source="older")
    reader.policy_rows = Collection((stale,))
    assert gates(read(reader))["latest_push_approval"].state != "satisfied"


def test_policy_without_latest_iteration_requirement_or_fresh_binding_is_not_proof():
    reader = Reader()
    reader.thread_rows = Collection((event(at=EARLIER),))
    old = policy(name="Reviewers", settings={"minimumApproverCount": 1})
    reader.policy_rows = Collection((old,))
    assert gates(read(reader))["latest_push_approval"].state == "blocked"
    old["configuration"]["settings"]["requireVoteOnLastIteration"] = True
    old["startedDate"] = EARLIER
    reader.policy_rows = Collection((old,))
    assert gates(read(reader))["latest_push_approval"].state == "blocked"


def test_custom_policy_cannot_impersonate_native_latest_iteration_semantics_with_an_unused_setting():
    reader = Reader()
    reader.thread_rows = Collection((event(at=EARLIER),))
    custom = policy(name="Custom", settings={"requireVoteOnLastIteration": True})
    custom["configuration"]["type"]["id"] = "custom-policy-type"
    reader.policy_rows = Collection((custom,))
    assert gates(read(reader))["latest_push_approval"].state != "satisfied"


def test_superseded_native_approval_policy_cannot_prove_a_newer_rejected_gate():
    reader = Reader()
    reader.thread_rows = Collection((event(at=EARLIER),))
    old = policy(name="Reviewers", settings={"requireVoteOnLastIteration": True})
    new = deepcopy(old)
    new.update(status="rejected", evaluationId="new", startedDate="2026-01-01T10:02:00Z")
    reader.policy_rows = Collection((old, new))
    assert gates(read(reader))["latest_push_approval"].state == "blocked"


@pytest.mark.parametrize(("older", "newer"), [
    ("2026-01-01T10:00:00Z", "2026-01-01T10:00:00.5Z"),
    ("2026-01-01T12:00:00+02:00", "2026-01-01T09:01:00-01:00"),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_evaluation_chronology_uses_instants_not_string_or_evaluation_id(older, newer, reverse):
    reader = Reader()
    reader.thread_rows = Collection((event(at=EARLIER),))
    old = policy(name="Reviewers", settings={"requireVoteOnLastIteration": True})
    old.update(startedDate=older, evaluationId="z")
    new = deepcopy(old)
    new.update(status="rejected", evaluationId="a", startedDate=newer)
    reader.policy_rows = Collection((new, old) if reverse else (old, new))
    result = read(reader)
    assert gates(result)["policy:1"].state == "blocked"
    assert gates(result)["latest_push_approval"].state == "blocked"


@pytest.mark.parametrize("at", [None, "", "invalid", "2026-01-01T10:00:00",
                               "2026-01-01T11:01:00+01:00"])
def test_missing_or_ambiguous_evaluation_chronology_cannot_prove_readiness(at):
    reader = Reader()
    reader.thread_rows = Collection((event(at=EARLIER),))
    old = policy(name="Reviewers", settings={"requireVoteOnLastIteration": True})
    new = deepcopy(old)
    new.update(status="rejected", evaluationId="new", startedDate=at)
    reader.policy_rows = Collection((old, new))
    result = read(reader)
    assert gates(result)["latest_push_approval"].state == "blocked"
    assert gates(result)["policy_evidence"].state == "unknown"
    assert not result.observation.to_dict()["ready_for_human"]


@pytest.mark.parametrize(("older", "newer"), [
    ("2026-01-01T10:00:00Z", "2026-01-01T10:00:00.5Z"),
    ("2026-01-01T12:00:00+02:00", "2026-01-01T09:01:00-01:00"),
])
def test_native_status_chronology_uses_instants(older, newer):
    reader = Reader()
    reader.policy_rows = Collection((policy(settings={
        "statusName": "example", "invalidateOnSourceUpdate": True,
    }),))
    reader.status_rows = Collection((
        {"id": 9, "updatedDate": older, "context": {"name": "example"}, "iterationId": 2},
        {"id": 1, "updatedDate": newer, "context": {"name": "example"}, "iterationId": 1},
    ))
    assert gates(read(reader))["policy:1"].state == "pending"


@pytest.mark.parametrize("at", [None, "invalid", "2026-01-01T11:01:00+01:00"])
def test_unrankable_native_status_cannot_prove_readiness(at):
    reader = Reader()
    reader.policy_rows = Collection((policy(settings={
        "statusName": "example", "invalidateOnSourceUpdate": True,
    }),))
    reader.status_rows = Collection((
        {"id": 9, "updatedDate": APPROVED, "context": {"name": "example"}, "iterationId": 2},
        {"id": 1, "updatedDate": at, "context": {"name": "example"}, "iterationId": 1},
    ))
    result = read(reader)
    assert gates(result)["policy_evidence"].state == "unknown"
    assert not result.observation.to_dict()["ready_for_human"]


def test_author_fresh_approval_is_independent_of_non_author_minimum():
    reader = Reader()
    reader.data["reviewers"].append(vote("author"))
    reader.thread_rows = Collection((event(1, at=EARLIER), event(2, actor="author")))
    result = read(reader)
    assert gates(result)["minimum_approvals"].state == "satisfied"
    assert gates(result)["latest_push_approval"].state == "satisfied"
    reader.data["reviewers"] = [vote("author")]
    result = read(reader)
    assert gates(result)["minimum_approvals"].state == "blocked"
    assert gates(result)["latest_push_approval"].state == "satisfied"


def test_branch_roster_does_not_impose_unrelated_repository_or_path_scoped_policies():
    reader = Reader()
    conditional = policy(2, settings={"filenamePatterns": ["/other/**"]})["configuration"]
    repository_setting = policy(3, settings={"scope": [{"repositoryId": "repository-id"}]})["configuration"]
    reader.configuration_rows = Collection((
        policy()["configuration"], conditional, repository_setting,
    ))
    result = read(reader)
    assert result.observation.complete
    assert result.observation.to_dict()["all_required_satisfied"]
    assert set(p.id for p in result.observation.policies) == {"1"}


def test_discussion_report_exposes_native_thread_bound_reply_fingerprint():
    from agent_worktrees.providers.azure_discussions import get_report
    from agent_worktrees.providers.azure_remediation import thread_fingerprint
    reader = Reader()
    row = thread(iteration=1)
    row["comments"][0]["content"] = "Original feedback"
    row["comments"][0]["parentCommentId"] = 0
    row["comments"][0]["isDeleted"] = False
    row["isDeleted"] = False
    row["pullRequestThreadContext"]["iterationContext"]["firstComparingIteration"] = 1
    reader.thread_rows = Collection((row,))
    report = get_report(AzureDevOpsProvider(), "Project/repository", 7,
                        api_base="https://dev.azure.com/example", token=None,
                        reader_factory=lambda *args: reader)
    assert report["complete"]
    assert report["threads"][0]["fingerprint"] == thread_fingerprint(row)
    changed = deepcopy(row)
    changed["comments"][0]["content"] = "A concurrent reply"
    assert report["threads"][0]["fingerprint"] != thread_fingerprint(changed)


def test_readable_discussion_with_incomplete_reply_evidence_remains_readable():
    from agent_worktrees.providers.azure_discussions import get_report
    reader = Reader()
    row = thread()
    row["comments"][0]["content"] = "Readable feedback"
    reader.thread_rows = Collection((row,))
    report = get_report(AzureDevOpsProvider(), "Project/repository", 7,
                        api_base="https://dev.azure.com/example", token=None,
                        reader_factory=lambda *args: reader)
    assert report["complete"]
    assert report["threads"][0]["comments"][0]["text"] == "Readable feedback"
    assert report["threads"][0]["fingerprint"] == ""
    assert report["threads"][0]["reply_error"]


def test_applicable_conditional_policy_retains_rejection_and_configuration_freshness():
    reader = Reader()
    row = policy(2, "rejected", settings={"filenamePatterns": ["/example/**"]})
    row["configuration"]["revision"] = 1
    reader.policy_rows = Collection((row,))
    current = deepcopy(row["configuration"])
    current["revision"] = 2
    reader.configuration_rows = Collection((current,))
    result = read(reader)
    assert gates(result)["policy:2"].state == "blocked"
    assert gates(result)["policy_evidence"].state == "unknown"
    assert not result.observation.to_dict()["ready_for_human"]


@pytest.mark.parametrize("settings", [
    {"scope": "invalid"},
    {"scope": ["invalid"]},
    {"filenamePatterns": "invalid"},
    {"filenamePatterns": [""]},
])
def test_invalid_roster_applicability_evidence_remains_unknown(settings):
    reader = Reader()
    reader.configuration_rows = Collection((policy(2, settings=settings)["configuration"],))
    result = read(reader)
    assert gates(result)["policy_evidence"].state == "unknown"
    assert not result.observation.to_dict()["ready_for_human"]


def test_one_person_may_satisfy_multiple_native_required_sets_without_disjoint_assignment():
    reader = Reader()
    reader.data["reviewers"] = [
        vote("reviewer", groups=("group-a", "group-b")),
        vote("group-a", required=True, group=True),
        vote("group-b", required=True, group=True),
    ]
    result = read(reader)
    assert gates(result)["required_reviewers"].state == "satisfied"
    assert gates(result)["minimum_approvals"].summary.startswith("1 of 1")
    reader.data["reviewers"][1]["vote"] = 0
    result = read(reader)
    assert gates(result)["required_reviewers"].state == "blocked"
    assert gates(result)["minimum_approvals"].state == "satisfied"


@pytest.mark.parametrize("value", [-5, -10])
def test_blocking_or_rejected_review_is_actionable_even_with_another_approval(value):
    reader = Reader()
    reader.data["reviewers"].append(vote("other", value))
    result = read(reader)
    assert gates(result)["no_blocking_votes"].state == "blocked"
    assert not result.observation.to_dict()["ready_for_human"]


def test_unresolved_old_round_thread_blocks_even_after_author_reply():
    reader = Reader()
    reader.thread_rows = Collection((event(), thread(actor="author", iteration=1)))
    result = read(reader)
    assert gates(result)["comments_resolved"].state == "blocked"
    assert result.observation.discussions[0].iteration == 1
    assert not result.observation.discussions[0].awaiting_author
    assert not result.observation.to_dict()["all_required_satisfied"]


@pytest.mark.parametrize("status", ["fixed", "closed", "wontFix", "byDesign"])
def test_native_resolution_not_overall_verdict_clears_discussion(status):
    reader = Reader()
    reader.thread_rows = Collection((event(), thread(status)))
    assert gates(read(reader))["comments_resolved"].state == "satisfied"


def test_unknown_discussion_status_is_not_silently_resolved():
    reader = Reader()
    reader.thread_rows = Collection((event(), thread("unknown")))
    result = read(reader)
    assert gates(result)["comments_resolved"].state == "unknown"
    assert not result.observation.to_dict()["ready_for_human"]


def test_composite_policy_waits_for_human_prerequisites_but_is_not_ignored():
    reader = Reader()
    reader.thread_rows = Collection((event(at=EARLIER),))
    reader.policy_rows = Collection((policy(name="Compliance", state="rejected"),))
    config = ObservationConfig(policy_rules=(PolicyRule(name="Compliance", kind="composite"),))
    result = read(reader, config)
    assert gates(result)["policy:1"].state == "waiting"
    assert gates(result)["policy:1"].waiting_for == ("latest_push_approval",)
    assert result.observation.to_dict()["ready_for_human"]
    assert not result.observation.to_dict()["all_required_satisfied"]
    assert result.checks_state != "failure"
    reader.thread_rows = Collection((event(),))
    result = read(reader, config)
    assert gates(result)["policy:1"].state == "blocked"
    assert "policy:1" in result.observation.to_dict()["agent_actionable"]


def test_stale_build_or_missing_policy_evidence_never_greens_current_push():
    reader = Reader()
    reader.policy_rows = Collection((policy(source="old-head"),))
    result = read(reader)
    assert result.checks_state == "pending"
    assert gates(result)["policy:1"].state == "pending"
    reader.policy_rows = Collection(error="Policy page unavailable")
    result = read(reader)
    assert not result.observation.complete
    assert gates(result)["policy_evidence"].state == "unknown"
    assert not result.observation.to_dict()["ready_for_human"]


def test_required_configuration_without_an_evaluation_is_unknown_not_green():
    reader = Reader()
    reader.configuration_rows = Collection((policy()["configuration"],))
    reader.policy_rows = Collection()
    result = read(reader)
    assert not result.observation.complete
    assert gates(result)["policy_evidence"].state == "unknown"
    assert not result.observation.to_dict()["ready_for_human"]


def test_policy_configuration_revision_mismatch_cannot_reuse_an_old_pass():
    reader = Reader()
    expected = deepcopy(policy()["configuration"])
    expected["revision"] = 2
    reader.configuration_rows = Collection((expected,))
    result = read(reader)
    assert not result.observation.complete
    assert gates(result)["policy_evidence"].state == "unknown"


def test_authoritative_empty_target_roster_is_distinct_from_missing_evidence():
    reader = Reader()
    reader.configuration_rows = Collection()
    reader.policy_rows = Collection()
    result = read(reader)
    assert result.observation.complete and result.checks_state == "none"
    assert result.observation.to_dict()["all_required_satisfied"]


def test_failed_current_build_includes_bounded_native_diagnostics():
    reader = Reader()
    failed = policy(state="rejected")
    failed["context"]["buildId"] = 42
    reader.policy_rows = Collection((failed,))
    result = read(reader)
    assert result.checks_state == "failure"
    assert result.observation.policies[0].failed_tasks[0]["task"] == "Compile"
    assert reader.calls.count("timeline") == 1


def test_moved_head_refuses_mixed_revision_snapshot():
    reader = Reader()
    reader.after = deepcopy(reader.data)
    reader.after["lastMergeSourceCommit"]["commitId"] = "new-head"
    with pytest.raises(ProviderError, match="moved") as error:
        read(reader)
    assert error.value.transient


def test_closed_pr_wakes_without_requiring_unrelated_policy_access():
    reader = Reader()
    reader.data["status"] = "completed"
    result = read(reader)
    assert result.merged
    assert reader.calls == ["pr"]


def test_closed_pr_does_not_need_a_retained_source_head():
    reader = Reader()
    reader.data.update(status="completed", lastMergeSourceCommit=None)
    assert read(reader).merged


def test_new_pr_queued_merge_without_source_metadata_is_retryable():
    reader = Reader()
    reader.data.update(mergeStatus="queued", lastMergeSourceCommit=None)
    with pytest.raises(ProviderError) as error:
        read(reader)
    assert error.value.transient


def test_retarget_without_source_push_does_not_reset_approval_clock():
    rows = [
        {"id": 1, "reason": "push", "sourceRefCommit": {"commitId": HEAD}, "createdDate": POSTED},
        {"id": 2, "reason": "retarget", "sourceRefCommit": {"commitId": HEAD}, "createdDate": APPROVED},
    ]
    assert ado.source_iteration(rows, HEAD) == (2, POSTED)


def test_duplicate_identity_is_unknown_not_an_extra_approval():
    reader = Reader()
    reader.data["reviewers"].append(vote("reviewer"))
    result = read(reader)
    assert gates(result)["minimum_approvals"].state == "unknown"
    assert not result.observation.complete


def test_author_guid_casing_cannot_turn_a_self_vote_into_non_author_approval():
    reader = Reader()
    actor = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    reader.data["createdBy"]["id"] = actor
    reader.data["reviewers"] = [vote(actor.lower())]
    reader.thread_rows = Collection((event(actor=actor),))
    result = read(reader)
    assert gates(result)["minimum_approvals"].state == "blocked"
    assert gates(result)["latest_push_approval"].state == "satisfied"
    assert result.author == actor.lower()


def test_missing_comment_type_is_incomplete_not_no_unresolved_comments():
    reader = Reader()
    unknown = thread()
    del unknown["comments"][0]["commentType"]
    reader.thread_rows = Collection((unknown,))
    result = read(reader)
    assert not result.observation.complete
    assert gates(result)["comments_resolved"].state == "unknown"


def test_native_thread_bodies_are_on_demand_not_persisted_in_watch_evidence():
    from agent_worktrees.providers.azure_discussions import get_report
    reader = Reader()
    discussion = thread()
    discussion["comments"][0]["content"] = "Please fix the boundary condition."
    reader.thread_rows = Collection((event(), discussion))
    report = get_report(AzureDevOpsProvider(), "Project/repository", 7,
                        api_base="https://dev.azure.com/example", token=None,
                        reader_factory=lambda *args: reader)
    assert report["complete"] and len(report["threads"]) == 1
    assert report["threads"][0]["comments"][0]["text"] == "Please fix the boundary condition."
    assert report["threads"][0]["iteration_context"]["secondComparingIteration"] == 1
    assert "text" not in read(reader).observation.to_dict()["discussions"][0]


def test_discussion_read_retains_round_identity_when_source_moves():
    from agent_worktrees.providers.azure_discussions import get_report
    reader = Reader()
    discussion = thread()
    discussion["comments"][0]["content"] = "Review from the earlier comparison."
    reader.thread_rows = Collection((discussion,))
    reader.after = deepcopy(reader.data)
    reader.after["lastMergeSourceCommit"]["commitId"] = "new-head"
    report = get_report(AzureDevOpsProvider(), "Project/repository", 7,
                        api_base="https://dev.azure.com/example", token=None,
                        reader_factory=lambda *args: reader)
    assert report["complete"] and report["candidate_changed"]
    assert report["threads"][0]["iteration"] == 1


class Response(BytesIO):
    status = 200

    def __init__(self, data, continuation=""):
        super().__init__(data)
        self.headers = {"x-ms-continuationtoken": continuation}


def test_native_transport_keeps_real_auth_value_out_of_process_arguments():
    provider = AzureDevOpsProvider()
    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            return Response(b'{"value":[],"count":0}')

    reader = AdoReader(provider, "Project/repository", 7, "https://dev.azure.com/example",
                       "fixture-token", opener=Opener())
    assert reader.threads().complete
    scheme, encoded = seen[0].get_header("Authorization").split(" ", 1)
    assert scheme == "Bas" + "ic"
    assert base64.b64decode(encoded) == b":fixture-token"
    assert seen[0].get_method() == "GET"


@pytest.mark.parametrize("framing", ["length", "chunked", "headers"])
@pytest.mark.parametrize(
    ("slow", "preconnect_delay"),
    [(False, 0), (True, 0), (True, .2)],
    ids=["False", "True", "expired-before-connect"],
)
def test_native_transport_absolute_deadline_bounds_slow_stream(
    monkeypatch, framing, slow, preconnect_delay,
):
    import http.client
    import socketserver
    import ssl
    import threading
    import time
    from urllib.request import ProxyHandler
    from agent_worktrees.providers import azure_observation_reads as reads

    body = b'{"value":[' + b" " * 80 + b"]}"
    received = threading.Event()
    stop = threading.Event()

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            self.request.recv(8192)
            received.set()
            headers = b"HTTP/1.1 200 OK\r\nx-ms-continuationtoken: next\r\n"
            if framing == "chunked":
                headers += b"Transfer-Encoding: chunked\r\n\r\n"
                fragments = [b"1\r\n" + bytes([value]) + b"\r\n" for value in body]
                fragments.append(b"0\r\n\r\n")
            else:
                headers += f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                fragments = [bytes([value]) for value in body]
            if framing == "headers":
                fragments = [bytes([value]) for value in headers] + [body]
            else:
                fragments.insert(0, headers)
            try:
                for fragment in fragments:
                    self.request.sendall(fragment)
                    if slow and stop.wait(.01):
                        return
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

    # Keep the production HTTPS opener/HTTP parser, but use a plaintext loopback
    # fixture so this deadline test needs neither external TLS nor certificates.
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", http.client.HTTPConnection.connect)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    initialize = reads.HTTPSHandler.__init__

    def initialize_fixture_context(self):
        initialize(self, context=context)

    monkeypatch.setattr(reads.HTTPSHandler, "__init__", initialize_fixture_context)
    build = reads.build_opener

    def fixture_opener(*handlers):
        if preconnect_delay:
            time.sleep(preconnect_delay)
        return build(ProxyHandler({}), *handlers)

    monkeypatch.setattr(reads, "build_opener", fixture_opener)
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 2
        worker = threading.Thread(target=server.handle_request)
        worker.start()
        try:
            reader = AdoReader(
                AzureDevOpsProvider(), "Project/repository", 7,
                f"https://127.0.0.1:{server.server_address[1]}", "fixture-token",
                budget=.15 if slow else 2.0,
            )
            started = time.monotonic()
            if slow:
                with pytest.raises(ProviderError, match="budget exhausted"):
                    reader.json(reader.base)
                assert time.monotonic() - started < .75
            else:
                assert reader.json(reader.base) == ({"value": []}, "next")
        finally:
            stop.set()
            worker.join(timeout=2)
            assert not worker.is_alive()
        assert received.is_set() is not bool(preconnect_delay), "unexpected request-arrival state"


def test_native_deadline_checks_request_arrival_after_worker_join(monkeypatch):
    import threading

    event = threading.Event
    events = []

    def fixture_event():
        value = event()
        events.append(value)
        return value

    class DeferredWorker:
        def __init__(self, *, target):
            pass

        def start(self):
            pass

        def join(self, *, timeout):
            assert timeout == 2
            events[0].set()

        def is_alive(self):
            return False

    class ExpiredReader:
        base = "https://fixture"

        def __init__(self, *args, **kwargs):
            pass

        def json(self, url):
            raise ProviderError("budget exhausted")

    monkeypatch.setattr(threading, "Event", fixture_event)
    monkeypatch.setattr(threading, "Thread", DeferredWorker)
    monkeypatch.setitem(globals(), "AdoReader", ExpiredReader)
    with pytest.raises(AssertionError, match="request-arrival"):
        test_native_transport_absolute_deadline_bounds_slow_stream(
            monkeypatch, "length", True, .2,
        )


def test_native_policy_skip_paging_and_repeated_page_refusal():
    provider = AzureDevOpsProvider()
    provider._auth_header = lambda token: ("Authorization: fixture", "")
    reader = AdoReader(provider, "Project/repository", 7, "https://dev.azure.com/example", None)
    calls = []

    def pages(url, query=None):
        calls.append(query)
        values = [{"id": i} for i in range(100)] if query["$skip"] == 0 else [{"id": 100}]
        return {"value": values, "count": len(values)}, ""

    reader.json = pages
    result = reader.evaluations("project", 7)
    assert result.complete and len(result.records) == 101
    assert [call["$skip"] for call in calls] == [0, 100]
    reader.json = lambda url, query=None: ({"value": [{"id": i} for i in range(100)]}, "")
    assert not reader.evaluations("project", 7).complete


def test_target_policy_roster_uses_documented_scope_and_continuation():
    provider = AzureDevOpsProvider()
    provider._auth_header = lambda token: ("Authorization: fixture", "")
    reader = AdoReader(provider, "Project/repository", 7, "https://dev.azure.com/example", None)
    calls = []

    def pages(url, query=None):
        calls.append((url, query))
        if not query.get("continuationToken"):
            return {"value": [{"id": 1}], "count": 1}, "next"
        return {"value": [{"id": 2}], "count": 1}, ""

    reader.json = pages
    result = reader.configurations("repository-id", "refs/heads/main")
    assert result.complete and len(result.records) == 2
    assert "repositoryId=repository-id" in calls[0][0]
    assert calls[1][1]["continuationToken"] == "next"


def test_continuation_cannot_be_mistaken_for_a_complete_empty_result():
    provider = AzureDevOpsProvider()
    provider._auth_header = lambda token: ("Authorization: fixture", "")
    reader = AdoReader(provider, "Project/repository", 7, "https://dev.azure.com/example", None)
    reader.json = lambda *args, **kwargs: ({"value": []}, "unhandled")
    result = reader.threads()
    assert not result.complete and "continuation" in result.error


def test_http_failure_is_explicit_and_never_contains_raw_response_body():
    provider = AzureDevOpsProvider()
    provider._auth_header = lambda token: ("Authorization: fixture", "")

    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 403, "Forbidden", {}, BytesIO(b"private-body"))

    reader = AdoReader(provider, "Project/repository", 7, "https://dev.azure.com/example",
                       None, opener=Opener())
    result = reader.threads()
    assert not result.complete and "403" in result.error
    assert "private-body" not in result.error
