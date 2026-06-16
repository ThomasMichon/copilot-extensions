"""Codespace resolver: raw + friendly name matching (#50)."""

from __future__ import annotations

import pytest

from agent_codespaces.lifecycle import CodespaceInfo
from agent_codespaces.resolver import (
    AmbiguousCodespaceError,
    _find_codespace,
    _friendly_aliases,
)


def _cs(name, display, state="Shutdown"):
    return CodespaceInfo(
        name=name, display_name=display, repository="o/r",
        branch="main", state=state, machine="m",
    )


class TestFindCodespace:
    def test_raw_name(self):
        assert _find_codespace([_cs("foo-aaa", "foo")], "foo-aaa").name == "foo-aaa"

    def test_friendly_name(self):
        assert _find_codespace([_cs("foo-aaa", "foo")], "foo").name == "foo-aaa"

    def test_friendly_case_insensitive(self):
        assert _find_codespace([_cs("foo-aaa", "Foo")], "foo").name == "foo-aaa"

    def test_exact_raw_wins_over_friendly_elsewhere(self):
        # "bar-bbb" is an exact raw match and must win even though another
        # codespace has display name "bar".
        codespaces = [_cs("foo-aaa", "bar"), _cs("bar-bbb", "other")]
        assert _find_codespace(codespaces, "bar-bbb").name == "bar-bbb"

    def test_ambiguous_friendly_raises(self):
        codespaces = [_cs("foo-aaa", "foo"), _cs("foo-bbb", "foo")]
        with pytest.raises(AmbiguousCodespaceError) as ei:
            _find_codespace(codespaces, "foo")
        assert "codespace:foo-aaa" in str(ei.value)
        assert "codespace:foo-bbb" in str(ei.value)
        assert ei.value.raw_candidates == ["foo-aaa", "foo-bbb"]

    def test_not_found_raises_keyerror(self):
        with pytest.raises(KeyError):
            _find_codespace([_cs("foo-aaa", "foo")], "nope")


class TestFriendlyAliases:
    def test_alias_is_display_name(self):
        assert _friendly_aliases(_cs("foo-aaa", "foo")) == ["foo"]

    def test_no_alias_when_display_equals_name(self):
        assert _friendly_aliases(_cs("foo", "foo")) == []
