"""Tests for the Phase 6 canonical claim-ref encoding.

Phase 6 of ``worktree-claims-transitive-finalization``, 2026-10-04 -- a
cross-repo *tracking-only* effort whose README/journal live in a separate
downstream control-plane repo (not this one); the implementation itself
lands here, in agent-worktrees. See that effort's README for the full
design rationale and the four decisions this module's docstrings
summarize inline.

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

import types

from agent_worktrees import claims_rank, cleanup, sweep
from agent_worktrees.tracking_claims import (
    canonicalize_ref,
    decanonicalize_ref,
    format_claim_ref,
    parse_claim_ref,
)


def test_worktree_ref_round_trips_through_canonical_form():
    legacy = format_claim_ref("lambda-core", "example-project", "wt-123", "sess1")
    canon = canonicalize_ref("worktree", legacy)
    assert canon == "worktree:lambda-core:example-project/wt-123#sess1"
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
        ("workdir", "pending-run:abc"),
    ):
        canon = canonicalize_ref(kind, legacy)
        assert canon == f"{kind}::{legacy}"
        assert decanonicalize_ref(canon) == legacy


def test_canonicalize_is_idempotent():
    legacy = format_claim_ref("lambda-core", "example-project", "wt-123")
    once = canonicalize_ref("worktree", legacy)
    twice = canonicalize_ref("worktree", once)
    assert once == twice


def test_session_ref_round_trips_through_canonical_form():
    legacy = format_claim_ref("lambda-core", "example-project", "wt-123", "sess1")
    canon = canonicalize_ref("session", legacy)
    assert canon == "session:lambda-core:example-project/wt-123#sess1"
    assert decanonicalize_ref(canon) == legacy


def test_session_ref_without_session_suffix_round_trips():
    # Regression: claims_rank's local duplicate unwrap only special-cased
    # "worktree", silently dropping the machine for a session ref with no
    # trailing "#session" (SESS m/p/w -> SESS p/w after canonicalization).
    legacy = format_claim_ref("lambda-core", "example-project", "wt-123")
    canon = canonicalize_ref("session", legacy)
    assert decanonicalize_ref(canon) == legacy
    assert claims_rank.format_claim("session", canon) == claims_rank.format_claim(
        "session", legacy
    )


def test_canonicalize_rejects_empty_ref():
    # An empty ref can't round-trip through the canonical grammar (the key
    # segment requires at least one character), so canonicalize_ref must
    # leave it alone rather than producing an unrecoverable "<kind>::".
    assert canonicalize_ref("task", "") == ""
    assert decanonicalize_ref("task::") == "task::"


def test_decanonicalize_restricts_to_expected_kinds():
    # A canonical ref for an unrelated kind must not be accepted by a call
    # site that only understands a narrower set -- e.g. a PR-only parser
    # must reject (pass through unchanged, never unwrap) a "task"-kind
    # canonical ref that happens to decode into something PR-shaped.
    mismatched = canonicalize_ref("task", "owner/repo#42")
    assert mismatched == "task::owner/repo#42"
    assert (
        decanonicalize_ref(mismatched, expected_kinds=frozenset({"pr"}))
        == mismatched
    )
    # ... but is still accepted (and parses as a PR) when the PR-restricted
    # unwrap is actually given a genuine PR-kind canonical ref.
    genuine = canonicalize_ref("pr", "owner/repo#42")
    assert (
        decanonicalize_ref(genuine, expected_kinds=frozenset({"pr"}))
        == "owner/repo#42"
    )


def test_cleanup_pr_claim_target_rejects_mismatched_kind_canonical_ref():
    mismatched = canonicalize_ref("task", "owner/repo#42")
    prcfg = types.SimpleNamespace(provider="github", api_base="")
    # Must NOT be silently accepted as a PR target -- a task ref that only
    # happens to decode into something PR-shaped once unwrapped must still
    # fail to resolve (the exact bug this regression guards against).
    assert cleanup._pr_claim_target(mismatched, prcfg) is None


def test_sweep_github_pr_view_args_rejects_mismatched_kind_canonical_ref():
    mismatched = canonicalize_ref("task", "owner/repo#42")
    assert sweep._github_pr_view_args(mismatched) is None


def test_canonicalize_does_not_mistake_a_different_kinds_opaque_ref_as_already_canonical():
    # An opaque "task" ref that happens to already read like another kind's
    # canonical form ("pr::foo") must still be wrapped as task -- treating
    # it as already-canonical would silently mislabel it as a pr ref and
    # break the round trip (the exact bug this guards against).
    opaque_legacy = "pr::foo"
    canon = canonicalize_ref("task", opaque_legacy)
    assert canon == "task::pr::foo"
    assert decanonicalize_ref(canon) == opaque_legacy


def test_decanonicalize_never_mistakes_a_url_scheme_for_a_kind():
    # "https" is not a known claim kind, so a PR/issue URL ref must pass
    # through completely unchanged rather than being misparsed as
    # kind="https", system="//github.com/acme-org/sample-repo/pull", ...
    url = "https://github.com/acme-org/sample-repo/pull/2481"
    assert decanonicalize_ref(url) == url


def test_decanonicalize_passes_through_plain_legacy_refs_unchanged():
    for ref in (
        "acme-org/sample-repo#2481",
        "lambda-core/example-project/wt-123#sess1",
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


def test_claims_rank_format_claim_accepts_canonical_ref_in_generic_fallback():
    # The generic (non-worktree, non-PR-like) branch of format_claim had no
    # unwrap of its own -- a canonical codespace/container/task/ssh/workdir
    # ref must render identically to its legacy shape, not fall through to
    # the raw `kind:: <wrapped>` form.
    for kind, legacy in (
        ("codespace", "cs-a1c4-relay"),
        ("task", "task-9f21#7"),
        ("workdir", "pending-run:abc"),
    ):
        canon = canonicalize_ref(kind, legacy)
        assert claims_rank.format_claim(kind, canon) == claims_rank.format_claim(
            kind, legacy
        )


def test_claims_rank_format_claim_accepts_canonical_worktree_ref():
    legacy = format_claim_ref("lambda-core", "example-project", "wt-123")
    canon = canonicalize_ref("worktree", legacy)
    assert claims_rank.format_claim(
        "worktree", canon
    ) == claims_rank.format_claim("worktree", legacy)
