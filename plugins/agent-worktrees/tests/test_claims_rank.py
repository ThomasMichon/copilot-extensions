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
    # An unrecognized kind ranks below every NAMED tier (including "session",
    # the lowest named tier as of 2026-09-29) -- the default fallback.
    assert ranked == [("task", "task-1"), ("mystery-kind", "x#1")]


def test_rank_claims_session_is_the_lowest_named_tier():
    """Operator feedback, 2026-09-29: a session claim is the LEAST
    differentiating kind for the CLAIMS column (every worktree effectively
    has one), so it ranks below every other named tier, including "task"."""
    claims = [
        ResourceClaim(kind="session", ref="sess-1"),
        ResourceClaim(kind="task", ref="task-1"),
    ]
    ranked = claims_rank.rank_claims(claims, limit=None)
    assert ranked == [("task", "task-1"), ("session", "sess-1")]


def test_format_claim_extracts_trailing_number():
    """No kind prefix at all for PR/bug/issue -- the ref alone is
    self-explanatory (operator feedback, 2026-09-29)."""
    assert claims_rank.format_claim("pr", "acme-org/sample-repo#2481") == "#2481"
    assert claims_rank.format_claim("bug", "acme-org/sample-repo#2410") == "#2410"
    assert claims_rank.format_claim("issue", "acme-org/sample-repo#2410") == "#2410"


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
        "T task-1 \u00b7 #1"
    )


def test_format_claim_falls_back_to_bare_ref_with_no_hash():
    assert claims_rank.format_claim("codespace", "cs-a1c4-relay") == "CS cs-a1c4-relay"
    # #3307 follow-up: "worktree" now gets its own "WT" label prefix (used
    # when the ref doesn't parse as the machine/project/id convention --
    # see test_format_claim_worktree_* below for the parsed-ref cases).
    assert claims_rank.format_claim("worktree", "a1c4") == "WT a1c4"


def test_summarize_claims_joins_prominent_entries():
    claims = [
        ResourceClaim(kind="task", ref="task-9f21"),
        ResourceClaim(kind="pr", ref="acme-org/sample-repo#2481"),
        ResourceClaim(kind="bug", ref="acme-org/sample-repo#2410"),
    ]
    assert claims_rank.summarize_claims(claims) == "#2481 \u00b7 #2410"


def test_summarize_claims_empty_ledger_is_empty_string():
    assert claims_rank.summarize_claims([]) == ""


def test_summarize_claims_all_non_live_is_empty_string():
    claims = [ResourceClaim(kind="pr", ref="r1#1", state="released")]
    assert claims_rank.summarize_claims(claims) == ""


# --- #3307 worktrees-pivot-ux-overhaul follow-up: cross-repo abbreviations,
# URL resolution, and the structured claim_entries_for_worktree() list -----

def test_format_claim_worktree_same_repo_has_wt_prefix():
    ref = "host-win/copilot-extensions/private-downstream-repo-testchamber-4b8a"
    assert claims_rank.format_claim(
        "worktree", ref, own_repo="copilot-extensions",
    ) == "WT 4b8a"


def test_format_claim_worktree_cross_repo_names_the_repo():
    """Operator's own example: '<repo>:<last4>' when the claimed child
    worktree lives in a DIFFERENT repo than the claiming worktree."""
    ref = "host-win/dotfiles/2026-09-26-retry-logic"
    assert claims_rank.format_claim(
        "worktree", ref, own_repo="copilot-extensions",
    ) == "WT dotfiles:ogic"


def test_format_claim_worktree_unknown_own_repo_never_asserts_cross_repo():
    """own_repo not passed (an older caller) -- never guesses cross-repo,
    even though the ref carries a project segment."""
    ref = "host-win/dotfiles/2026-09-26-retry-logic"
    assert claims_rank.format_claim("worktree", ref) == "WT ogic"


def test_format_claim_pr_same_repo_is_bare_number():
    """No 'PR' prefix at all -- the ref alone is self-explanatory
    (operator feedback, 2026-09-29)."""
    assert claims_rank.format_claim(
        "pr", "copilot-extensions#2481", own_repo="copilot-extensions",
    ) == "#2481"
    # repo unknown on our side -- never asserts cross-repo either.
    assert claims_rank.format_claim("pr", "copilot-extensions#2481") == "#2481"


def test_format_claim_pr_cross_repo_names_the_short_repo_not_the_owner():
    assert claims_rank.format_claim(
        "pr", "acme-org/sample-repo#2481", own_repo="copilot-extensions",
    ) == "sample-repo#2481"


def test_format_claim_pr_parses_a_full_github_url_ref():
    url = "https://github.com/acme-org/sample-repo/pull/2481"
    assert claims_rank.format_claim(
        "pr", url, own_repo="copilot-extensions",
    ) == "sample-repo#2481"
    assert claims_rank.format_claim(
        "bug", "https://github.com/acme-org/sample-repo/issues/17",
        own_repo="sample-repo",
    ) == "#17"


def test_claim_url_resolves_github_pr_and_issue_refs():
    assert claims_rank.claim_url("pr", "acme-org/sample-repo#2481") == (
        "https://github.com/acme-org/sample-repo/pull/2481")
    assert claims_rank.claim_url("bug", "acme-org/sample-repo#17") == (
        "https://github.com/acme-org/sample-repo/issues/17")
    # No owner/repo on the ref -- nothing to build a URL from.
    assert claims_rank.claim_url("pr", "#2481") is None
    # A non-PR-like kind with a raw URL ref passes it through as-is.
    assert claims_rank.claim_url("task", "https://example.com/t/1") == (
        "https://example.com/t/1")
    # A non-PR-like kind with a bare ref has no URL.
    assert claims_rank.claim_url("task", "task-9f21") is None


def test_format_claim_pr_parses_a_full_non_github_forge_url_ref():
    # Self-hosted Gitea/Forgejo use "pulls" (plural), not GitHub's "pull".
    gitea_url = "https://git.example.org/acme-org/sample-repo/pulls/7961"
    assert claims_rank.format_claim(
        "pr", gitea_url, own_repo="copilot-extensions",
    ) == "sample-repo#7961"
    # Same-repo (own_repo matches the ref's repo) -- bare number only.
    assert claims_rank.format_claim(
        "pr", gitea_url, own_repo="sample-repo",
    ) == "#7961"
    # GitLab uses "merge_requests".
    gitlab_url = "https://gitlab.example.org/acme-org/sample-repo/merge_requests/3"
    assert claims_rank.format_claim(
        "pr", gitlab_url, own_repo="sample-repo",
    ) == "#3"
    # "issues" is shared across forges.
    assert claims_rank.format_claim(
        "bug", "https://git.example.org/acme-org/sample-repo/issues/17",
        own_repo="sample-repo",
    ) == "#17"


def test_claim_url_hyperlinks_a_non_github_forge_ref_as_is():
    # A ref that is already a full URL on a non-GitHub host must be
    # hyperlinked unchanged -- never rewritten to a reconstructed
    # github.com URL (which would silently point at the wrong site).
    gitea_url = "https://git.example.org/acme-org/sample-repo/pulls/7961"
    assert claims_rank.claim_url("pr", gitea_url) == gitea_url


def test_claim_entries_for_worktree_pairs_label_with_url():
    claims = [
        ResourceClaim(kind="pr", ref="acme-org/sample-repo#2481"),
        ResourceClaim(
            kind="worktree",
            ref="host-win/copilot-extensions/private-downstream-repo-testchamber-4b8a",
        ),
    ]
    entries = claims_rank.claim_entries_for_worktree(
        claims, own_repo="copilot-extensions",
    )
    assert entries == [
        {"label": "sample-repo#2481",
         "url": "https://github.com/acme-org/sample-repo/pull/2481"},
        {"label": "WT 4b8a", "url": None},
    ]


def test_claim_entries_for_worktree_backfills_active_pr_like_summary_does():
    active_pr = {"repo": "acme-org/sample-repo", "number": 91, "state": "open"}
    entries = claims_rank.claim_entries_for_worktree(
        [], active_pr, own_repo="copilot-extensions",
    )
    assert entries == [
        {"label": "sample-repo#91",
         "url": "https://github.com/acme-org/sample-repo/pull/91"},
    ]


# --- operator feedback, 2026-09-29: condensed short-form kind prefixes ----

def test_format_claim_short_form_kind_prefixes():
    """Every non-PR-like kind gets a terse, fixed short-form prefix so a
    claims-list cell stays scannable across many different kinds sharing
    one narrow column."""
    assert claims_rank.format_claim("session", "sess-1") == "SESS sess-1"
    assert claims_rank.format_claim("codespace", "cs-a1c4-relay") == "CS cs-a1c4-relay"
    assert claims_rank.format_claim("container", "ct-b2d5-fleet") == "CT ct-b2d5-fleet"
    assert claims_rank.format_claim("task", "task-9f21") == "T task-9f21"
    assert claims_rank.format_claim("bridge", "bridge-1") == "BR bridge-1"
    assert claims_rank.format_claim("ssh", "dev6") == "SSH dev6"


def test_format_claim_pr_like_override_still_prefixes():
    """A plugin-contributed label override (e.g. an ADO-sourced 'bug' that
    isn't a native GitHub issue) still applies its prefix -- only the
    UNLABELED default is bare (operator feedback, 2026-09-29)."""
    assert claims_rank.format_claim(
        "bug", "r1#2", label_overrides={"bug": "ADO bug"},
    ) == "ADO bug #2"
    assert claims_rank.format_claim(
        "bug", "acme-org/sample-repo#2", own_repo="copilot-extensions",
        label_overrides={"bug": "ADO bug"},
    ) == "ADO bug sample-repo#2"

