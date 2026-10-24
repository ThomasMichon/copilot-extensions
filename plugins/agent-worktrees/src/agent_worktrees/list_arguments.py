"""One data-only declaration for both ordinary list and early feed entry."""

from __future__ import annotations

ARGUMENTS = (
    ("--json", {"action": "store_true", "help": "JSON output mode (stdout is JSON only)"}),
    ("--current", {"action": "store_true",
                   "help": "Read the resident current-roster contract; never scan or recompute in the caller."}),
    ("--mux-details", {"action": "store_true",
                       "help": "Include mux session attached/detached status (JSON only)"}),
    ("--tracking-status", {"default": "all",
                           "choices": ["active", "complete", "finalized", "orphaned", "archived", "all"],
                           "help": "Filter by tracking status (default: all). 'archived' also needs --all."}),
    ("--all", {"action": "store_true",
               "help": "Include worktrees whose directories no longer exist on disk"}),
    ("--include-other-platforms", {"action": "store_true",
                                   "help": "Include worktrees from other platforms (e.g. Windows when on Linux)"}),
    ("--classify", {"action": "store_true",
                    "help": "Include git state classification (state/ahead/behind/dirty; JSON only). Slower: ~5 git calls per worktree."}),
    ("--cache-only", {"action": "store_true",
                      "help": "Cache-only fast paint from session-render fields, without session history or git classification."}),
    ("--profile-assignment-history", {"action": "store_true",
                                      "help": "Include bounded profile_assignments history and the latest assignment in each JSON worktree row. Ordinary and cache-polled rows include only current_profile_assignment."}),
    ("--stream", {"action": "store_true",
                  "help": "Emit newline-delimited JSON with a complete current-roster begin/worktree/done publication barrier. Implies --json. Use --follow for held incremental updates."}),
    ("--follow", {"action": "store_true",
                  "help": "Hold a daemon-owned current-roster feed until cancelled; emit an atomic snapshot, incremental commits, and cutover notices. Implies --current and --stream; requires worktrees-feed-v1."}),
    ("--fresh", {"action": "store_true",
                 "help": "Bypass the coalescing result cache; current-roster reads retain resident authority."}),
    ("--worktree-id", {"help": "Restrict the listing to one exact or unique-suffix ID"}),
    ("--codename", {"default": None,
                    "help": "Restrict the listing to the worktree with this codename (alternative to --worktree-id)"}),
    ("--refresh", {"action": "store_true",
                   "help": "Before listing one --worktree-id, repair its missing session registry and refresh bound/mux liveness"}),
    ("--glance", {"action": "store_true",
                  "help": "Compact, agent-digestible summary of active worktrees."}),
)


def add_parsers(sub) -> None:
    parser = sub.add_parser("list", help="List worktrees from tracking records")
    for flag, options in ARGUMENTS:
        parser.add_argument(flag, **options)
