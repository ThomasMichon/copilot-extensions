"""``forks`` CLI dispatch: the durable confirmed fork-publish registry.

See :mod:`.fork_pr` for the catalog itself and the ``pr.fork`` confirmation
gate that consults it. Kept as its own module rather than folded into
``repos_cli.py`` to stay under this repo's per-module line-count cap,
mirroring how ``copilot_identity_cli.py`` and ``related_cli.py`` are split
out.
"""

from __future__ import annotations

from . import config as cfg
from . import output


def _core():
    from . import __main__ as core

    return core


def _forks_usage() -> None:
    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"
    print(f"Usage: {project} forks <command>")
    print()
    print("Durable catalog of confirmed fork-based PR publish targets")
    print("(forks.yaml, machine-local). Once a repo is listed here, create-pr's")
    print("pr.fork confirmation gate ('needs_confirmation: fork_setup') is")
    print("skipped for every future call against that repo on this machine --")
    print("a GitHub fork is durable and account-scoped, not per-worktree.")
    print()
    print("Commands:")
    print("  list                                List confirmed forks")
    print("  show <repo> [--account A]           Show a repo's confirmed fork(s) --")
    print("                                      all accounts, or just one with --account")
    print("  set <repo> --owner <login> [--remote R] [--account A | --token-stdin] [--notes T]")
    print("                                      Pre-approve a repo's fork (e.g. during")
    print("                                      setup) without waiting for create-pr to ask.")
    print("                                      --account defaults to the resolved account/")
    print("                                      ambient gh login. For a repo using")
    print("                                      pr.token_command/token_env, pipe that SAME")
    print("                                      token's value via --token-stdin (scope is")
    print("                                      derived from it, matching what create-pr will")
    print("                                      compute) -- --account alone cannot reproduce")
    print("                                      that scope, and the token is read from stdin")
    print("                                      rather than argv so it never lands in shell")
    print("                                      history or a process listing.")
    print("  remove <repo> [--account A]         Forget a repo's confirmation(s) -- every")
    print("                                      account for this repo, or just one with")
    print("                                      --account (create-pr will ask again)")
    print()
    print("Examples:")
    print(f"  {project} forks set octo-org/widgets --owner octocat")
    print(f"  {project} forks list")


class _ForksArgError(ValueError):
    """Raised by ``_opt``/``_reject_unknown_options`` on malformed input:
    a flag present with a missing/invalid value (e.g. ``--account`` with
    nothing after it, or immediately followed by another flag), an
    unrecognized flag, or more positionals than the subcommand accepts.
    Distinguishes 'flag not given' (``None``, a legitimate default) from
    'flag given but malformed', so a malformed or unsupported option always
    fails loudly instead of silently defaulting to a destructive scope (e.g.
    'remove every account') or an unintended ambient-auth identity.
    """


# Per-subcommand known flags -- True if the flag takes a value, False if
# it's a bare boolean switch. Anything else present in argv (an unknown
# flag, or more positionals than the subcommand accepts) is a usage error,
# never silently ignored -- see _ForksArgError's docstring for why this
# matters (a dropped unsupported flag can silently change what gets
# recorded, e.g. an intended --token-stdin typo'd and ignored).
_KNOWN_OPTIONS: dict[str, dict[str, bool]] = {
    "list": {"--json": False},
    "show": {"--account": True, "--json": False},
    "set": {
        "--owner": True, "--remote": True, "--account": True,
        "--token-stdin": False, "--notes": True,
    },
    "remove": {"--account": True},
    "rm": {"--account": True},
}
_MAX_POSITIONALS: dict[str, int] = {
    "list": 0, "show": 1, "set": 1, "remove": 1, "rm": 1,
}


def _reject_unknown_options(sub: str, rest: list[str]) -> None:
    """Walk ``rest`` and raise on anything outside ``sub``'s known contract:
    an unrecognized flag, a value-taking flag missing its value (mirrors
    ``_opt``'s own check, so this catches it even for a flag ``_opt`` is
    never called for), or more positionals than the subcommand accepts.
    """
    allowed = _KNOWN_OPTIONS.get(sub, {})
    max_positionals = _MAX_POSITIONALS.get(sub, 0)
    positionals = 0
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("--"):
            if tok not in allowed:
                raise _ForksArgError(f"unknown option {tok!r}")
            if allowed[tok]:
                if i + 1 >= len(rest) or rest[i + 1].startswith("--"):
                    raise _ForksArgError(f"{tok} requires a value")
                i += 2
            else:
                i += 1
        else:
            positionals += 1
            if positionals > max_positionals:
                raise _ForksArgError(f"unexpected extra argument {tok!r}")
            i += 1


def cmd_forks_dispatch(argv: list[str]) -> int:
    """Route the top-level ``forks`` registry subcommands."""
    from . import fork_pr

    if argv and argv[0] in ("--help", "-h"):
        _forks_usage()
        return 0
    sub = argv[0] if argv else "list"
    rest = argv[1:] if argv else []
    if "--help" in rest or "-h" in rest:
        _forks_usage()
        return 0

    def _opt(flag: str) -> str | None:
        if flag not in rest:
            return None
        idx = rest.index(flag)
        if idx + 1 >= len(rest) or rest[idx + 1].startswith("--"):
            raise _ForksArgError(f"{flag} requires a value")
        return rest[idx + 1]

    try:
        if sub in _KNOWN_OPTIONS:
            _reject_unknown_options(sub, rest)
        return _dispatch_sub(sub, rest, fork_pr, _opt)
    except _ForksArgError as exc:
        output.err(f"forks {sub}: {exc}")
        return 1


def _dispatch_sub(sub: str, rest: list[str], fork_pr, _opt) -> int:
    """The actual per-subcommand body, split out so ``cmd_forks_dispatch``
    can wrap it in one ``_ForksArgError`` handler rather than repeating
    try/except per subcommand."""
    if sub == "list":
        entries = fork_pr.list_forks()
        if "--json" in rest:
            _core()._json_output(
                {
                    "forks": [
                        {
                            "repo": e.repo,
                            "owner": e.owner,
                            "remote": e.remote,
                            "account": e.account,
                            "confirmed_at": e.confirmed_at,
                            "notes": e.notes,
                        }
                        for e in entries
                    ]
                }
            )
            return 0
        if not entries:
            print("No forks confirmed yet.")
            print("Pre-approve one with: forks set <repo> --owner <login>")
            return 0
        output.header("Confirmed forks")
        for e in entries:
            print(f"  {e.repo:<40} owner={e.owner}  remote={e.remote}  account={e.account or '(none)'}")
            if e.confirmed_at:
                print(f"  {'':40} confirmed: {e.confirmed_at}")
        return 0

    if sub == "show":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: forks show <repo> [--account A]")
            return 1
        repo = rest[0]
        account_filter = _opt("--account")
        entries = (
            [fork_pr.find_fork(repo, account_filter)]
            if account_filter is not None
            else fork_pr.find_forks_for_repo(repo)
        )
        entries = [e for e in entries if e is not None]
        if not entries:
            output.err(f"No confirmed fork for '{repo}' in forks.yaml")
            return 1
        if "--json" in rest:
            _core()._json_output(
                {
                    "forks": [
                        {
                            "repo": e.repo,
                            "owner": e.owner,
                            "remote": e.remote,
                            "account": e.account,
                            "confirmed_at": e.confirmed_at,
                            "notes": e.notes,
                        }
                        for e in entries
                    ]
                }
            )
            return 0
        for e in entries:
            output.header(f"Fork: {e.repo} (account={e.account or '(none)'})")
            print(f"  owner:        {e.owner}")
            print(f"  remote:       {e.remote}")
            print(f"  confirmed_at: {e.confirmed_at or '(unknown)'}")
            if e.notes:
                print(f"  notes:        {e.notes}")
        return 0

    if sub == "set":
        if not rest or rest[0].startswith("-"):
            output.err(
                "Usage: forks set <repo> --owner <login> [--remote R] "
                "[--account A | --token-stdin] [--notes T]"
            )
            return 1
        repo = rest[0]
        owner = _opt("--owner")
        if not owner:
            output.err("forks set requires --owner <login>")
            return 1
        account = _opt("--account")
        if account is not None and "--token-stdin" in rest:
            output.err("forks set: --account and --token-stdin are mutually exclusive")
            return 1
        if "--token-stdin" in rest:
            import sys as _sys

            # Read the secret from stdin rather than argv (--token <value>
            # would land it in shell history and any process listing).
            token = _sys.stdin.readline().rstrip("\r\n")
            if not token:
                output.err("forks set --token-stdin: no token read from stdin")
                return 1
            # The SAME scope create_pr derives for a token_command/token_env
            # -bound repo (see fork_pr._resolve_fork_credential) -- pass the
            # repo's real token here so the pre-seeded entry actually
            # matches what create-pr will look up; --account alone cannot
            # reproduce this, since create-pr never guesses a login for an
            # opaque token.
            account = fork_pr._token_scope(token)
        elif account is None:
            # Same resolver create_pr's gate checks against (not the bare
            # account mapping) -- see fork_pr._resolve_fork_credential.
            # Built with a BARE PRConfig (no token_command/token_env): this
            # default covers the common account-mapping/ambient-auth case
            # only. Use --token-stdin instead for a repo using
            # pr.token_command/token_env.
            _token, account = fork_pr._resolve_fork_credential(
                repo, cfg.PRConfig(provider="github"),
            )
        fork_pr.record_confirmation(
            repo,
            owner,
            remote=_opt("--remote") or "fork",
            account=account,
            notes=_opt("--notes"),
        )
        output.ok(
            f"Fork for '{repo}' confirmed (owner={owner}, account={account or '(none)'}) "
            f"-- future create-pr calls for this repo under the same resolved "
            f"account will skip the fork confirmation gate."
        )
        return 0

    if sub in ("remove", "rm"):
        if not rest or rest[0].startswith("-"):
            output.err("Usage: forks remove <repo> [--account A]")
            return 1
        if fork_pr.remove_fork(rest[0], _opt("--account")):
            return 0
        output.err(f"No confirmed fork for '{rest[0]}' in forks.yaml")
        return 1

    output.err(f"Unknown forks subcommand: {sub}")
    _forks_usage()
    return 1
