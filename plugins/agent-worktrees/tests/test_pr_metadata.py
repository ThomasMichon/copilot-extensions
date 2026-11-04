"""Author metadata exercises the real ADO serialization/auth transport boundary."""
from copy import deepcopy
import base64
import json
import subprocess
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import pytest

from agent_worktrees import pr_metadata, pr_publication_labels
from agent_worktrees.providers import azure_devops as azure
from agent_worktrees.providers.base import PRScope, ProviderError


ORG = "https://dev.azure.com/example"
REPO = "Source Project/repository"
PROJECT_ID = "11111111-1111-1111-1111-111111111111"
REPO_ID = "22222222-2222-2222-2222-222222222222"
REVIEWER = "33333333-3333-3333-3333-333333333333"
EXISTING = "44444444-4444-4444-4444-444444444444"
ORIGIN = pr_publication_labels.PublicationOrigin("author", ("source:author",))
MARKDOWN = '# “Résumé”\r\n\r\n"quoted" \\\\ `code`\n\n- item\n尾\n' * 1000
ARTIFACT = f"vstfs:///Git/PullRequestId/{PROJECT_ID}%2F{REPO_ID}%2F7"


class Backend:
    def __init__(self):
        self.pr = {
            "pullRequestId": 7, "status": "active", "title": "before", "description": "",
            "repository": {"id": REPO_ID, "name": "repository",
                           "project": {"id": PROJECT_ID, "name": "Source Project"}},
            "reviewers": [{"id": EXISTING, "vote": 10, "isRequired": True}],
        }
        self.wi = {"id": 42, "rev": 7, "fields": {"System.TeamProject": "Other Project"},
                   "relations": [{"rel": "Related", "url": "https://example.invalid/other"}]}
        self.labels = ["unrelated"]
        self.calls = []
        self.writes = []
        self.creates = 0
        self.fail_write = ""
        self.after_effect = False
        self.fail_readback = ""
        self.hide_reverse = False
        self.revision_race = False

    def cli(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:4] == ["az", "repos", "pr", "create"]:
            self.creates += 1
            assert "--description" not in args
            return subprocess.CompletedProcess(args, 0, json.dumps(self.pr), "")
        assert args[0] == "curl", "metadata must never invoke Git or create a PR"
        method = args[args.index("-X") + 1]
        url = args[args.index("-X") + 2]
        path = unquote(urlparse(url).path)
        body = kwargs.get("input_text")
        payload = json.loads(body) if body is not None else None
        if method != "GET":
            assert args[args.index("--data-binary") + 1] == "@-"
            assert body not in args
        key = ("work_item" if "/wit/workitems/" in path else
               "labels" if path.endswith("/labels") else
               "reviewer" if "/reviewers/" in path else
               next(iter(payload)) if payload else "")
        if method != "GET":
            self.writes.append((key, method, url, deepcopy(payload)))
            if self.fail_write == key and not self.after_effect:
                return subprocess.CompletedProcess(args, 28, "", "timed out")
        if method == "GET" and self.fail_readback and any(
                row[0] == self.fail_readback for row in self.writes):
            return subprocess.CompletedProcess(args, 28, "", "readback timed out")
        status, response = 200, {}
        if "/wit/workitems/" in path:
            assert path == "/example/_apis/wit/workitems/42"
            if method == "PATCH":
                assert "Content-Type: application/json-patch+json" in args
                assert payload[0] == {"op": "test", "path": "/rev", "value": self.wi["rev"]}
                assert payload[1]["path"] == "/relations/-"
                if self.revision_race:
                    self.wi["rev"] += 1
                    self.revision_race = False
                    return subprocess.CompletedProcess(args, 0, "{}\n412", "")
                self.wi["relations"].append(payload[1]["value"])
                self.wi["rev"] += 1
            response = self.wi
        elif path.endswith("/workitems"):
            linked = any(row.get("url") == ARTIFACT for row in self.wi["relations"])
            response = {"value": [{"id": "42"}] if linked and not self.hide_reverse else []}
        elif "/reviewers/" in path:
            assert method == "PUT" and payload == {"id": REVIEWER}
            self.pr["reviewers"].append(payload)
        elif path.endswith("/labels"):
            if method == "POST":
                self.labels.append(payload["name"])
            response = {"value": [{"name": value} for value in self.labels]}
        else:
            assert path.endswith("/pullRequests/7")
            if method == "PATCH":
                self.pr.update(payload)
            response = self.pr
        if method != "GET" and self.fail_write == key and self.after_effect:
            return subprocess.CompletedProcess(args, 28, "", "response lost")
        return subprocess.CompletedProcess(args, 0, json.dumps(response) + f"\n{status}", "")


@pytest.fixture
def backend(monkeypatch):
    server = Backend()
    monkeypatch.setattr(azure, "run_cli", server.cli)
    return server


def update(**kwargs):
    return azure.AzureDevOpsProvider().update_pull_metadata(
        REPO, 7, api_base=ORG, token="fixture-pat", **kwargs,
    )


def test_exact_markdown_additive_reviewers_cross_project_links_and_labels(backend):
    original_reviewer = deepcopy(backend.pr["reviewers"][0])
    original_relation = deepcopy(backend.wi["relations"][0])
    requested = dict(title="After “title”", description=MARKDOWN,
                     reviewers=(REVIEWER, REVIEWER), work_items=("Other Project/42",), origin=ORIGIN)
    result = update(**requested)
    assert result["ok"] and all(row["status"] == "confirmed" for row in result["actions"].values())
    assert result["provider"] == "azure-devops" and result["repo"] == REPO
    assert (result["number"], result["api_base"]) == (7, ORG)
    assert backend.pr["description"] == MARKDOWN
    assert backend.pr["reviewers"] == [original_reviewer, {"id": REVIEWER}]
    assert backend.wi["relations"] == [original_relation, {
        "rel": "ArtifactLink", "url": ARTIFACT, "attributes": {"name": "Pull Request"},
    }]
    assert backend.labels == ["unrelated", "source:author"]
    writes = len(backend.writes)
    assert update(**requested)["ok"]
    assert len(backend.writes) == writes and backend.creates == 0
    expected_auth = "Authorization: " + ("Bas" + "ic ") + base64.b64encode(b":fixture-pat").decode()
    assert all(expected_auth in args for args, _ in backend.calls)


@pytest.mark.parametrize("key,requested,action", [
    ("title", {"title": "after"}, "title"),
    ("description", {"description": MARKDOWN}, "description"),
    ("reviewer", {"reviewers": (REVIEWER,)}, "reviewer:" + REVIEWER),
    ("work_item", {"work_items": (42,)}, "work_item:42"),
    ("labels", {"origin": ORIGIN}, "publication_labels"),
])
@pytest.mark.parametrize("after_effect", [False, True])
def test_timeout_before_or_after_each_write_reconciles_same_identity(
        backend, key, requested, action, after_effect):
    backend.fail_write, backend.after_effect = key, after_effect
    result = update(**requested)
    assert result["ok"] is after_effect
    assert result["actions"][action]["status"] == ("confirmed" if after_effect else "unknown")
    assert result["publication_retained"] and result["number"] == 7 and result["url"]
    backend.fail_write = ""
    writes = len(backend.writes)
    assert update(**requested)["ok"]
    assert len(backend.writes) == writes + (0 if after_effect else 1)
    assert backend.creates == 0


def test_partial_readback_failure_retains_confirmed_actions_and_retry_does_not_repeat(backend):
    backend.fail_readback = "description"
    requested = dict(title="after", description=MARKDOWN, reviewers=(REVIEWER,))
    result = update(**requested)
    assert not result["ok"]
    assert result["actions"]["title"]["status"] == "confirmed"
    assert result["actions"]["description"]["status"] == "unknown"
    assert result["actions"]["reviewer:" + REVIEWER]["status"] == "not_attempted"
    backend.fail_readback = ""
    assert update(**requested)["ok"]
    assert [row[0] for row in backend.writes] == ["title", "description", "reviewer"]


def test_work_item_revision_conflict_requires_fresh_read_before_retry(backend):
    backend.revision_race = True
    assert not update(work_items=(42,))["ok"]
    assert len(backend.wi["relations"]) == 1
    assert update(work_items=(42,))["ok"]
    assert [row[3][0]["value"] for row in backend.writes] == [7, 8]


def test_both_relation_endpoints_must_confirm_without_duplicating_existing_relation(backend):
    backend.hide_reverse = True
    result = update(work_items=(42,))
    assert result["actions"]["work_item:42"]["status"] == "unknown"
    assert not update(work_items=(42,))["ok"]
    assert len(backend.writes) == 1
    backend.hide_reverse = False
    assert update(work_items=(42,))["ok"] and len(backend.writes) == 1


def test_wrong_work_item_project_cannot_link(backend):
    result = update(work_items=("Wrong Project/42",))
    assert not result["ok"] and not backend.writes
    assert result["actions"]["work_item:42"]["status"] == "unknown"


@pytest.mark.parametrize("field,value", [("pullRequestId", 8), ("repository", {"name": "wrong"}),
                                        ("repository", [])])
def test_wrong_or_malformed_target_identity_never_writes(backend, field, value):
    backend.pr[field] = value
    result = update(title="after")
    assert not result["ok"] and result["number"] == 7 and not backend.writes


def test_empty_description_is_distinct_from_unspecified(backend):
    backend.pr["description"] = "old body"
    assert update(title="after")["ok"] and backend.pr["description"] == "old body"
    assert update(description="")["ok"] and backend.pr["description"] == ""


@pytest.mark.parametrize("kwargs", [
    {"work_items": (True,)}, {"work_items": (0,)}, {"reviewers": ("email@example.invalid",)},
    {"title": ""}, {"description": 5},
    {"work_items": ("Other/42", "Wrong/42")}, {"work_items": ("Other/not-id",)},
])
def test_invalid_inputs_fail_before_effects(backend, kwargs):
    with pytest.raises(ValueError):
        update(**kwargs)
    assert not backend.calls


def test_create_repairs_exact_description_and_retains_identity_on_failure(backend):
    provider = azure.AzureDevOpsProvider()
    scope = PRScope(REPO, "feature", "main", "title", body=MARKDOWN, api_base=ORG)
    backend.fail_readback = "description"
    created = provider.create_pull(scope, token="fixture-pat")
    assert created.number == 7 and created.url and not created.metadata["ok"]
    assert created.metadata["actions"]["description"]["status"] == "unknown"
    backend.fail_readback = ""
    assert update(description=MARKDOWN)["ok"]
    assert backend.creates == 1 and len(backend.writes) == 1


def test_create_identity_survives_metadata_auth_failure(backend, monkeypatch):
    provider = azure.AzureDevOpsProvider()
    monkeypatch.setattr(provider, "_auth_header", lambda _: ("", "credential unavailable"))
    created = provider.create_pull(PRScope(REPO, "feature", "main", "title", api_base=ORG))
    assert created.number == 7 and created.metadata["publication_retained"]
    assert not created.metadata["ok"] and created.metadata["error"] == "credential unavailable"
    assert backend.creates == 1 and not backend.writes


@pytest.mark.parametrize("explicit_token", [None, "explicit-fixture-pat"])
def test_create_description_reuses_inherited_pat_without_azure_login(
        backend, monkeypatch, explicit_token):
    monkeypatch.setenv("AZURE_DEVOPS_EXT_PAT", "inherited-fixture-pat")
    provider = azure.AzureDevOpsProvider()
    scope = PRScope(REPO, "feature", "main", "title", body=MARKDOWN, api_base=ORG)
    created = provider.create_pull(scope, token=explicit_token)
    assert created.number == 7 and created.metadata["ok"]
    assert backend.pr["description"] == MARKDOWN and backend.creates == 1
    expected_pat = explicit_token or "inherited-fixture-pat"
    expected_auth = ("Authorization: " + ("Bas" + "ic ")
                     + base64.b64encode((":" + expected_pat).encode()).decode())
    rest_calls = [args for args, _ in backend.calls if args[0] == "curl"]
    assert rest_calls and all(expected_auth in args for args in rest_calls)
    assert not any(args[:2] == ["az", "account"] for args, _ in backend.calls)


def test_create_and_verified_description_work_with_aad_and_no_pat(backend, monkeypatch):
    monkeypatch.delenv("AZURE_DEVOPS_EXT_PAT", raising=False)
    aad_requests = []

    def cli(args, **kwargs):
        if args[:3] == ["az", "account", "get-access-token"]:
            aad_requests.append(args)
            return subprocess.CompletedProcess(args, 0, "fixture-aad\n", "")
        return backend.cli(args, **kwargs)

    monkeypatch.setattr(azure, "run_cli", cli)
    created = azure.AzureDevOpsProvider().create_pull(
        PRScope(REPO, "feature", "main", "title", body=MARKDOWN, api_base=ORG),
    )
    assert created.number == 7 and created.metadata["ok"]
    assert backend.creates == 1 and backend.pr["description"] == MARKDOWN
    assert len(aad_requests) == 1


@pytest.mark.parametrize("target_ref,expected_base", [
    ("refs/heads/release/next", "release/next"), (None, ""),
])
def test_existing_pr_lookup_preserves_authoritative_base_and_source(
        monkeypatch, target_ref, expected_base):
    data = {"pullRequestId": 7, "status": "active", "targetRefName": target_ref,
            "sourceRefName": "refs/heads/feature/one",
            "repository": {"remoteUrl": ORG + "/Source%20Project/_git/repository"}}
    monkeypatch.setattr(azure, "run_cli", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, json.dumps([data]), ""))
    result = azure.AzureDevOpsProvider().find_pull_by_head(
        REPO, "feature/one", api_base=ORG, token="fixture-pat",
    )
    assert result.number == 7 and result.base_ref == expected_base
    assert result.head_ref == "feature/one"
    assert result.head_url == data["repository"]["remoteUrl"]


def test_real_aad_header_construction_preserves_token(monkeypatch):
    monkeypatch.delenv("AZURE_DEVOPS_EXT_PAT", raising=False)
    monkeypatch.setattr(azure, "run_cli", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, "fixture-aad\n", ""))
    header, error = azure.AzureDevOpsProvider()._auth_header(None)
    assert not error
    assert header == "Authorization: " + ("Bear" + "er ") + "fixture-aad"


def test_central_operation_uses_target_binding_and_account(monkeypatch):
    from agent_worktrees import pr_config, providers

    policy = SimpleNamespace(enabled=True, provider="azure-devops", api_base=ORG)
    resolution = SimpleNamespace(resolved=True, repo_config=SimpleNamespace(pr=policy))
    config = object()
    calls = []
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda conf, repo: (calls.append((conf, repo)), resolution)[1])
    monkeypatch.setattr(providers, "get_provider",
                        lambda name: SimpleNamespace(update_pull_metadata=lambda *args, **kw: kw))
    monkeypatch.setattr(providers, "account_token_for_slug",
                        lambda repo, pr: (calls.append((repo, pr)), "target-token")[1])
    result = pr_metadata.update(config, REPO, 7, description=MARKDOWN)
    assert result["token"] == "target-token" and result["api_base"] == ORG
    assert result["description"] == MARKDOWN
    assert calls == [(config, REPO), (REPO, policy)]


def test_front_door_activates_existing_publication_policy(monkeypatch):
    from agent_worktrees import config as cfg

    config = cfg.Config(srcroot=".", machine="fixture", platform="windows", repo_name="fixture",
                        repos={"fixture": cfg.RepoConfig(
                            anchor=".", worktree_root=".",
                            pr=cfg.PRConfig(publication_origin=ORIGIN))})
    monkeypatch.setattr(pr_metadata, "update", lambda conf, repo, number, **kwargs: kwargs)
    result = pr_metadata.update_metadata(
        config, REPO, 7, body=MARKDOWN, publication_origin="author",
        work_items=("Other Project/42",),
    )
    assert result["origin"] == ORIGIN and result["description"] == MARKDOWN
    assert result["work_items"] == ("Other Project/42",)
    assert not config.default_repo.pr.publication_origin_active


def test_central_operation_refuses_disabled_target_before_credentials(monkeypatch):
    from agent_worktrees import pr_config, providers

    resolution = SimpleNamespace(
        resolved=True, repo_config=SimpleNamespace(pr=SimpleNamespace(enabled=False)),
    )
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *args: resolution)
    monkeypatch.setattr(providers, "get_provider", lambda _: pytest.fail("disabled target"))
    with pytest.raises(ValueError, match="not enabled"):
        pr_metadata.update(object(), REPO, 7, title="after")


def test_central_operation_refuses_unregistered_or_unsupported_target(monkeypatch):
    from agent_worktrees import pr_config, providers

    resolution = SimpleNamespace(resolved=False, error="unregistered")
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug", lambda *args: resolution)
    with pytest.raises(ValueError, match="unregistered"):
        pr_metadata.update(object(), REPO, 7, title="after")
    resolution.resolved = True
    resolution.repo_config = SimpleNamespace(pr=SimpleNamespace(enabled=True, provider="other"))
    monkeypatch.setattr(providers, "get_provider", lambda _: object())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *args: pytest.fail("no credentials"))
    with pytest.raises(ProviderError, match="does not support"):
        pr_metadata.update(object(), REPO, 7, title="after")


def test_authoritative_branch_observation_filters_exact_ref(monkeypatch):
    calls = []

    def cli(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({"value": [
            {"name": "refs/heads/feature/one-more", "objectId": "b" * 40},
            {"name": "refs/heads/feature/one", "objectId": "a" * 40},
        ]}) + "\n200", "")

    monkeypatch.setattr(azure, "run_cli", cli)
    result = azure.AzureDevOpsProvider().observe_branch(
        REPO, "refs/heads/feature/one", api_base=ORG, token="fixture-pat",
    )
    assert result == {"head_sha": "a" * 40, "repository": REPO, "branch": "feature/one"}
    assert len(calls) == 1 and "filter=heads%2Ffeature%2Fone" in calls[0][4]
    assert "Source%20Project" in calls[0][4]


@pytest.mark.parametrize("rows", [
    [], [{"name": "refs/heads/feature-more", "objectId": "a" * 40}],
    [{"name": "refs/heads/feature", "objectId": "0" * 40}],
    [{"name": "refs/heads/feature", "objectId": "invalid"}],
    [{"name": "refs/heads/feature", "objectId": "a" * 40}] * 2,
])
def test_branch_observation_refuses_missing_ambiguous_or_invalid_tips(monkeypatch, rows):
    monkeypatch.setattr(azure, "run_cli", lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, json.dumps({"value": rows}) + "\n200", ""))
    with pytest.raises(ProviderError, match="exact valid branch"):
        azure.AzureDevOpsProvider().observe_branch(REPO, "feature", api_base=ORG, token="fixture-pat")
