"""Admin review bypass cannot waive absent or unsuccessful required CI."""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_worktrees.providers.github_required_checks import RequiredChecksError, verified_merge_head

HEAD = "a" * 40


class GitHubFixture:
    def __init__(self):
        self.calls = []
        self.head = HEAD
        self.base = "dev"
        self.protection = {
            "requiresStatusChecks": True,
            "requiredStatusCheckContexts": ["PR gate"],
        }
        self.classic = {
            "contexts": ["PR gate"],
            "checks": [{"context": "PR gate", "app_id": 15368}],
        }
        self.rules = [{
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "policy"}]},
        }]
        self.later_pages = []
        self.runs = [{
            "name": "PR gate", "app": {"id": 15368}, "head_sha": HEAD,
            "status": "completed", "conclusion": "success",
        }]
        self.run_pages = None
        self.checks = [
            {"name": "PR gate", "bucket": "pass"},
            {"name": "policy", "bucket": "pass"},
        ]
        self.fail = None
        self.after_head = None
        self.after_base = None
        self.pr_reads = 0

    def invoke(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["gh", "pr", "merge"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["gh", "pr", "checks"]:
            assert "--required" in argv
            assert "--hostname" not in argv
            kind, data = "checks", self.checks
        elif argv[2].endswith("/pulls/7"):
            self.pr_reads += 1
            head = self.after_head if self.pr_reads > 1 and self.after_head else self.head
            base = self.after_base if self.pr_reads > 1 and self.after_base else self.base
            kind, data = "pr", {"state": "open", "head": {"sha": head}, "base": {"ref": base}}
        elif argv[2] == "graphql":
            assert f"ref=refs/heads/{self.base}" in argv
            kind, data = "policy", {
                "data": {"repository": {"ref": {"branchProtectionRule": self.protection}}},
            }
        elif "/rules/branches/" in argv[2]:
            assert "--paginate" in argv
            assert "--slurp" in argv
            kind, data = "rules", [self.rules, *self.later_pages]
        elif argv[2].endswith("/protection/required_status_checks"):
            kind, data = "classic", self.classic
        elif argv[2].endswith("/check-runs"):
            assert "--paginate" in argv
            assert "--slurp" in argv
            kind, data = "runs", self.run_pages if self.run_pages is not None else [{
                "total_count": len(self.runs), "check_runs": self.runs,
            }]
        else:
            raise AssertionError(f"Unexpected command: {argv}")
        if kind == self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "permission denied")
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    def verify(self, **kwargs):
        return verified_merge_head(
            "owner/project", 7, invoke=self.invoke, host="github.com", **kwargs,
        )


def test_classic_and_ruleset_contexts_must_both_report_success():
    fixture = GitHubFixture()
    assert fixture.verify(expected_head_sha=HEAD) == HEAD
    assert fixture.pr_reads == 2


@pytest.mark.parametrize("passed", [False, True])
def test_required_context_on_later_page_must_report_success(passed):
    fixture = GitHubFixture()
    fixture.rules = [{"type": "pull_request"}] * 30
    fixture.later_pages = [[{
        "type": "required_status_checks",
        "parameters": {"required_status_checks": [{"context": "late CI"}]},
    }]]
    if passed:
        fixture.checks.append({"name": "late CI", "bucket": "pass"})
        assert fixture.verify() == HEAD
    else:
        with pytest.raises(RequiredChecksError, match="late CI"):
            fixture.verify()


def test_malformed_later_page_fails_closed():
    fixture = GitHubFixture()
    fixture.later_pages = [None]
    with pytest.raises(RequiredChecksError, match="branch rules page"):
        fixture.verify()


def test_later_page_request_failure_cannot_authorize_merge():
    fixture = GitHubFixture()
    invoke = fixture.invoke

    def failed_page(argv):
        if "/rules/branches/" in argv[2]:
            assert "--paginate" in argv
            return subprocess.CompletedProcess(argv, 1, json.dumps([fixture.rules]), "page 2 denied")
        return invoke(argv)

    fixture.invoke = failed_page
    with pytest.raises(RequiredChecksError, match="page 2 denied"):
        fixture.verify()


@pytest.mark.parametrize("checks", [
    [], [{"name": "policy", "bucket": "pass"}],
    [{"name": "PR gate", "bucket": "pending"}, {"name": "policy", "bucket": "pass"}],
    [{"name": "PR gate", "bucket": "fail"}, {"name": "policy", "bucket": "pass"}],
    [{"name": "PR gate", "bucket": "skipping"}, {"name": "policy", "bucket": "pass"}],
])
def test_missing_pending_failed_or_unexecuted_required_check_blocks(checks):
    fixture = GitHubFixture()
    fixture.checks = checks
    with pytest.raises(RequiredChecksError, match="required checks"):
        fixture.verify()


def test_duplicate_failed_required_result_cannot_be_hidden_by_a_pass():
    fixture = GitHubFixture()
    fixture.checks.append({"name": "policy", "bucket": "fail"})
    with pytest.raises(RequiredChecksError, match="policy"):
        fixture.verify()


@pytest.mark.parametrize("boundary", ["pr", "policy", "classic", "rules", "checks", "runs"])
def test_unreadable_required_evidence_never_authorizes_merge(boundary):
    fixture = GitHubFixture()
    fixture.fail = boundary
    with pytest.raises(RequiredChecksError, match="verification failed"):
        fixture.verify()


@pytest.mark.parametrize("attribute,value", [
    ("protection", {}),
    ("protection", {"requiresStatusChecks": True, "requiredStatusCheckContexts": None}),
    ("rules", None),
    ("rules", [{}]),
    ("rules", [{"type": None}]),
    ("rules", [{"type": True}]),
    ("rules", [{"type": ""}]),
    ("rules", [{"type": "required_status_checks", "parameters": {}}]),
    ("checks", [{"name": {}, "bucket": "pass"}]),
    ("classic", {"contexts": ["PR gate"], "checks": []}),
    ("classic", {"contexts": ["PR gate"], "checks": [{"context": "PR gate"}]}),
    ("classic", {"contexts": ["PR gate"], "checks": [{"context": "PR gate", "app_id": True}]}),
])
def test_malformed_required_policy_or_results_fail_closed(attribute, value):
    fixture = GitHubFixture()
    setattr(fixture, attribute, value)
    with pytest.raises(RequiredChecksError):
        fixture.verify()


@pytest.mark.parametrize("attribute,value", [("after_head", "b" * 40), ("after_base", "main")])
def test_changed_identity_requires_new_validation(attribute, value):
    fixture = GitHubFixture()
    setattr(fixture, attribute, value)
    with pytest.raises(RequiredChecksError, match="changed"):
        fixture.verify()


def test_expected_commit_must_match_before_any_check_query():
    fixture = GitHubFixture()
    with pytest.raises(RequiredChecksError, match="expected commit"):
        fixture.verify(expected_head_sha="b" * 40)
    assert len(fixture.calls) == 1


@pytest.mark.parametrize("repo,number", [
    ("owner/project/extra", 7), ("../project", 7), ("owner/project?query", 7),
    ("owner/project", 0), ("owner/project", True),
])
def test_invalid_target_is_rejected_before_any_request(repo, number):
    fixture = GitHubFixture()
    with pytest.raises(RequiredChecksError):
        verified_merge_head(repo, number, invoke=fixture.invoke, host="github.com")
    assert not fixture.calls


def test_command_failure_keeps_both_diagnostic_streams():
    def failed(argv):
        return subprocess.CompletedProcess(argv, 1, "required context unavailable", "permission denied")

    with pytest.raises(RequiredChecksError) as caught:
        verified_merge_head("owner/project", 7, invoke=failed, host="github.com")
    assert "required context unavailable" in str(caught.value)
    assert "permission denied" in str(caught.value)


def test_no_required_checks_does_not_promote_optional_failures_to_a_gate():
    fixture = GitHubFixture()
    fixture.protection = None
    fixture.rules = [{"type": "pull_request"}]
    fixture.checks = [{"name": "optional", "bucket": "fail"}]
    assert fixture.verify() == HEAD
    assert not any(argv[:3] == ["gh", "pr", "checks"] for argv in fixture.calls)


@pytest.mark.parametrize("passed", [False, True])
def test_same_context_from_distinct_required_integrations_needs_both(passed):
    fixture = GitHubFixture()
    fixture.rules.append({
        "type": "required_status_checks",
        "parameters": {"required_status_checks": [{"context": "PR gate", "integration_id": 77}]},
    })
    if passed:
        fixture.runs.append({**fixture.runs[0], "app": {"id": 77}})
        assert fixture.verify() == HEAD
    else:
        with pytest.raises(RequiredChecksError, match=r"PR gate \(app 77\)"):
            fixture.verify()


@pytest.mark.parametrize("mutation", [
    {"app": {"id": 77}}, {"head_sha": "b" * 40}, {"status": "queued"},
    {"conclusion": "failure"}, {"app": {"id": True}}, {"name": {}},
])
def test_wrong_or_unsuccessful_integration_bound_result_refuses(mutation):
    fixture = GitHubFixture()
    fixture.runs[0].update(mutation)
    with pytest.raises(RequiredChecksError):
        fixture.verify()


def test_any_integration_legacy_context_needs_no_check_run_identity():
    fixture = GitHubFixture()
    fixture.classic["checks"][0]["app_id"] = -1
    assert fixture.verify() == HEAD
    assert not any(argv[2].endswith("/check-runs") for argv in fixture.calls)


def test_integration_bound_results_on_later_pages_are_validated():
    fixture = GitHubFixture()
    fixture.run_pages = [
        {"total_count": 1, "check_runs": []},
        {"total_count": 1, "check_runs": fixture.runs},
    ]
    assert fixture.verify() == HEAD


def test_partial_check_run_pages_cannot_authorize_merge():
    fixture = GitHubFixture()
    fixture.run_pages = [{"total_count": 2, "check_runs": fixture.runs}]
    with pytest.raises(RequiredChecksError, match="missing or incomplete"):
        fixture.verify()


def test_enterprise_authority_and_escaped_branch_are_preserved():
    fixture = GitHubFixture()
    fixture.base = "release/next"
    assert verified_merge_head(
        "owner/project", 7, invoke=fixture.invoke, host="github.example.test",
    ) == HEAD
    api = [argv for argv in fixture.calls if argv[1] == "api"]
    assert all(argv[-2:] == ["--hostname", "github.example.test"] for argv in api)
    assert any("/rules/branches/release%2Fnext" in argv[2] for argv in api)


@pytest.mark.parametrize("passed", [False, True])
def test_actual_admin_merge_is_guarded_and_pins_the_verified_head(monkeypatch, passed):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    if not passed:
        fixture.checks = [{"name": "policy", "bucket": "pass"}]
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull("owner/project", 7, admin=True)
    merges = [argv for argv in fixture.calls if argv[:3] == ["gh", "pr", "merge"]]
    if passed:
        assert result == ""
        assert len(merges) == 1
        assert "--admin" in merges[0]
        assert merges[0][merges[0].index("--match-head-commit") + 1] == HEAD
    else:
        assert "PR gate" in result
        assert not merges
