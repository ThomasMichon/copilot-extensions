"""Untrusted API payloads must remain data and incomplete history must block."""

from __future__ import annotations

import base64
import importlib
import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from test_maintainer_completion_policy import EPOCH, HEAD

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def evidence(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    return importlib.import_module("maintainer_pr_completion_github")


def response(argv, payload):
    return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")


def review(**changes):
    return {
        "id": 1, "state": "APPROVED", "commit_id": HEAD,
        "user": {"login": "copilot-pull-request-reviewer[bot]", "type": "Bot"},
        "submitted_at": "2026-01-01T00:00:01Z", **changes,
    }


def test_complete_paginated_reviews_preserve_a_human_veto(evidence):
    first = [review(id=index + 1, user={"login": f"reviewer-{index}", "type": "User"})
             for index in range(100)]
    later = [
        review(id=101, state="CHANGES_REQUESTED", user={"login": "human", "type": "User"}),
        review(id=102),
    ]
    calls = []

    def invoke(argv):
        calls.append(argv)
        return response(argv, later if "page=2" in argv[2] else first)

    client = evidence.GitHubEvidence("example/project", invoke)
    summary = client.reviews(7, "author", EPOCH)
    assert len(calls) == 2 and summary.complete
    assert summary.change_requests == ("human",)
    assert summary.copilot_state == "APPROVED"
    assert not client.write_attempted


@pytest.mark.parametrize("state", ["COMMENTED", "DISMISSED"])
def test_later_copilot_disposition_cannot_reuse_an_older_approval(evidence, state):
    rows = [review(), review(id=2, state=state, submitted_at="2026-01-01T00:00:02Z")]
    client = evidence.GitHubEvidence("example/project", lambda argv: response(argv, rows))
    assert client.reviews(7, "author", EPOCH).copilot_state == state


@pytest.mark.parametrize("changes", [
    {"user": None}, {"user": {}}, {"user": {"login": "", "type": "User"}},
    {"user": {"login": "copilot-pull-request-reviewer[bot]", "type": "User"}},
    {"id": True}, {"id": 0}, {"state": "UNRECOGNIZED"},
    {"submitted_at": "2026-01-01T00:00:01"}, {"commit_id": "short"},
])
def test_malformed_review_evidence_never_becomes_an_empty_veto_set(evidence, changes):
    rows = [review(**changes)]
    client = evidence.GitHubEvidence("example/project", lambda argv: response(argv, rows))
    with pytest.raises(evidence.EvidenceUnavailable):
        client.reviews(7, "author", EPOCH)
    assert not client.write_attempted


def test_malformed_later_review_page_is_not_hidden_by_the_first_page(evidence):
    first = [review(id=index + 1) for index in range(100)]
    client = evidence.GitHubEvidence("example/project", lambda argv: response(
        argv, [review(id=101, state="CHANGES_REQUESTED", user=None)]
        if "page=2" in argv[2] else first,
    ))
    with pytest.raises(evidence.EvidenceUnavailable, match="reviewer"):
        client.reviews(7, "author", EPOCH)
    assert not client.write_attempted


def test_reopen_and_retarget_epochs_are_paginated_and_monotonic(evidence):
    calls = []

    def invoke(argv):
        calls.append(argv)
        second = "cursor=next" in argv
        nodes = [{"__typename": "BaseRefChangedEvent" if second else "ReopenedEvent",
                  "createdAt": "2026-01-01T00:00:03Z" if second else "2026-01-01T00:00:02Z"}]
        return response(argv, {"data": {"repository": {"pullRequest": {"timelineItems": {
            "nodes": nodes, "pageInfo": {"hasNextPage": not second,
                                      "endCursor": None if second else "next"},
        }}}}})

    client = evidence.GitHubEvidence("example/project", invoke)
    assert client.epoch(7, EPOCH.isoformat()) == EPOCH + timedelta(seconds=3)
    assert len(calls) == 2 and all("number=7" in call for call in calls)


@pytest.mark.parametrize("payload", [
    {"errors": [{"message": "unavailable"}]},
    {"data": {"repository": {"pullRequest": {"timelineItems": {
        "nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None},
    }}}}},
    {"data": {"repository": {"pullRequest": {"timelineItems": {
        "nodes": None, "pageInfo": {"hasNextPage": False},
    }}}}},
])
def test_unknown_epoch_history_is_not_treated_as_creation_only(evidence, payload):
    client = evidence.GitHubEvidence("example/project", lambda argv: response(argv, payload))
    with pytest.raises(evidence.EvidenceUnavailable):
        client.epoch(7, EPOCH.isoformat())


def test_membership_uses_an_invitation_and_the_pinned_base_root_roster(evidence):
    calls = []
    content = "* @old-owner\n# comment\n* @maintainer @another\n"

    def invoke(argv):
        calls.append(argv)
        if "/collaborators/" in argv[2]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[2].endswith(f".github/CODEOWNERS?ref={HEAD}")
        return response(argv, {
            "type": "file", "encoding": "base64",
            "content": base64.b64encode(content.encode()).decode(),
        })

    client = evidence.GitHubEvidence("example/project", invoke)
    assert client.membership("Maintainer", HEAD) == (True, True)
    assert len(calls) == 2


@pytest.mark.parametrize("failure,expected", [("HTTP 404", (False, False)), ("HTTP 403", None)])
def test_invitation_unknown_is_not_permission(evidence, failure, expected):
    client = evidence.GitHubEvidence("example/project", lambda argv: (
        subprocess.CompletedProcess(argv, 1, "", failure)
    ))
    if expected is None:
        with pytest.raises(evidence.EvidenceUnavailable):
            client.membership("maintainer", HEAD)
    else:
        assert client.membership("maintainer", HEAD) == expected


def test_page_budget_fails_explicitly_instead_of_truncating(evidence, monkeypatch):
    calls = []
    monkeypatch.setattr(evidence, "MAX_PAGES", 2)
    client = evidence.GitHubEvidence("example/project", lambda argv: (
        calls.append(argv) or response(argv, [{"id": value} for value in range(100)])
    ))
    with pytest.raises(evidence.EvidenceUnavailable, match="pagination limit"):
        client.pages("repos/example/project/pulls")
    assert len(calls) == 2


@pytest.mark.parametrize("strict,behind,expected", [(False, 3, False), (True, 0, False), (True, 2, True)])
def test_only_required_up_to_date_policy_requests_a_rebase(evidence, strict, behind, expected):
    calls = []

    def invoke(argv):
        calls.append(argv)
        if argv[2] == "graphql":
            return response(argv, {"data": {"repository": {"ref": {
                "branchProtectionRule": None,
            }}}})
        if "/rules/branches/" in argv[2]:
            return response(argv, [{
                "type": "required_status_checks",
                "parameters": {"strict_required_status_checks_policy": strict},
            }])
        assert "/compare/" in argv[2]
        return response(argv, {"behind_by": behind})

    client = evidence.GitHubEvidence("example/project", invoke)
    assert client.update_required("dev", "b" * 40, HEAD) is expected
    assert any("/compare/" in call[2] for call in calls) is strict


class GitHubSnapshotFixture:
    """Documented wire shapes, exercising the actual shared CI/review helpers."""

    def __init__(self) -> None:
        self.calls = []
        self.base = "b" * 40
        repo = {"id": 42, "full_name": "example/project"}
        self.pr = {
            "number": 7, "node_id": "PR_example", "state": "open", "draft": False,
            "title": "Ready change", "labels": [{"name": "auto-merge"}],
            "user": {"login": "maintainer", "type": "User"},
            "created_at": "2026-01-01T00:00:00Z",
            "base": {"ref": "dev", "sha": self.base, "repo": dict(repo)},
            "head": {"ref": "pr/example", "sha": HEAD, "repo": dict(repo)},
            "mergeable": True, "rebaseable": True,
        }
        self.live_base = self.base
        self.rows = [review()]
        self.checks = [{"name": "PR gate", "bucket": "pass"}]
        self.run = {
            "name": "PR gate", "app": {"id": 15368}, "head_sha": HEAD,
            "status": "completed", "conclusion": "success",
        }
        self.strict = False
        self.behind = 0
        self.invited = True
        self.rebase = True

    def invoke(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["gh", "pr", "checks"]:
            return response(argv, self.checks)
        assert argv[:2] == ["gh", "api"], argv
        endpoint = argv[2].lstrip("/")
        if endpoint == "repos/example/project/pulls/7":
            return response(argv, self.pr)
        if "/git/ref/heads/" in endpoint:
            return response(argv, {"object": {"type": "commit", "sha": self.live_base}})
        if "/collaborators/" in endpoint:
            return subprocess.CompletedProcess(
                argv, 0 if self.invited else 1, "", "" if self.invited else "HTTP 404",
            )
        if "/contents/.github/CODEOWNERS" in endpoint:
            return response(argv, {
                "type": "file", "encoding": "base64",
                "content": base64.b64encode(b"* @maintainer\n").decode(),
            })
        if "/reviews?" in endpoint:
            return response(argv, self.rows)
        if endpoint == "graphql":
            query = next(field for field in argv if field.startswith("query="))
            if "timelineItems" in query:
                return response(argv, {"data": {"repository": {"pullRequest": {"timelineItems": {
                    "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None},
                }}}}})
            return response(argv, {"data": {"repository": {"ref": {"branchProtectionRule": None}}}})
        if "/rules/branches/" in endpoint:
            rules = [{
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": self.strict,
                    "required_status_checks": [{"context": "PR gate", "integration_id": 15368}],
                },
            }]
            return response(argv, [rules] if "--slurp" in argv else rules)
        if endpoint.endswith("/check-runs"):
            return response(argv, [{"total_count": 1, "check_runs": [self.run]}])
        if "/compare/" in endpoint:
            return response(argv, {"behind_by": self.behind})
        if endpoint == "repos/example/project":
            return response(argv, {
                "id": 42, "full_name": "example/project", "allow_rebase_merge": self.rebase,
            })
        raise AssertionError(f"Unexpected evidence request: {argv}")


def test_full_snapshot_uses_real_shared_required_check_and_review_policy(evidence):
    from test_maintainer_completion_policy import CONFIG, policy

    fixture = GitHubSnapshotFixture()
    client = evidence.GitHubEvidence(CONFIG.repository, fixture.invoke)
    snapshot = client.snapshot(7, CONFIG)
    assert snapshot.required_checks_verified is True and snapshot.required_checks_error == ""
    assert snapshot.reviews.copilot_state == "APPROVED"
    assert policy.decide(snapshot, CONFIG).action == "merge"
    assert any(call[:3] == ["gh", "pr", "checks"] for call in fixture.calls)
    assert any(call[2].endswith("/check-runs") for call in fixture.calls)
    assert not client.write_attempted


@pytest.mark.parametrize("changes", [
    {"app": {"id": 77}}, {"head_sha": "c" * 40},
    {"status": "queued"}, {"conclusion": "failure"},
])
def test_actual_required_check_verifier_cannot_accept_wrong_or_unsuccessful_source(
    evidence, changes,
):
    from test_maintainer_completion_policy import CONFIG, policy

    fixture = GitHubSnapshotFixture()
    fixture.run.update(changes)
    snapshot = evidence.GitHubEvidence(CONFIG.repository, fixture.invoke).snapshot(7, CONFIG)
    assert snapshot.required_checks_verified is False and snapshot.required_checks_error
    assert policy.decide(snapshot, CONFIG).action == "wait"


@pytest.mark.parametrize("changes", [
    {"labels": []}, {"draft": True}, {"base": {"ref": "main"}},
    {"head": {"repo": {"id": 99, "full_name": "outside/project"}}},
])
def test_scope_exclusions_stop_before_expensive_or_privileged_evidence(evidence, changes):
    from test_maintainer_completion_policy import CONFIG, policy

    fixture = GitHubSnapshotFixture()
    for key, value in changes.items():
        if key in ("base", "head"):
            fixture.pr[key].update(value)
        else:
            fixture.pr[key] = value
    snapshot = evidence.GitHubEvidence(CONFIG.repository, fixture.invoke).snapshot(7, CONFIG)
    assert policy.decide(snapshot, CONFIG).action == "skip"
    assert len(fixture.calls) == 1


def test_stale_pr_base_metadata_does_not_reuse_an_obsolete_roster_or_ci_policy(evidence):
    from test_maintainer_completion_policy import CONFIG

    fixture = GitHubSnapshotFixture()
    fixture.live_base = "c" * 40
    client = evidence.GitHubEvidence(CONFIG.repository, fixture.invoke)
    with pytest.raises(evidence.EvidenceUnavailable, match="base identity"):
        client.snapshot(7, CONFIG)
    assert len(fixture.calls) == 2 and not client.write_attempted


def test_uninvited_author_stops_before_review_or_required_ci_reads(evidence):
    from test_maintainer_completion_policy import CONFIG, policy

    fixture = GitHubSnapshotFixture()
    fixture.invited = False
    snapshot = evidence.GitHubEvidence(CONFIG.repository, fixture.invoke).snapshot(7, CONFIG)
    assert policy.decide(snapshot, CONFIG).action == "skip"
    assert not any(call[:3] == ["gh", "pr", "checks"] for call in fixture.calls)
    assert not any("timelineItems" in field for call in fixture.calls for field in call)
