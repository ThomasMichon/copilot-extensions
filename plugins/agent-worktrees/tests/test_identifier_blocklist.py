"""Tests for the cross-repo identifier-blocklist sweep."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import identifier_blocklist as iblk
from agent_worktrees import repos


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so the registry reads/writes under a tmp dir."""
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    return tmp_path


def _write_blocklist(root: Path, tier: str, yaml_text: str) -> None:
    d = root / iblk.BLOCKLIST_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    (d / iblk.TIER_FILES[tier]).write_text(yaml_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# visibility rank / tier resolution
# ---------------------------------------------------------------------------

def test_resolve_visibility_rank_unset_entry_is_most_exposed():
    entry = repos.RepoEntry(name="r")  # visibility unset
    assert iblk.resolve_visibility_rank(entry) == repos.VISIBILITY_RANK["public"]


def test_resolve_visibility_rank_none_is_most_exposed():
    assert iblk.resolve_visibility_rank(None) == repos.VISIBILITY_RANK["public"]


def test_resolve_visibility_rank_private():
    entry = repos.RepoEntry(name="r", visibility="private")
    assert iblk.resolve_visibility_rank(entry) == repos.VISIBILITY_RANK["private"]


def test_applicable_tiers_public_target_gets_both_tiers():
    rank = repos.VISIBILITY_RANK["public"]
    assert set(iblk.applicable_tiers(rank)) == {"internal", "public"}


def test_applicable_tiers_internal_target_gets_internal_only():
    rank = repos.VISIBILITY_RANK["internal"]
    assert iblk.applicable_tiers(rank) == ["internal"]


def test_applicable_tiers_private_target_gets_nothing():
    rank = repos.VISIBILITY_RANK["private"]
    assert iblk.applicable_tiers(rank) == []


# ---------------------------------------------------------------------------
# YAML entry compilation
# ---------------------------------------------------------------------------

def test_compile_internal_token_plain_literal():
    assert iblk._compile_internal_token({"token": "legacy-system"}) == "legacy-system"


def test_compile_internal_token_regex_kind():
    token = iblk._compile_internal_token({"token": r"\bSPO\b", "kind": "regex"})
    assert token == r"regex:\bSPO\b"


def test_compile_internal_token_whole_word_escapes_literal():
    token = iblk._compile_internal_token({"token": "spo-core", "whole_word": True})
    assert token == r"regex:\bspo\-core\b"


def test_compile_internal_token_case_sensitive_wraps_group():
    token = iblk._compile_internal_token(
        {"token": "CAR", "whole_word": True, "case_sensitive": True}
    )
    assert token == r"regex:(?-i:\bCAR\b)"


def test_compile_internal_token_empty_token_returns_empty():
    assert iblk._compile_internal_token({"token": ""}) == ""
    assert iblk._compile_internal_token({}) == ""


def test_compile_internal_token_unknown_kind_falls_back_to_literal():
    assert iblk._compile_internal_token({"token": "x", "kind": "bogus"}) == "x"


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------

def test_parse_blocklist_file_entries_list(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text(
        "entries:\n"
        "  - token: legacy-system\n"
        "    reason: Internal org name\n"
        "  - token: '\\bSPO\\b'\n"
        "    kind: regex\n",
        encoding="utf-8",
    )
    entries = iblk.parse_blocklist_file(f, "some-repo", "public")
    assert len(entries) == 2
    assert entries[0].token == "legacy-system"
    assert entries[0].reason == "Internal org name"
    assert entries[0].source_repo == "some-repo"
    assert entries[0].source_tier == "public"
    assert entries[1].token == r"regex:\bSPO\b"
    assert entries[1].reason is None


def test_parse_blocklist_file_bare_list_top_level(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("- token: legacy-system\n", encoding="utf-8")
    entries = iblk.parse_blocklist_file(f, "r", "public")
    assert len(entries) == 1
    assert entries[0].token == "legacy-system"


def test_parse_blocklist_file_missing_returns_empty(tmp_path: Path):
    entries = iblk.parse_blocklist_file(tmp_path / "nope.yaml", "r", "public")
    assert entries == []


def test_parse_blocklist_file_malformed_yaml_returns_empty(tmp_path: Path):
    f = tmp_path / "bad.yaml"
    f.write_text("entries: [unterminated", encoding="utf-8")
    assert iblk.parse_blocklist_file(f, "r", "public") == []


def test_parse_blocklist_file_skips_non_mapping_entries(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: legacy-system\n  - just a string\n", encoding="utf-8")
    entries = iblk.parse_blocklist_file(f, "r", "public")
    assert len(entries) == 1


# ---------------------------------------------------------------------------
# sweep()
# ---------------------------------------------------------------------------

def test_sweep_aggregates_across_repos(home: Path, tmp_path: Path):
    source_a = tmp_path / "source-a"
    source_b = tmp_path / "source-b"
    source_a.mkdir()
    source_b.mkdir()
    _write_blocklist(source_a, "public", "entries:\n  - token: termA\n")
    _write_blocklist(source_b, "internal", "entries:\n  - token: termB\n")

    repos.add_repo("source-a", str(source_a), repo_class="worktree", plat="windows")
    repos.add_repo("source-b", str(source_b), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")

    entries = iblk.sweep("target")
    tokens = {e.token for e in entries}
    assert tokens == {"termA", "termB"}


def test_sweep_internal_target_excludes_public_only_tier(home: Path, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _write_blocklist(source, "public", "entries:\n  - token: public-only\n")
    _write_blocklist(source, "internal", "entries:\n  - token: internal-term\n")
    repos.add_repo("source", str(source), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="internal", plat="windows")

    entries = iblk.sweep("target")
    tokens = {e.token for e in entries}
    assert tokens == {"internal-term"}


def test_sweep_private_target_gets_nothing(home: Path, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _write_blocklist(source, "public", "entries:\n  - token: whatever\n")
    repos.add_repo("source", str(source), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="private", plat="windows")

    assert iblk.sweep("target") == []


def test_sweep_unresolvable_target_is_fail_safe_maximal(home: Path, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _write_blocklist(source, "public", "entries:\n  - token: whatever\n")
    repos.add_repo("source", str(source), repo_class="worktree", plat="windows")

    entries = iblk.sweep("no-such-repo")
    assert {e.token for e in entries} == {"whatever"}


def test_sweep_deduplicates_identical_entries(home: Path, tmp_path: Path):
    source_a = tmp_path / "source-a"
    source_b = tmp_path / "source-b"
    source_a.mkdir()
    source_b.mkdir()
    _write_blocklist(source_a, "public", "entries:\n  - token: dup\n    reason: same\n")
    _write_blocklist(source_b, "public", "entries:\n  - token: dup\n    reason: same\n")
    repos.add_repo("source-a", str(source_a), repo_class="worktree", plat="windows")
    repos.add_repo("source-b", str(source_b), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")

    entries = iblk.sweep("target")
    assert len(entries) == 1


def test_sweep_ignores_repo_with_no_local_path(home: Path, tmp_path: Path):
    registry = repos.read_registry()
    registry.repos["ghost"] = repos.RepoEntry(name="ghost", repo_class="reference")
    repos.write_registry(registry)
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")
    # Must not raise even though "ghost" has no local_path for this platform.
    assert iblk.sweep("target") == []


def test_sweep_ignores_repo_with_missing_local_dir(home: Path, tmp_path: Path):
    repos.add_repo("gone", str(tmp_path / "does-not-exist"), repo_class="worktree",
                   plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")
    assert iblk.sweep("target") == []


# ---------------------------------------------------------------------------
# render_ci_format()
# ---------------------------------------------------------------------------

def test_render_ci_format_with_and_without_reason():
    entries = [
        iblk.BlocklistEntry(token="a", reason="why", source_repo="r", source_tier="public"),
        iblk.BlocklistEntry(token="b", reason=None, source_repo="r", source_tier="public"),
    ]
    assert iblk.render_ci_format(entries) == "a|why\nb"
