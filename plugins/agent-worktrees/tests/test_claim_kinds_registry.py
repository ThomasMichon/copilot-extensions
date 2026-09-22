"""Tests for the claim-kind ``.d/`` drop-in registry (picker-venue-pivots
effort). See `claim_kinds_registry`'s own module docstring for the contract.
"""
from __future__ import annotations

import json

from agent_worktrees import claim_kinds_registry, claims_rank


def _write(plugins_root, plugin_rel, kind_file, payload):
    plugin_dir = plugins_root / plugin_rel
    claim_kinds_dir = plugin_dir / "claim-kinds"
    claim_kinds_dir.mkdir(parents=True, exist_ok=True)
    (claim_kinds_dir / kind_file).write_text(json.dumps(payload), encoding="utf-8")


def test_installed_plugins_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(claim_kinds_registry.PLUGINS_ROOT_ENV, str(tmp_path / "custom"))
    assert claim_kinds_registry.installed_plugins_dir() == tmp_path / "custom"


def test_installed_plugins_dir_explicit_base_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv(claim_kinds_registry.PLUGINS_ROOT_ENV, str(tmp_path / "env"))
    assert claim_kinds_registry.installed_plugins_dir(tmp_path / "explicit") == (
        tmp_path / "explicit"
    )


def test_discover_absent_plugins_root_returns_empty(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert claim_kinds_registry.discover_claim_kind_contributions(missing) == []


def test_discover_finds_one_contribution(tmp_path):
    _write(tmp_path, "acme-org/some-plugin", "bug.json", {"kind": "bug", "priority": 1})
    contributions = claim_kinds_registry.discover_claim_kind_contributions(tmp_path)
    assert len(contributions) == 1
    assert contributions[0].kind == "bug"
    assert contributions[0].priority == 1
    assert contributions[0].label is None


def test_discover_reads_optional_label(tmp_path):
    _write(
        tmp_path,
        "acme-org/some-plugin",
        "bug.json",
        {"kind": "bug", "priority": 1, "label": "ADO bug"},
    )
    contributions = claim_kinds_registry.discover_claim_kind_contributions(tmp_path)
    assert contributions[0].label == "ADO bug"


def test_discover_skips_malformed_entries_without_raising(tmp_path):
    _write(tmp_path, "acme-org/p1", "bad1.json", {"kind": "bug"})  # no priority
    _write(tmp_path, "acme-org/p1", "bad2.json", {"priority": 1})  # no kind
    _write(tmp_path, "acme-org/p1", "bad3.json", ["not", "an", "object"])
    claim_kinds_dir = tmp_path / "acme-org" / "p1" / "claim-kinds"
    (claim_kinds_dir / "bad4.json").write_text("{not valid json", encoding="utf-8")
    _write(tmp_path, "acme-org/p1", "good.json", {"kind": "bug", "priority": 1})
    contributions = claim_kinds_registry.discover_claim_kind_contributions(tmp_path)
    assert len(contributions) == 1
    assert contributions[0].kind == "bug"


def test_discover_multiple_plugins_deterministic_order(tmp_path):
    _write(tmp_path, "acme-org/zzz-plugin", "bug.json", {"kind": "bug", "priority": 9})
    _write(tmp_path, "acme-org/aaa-plugin", "bug.json", {"kind": "bug", "priority": 1})
    contributions = claim_kinds_registry.discover_claim_kind_contributions(tmp_path)
    # sorted plugin path order -> aaa-plugin's contribution comes first
    assert [c.priority for c in contributions] == [1, 9]


def test_effective_pecking_order_merges_onto_defaults(tmp_path):
    _write(tmp_path, "acme-org/p1", "bug.json", {"kind": "bug", "priority": 1})
    merged = claim_kinds_registry.effective_pecking_order(tmp_path)
    # every built-in default kind survives untouched
    for kind, rank in claims_rank.DEFAULT_PECKING_ORDER.items():
        if kind != "bug":
            assert merged[kind] == rank
    assert merged["bug"] == 1


def test_effective_pecking_order_can_add_a_brand_new_kind(tmp_path):
    _write(tmp_path, "acme-org/p1", "widget.json", {"kind": "widget", "priority": 0})
    merged = claim_kinds_registry.effective_pecking_order(tmp_path)
    assert merged["widget"] == 0
    # a brand-new kind is otherwise absent from the built-in table
    assert "widget" not in claims_rank.DEFAULT_PECKING_ORDER


def test_effective_pecking_order_no_contributions_equals_defaults(tmp_path):
    assert claim_kinds_registry.effective_pecking_order(tmp_path) == dict(
        claims_rank.DEFAULT_PECKING_ORDER
    )


def test_effective_label_overrides_only_includes_labeled_contributions(tmp_path):
    _write(
        tmp_path,
        "acme-org/p1",
        "bug.json",
        {"kind": "bug", "priority": 1, "label": "ADO bug"},
    )
    _write(tmp_path, "acme-org/p1", "widget.json", {"kind": "widget", "priority": 5})
    labels = claim_kinds_registry.effective_label_overrides(tmp_path)
    assert labels == {"bug": "ADO bug"}


def test_end_to_end_a_plugin_contributed_kind_ranks_and_labels_correctly(tmp_path):
    """The full path: a plugin declares a brand-new claim kind with its own
    label and priority; claims_rank picks both up with no code change."""
    _write(
        tmp_path,
        "acme-org/ado-bugs",
        "bug.json",
        {"kind": "bug", "priority": -1, "label": "ADO bug"},
    )
    order = claim_kinds_registry.effective_pecking_order(tmp_path)
    labels = claim_kinds_registry.effective_label_overrides(tmp_path)
    claims = [
        {"kind": "pr", "ref": "r1#1", "state": "active"},
        {"kind": "bug", "ref": "r1#2", "state": "active"},
    ]
    summary = claims_rank.summarize_claims(
        claims, pecking_order=order, label_overrides=labels
    )
    # "bug"'s contributed priority (-1) now strictly outranks the built-in
    # "pr" (0), proving the override actually took effect rather than
    # coincidentally winning a tie on ledger order.
    assert order["bug"] == -1
    assert summary == "ADO bug #2 \u00b7 PR #1"
