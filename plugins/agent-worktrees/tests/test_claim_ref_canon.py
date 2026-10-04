"""Tests for the Phase 6 canonical claim-ref encoding
(worktree-claims-transitive-finalization effort, 2026-10-04).

Validates the Validation Plan's own Phase 6 item: every existing persisted
ref shape (the structured ``worktree``/``session`` grammar, a PR URL, the
``owner/repo#N`` PR shorthand, and an opaque kind-specific id) still
round-trips correctly once the new canonical form's parser lands -- proving
the migration is genuinely additive, not a silent breaking change for any
machine's existing ledger. Also exercises the same unwrap duplicated
locally in ``claims_rank`` (which stays import-free of siblings by design)
and the call sites wired into ``cleanup``/``sweep``.
"""
from __future__ import annotations

from agent_worktrees import claims_rank
from agent_worktrees.tracking_claims import (
    canonicalize_ref,
    decanonicalize_ref,
    format_claim_ref,
    parse_claim_ref,
)


def test_worktree_ref_round_trips_through_canonical_form():
    legacy = format_claim_ref("lambda-core", "aperture-labs", "wt-123", "sess1")
    canon = canonicalize_ref("worktree", legacy)
    assert canon == "worktree:lambda-core:aperture-labs/wt-123#sess1"
    assert decanonicalize_ref(canon) == legacy
    # And the canonical form parses identically to the legacy one.
    assert parse_claim_ref(canon) == parse_claim_ref(legacy)


def test_worktree_ref_round_trips_without_session():
    legacy = format_claim_ref("wheatley", "copilot-extensions", "wt-abc")
    canon = canonicalize_ref("worktree", legacy)
    assert canon == "worktree:wheatley:copilot-extensions/wt-abc"
    assert decanonicalize_ref(canon) == legacy


def test_unqualified_worktree_ref_falls_back_to_opaque_canonical_form():
    # No machine/project -- not qualified, so canonicalize can't build the
    # structured form; it degrades to the opaque `<kind>::<ref>` shape and
    # still round-trips losslessly.
    legacy = "bare-worktree-id"
    canon = canonicalize_ref("worktree", legacy)
    assert canon == "worktree::bare-worktree-id"
    assert decanonicalize_ref(canon) == legacy


def test_pr_shorthand_ref_round_trips_through_canonical_form():
    legacy = "acme-org/sample-repo#2481"
    canon = canonicalize_ref("pr", legacy)
    assert canon == "pr::acme-org/sample-repo#2481"
    assert decanonicalize_ref(canon) == legacy


def test_pr_url_ref_round_trips_through_canonical_form():
    legacy = "https://github.com/acme-org/sample-repo/pull/2481"
    canon = canonicalize_ref("pr", legacy)
    assert decanonicalize_ref(canon) == legacy


def test_opaque_kind_ref_round_trips_through_canonical_form():
    for kind, legacy in (
        ("codespace", "cs-a1c4-relay"),
        ("container", "ct-9f21"),
        ("task", "task-9f21"),
        ("bridge", "wheatley"),
        ("ssh", "borealis"),
        ("effort", "worktree-claims-transitive-finalization"),
    ):
        canon = canonicalize_ref(kind, legacy)
        assert canon == f"{kind}::{legacy}"
        assert decanonicalize_ref(canon) == legacy


def test_canonicalize_is_idempotent():
    legacy = format_claim_ref("lambda-core", "aperture-labs", "wt-123")
    once = canonicalize_ref("worktree", legacy)
    twice = canonicalize_ref("worktree", once)
    assert once == twice


def test_decanonicalize_never_mistakes_a_url_scheme_for_a_kind():
    # "https" is not a known claim kind, so a PR/issue URL ref must pass
    # through completely unchanged rather than being misparsed as
    # kind="https", system="//github.com/acme-org/sample-repo/pull", ...
    url = "https://github.com/acme-org/sample-repo/pull/2481"
    assert decanonicalize_ref(url) == url


def test_decanonicalize_passes_through_plain_legacy_refs_unchanged():
    for ref in (
        "acme-org/sample-repo#2481",
        "lambda-core/aperture-labs/wt-123#sess1",
        "cs-a1c4-relay",
        "",
    ):
        assert decanonicalize_ref(ref) == ref


def test_claims_rank_claim_url_accepts_canonical_pr_shorthand():
    canon = canonicalize_ref("pr", "acme-org/sample-repo#2481")
    assert (
        claims_rank.claim_url("pr", canon)
        == "https://github.com/acme-org/sample-repo/pull/2481"
    )


def test_claims_rank_claim_url_accepts_canonical_pr_url():
    legacy = "https://github.com/acme-org/sample-repo/pull/2481"
    canon = canonicalize_ref("pr", legacy)
    assert claims_rank.claim_url("pr", canon) == legacy


def test_claims_rank_format_claim_accepts_canonical_worktree_ref():
    legacy = format_claim_ref("lambda-core", "aperture-labs", "wt-123")
    canon = canonicalize_ref("worktree", legacy)
    assert claims_rank.format_claim(
        "worktree", canon
    ) == claims_rank.format_claim("worktree", legacy)
