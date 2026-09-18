"""Tests for agent_worktrees.codename_config: CodenameConfig parsing."""

from __future__ import annotations

from agent_worktrees.codename import (
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    MAX_HOOK_TIMEOUT_SECONDS,
)
from agent_worktrees.codename_config import CodenameConfig, parse_codename


class TestParseCodename:
    def test_missing_block_uses_defaults(self) -> None:
        cfg = parse_codename(None)
        assert cfg == CodenameConfig()
        assert cfg.hook_command == ""
        assert cfg.hook_timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_non_mapping_uses_defaults(self) -> None:
        assert parse_codename("not-a-mapping") == CodenameConfig()
        assert parse_codename([1, 2, 3]) == CodenameConfig()

    def test_explicit_hook_command(self) -> None:
        cfg = parse_codename({"hook_command": "my-hook --generate"})
        assert cfg.hook_command == "my-hook --generate"
        assert cfg.hook_timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_explicit_timeout(self) -> None:
        cfg = parse_codename({"hook_command": "my-hook", "hook_timeout_seconds": 2.5})
        assert cfg.hook_timeout_seconds == 2.5

    def test_hook_command_is_stripped(self) -> None:
        cfg = parse_codename({"hook_command": "  my-hook  "})
        assert cfg.hook_command == "my-hook"

    def test_non_string_hook_command_falls_back_to_empty(self) -> None:
        # str(None) == "None" -- a truthy, non-empty "command" -- must never
        # reach CodenameConfig.hook_command unguarded. This parser also
        # serves the plain in-repo/machine config paths that skip
        # config_dropins's schema validation, so this guard is load-bearing
        # here, not just defense in depth.
        for bad in (None, False, True, 7, 3.5, [], {}):
            cfg = parse_codename({"hook_command": bad})
            assert cfg.hook_command == ""

    def test_malformed_timeout_falls_back_to_default(self) -> None:
        cfg = parse_codename({"hook_timeout_seconds": "not-a-number"})
        assert cfg.hook_timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_non_finite_timeout_falls_back_to_default(self) -> None:
        # float("nan")/float("inf") parse without raising but would crash
        # subprocess timeout handling downstream -- must be rejected here.
        for bad in ("nan", "inf", "-inf"):
            cfg = parse_codename({"hook_timeout_seconds": bad})
            assert cfg.hook_timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_non_positive_timeout_falls_back_to_default(self) -> None:
        for bad in (0, -1, -0.5):
            cfg = parse_codename({"hook_timeout_seconds": bad})
            assert cfg.hook_timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_boolean_timeout_falls_back_to_default(self) -> None:
        # float(True) == 1.0 -- a bare float() coercion would silently
        # accept a bool as a real numeric timeout; must be rejected before
        # coercion instead.
        for bad in (True, False):
            cfg = parse_codename({"hook_timeout_seconds": bad})
            assert cfg.hook_timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_oversized_integer_timeout_falls_back_to_default(self) -> None:
        cfg = parse_codename({"hook_timeout_seconds": 10**400})
        assert cfg.hook_timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_very_large_finite_timeout_falls_back_to_default(self) -> None:
        cfg = parse_codename({"hook_timeout_seconds": 1e308})
        assert cfg.hook_timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_timeout_beyond_max_bound_falls_back_to_default(self) -> None:
        cfg = parse_codename(
            {"hook_timeout_seconds": MAX_HOOK_TIMEOUT_SECONDS + 1}
        )
        assert cfg.hook_timeout_seconds == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_integer_timeout_is_coerced_to_float(self) -> None:
        cfg = parse_codename({"hook_timeout_seconds": 3})
        assert cfg.hook_timeout_seconds == 3.0
