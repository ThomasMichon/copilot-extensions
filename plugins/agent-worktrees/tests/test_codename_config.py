"""Tests for agent_worktrees.codename_config: CodenameConfig parsing."""

from __future__ import annotations

from agent_worktrees.codename_config import CodenameConfig, parse_codename


class TestParseCodename:
    def test_missing_block_uses_defaults(self) -> None:
        cfg = parse_codename(None)
        assert cfg == CodenameConfig()
        assert cfg.wordlist_path == ""

    def test_non_mapping_uses_defaults(self) -> None:
        assert parse_codename("not-a-mapping") == CodenameConfig()
        assert parse_codename([1, 2, 3]) == CodenameConfig()

    def test_explicit_wordlist_path(self) -> None:
        cfg = parse_codename({"wordlist_path": "config/codenames.yaml"})
        assert cfg.wordlist_path == "config/codenames.yaml"

    def test_wordlist_path_is_stripped(self) -> None:
        cfg = parse_codename({"wordlist_path": "  config/codenames.yaml  "})
        assert cfg.wordlist_path == "config/codenames.yaml"

    def test_non_string_wordlist_path_falls_back_to_empty(self) -> None:
        # str(None) == "None" -- a truthy, non-empty "path" -- must never
        # reach CodenameConfig.wordlist_path unguarded. This parser also
        # serves the plain in-repo/machine config paths that skip
        # config_dropins's schema validation, so this guard is load-bearing
        # here, not just defense in depth.
        for bad in (None, False, True, 7, 3.5, [], {}):
            cfg = parse_codename({"wordlist_path": bad})
            assert cfg.wordlist_path == ""

    def test_wordlist_path_configured_false_when_key_absent(self) -> None:
        cfg = parse_codename({})
        assert cfg.wordlist_path == ""
        assert cfg.wordlist_path_configured is False

    def test_wordlist_path_configured_true_when_key_present_and_valid(
        self,
    ) -> None:
        cfg = parse_codename({"wordlist_path": "config/codenames.yaml"})
        assert cfg.wordlist_path_configured is True

    def test_wordlist_path_configured_true_for_malformed_value(self) -> None:
        # codename-attribution-by-default (round-13 finding): a malformed
        # value normalizes wordlist_path to the same empty string as a
        # genuinely absent key -- wordlist_path_configured must still be
        # True, so classification callers can tell "no custom wordlist"
        # apart from "a custom wordlist was configured but is malformed"
        # (the latter must still classify as custom/unsafe, never
        # built-in).
        for bad in (None, False, True, 7, 3.5, [], {}):
            cfg = parse_codename({"wordlist_path": bad})
            assert cfg.wordlist_path == ""
            assert cfg.wordlist_path_configured is True

    def test_wordlist_path_configured_false_for_non_mapping_raw(self) -> None:
        assert parse_codename(None).wordlist_path_configured is False
        assert parse_codename("not-a-mapping").wordlist_path_configured is False

