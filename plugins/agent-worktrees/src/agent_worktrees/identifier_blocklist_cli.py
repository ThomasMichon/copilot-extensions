"""``identifiers`` CLI dispatch -- the identifier-blocklist sweep's command surface."""

from __future__ import annotations

from . import config as cfg
from . import identifier_blocklist as iblk
from . import output, repos as repos_mod


def _core():
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    sub.add_parser(
        "identifiers",
        help="Cross-repo identifier-blocklist sweep (run 'identifiers' for usage)",
    )


def _identifiers_usage() -> None:
    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"
    print(f"Usage: {project} identifiers <command>")
    print()
    print("Discovers and aggregates '.identifier-blocklist/block-for-<tier>.yaml'")
    print("denylists from every locally registered repo, scoped to a target repo's")
    print("own audience-exposure tier ('repos set-visibility').")
    print()
    print("Commands:")
    print("  sweep [--repo NAME] [--json]   Aggregate applicable blocklists")
    print("        [--format ci]            token|reason lines (default; --json for")
    print("                                 structured output)")
    print("                                 NAME defaults to the active project")
    print()
    print("See docs/identifier-blocklist.md for the full convention.")


def cmd_identifiers_dispatch(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        _identifiers_usage()
        return 0

    sub, rest = argv[0], argv[1:]

    if sub == "sweep":
        json_out = "--json" in rest
        target = None
        if "--repo" in rest:
            idx = rest.index("--repo")
            if idx + 1 < len(rest):
                target = rest[idx + 1]
        if target is None:
            target = cfg.active_project()

        entries = iblk.sweep(target)
        target_entry = repos_mod.find_repo(target) if target else None
        target_rank = iblk.resolve_visibility_rank(target_entry)
        resolved_visibility = next(
            (name for name, rank in repos_mod.VISIBILITY_RANK.items() if rank == target_rank),
            "public",
        )

        if json_out:
            _core()._json_output(
                {
                    "target": target,
                    "target_visibility": (target_entry.visibility if target_entry else ""),
                    "resolved_visibility": resolved_visibility,
                    "tiers_applied": iblk.applicable_tiers(target_rank),
                    "entries": [
                        {
                            "token": e.token,
                            "reason": e.reason,
                            "source_repo": e.source_repo,
                            "source_tier": e.source_tier,
                        }
                        for e in entries
                    ],
                }
            )
            return 0

        if not entries:
            output.warn(
                f"No applicable blocklist entries found for target "
                f"'{target or '(unresolved)'}' (visibility={resolved_visibility})."
            )
            return 0
        print(iblk.render_ci_format(entries))
        return 0

    output.err(f"Unknown identifiers subcommand: {sub}")
    _identifiers_usage()
    return 1
