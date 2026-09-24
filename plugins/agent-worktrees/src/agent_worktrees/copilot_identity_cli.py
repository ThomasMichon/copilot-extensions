"""``copilot-identity`` CLI dispatch: enforce Copilot CLI's own account.

Prototype for ThomasMichon/copilot-extensions#3296 -- see
``copilot_identity.py`` for the resolution/enforcement logic this wraps.
"""

from __future__ import annotations

from . import config as cfg
from . import copilot_identity, output


def _core():
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    sub.add_parser(
        "copilot-identity",
        help="Ensure Copilot CLI's own login matches the intended account "
        "(run 'copilot-identity' for usage)",
    )


def _usage() -> None:
    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"
    print(f"Usage: {project} copilot-identity <command>")
    print()
    print("Ensures Copilot CLI's own inference identity (distinct from the")
    print("'gh' CLI account -- see 'repos account'/'repos account-for')")
    print("matches the intended account for a repo/machine, non-interactively")
    print("re-logging in via an already-cached 'gh' token when it doesn't.")
    print()
    print("Commands:")
    print("  status [--repo NAME] [--json]        Show current vs. intended login")
    print("  ensure [<account>] [--repo NAME]      Ensure Copilot is logged in as")
    print("         [--dry-run] [--force] [--json]  <account> (or the resolved one)")
    print()
    print("--force overrides the safety gate that otherwise refuses to switch")
    print("while any other Copilot CLI process is running on this machine --")
    print("switching underneath a running session risks splicing its billing")
    print("across accounts, invalidating its prompt cache, and auth errors.")
    print()
    print("'ensure' is a no-op unless this machine's config.yaml sets")
    print("  copilot_identity_switch_enabled: true")
    print("(default false -- opt in per machine, no environment-variable")
    print("override).")
    print()
    print("Examples:")
    print(f"  {project} copilot-identity status --repo my-personal-repo")
    print(f"  {project} copilot-identity ensure --repo my-work-repo")
    print(f"  {project} copilot-identity ensure some-account-login")


def _resolve_target(rest: list[str]) -> tuple[str | None, str | None]:
    """Return (explicit_account, repo) parsed from positional/--repo args."""
    repo = None
    account = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--repo" and i + 1 < len(rest):
            repo = rest[i + 1]
            i += 2
            continue
        if tok in ("--json", "--dry-run", "--force"):
            i += 1
            continue
        if account is None and not tok.startswith("-"):
            account = tok
        i += 1
    return account, repo


def cmd_copilot_identity_dispatch(argv: list[str]) -> int:
    """Route the top-level ``copilot-identity`` subcommands."""
    if not argv or argv[0] in ("--help", "-h"):
        _usage()
        return 0
    sub = argv[0]
    rest = argv[1:]
    if "--help" in rest or "-h" in rest:
        _usage()
        return 0
    json_out = "--json" in rest

    if sub == "status":
        _, repo = _resolve_target(rest)
        current = copilot_identity.current_login()
        target = copilot_identity.intended_account(repo)
        matches = bool(target) and current == target
        if json_out:
            _core()._json_output(
                {
                    "current": current,
                    "target": target,
                    "matches": matches,
                    "repo": repo,
                }
            )
            return 0 if (target is None or matches) else 1
        print(f"  current login:  {current or '(unknown)'}")
        print(f"  intended login: {target or '(no preference resolved)'}")
        if target:
            if matches:
                output.ok("Copilot CLI identity matches.")
            else:
                output.warn(
                    "Copilot CLI identity does NOT match -- run "
                    "'copilot-identity ensure' to fix."
                )
        return 0 if (target is None or matches) else 1

    if sub == "ensure":
        account, repo = _resolve_target(rest)
        if not account:
            account = copilot_identity.intended_account(repo)
        dry_run = "--dry-run" in rest
        force = "--force" in rest
        if not copilot_identity.switch_enabled():
            result = copilot_identity.IdentityResult(
                "disabled",
                copilot_identity.current_login(),
                account,
                "Copilot identity switching is disabled (set "
                "'copilot_identity_switch_enabled: true' in config.yaml to "
                "enable it).",
            )
        else:
            result = copilot_identity.ensure_login(account, dry_run=dry_run, force=force)
        if json_out:
            _core()._json_output(
                {
                    "status": result.status,
                    "previous": result.previous,
                    "target": result.target,
                    "detail": result.detail,
                }
            )
            return 0 if result.ok else 1
        if result.status == "already-correct":
            output.ok(f"Copilot CLI already logged in as '{result.target}'.")
        elif result.status == "switched":
            output.ok(
                f"Copilot CLI switched from '{result.previous}' to "
                f"'{result.target}'."
            )
        elif result.status == "no-target":
            output.info("No intended Copilot account resolved; nothing to do.")
        elif result.status == "disabled":
            output.info(f"{result.detail}")
        elif result.status == "other-sessions-active":
            output.warn(f"{result.detail}")
        else:
            output.err(f"{result.status}: {result.detail}")
        return 0 if result.ok else 1

    output.err(f"Unknown 'copilot-identity' subcommand: {sub}")
    _usage()
    return 1
