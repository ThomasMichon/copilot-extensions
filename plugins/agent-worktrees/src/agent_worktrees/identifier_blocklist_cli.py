"""``identifiers`` CLI dispatch -- the identifier-blocklist sweep's command surface."""

from __future__ import annotations

import sys

from . import config as cfg
from . import identifier_blocklist as iblk
from . import output, repos as repos_mod

_VALID_FORMATS = ("ci", "json")


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
    print("  sweep [--repo NAME] [--format ci|json] [--json]")
    print("                                 Aggregate applicable blocklists.")
    print("                                 ci (default): token|reason lines.")
    print("                                 --json / --format json: structured output.")
    print("                                 NAME defaults to the active project.")
    print()
    print("See docs/identifier-blocklist.md for the full convention.")


def _parse_sweep_args(rest: list[str]) -> tuple[str | None, str, str | None]:
    """Parse ``sweep``'s own arguments strictly.

    Returns ``(target, fmt, error)``. ``error``, when not ``None``, names
    exactly what was wrong (missing value, unknown flag, unknown format) --
    the caller must surface it and refuse to run, never silently fall back
    to a default on malformed input.
    """
    target: str | None = None
    fmt = "ci"
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--repo":
            if i + 1 >= len(rest):
                return None, fmt, "--repo requires a value"
            target = rest[i + 1]
            i += 2
            continue
        if arg == "--format":
            if i + 1 >= len(rest):
                return None, fmt, "--format requires a value"
            fmt = rest[i + 1]
            i += 2
            continue
        if arg == "--json":
            fmt = "json"
            i += 1
            continue
        return None, fmt, f"unknown argument: {arg}"
    if fmt not in _VALID_FORMATS:
        return None, fmt, f"unknown --format '{fmt}' (expected one of: {', '.join(_VALID_FORMATS)})"
    return target, fmt, None


def cmd_identifiers_dispatch(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        _identifiers_usage()
        return 0

    sub, rest = argv[0], argv[1:]

    if sub == "sweep":
        target, fmt, error = _parse_sweep_args(rest)
        if error:
            output.err(f"identifiers sweep: {error}")
            _identifiers_usage()
            return 1
        if target is None:
            target = cfg.active_project()

        parse_error: iblk.BlocklistParseError | None = None
        try:
            entries = iblk.sweep(target)
        except iblk.BlocklistParseError as exc:
            # Preserve every entry that DID parse successfully from other,
            # unaffected sources -- one broken peer's blocklist must not
            # discard everyone else's valid entries. Still fail loudly
            # (nonzero exit, error on stderr/output.err) so the breakage is
            # visible and fixed, rather than silently degrading enforcement.
            parse_error = exc
            entries = exc.partial_entries

        target_entry = repos_mod.find_repo(target) if target else None
        target_rank = iblk.resolve_visibility_rank(target_entry)
        resolved_visibility = next(
            (name for name, rank in repos_mod.VISIBILITY_RANK.items() if rank == target_rank),
            "public",
        )

        if fmt == "json":
            _core()._json_output(
                {
                    "target": target,
                    "target_visibility": (target_entry.visibility if target_entry else ""),
                    "resolved_visibility": resolved_visibility,
                    "tiers_applied": iblk.applicable_tiers(target_rank),
                    "error": str(parse_error) if parse_error else None,
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
            return 1 if parse_error else 0

        # ci format: stdout must carry ONLY the token|reason lines (or
        # nothing at all) -- this is meant to be piped straight into a
        # consumer (the live guard, or `secret set`) that may well continue
        # past this process's own nonzero exit (a native command's exit
        # code is easy to ignore in a pipeline/script). On failure, emit
        # NOTHING on stdout -- a partial denylist silently replacing a
        # complete one is worse than an obviously-empty one -- and route
        # every diagnostic to stderr instead.
        if parse_error:
            print(f"identifiers sweep: {parse_error}", file=sys.stderr)
            return 1
        if not entries:
            print(
                f"identifiers sweep: no applicable blocklist entries found for "
                f"target '{target or '(unresolved)'}' (visibility={resolved_visibility}).",
                file=sys.stderr,
            )
            return 0
        print(iblk.render_ci_format(entries))
        return 0

    output.err(f"Unknown identifiers subcommand: {sub}")
    _identifiers_usage()
    return 1
