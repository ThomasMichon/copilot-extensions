"""Synthetic ADO contract controls; no forge, credentials or runtime installation."""

import copy
import json

import pytest

from agent_pull_requests import review_workspace as workspace
from agent_pull_requests import review_workspace_cli as cli
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.review_workspace_cli import main


HEAD = "a" * 40
COMMON = "b" * 40
TARGET = "c" * 40


def section(data, **extra):
    return {
        "data": data, "complete": True, "errors": [],
        "observed_from": "2026-10-30T00:00:00Z",
        "observed_to": "2026-10-30T00:00:01Z", **extra,
    }


@pytest.fixture
def evidence():
    return {
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": {
            "provider": "azure-devops", "endpoint": "https://dev.azure.com/example",
            "project_id": "project-id", "repository_id": "repository-id",
            "principal_id": "reviewer-id", "number": 42,
        },
        "sections": {
            "pull_request": section({
                "pullRequestId": 42,
                "repository": {"id": "repository-id", "project": {"id": "project-id"}},
                "title": "Synthetic change", "description": "Review this change",
                "createdBy": {"id": "author-id"}, "status": "active", "isDraft": False,
                "sourceRefName": "refs/heads/topic", "targetRefName": "refs/heads/main",
                "lastMergeSourceCommit": {"commitId": HEAD},
                "lastMergeTargetCommit": {"commitId": TARGET},
                "autoCompleteSetBy": None,
                "completionOptions": {"deleteSourceBranch": False, "mergeStrategy": "squash"},
            }),
            "iterations": section([{
                "id": 3, "sourceRefCommit": {"commitId": HEAD},
                "targetRefCommit": {"commitId": TARGET},
                "commonRefCommit": {"commitId": COMMON},
            }]),
            "changes": section([{
                "changeTrackingId": 7, "changeType": "rename", "originalPath": "/before.txt",
                "item": {"path": "/after.txt", "objectId": "d" * 40, "gitObjectType": "blob"},
            }], revision={"head_sha": HEAD, "iteration": 3, "common_sha": COMMON}),
            "discussions": section([{
                "id": 8, "status": "closed",
                "pullRequestThreadContext": {
                    "iterationContext": {"firstComparingIteration": 3, "secondComparingIteration": 3},
                    "changeTrackingId": 7,
                },
                "comments": [
                    {"id": 1, "content": "Question", "author": {"id": "reviewer-id"}},
                    {"id": 2, "parentCommentId": 1, "content": "Reply",
                     "author": {"id": "author-id"}, "isDeleted": False},
                ],
            }]),
            "reviewers": section([{
                "id": "reviewer-id", "vote": 0, "isRequired": True, "isContainer": False,
            }]),
            "policies": section([{"evaluationId": "policy-id", "status": "queued"}]),
        },
    }


def test_initial_load_preserves_presence_revision_and_no_effects(evidence):
    original = copy.deepcopy(evidence)
    result = build_generation(evidence)
    generation = result["generation"]
    pull = generation["sections"]["pull_request"]["data"]
    assert pull["autoCompleteSetBy"] is None
    assert pull["completionOptions"]["deleteSourceBranch"] is False
    assert "completionQueueTime" not in pull
    assert generation["sections"]["reviewers"]["data"][0]["vote"] == 0
    assert generation["revision"] == {
        "head_sha": HEAD, "iteration": 3, "common_sha": COMMON,
        "iteration_head_sha": HEAD,
        "iteration_target_sha": TARGET, "target_sha": TARGET,
    }
    assert generation["complete"] is True
    assert generation["source_attachment"] == {"state": "unsupported", "head_sha": HEAD}
    assert result["effects"] == "none"
    assert evidence == original


def test_observation_only_refresh_is_not_a_material_delta(evidence):
    before = build_generation(evidence)["generation"]
    for data in evidence["sections"].values():
        data["observed_from"] = "2026-10-30T00:01:00Z"
        data["observed_to"] = "2026-10-30T00:01:01Z"
    result = build_generation(evidence, before)
    assert result["generation"]["id"] != before["id"]
    assert result["changed_sections"] == []
    assert result["invalidations"] == []


@pytest.mark.parametrize("change", ["reply", "edit", "delete", "status"])
def test_discussion_changes_without_source_push(evidence, change):
    before = build_generation(evidence)["generation"]
    thread = evidence["sections"]["discussions"]["data"][0]
    if change == "reply":
        thread["comments"].append({"id": 3, "content": "Follow-up"})
    elif change == "edit":
        thread["comments"][1]["content"] = "Edited reply"
    elif change == "delete":
        thread["comments"][1].pop("content")
        thread["comments"][1]["isDeleted"] = True
    else:
        thread["status"] = "active"
    result = build_generation(evidence, before)
    assert result["changed_sections"] == ["discussions"]
    assert result["invalidations"] == ["discussion_decisions", "review_decision"]
    assert result["generation"]["revision"] == before["revision"]


@pytest.mark.parametrize("same_head", [False, True])
def test_push_invalidates_anchors_and_does_not_relabel_old_changes(evidence, same_head):
    before = build_generation(evidence)["generation"]
    new_head = HEAD if same_head else "e" * 40
    evidence["sections"]["pull_request"]["data"]["lastMergeSourceCommit"]["commitId"] = new_head
    evidence["sections"]["iterations"]["data"].append({
        "id": 4, "sourceRefCommit": {"commitId": new_head},
        "targetRefCommit": {"commitId": TARGET}, "commonRefCommit": {"commitId": COMMON},
    })
    result = build_generation(evidence, before)
    generation = result["generation"]
    assert {"source_analysis", "inline_anchors"} <= set(result["invalidations"])
    assert generation["revision"]["iteration"] == 4
    assert generation["source_attachment"]["head_sha"] == new_head
    assert generation["sections"]["changes"]["complete"] is False
    assert generation["sections"]["changes"]["errors"] == ["revision_mismatch"]
    assert generation["sections"]["changes"]["data"]
    assert not generation["complete"]


def test_target_only_movement_does_not_discard_exact_source(evidence):
    before = build_generation(evidence)["generation"]
    evidence["sections"]["pull_request"]["data"]["lastMergeTargetCommit"]["commitId"] = "f" * 40
    result = build_generation(evidence, before)
    assert result["changed_sections"] == ["pull_request"]
    assert result["invalidations"] == ["review_decision"]
    assert result["generation"]["revision"]["common_sha"] == COMMON


def test_missing_partial_and_unknown_iteration_are_not_empty_success(evidence):
    del evidence["sections"]["reviewers"]
    evidence["sections"]["discussions"]["complete"] = False
    evidence["sections"]["discussions"]["errors"] = ["page_unavailable"]
    evidence["sections"]["iterations"]["data"] = []
    generation = build_generation(evidence)["generation"]
    assert generation["sections"]["reviewers"]["data"] is None
    assert generation["sections"]["reviewers"]["errors"] == ["not_supplied"]
    assert generation["sections"]["discussions"]["data"][0]["comments"]
    assert generation["revision"]["iteration"] is None
    assert not generation["complete"]


def test_partial_iteration_read_cannot_reuse_old_current_range(evidence):
    before = build_generation(evidence)["generation"]
    evidence["sections"]["iterations"].update(complete=False, errors=["page_unavailable"])
    result = build_generation(evidence, before)
    assert result["generation"]["revision"]["iteration"] is None
    assert "source_analysis" in result["invalidations"]
    assert not result["generation"]["sections"]["changes"]["complete"]


def test_newest_iteration_at_another_head_cannot_reuse_historical_range(evidence):
    before = build_generation(evidence)["generation"]
    evidence["sections"]["iterations"]["data"].append({
        "id": 4, "sourceRefCommit": {"commitId": "e" * 40},
        "commonRefCommit": {"commitId": COMMON},
    })
    result = build_generation(evidence, before)
    generation = result["generation"]
    assert generation["revision"]["head_sha"] == HEAD
    assert generation["revision"]["iteration_head_sha"] == "e" * 40
    assert generation["revision"]["iteration"] is None
    assert generation["revision"]["common_sha"] is None
    assert not generation["complete"]
    assert not generation["sections"]["changes"]["complete"]
    assert {"source_analysis", "inline_anchors"} <= set(result["invalidations"])


def test_changed_manifest_invalidates_analysis_even_with_same_head(evidence):
    before = build_generation(evidence)["generation"]
    evidence["sections"]["changes"]["data"].append({
        "changeTrackingId": 9, "changeType": "delete", "item": {"path": "/removed.bin"},
    })
    result = build_generation(evidence, before)
    assert result["changed_sections"] == ["changes"]
    assert "source_analysis" in result["invalidations"]
    changes = result["generation"]["sections"]["changes"]["data"]
    assert any(row["changeType"] == "delete" for row in changes)
    assert all("isBinary" not in row["item"] for row in changes)


def test_native_vote_event_presence_and_changes_survive_normalization(evidence):
    thread = evidence["sections"]["discussions"]["data"][0]
    thread["properties"] = {
        "CodeReviewThreadType": {"$value": "VoteUpdate"},
        "CodeReviewVoteResult": {"$value": 0},
        "CodeReviewVotedByTfId": {"$value": "reviewer-id"},
        "unrelatedProperty": "not retained",
    }
    before = build_generation(evidence)["generation"]
    properties = before["sections"]["discussions"]["data"][0]["properties"]
    assert properties["CodeReviewVoteResult"] == {"$value": 0}
    assert "unrelatedProperty" not in properties
    thread["properties"]["CodeReviewVoteResult"]["$value"] = 10
    result = build_generation(evidence, before)
    assert result["changed_sections"] == ["discussions"]
    assert "review_decision" in result["invalidations"]


@pytest.mark.parametrize("field,value", [
    ("principal_id", "another-account"), ("endpoint", "https://dev.azure.com/another"),
    ("repository_id", "another-repository"), ("number", 43),
])
def test_previous_generation_cannot_cross_scope(evidence, field, value):
    before = build_generation(evidence)["generation"]
    before["scope"][field] = value
    with pytest.raises(ValueError, match="corrupt or outside"):
        build_generation(evidence, before)


def test_corrupt_previous_generation_and_boolean_versions_refused(evidence):
    before = build_generation(evidence)["generation"]
    before["sections"]["discussions"]["data"] = []
    with pytest.raises(ValueError, match="corrupt"):
        build_generation(evidence, before)
    evidence["version"] = True
    with pytest.raises(ValueError, match="contract"):
        build_generation(evidence)


@pytest.mark.parametrize("mutation", [
    lambda e: e["scope"].update(endpoint="https://user:password@example.org"),
    lambda e: e["scope"].update(principal_id=""),
    lambda e: e["sections"]["pull_request"]["data"].update(pullRequestId=43),
    lambda e: e["sections"]["pull_request"]["data"]["repository"].update(id="other"),
    lambda e: e["sections"]["reviewers"].update(errors=["missing_page"]),
    lambda e: e["sections"]["reviewers"].update(observed_to="2026-10-29T00:00:00Z"),
    lambda e: e["sections"]["reviewers"].update(observed_to="2026-10-30T00:00:01"),
    lambda e: e["sections"]["iterations"]["data"][0].update(id=True),
    lambda e: e["sections"]["iterations"]["data"][0]["sourceRefCommit"].update(commitId=["bad"]),
    lambda e: e["sections"]["changes"].pop("revision"),
])
def test_malformed_evidence_refused(evidence, mutation):
    mutation(evidence)
    with pytest.raises(ValueError):
        build_generation(evidence)


def test_actual_cli_contract_load_sync_and_strict_syntax(evidence, tmp_path, capsys):
    supplied = tmp_path / "evidence.json"
    previous = tmp_path / "generation.json"
    supplied.write_text(json.dumps(evidence), encoding="utf-8")
    assert main(["capabilities"]) == 0
    capabilities = json.loads(capsys.readouterr().out)
    assert capabilities["submission"] is capabilities["acquisition"] is False
    assert main(["load", "--evidence", str(supplied)]) == 0
    result = json.loads(capsys.readouterr().out)
    previous.write_text(json.dumps(result["generation"]), encoding="utf-8")
    assert main(["sync", "--evidence", str(supplied), "--previous", str(previous)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["changed_sections"] == result["invalidations"] == []
    with pytest.raises(SystemExit) as failure:
        main(["load", "--evid", str(supplied)])
    assert failure.value.code == 2
    supplied.write_text('{"private": "do not echo"', encoding="utf-8")
    assert main(["load", "--evidence", str(supplied)]) == 1
    assert "do not echo" not in capsys.readouterr().out


def test_cli_explains_contract_failure_without_input_echo(evidence, tmp_path, capsys):
    supplied = tmp_path / "evidence.json"
    evidence["version"] = 2
    supplied.write_text(json.dumps(evidence), encoding="utf-8")
    assert main(["load", "--evidence", str(supplied)]) == 1
    assert "expected version 1" in json.loads(capsys.readouterr().out)["error"]


def test_cli_refuses_ambiguous_duplicate_fields_without_echoing_values(tmp_path, capsys):
    supplied = tmp_path / "evidence.json"
    supplied.write_text('{"scope":"private-value","scope":{}}', encoding="utf-8")
    assert main(["load", "--evidence", str(supplied)]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["error"] == "workspace JSON contains a duplicate field"
    assert "private-value" not in str(error)


def test_endpoint_parser_failures_never_echo_authority(evidence, tmp_path, capsys):
    supplied = tmp_path / "evidence.json"
    evidence["scope"]["endpoint"] = "https://private-user:private-password@example.org\uff0f"
    supplied.write_text(json.dumps(evidence), encoding="utf-8")
    assert main(["load", "--evidence", str(supplied)]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["error"].startswith("endpoint must be an explicit HTTPS endpoint")
    assert "private-" not in str(error)


@pytest.mark.parametrize("change", [{}, {"item": {}}, {"item": {"path": ""}},
                                 {"item": {"path": "/a/../b"}}])
def test_malformed_change_entries_cannot_appear_complete(evidence, change):
    evidence["sections"]["changes"]["data"] = [change]
    with pytest.raises(ValueError, match="usable repository path"):
        build_generation(evidence)


def test_sync_refuses_null_baseline(evidence, tmp_path, capsys):
    supplied = tmp_path / "evidence.json"
    previous = tmp_path / "previous.json"
    supplied.write_text(json.dumps(evidence), encoding="utf-8")
    previous.write_text("null", encoding="utf-8")
    assert main(["sync", "--evidence", str(supplied), "--previous", str(previous)]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["error"] == "sync requires a previous generation object"
    assert "generation" not in error


def test_near_limit_load_output_round_trips_as_sync_baseline(
    evidence, tmp_path, capsys, monkeypatch,
):
    monkeypatch.setattr(workspace, "MAX_BYTES", 4096)
    monkeypatch.setattr(workspace, "MAX_GENERATION_BYTES", 8192)
    monkeypatch.setattr(cli, "MAX_BYTES", 4096)
    monkeypatch.setattr(cli, "MAX_GENERATION_BYTES", 8192)
    pull = evidence["sections"]["pull_request"]["data"]
    pull["description"] = ""
    pull["description"] = "x" * (4096 - len(json.dumps(evidence).encode()) - 1)
    supplied = tmp_path / "evidence.json"
    previous = tmp_path / "generation.json"
    supplied.write_text(json.dumps(evidence), encoding="utf-8")
    assert main(["load", "--evidence", str(supplied)]) == 0
    result = json.loads(capsys.readouterr().out)
    encoded = json.dumps(result["generation"])
    assert 4096 < len(encoded.encode()) < 8192
    previous.write_text(encoded, encoding="utf-8")
    assert main(["sync", "--evidence", str(supplied), "--previous", str(previous)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["changed_sections"] == result["invalidations"] == []
