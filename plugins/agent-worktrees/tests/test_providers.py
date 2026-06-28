"""Tests for the PR provider plugins (agent_worktrees.providers)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking
from agent_worktrees.providers import (
    ProviderError,
    PRScope,
    PullResult,
    attribution,
    base,
)

# ---------------------------------------------------------------------------
# base: credential resolution + registry + scope builder
# ---------------------------------------------------------------------------

class TestResolveToken:
    def test_token_env(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")
        prcfg = cfg.PRConfig(token_env="MY_TOKEN")
        assert base.resolve_token(prcfg) == "secret123"

    def test_token_command_precedence(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "from-env")
        # `echo` is portable across the Windows (cmd.exe) and POSIX shells the
        # test may run under; `printf` is not a cmd.exe builtin.
        prcfg = cfg.PRConfig(token_env="MY_TOKEN", token_command="echo cmd-tok")
        assert base.resolve_token(prcfg) == "cmd-tok"

    def test_none_when_unset(self):
        assert base.resolve_token(cfg.PRConfig()) is None

    def test_command_failure_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "env-tok")
        prcfg = cfg.PRConfig(token_env="MY_TOKEN", token_command="exit 3")
        assert base.resolve_token(prcfg) == "env-tok"


class TestGetProvider:
    def test_known_providers(self):
        assert base.get_provider("gitea").name == "gitea"
        assert base.get_provider("github").name == "github"
        assert base.get_provider("azure-devops").name == "azure-devops"

    def test_unknown_raises(self):
        with pytest.raises(ProviderError, match="Unknown PR provider"):
            base.get_provider("bitbucket")


class TestScopeFromResult:
    def test_builds_scope_and_templates_labels(self):
        prcfg = cfg.PRConfig(api_base="https://h/gitea", labels=("auto-merge", "source:{machine}"))
        scope = base.scope_from_create_result(
            {"repo": "o/r", "branch": "feature/x", "default_branch": "master"},
            title="T", body="B", prcfg=prcfg, machine="lambda-core",
        )
        assert scope.repo == "o/r"
        assert scope.head == "feature/x"
        assert scope.base == "master"
        assert scope.api_base == "https://h/gitea"
        assert scope.labels == ("auto-merge", "source:lambda-core")


# ---------------------------------------------------------------------------
# attribution markers
# ---------------------------------------------------------------------------

class TestAttribution:
    def test_build_and_parse_round_trip(self):
        marker = attribution.build_marker(
            "wt-123", machine="lambda-core", session="sess-9", head="abc123",
        )
        fields = attribution.parse_marker(f"Some body\n\n{marker}\n")
        assert fields == {
            "worktree": "wt-123", "machine": "lambda-core",
            "session": "sess-9", "head": "abc123",
        }

    def test_append_replaces_existing_marker(self):
        m1 = attribution.build_marker("wt-1")
        m2 = attribution.build_marker("wt-2")
        body = attribution.append_marker("Hello", m1)
        body = attribution.append_marker(body, m2)
        assert body.count("agent-worktrees:source") == 1
        assert attribution.parse_marker(body)["worktree"] == "wt-2"

    def test_parse_none_when_absent(self):
        assert attribution.parse_marker("no marker here") is None


# ---------------------------------------------------------------------------
# Gitea provider (curl seam mocked)
# ---------------------------------------------------------------------------

def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class _NoSleep:
    """Stand-in for the ``time`` module so retry backoff doesn't slow tests."""

    @staticmethod
    def sleep(_seconds):
        return None


def _label_endpoint(args, label_post, applied, id_name):
    """Fake the ``/issues/{n}/labels`` endpoint for both POST and verify-GET.

    POST captures the attached ids (into ``label_post`` + ``applied``); the
    verify GET echoes back the currently-applied labels by name so the
    POST-then-verify loop in ``_attach_labels_verified`` sees them as present.
    """
    method = args[args.index("-X") + 1] if "-X" in args else "GET"
    if method == "POST":
        idx = args.index("-d")
        payload = json.loads(args[idx + 1])
        label_post.clear()
        label_post.update(payload)
        applied.update(payload.get("labels", []))
        return _proc(stdout="[]\n201")
    names = [{"name": id_name[i]} for i in sorted(applied) if i in id_name]
    return _proc(stdout=json.dumps(names) + "\n200")


class TestGiteaProvider:
    def test_create_pull_parses_url_and_number(self, monkeypatch):
        from agent_worktrees.providers import gitea
        body = json.dumps({"html_url": "https://h/gitea/o/r/pulls/42",
                            "number": 42, "state": "open"})
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            return _proc(stdout=body + "\n201")

        monkeypatch.setattr(gitea, "run_cli", fake_run)
        prov = gitea.GiteaProvider()
        scope = PRScope(repo="o/r", head="feature/x", base="master",
                        title="T", body="B", api_base="https://h/gitea")
        res = prov.create_pull(scope, token="tok")
        assert res.number == 42
        assert res.url == "https://h/gitea/o/r/pulls/42"
        # POSTs to the pulls endpoint with the token header.
        assert any("/repos/o/r/pulls" in a for call in calls for a in call)

    def test_create_pull_requires_token(self):
        from agent_worktrees.providers import gitea
        scope = PRScope(repo="o/r", head="h", base="b", title="T",
                        api_base="https://h/gitea")
        with pytest.raises(ProviderError, match="needs a token"):
            gitea.GiteaProvider().create_pull(scope, token=None)

    def test_create_pull_requires_api_base(self):
        from agent_worktrees.providers import gitea
        scope = PRScope(repo="o/r", head="h", base="b", title="T")
        with pytest.raises(ProviderError, match="api_base"):
            gitea.GiteaProvider().create_pull(scope, token="tok")

    def test_http_error_raises(self, monkeypatch):
        from agent_worktrees.providers import gitea
        monkeypatch.setattr(gitea, "run_cli",
                            lambda args, **kw: _proc(stdout="boom\n422"))
        scope = PRScope(repo="o/r", head="h", base="b", title="T",
                        api_base="https://h/gitea")
        with pytest.raises(ProviderError, match="HTTP 422"):
            gitea.GiteaProvider().create_pull(scope, token="tok")

    def test_get_pull_merged_sets_flag_and_state(self, monkeypatch):
        # Gitea reports a squash-merged PR as state "closed" + merged: true.
        from agent_worktrees.providers import gitea
        body = json.dumps({"html_url": "https://h/gitea/o/r/pulls/9",
                           "number": 9, "state": "closed", "merged": True})
        monkeypatch.setattr(gitea, "run_cli",
                            lambda args, **kw: _proc(stdout=body + "\n200"))
        res = gitea.GiteaProvider().get_pull("o/r", 9,
                                             api_base="https://h/gitea", token="tok")
        assert res.merged is True
        assert res.state == "merged"

    def test_get_pull_open_is_not_merged(self, monkeypatch):
        from agent_worktrees.providers import gitea
        body = json.dumps({"html_url": "https://h/gitea/o/r/pulls/9",
                           "number": 9, "state": "open", "merged": False})
        monkeypatch.setattr(gitea, "run_cli",
                            lambda args, **kw: _proc(stdout=body + "\n200"))
        res = gitea.GiteaProvider().get_pull("o/r", 9,
                                             api_base="https://h/gitea", token="tok")
        assert res.merged is False
        assert res.state == "open"

    def test_get_pull_closed_unmerged_is_not_merged(self, monkeypatch):
        # The #1151 shape: closed without merging -- must NOT read as merged.
        from agent_worktrees.providers import gitea
        body = json.dumps({"html_url": "https://h/gitea/o/r/pulls/9",
                           "number": 9, "state": "closed", "merged": False})
        monkeypatch.setattr(gitea, "run_cli",
                            lambda args, **kw: _proc(stdout=body + "\n200"))
        res = gitea.GiteaProvider().get_pull("o/r", 9,
                                             api_base="https://h/gitea", token="tok")
        assert res.merged is False
        assert res.state == "closed"

    def test_apply_labels_paginates_label_lookup(self, monkeypatch):
        # A label past the first label-list page must still resolve + attach.
        # Gitea returns one page (default 30) per GET; we page with limit=50.
        # Page 1 is a FULL page (50 labels) that does NOT contain the target;
        # the target only appears on page 2. A single-page fetch (the old bug)
        # would silently drop it -- e.g. a freshly-created source:<machine>.
        from agent_worktrees.providers import gitea

        page1 = [{"name": f"x{i}", "id": i} for i in range(1, 51)]   # full page
        page2 = [{"name": "source:wheatley", "id": 228}]            # short -> stop
        label_post: dict = {}
        applied: set = set()
        id_name = {228: "source:wheatley"}

        def fake_run(args, **kw):
            url = next((a for a in args if isinstance(a, str)
                        and a.startswith("http")), "")
            if "/pulls" in url:
                return _proc(stdout=json.dumps(
                    {"html_url": "https://h/gitea/o/r/pulls/42",
                     "number": 42, "state": "open"}) + "\n201")
            if "/labels?" in url and "page=1" in url:
                return _proc(stdout=json.dumps(page1) + "\n200")
            if "/labels?" in url and "page=2" in url:
                return _proc(stdout=json.dumps(page2) + "\n200")
            if "/issues/42/labels" in url:
                return _label_endpoint(args, label_post, applied, id_name)
            return _proc(stdout="[]\n200")

        monkeypatch.setattr(gitea, "time", _NoSleep())
        monkeypatch.setattr(gitea, "run_cli", fake_run)
        scope = PRScope(repo="o/r", head="feature/x", base="master", title="T",
                        body="B", api_base="https://h/gitea",
                        labels=["source:wheatley"])
        res = gitea.GiteaProvider().create_pull(scope, token="tok")
        assert res.number == 42
        # The page-2 label id was resolved and attached.
        assert label_post == {"labels": [228]}
        assert res.label_error == ""

    def test_apply_labels_retries_transient_page_failure(self, monkeypatch):
        # Regression (#1161 / observed #1319): a *single* transient failure on
        # the label-list page that carries source:<machine> (always page 2)
        # used to make _all_labels return a partial map, silently dropping the
        # required source label while auto-merge (page 1) still applied. The
        # GET must now be retried so BOTH labels resolve and attach.
        from agent_worktrees.providers import gitea

        page1 = [{"name": "auto-merge", "id": 189}] + \
            [{"name": f"x{i}", "id": i} for i in range(1, 50)]   # full page (50)
        page2 = [{"name": "source:wheatley", "id": 228}]
        label_post: dict = {}
        applied: set = set()
        id_name = {189: "auto-merge", 228: "source:wheatley"}
        page2_attempts = {"n": 0}

        def fake_run(args, **kw):
            url = next((a for a in args if isinstance(a, str)
                        and a.startswith("http")), "")
            if "/pulls" in url:
                return _proc(stdout=json.dumps(
                    {"html_url": "https://h/gitea/o/r/pulls/42",
                     "number": 42, "state": "open"}) + "\n201")
            if "/labels?" in url and "page=1" in url:
                return _proc(stdout=json.dumps(page1) + "\n200")
            if "/labels?" in url and "page=2" in url:
                page2_attempts["n"] += 1
                if page2_attempts["n"] == 1:
                    return _proc(stdout="upstream hiccup\n503")  # transient
                return _proc(stdout=json.dumps(page2) + "\n200")
            if "/issues/42/labels" in url:
                return _label_endpoint(args, label_post, applied, id_name)
            return _proc(stdout="[]\n200")  # page 3 empty -> stop

        monkeypatch.setattr(gitea, "time", _NoSleep())
        monkeypatch.setattr(gitea, "run_cli", fake_run)
        scope = PRScope(repo="o/r", head="feature/x", base="master", title="T",
                        body="B", api_base="https://h/gitea",
                        labels=["auto-merge", "source:wheatley"])
        res = gitea.GiteaProvider().create_pull(scope, token="tok")
        assert res.number == 42
        assert page2_attempts["n"] == 2  # retried once
        assert label_post == {"labels": [189, 228]}  # both, sorted
        assert res.label_error == ""

    def test_apply_labels_reattaches_until_verified(self, monkeypatch):
        # The #1326 race: the attach POST returns 200, but the brand-new PR's
        # labels don't reflect on the first read-back. The apply must re-POST
        # and re-verify until the labels are actually present -- "applied" means
        # verified-present, not "the POST returned 200".
        from agent_worktrees.providers import gitea

        page1 = [{"name": "auto-merge", "id": 189},
                 {"name": "source:lambda-core", "id": 216}]
        id_name = {189: "auto-merge", 216: "source:lambda-core"}
        label_post: dict = {}
        applied: set = set()
        verify_reads = {"n": 0}
        posts = {"n": 0}

        def fake_run(args, **kw):
            url = next((a for a in args if isinstance(a, str)
                        and a.startswith("http")), "")
            method = args[args.index("-X") + 1] if "-X" in args else "GET"
            if "/pulls" in url:
                return _proc(stdout=json.dumps(
                    {"html_url": "https://h/gitea/o/r/pulls/42",
                     "number": 42, "state": "open"}) + "\n201")
            if "/labels?" in url and "page=1" in url:
                return _proc(stdout=json.dumps(page1) + "\n200")
            if "/labels?" in url:
                return _proc(stdout="[]\n200")  # page 2 empty -> stop
            if "/issues/42/labels" in url:
                if method == "POST":
                    posts["n"] += 1
                    idx = args.index("-d")
                    payload = json.loads(args[idx + 1])
                    label_post.clear()
                    label_post.update(payload)
                    # Only the SECOND POST actually makes the labels stick.
                    if posts["n"] >= 2:
                        applied.update(payload["labels"])
                    return _proc(stdout="[]\n201")
                # verify GET
                verify_reads["n"] += 1
                names = [{"name": id_name[i]} for i in sorted(applied)]
                return _proc(stdout=json.dumps(names) + "\n200")
            return _proc(stdout="[]\n200")

        monkeypatch.setattr(gitea, "time", _NoSleep())
        monkeypatch.setattr(gitea, "run_cli", fake_run)
        scope = PRScope(repo="o/r", head="feature/x", base="master", title="T",
                        body="B", api_base="https://h/gitea",
                        labels=["auto-merge", "source:lambda-core"])
        res = gitea.GiteaProvider().create_pull(scope, token="tok")
        assert res.number == 42
        assert posts["n"] == 2          # re-POSTed after the first didn't stick
        assert res.label_error == ""    # eventually verified present

    def test_apply_labels_surfaces_error_on_persistent_failure(self, monkeypatch):
        # When a label-list page fails on every retry, _all_labels must NOT
        # silently return a partial map; create_pull surfaces label_error so the
        # dropped label is visible rather than mysterious. The PR still opens.
        from agent_worktrees.providers import gitea

        def fake_run(args, **kw):
            url = next((a for a in args if isinstance(a, str)
                        and a.startswith("http")), "")
            if "/pulls" in url:
                return _proc(stdout=json.dumps(
                    {"html_url": "https://h/gitea/o/r/pulls/42",
                     "number": 42, "state": "open"}) + "\n201")
            if "/labels?" in url:
                return _proc(stdout="down\n503")  # always transient-fails
            return _proc(stdout="[]\n200")

        monkeypatch.setattr(gitea, "time", _NoSleep())
        monkeypatch.setattr(gitea, "run_cli", fake_run)
        scope = PRScope(repo="o/r", head="feature/x", base="master", title="T",
                        body="B", api_base="https://h/gitea",
                        labels=["auto-merge", "source:wheatley"])
        res = gitea.GiteaProvider().create_pull(scope, token="tok")
        assert res.number == 42            # PR still created
        assert "label lookup failed" in res.label_error
        assert "503" in res.label_error

    def test_apply_labels_reports_label_absent_from_repo(self, monkeypatch):
        # A configured label that simply doesn't exist in the repo (e.g. a
        # source:<machine> not yet created) is reported, and the labels that DO
        # resolve are still attached.
        from agent_worktrees.providers import gitea

        page1 = [{"name": "auto-merge", "id": 189}]
        label_post: dict = {}
        applied: set = set()
        id_name = {189: "auto-merge"}

        def fake_run(args, **kw):
            url = next((a for a in args if isinstance(a, str)
                        and a.startswith("http")), "")
            if "/pulls" in url:
                return _proc(stdout=json.dumps(
                    {"html_url": "https://h/gitea/o/r/pulls/42",
                     "number": 42, "state": "open"}) + "\n201")
            if "/labels?" in url and "page=1" in url:
                return _proc(stdout=json.dumps(page1) + "\n200")
            if "/labels?" in url:
                return _proc(stdout="[]\n200")  # page 2 empty -> stop
            if "/issues/42/labels" in url:
                return _label_endpoint(args, label_post, applied, id_name)
            return _proc(stdout="[]\n200")

        monkeypatch.setattr(gitea, "time", _NoSleep())
        monkeypatch.setattr(gitea, "run_cli", fake_run)
        scope = PRScope(repo="o/r", head="feature/x", base="master", title="T",
                        body="B", api_base="https://h/gitea",
                        labels=["auto-merge", "source:ghost"])
        res = gitea.GiteaProvider().create_pull(scope, token="tok")
        assert label_post == {"labels": [189]}        # resolved one still attached
        assert "labels not found" in res.label_error
        assert "source:ghost" in res.label_error

    def test_remove_label_resolves_id_and_deletes(self, monkeypatch):
        from agent_worktrees.providers import gitea

        prov = gitea.GiteaProvider()
        calls = []

        def fake_retry(method, url, token, *, payload=None):
            calls.append((method, url, token, payload))
            if method == "GET" and "page=1" in url:
                return 200, json.dumps([{"name": "do-not-merge", "id": 321}])
            if method == "GET" and "page=2" in url:
                return 200, "[]"
            if method == "DELETE":
                return 204, ""
            return 500, "unexpected"

        monkeypatch.setattr(prov, "_curl_with_retry", fake_retry)
        err = prov.remove_label(
            "o/r", 42, "do-not-merge", api_base="https://h/gitea", token="tok",
        )
        assert err == ""
        delete_calls = [c for c in calls if c[0] == "DELETE"]
        assert len(delete_calls) == 1
        assert delete_calls[0][1].endswith("/repos/o/r/issues/42/labels/321")

    def test_remove_label_404_is_success(self, monkeypatch):
        from agent_worktrees.providers import gitea

        prov = gitea.GiteaProvider()

        def fake_retry(method, url, token, *, payload=None):
            if method == "GET" and "page=1" in url:
                return 200, json.dumps([{"name": "do-not-merge", "id": 321}])
            if method == "GET" and "page=2" in url:
                return 200, "[]"
            if method == "DELETE":
                return 404, "label not present"
            return 500, "unexpected"

        monkeypatch.setattr(prov, "_curl_with_retry", fake_retry)
        assert prov.remove_label(
            "o/r", 42, "do-not-merge", api_base="https://h/gitea", token="tok",
        ) == ""

    def test_remove_label_hard_error_surfaces(self, monkeypatch):
        from agent_worktrees.providers import gitea

        prov = gitea.GiteaProvider()

        def fake_retry(method, url, token, *, payload=None):
            if method == "GET" and "page=1" in url:
                return 200, json.dumps([{"name": "do-not-merge", "id": 321}])
            if method == "GET" and "page=2" in url:
                return 200, "[]"
            if method == "DELETE":
                return 500, "boom"
            return 500, "unexpected"

        monkeypatch.setattr(prov, "_curl_with_retry", fake_retry)
        err = prov.remove_label(
            "o/r", 42, "do-not-merge", api_base="https://h/gitea", token="tok",
        )
        assert err
        assert "HTTP 500" in err


# ---------------------------------------------------------------------------
# GitHub provider (gh seam mocked)
# ---------------------------------------------------------------------------

class TestGitHubProvider:
    def test_create_pull_parses_number_from_url(self, monkeypatch):
        from agent_worktrees.providers import github
        monkeypatch.setattr(
            github, "run_cli",
            lambda args, **kw: _proc(stdout="https://github.com/o/r/pull/7\n"),
        )
        scope = PRScope(repo="o/r", head="h", base="master", title="T")
        res = github.GitHubProvider().create_pull(scope, token=None)
        assert res.url == "https://github.com/o/r/pull/7"
        assert res.number == 7

    def test_get_pull_merged_state_sets_flag(self, monkeypatch):
        # gh reports a merged PR as state MERGED.
        from agent_worktrees.providers import github
        body = json.dumps({"url": "https://github.com/o/r/pull/7",
                           "number": 7, "state": "MERGED"})
        monkeypatch.setattr(github, "run_cli",
                            lambda args, **kw: _proc(stdout=body))
        res = github.GitHubProvider().get_pull("o/r", 7)
        assert res.merged is True
        assert res.state == "merged"

    def test_get_pull_closed_is_not_merged(self, monkeypatch):
        from agent_worktrees.providers import github
        body = json.dumps({"url": "https://github.com/o/r/pull/7",
                           "number": 7, "state": "CLOSED"})
        monkeypatch.setattr(github, "run_cli",
                            lambda args, **kw: _proc(stdout=body))
        res = github.GitHubProvider().get_pull("o/r", 7)
        assert res.merged is False
        assert res.state == "closed"


class TestAzureDevOpsProvider:
    def test_get_pull_completed_is_merged(self, monkeypatch):
        # Azure status "completed" == merged; canonicalize state to "merged".
        from agent_worktrees.providers import azure_devops as azure
        body = json.dumps({"status": "completed"})
        monkeypatch.setattr(azure, "run_cli",
                            lambda args, **kw: _proc(stdout=body))
        res = azure.AzureDevOpsProvider().get_pull(
            "proj/repo", 5, api_base="https://dev.azure.com/org")
        assert res.merged is True
        assert res.state == "merged"

    def test_get_pull_abandoned_and_active(self, monkeypatch):
        from agent_worktrees.providers import azure_devops as azure
        for status, exp_state in (("abandoned", "closed"), ("active", "open")):
            monkeypatch.setattr(
                azure, "run_cli",
                lambda args, status=status, **kw: _proc(
                    stdout=json.dumps({"status": status})
                ))
            res = azure.AzureDevOpsProvider().get_pull(
                "proj/repo", 5, api_base="https://dev.azure.com/org")
            assert res.merged is False
            assert res.state == exp_state


# ---------------------------------------------------------------------------
# create_pr auto-open wiring (fake provider)
# ---------------------------------------------------------------------------

@dataclass
class _FakeProvider:
    name: str = "gitea"
    captured: dict | None = None

    def create_pull(self, scope, *, token=None):
        self.captured = {"scope": scope, "token": token}
        return PullResult(url="https://h/gitea/ext/pulls/99", number=99, state="open")

    def get_pull(self, repo, number, *, api_base="", token=None):
        return PullResult(url="", number=number)

    def remove_label(self, repo, number, label, *, api_base="", token=None):
        return ""


@dataclass
class _FakeReadyProvider:
    name: str = "gitea"
    removed: dict | None = None

    def create_pull(self, scope, *, token=None):
        return PullResult(url="https://h/gitea/ext/pulls/99", number=99, state="open")

    def get_pull(self, repo, number, *, api_base="", token=None):
        return PullResult(url=f"https://h/gitea/{repo}/pulls/{number}",
                          number=number, state="open")

    def remove_label(self, repo, number, label, *, api_base="", token=None):
        self.removed = {
            "repo": repo, "number": number, "label": label,
            "api_base": api_base, "token": token,
        }
        return ""


class TestCreatePRAutoOpen:
    def _enable_open(self, config):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(
            repo.pr, auto_open=True, api_base="https://h/gitea",
            token_env="EXT_TOKEN", labels=("auto-merge", "source:{machine}"),
        )
        return dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)}
        )

    def test_auto_open_records_pr_and_embeds_marker(self, pr_repo, monkeypatch):
        from agent_worktrees import providers
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _FakeProvider()
        monkeypatch.setattr(providers, "get_provider", lambda name: fake)
        # pr_ops imports providers lazily inside _open_via_provider
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is True
        assert res["pr_opened"] is True
        assert res["number"] == 99
        assert res["url"] == "https://h/gitea/ext/pulls/99"

        # The worktree auto-recorded the PR (no manual set-pr).
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.active_pr().number == 99
        assert rec.active_pr().url == "https://h/gitea/ext/pulls/99"

        # The PR body carries the source-worktree attribution marker.
        scope = fake.captured["scope"]
        fields = attribution.parse_marker(scope.body)
        assert fields["worktree"] == wid
        assert fields["machine"] == "test"
        # Labels templated with the machine.
        assert "source:test" in scope.labels
        assert fake.captured["token"] == "tok"

    def test_auto_open_hold_adds_do_not_merge_label(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _FakeProvider()
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.create_pr(wid, config, title="Add feature", hold=True)
        assert res["success"] is True
        assert res["held"] is True
        assert pr_ops.HOLD_LABEL in fake.captured["scope"].labels

    def test_no_open_skips_provider(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)

        def _boom(name):
            raise AssertionError("provider should not be called with open_pr=False")

        monkeypatch.setattr("agent_worktrees.providers.get_provider", _boom)
        res = pr_ops.create_pr(wid, config, title="Add feature", open_pr=False)
        assert res["success"] is True
        assert "pr_opened" not in res

    def test_provider_failure_is_non_fatal(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")

        def _fail(name):
            raise ProviderError("gitea exploded")

        monkeypatch.setattr("agent_worktrees.providers.get_provider", _fail)
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is True  # branch still pushed
        assert res["pr_opened"] is False
        assert "gitea exploded" in res["pr_open_error"]

    def test_no_attribution_omits_marker(self, pr_repo, monkeypatch):
        from agent_worktrees.providers import attribution as attr
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _FakeProvider()
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        pr_ops.create_pr(wid, config, title="Add feature", body="Hello",
                         attribution=False)
        scope = fake.captured["scope"]
        assert attr.parse_marker(scope.body) is None
        assert scope.body == "Hello"

    def test_pr_ready_removes_hold_label(self, pr_repo, monkeypatch):
        import dataclasses

        config, wid, _wt, _ = pr_repo
        repo = config.repos["ext"]
        pr = dataclasses.replace(
            repo.pr, api_base="https://h/gitea", token_env="EXT_TOKEN",
        )
        config = dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)}
        )
        monkeypatch.setenv("EXT_TOKEN", "tok")
        pr_ops.create_pr(wid, config, title="Add feature")
        pr_ops.set_pr(
            wid, url="https://h/gitea/o/r/pulls/99", number=99, provider="gitea",
        )
        fake = _FakeReadyProvider()
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.pr_ready(wid, config, target_repo="o/r")
        assert res["success"] is True
        assert res["removed"] is True
        assert fake.removed == {
            "repo": "o/r",
            "number": 99,
            "label": pr_ops.HOLD_LABEL,
            "api_base": "https://h/gitea",
            "token": "tok",
        }


class TestAutoOpenDefault:
    def test_auto_open_is_opt_in(self):
        from agent_worktrees.config import _parse_pr
        assert cfg.PRConfig().auto_open is False
        assert _parse_pr({"provider": "gitea"}).auto_open is False
        assert _parse_pr({"auto_open": True}).auto_open is True

    def test_create_pr_skips_provider_by_default(self, pr_repo, monkeypatch):
        # Default config (no auto_open) must NOT attempt to open a PR.
        import dataclasses
        config, wid, _wt, _ = pr_repo
        repo = config.repos["ext"]
        # Re-enable a default PRConfig (auto_open defaults False).
        pr = cfg.PRConfig(enabled=True, provider="gitea", branch_prefix="feature")
        config = dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)}
        )

        def _boom(name):
            raise AssertionError("provider must not be called when auto_open is default-off")

        monkeypatch.setattr("agent_worktrees.providers.get_provider", _boom)
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is True
        assert "pr_opened" not in res
        assert "pr_open_error" not in res


# ---------------------------------------------------------------------------
# Provider PR-state reconciliation + rerun auto-open (issues #1163, #1167)
# ---------------------------------------------------------------------------

def _g(*args: str, cwd) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


class _StatefulFakeProvider:
    """A fake provider that hands out incrementing PR numbers and lets a test
    drive what ``get_pull`` reports (to simulate an externally-merged PR)."""

    name = "gitea"

    def __init__(self) -> None:
        self._next = 100
        self.pull_states: dict[int, str] = {}   # number -> state get_pull reports
        self.create_calls = 0
        self.captured: list = []

    def create_pull(self, scope, *, token=None):
        self.create_calls += 1
        n = self._next
        self._next += 1
        self.captured.append(scope)
        self.pull_states[n] = "open"
        return PullResult(
            url=f"https://h/gitea/ext/pulls/{n}", number=n, state="open",
        )

    def get_pull(self, repo, number, *, api_base="", token=None):
        return PullResult(
            url=f"https://h/gitea/ext/pulls/{number}", number=number,
            state=self.pull_states.get(number, "open"),
        )


class TestCreatePRReconcile:
    """create-pr must reconcile the active PR against the provider before
    deciding to reuse its branch -- an externally-merged PR (whose local state
    is stale ``open``) must not be reused/force-pushed (#1163)."""

    def _enable_open(self, config):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(
            repo.pr, auto_open=True, api_base="https://h/gitea",
            token_env="EXT_TOKEN", labels=("auto-merge",),
        )
        return dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)}
        )

    def test_merged_active_pr_is_reconciled_not_reused(self, pr_repo, monkeypatch):
        config, wid, wt_path, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _StatefulFakeProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake
        )

        r1 = pr_ops.create_pr(wid, config, title="Add feature")
        assert r1["pr_opened"] is True, r1
        n1 = r1["number"]

        # Externally merged (Gitea API + auto-merge label), so the provider now
        # reports the PR as merged while the LOCAL record still says 'open'.
        fake.pull_states[n1] = "merged"

        # New work on the base branch for a second PR.
        _g("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "d.txt").write_text("second\n")
        _g("add", "-A", cwd=wt_path)
        _g("commit", "-m", "second work", cwd=wt_path)

        r2 = pr_ops.create_pr(wid, config, title="Second feature")
        assert r2["success"], r2
        assert "rerun" not in r2                       # NOT the reuse path
        assert r2["branch"] == "feature/second-feature-aaaa"
        assert r2["number"] != n1                      # a fresh PR, not the merged one
        assert r2["pr_opened"] is True

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 2
        assert rec.prs[0].state == "merged"            # reconciled from provider
        assert rec.prs[0].branch == "feature/add-feature-aaaa"
        assert rec.prs[1].state == "open"
        assert rec.prs[1].branch == "feature/second-feature-aaaa"

    def test_open_active_pr_still_reused_when_provider_agrees(self, pr_repo, monkeypatch):
        # Reconciliation must NOT break the legitimate iterate-an-open-PR path:
        # when the provider confirms the active PR is still open, the branch is
        # reused (force-with-lease) and no second PR is opened.
        config, wid, wt_path, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _StatefulFakeProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake
        )

        r1 = pr_ops.create_pr(wid, config, title="Add feature")
        n1 = r1["number"]
        # Provider still reports open (default). Iterate: new work, push-changes
        # would normally do this, but a re-create on the open PR must reuse.
        _g("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "more.txt").write_text("more\n")
        _g("add", "-A", cwd=wt_path)
        _g("commit", "-m", "more work", cwd=wt_path)

        r2 = pr_ops.create_pr(wid, config, title="Add feature")
        assert r2["success"], r2
        assert r2["branch"] == "feature/add-feature-aaaa"   # reused
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 1                            # no duplicate PR
        assert fake.create_calls == 1                       # provider not re-called
        assert rec.prs[0].number == n1


class TestRerunAutoOpen:
    """The create-pr re-run path (already on the feature branch) must complete
    auto-open for a still-pending PR and surface an already-opened PR's number,
    so the agent never opens a duplicate (#1167)."""

    def _enable_open(self, config):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(
            repo.pr, auto_open=True, api_base="https://h/gitea",
            token_env="EXT_TOKEN", labels=("auto-merge",),
        )
        return dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)}
        )

    def test_rerun_opens_pending_pr(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _StatefulFakeProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake
        )

        # First run pushes the branch but does NOT open the PR.
        r1 = pr_ops.create_pr(wid, config, title="Add feature", open_pr=False)
        assert r1["success"], r1
        assert "pr_opened" not in r1

        # HEAD is now on the feature branch -> re-run should finish auto-open.
        r2 = pr_ops.create_pr(wid, config, title="Add feature")
        assert r2.get("rerun") is True, r2
        assert r2["pr_opened"] is True
        assert r2["number"]
        assert fake.create_calls == 1

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.active_pr().number == r2["number"]

    def test_rerun_surfaces_existing_pr_no_duplicate(self, pr_repo, monkeypatch):
        config, wid, _wt_path, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _StatefulFakeProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake
        )

        r1 = pr_ops.create_pr(wid, config, title="Add feature")  # opens #100
        n1 = r1["number"]
        assert n1

        # Re-run while still on the feature branch: must surface the existing PR
        # (so the caller does not open a second one), not silently omit it.
        r2 = pr_ops.create_pr(wid, config, title="Add feature")
        assert r2.get("rerun") is True, r2
        assert r2["number"] == n1
        assert r2["pr_opened"] is True
        assert fake.create_calls == 1                       # no duplicate PR opened

    def test_rerun_after_external_merge_opens_fresh_pr(self, pr_repo, monkeypatch):
        # #1336: HEAD is left on the feature branch and that branch's PR was
        # merged externally (auto-merge). A re-run must NOT surface the merged
        # PR as if freshly opened -- it must open a FRESH PR for the new commit.
        config, wid, wt_path, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _StatefulFakeProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake
        )

        r1 = pr_ops.create_pr(wid, config, title="Add feature")  # opens #100
        n1 = r1["number"]
        assert n1

        # #100 merges externally; local record is still stale 'open'. HEAD stays
        # on the feature branch, where a new commit lands.
        fake.pull_states[n1] = "merged"
        (wt_path / "more.txt").write_text("more after merge\n")
        _g("add", "-A", cwd=wt_path)
        _g("commit", "-m", "more after merge", cwd=wt_path)

        r2 = pr_ops.create_pr(wid, config, title="Add feature")
        assert r2.get("rerun") is True, r2
        assert r2["pr_opened"] is True
        assert r2["number"] != n1                     # a NEW PR, not the merged one
        assert fake.create_calls == 2                 # a second PR was opened

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        # Two PRs on the same branch: the merged one and the fresh one.
        assert len(rec.prs) == 2
        assert rec.prs[0].number == n1 and rec.prs[0].state == "merged"
        assert rec.prs[1].number == r2["number"] and rec.prs[1].state == "open"
