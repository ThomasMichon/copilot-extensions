"""Typed query evidence and real native query/credential construction."""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Sequence
from dataclasses import replace
import json
import subprocess
import time
from typing import Any, cast

import pytest

from agent_worktrees.providers import github_batch as batch
from agent_worktrees.providers.github_batch_decode import Collection, Member
from agent_worktrees.providers.github_batch_query import PageRequest, query
from agent_worktrees.providers.github_checks import checks_from_data
from test_pr_observation_credentials import Binding, Source

SHA = "a" * 40
BASE = "b" * 40
STAMP = "2026-01-01T00:00:00Z"
Document = dict[str, Any]
Call = tuple[list[str], dict[str, Any]]


def connection(
    nodes: Sequence[Document] = (), *, total: int | None = None, after: str | None = None,
) -> Document:
    return {
        "totalCount": len(nodes) if total is None else total, "nodes": list(nodes),
        "pageInfo": {"hasNextPage": after is not None, "endCursor": after},
    }


def review(number: int = 4_294_967_301, state: str = "APPROVED") -> Document:
    return {
        "id": "review-" + str(number), "fullDatabaseId": str(number), "state": state,
        "author": {"login": "reviewer"}, "commit": {"oid": SHA}, "submittedAt": STAMP,
    }


def candidate(number: int = 7) -> Document:
    return {
        "number": number, "state": "OPEN", "merged": False, "mergeable": "UNKNOWN",
        "headRefOid": SHA, "baseRefOid": BASE, "baseRefName": "dev", "updatedAt": STAMP,
        "title": "Example", "isDraft": False, "author": {"login": "author"},
        "labels": connection([{"id": "label", "name": "selected"}]),
        "reviews": connection([review()]),
        "commits": {"nodes": [{"commit": {"oid": SHA, "statusCheckRollup": {
            "contexts": connection(),
        }}}]},
    }


def envelope(*members: object) -> Document:
    return {"data": {
        "rateLimit": {"cost": 1, "remaining": 99,
                      "resetAt": "2099-01-01T00:00:00Z"},
        "repository": {"nameWithOwner": "owner/repository", **{
            f"p{index}": value for index, value in enumerate(members)
        }},
    }}


def transport(monkeypatch: pytest.MonkeyPatch, replies: Sequence[Any]) -> list[Call]:
    calls: list[Call] = []
    responses = iter(replies)

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        value = next(responses)
        if callable(value):
            value = value()
        if isinstance(value, subprocess.CompletedProcess):
            return value
        return subprocess.CompletedProcess(
            argv, 0, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
            + json.dumps(value), "",
        )

    monkeypatch.setattr(batch, "run_cli", run)
    return calls


def document(call: Call) -> Document:
    lines = call[1]["input_text"].splitlines()
    return cast(Document, json.loads(json.loads(lines[1].partition(" = ")[2])))


def test_multi_pr_fixed_transport_renews_and_preserves_numeric_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = transport(monkeypatch, [envelope(candidate(), candidate(8))])
    source = Source()
    result = batch.snapshots("owner/repository", [7, 8], credential_source=source)
    assert [row["outcome"] for row in result["results"]] == ["complete", "complete"]
    snapshot = result["results"][0]["snapshot"]
    assert snapshot["reviews"][0]["id"] == 4_294_967_301
    assert snapshot["reviews"][0]["commit_id"] == SHA
    assert snapshot["reviews_complete"] is True and snapshot["mergeable"] is None
    assert snapshot["head_sha"] == SHA and snapshot["base_sha"] == BASE
    assert snapshot["checks_state"] == "none" and snapshot["checks_complete"] is True
    assert snapshot["labels"] == ["selected"]
    assert source.calls == len(calls) == result["query_attempts"] == 1
    argv, options = calls[0]
    assert argv[:2] == ["curl", "-q"]
    assert argv[argv.index("-X") + 1] == "POST"
    assert argv[argv.index("--retry") + 1] == "0"
    assert argv[argv.index("--max-redirs") + 1] == "0"
    assert argv[argv.index("--proto") + 1] == "=https"
    assert argv[argv.index("--noproxy") + 1] == "*"
    assert argv[argv.index("--http1.1")] == "--http1.1"
    assert "https://api.github.com/graphql" in argv and "--config" in argv
    assert "synthetic-" not in " ".join(argv) and "env" not in options
    scheme = "Bear" + "er "
    assert json.loads(options["input_text"].splitlines()[0].partition(" = ")[2]) == (
        "Authorization: " + scheme + "synthetic-1"
    )
    assert "synthetic-" not in json.dumps(result)
    fixed = document(calls[0])
    assert fixed == query("owner/repository", (PageRequest(7), PageRequest(8)))
    assert "mutation" not in fixed["query"] and "body" not in fixed["query"]
    assert "first: 100" in fixed["query"] and "fullDatabaseId" in fixed["query"]
    assert result["validated_primary_points"] == 1


def test_manual_pages_keep_one_operation_budget_and_skip_completed_members(monkeypatch: pytest.MonkeyPatch) -> None:
    first = candidate()
    first["reviews"] = connection([review()], total=2, after="cursor-1")
    second = candidate()
    second["reviews"] = connection([review(4_294_967_302, "DISMISSED")], total=2)
    second.pop("labels")
    second["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = {}
    calls = transport(monkeypatch, [envelope(first, candidate(8)), envelope(second)])
    source = Source()
    result = batch.snapshots("owner/repository", [7, 8], credential_source=source)
    snapshot = result["results"][0]["snapshot"]
    assert len(snapshot["reviews"]) == 2 and snapshot["reviews"][1]["dismissed"]
    assert source.calls == len(calls) == 2
    variables = document(calls[1])["variables"]
    assert variables["reviews0"] == "cursor-1"
    assert variables["readLabels0"] is False and variables["readChecks0"] is False
    assert "n1" not in variables and calls[1][1]["timeout"] <= calls[0][1]["timeout"]


@pytest.mark.parametrize("kind", ["missing", "foreign", "scoped", "duplicate-id", "changed"])
def test_bad_member_never_becomes_complete_or_poison_sibling(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    member: Any = candidate()
    if kind == "missing":
        member = None
    elif kind == "foreign":
        member["number"] = 99
    elif kind == "duplicate-id":
        member["reviews"] = connection([review(), review()])
    first = envelope(member, candidate(8))
    replies = [first]
    if kind == "scoped":
        first["errors"] = [{"path": ["repository", "p0", "reviews"], "message": "private-detail"}]
    if kind == "changed":
        member["reviews"] = connection([review()], total=2, after="cursor-1")
        newer = candidate()
        newer["baseRefOid"] = "c" * 40
        newer["reviews"] = connection([review(9)], total=2)
        replies.append(envelope(newer))
    calls = transport(monkeypatch, replies)
    result = batch.snapshots("owner/repository", [7, 8], credential_source=Source())
    assert result["results"][0]["snapshot"] is None
    assert result["results"][0]["outcome"] == "unavailable"
    assert result["results"][1]["outcome"] == "complete"
    assert "private-detail" not in json.dumps(result)
    assert len(calls) == len(replies)


@pytest.mark.parametrize("kind", ["unscoped", "quota-missing", "quota-cost", "quota-time", "foreign-repo"])
def test_invalid_envelope_stops_without_partial_success_or_retry(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    value = envelope(candidate())
    if kind == "unscoped":
        value["errors"] = [{"message": "private-detail"}]
    elif kind == "quota-missing":
        value["data"].pop("rateLimit")
    elif kind == "quota-cost":
        value["data"]["rateLimit"]["cost"] = 2
    elif kind == "quota-time":
        value["data"]["rateLimit"]["resetAt"] = "2099-01-01Z"
    else:
        value["data"]["repository"]["nameWithOwner"] = "other/repository"
    calls = transport(monkeypatch, [value])
    result = batch.snapshots("owner/repository", [7], credential_source=Source())
    assert result["results"][0]["snapshot"] is None
    assert result["stop_reason"] == "invalid-provider-evidence" and len(calls) == 1


def test_request_limit_and_midpage_credential_loss_preserve_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    first = candidate()
    first["reviews"] = connection([review()], total=2, after="next")
    calls = transport(monkeypatch, [envelope(first)])
    result = batch.snapshots("owner/repository", [7], credential_source=Source(), max_requests=1)
    assert result["results"][0]["snapshot"] is None
    assert result["stop_reason"] == "operation-budget-exhausted" and len(calls) == 1
    calls = transport(monkeypatch, [envelope(first)])
    source = Source(fail_at=2)
    result = batch.snapshots("owner/repository", [7], credential_source=source)
    assert result["results"][0]["snapshot"] is None and source.calls == 2 and len(calls) == 1


@pytest.mark.parametrize("numbers", [[True], [0], [2_147_483_648], [7, 7], list(range(1, 12))])
def test_invalid_selection_refuses_before_acquisition(numbers: list[int]) -> None:
    source = Source()
    with pytest.raises(ValueError):
        batch.snapshots("owner/repository", numbers, credential_source=source)
    assert source.calls == 0


def test_wrong_endpoint_binding_and_expiry_refuse_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = transport(monkeypatch, [])
    source = Source()
    with pytest.raises(ValueError):
        batch.snapshots("owner/repository", [7], credential_source=source,
                        api_base="https://enterprise.example/api/v3")
    assert source.calls == 0
    source = Source(replace(Binding(), repository="other/repository"))
    result = batch.snapshots("owner/repository", [7], credential_source=source)
    assert result["results"][0]["snapshot"] is None and not calls
    material = replace(Source()(), expires_at=int(time.time()) - 1)
    result = batch.snapshots("owner/repository", [7], credential_source=lambda: material)
    assert result["results"][0]["snapshot"] is None and not calls


def test_collection_refuses_repeated_cursor_and_incomplete_final_page() -> None:
    collection = Collection()
    collection.append(connection([{"id": "a"}], total=3, after="next"))
    with pytest.raises(ValueError, match="cursor"):
        collection.append(connection([{"id": "b"}], total=3, after="next"))
    with pytest.raises(ValueError, match="incomplete"):
        Collection().append(connection([{"id": "a"}], total=2))
    collection = Collection()
    collection.append(connection([{"id": "a"}], total=2, after="next"))
    final = connection([{"id": "b"}], total=2)
    final["pageInfo"]["endCursor"] = "next"
    with pytest.raises(ValueError, match="cursor"):
        collection.append(final)


def test_checks_and_reviews_use_scalar_normalization_without_losing_old_evidence() -> None:
    value = candidate()
    value["reviews"] = connection([review(9, "PENDING"), review(10, "DISMISSED"), review(11)])
    value["reviews"]["nodes"][0]["submittedAt"] = None
    value["reviews"]["nodes"][2]["commit"]["oid"] = "c" * 40
    run: Document = {
        "__typename": "CheckRun", "id": "check-1", "name": "build",
        "status": "COMPLETED", "conclusion": "STARTUP_FAILURE",
        "detailsUrl": "https://example.test/check", "url": "https://example.test/run",
        "summary": "failure detail " * 300, "title": "short",
        "startedAt": STAMP, "checkSuite": {"id": "suite", "app": {"id": "app"}},
    }
    status = {
        "__typename": "StatusContext", "id": "status-1", "context": "external",
        "state": "PENDING", "description": "pending", "targetUrl": None, "createdAt": STAMP,
    }
    value["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"] = connection([run, status])
    member = Member(7)
    member.append(value)
    snapshot = member.snapshot()
    expected = checks_from_data(
        {"state": "pending", "statuses": [{"context": "external", "state": "pending"}]},
        [{"name": "build", "status": "completed", "conclusion": "startup_failure",
          "details_url": run["detailsUrl"], "output": {"summary": run["summary"]}}],
        runs_complete=True,
    )
    assert snapshot.check_failures == expected.failures
    assert snapshot.checks_state == expected.state == "failure"
    assert snapshot.reviews[0].submitted_at == ""
    assert snapshot.reviews[1].dismissed and snapshot.reviews[2].commit_id == "c" * 40
    unknown = deepcopy(value)
    unknown["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["nodes"][0]["conclusion"] = None
    member = Member(7)
    member.append(unknown)
    with pytest.raises(ValueError, match="incomplete batch checks"):
        member.snapshot()


@pytest.mark.parametrize("state,merged", [("CLOSED", False), ("MERGED", True)])
def test_closed_lifecycle_and_empty_rollup_are_native_evidence(state: str, merged: bool) -> None:
    value = candidate()
    value.update(state=state, merged=merged, mergeable="CONFLICTING")
    value["reviews"] = connection()
    value["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
    member = Member(7)
    member.append(value)
    snapshot = member.snapshot()
    assert snapshot.pr_state == "closed" and snapshot.merged is merged
    assert snapshot.reviews_complete and snapshot.checks_complete and snapshot.mergeable is False
    assert snapshot.checks_state == "none"


def test_newest_status_supersedes_stale_failure_in_the_shared_normalizer() -> None:
    value = candidate()
    contexts: list[Document] = [{
        "__typename": "StatusContext", "id": "new", "context": "Build",
        "state": "SUCCESS", "description": None, "targetUrl": None,
        "createdAt": "2026-01-02T00:00:00Z",
    }, {
        "__typename": "StatusContext", "id": "old", "context": "build",
        "state": "FAILURE", "description": "old attempt", "targetUrl": None,
        "createdAt": STAMP,
    }, {
        "__typename": "CheckRun", "id": "neutral", "name": "optional",
        "status": "COMPLETED", "conclusion": "NEUTRAL",
        "startedAt": STAMP, "checkSuite": {"id": "suite", "app": {"id": "app"}},
    }]
    value["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"] = connection(contexts)
    member = Member(7)
    member.append(value)
    snapshot = member.snapshot()
    assert snapshot.checks_state == "success" and snapshot.check_failures == ()


def test_check_reruns_select_latest_per_application_and_refuse_unknown_order() -> None:
    old: Document = {
        "__typename": "CheckRun", "id": "old-run", "name": "build",
        "status": "COMPLETED", "conclusion": "CANCELLED", "startedAt": STAMP,
        "checkSuite": {"id": "old-suite", "app": {"id": "app"}},
    }
    new = {**old, "id": "new-run", "conclusion": "SUCCESS",
           "startedAt": "2026-01-02T00:00:00Z",
           "checkSuite": {"id": "new-suite", "app": {"id": "app"}}}
    value = candidate()
    contexts = value["commits"]["nodes"][0]["commit"]["statusCheckRollup"]
    contexts["contexts"] = connection([old, new])
    member = Member(7)
    member.append(value)
    assert member.snapshot().checks_state == "success"
    other = {**old, "id": "other-app", "checkSuite": {"id": "other-suite", "app": {"id": "other"}}}
    contexts["contexts"] = connection([new, old, other])
    member = Member(7)
    member.append(value)
    assert member.snapshot().checks_state == "failure"
    contexts["contexts"] = connection([old, {**new, "startedAt": None}])
    member = Member(7)
    member.append(value)
    with pytest.raises(ValueError, match="rerun ordering"):
        member.snapshot()
