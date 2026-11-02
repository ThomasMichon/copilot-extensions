"""Publication metadata never changes create-only labels or merge consent."""
import json
import os
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace
import textwrap

import pytest

from agent_worktrees import config as cfg, pr_config, pr_ops, pr_publish, providers, tracking
from agent_worktrees import pr_publication_labels as labels
from agent_worktrees.providers.base import ProviderError
from agent_worktrees.providers.github import GitHubProvider
from agent_worktrees.providers.azure_devops import AzureDevOpsProvider


ORIGIN = {"id": "control-plane", "labels": ["source:control-plane"]}


class Backend:
    def authority_endpoint(self, api_base=""):
        return api_base or "github.com"

    def __init__(self):
        self.labels = ["unrelated"]
        self.additions = []
        self.fail = False

    def get_publication_labels(self, repo, number, **kwargs):
        assert (repo, number) == ("base/project", 7)
        return tuple(self.labels)

    def add_publication_labels(self, repo, number, requested, **kwargs):
        self.additions.append(requested)
        if self.fail:
            raise ProviderError("metadata unavailable")
        self.labels.extend(requested)


def config():
    return cfg.Config(
        srcroot=".", machine="fixture", platform="windows", repo_name="fixture",
        repos={"fixture": cfg.RepoConfig(
            anchor=".", worktree_root=".",
            pr=cfg.PRConfig(enabled=True, provider="github", repository="base/project",
                            publication_origin=labels.parse_origin(ORIGIN)),
        )},
    )


@pytest.mark.parametrize("raw", [
    {}, {"id": "x", "labels": []}, {"id": "x", "labels": "wrong"},
    {"id": "../x", "labels": ["ok"]}, {"id": "x", "labels": ["bad\nlabel"]},
    {"id": "x", "labels": [" ok"]}, {"id": "x", "labels": [1]},
])
def test_invalid_policy_is_not_silently_admitted(raw):
    with pytest.raises(ValueError):
        cfg._parse_pr({"publication_origin": raw})


def test_explicit_activation_is_required_and_not_sticky():
    conf = config()
    assert labels.activate(conf, None) is conf
    assert labels.selected(conf.default_repo.pr) is None
    conf = labels.activate(conf, "control-plane")
    assert labels.selected(conf.default_repo.pr) == labels.parse_origin(ORIGIN)
    conf = labels.activate(conf, None)
    assert labels.selected(conf.default_repo.pr) is None
    with pytest.raises(ValueError):
        labels.activate(conf, None, ("source:control-plane",))
    with pytest.raises(ValueError):
        labels.activate(conf, "other")
    conf.repos["fixture"] = replace(conf.default_repo, pr=replace(
        conf.default_repo.pr, automerge_label="source:control-plane"))
    with pytest.raises(ValueError, match="merge-consent"):
        labels.activate(conf, "control-plane")


@pytest.mark.parametrize("requested", [
    ["source:control-plane"],
    ["source:control-plane", "secondary", "source:control-plane"],
])
def test_additive_readback_idempotency_and_repair(requested):
    backend = Backend()
    backend.fail = True
    origin = labels.parse_origin({"id": "control-plane", "labels": requested})
    failed = labels.reconcile(backend, "base/project", 7, origin)
    assert not failed["ok"] and failed["publication_retained"]
    assert failed["number"] == 7 and failed["repair"] == "pr-publication-labels"
    backend.fail = False
    assert labels.reconcile(backend, "base/project", 7, origin)["ok"]
    backend.additions.clear()
    assert labels.reconcile(backend, "base/project", 7, origin)["ok"]
    assert backend.labels == ["unrelated", *dict.fromkeys(requested)]
    assert backend.additions == []


def test_readback_failure_retains_created_identity():
    backend = Backend()
    backend.add_publication_labels = lambda *a, **k: None
    result = labels.reconcile(backend, "base/project", 7, labels.parse_origin(ORIGIN))
    assert not result["ok"] and result["publication_retained"]
    assert "read-back" in result["error"]
    assert labels.reconcile(object(), "base/project", 7, None) is None


def test_existing_label_casing_is_preserved_without_readding():
    backend = Backend()
    backend.labels.append("SOURCE:CONTROL-PLANE")
    assert labels.reconcile(backend, "base/project", 7, labels.parse_origin(ORIGIN))["ok"]
    assert backend.additions == [] and backend.labels[-1] == "SOURCE:CONTROL-PLANE"


def test_origin_scope_and_outcome_round_trip():
    target = tracking.PRRecord(repo="base/project", number=7,
                               publication_origin=ORIGIN, publication_labels={"ok": False})
    restored = tracking._parse_pr_mapping(tracking._pr_to_yaml_dict(target), "")
    assert restored.publication_origin == ORIGIN
    assert restored.publication_labels == {"ok": False}
    conf = config()
    conf = labels.activate(conf, "different", ("source:other",))
    with pytest.raises(ValueError, match="frozen"):
        labels.validate_scope(conf.default_repo.pr, restored)


@pytest.mark.parametrize("state", ["open", "closed", "merged"])
def test_frozen_scope_survives_label_failure_and_repair_without_republication(tmp_path, monkeypatch, state):
    directory = tmp_path / "tracking"
    directory.mkdir()
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: directory)
    tracking.create_new_record("fixture", "worktree/fixture", str(tmp_path / "source"),
                               "fixture", "fixture", "windows", directory)
    path = directory / "fixture.yaml"
    record = tracking.load_record(path)
    record.prs.append(tracking.PRRecord(
        repo="base/project", number=7, state=state, branch="topic",
        url="https://github.com/base/project/pull/7",
    ))
    tracking.save_record(record)
    backend = Backend()
    backend.fail = True
    monkeypatch.setattr(providers, "get_provider", lambda _: backend)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: None)
    conf = labels.activate(config(), "control-plane")
    failed = labels.refresh(conf, record, record.prs[0])
    assert not failed["ok"]
    fresh = tracking.load_record(path)
    assert fresh.prs[0].publication_origin == ORIGIN and fresh.prs[0].number == 7
    backend.fail = False
    assert labels.refresh(config(), fresh, fresh.prs[0])["ok"]
    assert tracking.load_record(path).prs[0].publication_labels["ok"]


def test_provider_mismatch_refuses_before_credentials_or_scope_attachment(monkeypatch):
    conf = labels.activate(config(), "control-plane")
    target = tracking.PRRecord(repo="base/project", number=7, provider="azure-devops")

    def unexpected(*args, **kwargs):
        pytest.fail("A foreign provider binding must not resolve credentials or a backend")

    monkeypatch.setattr(providers, "get_provider", unexpected)
    monkeypatch.setattr(providers, "account_token_for_slug", unexpected)
    result = labels.refresh(conf, None, target)
    assert not result["ok"] and result["publication_retained"]
    assert "provider differs" in result["error"]
    assert not target.publication_origin


@pytest.mark.parametrize(("provider", "endpoint", "url"), [
    ("github", "https://forge.invalid", "https://other.invalid/base/project/pull/7"),
    ("azure-devops", "https://dev.azure.com/current",
     "https://dev.azure.com/previous/base/_git/project/pullrequest/7"),
    ("github", "", ""),
    ("github", "", "https://github.com/base/other/pull/7"),
    ("github", "", "https://github.com/base/project/pull/8"),
])
def test_tracked_identity_mismatch_refuses_before_credentials_or_scope_attachment(
        monkeypatch, provider, endpoint, url):
    conf = labels.activate(config(), "control-plane")
    conf.repos["fixture"] = replace(conf.default_repo, pr=replace(
        conf.default_repo.pr, provider=provider, api_base=endpoint))
    target = tracking.PRRecord(repo="base/project", number=7, provider=provider, url=url)
    backend = Backend()
    monkeypatch.setattr(providers, "get_provider", lambda _: backend)
    monkeypatch.setattr(providers, "account_token_for_slug",
                        lambda *_: pytest.fail("Mismatched identity must not resolve credentials"))
    result = labels.refresh(conf, None, target)
    assert not result["ok"] and result["publication_retained"]
    assert "owning binding endpoint" in result["error"]
    assert not target.publication_origin and not backend.additions


@pytest.mark.parametrize("reserved", [False, True])
def test_foreign_target_uses_owning_policy_endpoint_and_credentials(monkeypatch, reserved):
    conf = labels.activate(config(), "control-plane")
    owner = replace(conf.default_repo, pr=replace(
        conf.default_repo.pr, api_base="https://forge.invalid",
        automerge_label="source:control-plane" if reserved else "consent"))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *_: pr_config.ForeignRepoResolution(owner, "owning-project"))
    target = tracking.PRRecord(
        repo="base/project", number=7, provider="github",
        url="https://forge.invalid/base/project/pull/7",
    )
    backend = Backend()
    monkeypatch.setattr(providers, "get_provider", lambda _: backend)
    bindings = []
    monkeypatch.setattr(providers, "account_token_for_slug",
                        lambda repo, prcfg: bindings.append((repo, prcfg)))
    result = labels.refresh(conf, None, target)
    assert result["ok"] is not reserved
    if reserved:
        assert "merge-consent" in result["error"]
        assert not bindings and not target.publication_origin and not backend.additions
    else:
        assert bindings == [("base/project", owner.pr)]
        assert backend.additions == [("source:control-plane",)]


def test_unresolved_owner_does_not_inherit_active_binding(monkeypatch):
    conf = labels.activate(config(), "control-plane")
    target = tracking.PRRecord(repo="base/project", number=7)
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *_: pr_config.ForeignRepoResolution(None))
    monkeypatch.setattr(providers, "get_provider",
                        lambda *_: pytest.fail("Unresolved owner must not select a backend"))
    result = labels.refresh(conf, None, target)
    assert not result["ok"] and "owning repository binding" in result["error"]
    assert not target.publication_origin


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize(("repository", "path", "matches"), [
    ("My Project/My Repo", "My%20Project/_git/My%20Repo", True),
    ("My Project/My Repo", "Other%20Project/_git/My%20Repo", False),
    ("My Project/My Repo", "My%20Project/_git/Other%20Repo", False),
    ("My%20Project/My Repo", "My%2520Project/_git/My%20Repo", True),
])
def test_encoded_azure_identity_labels_and_repairs_only_the_matching_target(
        monkeypatch, frozen, repository, path, matches):
    conf = config()
    conf.repos["fixture"] = replace(conf.default_repo, pr=replace(
        conf.default_repo.pr, provider="azure-devops", repository=repository,
        api_base="https://dev.azure.com/own"))
    if not frozen:
        conf = labels.activate(conf, "control-plane")
    target = tracking.PRRecord(
        repo=repository, number=7, provider="azure-devops",
        url=f"https://dev.azure.com/own/{path}/pullrequest/7",
        publication_origin=ORIGIN if frozen else {},
    )
    backend = Backend()

    def read(repo, number, **kwargs):
        assert (repo, number, kwargs["api_base"]) == (repository, 7, "https://dev.azure.com/own")
        return tuple(backend.labels)

    backend.get_publication_labels = read
    monkeypatch.setattr(providers, "get_provider", lambda _: backend)
    bindings = []
    monkeypatch.setattr(providers, "account_token_for_slug",
                        lambda repo, prcfg: bindings.append((repo, prcfg)))
    result = labels.refresh(conf, None, target)
    assert result["ok"] is matches
    if matches:
        assert bindings == [(repository, conf.default_repo.pr)]
        assert backend.labels == ["unrelated", "source:control-plane"]
    else:
        assert not bindings and not backend.additions
        assert "owning binding endpoint" in result["error"]
        assert target.publication_origin == (ORIGIN if frozen else {})


def test_successful_push_attaches_origin_to_preexisting_pr(monkeypatch):
    backend = Backend()
    conf = config()
    conf = labels.activate(conf, "control-plane")
    target = tracking.PRRecord(repo="base/project", number=7, head_repo="fork/project",
                               url="https://github.com/base/project/pull/7")
    order = []
    monkeypatch.setattr(pr_publish, "_persist_head", lambda *a, **k: order.append("pushed"))
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    monkeypatch.setattr(providers, "get_provider", lambda _: backend)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda repo, _: None)
    pr_publish.record_pushed_head(conf, None, "fixture", target, "a" * 40)
    assert order == ["pushed"]
    assert target.publication_origin == ORIGIN
    assert labels.last_result.get()["ok"]
    assert backend.additions == [("source:control-plane",)]


def test_reused_local_pr_labels_without_another_create(monkeypatch):
    backend = Backend()
    conf = config()
    conf = labels.activate(conf, "control-plane")
    monkeypatch.setattr(providers, "get_provider", lambda _: backend)
    monkeypatch.setattr(providers, "account_token_for_slug", lambda repo, _: None)
    target = tracking.PRRecord(repo="base/project", number=7, url="https://github.com/base/project/pull/7")
    result = {"success": True}
    pr_ops._finish_auto_open(
        result, conf, None, target, title="fixture", body="", worktree_id="fixture",
        head_sha="a" * 40, open_pr=True, draft=False, attribution=False,
        prcfg=conf.default_repo.pr,
    )
    assert result["number"] == 7 and result["publication_labels"]["ok"]


def test_github_label_transport_is_add_only_and_host_scoped(monkeypatch):
    from agent_worktrees.providers import github
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps([[{"name": "unrelated"}]]), "")

    monkeypatch.setattr(github, "run_cli", run)
    provider = GitHubProvider()
    assert provider.get_publication_labels("base/project", 7, api_base="https://forge.invalid") == ("unrelated",)
    provider.add_publication_labels("base/project", 7, ("source:control-plane", "secondary"),
                                    api_base="https://forge.invalid", token="fixture")
    argv, kwargs = calls[-1]
    assert "--method" in argv and "POST" in argv and "DELETE" not in argv and "PATCH" not in argv
    assert "repos/base/project/issues/7/labels" in argv and "labels[]=source:control-plane" in argv
    assert "labels[]=secondary" in argv
    assert kwargs["env"]["GH_HOST"] == "forge.invalid"


def test_azure_labels_use_per_label_post_and_encoded_target(monkeypatch):
    provider = AzureDevOpsProvider()
    calls = []
    monkeypatch.setattr(provider, "_auth_header", lambda _: ("fixture", ""))

    def request(method, url, auth, payload=None):
        calls.append((method, url, payload))
        return 200, json.dumps({"value": [{"name": "unrelated"}]})

    monkeypatch.setattr(provider, "_rest_call", request)
    assert provider.get_publication_labels("base project/repo", 7, api_base="https://dev.azure.com/example") == ("unrelated",)
    provider.add_publication_labels("base project/repo", 7, ("source:control-plane", "secondary"),
                                    api_base="https://dev.azure.com/example")
    assert calls[-1][0] == "POST"
    assert "/base%20project/_apis/git/repositories/repo/pullRequests/7/labels?" in calls[-1][1]
    assert [json.loads(payload) for method, _, payload in calls if method == "POST"] == [
        {"name": "source:control-plane"}, {"name": "secondary"},
    ]


def test_unsupported_provider_is_a_repairable_partial_result():
    result = labels.reconcile(SimpleNamespace(), "base/project", 7, labels.parse_origin(ORIGIN))
    assert not result["ok"] and result["number"] == 7 and result["publication_retained"]


def test_real_cli_subprocess_repairs_with_fake_backend_without_publication(tmp_path):
    from agent_procutil import no_window_kwargs

    path = tmp_path / "policy.json"
    path.write_text(json.dumps({
        "repo_name": "fixture",
        "repos": {"fixture": {"anchor": str(tmp_path), "worktree_root": str(tmp_path),
                             "pr": {"enabled": True, "provider": "github"}}},
    }), "utf-8")
    script = textwrap.dedent("""
        import sys
        from agent_worktrees import config as cfg, pr_config, providers
        from agent_worktrees.__main__ import _dispatch_lazy
        cfg.set_active_project("fixture")
        class Backend:
            labels = ["unrelated"]
            def get_pull(self, *a, **k):
                return object()
            def get_publication_labels(self, *a, **k):
                return tuple(self.labels)
            def add_publication_labels(self, repo, number, labels, **k):
                assert (repo, number) == ("base/project", 7)
                self.labels.extend(labels)
        backend = Backend()
        providers.get_provider = lambda _: backend
        providers.account_token_for_slug = lambda *a: None
        pr_config.resolve_repo_config_for_slug = lambda config, slug: pr_config.ForeignRepoResolution(
            config.default_repo, slug, same_as_active=True)
        args = ["pr-publication-labels", "--config", sys.argv[1], "--repo", "base/project",
                "--number", "7", "--publication-origin", "control-plane",
                "--publication-label", "source:control-plane", "--json"]
        for _ in range(2):
            assert _dispatch_lazy(args[0], args) == 0
        assert backend.labels == ["unrelated", "source:control-plane"]
        missing = ["pr-publication-labels", "--config", sys.argv[1], "--json"]
        assert _dispatch_lazy(missing[0], missing) == 1
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)], cwd=tmp_path, env=dict(os.environ),
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    decoder = json.JSONDecoder()
    first, end = decoder.raw_decode(result.stdout.lstrip())
    remaining = result.stdout.lstrip()[end:].lstrip()
    second, end = decoder.raw_decode(remaining)
    missing, end = decoder.raw_decode(remaining[end:].lstrip())
    assert first["ok"] and second["ok"] and second["added"] == []
    assert "worktree ID" in missing["error"]
