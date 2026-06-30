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
        ("https://host/gitea/tmichon/aperture-labs.git", "tmichon/aperture-labs"),
        ("https://github.com/owner/copilot-extensions.git", "owner/copilot-extensions"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("ssh://git@host/owner/repo", "owner/repo"),
        ("https://host/deep/path/org/proj.git/", "org/proj"),
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
        assert go.push("origin", "master", cwd=".") is True
        assert calls == [True, False]  # injected first, then plain fallback

    def test_push_no_fallback_when_no_injected_auth(self, monkeypatch):
        """When no override was injected, a failed push must NOT silently
        retry -- it simply returns False."""
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        calls: list[int] = []

        def fake_git(*args, **kwargs):
            calls.append(1)
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        assert go.push("origin", "master", cwd=".") is False
        assert len(calls) == 1  # no fallback attempt

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
