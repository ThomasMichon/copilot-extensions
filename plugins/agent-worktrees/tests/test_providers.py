"""Tests for the PR provider plugins (agent_worktrees.providers)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking
from agent_worktrees.pr_contract import PRSnapshot
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


class TestAccountTokenForSlug:
    """The gh-ops half of repo-scoped identity (v1: github-only)."""

    def test_explicit_config_token_wins(self, monkeypatch):
        # An explicit vault/env binding always wins -- no account lookup, no gh.
        monkeypatch.setenv("MY_TOKEN", "vault-tok")
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug",
            lambda slug: pytest.fail("should not resolve account when token set"),
        )
        prcfg = cfg.PRConfig(provider="github", token_env="MY_TOKEN")
        assert base.account_token_for_slug("example-org/proj", prcfg) == "vault-tok"

    def test_github_resolves_account_token(self, monkeypatch):
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug",
            lambda slug: "host-acct",
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.gh_token_for_account",
            lambda account: "gh-tok" if account == "host-acct" else None,
        )
        prcfg = cfg.PRConfig(provider="github")
        assert base.account_token_for_slug("example-org/proj", prcfg) == "gh-tok"

    def test_non_github_provider_is_none(self, monkeypatch):
        # v1 is github-only: other providers keep ambient-auth behavior.
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug",
            lambda slug: pytest.fail("non-github must not resolve an account"),
        )
        prcfg = cfg.PRConfig(provider="gitea")
        assert base.account_token_for_slug("example-org/proj", prcfg) is None

    def test_github_no_account_is_none(self, monkeypatch):
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug", lambda slug: None,
        )
        prcfg = cfg.PRConfig(provider="github")
        assert base.account_token_for_slug("", prcfg) is None

    def test_github_account_without_gh_token_is_none(self, monkeypatch):
        # Account resolves but gh has no token for it -> fall through to ambient.
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug",
            lambda slug: "host-acct",
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.gh_token_for_account", lambda account: None,
        )
        prcfg = cfg.PRConfig(provider="github")
        assert base.account_token_for_slug("example-org/proj", prcfg) is None


class TestGetProvider:
    def test_known_providers(self):
        assert base.get_provider("gitea").name == "gitea"
        assert base.get_provider("github").name == "github"
        assert base.get_provider("azure-devops").name == "azure-devops"

    def test_unknown_raises(self):
        with pytest.raises(ProviderError, match="Unknown PR provider"):
            base.get_provider("bitbucket")


class TestRunCli:
    def test_resolves_pathext_shim(self, monkeypatch):
        # A Windows batch shim (az -> az.cmd) is resolvable only via PATHEXT;
        # run_cli must resolve it via shutil.which, not hand the bare name to
        # CreateProcess (which only appends .exe -> WinError 2).
        captured = {}
        monkeypatch.setattr(
            base.shutil, "which",
            lambda name, path=None: r"C:\tools\az.cmd" if name == "az" else None)
        monkeypatch.setattr(
            base.subprocess, "run",
            lambda args, **kw: (captured.__setitem__("argv0", args[0]),
                                subprocess.CompletedProcess(args, 0, "ok", ""))[1])
        r = base.run_cli(["az", "--version"])
        assert captured["argv0"] == r"C:\tools\az.cmd"
        assert r.returncode == 0

    def test_never_raises_on_spawn_failure(self, monkeypatch):
        # A missing exe / spawn error must become a returncode=127 result, never
        # an exception that aborts an unrelated command (create-pr's git work).
        monkeypatch.setattr(base.shutil, "which", lambda name, path=None: None)

        def boom(args, **kw):
            raise FileNotFoundError(2, "The system cannot find the file specified")

        monkeypatch.setattr(base.subprocess, "run", boom)
        r = base.run_cli(["definitely-missing"])
        assert r.returncode == 127
        assert "cannot find the file" in r.stderr


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


class TestEnsureCliReady:
    """ensure_cli_ready() provisions the az 'azure-devops' extension for the
    ADO PR provider -- the install/adopt preflight so the first create-pr
    doesn't hit an interactive extension-install prompt under automation."""

    @staticmethod
    def _have_az(monkeypatch, azure):
        monkeypatch.setattr(azure.shutil, "which", lambda name: "az")

    def test_missing_az_reports_gap(self, monkeypatch):
        from agent_worktrees.providers import azure_devops as azure
        monkeypatch.setattr(azure.shutil, "which", lambda name: None)
        ok, msg = azure.ensure_cli_ready()
        assert ok is False
        assert "az" in msg and "azure-devops" in msg

    def test_extension_already_present(self, monkeypatch):
        from agent_worktrees.providers import azure_devops as azure
        self._have_az(monkeypatch, azure)
        monkeypatch.setattr(azure, "run_cli", lambda args, **kw: _proc(returncode=0))
        ok, msg = azure.ensure_cli_ready()
        assert ok is True
        assert "already installed" in msg

    def test_installs_when_missing(self, monkeypatch):
        from agent_worktrees.providers import azure_devops as azure
        self._have_az(monkeypatch, azure)
        calls = []

        def fake(args, **kw):
            calls.append(args)
            # `extension show` fails (absent); `extension add` succeeds.
            return _proc(returncode=1) if "show" in args else _proc(returncode=0)

        monkeypatch.setattr(azure, "run_cli", fake)
        ok, msg = azure.ensure_cli_ready()
        assert ok is True
        assert "installed the 'azure-devops' extension" in msg
        assert any("add" in a for a in calls)

    def test_install_false_reports_without_mutating(self, monkeypatch):
        from agent_worktrees.providers import azure_devops as azure
        self._have_az(monkeypatch, azure)
        added = []

        def fake(args, **kw):
            if "add" in args:
                added.append(args)
            return _proc(returncode=1)

        monkeypatch.setattr(azure, "run_cli", fake)
        ok, msg = azure.ensure_cli_ready(install=False)
        assert ok is False
        assert "missing" in msg
        assert added == []  # never attempts an install in report-only mode

    def test_install_failure_is_reported(self, monkeypatch):
        from agent_worktrees.providers import azure_devops as azure
        self._have_az(monkeypatch, azure)

        def fake(args, **kw):
            return _proc(returncode=1) if "show" in args else _proc(
                returncode=2, stderr="boom")

        monkeypatch.setattr(azure, "run_cli", fake)
        ok, msg = azure.ensure_cli_ready()
        assert ok is False
        assert "could not install" in msg and "boom" in msg


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

    def mark_ready(self, repo, number, *, api_base="", token=None, title="",
                   wip_title_prefixes=()):
        return ""


@dataclass
class _FakeReadyProvider:
    """Fake whose ``get_snapshot`` drives the un-draft / legacy-hold branches.

    ``snapshot`` is the PRSnapshot ``pr_ready`` sees; ``mark_ready_error`` lets a
    test force the un-draft primitive to fail. Records what was called.
    """

    name: str = "gitea"
    snapshot: PRSnapshot | None = None
    mark_ready_error: str = ""
    removed: dict | None = None
    marked_ready: dict | None = None

    def create_pull(self, scope, *, token=None):
        return PullResult(url="https://h/gitea/ext/pulls/99", number=99, state="open")

    def get_pull(self, repo, number, *, api_base="", token=None):
        return PullResult(url=f"https://h/gitea/{repo}/pulls/{number}",
                          number=number, state="open")

    def get_snapshot(self, repo, number, *, api_base="", token=None):
        if self.snapshot is not None:
            return self.snapshot
        return PRSnapshot(pr_state="open")

    def mark_ready(self, repo, number, *, api_base="", token=None, title="",
                   wip_title_prefixes=()):
        self.marked_ready = {
            "repo": repo, "number": number, "api_base": api_base,
            "token": token, "title": title,
            "wip_title_prefixes": tuple(wip_title_prefixes),
        }
        return self.mark_ready_error

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

    def test_auto_open_draft_marks_scope_draft(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _FakeProvider()
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.create_pr(wid, config, title="Add feature", draft=True)
        assert res["success"] is True
        assert res["draft"] is True
        # Native draft: the scope carries draft=True (no do-not-merge label).
        assert fake.captured["scope"].draft is True
        assert pr_ops.HOLD_LABEL not in fake.captured["scope"].labels

    def test_auto_open_hold_is_deprecated_alias_for_draft(self, pr_repo, monkeypatch):
        # --hold is retained as a deprecated alias for --draft.
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _FakeProvider()
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.create_pr(wid, config, title="Add feature", hold=True)
        assert res["success"] is True
        assert res["draft"] is True
        assert fake.captured["scope"].draft is True
        assert pr_ops.HOLD_LABEL not in fake.captured["scope"].labels

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

    def _ready_config(self, pr_repo):
        import dataclasses
        config, wid, _wt, _ = pr_repo
        repo = config.repos["ext"]
        pr = dataclasses.replace(
            repo.pr, api_base="https://h/gitea", token_env="EXT_TOKEN",
            wip_title_prefixes=("wip:", "[wip]"),
            hold_labels=("do-not-merge",),
        )
        config = dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)}
        )
        return config, wid

    def _track_pr(self, wid, config):
        pr_ops.create_pr(wid, config, title="Add feature")
        pr_ops.set_pr(
            wid, url="https://h/gitea/o/r/pulls/99", number=99, provider="gitea",
        )

    def test_pr_ready_undrafts_draft_pr(self, pr_repo, monkeypatch):
        config, wid = self._ready_config(pr_repo)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        self._track_pr(wid, config)
        fake = _FakeReadyProvider(
            snapshot=PRSnapshot(pr_state="open", draft=True, title="WIP: Add feature"),
        )
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.pr_ready(wid, config, target_repo="o/r")
        assert res["success"] is True
        assert res["transition"] == "undraft"
        assert res["was_draft"] is True
        # The un-draft primitive was invoked with the snapshot title + binding.
        assert fake.marked_ready["repo"] == "o/r"
        assert fake.marked_ready["number"] == 99
        assert fake.marked_ready["title"] == "WIP: Add feature"
        assert fake.marked_ready["wip_title_prefixes"] == ("wip:", "[wip]")
        # It must NOT touch the legacy hold label on the draft path.
        assert fake.removed is None

    def test_pr_ready_errors_when_mark_ready_fails(self, pr_repo, monkeypatch):
        config, wid = self._ready_config(pr_repo)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        self._track_pr(wid, config)
        fake = _FakeReadyProvider(
            snapshot=PRSnapshot(pr_state="open", draft=True, title="WIP: x"),
            mark_ready_error="un-draft failed (HTTP 500)",
        )
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.pr_ready(wid, config, target_repo="o/r")
        assert res["success"] is False
        assert "un-draft failed" in res["error"]

    def test_pr_ready_errors_when_not_draft(self, pr_repo, monkeypatch):
        # A no-op must never masquerade as success (issue #2779).
        config, wid = self._ready_config(pr_repo)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        self._track_pr(wid, config)
        fake = _FakeReadyProvider(
            snapshot=PRSnapshot(pr_state="open", draft=False, title="Add feature",
                                labels=("source:test",)),
        )
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.pr_ready(wid, config, target_repo="o/r")
        assert res["success"] is False
        assert "not in draft state" in res["error"]
        assert fake.marked_ready is None
        assert fake.removed is None

    def test_pr_ready_removes_legacy_hold_label(self, pr_repo, monkeypatch):
        # Backward-compat: a non-draft PR carrying the retired do-not-merge hold
        # label is released by removing the label (the equivalent transition).
        config, wid = self._ready_config(pr_repo)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        self._track_pr(wid, config)
        fake = _FakeReadyProvider(
            snapshot=PRSnapshot(pr_state="open", draft=False, title="Add feature",
                                labels=(pr_ops.HOLD_LABEL, "source:test")),
        )
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.pr_ready(wid, config, target_repo="o/r")
        assert res["success"] is True
        assert res["transition"] == "release-legacy-hold"
        assert res["removed"] is True
        assert fake.removed == {
            "repo": "o/r",
            "number": 99,
            "label": pr_ops.HOLD_LABEL,
            "api_base": "https://h/gitea",
            "token": "tok",
        }
        assert fake.marked_ready is None


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


class TestHeadSchemeConfig:
    def test_defaults_to_refspec(self):
        from agent_worktrees.config import _parse_pr
        # #1899: refspec is the default scheme (missing key + dataclass default).
        assert cfg.PRConfig().head_scheme == "refspec"
        assert cfg.PRConfig().head_pattern == ""
        pr = _parse_pr({"provider": "gitea"})
        assert pr.head_scheme == "refspec"
        assert pr.head_pattern == ""

    def test_parses_refspec_and_pattern(self):
        from agent_worktrees.config import _parse_pr
        pr = _parse_pr({
            "head_scheme": "refspec",
            "head_pattern": "user/{username}/{slug}-{suffix}",
        })
        assert pr.head_scheme == "refspec"
        assert pr.head_pattern == "user/{username}/{slug}-{suffix}"

    def test_unknown_scheme_falls_back_to_snapshot(self):
        from agent_worktrees.config import _parse_pr
        # A garbage value falls back to the compatible snapshot scheme, NOT the
        # refspec default (#1899) -- a typo must not silently break pushes in a
        # repo whose pre-push hook isn't refspec-ready.
        assert _parse_pr({"head_scheme": "bogus"}).head_scheme == "snapshot"
        # Explicit refspec + case-insensitive normalization still honored.
        assert _parse_pr({"head_scheme": "REFSPEC"}).head_scheme == "refspec"


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

    def test_stale_merged_and_pruned_branch_not_reused(self, pr_repo, monkeypatch):
        # #1984: the provider state query can RACE the merge (the PR is merged a
        # beat after the query) or the provider can be briefly unreachable, so
        # the reconcile leaves the record stale at 'open'. If the host deleted
        # the feature branch on merge (auto-merge + delete-branch), reusing it
        # would force-push (with lease) onto a now-absent ref -- the lease check
        # rejects it and tracking wedges at 'creating'. create-pr must instead
        # detect the branch is gone from the remote, treat the PR as terminal,
        # and open a FRESH branch/PR derived from --title.
        config, wid, wt_path, _remote_dir = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _StatefulFakeProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake
        )

        r1 = pr_ops.create_pr(wid, config, title="Add feature")   # opens #100
        n1 = r1["number"]
        assert r1["pr_opened"] is True, r1
        assert r1["branch"] == "feature/add-feature-aaaa"

        # The PR merged externally and the host auto-pruned its branch, but the
        # provider still reports 'open' (query raced the merge) so the reconcile
        # cannot catch it -- exactly the stale-'open' record #1984 hits.
        _g("push", "origin", "--delete", "feature/add-feature-aaaa", cwd=wt_path)
        assert git_ops.remote_branch_state(
            "origin", "feature/add-feature-aaaa", cwd=wt_path
        ) == "absent"
        # (fake.pull_states[n1] stays "open" -- the reconcile agrees it's live.)

        # New work for the next change, with a DIFFERENT title.
        _g("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "d.txt").write_text("second\n")
        _g("add", "-A", cwd=wt_path)
        _g("commit", "-m", "second work", cwd=wt_path)

        r2 = pr_ops.create_pr(wid, config, title="Second feature")
        assert r2["success"], r2
        assert "rerun" not in r2                            # NOT the reuse path
        assert r2["branch"] == "feature/second-feature-aaaa"   # fresh, from title
        assert r2["number"] != n1                           # a fresh PR
        assert r2["pr_opened"] is True

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 2
        assert rec.prs[0].number == n1
        assert rec.prs[0].state == "merged"                 # pruned branch -> terminal
        assert rec.prs[0].branch == "feature/add-feature-aaaa"
        assert rec.prs[1].state == "open"
        assert rec.prs[1].branch == "feature/second-feature-aaaa"

    def test_present_remote_branch_still_reused(self, pr_repo, monkeypatch):
        # The #1984 guard must be surgical: when the active PR is live AND its
        # branch still exists on the remote, the legitimate iterate path is
        # untouched (branch reused, no duplicate PR) even if a new title is
        # passed. Only a *confirmed-absent* remote branch downgrades the PR.
        config, wid, wt_path, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _StatefulFakeProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake
        )

        r1 = pr_ops.create_pr(wid, config, title="Add feature")   # opens #100
        n1 = r1["number"]
        assert git_ops.remote_branch_state(
            "origin", "feature/add-feature-aaaa", cwd=wt_path
        ) == "present"

        _g("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "more.txt").write_text("more\n")
        _g("add", "-A", cwd=wt_path)
        _g("commit", "-m", "more work", cwd=wt_path)

        r2 = pr_ops.create_pr(wid, config, title="Different title")
        assert r2["success"], r2
        assert r2["branch"] == "feature/add-feature-aaaa"   # reused, branch present
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 1
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

        # create-pr returns HEAD to the base branch (#1804); the re-run is
        # recognized from there (live PR + existing branch) and finishes
        # auto-open.
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
        # #1336: a feature branch whose PR merged externally (auto-merge), with
        # a new commit added on that branch, must open a FRESH PR on re-run --
        # never surface the merged PR as if freshly opened. create-pr returns
        # HEAD to the base branch (#1804), so this exercises the legacy on-
        # feature-branch re-run path by checking the branch out explicitly.
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

        # #100 merges externally; local record is still stale 'open'. Check out
        # the feature branch and add a new commit there.
        fake.pull_states[n1] = "merged"
        _g("checkout", "feature/add-feature-aaaa", cwd=wt_path)
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


# ---------------------------------------------------------------------------
# Azure DevOps: get_snapshot / request_auto_complete / list_open_pulls / threads
# ---------------------------------------------------------------------------

class TestAzureDevOpsCapabilities:
    ORG = "https://dev.azure.com/org"

    def _prov(self):
        from agent_worktrees.providers import azure_devops as azure
        return azure, azure.AzureDevOpsProvider()

    def test_get_snapshot_maps_votes_and_mergestatus(self, monkeypatch):
        azure, prov = self._prov()
        show = {
            "status": "active",
            "mergeStatus": "succeeded",
            "targetRefName": "refs/heads/main",
            "isDraft": False,
            "title": "My change",
            "createdBy": {"displayName": "Author"},
            "lastMergeSourceCommit": {"commitId": "abc123"},
            "reviewers": [
                {"displayName": "Approver", "vote": 10},
                {"displayName": "Rejecter", "vote": -10},
                {"displayName": "NoVote", "vote": 0},
            ],
        }
        monkeypatch.setattr(azure, "run_cli",
                            lambda args, **kw: _proc(stdout=json.dumps(show)))
        snap = prov.get_snapshot("proj/repo", 5, api_base=self.ORG, token="t")
        assert snap.pr_state == "open" and snap.merged is False
        assert snap.mergeable is True
        assert snap.base_ref == "main" and snap.head_sha == "abc123"
        assert snap.author == "Author" and snap.title == "My change"
        # 0-vote reviewer dropped; rejection sorts last (highest id) so it wins.
        assert len(snap.reviews) == 2
        from agent_worktrees.pr_contract import effective_verdict
        assert effective_verdict(snap.reviews, snap.head_sha, snap.author) == \
            "CHANGES_REQUESTED"

    def test_get_snapshot_autocomplete_marker(self, monkeypatch):
        azure, prov = self._prov()
        show = {"status": "active", "mergeStatus": "queued",
                "autoCompleteSetBy": {"id": "x"}, "reviewers": []}
        monkeypatch.setattr(azure, "run_cli",
                            lambda args, **kw: _proc(stdout=json.dumps(show)))
        snap = prov.get_snapshot("proj/repo", 5, api_base=self.ORG, token="t")
        assert "auto-complete" in snap.labels
        assert snap.mergeable is None  # queued -> not yet known

    def test_get_snapshot_completed_is_merged(self, monkeypatch):
        azure, prov = self._prov()
        monkeypatch.setattr(
            azure, "run_cli",
            lambda args, **kw: _proc(stdout=json.dumps(
                {"status": "completed", "mergeStatus": "succeeded", "reviewers": []})))
        snap = prov.get_snapshot("proj/repo", 5, api_base=self.ORG, token="t")
        assert snap.merged is True and snap.pr_state == "closed"

    def test_request_auto_complete_no_bypass_sets_autocomplete(self, monkeypatch):
        azure, prov = self._prov()
        captured = {}
        monkeypatch.setattr(
            azure, "run_cli",
            lambda args, **kw: (captured.__setitem__("args", args),
                                _proc(stdout="{}"))[1])
        err = prov.request_auto_complete(
            "proj/repo", 5, api_base=self.ORG, token="t",
            automerge_label="auto-complete", squash=True,
            delete_source_branch=True, bypass_policy=False)
        assert err == ""
        a = captured["args"]
        assert a[:4] == ["az", "repos", "pr", "update"]
        assert a[a.index("--auto-complete") + 1] == "true"
        assert a[a.index("--squash") + 1] == "true"
        assert a[a.index("--delete-source-branch") + 1] == "true"
        assert "--status" not in a  # auto-complete path, not direct completion

    def test_request_auto_complete_bypass_completes_directly(self, monkeypatch):
        # ADO rejects --bypass-policy with --auto-complete, so a bypass request
        # is a DIRECT completion (--status completed --bypass-policy), never
        # --auto-complete.
        azure, prov = self._prov()
        captured = {}
        monkeypatch.setattr(
            azure, "run_cli",
            lambda args, **kw: (captured.__setitem__("args", args),
                                _proc(stdout="{}"))[1])
        err = prov.request_auto_complete(
            "proj/repo", 5, api_base=self.ORG, token="t",
            bypass_policy=True, bypass_reason="self")
        assert err == ""
        a = captured["args"]
        assert a[a.index("--status") + 1] == "completed"
        assert a[a.index("--bypass-policy") + 1] == "true"
        assert a[a.index("--bypass-policy-reason") + 1] == "self"
        assert "--auto-complete" not in a  # mutually exclusive with bypass

    def test_request_auto_complete_failure(self, monkeypatch):
        azure, prov = self._prov()
        monkeypatch.setattr(azure, "run_cli",
                            lambda args, **kw: _proc(returncode=1, stderr="no perms"))
        err = prov.request_auto_complete("proj/repo", 5, api_base=self.ORG, token="t")
        assert "no perms" in err

    def test_list_open_pulls(self, monkeypatch):
        azure, prov = self._prov()
        monkeypatch.setattr(
            azure, "run_cli",
            lambda args, **kw: _proc(stdout=json.dumps(
                [{"pullRequestId": 3}, {"pullRequestId": 9}])))
        assert prov.list_open_pulls("proj/repo", api_base=self.ORG, token="t") == (3, 9)

    def test_get_comment_threads_filters_system(self, monkeypatch):
        azure, prov = self._prov()
        payload = {"value": [
            {"id": 1, "status": "active", "threadContext": {"filePath": "/a.py"},
             "comments": [
                 {"author": {"displayName": "Rev"}, "content": "fix",
                  "commentType": "text"},
                 {"author": {"displayName": "sys"}, "content": "voted",
                  "commentType": "system"}]},
            {"id": 2, "status": "active",
             "comments": [{"author": {"displayName": "sys"}, "content": "x",
                           "commentType": "system"}]},
            {"id": 3, "status": "fixed",
             "comments": [{"author": {"displayName": "R"}, "content": "done",
                           "commentType": "text"}]},
        ]}
        monkeypatch.setattr(azure, "run_cli",
                            lambda args, **kw: _proc(stdout=json.dumps(payload) + "\n200"))
        res = prov.get_comment_threads("proj/repo", 5, api_base=self.ORG, token="pat")
        assert [t.id for t in res.threads] == [1, 3]
        assert [t.id for t in res.active] == [1]
        assert res.threads[0].comments[0].content == "fix"

    def test_get_comment_threads_uses_aad_when_no_pat(self, monkeypatch):
        azure, prov = self._prov()
        payload = {"value": [{"id": 1, "status": "active",
                              "comments": [{"author": {"displayName": "R"},
                                            "content": "x", "commentType": "text"}]}]}

        def fake(args, **kw):
            if args[:2] == ["az", "account"]:
                return _proc(stdout="aad-tok\n")
            assert any("Bearer aad-tok" in a for a in args)
            return _proc(stdout=json.dumps(payload) + "\n200")

        monkeypatch.setattr(azure, "run_cli", fake)
        res = prov.get_comment_threads("proj/repo", 5, api_base=self.ORG, token=None)
        assert res.supported is True and [t.id for t in res.threads] == [1]

    def test_resolve_threads_patches_closed(self, monkeypatch):
        azure, prov = self._prov()
        calls = []
        monkeypatch.setattr(azure, "run_cli",
                            lambda args, **kw: (calls.append(args), _proc(stdout="\n200"))[1])
        err = prov.resolve_threads("proj/repo", 5, api_base=self.ORG, token="pat",
                                   thread_ids=(11, 12))
        assert err == "" and len(calls) == 2
        payload = json.loads(calls[0][calls[0].index("-d") + 1])
        assert payload == {"status": "closed"}


class TestGiteaThreads:
    def test_get_comment_threads(self, monkeypatch):
        from agent_worktrees.providers import gitea
        prov = gitea.GiteaProvider()
        reviews = [{"id": 7}]
        comments = [{"user": {"login": "rev"}, "body": "please fix", "path": "a.py"}]

        def fake_curl(method, url, token, *, payload=None):
            if url.endswith("/reviews"):
                return 200, json.dumps(reviews)
            if "/reviews/7/comments" in url:
                return 200, json.dumps(comments)
            return 404, ""

        monkeypatch.setattr(prov, "_curl", fake_curl)
        res = prov.get_comment_threads("o/r", 3, api_base="https://h", token="t")
        assert res.supported is True
        assert [t.id for t in res.threads] == [7]
        assert res.threads[0].status == "active"
        assert res.threads[0].comments[0].content == "please fix"

    def test_resolve_threads_reports_unsupported(self):
        from agent_worktrees.providers import gitea
        err = gitea.GiteaProvider().resolve_threads("o/r", 3, token="t")
        assert "not exposed by the Gitea REST API" in err

    def test_request_auto_complete_applies_label(self, monkeypatch):
        from agent_worktrees.providers import gitea
        prov = gitea.GiteaProvider()
        called = {}
        monkeypatch.setattr(prov, "add_label",
                            lambda repo, number, label, *, api_base="", token=None:
                            (called.update(repo=repo, label=label), "")[1])
        err = prov.request_auto_complete("o/r", 3, api_base="https://h", token="t",
                                         automerge_label="auto-merge")
        assert err == "" and called["label"] == "auto-merge"


class TestGitHubThreads:
    def test_request_auto_complete_edits_label(self, monkeypatch):
        from agent_worktrees.providers import github
        captured = {}
        monkeypatch.setattr(
            github, "run_cli",
            lambda args, **kw: (captured.__setitem__("args", args), _proc())[1])
        err = github.GitHubProvider().request_auto_complete(
            "o/r", 3, automerge_label="auto-merge", token="t")
        assert err == ""
        a = captured["args"]
        assert a[:3] == ["gh", "pr", "edit"]
        assert a[a.index("--add-label") + 1] == "auto-merge"

    def test_get_comment_threads_graphql(self, monkeypatch):
        from agent_worktrees.providers import github
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
            {"id": "T1", "isResolved": False, "isOutdated": False, "path": "a.py",
             "comments": {"nodes": [{"author": {"login": "rev"}, "body": "fix"}]}},
            {"id": "T2", "isResolved": True, "path": "b.py",
             "comments": {"nodes": [{"author": {"login": "rev"}, "body": "ok"}]}},
        ]}}}}}
        monkeypatch.setattr(github, "run_cli",
                            lambda args, **kw: _proc(stdout=json.dumps(gql)))
        res = github.GitHubProvider().get_comment_threads("o/r", 3, token="t")
        assert res.supported is True
        assert [t.status for t in res.threads] == ["active", "resolved"]
        assert [t.id for t in res.active] == [1]

    def test_resolve_threads_mutates_unresolved(self, monkeypatch):
        from agent_worktrees.providers import github
        gql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
            {"id": "T1", "isResolved": False,
             "comments": {"nodes": [{"author": {"login": "r"}, "body": "x"}]}},
            {"id": "T2", "isResolved": True,
             "comments": {"nodes": [{"author": {"login": "r"}, "body": "y"}]}},
        ]}}}}}
        mutations = []

        def fake(args, **kw):
            if "resolveReviewThread" in " ".join(args):
                mutations.append(args)
                return _proc(stdout='{"data":{}}')
            return _proc(stdout=json.dumps(gql))

        monkeypatch.setattr(github, "run_cli", fake)
        err = github.GitHubProvider().resolve_threads("o/r", 3, token="t")
        assert err == "" and len(mutations) == 1  # only the unresolved thread
