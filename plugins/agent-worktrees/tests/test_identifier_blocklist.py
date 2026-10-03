"""Tests for the cross-repo identifier-blocklist sweep."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_worktrees import identifier_blocklist as iblk
from agent_worktrees import repos


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so the registry reads/writes under a tmp dir.

    Every repo in this file is registered with an explicit ``plat="windows"``
    path (so fixtures read naturally regardless of which OS authored them),
    so ``_current_platform()`` must be pinned to ``"windows"`` too --
    otherwise ``RepoEntry.local_path()`` resolves against whatever OS the
    test suite actually runs on (Ubuntu in CI), finds no matching path, and
    every sweep silently skips every source.
    """
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.setattr(repos, "_current_platform", lambda: "windows")
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
    assert (
        iblk._compile_internal_token({"token": "legacy-system"}, context="t")
        == "legacy-system"
    )


def test_compile_internal_token_literal_with_reserved_regex_prefix_is_escaped():
    """A plain `literal` token that happens to start with the reserved
    "regex:" marker must never be returned verbatim -- every downstream
    consumer treats that prefix as regex mode, so `token: "regex:[abc]"`
    returned as-is would match any of a/b/c instead of the literal text."""
    token = iblk._compile_internal_token({"token": "regex:[abc]"}, context="t")
    assert token == "regex:" + re.escape("regex:[abc]")
    assert token == r"regex:regex:\[abc\]"


def test_compile_internal_token_regex_kind():
    token = iblk._compile_internal_token(
        {"token": r"\bSPO\b", "kind": "regex"}, context="t",
    )
    assert token == r"regex:\bSPO\b"


def test_compile_internal_token_whole_word_escapes_literal():
    token = iblk._compile_internal_token(
        {"token": "spo-core", "whole_word": True}, context="t",
    )
    assert token == r"regex:\bspo\-core\b"


def test_compile_internal_token_case_sensitive_wraps_group():
    token = iblk._compile_internal_token(
        {"token": "ABC", "whole_word": True, "case_sensitive": True}, context="t",
    )
    assert token == r"regex:(?-i:\bABC\b)"


def test_compile_internal_token_empty_token_raises():
    with pytest.raises(iblk.BlocklistParseError, match="missing required 'token'"):
        iblk._compile_internal_token({"token": ""}, context="t")
    with pytest.raises(iblk.BlocklistParseError, match="missing required 'token'"):
        iblk._compile_internal_token({}, context="t")


def test_compile_internal_token_unknown_kind_raises():
    with pytest.raises(iblk.BlocklistParseError, match="unknown kind"):
        iblk._compile_internal_token({"token": "x", "kind": "bogus"}, context="t")


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


def test_parse_blocklist_file_empty_document_returns_empty(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("", encoding="utf-8")
    assert iblk.parse_blocklist_file(f, "r", "public") == []


def test_parse_blocklist_file_empty_mapping_returns_empty(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("{}\n", encoding="utf-8")
    assert iblk.parse_blocklist_file(f, "r", "public") == []


def test_parse_blocklist_file_entries_key_with_no_value_returns_empty(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n", encoding="utf-8")
    assert iblk.parse_blocklist_file(f, "r", "public") == []


def test_parse_blocklist_file_malformed_yaml_raises(tmp_path: Path):
    f = tmp_path / "bad.yaml"
    f.write_text("entries: [unterminated", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_non_mapping_entries(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: legacy-system\n  - just a string\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="expected a mapping"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_missing_entries_key(tmp_path: Path):
    """A misspelled top-level key (e.g. 'enteries') must be a hard error,
    not silently treated as zero entries -- that would hide the exact typo
    this validation exists to catch."""
    f = tmp_path / "block-for-public.yaml"
    f.write_text("enteries:\n  - token: oops\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="missing required top-level 'entries'"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_non_list_entries(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries: not-a-list\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="must be a list"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_non_dict_non_list_top_level(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("just a bare string\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="expected a YAML mapping"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_missing_token(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - reason: no token here\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="missing required 'token'"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_unknown_kind(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: x\n    kind: regxe\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="unknown kind"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_non_boolean_whole_word(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: x\n    whole_word: \"false\"\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="'whole_word' must be a boolean"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_non_boolean_case_sensitive(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: x\n    case_sensitive: 1\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="'case_sensitive' must be a boolean"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_uncompilable_regex(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: '('\n    kind: regex\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="invalid regex"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_regex_matching_empty_string(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: 'x*'\n    kind: regex\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="matches the empty string"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_accepts_valid_regex(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text(r"entries:" "\n  - token: '\\bexample\\b'\n    kind: regex\n", encoding="utf-8")
    entries = iblk.parse_blocklist_file(f, "r", "public")
    assert entries[0].token == r"regex:\bexample\b"


def test_parse_blocklist_file_rejects_token_with_newline(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: \"alpha\\nbeta\"\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="newline or semicolon"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_token_with_semicolon(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: 'alpha;beta'\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="newline or semicolon"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_rejects_unknown_field(tmp_path: Path):
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: x\n    case_sensitve: true\n", encoding="utf-8")
    with pytest.raises(iblk.BlocklistParseError, match="unknown field"):
        iblk.parse_blocklist_file(f, "r", "public")


def test_parse_blocklist_file_unreadable_raises_not_silent(tmp_path: Path, monkeypatch):
    """A permissions/IO error reading an EXISTING file must be surfaced, not
    treated the same as the file simply being absent."""
    f = tmp_path / "block-for-public.yaml"
    f.write_text("entries:\n  - token: x\n", encoding="utf-8")

    def _raise(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(iblk.Path, "read_text", _raise)
    with pytest.raises(iblk.BlocklistParseError, match="could not read"):
        iblk.parse_blocklist_file(f, "r", "public")


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


def test_sweep_preserves_case_sensitive_entries_distinctly(home: Path, tmp_path: Path):
    """Two case-sensitive entries differing only by case (ABC vs abc) must
    not collapse into one -- lowercasing the dedup key would silently drop
    enforcement for one of the two spellings."""
    source = tmp_path / "source"
    source.mkdir()
    _write_blocklist(
        source, "public",
        "entries:\n"
        "  - token: ABC\n"
        "    whole_word: true\n"
        "    case_sensitive: true\n"
        "    reason: same reason\n"
        "  - token: abc\n"
        "    whole_word: true\n"
        "    case_sensitive: true\n"
        "    reason: same reason\n",
    )
    repos.add_repo("source", str(source), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")

    entries = iblk.sweep("target")
    assert len(entries) == 2
    tokens = {e.token for e in entries}
    assert tokens == {r"regex:(?-i:\bABC\b)", r"regex:(?-i:\babc\b)"}


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


def test_sweep_excludes_target_from_source_discovery(home: Path, tmp_path: Path):
    """A repo that just added its own blocklist must not immediately flag the
    token inside that very file -- the target is never its own source."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    _write_blocklist(target_dir, "public", "entries:\n  - token: self-term\n")
    repos.add_repo("target", str(target_dir), repo_class="worktree",
                   visibility="public", plat="windows")

    assert iblk.sweep("target") == []


def test_sweep_propagates_malformed_blocklist_file(home: Path, tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _write_blocklist(source, "public", "entries: [unterminated")
    repos.add_repo("source", str(source), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")

    with pytest.raises(iblk.BlocklistParseError):
        iblk.sweep("target")


def test_sweep_preserves_valid_peer_entries_alongside_a_malformed_source(
    home: Path, tmp_path: Path,
):
    """One source's broken YAML must not discard every other source's
    already-successfully-parsed entries -- the raised exception still
    carries them via `partial_entries`."""
    broken = tmp_path / "broken"
    broken.mkdir()
    _write_blocklist(broken, "public", "entries: [unterminated")
    good = tmp_path / "good"
    good.mkdir()
    _write_blocklist(good, "public", "entries:\n  - token: still-valid\n")
    repos.add_repo("broken", str(broken), repo_class="worktree", plat="windows")
    repos.add_repo("good", str(good), repo_class="worktree", plat="windows")
    repos.add_repo("target", str(tmp_path / "target"), repo_class="worktree",
                   visibility="public", plat="windows")

    with pytest.raises(iblk.BlocklistParseError) as excinfo:
        iblk.sweep("target")
    assert [e.token for e in excinfo.value.partial_entries] == ["still-valid"]


# ---------------------------------------------------------------------------
# render_ci_format()
# ---------------------------------------------------------------------------

def test_render_ci_format_with_and_without_reason():
    entries = [
        iblk.BlocklistEntry(token="a", reason="why", source_repo="r", source_tier="public"),
        iblk.BlocklistEntry(token="b", reason=None, source_repo="r", source_tier="public"),
    ]
    assert iblk.render_ci_format(entries) == "a|why\nb"


def test_render_ci_format_escapes_regex_alternation_pipe():
    entries = [
        iblk.BlocklistEntry(
            token=r"regex:foo|bar", reason=None, source_repo="r", source_tier="public",
        ),
    ]
    assert iblk.render_ci_format(entries) == r"regex:foo||bar"


def test_render_ci_format_promotes_literal_pipe_to_escaped_regex():
    entries = [
        iblk.BlocklistEntry(token="a|b", reason=None, source_repo="r", source_tier="public"),
    ]
    rendered = iblk.render_ci_format(entries)
    assert rendered.startswith("regex:")
    assert "||" in rendered


def test_render_ci_format_sanitizes_reason_delimiters():
    entries = [
        iblk.BlocklistEntry(
            token="a", reason="line one\nline two; more", source_repo="r", source_tier="public",
        ),
    ]
    rendered = iblk.render_ci_format(entries)
    assert "\n" not in rendered.split("|", 1)[1]
    assert ";" not in rendered
    assert rendered == "a|line one line two, more"


def test_render_ci_format_round_trips_through_ci_loader():
    """An authored regex alternation (e.g. ``foo|bar``) must survive the
    render -> the real CI loader's own parsing unchanged, since the
    consumer's grammar reads an unescaped '|' as the token/reason
    separator."""
    import importlib.util
    import sys as _sys

    script = (
        Path(__file__).resolve().parents[3] / "tools" / "check-no-internal-identifiers.py"
    )
    spec = importlib.util.spec_from_file_location("check_no_internal_identifiers", script)
    module = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    entries = [
        iblk.BlocklistEntry(
            token=r"regex:\b(foo|bar)\b", reason="Use generic | not internal",
            source_repo="r", source_tier="public",
        ),
    ]
    rendered = iblk.render_ci_format(entries)
    pairs = module._load_ci_identifiers(rendered)
    assert pairs == [(r"regex:\b(foo|bar)\b", "Use generic | not internal")]
