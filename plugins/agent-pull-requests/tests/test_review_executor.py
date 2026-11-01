"""Real durable store and synthetic existing protected transport; zero live effects."""

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import time

import pytest

from agent_pull_requests.review_executor import execute_review
from agent_pull_requests.review_workspace import _digest, build_generation
from agent_pull_requests.watch_replay_store import ReplayStore


@pytest.fixture
def environment(tmp_path, monkeypatch, request):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-worktrees" / "src"))
    from agent_worktrees.providers.azure_owned_review import AdoReviewContext, NativeReviewResponse, ReviewContextUnavailable

    evidence = json.loads((root / "plugins" / "agent-pull-requests" / "docs" /
                           "review-evidence.example.json").read_text())
    evidence["sections"]["discussions"] = {
        "observed_from": "2026-10-30T00:00:03Z", "observed_to": "2026-10-30T00:00:04Z",
        "data": [], "complete": True, "errors": [],
    }
    evidence["sections"]["changes"]["data"] = []
    generation = build_generation(evidence)["generation"]
    scope = generation["scope"]
    source = {"schema": "agent-pull-requests.local-source", "version": 1, "scope": copy.deepcopy(scope),
              "revision": copy.deepcopy(generation["revision"]), "complete": True, "errors": [],
              "files": [], "workspace_id": "synthetic-existing-source"}
    source["id"] = _digest(source)
    attachment = {"state": "ready", "scope": copy.deepcopy(scope), "generation_id": generation["id"], "source": source}
    selected = {
        "schema": "agent-pull-requests.review-intent", "version": 1, "operation_id": "selected-operation",
        "scope": copy.deepcopy(scope), "generation_id": generation["id"], "source_id": source["id"],
        "revision": {key: generation["revision"][key] for key in ("head_sha", "common_sha", "iteration")},
        "actions": [{"id": "finding", "kind": "comment", "body": "Synthetic selected finding"},
                    {"id": "verdict", "kind": "verdict", "verdict": "APPROVED"}],
    }
    from agent_pull_requests.watch_replay_store import ReplayServiceIdentity
    store = ReplayStore(
        tmp_path.resolve(), "existing-owner",
        service_identity=ReplayServiceIdentity("fleet-a", "gateway-a", "service-a")
        if getattr(request, "param", None) else None,
    )
    store.provision()
    store.adopt_review_operations(lifecycle_checkpoint=lambda: None, expires_at=int(time.time()) + 600)

    class Credential:
        pass

    class Backend:
        credential = Credential()
        observer = None
        denied = None
        lose_reply = False
        drift_after_comment = False
        on_policy = None
        before_dispatch = None
        after_write = None
        partial_reply = False
        mismatched_reply = False
        require_policy = False
        policy_failed = False
        emit_vote_thread = False
        vote_reply = "complete"
        vote_thread_actor = None
        vote_identity_mode = "legacy"
        system_comment_type = 2
        malformed_author_reply = False
        singleton_continuation = None
        response_ids = None

        def __init__(self):
            self.pr = copy.deepcopy(evidence["sections"]["pull_request"]["data"])
            self.iterations = copy.deepcopy(evidence["sections"]["iterations"]["data"])
            self.threads = []
            self.calls = []
            self.other_reviewer = {"id": "other-reviewer", "vote": 5}
            self.policy_calls = []
            self.vote = None

        def checkpoint(self):
            if self.denied is not None:
                raise self.denied

        def review_check(self, operation, action, current, *, context):
            self.policy_calls.append(action["id"])
            if self.require_policy:
                from agent_pull_requests.review_executor import ReviewCandidateChanged
                inputs = context.read_decision_inputs()
                if not inputs["policy_evaluations"]["complete"] or any(
                    row.get("status") == "failed" for row in inputs["policy_evaluations"]["data"]
                ):
                    raise ReviewCandidateChanged("existing caller policy requires a different selection")
            if self.on_policy is not None:
                self.on_policy(self, action)

        def request(self, method, url, body, *, query, expected_binding, remaining):
            self.checkpoint()
            if method != "GET" and self.before_dispatch is not None:
                self.before_dispatch(self)
            used = self.observer if self.observer is not None and "connectionData" in url else self.credential
            if used is not expected_binding:
                raise ReviewContextUnavailable("existing transport detected changed actual material")
            self.calls.append((method, url, copy.deepcopy(body), expected_binding, remaining))
            if method == "GET":
                if "connectionData" in url:
                    return NativeReviewResponse(200, {"authorizedUser": {"id": scope["principal_id"]},
                                                     "authenticatedUser": {"id": "different-delegated-user"}},
                                                "unexpected" if self.singleton_continuation == "identity" else "")
                if "/iterations?" in url:
                    return NativeReviewResponse(200, {"value": copy.deepcopy(self.iterations)})
                if "/threads?" in url:
                    return NativeReviewResponse(200, {"value": copy.deepcopy(self.threads)})
                if "/statuses?" in url or "/policy/evaluations?" in url:
                    values = [{"id": "synthetic-evaluation", "status": "failed"}] if self.policy_failed else []
                    return NativeReviewResponse(200, {"value": values})
                return NativeReviewResponse(200, copy.deepcopy(self.pr),
                                            "unexpected" if self.singleton_continuation == "pr" else "")
            retained = store.inspect_review(selected["operation_id"], scope, expires_at=int(time.time()) + 600)
            action = next(row for row in retained["actions"] if row["state"] == "unknown")
            assert action["attempt_id"], "backend entered before durable in-flight persistence"
            if method == "PUT":
                self.vote = body["vote"]
                if self.emit_vote_thread:
                    actor = self.vote_thread_actor or scope["principal_id"]
                    event = {
                        "id": 200 + len(self.threads), "status": 1,
                        "publishedDate": "2026-10-30T00:01:00Z",
                        "properties": {
                            "CodeReviewThreadType": {"$value": "VoteUpdate"},
                            "CodeReviewVoteResult": {"$value": str(self.vote)},
                            "CodeReviewVotedByTfId": {"$value": actor},
                        },
                        "comments": [{"id": 1, "commentType": self.system_comment_type,
                                      "content": "Synthetic vote system event"}],
                    }
                    if self.vote_identity_mode in ("indexed", "conflict"):
                        event["properties"]["CodeReviewVotedByIdentity"] = {"$value": "voter"}
                        event["identities"] = {"voter": {"id": actor if self.vote_identity_mode == "indexed" else "conflicting-actor"}}
                        if self.vote_identity_mode == "indexed":
                            del event["properties"]["CodeReviewVotedByTfId"]
                    self.threads.append(event)
                if self.after_write is not None:
                    self.after_write(self)
                if self.vote_reply == "missing":
                    return NativeReviewResponse(200, {"id": scope["principal_id"]})
                if self.vote_reply == "contradictory":
                    return NativeReviewResponse(200, {"id": scope["principal_id"], "vote": 0})
                return NativeReviewResponse(200, {"id": scope["principal_id"], "vote": self.vote})
            if method == "POST" and "/threads?" in url:
                thread = {"id": 100 + len(self.threads), "status": 1, "properties": body["properties"],
                          "comments": [{"id": 1, "parentCommentId": 0, "commentType": 1,
                                        "content": body["comments"][0]["content"],
                                        "author": {"id": scope["principal_id"]}}]}
                self.threads.append(thread)
                if self.drift_after_comment:
                    self.threads.append({"id": 999, "status": 1, "comments": [
                        {"id": 1, "content": "Unrelated author reply", "author": {"id": "other-author"}}]})
                if self.lose_reply:
                    raise OSError("synthetic reply lost after acceptance")
                if self.after_write is not None:
                    self.after_write(self)
                if self.partial_reply:
                    return NativeReviewResponse(201, {"id": thread["id"]})
                if self.response_ids is not None:
                    reply = copy.deepcopy(thread)
                    reply["id"], reply["comments"][0]["id"] = self.response_ids
                    return NativeReviewResponse(201, reply)
                if self.mismatched_reply:
                    mismatched = copy.deepcopy(thread)
                    mismatched["comments"][0]["content"] = "Different response content"
                    return NativeReviewResponse(201, mismatched)
                if self.malformed_author_reply:
                    malformed = copy.deepcopy(thread)
                    malformed["comments"][0]["author"] = "not-an-author-object"
                    return NativeReviewResponse(201, malformed)
                return NativeReviewResponse(201, copy.deepcopy(thread))
            thread_id = int(re.search(r"/threads/(\d+)/", url)[1])
            thread = next(row for row in self.threads if row["id"] == thread_id)
            if method == "PATCH":
                comment_id = int(re.search(r"/comments/(\d+)", url)[1])
                comment = next(row for row in thread["comments"] if row["id"] == comment_id)
                comment["content"] = body["content"]
            else:
                comment = {"id": len(thread["comments"]) + 1, "parentCommentId": 0, "commentType": 1,
                           "content": body["content"], "author": {"id": scope["principal_id"]}}
                thread["comments"].append(comment)
            reply = copy.deepcopy(comment)
            if self.malformed_author_reply:
                reply["author"] = "not-an-author-object"
            return NativeReviewResponse(200, reply)

        @contextmanager
        def context(self):
            yield AdoReviewContext(
                scope, request=self.request, credential_binding=lambda: self.credential,
                checkpoint=self.checkpoint, review_check=self.review_check,
            )

    return store, selected, generation, attachment, Backend(), Credential


def run(environment):
    store, selected, generation, attachment, backend, _ = environment
    return execute_review(store, selected, generation, attachment, context_factory=backend.context,
                          checkpoint=backend.checkpoint, expires_at=int(time.time()) + 600, budget_seconds=120)


def writes(backend):
    return [call for call in backend.calls if call[0] != "GET"]


@pytest.mark.parametrize("endpoint", ["identity", "pr"])
@pytest.mark.parametrize("after_effect", [False, True])
def test_singleton_continuation_retains_structured_action_state(environment, endpoint, after_effect):
    _, _, _, _, backend, _ = environment
    if after_effect:
        backend.after_write = lambda value: setattr(value, "singleton_continuation", endpoint)
    else:
        backend.singleton_continuation = endpoint
    result = run(environment)
    assert result["status"] == "unknown"
    assert result["actions"][0]["state"] == ("confirmed" if after_effect else "unattempted")
    assert len(writes(backend)) == int(after_effect)
    assert backend.vote is None


@pytest.mark.parametrize("ids,retained", [
    ((100, 0), {"thread_id": 100}),
    ((False, 1), {"comment_id": 1}),
    ((100, 2**63), {"thread_id": 100}),
])
def test_invalid_response_id_does_not_discard_other_native_id(environment, ids, retained):
    _, _, _, _, backend, _ = environment
    backend.response_ids = ids
    result = run(environment)
    assert result["status"] == "unknown"
    assert result["actions"][0]["state"] == "unknown"
    assert result["actions"][0]["receipt"]["platform_ids"] == retained
    assert len(writes(backend)) == 1 and backend.vote is None


@pytest.mark.parametrize("target,value", [("thread", None), ("comment", None), ("thread", 0), ("comment", True)])
def test_marked_readback_without_valid_native_ids_retains_unknown(environment, target, value):
    _, _, _, _, backend, _ = environment
    backend.lose_reply = True
    assert run(environment)["status"] == "unknown"
    backend.lose_reply = False
    record = backend.threads[0] if target == "thread" else backend.threads[0]["comments"][0]
    if value is None:
        del record["id"]
    else:
        record["id"] = value
    result = run(environment)
    assert result["status"] == "unknown" and result["actions"][0]["state"] == "unknown"
    assert len(writes(backend)) == 1 and backend.vote is None


@pytest.mark.parametrize("shape", ["duplicate", "source", "common", "metadata"])
@pytest.mark.parametrize("after_effect", [False, True])
def test_malformed_or_duplicate_iteration_evidence_retains_action_states(environment, shape, after_effect):
    _, _, _, _, backend, _ = environment

    def corrupt(value):
        latest = max(value.iterations, key=lambda row: row["id"])
        if shape == "duplicate":
            conflict = copy.deepcopy(latest)
            conflict["sourceRefCommit"]["commitId"] = "f" * 40
            value.iterations.append(conflict)
        elif shape == "metadata":
            value.pr["lastMergeSourceCommit"] = "not-an-object"
        else:
            latest["sourceRefCommit" if shape == "source" else "commonRefCommit"] = "not-an-object"

    if after_effect:
        backend.after_write = corrupt
    else:
        corrupt(backend)
    result = run(environment)
    assert result["status"] == "unknown"
    assert result["actions"][0]["state"] == ("confirmed" if after_effect else "unattempted")
    assert len(writes(backend)) == int(after_effect) and backend.vote is None


@pytest.mark.parametrize("field", ["threadContext", "pullRequestThreadContext", "properties", "parentCommentId", "commentType"])
def test_unrelated_metadata_on_confirmed_new_thread_cannot_advance_selection(environment, field):
    _, _, _, _, backend, _ = environment

    def drift(value):
        thread = value.threads[0]
        if field in ("parentCommentId", "commentType"):
            thread["comments"][0][field] = 2
        else:
            thread[field] = (
                {"CodeReviewThreadType": {"$value": "UnrelatedType"}}
                if field == "properties" else {"filePath": "/unselected.txt"}
            )

    backend.after_write = drift
    result = run(environment)
    assert result["status"] == "candidate_changed"
    assert result["actions"][0]["state"] == "confirmed"
    assert result["actions"][1]["state"] == "unattempted"
    assert len(writes(backend)) == 1 and backend.vote is None
    backend.after_write = None
    assert run(environment)["status"] == "candidate_changed"
    assert len(writes(backend)) == 1


def test_real_store_and_bound_transport_complete_without_completion_or_other_reviewer_changes(environment):
    store, selected, generation, attachment, backend, _ = environment
    original = copy.deepcopy((generation, attachment, backend.pr, backend.other_reviewer))
    result = run(environment)
    assert result["status"] == "complete", result
    assert len(writes(backend)) == 2 and backend.vote == 10
    assert all(row["state"] == "confirmed" for row in result["actions"])
    assert (generation, attachment, backend.pr, backend.other_reviewer) == original
    assert not generation["complete"]  # Incomplete policy facts do not invent a merge gate.
    assert writes(backend)[-1][2] == {"vote": 10}
    assert all(call[3] is backend.credential for call in backend.calls)
    assert run(environment)["status"] == "complete"
    assert len(writes(backend)) == 2


@pytest.mark.parametrize("drift", ["head", "discussion", "observer_material", "renewal"])
def test_pre_send_source_discussion_and_actual_write_binding_drift_never_votes(environment, drift):
    _, _, _, _, backend, Credential = environment
    if drift == "head":
        backend.pr["lastMergeSourceCommit"]["commitId"] = "c" * 40
    elif drift == "discussion":
        backend.threads.append({"id": 50, "status": 1, "comments": [{"id": 1, "content": "new reply"}]})
    elif drift == "observer_material":
        backend.observer = Credential()
    else:
        backend.on_policy = lambda value, action: setattr(value, "credential", Credential())
    result = run(environment)
    assert result["status"] != "complete"
    assert writes(backend) == []


def test_lost_native_comment_reply_is_attributed_read_only_without_reposting(environment):
    _, _, _, _, backend, _ = environment
    backend.lose_reply = True
    first = run(environment)
    assert first["status"] == "unknown" and first["actions"][0]["state"] == "unknown"
    backend.lose_reply = False
    second = run(environment)
    assert second["status"] == "complete", second
    assert len([call for call in writes(backend) if call[0] == "POST"]) == 1
    assert backend.vote == 10


@pytest.mark.parametrize("action", ["finding", "verdict"])
def test_unknown_vote_value_or_unmarked_matching_content_never_confirms_or_replays(environment, action):
    store, selected, _, _, backend, _ = environment
    if action == "verdict":
        selected["actions"].reverse()
        backend.vote = 10
    store.admit_review(selected, expires_at=int(time.time()) + 600)
    store.begin_review_action(selected["operation_id"], selected["scope"], action, expires_at=int(time.time()) + 600)
    if action == "finding":
        backend.threads.append({"id": 100, "status": 1, "comments": [
            {"id": 1, "content": selected["actions"][0]["body"], "author": {"id": selected["scope"]["principal_id"]}}]})
    result = run(environment)
    assert result["status"] == "unknown"
    assert writes(backend) == []


def test_unrelated_reply_after_known_effect_preserves_receipt_and_stops_verdict(environment):
    _, _, _, _, backend, _ = environment
    backend.drift_after_comment = True
    result = run(environment)
    assert result["status"] == "candidate_changed"
    assert result["actions"][0]["state"] == "confirmed"
    assert result["actions"][1]["state"] == "unattempted"
    assert len(writes(backend)) == 1 and backend.vote is None


def test_existing_owner_denial_is_original_and_does_not_create_effect(environment):
    _, _, _, _, backend, _ = environment
    denied = PermissionError("existing owner cancelled")
    backend.denied = denied
    with pytest.raises(PermissionError) as error:
        run(environment)
    assert error.value is denied and writes(backend) == []


def test_existing_transport_refuses_credential_change_at_actual_dispatch(environment):
    _, _, _, _, backend, Credential = environment
    backend.before_dispatch = lambda value: setattr(value, "credential", Credential())
    result = run(environment)
    assert result["status"] == "unknown"
    assert result["actions"][0]["state"] == "unknown"
    assert writes(backend) == []


@pytest.mark.parametrize("error_type", [PermissionError, RuntimeError])
def test_owner_loss_after_backend_acceptance_keeps_durable_unknown_and_no_following_vote(environment, error_type):
    store, selected, _, _, backend, _ = environment
    denied = error_type("existing owner lost/cancelled after acceptance")
    backend.after_write = lambda value: setattr(value, "denied", denied)
    with pytest.raises(error_type) as error:
        run(environment)
    assert error.value is denied and len(backend.threads) == 1 and backend.vote is None
    state = store.inspect_review(selected["operation_id"], selected["scope"], expires_at=int(time.time()) + 600)
    assert state["actions"][0]["state"] == "unknown" and state["actions"][1]["state"] == "unattempted"


def test_partial_native_reply_retains_ids_and_is_never_automatically_reposted(environment):
    store, selected, _, _, backend, _ = environment
    backend.partial_reply = True
    result = run(environment)
    assert result["status"] == "unknown"
    assert result["actions"][0]["receipt"]["platform_ids"] == {"thread_id": 100}
    assert backend.vote is None and len(writes(backend)) == 1
    backend.partial_reply = False
    assert run(environment)["status"] == "complete"
    assert len([call for call in writes(backend) if call[0] == "POST"]) == 1


@pytest.mark.parametrize("kind,owned", [("reply", True), ("edit_owned_comment", True), ("edit_owned_comment", False)])
def test_selected_reply_and_edit_apply_only_to_fresh_native_owned_target(environment, kind, owned):
    from agent_pull_requests.review_workspace import _record
    _, selected, generation, attachment, backend, _ = environment
    principal = selected["scope"]["principal_id"]
    thread = {"id": 50, "status": 1, "comments": [
        {"id": 1, "parentCommentId": 0, "commentType": 1, "content": "Reviewed original",
         "author": {"id": principal if owned else "other-author"}}]}
    backend.threads = [copy.deepcopy(thread)]
    generation["sections"]["discussions"]["data"] = [_record("discussions", thread)]
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    selected["generation_id"] = generation["id"]
    attachment["generation_id"] = generation["id"]
    action = {"id": "finding", "kind": kind, "thread_id": 50, "body": "Selected update"}
    if kind == "edit_owned_comment":
        action.update(comment_id=1, expected_content_id=_digest("Reviewed original"))
    selected["actions"][0] = action
    result = run(environment)
    assert result["status"] == ("complete" if owned else "candidate_changed"), result
    if owned:
        assert backend.vote == 10 and len(writes(backend)) == 2
        assert run(environment)["status"] == "complete" and len(writes(backend)) == 2
    else:
        assert writes(backend) == [] and backend.threads == [thread]


def test_unverified_native_response_retains_complete_ids_without_confirming(environment):
    _, _, _, _, backend, _ = environment
    backend.mismatched_reply = True
    result = run(environment)
    assert result["status"] == "unknown"
    assert result["actions"][0]["state"] == "unknown"
    assert result["actions"][0]["receipt"]["platform_ids"] == {"thread_id": 100, "comment_id": 1}
    assert backend.vote is None


@pytest.mark.parametrize("kind", ["reply", "edit_owned_comment"])
def test_multiple_confirmed_own_updates_advance_only_their_expected_thread_state(environment, kind):
    from agent_pull_requests.review_workspace import _record
    _, selected, generation, attachment, backend, _ = environment
    principal = selected["scope"]["principal_id"]
    thread = {"id": 50, "status": 1, "comments": [
        {"id": 1, "parentCommentId": 0, "commentType": 1, "content": "Original",
         "author": {"id": principal}}]}
    backend.threads = [copy.deepcopy(thread)]
    generation["sections"]["discussions"]["data"] = [_record("discussions", thread)]
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    selected["generation_id"] = attachment["generation_id"] = generation["id"]
    actions = []
    for index, body in enumerate(("First selected update", "Second selected update")):
        action = {"id": f"update-{index}", "kind": kind, "thread_id": 50, "body": body}
        if kind == "edit_owned_comment":
            action.update(comment_id=1, expected_content_id=_digest("Original" if index == 0 else actions[0]["body"]))
        actions.append(action)
    selected["actions"] = [*actions, selected["actions"][-1]]
    result = run(environment)
    assert result["status"] == "complete", result
    assert backend.vote == 10 and len(writes(backend)) == 3
    assert run(environment)["status"] == "complete" and len(writes(backend)) == 3


def test_pre_send_store_failure_never_enters_mutation_transport(environment, monkeypatch):
    from agent_pull_requests import watch_replay_store as storage
    store, selected, _, _, backend, _ = environment
    original = storage._atomic_write_json

    def fail(path, document):
        if document.get("reviews") and document["reviews"][0]["actions"][0]["state"] == "unknown":
            raise OSError("synthetic durable-fence failure")
        original(path, document)

    monkeypatch.setattr(storage, "_atomic_write_json", fail)
    result = run(environment)
    assert result["status"] == "unknown" and writes(backend) == []
    assert store.inspect_review(selected["operation_id"], selected["scope"],
                                expires_at=int(time.time()) + 600)["actions"][0]["state"] == "unattempted"


def test_full_receipt_reservation_refuses_before_any_backend_context(environment):
    _, _, _, _, backend, _ = environment
    def unavailable_context():
        raise AssertionError("backend opened after rejected data admission")
    store, selected, generation, attachment, _, _ = environment
    document = json.loads(store.path.read_text())
    document["capacities"]["review_bytes"] = 1024
    store.path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="capacity"):
        execute_review(store, selected, generation, attachment, context_factory=unavailable_context,
                       checkpoint=backend.checkpoint, expires_at=int(time.time()) + 600, budget_seconds=120)
    assert writes(backend) == []


def test_existing_caller_policy_refreshes_actual_evaluations_through_the_same_bound_context(environment):
    _, _, _, _, backend, _ = environment
    backend.require_policy = backend.policy_failed = True
    result = run(environment)
    assert result["status"] == "candidate_changed" and writes(backend) == []
    evaluations = [call for call in backend.calls if "/policy/evaluations?" in call[1]]
    assert evaluations and all(call[3] is backend.credential for call in evaluations)


def test_ambiguous_native_marker_matches_remain_unknown_without_new_writes(environment):
    _, _, _, _, backend, _ = environment
    backend.lose_reply = True
    assert run(environment)["status"] == "unknown"
    duplicate = copy.deepcopy(backend.threads[0])
    duplicate["id"] += 1
    backend.threads.append(duplicate)
    backend.lose_reply = False
    before = len(writes(backend))
    result = run(environment)
    assert result["status"] == "unknown" and len(writes(backend)) == before


@pytest.mark.parametrize("vote_first", [False, True])
def test_late_native_reply_does_not_report_success_or_repeat_a_write(environment, vote_first):
    store, selected, generation, attachment, backend, _ = environment
    now = [1.0]
    if vote_first:
        selected["actions"].reverse()
    backend.after_write = lambda value: now.__setitem__(0, 200.0)
    result = execute_review(store, selected, generation, attachment, context_factory=backend.context,
                            checkpoint=backend.checkpoint, expires_at=int(time.time()) + 600,
                            budget_seconds=120, clock=lambda: now[0])
    assert result["status"] == "unknown"
    assert result["actions"][0]["state"] == "unknown"
    expected = {"reviewer_id": selected["scope"]["principal_id"]} if vote_first else {"thread_id": 100, "comment_id": 1}
    assert result["actions"][0]["receipt"]["platform_ids"] == expected
    assert len(writes(backend)) == 1


@pytest.mark.parametrize("other_actor", [False, True])
def test_only_confirmed_own_vote_system_events_advance_expected_discussions(environment, other_actor):
    _, selected, _, _, backend, _ = environment
    selected["actions"].reverse()
    backend.emit_vote_thread = True
    if other_actor:
        backend.vote_thread_actor = "another-native-reviewer"
    result = run(environment)
    assert result["status"] == ("candidate_changed" if other_actor else "complete"), result
    assert result["actions"][0]["state"] == "confirmed"
    assert len(writes(backend)) == (1 if other_actor else 2)
    if not other_actor:
        assert run(environment)["status"] == "complete" and len(writes(backend)) == 2


@pytest.mark.parametrize("reply", ["missing", "contradictory"])
def test_unverified_vote_reply_keeps_native_reviewer_id_without_value_based_confirmation(environment, reply):
    _, selected, _, _, backend, _ = environment
    selected["actions"].reverse()
    backend.emit_vote_thread = True
    backend.vote_reply = reply
    result = run(environment)
    assert result["status"] == "unknown"
    assert result["actions"][0]["receipt"]["platform_ids"] == {"reviewer_id": selected["scope"]["principal_id"]}
    assert result["actions"][0]["state"] == "unknown"
    assert backend.vote == 10
    count = len(writes(backend))
    assert run(environment)["status"] == "unknown" and len(writes(backend)) == count


@pytest.mark.parametrize("identity_mode,system_type", [("indexed", 2), ("indexed", "system"), ("conflict", 2)])
def test_native_indexed_vote_identity_and_system_enum_are_resolved_before_normalization(environment, identity_mode, system_type):
    _, selected, _, _, backend, _ = environment
    selected["actions"].reverse()
    backend.emit_vote_thread = True
    backend.vote_identity_mode, backend.system_comment_type = identity_mode, system_type
    result = run(environment)
    assert result["status"] == ("unknown" if identity_mode == "conflict" else "complete"), result
    assert len(writes(backend)) == (1 if identity_mode == "conflict" else 2)


@pytest.mark.parametrize("kind", ["comment", "reply", "edit_owned_comment"])
def test_malformed_native_author_retains_known_ids_as_unconfirmed(environment, kind):
    from agent_pull_requests.review_workspace import _record
    _, selected, generation, attachment, backend, _ = environment
    if kind != "comment":
        thread = {"id": 50, "status": 1, "comments": [
            {"id": 1, "content": "Original", "parentCommentId": 0, "commentType": 1,
             "author": {"id": selected["scope"]["principal_id"]}}]}
        backend.threads = [copy.deepcopy(thread)]
        generation["sections"]["discussions"]["data"] = [_record("discussions", thread)]
        generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
        selected["generation_id"] = attachment["generation_id"] = generation["id"]
        action = {"id": "finding", "kind": kind, "thread_id": 50, "body": "Selected update"}
        if kind == "edit_owned_comment":
            action.update(comment_id=1, expected_content_id=_digest("Original"))
        selected["actions"][0] = action
    backend.malformed_author_reply = True
    result = run(environment)
    assert result["status"] == "unknown"
    expected = {"thread_id": 100 if kind == "comment" else 50,
                "comment_id": 2 if kind == "reply" else 1}
    assert result["actions"][0]["receipt"]["platform_ids"] == expected
    assert backend.vote is None
