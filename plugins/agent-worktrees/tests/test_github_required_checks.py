"""Admin review bypass cannot waive absent or unsuccessful required CI."""

from __future__ import annotations

import copy
import json
import subprocess

import pytest

from agent_worktrees.providers.github_required_checks import (
    RequiredChecksError,
    review_exception_evidence,
    verified_merge_head,
)

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
            "parameters": {
                "required_status_checks": [{"context": "policy"}],
                "strict_required_status_checks_policy": False,
            },
        }, {
            "type": "pull_request",
            "ruleset_id": 42,
            "parameters": {
                "require_code_owner_review": True,
                "allowed_merge_methods": ["merge", "squash", "rebase"],
                "required_approving_review_count": 1,
                "required_review_thread_resolution": False,
                "require_last_push_approval": False,
            },
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
        self.rules_reads = 0
        self.protection_reads = 0
        self.normal_merge_fails = False
        self.admin_merge_fails = False
        self.reviews = [{
            "id": 1, "state": "COMMENTED", "user": {"login": "copilot"},
            "submitted_at": "2026-10-18T00:00:00Z", "commit_id": HEAD,
        }]
        self.review_decision = "REVIEW_REQUIRED"
        self.bypass = "pull_requests_only"
        self.ruleset_bypasses: dict[int, str | None] = {}
        self.labels = []
        self.draft = False
        self.title = "Ready"
        self.classic_protection = {
            "required_pull_request_reviews": None,
            "required_status_checks": {"strict": False},
            "required_conversation_resolution": {"enabled": False},
            "required_signatures": {"enabled": False},
            "required_linear_history": {"enabled": False},
            "lock_branch": {"enabled": False},
        }

    def invoke(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["gh", "pr", "merge"]:
            failed = self.admin_merge_fails if "--admin" in argv else self.normal_merge_fails
            if failed:
                return subprocess.CompletedProcess(argv, 1, "", "protected merge refused")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["gh", "api", "--include"]:
            assert argv[3].endswith("/protection")
            self.protection_reads += 1
            kind, data = "review_policy", self.classic_protection
        elif argv[:3] == ["gh", "pr", "view"]:
            kind, data = "decision", {"reviewDecision": self.review_decision}
        elif argv[:3] == ["gh", "pr", "checks"]:
            assert "--required" in argv
            assert "--hostname" not in argv
            kind, data = "checks", self.checks
        elif argv[2].endswith("/pulls/7"):
            self.pr_reads += 1
            head = self.after_head if self.pr_reads > 1 and self.after_head else self.head
            base = self.after_base if self.pr_reads > 1 and self.after_base else self.base
            kind, data = "pr", {
                "state": "open", "draft": self.draft, "title": self.title,
                "labels": self.labels, "user": {"login": "author"},
                "head": {"sha": head}, "base": {"ref": base},
            }
        elif argv[2].endswith("/pulls/7/reviews"):
            assert "--paginate" in argv and "--slurp" in argv
            kind, data = "reviews", [self.reviews]
        elif argv[2] == "graphql":
            assert f"ref=refs/heads/{self.base}" in argv
            kind, data = "policy", {
                "data": {"repository": {"ref": {"branchProtectionRule": self.protection}}},
            }
        elif "/rules/branches/" in argv[2]:
            assert "--paginate" in argv
            assert "--slurp" in argv
            self.rules_reads += 1
            kind, data = "rules", [self.rules, *self.later_pages]
        elif argv[2].endswith("/protection/required_status_checks"):
            kind, data = "classic", self.classic
        elif argv[2].endswith("/check-runs"):
            assert "--paginate" in argv
            assert "--slurp" in argv
            kind, data = "runs", self.run_pages if self.run_pages is not None else [{
                "total_count": len(self.runs), "check_runs": self.runs,
            }]
        elif "/rulesets/" in argv[2]:
            ruleset_id = int(argv[2].rsplit("/", 1)[1])
            assert ruleset_id == 42 or ruleset_id in self.ruleset_bypasses
            kind, data = "ruleset", {
                "current_user_can_bypass": self.ruleset_bypasses.get(ruleset_id, self.bypass),
            }
        else:
            raise AssertionError(f"Unexpected command: {argv}")
        if kind == self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "permission denied")
        if kind == "review_policy":
            return subprocess.CompletedProcess(
                argv, 0,
                "HTTP/2.0 200 OK\r\ncontent-type: application/json\r\n\r\n"
                + json.dumps(data),
                "",
            )
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
@pytest.mark.parametrize("base_ref_rules", [(), ("deletion", "non_fast_forward")])
def test_actual_admin_merge_is_guarded_and_pins_the_verified_head(
    monkeypatch: pytest.MonkeyPatch, passed: bool, base_ref_rules: tuple[str, ...],
) -> None:
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    fixture.rules.extend({"type": kind} for kind in base_ref_rules)
    if not passed:
        fixture.checks = [{"name": "policy", "bucket": "pass"}]
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    merges = [argv for argv in fixture.calls if argv[:3] == ["gh", "pr", "merge"]]
    if passed:
        assert result == ""
        assert len(merges) == 2
        assert "--admin" not in merges[0] and "--admin" in merges[1]
        assert all(argv[argv.index("--match-head-commit") + 1] == HEAD for argv in merges)
    else:
        assert "PR gate" in result
        assert len(merges) == 1 and "--admin" not in merges[0]


def test_review_exception_requires_complete_clean_codeowner_evidence():
    fixture = GitHubFixture()
    evidence = review_exception_evidence(
        "owner/project", 7, invoke=fixture.invoke, host="github.com",
        expected_head_sha=HEAD,
    )
    assert evidence.head_sha == HEAD and evidence.base_ref == "dev"


def _layered_review_fixture() -> GitHubFixture:
    fixture = GitHubFixture()
    fixture.rules[1]["parameters"]["required_approving_review_count"] = 0
    approval = copy.deepcopy(fixture.rules[1])
    approval["ruleset_id"] = 43
    approval["parameters"]["require_code_owner_review"] = False
    approval["parameters"]["required_approving_review_count"] = 1
    fixture.rules.append(approval)
    fixture.ruleset_bypasses[43] = "pull_requests_only"
    return fixture


def test_layered_review_exception_requires_authority_on_both_layers() -> None:
    fixture = _layered_review_fixture()
    evidence = review_exception_evidence(
        "owner/project", 7, invoke=fixture.invoke, host="github.com",
        expected_head_sha=HEAD,
    )
    assert evidence.head_sha == HEAD
    for ruleset_id in (42, 43):
        assert any(argv[2].endswith(f"/rulesets/{ruleset_id}") for argv in fixture.calls)


def test_layered_authorized_merge_preserves_required_checks_and_expected_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees.providers import github

    fixture = _layered_review_fixture()
    fixture.normal_merge_fails = True
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert result == ""
    merges = [argv for argv in fixture.calls if argv[:3] == ["gh", "pr", "merge"]]
    assert len(merges) == 2
    assert "--admin" not in merges[0] and "--admin" in merges[1]
    assert all(argv[argv.index("--match-head-commit") + 1] == HEAD for argv in merges)


def test_layered_zero_vote_general_rule_needs_no_review_bypass() -> None:
    fixture = _layered_review_fixture()
    general = copy.deepcopy(fixture.rules[2])
    general["ruleset_id"] = 44
    general["parameters"]["required_approving_review_count"] = 0
    fixture.rules.append(general)
    fixture.ruleset_bypasses[44] = "never"
    evidence = review_exception_evidence(
        "owner/project", 7, invoke=fixture.invoke, host="github.com",
    )
    assert evidence.head_sha == HEAD
    assert not any(argv[2].endswith("/rulesets/44") for argv in fixture.calls)


def test_layered_approval_authority_lookup_failure_is_not_permission() -> None:
    fixture = _layered_review_fixture()
    original = fixture.invoke

    def unavailable(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[2].endswith("/rulesets/43"):
            return subprocess.CompletedProcess(argv, 1, "", "permission denied")
        return original(argv, **kwargs)

    with pytest.raises(RequiredChecksError, match="permission denied"):
        review_exception_evidence(
            "owner/project", 7, invoke=unavailable, host="github.com",
        )


@pytest.mark.parametrize("bypass", ["never", "unknown", None])
def test_layered_codeowner_only_actor_cannot_waive_independent_approval(
    monkeypatch: pytest.MonkeyPatch, bypass: str | None,
) -> None:
    from agent_worktrees.providers import github

    fixture = _layered_review_fixture()
    fixture.normal_merge_fails = True
    fixture.ruleset_bypasses[43] = bypass
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert "not authorized" in result and "43" in result
    assert not any("--admin" in argv for argv in fixture.calls)


@pytest.mark.parametrize("field,value", [
    ("required_approving_review_count", 2),
    ("required_review_thread_resolution", True),
    ("require_last_push_approval", True),
])
def test_layered_review_cannot_hide_additional_requirements(field: str, value: object) -> None:
    fixture = _layered_review_fixture()
    fixture.rules[2]["parameters"][field] = value
    with pytest.raises(RequiredChecksError, match="another pull-request review"):
        review_exception_evidence(
            "owner/project", 7, invoke=fixture.invoke, host="github.com",
        )


@pytest.mark.parametrize("layer", [1, 2])
@pytest.mark.parametrize("reviewers", [
    [{"reviewer": {"id": 99, "type": "Team"}}], "unavailable",
])
def test_layered_review_does_not_waive_specific_or_unknown_reviewers(
    layer: int, reviewers: object,
) -> None:
    fixture = _layered_review_fixture()
    fixture.rules[layer]["parameters"]["required_reviewers"] = reviewers
    with pytest.raises(RequiredChecksError, match="required reviewer"):
        review_exception_evidence(
            "owner/project", 7, invoke=fixture.invoke, host="github.com",
        )


def test_zero_vote_rule_cannot_hide_a_specific_required_reviewer() -> None:
    fixture = _layered_review_fixture()
    fixture.rules[2]["parameters"].update(
        required_approving_review_count=0,
        required_reviewers=[{"reviewer": {"id": 99, "type": "Team"}}],
    )
    with pytest.raises(RequiredChecksError, match="required reviewer"):
        review_exception_evidence(
            "owner/project", 7, invoke=fixture.invoke, host="github.com",
        )


def test_layered_review_authority_never_overrides_a_human_veto() -> None:
    fixture = _layered_review_fixture()
    fixture.reviews.insert(0, {
        "id": 0, "state": "CHANGES_REQUESTED", "user": {"login": "human"},
        "submitted_at": "2026-10-17T00:00:00Z", "commit_id": "b" * 40,
    })
    with pytest.raises(RequiredChecksError, match="active change request"):
        review_exception_evidence(
            "owner/project", 7, invoke=fixture.invoke, host="github.com",
        )


def test_layered_review_authority_never_overrides_required_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees.providers import github

    fixture = _layered_review_fixture()
    fixture.normal_merge_fails = True
    fixture.runs[0]["conclusion"] = "failure"
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert "required check" in result and "15368" in result
    assert not any("--admin" in argv for argv in fixture.calls)


def test_layered_approval_without_codeowner_requirement_is_not_an_exception() -> None:
    fixture = _layered_review_fixture()
    fixture.rules.pop(1)
    with pytest.raises(RequiredChecksError, match="not positively identified"):
        review_exception_evidence(
            "owner/project", 7, invoke=fixture.invoke, host="github.com",
        )


def test_layered_review_policy_changes_invalidate_the_merge_evidence() -> None:
    fixture = _layered_review_fixture()
    evidence = review_exception_evidence(
        "owner/project", 7, invoke=fixture.invoke, host="github.com",
    )
    fixture.rules[2]["parameters"]["required_approving_review_count"] = 2
    with pytest.raises(RequiredChecksError, match="effective branch rules changed"):
        fixture.verify(
            expected_head_sha=HEAD, expected_rules_fingerprint=evidence.rules_fingerprint,
            expected_classic_fingerprint=evidence.classic_fingerprint,
        )


@pytest.mark.parametrize("base_ref_rules", [
    ("deletion",), ("non_fast_forward",), ("deletion", "non_fast_forward"),
])
def test_nonmerge_base_ref_protections_preserve_review_exception(
    base_ref_rules: tuple[str, ...],
) -> None:
    fixture = GitHubFixture()
    fixture.rules.extend({"type": kind} for kind in base_ref_rules)
    evidence = review_exception_evidence(
        "owner/project", 7, invoke=fixture.invoke, host="github.com",
        expected_head_sha=HEAD,
    )
    assert evidence.head_sha == HEAD
    assert all(kind in evidence.rules_fingerprint for kind in base_ref_rules)


@pytest.mark.parametrize("kind", [
    "update", "creation", "required_signatures", "merge_queue", "future_merge_condition",
])
def test_nonmerge_base_ref_protections_do_not_hide_other_requirements(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    fixture.rules.extend({"type": value} for value in ("deletion", "non_fast_forward", kind))
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert f"additional unverified merge requirement is active: {kind}" in result
    assert not any("--admin" in argv for argv in fixture.calls)


@pytest.mark.parametrize("mutation,match", [
    (lambda f: setattr(f, "review_decision", "APPROVED"), "missing required approval"),
    (lambda f: setattr(f, "bypass", "never"), "not authorized"),
    (lambda f: f.rules.__setitem__(1, {"type": "pull_request", "ruleset_id": 42,
                                      "parameters": {
                                          "require_code_owner_review": False,
                                          "allowed_merge_methods": [
                                              "merge", "squash", "rebase",
                                          ],
                                          "required_approving_review_count": 0,
                                          "required_review_thread_resolution": False,
                                          "require_last_push_approval": False,
                                      }}),
     "not positively identified"),
    (lambda f: f.reviews.insert(0, {
        "id": 0, "state": "CHANGES_REQUESTED", "user": {"login": "human"},
        "submitted_at": "2026-10-17T00:00:00Z", "commit_id": "b" * 40,
    }), "active change request"),
])
def test_review_exception_fails_closed(mutation, match):
    fixture = GitHubFixture()
    mutation(fixture)
    with pytest.raises(RequiredChecksError, match=match):
        review_exception_evidence(
            "owner/project", 7, invoke=fixture.invoke, host="github.com",
            expected_head_sha=HEAD,
        )


def test_later_other_approval_does_not_hide_human_negative():
    fixture = GitHubFixture()
    fixture.reviews = [
        {"id": 1, "state": "CHANGES_REQUESTED", "user": {"login": "human"},
         "submitted_at": "2026-10-17T00:00:00Z", "commit_id": "b" * 40},
        {"id": 2, "state": "APPROVED", "user": {"login": "copilot"},
         "submitted_at": "2026-10-18T00:00:00Z", "commit_id": HEAD},
    ]
    with pytest.raises(RequiredChecksError, match="active change request"):
        review_exception_evidence(
            "owner/project", 7, invoke=fixture.invoke, host="github.com",
            expected_head_sha=HEAD,
        )


def test_extra_required_approval_prevents_codeowner_exception():
    fixture = GitHubFixture()
    fixture.rules[1]["parameters"]["required_approving_review_count"] = 2
    with pytest.raises(RequiredChecksError, match="not the only remaining"):
        review_exception_evidence(
            "owner/project", 7, invoke=fixture.invoke, host="github.com",
            expected_head_sha=HEAD,
        )


def test_classic_review_requirement_prevents_ruleset_exception():
    fixture = GitHubFixture()
    fixture.classic_protection = {"required_pull_request_reviews": {
        "required_approving_review_count": 1,
        "require_code_owner_reviews": False,
        "require_last_push_approval": False,
    }, "required_conversation_resolution": {"enabled": False},
        "required_signatures": {"enabled": False},
        "required_linear_history": {"enabled": False}}
    with pytest.raises(RequiredChecksError, match="classic branch protection"):
        review_exception_evidence(
            "owner/project", 7, invoke=fixture.invoke, host="github.com",
            expected_head_sha=HEAD,
        )


def test_absent_classic_protection_404_is_not_an_unknown_policy():
    fixture = GitHubFixture()
    original = fixture.invoke

    def no_classic(argv, **kwargs):
        if argv[:3] == ["gh", "api", "--include"]:
            return subprocess.CompletedProcess(
                argv, 1,
                "HTTP/2.0 404 Not Found\r\ncontent-type: application/json\r\n\r\n{}",
                "gh: Branch not protected (HTTP 404)",
            )
        return original(argv, **kwargs)

    evidence = review_exception_evidence(
        "owner/project", 7, invoke=no_classic, host="github.com",
        expected_head_sha=HEAD,
    )
    assert evidence.base_ref == "dev"


def test_classic_conversation_resolution_prevents_admin_retry(monkeypatch):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    fixture.classic_protection["required_conversation_resolution"] = {"enabled": True}
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert "required_conversation_resolution" in result
    assert not any("--admin" in argv for argv in fixture.calls)


def test_locked_classic_branch_prevents_admin_retry(monkeypatch):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    fixture.classic_protection["lock_branch"] = {"enabled": True}
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert "lock_branch" in result
    assert not any("--admin" in argv for argv in fixture.calls)


def test_base_change_between_review_and_check_guards_blocks_admin_retry(monkeypatch):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    original = fixture.invoke

    def retarget_after_review(argv, **kwargs):
        if len(argv) > 2 and argv[2].endswith("/pulls/7") and fixture.pr_reads >= 3:
            fixture.after_base = "main"
        return original(argv, **kwargs)

    monkeypatch.setattr(github, "run_cli", retarget_after_review)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert "base differs from the review-policy evidence" in result
    assert not any("--admin" in argv for argv in fixture.calls)


@pytest.mark.parametrize("late_state", ["hold", "changes"])
def test_concrete_exception_probe_refuses_late_unsafe_evidence(monkeypatch, late_state):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.review_decision = "APPROVED"
    if late_state == "hold":
        original = fixture.invoke

        def held(argv, **kwargs):
            result = original(argv, **kwargs)
            if len(argv) > 2 and argv[2].endswith("/pulls/7") and result.returncode == 0:
                data = json.loads(result.stdout)
                data["labels"] = [{"name": "hold"}]
                return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")
            return result

        monkeypatch.setattr(github, "run_cli", held)
        provider = github.GitHubProvider()
        result = provider.pull_review_exception(
            "owner/project", 7, expected_head_sha=HEAD, hold_labels=("hold",),
        )
        assert result[0:2] == (False, True)
    else:
        fixture.reviews.insert(0, {
            "id": 0, "state": "CHANGES_REQUESTED", "user": {"login": "human"},
            "submitted_at": "2026-10-17T00:00:00Z", "commit_id": HEAD,
        })
        monkeypatch.setattr(github, "run_cli", fixture.invoke)
        result = github.GitHubProvider().pull_review_exception(
            "owner/project", 7, expected_head_sha=HEAD,
        )
        assert result[0:2] == (False, True)


def test_required_deployment_prevents_admin_retry(monkeypatch):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    fixture.rules.append({
        "type": "required_deployments",
        "parameters": {"required_deployment_environments": ["production"]},
    })
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull("owner/project", 7, admin=True)
    assert "required_deployments" in result
    assert not any("--admin" in argv for argv in fixture.calls)


def test_merge_method_restriction_prevents_admin_squash_retry(monkeypatch):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    fixture.rules[1]["parameters"]["allowed_merge_methods"] = ["merge"]
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, squash=True, expected_head_sha=HEAD,
    )
    assert "does not allow squash merges" in result
    assert not any("--admin" in argv for argv in fixture.calls)


@pytest.mark.parametrize("source", ["ruleset", "classic"])
def test_policy_change_between_guards_prevents_admin_retry(monkeypatch, source):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    original = fixture.invoke

    def change_after_review(argv, **kwargs):
        if source == "ruleset" and "/rules/branches/" in " ".join(argv):
            if fixture.rules_reads >= 1:
                fixture.rules.append({
                    "type": "required_deployments",
                    "parameters": {
                        "required_deployment_environments": ["production"],
                    },
                })
        if source == "classic" and argv[:3] == ["gh", "api", "--include"]:
            if fixture.protection_reads >= 1:
                fixture.classic_protection[
                    "required_conversation_resolution"
                ] = {"enabled": True}
        return original(argv, **kwargs)

    monkeypatch.setattr(github, "run_cli", change_after_review)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert "changed after review verification" in result
    assert not any("--admin" in argv for argv in fixture.calls)


@pytest.mark.parametrize("kind", ["deletion", "non_fast_forward"])
def test_nonmerge_base_rule_change_still_invalidates_policy_snapshot(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    fixture.rules.append({"type": kind})
    original = fixture.invoke

    def change_after_review(
        argv: list[str], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if "/rules/branches/" in " ".join(argv) and fixture.rules_reads >= 1:
            fixture.rules.append({"type": kind, "ruleset_id": 84})
        return original(argv, **kwargs)

    monkeypatch.setattr(github, "run_cli", change_after_review)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert "changed after review verification" in result
    assert not any("--admin" in argv for argv in fixture.calls)


@pytest.mark.parametrize("source", ["ruleset", "classic"])
def test_strict_branch_freshness_prevents_admin_retry(monkeypatch, source):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    if source == "ruleset":
        fixture.rules[0]["parameters"]["strict_required_status_checks_policy"] = True
    else:
        fixture.classic_protection["required_status_checks"]["strict"] = True
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert "up-to-date branch" in result
    assert not any("--admin" in argv for argv in fixture.calls)


@pytest.mark.parametrize("mutation", [
    {"user": None, "state": "CHANGES_REQUESTED"},
    {"user": {"login": ""}, "state": "CHANGES_REQUESTED"},
    {"user": {"login": "reviewer"}, "state": "UNKNOWN"},
    {"user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED",
     "submitted_at": None},
])
def test_malformed_review_evidence_prevents_admin_retry(monkeypatch, mutation):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    fixture.reviews[0].update(mutation)
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
    )
    assert "review history is incomplete or invalid" in result
    assert not any("--admin" in argv for argv in fixture.calls)


@pytest.mark.parametrize("mutation", ["hold", "wip", "draft"])
def test_late_metadata_gate_prevents_admin_retry(monkeypatch, mutation):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    original = fixture.invoke

    def change_after_review(argv, **kwargs):
        if len(argv) > 2 and argv[2].endswith("/pulls/7") and fixture.pr_reads >= 3:
            if mutation == "hold":
                fixture.labels = [{"name": "hold"}]
            elif mutation == "wip":
                fixture.title = "WIP: not ready"
            else:
                fixture.draft = True
        return original(argv, **kwargs)

    monkeypatch.setattr(github, "run_cli", change_after_review)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, expected_head_sha=HEAD,
        hold_labels=("hold",), wip_title_prefixes=("WIP:",),
    )
    assert not any("--admin" in argv for argv in fixture.calls)
    assert any(token in result for token in ("hold label", "WIP", "non-draft"))


@pytest.mark.parametrize("admin", [False, True])
def test_normal_merge_uses_server_verdict_without_rejecting_historical_failed_runs(monkeypatch, admin):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.fail = "policy"
    fixture.runs.append({**fixture.runs[0], "conclusion": "failure"})
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    assert github.GitHubProvider().merge_pull("owner/project", 7, admin=admin) == ""
    merges = [argv for argv in fixture.calls if argv[:3] == ["gh", "pr", "merge"]]
    assert len(merges) == 1 and "--admin" not in merges[0]
    assert merges[0][merges[0].index("--match-head-commit") + 1] == HEAD
    assert fixture.pr_reads == 1
    assert not any(argv[1:3] == ["api", "graphql"] for argv in fixture.calls)


def test_normal_merge_failure_without_admin_permission_never_escalates(monkeypatch):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, expected_head_sha=HEAD,
    )
    assert "protected merge refused" in result
    assert len(fixture.calls) == 1 and "--admin" not in fixture.calls[0]


def test_changed_head_after_normal_refusal_cannot_authorize_admin_fallback(monkeypatch):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    fixture.after_head = "b" * 40
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull("owner/project", 7, admin=True)
    assert "expected commit" in result
    assert not any("--admin" in argv for argv in fixture.calls)


@pytest.mark.parametrize("head", ["", "bad", "b" * 39])
@pytest.mark.parametrize("admin", [False, True])
def test_normal_merge_without_valid_discovered_head_fails_before_mutation(monkeypatch, head, admin):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.head = head
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull("owner/project", 7, admin=admin)
    assert "head identity" in result
    assert not any(argv[:3] == ["gh", "pr", "merge"] for argv in fixture.calls)


def test_admin_fallback_failure_preserves_normal_merge_diagnostic(monkeypatch):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = fixture.admin_merge_fails = True
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull("owner/project", 7, admin=True)
    assert "gh pr merge failed" in result and "gh admin merge failed" in result


@pytest.mark.parametrize("expected", ["short", "--admin"])
@pytest.mark.parametrize("admin", [False, True])
def test_invalid_expected_normal_head_cannot_reach_merge(monkeypatch, expected, admin):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=admin, expected_head_sha=expected,
    )
    assert "full expected head" in result and not fixture.calls


@pytest.mark.parametrize("admin", [False, True])
def test_unreadable_normal_head_never_reaches_either_merge(monkeypatch, admin):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.fail = "pr"
    monkeypatch.setattr(github, "run_cli", fixture.invoke)
    result = github.GitHubProvider().merge_pull("owner/project", 7, admin=admin)
    assert "permission denied" in result
    assert not any(argv[:3] == ["gh", "pr", "merge"] for argv in fixture.calls)


def test_normal_merge_and_admin_fallback_keep_explicit_host_and_same_head(monkeypatch):
    from agent_worktrees.providers import github

    fixture = GitHubFixture()
    fixture.normal_merge_fails = True
    environments = []

    def invoke(argv, **kwargs):
        environments.append(kwargs.get("env"))
        return fixture.invoke(argv)

    monkeypatch.setattr(github, "run_cli", invoke)
    assert github.GitHubProvider().merge_pull(
        "owner/project", 7, admin=True, api_base="https://github.example.test/api/v3",
    ) == ""
    assert all(env["GH_HOST"] == "github.example.test" for env in environments)
    merges = [argv for argv in fixture.calls if argv[:3] == ["gh", "pr", "merge"]]
    assert len(merges) == 2
    assert all(argv[argv.index("--match-head-commit") + 1] == HEAD for argv in merges)
