"""Tests for pr-* / ``repos`` provider-repo inference (#1234).

The ``pr-watch`` / ``pr-merge`` / ``pr-research`` verbs and the ``repos gh`` /
``repos account-for`` helpers may now **omit** the explicit ``owner/name``
positional -- the repo is inferred from the active project's remote. This
covers:

* :func:`git_ops.slug_from_url` -- the provider-correct URL->slug parse (the
  pure core of ``remote_slug``), for GitHub (``owner/name``) and Azure DevOps
  (``project/repo``).
* :func:`_classify_pr_operands` -- the pr-merge operand splitter that lets a
  bare ``pr-merge <#>`` resolve without mistaking the number for a slug.
* :func:`_infer_active_repo_slug` -- resolve-remote -> slug for the active repo.
* Dispatcher wiring: a bare PR number no longer errors at parse time and reaches
  inference; an explicit slug bypasses inference.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import git_ops


# --------------------------------------------------------------------------
# git_ops.slug_from_url -- provider-correct URL -> slug
# --------------------------------------------------------------------------
class TestSlugFromUrl:
    @pytest.mark.parametrize("url,slug", [
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("ssh://git@host/owner/repo", "owner/repo"),
        ("https://host/gitea/example-user/test-chamber.git",
         "example-user/test-chamber"),
        ("https://host/deep/path/org/proj.git/", "org/proj"),
        # Azure DevOps https: {project}/_git/{repo} -> project/repo.
        ("https://your-org.visualstudio.com/Developer/_git/example-repo",
         "Developer/example-repo"),
        ("https://dev.azure.com/your-org/Developer/_git/example-repo",
         "Developer/example-repo"),
        # Azure DevOps ssh (v3/{org}/{project}/{repo}) has no _git segment.
        ("git@ssh.dev.azure.com:v3/your-org/Developer/example-repo",
         "Developer/example-repo"),
    ])
    def test_parses_provider_correct_slug(self, url, slug):
        assert git_ops.slug_from_url(url) == slug

    @pytest.mark.parametrize("bad", [None, "", "   ", "single-segment"])
    def test_none_when_underivable(self, bad):
        assert git_ops.slug_from_url(bad) is None

    def test_remote_slug_delegates_to_slug_from_url(self, monkeypatch):
        # remote_slug resolves the remote *name* to a URL, then parses it.
        monkeypatch.setattr(
            git_ops, "_remote_url",
            lambda remote, *, cwd: "https://github.com/o/n.git")
        assert git_ops.remote_slug("origin", cwd=".") == "o/n"


# --------------------------------------------------------------------------
# _classify_pr_operands -- split [owner/name] [pr] in any order
# --------------------------------------------------------------------------
class TestClassifyPrOperands:
    def test_empty(self):
        assert m._classify_pr_operands([]) == (None, None)

    def test_bare_pr_number_is_never_a_repo(self):
        # The #1234 repro: a bare number must be the PR, not a bogus slug.
        assert m._classify_pr_operands(["2333486"]) == (None, 2333486)

    def test_repo_only(self):
        assert m._classify_pr_operands(["owner/name"]) == ("owner/name", None)

    def test_repo_then_pr(self):
        assert m._classify_pr_operands(["owner/name", "5"]) == ("owner/name", 5)

    def test_pr_then_repo_any_order(self):
        assert m._classify_pr_operands(["5", "owner/name"]) == ("owner/name", 5)

    def test_ado_project_repo_slug(self):
        assert m._classify_pr_operands(
            ["Developer/example-repo", "2333486"]) == ("Developer/example-repo", 2333486)

    def test_rejects_bad_slug(self):
        with pytest.raises(ValueError):
            m._classify_pr_operands(["a/b/c"])  # not exactly one '/'

    def test_rejects_duplicate_repo(self):
        with pytest.raises(ValueError):
            m._classify_pr_operands(["o/n", "o2/n2"])

    def test_rejects_duplicate_pr(self):
        with pytest.raises(ValueError):
            m._classify_pr_operands(["1", "2"])

    def test_rejects_unrecognized_token(self):
        with pytest.raises(ValueError):
            m._classify_pr_operands(["not-a-repo-or-number"])


# --------------------------------------------------------------------------
# _infer_active_repo_slug -- active project's remote -> provider slug
# --------------------------------------------------------------------------
class TestInferActiveRepoSlug:
    def _config(self):
        cfg_obj = MagicMock()
        cfg_obj.default_repo = MagicMock()
        return cfg_obj

    def test_github_remote(self, monkeypatch):
        monkeypatch.setattr(
            m, "_resolve_repo_remote",
            lambda config, repo: "https://github.com/example-user/example-repo.git")
        assert m._infer_active_repo_slug(self._config()) == "example-user/example-repo"

    def test_ado_remote(self, monkeypatch):
        monkeypatch.setattr(
            m, "_resolve_repo_remote",
            lambda config, repo: "https://your-org.visualstudio.com/Developer/_git/example-repo")
        assert m._infer_active_repo_slug(self._config()) == "Developer/example-repo"

    def test_none_when_remote_empty(self, monkeypatch):
        monkeypatch.setattr(m, "_resolve_repo_remote", lambda config, repo: "")
        assert m._infer_active_repo_slug(self._config()) is None

    def test_none_when_resolve_raises(self, monkeypatch):
        def _boom(config, repo):
            raise OSError("anchor missing")
        monkeypatch.setattr(m, "_resolve_repo_remote", _boom)
        assert m._infer_active_repo_slug(self._config()) is None


# --------------------------------------------------------------------------
# Dispatcher wiring
# --------------------------------------------------------------------------
class _Stop(Exception):
    """Sentinel to halt a dispatcher past the inference point (not caught by the
    dispatcher, which only catches ProviderError/ValueError)."""


class TestPrMergeDispatcherInference:
    def test_bare_pr_number_reaches_inference(self, monkeypatch, capsys):
        # The repro: `pr-merge 2333486` must parse (no argparse error) and, with
        # no explicit slug, attempt inference. Inference returns None here, so
        # the dispatcher fails cleanly with the infer-error (exit 2).
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: MagicMock())
        calls = {"n": 0}

        def _infer(config):
            calls["n"] += 1
            return None
        monkeypatch.setattr(m, "_infer_active_repo_slug", _infer)

        rc = m.cmd_pr_merge_dispatch(["2333486"])
        assert rc == 2
        assert calls["n"] == 1
        assert "could not infer" in capsys.readouterr().out

    def test_explicit_slug_bypasses_inference(self, monkeypatch):
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: MagicMock())

        def _must_not_call(config):
            raise AssertionError("inference must not run when a slug is explicit")
        monkeypatch.setattr(m, "_infer_active_repo_slug", _must_not_call)
        # Halt just past the inference point so we never hit the network.
        monkeypatch.setattr(m, "_pr_flow_profile",
                            lambda repo_cfg: (_ for _ in ()).throw(_Stop()))

        with pytest.raises(_Stop):
            m.cmd_pr_merge_dispatch(["owner/name", "2333486"])


class TestPrResearchDispatcherInference:
    def test_infers_when_omitted(self, monkeypatch, capsys):
        # No positional -> inference attempted; None here -> ValueError path
        # (caught by the dispatcher, exit 1).
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: MagicMock())
        calls = {"n": 0}

        def _infer(config):
            calls["n"] += 1
            return None
        monkeypatch.setattr(m, "_infer_active_repo_slug", _infer)

        rc = m.cmd_pr_research_dispatch([])
        assert rc == 1
        assert calls["n"] == 1
        assert "could not infer" in capsys.readouterr().out


class TestPrWatchDispatcherInference:
    def test_infers_when_repo_omitted(self, monkeypatch, capsys):
        # `pr-watch wait 123` -> repo inferred; None here -> exit 2.
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: MagicMock())
        calls = {"n": 0}

        def _infer(config):
            calls["n"] += 1
            return None
        monkeypatch.setattr(m, "_infer_active_repo_slug", _infer)

        rc = m.cmd_pr_watch_dispatch(["wait", "123"])
        assert rc == 2
        assert calls["n"] == 1
        assert "could not infer" in capsys.readouterr().out
