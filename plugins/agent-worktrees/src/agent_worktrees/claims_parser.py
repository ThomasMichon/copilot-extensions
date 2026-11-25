"""Argument registration for the claims command family."""

from __future__ import annotations

import argparse

from . import derived_claims_cli, producer_views_cli

OPERATIONS = frozenset(
    {
        "show",
        "ownership",
        "binding",
        "owner",
        "handoff",
        "add",
        "release",
        "annotate",
        "settle",
        "sweep",
        "reconcile-at-rest",
        "mirror-status",
        "cleanup",
        "orphans",
        "fleet-audit",
        "find",
        "transitive",
        "retained",
        "history",
    }
)


def add_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register claims operations and their explicit selectors."""
    p = sub.add_parser(
        "claims",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Inspect or manage resource claims; defaults to the current worktree's full ledger.",
        description=(
            "Use 'show --worktree-id ID' for a full ledger, including outbound resources, "
            "the creator relation and best-effort inbound tasks. Worktree selectors are "
            "options, not command-position tokens."
        ),
        epilog=(
            "Contract: agent-worktrees.claims-cli/v1\n"
            "Capability: explicit-worktree-selector-v1\n"
            "Legacy positional worktree selectors remain deprecated compatibility forms "
            "during the documented provider upgrade/rollback window."
        ),
    )
    p.add_argument(
        "target",
        nargs="*",
        default=None,
        metavar="COMMAND",
        help=(
            "'show' for a full ledger; 'add <kind> <ref>', 'release <ref>', "
            "'annotate <ref>' or 'settle <ref>' for one claim; 'owner <kind> <ref>' "
            "for reverse ownership; 'binding <task-ref>'; 'handoff <operation>'; "
            "'sweep'; 'reconcile-at-rest'; 'cleanup'; 'orphans'; 'fleet-audit'; "
            "'find pr'; 'transitive'; 'retained'; 'history <ref>'; 'ownership'. "
            "Select a worktree with --worktree-id, not a bare ID."
        ),
    )
    p.add_argument(
        "--worktree-id",
        dest="claim_worktree_ids",
        action="append",
        metavar="ID",
        help=(
            "Worktree to inspect or whose claims to manage (default: current where applicable). "
            "Repeat for reconcile-at-rest or cleanup; other operations accept one."
        ),
    )
    p.add_argument(
        "--claim-ref",
        dest="claim_refs",
        action="append",
        metavar="REF",
        help="With cleanup: select an orphaned resource reference; repeat for several.",
    )
    p.add_argument(
        "--remove",
        action="store_true",
        help="With release: drop the claim entry rather than marking it released.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="With sweep/cleanup/reconcile-at-rest: apply the reported changes (default: preview).",
    )
    p.add_argument("--note", default="", help="With add/annotate: an optional human label.")
    p.add_argument(
        "--status",
        default=None,
        help="With mirror-status: disposition to mirror, such as active, at-rest or released.",
    )
    p.add_argument(
        "--holder",
        default=None,
        dest="claim_holder",
        help="With mirror-status: mirrored lease holder (default: agent-dispatch).",
    )
    p.add_argument("--released", action="store_true", help="With settle: mark the claim released.")
    p.add_argument(
        "--expected-record-nonce", help="With settle: pin the owner's record incarnation."
    )
    p.add_argument("--expected-claim-nonce", help="With settle: pin the claim incarnation.")
    p.add_argument(
        "--expected-kind",
        help="With settle: pin the claim kind; all pins require --owner-ref.",
    )
    p.add_argument(
        "--worktree",
        default=None,
        dest="release_worktree",
        help="Deprecated owner-worktree selector; use --worktree-id.",
    )
    p.add_argument(
        "--owner-ref",
        default=None,
        dest="claim_owner_ref",
        help=(
            "With ownership: filter the disposable ownership snapshot; with add/settle/binding: "
            "select a qualified machine/project/worktree_id owner across local projects. "
            "Cross-machine owners retain the lease-mirror path."
        ),
    )
    p.add_argument(
        "--to",
        nargs="+",
        default=None,
        dest="handoff_to",
        metavar="VALUE",
        help="With handoff offer: qualified consumer followed by claim refs, ending at the next option.",
    )
    p.add_argument(
        "--reason",
        default="",
        help="With handoff decline/cancel: required explanation.",
    )
    p.add_argument("--actor", default="", dest="claim_actor", help=argparse.SUPPRESS)
    p.add_argument(
        "--all-states", action="store_true", help="With owner: include released claims."
    )
    p.add_argument(
        "--max-depth", type=int, default=8, help="With retained: traversal depth (1-32)."
    )
    p.add_argument(
        "--max-nodes", type=int, default=256, help="With retained: record limit (1-1024)."
    )
    p.add_argument(
        "--repo",
        default=None,
        dest="claim_repo",
        help="With find pr: restrict the repository; omitted, search registered projects.",
    )
    p.add_argument(
        "--state",
        default="open",
        dest="claim_state",
        choices=("open", "closed", "merged", "all"),
        help="With find pr: tracked PR state (default: open).",
    )
    p.add_argument(
        "--live",
        action="store_true",
        dest="claim_live",
        help="With find pr: verify tracked PR state with the provider.",
    )
    p.add_argument(
        "--remote",
        action="store_true",
        help="With history: include remote mirrored events.",
    )
    p.add_argument("--sweep-observation", action="store_true", help=argparse.SUPPRESS)
    producer_views_cli.add_arguments(p)
    derived_claims_cli.add_arguments(p)
    p.add_argument("--json", action="store_true", help="Emit JSON on stdout.")
