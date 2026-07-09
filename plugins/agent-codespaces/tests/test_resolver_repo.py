"""Codespace resolver: <repo>@<codespace> repo matching (venue-unify)."""

from __future__ import annotations

from agent_codespaces.resolver import _norm_repo, _repo_matches_codespace


class TestNormRepo:
    def test_strips_owner_and_codespaces_suffix(self):
        assert _norm_repo("odsp-microsoft/odsp-web-codespaces") == "odsp-web"
        assert _norm_repo("odsp-web") == "odsp-web"
        assert _norm_repo("tmichon_microsoft/dotfiles") == "dotfiles"

    def test_case_insensitive(self):
        assert _norm_repo("ODSP-Web") == "odsp-web"


class TestRepoMatchesCodespace:
    def test_logical_repo_matches_codespaces_host(self):
        # odsp-web addresses an odsp-web-codespaces CodeSpace.
        assert _repo_matches_codespace(
            "odsp-web", "odsp-microsoft/odsp-web-codespaces"
        )

    def test_exact_host_repo_matches(self):
        assert _repo_matches_codespace(
            "odsp-web-codespaces", "odsp-microsoft/odsp-web-codespaces"
        )

    def test_different_repo_does_not_match(self):
        assert not _repo_matches_codespace(
            "dotfiles", "odsp-microsoft/odsp-web-codespaces"
        )

    def test_empty_cs_repository(self):
        assert not _repo_matches_codespace("odsp-web", None)
        assert not _repo_matches_codespace("odsp-web", "")
