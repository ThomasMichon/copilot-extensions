"""Tests for agent_worktrees.git_ops — git wrappers and classification."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from agent_worktrees.git_ops import (
    GitError,
    WorktreeState,
    WorktreeStateInfo,
    git,
    is_cwd_inside,
    refine_state_with_session,
    resolve_to_anchor,
)

# ---------------------------------------------------------------------------
# git() wrapper
# ---------------------------------------------------------------------------

class TestGitWrapper:
    def test_successful_command(self, tmp_path: Path):
        """git() should capture stdout from a successful command."""
        # Use a real git command that works without a repo
        result = git("--version")
        assert result.returncode == 0
        assert "git version" in result.stdout

    def test_raises_git_error_on_failure(self, tmp_path: Path):
        """git() should raise GitError when check=True and command fails."""
        with pytest.raises(GitError) as exc_info:
            git("log", cwd=str(tmp_path))  # valid dir, not a git repo
        assert exc_info.value.returncode != 0

    def test_no_raise_when_check_false(self, tmp_path: Path):
        """git() with check=False should return result even on failure."""
        result = git("log", cwd=str(tmp_path), check=False)
        assert result.returncode != 0

    def test_git_error_attributes(self, tmp_path: Path):
        try:
            git("log", cwd=str(tmp_path))
        except GitError as e:
            assert e.returncode != 0
            assert isinstance(e.cmd, list)
            assert isinstance(e.stderr, str)


class TestNoHooks:
    """#3707: the plugin's mechanical git ops (squash re-commit / rebase / push)
    disable a repo's client-side guard hooks via ``-c core.hooksPath=`` so a
    branch-protection pre-commit/pre-push/pre-rebase can't block or corrupt the
    flow. Server-side protection is unaffected (not a client hook)."""

    def _capture(self, monkeypatch):
        import subprocess as _sp
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return _sp.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(go.subprocess, "run", fake_run)
        return seen

    def test_no_hooks_prepends_config(self, monkeypatch):
        seen = self._capture(monkeypatch)
        go.git("commit", "-m", "x", no_hooks=True)
        assert seen["cmd"][:3] == ["git", "-c", f"core.hooksPath={go._NO_HOOKS_PATH}"]
        assert seen["cmd"][3:] == ["commit", "-m", "x"]

    def test_default_keeps_hooks_enabled(self, monkeypatch):
        seen = self._capture(monkeypatch)
        go.git("commit", "-m", "x")
        assert seen["cmd"] == ["git", "commit", "-m", "x"]
        assert "core.hooksPath" not in " ".join(seen["cmd"])

    def test_squash_recommit_bypasses_hooks(self, monkeypatch):
        # The squash re-commit must run with hooks disabled (the #3707 root
        # cause: a branch-guard pre-commit blocking the soft-reset re-commit).
        calls = []

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False):
            calls.append((args, no_hooks))
            # merge-base / rev-list --count 2 so we reach the commit path.
            if args[:1] == ("rev-list",):
                out = "2"
            elif args[:1] == ("merge-base",):
                out = "deadbeef"
            elif args[:1] == ("rev-parse",):
                out = "cafef00d"
            else:
                out = ""
            return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        ok, reason = go.squash_branch("origin/master", "squashed", cwd=".")
        assert ok and reason is None
        commit_calls = [c for c in calls if c[0][:1] == ("commit",)]
        assert commit_calls and all(nh for _, nh in commit_calls)

    def test_rebase_bypasses_hooks(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(go, "git", lambda *a, cwd=None, check=True,
                            capture=True, timeout=None, no_hooks=False: (
            seen.update(args=a, no_hooks=no_hooks),
            types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
        assert go.rebase("origin/master", cwd=".") is True
        assert seen["args"][:1] == ("rebase",)
        assert seen["no_hooks"] is True

    def test_push_bypasses_hooks(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        seen = {}
        monkeypatch.setattr(go, "git", lambda *a, cwd=None, check=True,
                            capture=True, timeout=None, no_hooks=False: (
            seen.update(args=a, no_hooks=no_hooks),
            types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
        assert bool(go.push("origin", "main", cwd=".")) is True
        assert seen["args"][:1] == ("push",)
        assert seen["no_hooks"] is True


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_is_cwd_inside_same_dir(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert is_cwd_inside(str(tmp_path)) is True

    def test_is_cwd_inside_subdir(self, tmp_path: Path, monkeypatch):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        monkeypatch.chdir(subdir)
        assert is_cwd_inside(str(tmp_path)) is True

    def test_is_cwd_outside(self, tmp_path: Path, monkeypatch):
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.chdir(other)
        assert is_cwd_inside(str(tmp_path / "elsewhere")) is False

    def test_resolve_to_anchor_with_git_dir(self, tmp_path: Path):
        """If .git is a directory, return path unchanged."""
        (tmp_path / ".git").mkdir()
        assert resolve_to_anchor(tmp_path) == tmp_path

    def test_resolve_to_anchor_no_git(self, tmp_path: Path):
        """If no .git at all, return path unchanged (fallback)."""
        assert resolve_to_anchor(tmp_path) == tmp_path


# ---------------------------------------------------------------------------
# Cross-account authentication (#29)
# ---------------------------------------------------------------------------

from agent_worktrees import git_ops as go  # noqa: E402


class TestCrossAccountAuth:
    @pytest.mark.parametrize("url,owner", [
        ("https://github.com/ThomasMichon/copilot-extensions.git", "ThomasMichon"),
        ("https://github.com/octo-org/repo", "octo-org"),
        ("git@github.com:ThomasMichon/copilot-extensions.git", "ThomasMichon"),
        ("ssh://git@github.com/owner/repo.git", "owner"),
        ("https://gitlab.com/owner/repo.git", None),
        ("/local/path/repo", None),
    ])
    def test_parse_github_owner(self, url, owner):
        assert go._parse_github_owner(url) == owner

    @pytest.mark.parametrize("url,slug", [
        ("https://host/gitea/example-user/test-chamber.git", "example-user/test-chamber"),
        ("https://github.com/owner/copilot-extensions.git", "owner/copilot-extensions"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("ssh://git@host/owner/repo", "owner/repo"),
        ("https://host/deep/path/org/proj.git/", "org/proj"),
        # Azure DevOps https remotes: {project}/_git/{repo} -> project/repo.
        ("https://your-org.visualstudio.com/Developer/_git/example-marketplace",
         "Developer/example-marketplace"),
        ("https://dev.azure.com/your-org/Developer/_git/example-marketplace",
         "Developer/example-marketplace"),
        # Azure DevOps ssh (v3/{org}/{project}/{repo}) has no _git segment.
        ("git@ssh.dev.azure.com:v3/your-org/Developer/example-marketplace",
         "Developer/example-marketplace"),
    ])
    def test_remote_slug(self, monkeypatch, url, slug):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: url)
        assert go.remote_slug("origin", cwd=".") == slug

    def test_remote_slug_none_when_no_url(self, monkeypatch):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: None)
        assert go.remote_slug("origin", cwd=".") is None

    def test_auth_args_empty_when_no_token(self, monkeypatch):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/Owner/r.git")
        monkeypatch.setattr(go, "_active_gh_account", lambda: "DifferentUser")
        monkeypatch.setattr(go, "_gh_token_for_owner", lambda owner: None)
        assert go._auth_config_args("origin", cwd=".") == []

    def test_auth_args_empty_for_non_github(self, monkeypatch):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://gitlab.com/o/r.git")
        assert go._auth_config_args("origin", cwd=".") == []

    def test_auth_args_injects_header_with_token(self, monkeypatch):
        import base64
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/Owner/r.git")
        # Owner differs from the active account -> cross-account: inject.
        monkeypatch.setattr(go, "_active_gh_account", lambda: "DifferentUser")
        monkeypatch.setattr(go, "_gh_token_for_owner", lambda owner: "ghp_secret")
        args = go._auth_config_args("origin", cwd=".")
        assert args[0] == "-c"
        expected = base64.b64encode(b"x-access-token:ghp_secret").decode()
        assert args[1] == f"http.extraheader=AUTHORIZATION: basic {expected}"

    def test_auth_args_empty_when_owner_is_active_account(self, monkeypatch):
        """#900: when the repo owner *is* the active gh account, skip injection
        so the working credential helper isn't overridden by a possibly
        push-scopeless OAuth token. Case-insensitive."""
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/Owner/r.git")
        monkeypatch.setattr(go, "_active_gh_account", lambda: "owner")  # case differs
        # Token would be available, but the gate must short-circuit before it.
        monkeypatch.setattr(go, "_gh_token_for_owner",
                            lambda owner: pytest.fail("should not be called"))
        assert go._auth_config_args("origin", cwd=".") == []

    def test_auth_args_honors_account_override(self, monkeypatch):
        """An explicit repos.yaml account: overrides the derived remote owner,
        so the injected credential authenticates as the account (not the org)."""
        import base64

        from agent_worktrees import repos as repos_mod
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/example-org/r.git")
        # Registry maps this owner's repo to a different account login.
        monkeypatch.setattr(
            repos_mod, "account_for_github_owner",
            lambda owner: "host-acct" if owner == "example-org" else owner,
        )
        monkeypatch.setattr(go, "_active_gh_account", lambda: "example-org")
        seen: list[str] = []

        def _tok(account):
            seen.append(account)
            return "acct_secret"

        monkeypatch.setattr(go, "_gh_token_for_owner", _tok)
        args = go._auth_config_args("origin", cwd=".")
        # Effective account (host-acct) != active (example-org) -> inject it.
        assert seen == ["host-acct"]
        expected = base64.b64encode(b"x-access-token:acct_secret").decode()
        assert args == ["-c", f"http.extraheader=AUTHORIZATION: basic {expected}"]

    def test_active_gh_account_parses_active_marker(self, monkeypatch):
        out = (
            "github.com\n"
            "  \u2713 Logged in to github.com account WorkAcct (keyring)\n"
            "  - Active account: false\n"
            "  \u2713 Logged in to github.com account PersonalAcct (keyring)\n"
            "  - Active account: true\n"
        )
        go._active_gh_account.cache_clear()
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(
            go.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out, stderr=""),
        )
        assert go._active_gh_account() == "PersonalAcct"
        go._active_gh_account.cache_clear()

    def test_active_gh_account_single_account_fallback(self, monkeypatch):
        out = "github.com\n  \u2713 Logged in to github.com account Solo (keyring)\n"
        go._active_gh_account.cache_clear()
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(
            go.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out, stderr=""),
        )
        assert go._active_gh_account() == "Solo"
        go._active_gh_account.cache_clear()

    def test_list_gh_accounts_parses_all(self, monkeypatch):
        out = (
            "github.com\n"
            "  \u2713 Logged in to github.com account ThomasMichon (keyring)\n"
            "  - Active account: true\n"
            "  - Token scopes: 'repo'\n"
            "  \u2713 Logged in to github.com account example-operator (keyring)\n"
            "  - Active account: false\n"
        )
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(
            go.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out, stderr=""),
        )
        # EMU logins with underscores must survive.
        assert go.list_gh_accounts() == ["ThomasMichon", "example-operator"]

    def test_list_gh_accounts_empty_without_gh(self, monkeypatch):
        monkeypatch.setattr(go.shutil, "which", lambda _: None)
        assert go.list_gh_accounts() == []


    def test_push_falls_back_to_plain_when_injected_auth_403s(self, monkeypatch):
        """#900: a token-injected push that fails must retry once *without* the
        override so the default credential helper can authenticate."""
        monkeypatch.setattr(
            go, "_auth_config_args",
            lambda remote, *, cwd: ["-c", "http.extraheader=AUTHORIZATION: basic x"],
        )
        calls: list[bool] = []

        def fake_git(*args, **kwargs):
            injected = "http.extraheader=AUTHORIZATION: basic x" in args
            calls.append(injected)
            # Injected push 403s; plain push (no override) succeeds.
            rc = 1 if injected else 0
            return types.SimpleNamespace(returncode=rc, stdout="", stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        assert bool(go.push("origin", "master", cwd=".")) is True
        assert calls == [True, False]  # injected first, then plain fallback

    def test_push_no_fallback_when_no_injected_auth(self, monkeypatch):
        """When no override was injected, a failed push must NOT silently
        retry -- it simply returns a falsy PushResult carrying the stderr."""
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        calls: list[int] = []

        def fake_git(*args, **kwargs):
            calls.append(1)
            return types.SimpleNamespace(
                returncode=1, stdout="",
                stderr="remote: version-consistency violations\n"
                       "error: failed to push some refs")

        monkeypatch.setattr(go, "git", fake_git)
        res = go.push("origin", "master", cwd=".")
        assert bool(res) is False
        assert len(calls) == 1  # no fallback attempt
        # #993: the real git stderr is surfaced, and a hook decline is NOT a
        # fast-forward race, so it must not be retried by the caller.
        assert "version-consistency" in res.stderr
        assert res.retryable is False

    def test_push_nonff_failure_is_retryable(self, monkeypatch):
        """#993: a non-fast-forward rejection IS a race the caller should
        fetch+rebase+retry -- classified retryable from git's stderr."""
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        monkeypatch.setattr(go, "git", lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout="",
            stderr=" ! [rejected]        master -> master (fetch first)\n"
                   "error: failed to push some refs"))
        res = go.push("origin", "master", cwd=".")
        assert bool(res) is False
        assert res.retryable is True

    def test_push_success_returns_truthy_no_stderr(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        monkeypatch.setattr(go, "git", lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="", stderr=""))
        res = go.push("origin", "master", cwd=".")
        assert bool(res) is True
        assert res.retryable is False

    def test_redact_args_strips_extraheader(self):
        cmd = ["git", "-c", "http.extraheader=AUTHORIZATION: basic c2VjcmV0", "push"]
        redacted = go._redact_args(cmd)
        assert "http.extraheader=<redacted>" in redacted
        assert not any("c2VjcmV0" in a for a in redacted)

    def test_git_error_message_redacts_token(self):
        err = GitError(
            ["git", "-c", "http.extraheader=AUTHORIZATION: basic c2VjcmV0", "push"],
            1, "denied",
        )
        assert "c2VjcmV0" not in str(err)
        assert "<redacted>" in str(err)
        assert all("c2VjcmV0" not in a for a in err.cmd)


class TestFetchTimeout:
    """#1709: a network ``fetch`` is bounded so an unreachable remote can't
    hang push-changes / create-pr / pr-status / sync indefinitely."""

    def test_fetch_passes_default_timeout(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        captured = {}

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False):
            captured["timeout"] = timeout
            captured["args"] = args
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        go.fetch("origin", cwd=".")
        assert captured["timeout"] == go.DEFAULT_FETCH_TIMEOUT
        assert captured["args"] == ("fetch", "origin", "--quiet")

    def test_fetch_timeout_override(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        captured = {}
        monkeypatch.setattr(go, "git", lambda *a, cwd=None, check=True,
                            capture=True, timeout=None: (
            captured.__setitem__("timeout", timeout),
            types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
        go.fetch("origin", cwd=".", timeout=5)
        assert captured["timeout"] == 5

    def test_fetch_stall_becomes_giterror_not_hang(self, monkeypatch):
        """A stall past the bound surfaces as a GitError (rc 124) the
        best-effort callers already handle -- never an unbounded hang."""
        import subprocess
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None):
            raise subprocess.TimeoutExpired(cmd=["git", *args], timeout=timeout)

        monkeypatch.setattr(go, "git", fake_git)
        with pytest.raises(GitError) as exc:
            go.fetch("origin", cwd=".")
        assert exc.value.returncode == 124
        assert "timed out" in str(exc.value)

    def test_fetch_none_timeout_preserves_unbounded(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        captured = {}
        monkeypatch.setattr(go, "git", lambda *a, cwd=None, check=True,
                            capture=True, timeout="sentinel": (
            captured.__setitem__("timeout", timeout),
            types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
        go.fetch("origin", cwd=".", timeout=None)
        assert captured["timeout"] is None


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TestDataModels:
    def test_worktree_state_values(self):
        assert WorktreeState.ACTIVE == "active"
        assert WorktreeState.COMPLETED == "completed"
        assert WorktreeState.GONE == "gone"

    def test_worktree_state_info_defaults(self):
        info = WorktreeStateInfo(state=WorktreeState.ACTIVE)
        assert info.ahead == 0
        assert info.behind == 0
        assert info.dirty == 0
        assert info.branch_drift is False
        assert info.current_branch is None


class TestRefineStateWithSession:
    """The CONVO refinement shared by the status bar and list --classify."""

    def test_convo_is_canonical_state(self):
        # CONVO must be a first-class enum value so every surface (status bar,
        # `list --json --classify`) reports the same vocabulary.
        assert WorktreeState.CONVO == "convo"

    def test_unused_with_turns_becomes_convo(self):
        assert (
            refine_state_with_session(WorktreeState.UNUSED, 7)
            == WorktreeState.CONVO
        )

    def test_unused_without_turns_stays_unused(self):
        assert (
            refine_state_with_session(WorktreeState.UNUSED, 0)
            == WorktreeState.UNUSED
        )

    def test_other_states_unaffected_by_turns(self):
        for st in (
            WorktreeState.DIRTY,
            WorktreeState.WIP,
            WorktreeState.COMPLETED,
            WorktreeState.ACTIVE,
            WorktreeState.ORPHAN,
            WorktreeState.GONE,
        ):
            assert refine_state_with_session(st, 12) == st


# --- classification git timeout -> honest UNKNOWN (perf hang fix) -----------


class TestClassifyGitTimeout:
    """A stalled git spawn during classification must degrade to UNKNOWN for
    that one worktree -- never a fabricated concrete state, never a raised
    timeout that hangs the picker's per-worktree loop."""

    def test_git_passes_timeout_through(self, monkeypatch):
        import subprocess

        from agent_worktrees import git_ops as go

        captured = {}

        def fake_run(cmd, **kw):
            captured.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(go.subprocess, "run", fake_run)
        go.git("status", "--porcelain", timeout=7)
        assert captured["timeout"] == 7

    def test_git_default_timeout_is_none(self, monkeypatch):
        """Callers that don't opt in keep the historical unbounded behavior."""
        import subprocess

        from agent_worktrees import git_ops as go

        captured = {}

        def fake_run(cmd, **kw):
            captured.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(go.subprocess, "run", fake_run)
        go.git("status")
        assert captured["timeout"] is None

    def test_classify_worktree_timeout_reports_unknown(self, tmp_path, monkeypatch):
        import subprocess

        from agent_worktrees import git_ops as go

        (tmp_path / ".git").mkdir()

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None):
            # Branch detection succeeds so classification reaches the git ops.
            if args[:2] == ("rev-parse", "--abbrev-ref"):
                return subprocess.CompletedProcess(
                    ["git", *args], 0, "worktree/x\n", "")
            # The first classification probe stalls past the bound.
            raise subprocess.TimeoutExpired(
                cmd=["git", *args], timeout=timeout or go._CLASSIFY_GIT_TIMEOUT)

        monkeypatch.setattr(go, "git", fake_git)

        info = go.classify_worktree(str(tmp_path), "worktree/x")
        # Honest "couldn't determine", not a confidently-wrong concrete state.
        assert info.state == go.WorktreeState.UNKNOWN
        # Branch metadata from the successful pre-git read is still reported.
        assert info.current_branch == "worktree/x"


class TestClassifyGitProcessCount:
    """Classification avoids separate merge-base and rev-list count spawns."""

    @staticmethod
    def _run(monkeypatch, responses):
        calls = []

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None):
            calls.append(args)
            return responses(args)

        monkeypatch.setattr(go, "git", fake_git)
        info = go._classify_git_state(
            Path("."),
            "worktree/x",
            "origin/main",
            fetch=False,
            remote="origin",
            default_branch="main",
            actual_branch="worktree/x",
            drift=False,
        )
        return info, calls

    def test_dirty_ahead_path_gets_counts_without_merge_base(self, monkeypatch):
        def responses(args):
            if args[:1] == ("status",):
                return types.SimpleNamespace(
                    returncode=0, stdout=" M changed.txt\n", stderr="")
            if args[:1] == ("rev-list",):
                return types.SimpleNamespace(
                    returncode=0, stdout="2\t0\n", stderr="")
            if args[:2] == ("--no-pager", "log"):
                return types.SimpleNamespace(
                    returncode=0, stdout="Pending work\n", stderr="")
            raise AssertionError(f"unexpected git call: {args}")

        info, calls = self._run(monkeypatch, responses)

        assert info.state == WorktreeState.DIRTY
        assert (info.ahead, info.behind, info.dirty) == (2, 0, 1)
        assert [call[0] for call in calls] == [
            "status", "rev-list", "--no-pager",
        ]
        assert calls[1] == (
            "rev-list", "--left-right", "--count",
            "worktree/x...origin/main",
        )

    def test_diverged_orphan_still_requires_failed_merge_base(self, monkeypatch):
        def responses(args):
            if args[:1] == ("status",):
                return types.SimpleNamespace(
                    returncode=0, stdout="", stderr="")
            if args[:1] == ("rev-list",):
                return types.SimpleNamespace(
                    returncode=0, stdout="1 4\n", stderr="")
            if args[:1] == ("merge-base",):
                return types.SimpleNamespace(
                    returncode=1, stdout="", stderr="")
            raise AssertionError(f"unexpected git call: {args}")

        info, calls = self._run(monkeypatch, responses)

        assert info.state == WorktreeState.ORPHAN
        assert [call[0] for call in calls] == [
            "status", "rev-list", "merge-base",
        ]

    def test_failed_count_preserves_orphan_detection(self, monkeypatch):
        def responses(args):
            if args[:1] == ("status",):
                return types.SimpleNamespace(
                    returncode=0, stdout="", stderr="")
            if args[:1] == ("rev-list",):
                return types.SimpleNamespace(
                    returncode=128, stdout="", stderr="bad revision")
            if args[:1] == ("merge-base",):
                return types.SimpleNamespace(
                    returncode=1, stdout="", stderr="bad revision")
            raise AssertionError(f"unexpected git call: {args}")

        info, calls = self._run(monkeypatch, responses)

        assert info.state == WorktreeState.ORPHAN
        assert [call[0] for call in calls] == [
            "status", "rev-list", "merge-base",
        ]

    def test_patch_equivalent_ahead_branch_never_needs_merge_base(
        self, monkeypatch,
    ):
        def responses(args):
            if args[:1] == ("status",):
                stdout = ""
            elif args[:1] == ("rev-list",):
                stdout = "2 0\n"
            elif args[:2] == ("--no-pager", "log"):
                stdout = "Already landed\n"
            elif args[:1] == ("cherry",):
                stdout = "- deadbeef\n- cafef00d\n"
            else:
                raise AssertionError(f"unexpected git call: {args}")
            return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        info, calls = self._run(monkeypatch, responses)

        assert info.state == WorktreeState.COMPLETED
        assert (info.ahead, info.behind) == (2, 0)
        assert "merge-base" not in [call[0] for call in calls]
