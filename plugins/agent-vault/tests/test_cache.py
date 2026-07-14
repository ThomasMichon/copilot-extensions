"""Tests for the cache-source extension seam and cache-populate helpers."""

from __future__ import annotations

from pathlib import Path

from agent_vault.cli import _read_cache_manifest
from agent_vault.extensions import ExtensionRegistry


# ---------------------------------------------------------------------------
# register_cache_source + collect_cache_entries
# ---------------------------------------------------------------------------


def test_collect_from_single_source():
    reg = ExtensionRegistry()
    reg.register_cache_source(lambda machine: ["A/x", ("A/y", "username")])
    assert reg.collect_cache_entries(None) == [("A/x", "password"), ("A/y", "username")]


def test_collect_dedupes_preserving_order():
    reg = ExtensionRegistry()
    reg.register_cache_source(lambda m: ["A/x", "A/x", ("A/x", "password")], name="s1")
    reg.register_cache_source(lambda m: [("A/y", "password"), "A/x"], name="s2")
    assert reg.collect_cache_entries(None) == [("A/x", "password"), ("A/y", "password")]


def test_sources_run_in_priority_order():
    reg = ExtensionRegistry()
    reg.register_cache_source(lambda m: ["late"], priority=200, name="late")
    reg.register_cache_source(lambda m: ["early"], priority=10, name="early")
    assert reg.collect_cache_entries(None) == [("early", "password"), ("late", "password")]


def test_machine_hint_is_passed():
    seen = {}
    reg = ExtensionRegistry()
    reg.register_cache_source(lambda machine: seen.update(m=machine) or ["A/x"])
    reg.collect_cache_entries("wheatley")
    assert seen["m"] == "wheatley"


def test_raising_source_is_skipped():
    reg = ExtensionRegistry()
    reg.register_cache_source(lambda m: (_ for _ in ()).throw(RuntimeError("boom")), name="bad")
    reg.register_cache_source(lambda m: ["A/ok"], name="good")
    assert reg.collect_cache_entries(None) == [("A/ok", "password")]


def test_empty_and_malformed_items_ignored():
    reg = ExtensionRegistry()
    reg.register_cache_source(lambda m: ["A/x", "", (), None, 123, ("", "password")])
    assert reg.collect_cache_entries(None) == [("A/x", "password")]


def test_no_sources_is_empty():
    assert ExtensionRegistry().collect_cache_entries(None) == []


# ---------------------------------------------------------------------------
# manifest parser
# ---------------------------------------------------------------------------


def test_read_cache_manifest(tmp_path):
    p = tmp_path / "m.conf"
    p.write_text(
        "# comment\n"
        "A/token | password\n"
        "\n"
        "A/token | username\n"
        "B/plain\n"
        "   # indented comment\n",
        encoding="utf-8",
    )
    assert _read_cache_manifest(Path(p)) == [
        ("A/token", "password"),
        ("A/token", "username"),
        ("B/plain", "password"),
    ]
