"""Provider-policy observation without credentials, publication or binding effects."""

from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import repos
from agent_worktrees.repos_registration_preflight import inspect_repository


def api(value, *, status=200, link=""):
    return SimpleNamespace(
        returncode=0 if status == 200 else 1,
        stdout=f"HTTP/2.0 {status} Response\r\n{link}\r\n\r\n{json.dumps(value)}",
        stderr="private-provider-diagnostic",
    )


def classic(*, requires_pr=None, branch="main", permission="WRITE", archived=False):
    return {"data": {"repository": {
        "id": "repository-id", "nameWithOwner": "example/knowledge",
        "isArchived": archived, "isDisabled": False, "viewerPermission": permission,
        "defaultBranchRef": {
            "name": branch, "target": {"oid": "a" * 40},
            "branchProtectionRule": None if requires_pr is None else {
                "id": "classic-rule-id", "requiresApprovingReviews": requires_pr,
            },
        },
    }}}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    root = tmp_path / "knowledge"
    root.mkdir()
    path = repos._repos_yaml_path()
    path.parent.mkdir(parents=True)
    path.write_text(
        "repos:\n  knowledge:\n    class: knowledge\n"
        "    remote: https://github.com/example/knowledge.git\n"
        "    account: selected-user\n    default_branch: registry-is-not-server\n"
        f"    {repos._current_platform()}: {json.dumps(str(root))}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def provider(monkeypatch):
    from agent_worktrees import github_account_observation

    calls = []
    responses = [
        SimpleNamespace(returncode=0, stdout="synthetic-private-token\n", stderr=""),
        api({"login": "selected-user"}),
        api(classic()), api([]), api(classic()),
    ]

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        value = responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(github_account_observation, "run_caller_owned", run)
    monkeypatch.setattr("shutil.which", lambda name: "/qualified/gh" if name == "gh" else None)
    return calls, responses


def inspect():
    return inspect_repository("knowledge")


def test_complete_default_branch_policy_uses_one_snapshot_budget_and_selected_identity(
    registry, provider, monkeypatch,
):
    calls, _ = provider
    before = registry.read_bytes()
    read = repos.read_registry
    snapshots = []

    def snapshot(**kwargs):
        snapshots.append(kwargs)
        return read(**kwargs)

    monkeypatch.setattr(repos, "read_registry", snapshot)
    monkeypatch.setenv("GH_TOKEN", "foreign-private-token")
    result = inspect()
    assert result["status"] == "observed"
    assert result["registration"]["source"] == "canonical"
    assert result["registration"]["class"] == "knowledge"
    assert result["account"] == {"login": "selected-user", "source": "repository", "status": "verified"}
    assert result["landing"]["mode"] == "direct"
    assert result["landing"]["branch"] == "main"
    assert result["landing"]["classic"] == "absent"
    assert result["landing"]["rulesets"] == "complete"
    assert snapshots == [{"strict": True}]
    assert len({id(kwargs["budget"]) for _, kwargs in calls}) == 1
    assert "GH_TOKEN" not in calls[0][1]["env"]
    assert calls[1][1]["env"]["GH_TOKEN"] == "synthetic-private-token"
    assert registry.read_bytes() == before
    for private in ("synthetic-private-token", "foreign-private-token", "private-provider-diagnostic", "repository-id"):
        assert private not in json.dumps(result)


@pytest.mark.parametrize("kind", ["classic", "ruleset", "both"])
def test_positive_pr_policy_is_not_erased_by_write_permissions(registry, provider, kind):
    _, responses = provider
    value = classic(requires_pr=kind != "ruleset", permission="ADMIN")
    responses[2] = api(value)
    responses[3] = api([{"type": "pull_request"}] if kind != "classic" else [])
    responses[4] = api(value)
    assert inspect()["landing"]["mode"] == "pull-request"


@pytest.mark.parametrize("kind", ["classic", "ruleset"])
def test_positive_pr_fact_survives_incomplete_other_policy(registry, provider, kind):
    _, responses = provider
    if kind == "classic":
        responses[2] = api(classic(requires_pr=True))
        responses[3] = api({"message": "private-policy"}, status=403)
    else:
        responses[3] = api(
            [{"type": "pull_request"}],
            link='Link: <https://api.github.com/next?page=2>; rel="next"',
        )
        responses[4] = api({"message": "private-policy"}, status=404)
    result = inspect()
    assert result["landing"]["mode"] == "pull-request"
    assert result["landing"]["observed_pr_requirement"] is True
    assert result["status"] == "partial"
    assert "private-policy" not in json.dumps(result)


@pytest.mark.parametrize("value", [
    classic(requires_pr=False), classic(permission="READ"), classic(archived=True),
])
def test_incomplete_ordinary_write_authority_is_not_direct(registry, provider, value):
    _, responses = provider
    responses[2] = api(value)
    responses[4] = api(value)
    assert inspect()["landing"]["mode"] == "unknown"


@pytest.mark.parametrize("rules, mode", [
    ([{"type": "deletion"}, {"type": "non_fast_forward"}], "direct"),
    ([{"type": "update"}], "unknown"),
    ([{"type": "future_rule"}], "unknown"),
    ([{"type": "required_status_checks"}], "unknown"),
])
def test_only_proven_ordinary_fast_forward_rules_allow_direct(registry, provider, rules, mode):
    _, responses = provider
    responses[3] = api(rules)
    assert inspect()["landing"]["mode"] == mode


@pytest.mark.parametrize("kind", ["identity", "repository", "default-branch", "head", "registry"])
def test_changed_target_does_not_return_stale_direct_policy(registry, provider, monkeypatch, kind):
    calls, responses = provider
    changed = deepcopy(classic())
    if kind == "identity":
        responses[1] = api({"login": "another-user"})
    elif kind == "repository":
        changed["data"]["repository"]["id"] = "different-id"
        responses[4] = api(changed)
    elif kind == "default-branch":
        responses[4] = api(classic(branch="changed"))
    elif kind == "head":
        changed["data"]["repository"]["defaultBranchRef"]["target"]["oid"] = "b" * 40
        responses[4] = api(changed)
    else:
        from agent_worktrees import github_account_observation

        original = github_account_observation.run_caller_owned

        def run(*args, **kwargs):
            value = original(*args, **kwargs)
            if len(calls) == 3:
                registry.write_text(registry.read_text(encoding="utf-8") + "# moved\n", encoding="utf-8")
            return value

        monkeypatch.setattr(github_account_observation, "run_caller_owned", run)
    result = inspect()
    assert result["landing"]["mode"] == "unknown"
    assert result["status"] == "partial"
    if kind == "identity":
        assert len(calls) == 2
        assert result["account"]["status"] == "mismatch"
    if kind == "registry":
        assert len(calls) == 3
        assert result["failure"]["code"] == "source-changed"


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_denied_or_ambiguous_policy_does_not_prove_absence(registry, provider, status):
    _, responses = provider
    responses[2] = api({"private": "policy-body"}, status=status)
    result = inspect()
    assert result["landing"]["mode"] == "unknown"
    assert result["failure"]["http_status"] == status
    assert result["landing"]["status"] == ("insufficient-permission" if status == 403 else "unknown")
    assert "policy-body" not in json.dumps(result)


@pytest.mark.parametrize("value", [
    {}, {"data": {"repository": None}}, {"data": {"repository": {}}, "errors": [{"message": "private"}]},
    {"data": {"repository": {**classic()["data"]["repository"], "viewerPermission": []}}},
])
def test_malformed_or_partial_graphql_is_not_policy_success(registry, provider, value):
    _, responses = provider
    responses[2] = api(value)
    result = inspect()
    assert result["landing"]["mode"] == "unknown"
    assert result["status"] == "partial"


def test_explicit_graphql_permission_failure_is_not_an_expired_credential(registry, provider):
    _, responses = provider
    responses[2] = api({"errors": [{"type": "FORBIDDEN", "message": "private-policy-detail"}]})
    result = inspect()
    assert result["account"]["status"] == "verified"
    assert result["landing"]["status"] == "insufficient-permission"
    assert result["landing"]["mode"] == "unknown"
    assert "private-policy-detail" not in json.dumps(result)


def test_paginated_active_rules_are_not_a_first_page_absence_claim(registry, provider):
    calls, responses = provider
    responses[3:] = [
        api([{"type": "deletion"}] * 100, link='Link: <https://api.github.com/next?page=2>; rel="next"'),
        api([{"type": "pull_request"}]), api(classic()),
    ]
    result = inspect()
    assert result["landing"]["mode"] == "pull-request"
    assert result["landing"]["rule_count"] == 101
    assert calls[4][0][-1].endswith("per_page=100&page=2")


def test_page_budget_exhaustion_stays_unknown(registry, provider):
    calls, responses = provider
    responses[3:] = [
        api([{"type": "deletion"}], link='Link: <https://api.github.com/next?page=2>; rel="next"')
        for _ in range(8)
    ]
    result = inspect()
    assert result["landing"]["mode"] == "unknown"
    assert result["failure"]["code"] == "page-limit"
    assert len(calls) == 11


@pytest.mark.parametrize("value, link", [
    ([{"type": None}], ""),
    ([], 'Link: <https://other.example/next>; rel="next"'),
])
def test_invalid_rules_or_pagination_never_become_an_empty_policy(registry, provider, value, link):
    _, responses = provider
    responses[3] = api(value, link=link)
    result = inspect()
    assert result["landing"]["mode"] == "unknown"
    assert result["status"] == "partial"


@pytest.mark.parametrize("kind", ["missing-account", "local-only", "azure-devops", "fallback", "unresolved"])
def test_unqualified_or_other_provider_never_reads_github_credentials(registry, provider, tmp_path, kind):
    calls, _ = provider
    text = registry.read_text(encoding="utf-8")
    if kind == "missing-account":
        text = text.replace("    account: selected-user\n", "")
    elif kind == "local-only":
        text = text.replace("    remote: https://github.com/example/knowledge.git\n", "")
    elif kind == "azure-devops":
        text = text.replace("https://github.com/example/knowledge.git", "https://dev.azure.com/example/project/_git/knowledge")
    else:
        text = "repos: {}\n"
        if kind == "fallback":
            text += f"srcroot:\n  {repos._current_platform()}: {json.dumps(str(tmp_path))}\n"
    registry.write_text(text, encoding="utf-8")
    before = registry.read_bytes()
    result = inspect()
    assert calls == []
    assert registry.read_bytes() == before
    if kind == "fallback":
        assert result["registration"]["source"] == "fallback-discovered"
        assert result["provider"]["kind"] == "unverified"
    elif kind == "azure-devops":
        assert result["provider"]["kind"] == "azure-devops"
        assert result["landing"]["status"] == "unsupported"
    elif kind == "local-only":
        assert result["landing"]["status"] == "not-applicable"


def test_cleanup_failure_is_never_relabelled_as_an_observation(registry, provider, capsys):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_worktrees.repos_registration_preflight import command

    _, responses = provider
    failure = OwnedCleanupError(None)
    responses[0] = failure
    with pytest.raises(OwnedCleanupError) as caught:
        command(["--repository", "knowledge"])
    assert caught.value is failure
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("remote", [
    "https://private-value@github.com/example/knowledge.git",
    "https://github.com/example/knowledge.git?private=value",
    "https://github.com/../knowledge.git",
])
def test_unsafe_or_ambiguous_origins_refuse_before_credentials(registry, provider, capsys, remote):
    from agent_worktrees.repos_registration_preflight import command

    calls, _ = provider
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("https://github.com/example/knowledge.git", remote),
        encoding="utf-8",
    )
    assert command(["--repository", "knowledge"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "failed"
    assert "private-value" not in captured.out
    assert calls == []


def test_new_parser_is_strict_without_widening_legacy_inspection(registry, provider, capsys):
    from agent_worktrees.repos_cli import cmd_repos_dispatch

    calls, _ = provider
    assert cmd_repos_dispatch(["registration", "preflight", "--repository", "knowledge", "--jsno"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "invalid-request"
    assert calls == []
