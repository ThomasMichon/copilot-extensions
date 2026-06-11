"""Tests for agent_worktrees.git_ops — git wrappers and classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees.git_ops import (
    GitError,
    WorktreeState,
    WorktreeStateInfo,
    git,
    is_cwd_inside,
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

    def test_auth_args_empty_when_no_token(self, monkeypatch):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/Owner/r.git")
        monkeypatch.setattr(go, "_gh_token_for_owner", lambda owner: None)
        assert go._auth_config_args("origin", cwd=".") == []

    def test_auth_args_empty_for_non_github(self, monkeypatch):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://gitlab.com/o/r.git")
        assert go._auth_config_args("origin", cwd=".") == []

    def test_auth_args_injects_header_with_token(self, monkeypatch):
        import base64
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/Owner/r.git")
        monkeypatch.setattr(go, "_gh_token_for_owner", lambda owner: "ghp_secret")
        args = go._auth_config_args("origin", cwd=".")
        assert args[0] == "-c"
        expected = base64.b64encode(b"x-access-token:ghp_secret").decode()
        assert args[1] == f"http.extraheader=AUTHORIZATION: basic {expected}"

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
