"""Tests for the resource-obligation disposition vocabulary + helpers (Phase 1)."""

from __future__ import annotations

import pytest

from agent_worktrees import obligations as ob

# ── normalize ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["active", "at-rest", "released"])
def test_normalize_known_values_passthrough(value):
    assert ob.normalize(value) == value


@pytest.mark.parametrize("value", ["", None, "bogus", "ACTIVE", "  ", 42])
def test_normalize_unknown_degrades_to_active(value):
    assert ob.normalize(value) == ob.ACTIVE


def test_normalize_strips_whitespace():
    assert ob.normalize("  at-rest  ") == ob.AT_REST


# ── blocks_finalize (the gate predicate) ─────────────────────────────────────

@pytest.mark.parametrize("value", ["active", "", None, "unknown"])
def test_active_or_unknown_blocks_finalize(value):
    assert ob.blocks_finalize(value) is True


@pytest.mark.parametrize("value", ["at-rest", "released"])
def test_settled_does_not_block(value):
    assert ob.blocks_finalize(value) is False


# ── held / at-rest / released ────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["", "active", "at-rest"])
def test_held_includes_active_and_at_rest(value):
    assert ob.is_held(value) is True


def test_released_not_held():
    assert ob.is_held("released") is False


def test_is_at_rest_and_is_released():
    assert ob.is_at_rest("at-rest") and not ob.is_at_rest("active")
    assert ob.is_released("released") and not ob.is_released("at-rest")


# ── context round-trip ───────────────────────────────────────────────────────

def test_from_context_reads_key():
    assert ob.from_context({"disposition": "at-rest"}) == ob.AT_REST


@pytest.mark.parametrize("ctx", [None, {}, {"other": "x"}, "not-a-map", 5])
def test_from_context_missing_degrades_to_active(ctx):
    assert ob.from_context(ctx) == ob.ACTIVE


def test_from_context_unknown_value_degrades():
    assert ob.from_context({"disposition": "weird"}) == ob.ACTIVE


def test_with_disposition_sets_key_and_preserves_others():
    out = ob.with_disposition({"a": "1"}, "at-rest")
    assert out == {"a": "1", "disposition": "at-rest"}


def test_with_disposition_starts_fresh_on_non_map():
    assert ob.with_disposition(None, "released") == {"disposition": "released"}


def test_with_disposition_normalizes_value():
    assert ob.with_disposition({}, "bogus") == {"disposition": "active"}


def test_with_disposition_does_not_mutate_input():
    src = {"a": "1"}
    ob.with_disposition(src, "at-rest")
    assert src == {"a": "1"}


def test_with_disposition_stringifies_values():
    out = ob.with_disposition({"n": 3}, "active")
    assert out == {"n": "3", "disposition": "active"}


def test_roundtrip_with_then_from():
    ctx = ob.with_disposition({"k": "v"}, "at-rest")
    assert ob.from_context(ctx) == ob.AT_REST


# ── gate_mode (the finalize gate) ────────────────────────────────────────────

def test_gate_mode_defaults_to_warn():
    assert ob.gate_mode({}) == ob.WARN


@pytest.mark.parametrize("value,expected", [
    ("off", ob.OFF), ("warn", ob.WARN), ("block", ob.BLOCK),
    ("BLOCK", ob.BLOCK), ("  warn  ", ob.WARN),
])
def test_gate_mode_reads_known_values(value, expected):
    assert ob.gate_mode({ob.GATE_ENV: value}) == expected


@pytest.mark.parametrize("value", ["", "bogus", "enforce", "1"])
def test_gate_mode_unknown_degrades_to_warn_never_block(value):
    # An unrecognized value must never silently start *enforcing*.
    assert ob.gate_mode({ob.GATE_ENV: value}) == ob.WARN


def test_gate_mode_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv(ob.GATE_ENV, "block")
    assert ob.gate_mode() == ob.BLOCK
