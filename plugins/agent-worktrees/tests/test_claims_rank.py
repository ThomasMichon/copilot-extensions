"""Tests for the shared claims-pecking-order module (picker-venue-pivots
effort). See `claims_rank`'s own module docstring for the design rationale.
"""
from __future__ import annotations

from agent_worktrees import claims_rank
from agent_worktrees.tracking_claims import ResourceClaim


def test_rank_claims_orders_by_pecking_order_not_ledger_order():
    claims = [
        ResourceClaim(kind="task", ref="task-9f21"),
        ResourceClaim(kind="pr", ref="acme-org/sample-repo#2481"),
        ResourceClaim(kind="codespace", ref="cs-a1c4-relay"),
        ResourceClaim(kind="worktree", ref="88de"),
    ]
    ranked = claims_rank.rank_claims(claims, limit=None)
    assert ranked == [
        ("pr", "acme-org/sample-repo#2481"),
        ("codespace", "cs-a1c4-relay"),
        ("worktree", "88de"),
        ("task", "task-9f21"),
    ]


def test_rank_claims_truncates_to_limit():
    claims = [
        ResourceClaim(kind="pr", ref="r1#1"),
        ResourceClaim(kind="bug", ref="r1#2"),
        ResourceClaim(kind="worktree", ref="a1c4"),
    ]
    assert claims_rank.rank_claims(claims, limit=2) == [
        ("pr", "r1#1"),
        ("bug", "r1#2"),
    ]


def test_rank_claims_ties_keep_ledger_order():
    claims = [
        ResourceClaim(kind="pr", ref="r1#10"),
        ResourceClaim(kind="pr", ref="r1#20"),
    ]
    assert claims_rank.rank_claims(claims, limit=None) == [
        ("pr", "r1#10"),
        ("pr", "r1#20"),
    ]


def test_rank_claims_excludes_non_live_by_default():
    claims = [
        ResourceClaim(kind="pr", ref="r1#1", state="released"),
        ResourceClaim(kind="bug", ref="r1#2", state="active"),
        ResourceClaim(kind="worktree", ref="a1c4", state="abandoned"),
    ]
    assert claims_rank.rank_claims(claims, limit=None) == [("bug", "r1#2")]


def test_rank_claims_live_only_false_includes_everything():
    claims = [
        ResourceClaim(kind="pr", ref="r1#1", state="released"),
        ResourceClaim(kind="bug", ref="r1#2", state="active"),
    ]
    ranked = claims_rank.rank_claims(claims, limit=None, live_only=False)
    assert ranked == [("pr", "r1#1"), ("bug", "r1#2")]


def test_rank_claims_accepts_plain_dicts():
    claims = [
        {"kind": "task", "ref": "task-9f21", "state": "active"},
        {"kind": "pr", "ref": "r1#1", "state": "active"},
    ]
    assert claims_rank.rank_claims(claims, limit=None) == [
        ("pr", "r1#1"),
        ("task", "task-9f21"),
    ]


def test_rank_claims_skips_malformed_entries():
    claims = [
        {"kind": "", "ref": "r1#1"},
        {"kind": "pr", "ref": ""},
        {"kind": "pr", "ref": "r1#2"},
    ]
    assert claims_rank.rank_claims(claims, limit=None) == [("pr", "r1#2")]


def test_rank_claims_unknown_kind_falls_back_to_default_rank():
    claims = [
        ResourceClaim(kind="mystery-kind", ref="x#1"),
        ResourceClaim(kind="task", ref="task-1"),
    ]
    ranked = claims_rank.rank_claims(claims, limit=None)
    # "task" is the lowest NAMED tier; an unrecognized kind ranks even lower
    # (the default fallback), so it comes after "task" here.
    assert ranked == [("task", "task-1"), ("mystery-kind", "x#1")]


def test_format_claim_extracts_trailing_number():
    assert claims_rank.format_claim("pr", "acme-org/sample-repo#2481") == "PR #2481"
    assert claims_rank.format_claim("bug", "acme-org/sample-repo#2410") == "bug #2410"
    assert claims_rank.format_claim("issue", "acme-org/sample-repo#2410") == "bug #2410"


def test_rank_claims_accepts_a_custom_pecking_order():
    claims = [
        ResourceClaim(kind="task", ref="task-1"),
        ResourceClaim(kind="pr", ref="r1#1"),
    ]
    # A caller-supplied table (e.g. a plugin-augmented one) can invert the
    # default order entirely -- this module never hardcodes it.
    custom = {"task": 0, "pr": 1}
    assert claims_rank.rank_claims(claims, limit=None, pecking_order=custom) == [
        ("task", "task-1"),
        ("pr", "r1#1"),
    ]


def test_rank_claims_custom_pecking_order_unknown_kind_ranks_last():
    claims = [
        ResourceClaim(kind="brand-new-kind", ref="x#1"),
        ResourceClaim(kind="pr", ref="r1#1"),
    ]
    custom = {"pr": 0}
    ranked = claims_rank.rank_claims(claims, limit=None, pecking_order=custom)
    assert ranked == [("pr", "r1#1"), ("brand-new-kind", "x#1")]


def test_summarize_claims_accepts_a_custom_pecking_order():
    claims = [
        ResourceClaim(kind="task", ref="task-1"),
        ResourceClaim(kind="pr", ref="r1#1"),
    ]
    custom = {"task": 0, "pr": 1}
    assert claims_rank.summarize_claims(claims, pecking_order=custom) == (
        "task task-1 \u00b7 PR #1"
    )


def test_format_claim_falls_back_to_bare_ref_with_no_hash():
    assert claims_rank.format_claim("codespace", "cs-a1c4-relay") == "codespace cs-a1c4-relay"
    assert claims_rank.format_claim("worktree", "a1c4") == "worktree a1c4"


def test_summarize_claims_joins_prominent_entries():
    claims = [
        ResourceClaim(kind="task", ref="task-9f21"),
        ResourceClaim(kind="pr", ref="acme-org/sample-repo#2481"),
        ResourceClaim(kind="bug", ref="acme-org/sample-repo#2410"),
    ]
    assert claims_rank.summarize_claims(claims) == "PR #2481 \u00b7 bug #2410"


def test_summarize_claims_empty_ledger_is_empty_string():
    assert claims_rank.summarize_claims([]) == ""


def test_summarize_claims_all_non_live_is_empty_string():
    claims = [ResourceClaim(kind="pr", ref="r1#1", state="released")]
    assert claims_rank.summarize_claims(claims) == ""
