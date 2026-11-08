"""Exact native security semantics with synthetic provider responses, never live auth."""

import copy
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_worktrees.providers.azure_review_permissions import require_review_permissions
from test_azure_review_transport import NAMESPACE, PROJECT, REPOSITORY, namespaces


def evaluate(*, namespace=None, permissions=None, target="refs/heads/main", mutate_pr=None, denied_bit=None):
    scope = {
        "endpoint": "https://forge.invalid/org", "project_id": PROJECT,
        "repository_id": REPOSITORY, "number": 42,
    }
    pr = {
        "pullRequestId": 42, "targetRefName": target,
        "repository": {"id": REPOSITORY, "project": {"id": PROJECT}},
    }
    if mutate_pr:
        mutate_pr(pr)
    calls = []

    def read(url):
        calls.append(url)
        if "/securitynamespaces?" in url:
            return copy.deepcopy(namespaces() if namespace is None else namespace)
        if denied_bit is not None and f"/{denied_bit}?" in url:
            return {"count": 2, "value": [True, False]}
        return copy.deepcopy({"count": 2, "value": [True, True]} if permissions is None else permissions)

    require_review_permissions(read, scope=scope, pr=pr)
    return calls


@pytest.mark.parametrize("branch", ["main", "Main", "team/topic", "café/😀"])
def test_native_branch_token_preserves_case_slashes_and_utf16(branch):
    calls = evaluate(target="refs/heads/" + branch)
    assert len(calls) == 3
    permission = urlsplit(calls[-1])
    assert urlsplit(calls[1]).path == f"/org/_apis/permissions/{NAMESPACE}/2"
    assert permission.path == f"/org/_apis/permissions/{NAMESPACE}/8"
    query = parse_qs(permission.query)
    root = f"repoV2/{PROJECT}/{REPOSITORY}"
    encoded = "/".join(part.encode("utf-16-le").hex() for part in branch.split("/"))
    assert query == {
        "tokens": [root + "," + root + "/refs/heads/" + encoded + "/"],
        "alwaysAllowAdministrators": ["false"], "api-version": ["7.1"],
    }


@pytest.mark.parametrize("values", [[False, True], [True, False], [False, False]])
def test_repository_and_branch_denial_cannot_be_substituted(values):
    with pytest.raises(PermissionError):
        evaluate(permissions={"count": 2, "value": values})


@pytest.mark.parametrize("bit", [2, 8])
def test_read_and_review_permissions_must_each_be_positive(bit):
    with pytest.raises(PermissionError):
        evaluate(denied_bit=bit)


@pytest.mark.parametrize("translated", [False, True])
def test_native_translator_flag_preserves_logical_git_token(translated):
    body = namespaces()
    body["value"][0]["useTokenTranslator"] = translated
    calls = evaluate(namespace=body)
    assert len(calls) == 3
    assert parse_qs(urlsplit(calls[-1]).query)["tokens"] == [
        f"repoV2/{PROJECT}/{REPOSITORY},repoV2/{PROJECT}/{REPOSITORY}/refs/heads/6d00610069006e00/",
    ]


@pytest.mark.parametrize("body", [
    {}, {"count": True, "value": [True]}, {"count": 2, "value": [True]},
    {"count": 1, "value": [True]}, {"count": 2, "value": [True, 1]},
    {"count": 3, "value": [True, True, True]},
])
def test_missing_partial_or_truthy_permission_results_refuse(body):
    with pytest.raises(ValueError):
        evaluate(permissions=body)


@pytest.mark.parametrize("change", [
    lambda body: body.update(count=2),
    lambda body: body["value"].append(copy.deepcopy(body["value"][0])),
    lambda body: body["value"][0].update(namespaceId="not-a-uuid"),
    lambda body: body["value"][0].update(useTokenTranslator="true"),
    lambda body: body["value"][0].update(readPermission=4),
    lambda body: body["value"][0]["actions"].pop(),
    lambda body: body["value"][0]["actions"].append(copy.deepcopy(body["value"][0]["actions"][0])),
    lambda body: body["value"][0]["actions"][1].update(bit=2),
    lambda body: body["value"][0]["actions"][1].update(bit=True),
    lambda body: body["value"][0]["actions"][1].update(bit=12),
    lambda body: body["value"][0]["actions"][1].update(namespaceId=PROJECT),
])
def test_ambiguous_or_unsupported_native_semantics_refuse(change):
    body = namespaces()
    change(body)
    with pytest.raises(ValueError):
        evaluate(namespace=body)


@pytest.mark.parametrize("target", [None, "main", "refs/tags/main", "refs/heads/", "refs/heads/a//b",
                                  "refs/heads/a,b\n", "refs/heads/../main", "refs/heads/a.lock"])
def test_missing_or_unsupported_target_ref_never_falls_back_to_repository(target):
    with pytest.raises(ValueError):
        evaluate(target=target)


def test_native_target_mismatch_refuses_before_permissions():
    with pytest.raises(ValueError):
        evaluate(mutate_pr=lambda pr: pr["repository"].update(id=PROJECT))
