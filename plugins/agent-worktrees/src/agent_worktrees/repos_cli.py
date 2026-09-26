"""Repository/account registry CLI dispatch extracted from ``__main__``."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime

from . import config as cfg
from . import output


def _core():
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    sub.add_parser("repos", help="Repos registry and source roots (run 'repos' for usage)")
    sub.add_parser("accounts", help="gh account identity catalog (run 'accounts' for usage)")


def _repos_usage() -> None:
    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"
    print(f"Usage: {project} repos <command>")
    print()
    print("Commands:")
    print("  list [--class reference|singleton|worktree|knowledge]   List known repositories")
    print("  find <name>                         Resolve a repo to its local path")
    print("  add <name> <path>                   Register a repo at a known path")
    print("     [--class C] [--remote URL] [--default-branch B]")
    print("     [--account LOGIN] [--tags a,b] [--contributing PATH]")
    print("     [--agent|--no-agent]")
    print("  remove <name>                       Remove a repo from the registry")
    print("  clone <remote> [--name N]           Clone a repo to srcroot and register")
    print("     [--target PATH]")
    print("  srcroot [--set PATH]                Show or set the source root")
    print("     [--platform windows|wsl|linux]")
    print("  migrate [--default-class C]         Import legacy ~/.git-repos")
    print("  status [--tag T] [--class C]        Show branch/dirty/ahead-behind")
    print("  sync [<repo> ...] [--tag T] [--class C]  Fetch + fast-forward (skips dirty);")
    print("                                      named repos only, else every registered one")
    print("  doctor [--fix] [--json]             Reconcile projects.yaml <-> repos.yaml")
    print("  account [list|set <owner> <login>|unset <owner>]")
    print("                                      Decoupled owner->gh-login map (account_map)")
    print("  account-for [owner|owner/name|name] Print the resolved gh login (exit 1 if none)")
    print("  copilot-account-for <name>          Print the intended Copilot CLI login for a")
    print("     [--json]                         registered repo (exit 1 if none resolved)")
    print("  copilot-account set <name> <login>  Set/unset a repo's explicit Copilot CLI")
    print("                unset <name>          identity override (see 'copilot-identity')")
    print("  gh [owner|owner/name|name] [--] <args>")
    print("                                      Run gh under that repo's account (token-inject)")
    print(
        "  pin-credentials [name] [--all]      Backfill each repo's local git credential"
    )
    print(
        "     [--json]                         pin (see 'account-for'); new registrations"
    )
    print("                                      pin automatically")
    print("  allow-edits <repo> --reason <why>   Break-glass: temporarily allow direct edits")
    print(
        "     [--minutes N] | --list | <repo> --revoke   to a guarded repo (default 10m, max 60m)"
    )
    print()
    print("Repo classes:")
    print("  reference   read-only; resolve/clone/index only; never edited")
    print("  singleton   single anchor checkout; no worktree isolation")
    print("  worktree    full agent-worktrees lifecycle; concurrent-flow safe")
    print("  knowledge   worktree-capable, but only as another project's paired")
    print("              '-k' companion -- never driven directly (see")
    print("              RepoConfig.knowledge_only)")
    print()
    print("Examples:")
    print(f"  {project} repos list")
    print(f"  {project} repos migrate")
    print(f"  {project} repos find dotfiles")
    print(f"  {project} repos add my-lib D:\\Src\\my-lib --class reference")
    print(f"  {project} repos sync --tag multi-machine system")
    print(f"  {project} repos sync copilot-extensions   # fast-forward just its anchor")


def _clarify_registration_account(
    remote: str,
    name: str,
    explicit_account: str = "",
    path: str | None = None,
) -> None:
    from . import git_ops, repos

    try:
        res = repos.resolve_registration_account(remote, explicit_account)
    except Exception:
        return

    if not res.needs_clarify:
        if res.source in ("account_map", "sibling") and res.login:
            output.info(f"  account:  {res.login} (via {res.source})")
        return

    owner = res.owner or ""
    accounts = git_ops.list_gh_accounts()
    interactive = sys.stdin is not None and sys.stdin.isatty()

    if not interactive:
        remedy = f"repos account set {owner} <login>"
        extra = f"  (authenticated: {', '.join(accounts)})" if accounts else ""
        output.warn(
            f"'{name}': owner '{owner}' is not an authenticated gh account "
            f"(likely an org); its gh/CodeSpace ops would use an unusable "
            f"derived login. Pin one: {remedy}{extra}"
        )
        return

    output.warn(
        f"Repo owner '{owner}' is not an authenticated gh account (likely an "
        f"org). Which gh account should repos under '{owner}' use?"
    )
    for i, a in enumerate(accounts, 1):
        print(f"    {i}) {a}")
    if accounts:
        print("    (enter a number or a login; blank to skip)")
    try:
        choice = input(f"  Account for '{owner}': ").strip()
    except EOFError:
        choice = ""
    if not choice:
        output.warn(f"Skipped -- set later with: repos account set {owner} <login>")
        return
    if choice.isdigit() and accounts:
        idx = int(choice) - 1
        if 0 <= idx < len(accounts):
            choice = accounts[idx]
    repos.set_account_map(owner, choice)
    if path and repos.is_https_remote(remote):
        try:
            host = repos.derive_https_host(remote) or "github.com"
            git_ops.pin_git_credential(os.path.expanduser(path), choice, host=host)
        except Exception:
            pass


def cmd_repos_dispatch(argv: list[str]) -> int:
    """Route repos subcommands."""
    from . import repos

    if not argv or argv[0] in ("--help", "-h"):
        _repos_usage()
        return 0 if argv else 1

    sub = argv[0]
    rest = argv[1:]

    if "--help" in rest or "-h" in rest:
        _repos_usage()
        return 0

    if sub == "list":
        class_filter = None
        for flag in ("--class", "--type"):
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    class_filter = rest[idx + 1]
        json_out = "--json" in rest
        entries = repos.list_repos(class_filter=class_filter)
        if json_out:
            _core()._json_output(
                {
                    "repos": [
                        {
                            "name": e.name,
                            "class": e.repo_class,
                            "remote": e.remote,
                            "default_branch": e.default_branch,
                            "tags": e.tags,
                            "contributing": e.contributing,
                            "account": e.account,
                            "resolved_account": repos.resolve_account(e),
                            "agent": e.agent,
                            "paths": e.paths,
                        }
                        for e in entries
                    ],
                }
            )
        elif not entries:
            print("No repos registered.")
            print("Add one with: repos add <name> <path> --class <class>")
            print("Or import the legacy registry with: repos migrate")
        else:
            plat = repos._current_platform()
            output.header("Repos Registry")
            for e in entries:
                tag = f"[{e.repo_class}]" if e.agent else f"[{e.repo_class} no-agent]"
                local = e.local_path(plat) or "(no local path)"
                print(f"  {e.name:<25} {tag:<20} {local}")
                if e.remote:
                    print(f"  {'':25} {'':20} {e.remote}")
                acct = repos.resolve_account(e)
                if acct:
                    src = "explicit" if e.account else "derived"
                    print(f"  {'':25} {'':20} account: {acct} ({src})")
        return 0

    if sub == "find":
        if not rest:
            output.err("Usage: repos find <name>")
            return 1
        name = rest[0]
        json_out = "--json" in rest
        path = repos.resolve_path(name)
        if path:
            if json_out:
                _core()._json_output({"name": name, "path": path})
            else:
                print(path)
            return 0
        entry = repos.find_repo(name)
        if entry and entry.remote:
            msg = f"Repo '{name}' has no local path. Clone with: repos clone {entry.remote}"
        else:
            msg = f"Repo '{name}' not found in registry"
        if json_out:
            return _core()._json_error(msg)
        output.err(msg)
        return 1

    if sub == "add":
        if len(rest) < 2:
            output.err(
                "Usage: repos add <name> <path> "
                "[--class reference|singleton|worktree|knowledge] [--remote URL] "
                "[--default-branch B] [--account LOGIN] [--tags a,b] "
                "[--contributing PATH] [--agent|--no-agent]"
            )
            return 1
        name, path = rest[0], rest[1]
        rclass = "reference"
        remote = ""
        default_branch = ""
        tags: list[str] = []
        contributing = ""

        def _opt(flag: str) -> str | None:
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    return rest[idx + 1]
            return None

        rclass = _opt("--class") or _opt("--type") or rclass
        remote = _opt("--remote") or remote
        default_branch = _opt("--default-branch") or default_branch
        if not default_branch:
            # The repo may already declare its own default_branch via its
            # in-repo .agent-worktrees/config.yaml (e.g. a contribution
            # branch distinct from GitHub's advertised HEAD) -- prefer that
            # over requiring the operator to know and pass it by hand.
            default_branch = repos.inrepo_declared_default_branch(path)
        contributing = _opt("--contributing") or contributing
        account = _opt("--account") or ""
        raw_tags = _opt("--tags")
        if raw_tags:
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

        agent_flag: bool | None = None
        if "--no-agent" in rest:
            agent_flag = False
        elif "--agent" in rest:
            agent_flag = True

        repos.add_repo(
            name,
            path,
            repo_class=rclass,
            remote=remote,
            default_branch=default_branch,
            tags=tags,
            contributing=contributing,
            account=account,
            agent=agent_flag,
        )
        _core()._clarify_registration_account(remote, name, account, path)
        return 0

    if sub == "remove":
        if not rest:
            output.err("Usage: repos remove <name>")
            return 1
        if repos.remove_repo(rest[0]):
            return 0
        output.err(f"Repo '{rest[0]}' not found in registry")
        return 1

    if sub == "clone":
        if not rest:
            output.err("Usage: repos clone <remote> [--name N] [--target PATH]")
            return 1
        remote = rest[0]
        name = None
        target = None
        if "--name" in rest:
            idx = rest.index("--name")
            if idx + 1 < len(rest):
                name = rest[idx + 1]
        if "--target" in rest:
            idx = rest.index("--target")
            if idx + 1 < len(rest):
                target = rest[idx + 1]
        entry = repos.clone_repo(remote, name=name, target=target)
        if entry:
            _entry_path = repos.resolve_path(entry.name)
            _core()._clarify_registration_account(
                entry.remote, entry.name, entry.account, _entry_path
            )
        return 0 if entry else 1

    if sub == "srcroot":
        plat_arg = None
        if "--platform" in rest:
            idx = rest.index("--platform")
            if idx + 1 < len(rest):
                plat_arg = rest[idx + 1]
        if "--set" in rest:
            idx = rest.index("--set")
            if idx + 1 < len(rest):
                repos.set_srcroot(rest[idx + 1], plat=plat_arg)
                return 0
            output.err("--set requires a path")
            return 1
        registry = repos.read_registry()
        if registry.srcroot:
            for p, v in sorted(registry.srcroot.items()):
                marker = " ←" if p == (plat_arg or repos._current_platform()) else ""
                print(f"  {p}: {v}{marker}")
        else:
            print("No source roots configured.")
            print("Set one with: repos srcroot --set <path>")
        return 0

    if sub == "migrate":
        default_class = "singleton"
        if "--default-class" in rest:
            idx = rest.index("--default-class")
            if idx + 1 < len(rest):
                default_class = rest[idx + 1]
        migrated, skipped = repos.migrate_git_repos(default_class=default_class)
        if migrated == 0 and skipped == 0:
            return 1
        output.ok(
            f"Migrated {migrated} repo(s) from ~/.git-repos ({skipped} skipped) into repos.yaml"
        )
        output.info(
            "~/.git-repos was left in place; remove it once you have verified the migration."
        )
        return 0

    if sub == "status":
        tag = None
        class_filter = None
        if "--tag" in rest:
            idx = rest.index("--tag")
            if idx + 1 < len(rest):
                tag = rest[idx + 1]
        for flag in ("--class", "--type"):
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    class_filter = rest[idx + 1]
        json_out = "--json" in rest
        statuses = repos.status_all(tag=tag, class_filter=class_filter)
        if json_out:
            _core()._json_output(
                {
                    "repos": [
                        {
                            "name": s.name,
                            "class": s.repo_class,
                            "present": s.present,
                            "branch": s.branch,
                            "dirty": s.dirty,
                            "ahead": s.ahead,
                            "behind": s.behind,
                            "path": s.path,
                            "error": s.error,
                        }
                        for s in statuses
                    ],
                }
            )
            return 0
        if not statuses:
            print("No repos registered.")
            return 0
        output.header("Repos Status")
        for s in statuses:
            if not s.present:
                print(f"  {s.name:<25} [{s.repo_class:<9}] MISSING")
                continue
            flags = []
            if s.dirty:
                flags.append("dirty")
            if s.ahead:
                flags.append(f"+{s.ahead}")
            if s.behind:
                flags.append(f"-{s.behind}")
            state = ", ".join(flags) if flags else "clean"
            print(f"  {s.name:<25} [{s.repo_class:<9}] {s.branch:<18} {state}")
        return 0

    if sub == "sync":
        tag = None
        class_filter = None
        consumed_idxs: set[int] = set()
        if "--tag" in rest:
            idx = rest.index("--tag")
            consumed_idxs.add(idx)
            if idx + 1 < len(rest):
                tag = rest[idx + 1]
                consumed_idxs.add(idx + 1)
        for flag in ("--class", "--type"):
            if flag in rest:
                idx = rest.index(flag)
                consumed_idxs.add(idx)
                if idx + 1 < len(rest):
                    class_filter = rest[idx + 1]
                    consumed_idxs.add(idx + 1)
        # Any remaining bare tokens (not consumed as a flag or its value) are
        # explicit repo names -- lets a caller fast-forward exactly one
        # anchor (`repos sync <name>`) instead of every registered repo, the
        # same safe fetch + `merge --ff-only` (skips dirty/diverged/detached)
        # `sync_repo` always used, just scoped down.
        names = tuple(
            tok for i, tok in enumerate(rest)
            if i not in consumed_idxs and not tok.startswith("--")
        )
        results = repos.sync_all(
            tag=tag, class_filter=class_filter, names=names or None,
        )
        if not results:
            print("No repos registered.")
            return 0
        output.header("Repos Sync")
        had_error = False
        for name, state, detail in results:
            if state == "synced":
                output.ok(f"{name}: {detail}")
            elif state in ("skipped", "missing"):
                output.info(f"{name}: {state} ({detail})")
            else:
                had_error = True
                output.err(f"{name}: {detail}")
        return 1 if had_error else 0

    if sub == "doctor":
        from . import doctor

        do_fix = "--fix" in rest
        json_out = "--json" in rest
        findings = doctor.reconcile(fix=do_fix)
        if json_out:
            _core()._json_output(
                {
                    "fixed": do_fix,
                    "findings": [
                        {
                            "repo": f.repo,
                            "kind": f.kind,
                            "severity": f.severity,
                            "detail": f.detail,
                            "fixable": f.fixable,
                            "fix_detail": f.fix_detail,
                            "fixed": f.fixed,
                        }
                        for f in findings
                    ],
                }
            )
        else:
            doctor.render(findings, fixed_mode=do_fix)
        unresolved = [f for f in findings if f.severity == doctor.SEV_ERROR and not f.fixed]
        return 1 if unresolved else 0

    if sub == "account-for":
        target = rest[0] if rest and not rest[0].startswith("-") else None
        json_out = "--json" in rest
        if not target:
            target = _core()._infer_active_github_slug(cfg.load_config())
        if not target:
            output.err(
                "Usage: repos account-for [owner|owner/name|registered-repo-name]  "
                "(inferred from the active project when omitted)"
            )
            return 1
        login = repos.account_for_github_slug(target)
        if json_out:
            _core()._json_output({"target": target, "account": login})
            return 0 if login else 1
        if login:
            print(login)
            return 0
        return 1

    if sub == "copilot-account-for":
        target = rest[0] if rest and not rest[0].startswith("-") else None
        json_out = "--json" in rest
        if not target:
            output.err("Usage: repos copilot-account-for <registered-repo-name> [--json]")
            return 1
        login = repos.copilot_account_for(target)
        if json_out:
            _core()._json_output({"target": target, "copilot_account": login})
            return 0 if login else 1
        if login:
            print(login)
            return 0
        return 1

    if sub == "copilot-account":
        casub = rest[0] if rest else None
        carest = rest[1:] if rest else []
        if casub == "set":
            if len(carest) < 2:
                output.err("Usage: repos copilot-account set <name> <login>")
                return 1
            if repos.set_copilot_account(carest[0], carest[1]):
                return 0
            output.err(f"'{carest[0]}' is not a registered repo")
            return 1
        if casub in ("unset", "remove", "rm"):
            if not carest:
                output.err("Usage: repos copilot-account unset <name>")
                return 1
            if repos.unset_copilot_account(carest[0]):
                return 0
            output.err(f"No explicit copilot_account override for '{carest[0]}'")
            return 1
        output.err(f"Unknown 'repos copilot-account' subcommand: {casub}")
        output.info("Usage: repos copilot-account [set <name> <login>|unset <name>]")
        return 1

    if sub == "pin-credentials":
        json_out = "--json" in rest
        target = None
        for tok in rest:
            if tok not in ("--all", "--json"):
                target = tok
                break
        results = repos.backfill_credential_pins(target)
        if json_out:
            _core()._json_output(
                {
                    "results": [
                        {
                            "name": r.name,
                            "status": r.status,
                            "login": r.login,
                            "detail": r.detail,
                        }
                        for r in results
                    ]
                }
            )
        else:
            if not results:
                print("No repos registered.")
            for r in results:
                if r.status == "pinned":
                    output.ok(f"{r.name}: {r.detail}")
                elif r.status in ("no_path", "not_github", "ssh_remote", "not_https"):
                    output.info(f"{r.name}: {r.status} ({r.detail})")
                else:
                    output.warn(f"{r.name}: {r.detail}")
        had_error = any(
            r.status not in ("pinned", "no_path", "not_github", "ssh_remote", "not_https")
            for r in results
        )
        return 1 if had_error else 0

    if sub == "gh":
        args = list(rest)
        target = None
        if args and args[0] == "--":
            gh_args = args[1:]
        elif args and not args[0].startswith("-"):
            target = args[0]
            gh_args = args[1:]
            if gh_args and gh_args[0] == "--":
                gh_args = gh_args[1:]
        else:
            gh_args = args
        if target is None:
            target = _core()._infer_active_github_slug(cfg.load_config())
        if not target or not gh_args:
            output.err(
                "Usage: repos gh [owner|owner/name|registered-repo-name] "
                "[--] <gh args...>  "
                "(repo inferred from the active project when omitted)"
            )
            return 1
        if shutil.which("gh") is None:
            output.err("gh CLI not found on PATH")
            return 1
        if repos.is_unresolved_registered_target(target):
            output.err(
                f"'{target}' is a registered repo whose remote has no derivable "
                "GitHub owner -- refusing to run gh under ambient auth for a "
                "known-but-unresolvable identity. Pass an explicit owner/name, "
                "or set an account: override for this repo."
            )
            return 1
        env, login, injected = _core()._gh_env_for_repo(target)
        if login and not injected:
            output.warn(f"could not mint a gh token for '{login}'; using ambient auth")
        return subprocess.run(["gh", *gh_args], env=env).returncode

    if sub == "account":
        acsub = rest[0] if rest else "list"
        acrest = rest[1:] if rest else []
        if acsub == "list":
            registry = repos.read_registry()
            json_out = "--json" in acrest
            if json_out:
                _core()._json_output({"account_map": dict(registry.account_map)})
                return 0
            if not registry.account_map:
                print("No account_map entries.")
                print("Add one with: repos account set <owner> <login>")
                return 0
            output.header("Account map (owner -> gh login)")
            for owner in sorted(registry.account_map.keys()):
                print(f"  {owner:<24} -> {registry.account_map[owner]}")
            return 0
        if acsub == "set":
            if len(acrest) < 2:
                output.err("Usage: repos account set <owner> <login>")
                return 1
            repos.set_account_map(acrest[0], acrest[1])
            return 0
        if acsub in ("unset", "remove", "rm"):
            if not acrest:
                output.err("Usage: repos account unset <owner>")
                return 1
            if repos.unset_account_map(acrest[0]):
                return 0
            output.err(f"No account_map entry for '{acrest[0]}'")
            return 1
        output.err(f"Unknown 'repos account' subcommand: {acsub}")
        output.info("Usage: repos account [list|set <owner> <login>|unset <owner>]")
        return 1

    if sub == "allow-edits":
        from . import allow_edits

        json_out = "--json" in rest
        do_list = "--list" in rest
        do_revoke = "--revoke" in rest
        reason = None
        minutes = None
        positional: list[str] = []
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok == "--reason" and i + 1 < len(rest):
                reason = rest[i + 1]
                i += 2
                continue
            if tok == "--minutes" and i + 1 < len(rest):
                minutes = rest[i + 1]
                i += 2
                continue
            if tok in ("--json", "--list", "--revoke"):
                i += 1
                continue
            positional.append(tok)
            i += 1

        if do_list:
            grants = allow_edits.list_active()
            if json_out:
                _core()._json_output(
                    {
                        "grants": [
                            {
                                "repo": g.repo,
                                "expires_at_ms": g.expires_at_ms,
                                "remaining_seconds": g.remaining_seconds,
                                "minutes": g.minutes,
                                "reason": g.reason,
                                "session": g.session,
                            }
                            for g in grants
                        ]
                    }
                )
            elif not grants:
                print("No active edit grants.")
            else:
                output.header("Active edit grants (break-glass)")
                for g in grants:
                    mins = max(0, g.remaining_seconds // 60)
                    print(f"  {g.repo:<25} {mins}m left   {g.reason}")
            return 0

        repo = positional[0] if positional else None
        if not repo:
            output.err(
                "Usage: repos allow-edits <repo> --reason <why> [--minutes N] "
                "| --list | <repo> --revoke"
            )
            return 1

        if do_revoke:
            removed = allow_edits.revoke(repo)
            if json_out:
                _core()._json_output({"repo": repo, "revoked": removed})
            elif removed:
                output.ok(f"Revoked edit grant for '{repo}'.")
            else:
                output.info(f"No active edit grant for '{repo}'.")
            return 0

        if not reason or len(reason.strip()) < allow_edits.MIN_REASON_LEN:
            msg = (
                f"repos allow-edits requires --reason (>= {allow_edits.MIN_REASON_LEN} chars) "
                "explaining why delegation cannot be used."
            )
            return _core()._json_error(msg) if json_out else (output.err(msg) or 1)

        entry = repos.find_repo(repo)
        g = allow_edits.grant(repo, reason.strip(), minutes)
        note = (
            ""
            if entry
            else (f" (note: '{repo}' is not in the repos registry — nothing may be guarding it)")
        )
        if json_out:
            _core()._json_output(
                {
                    "repo": repo,
                    "expires_at_ms": g.expires_at_ms,
                    "minutes": g.minutes,
                    "reason": g.reason,
                    "known": entry is not None,
                }
            )
        else:
            output.warn(
                f"BREAK-GLASS: direct edits to '{repo}' allowed for "
                f"{g.minutes}m — reason: {g.reason}"
            )
            expires = datetime.fromtimestamp(g.expires_at_ms / 1000).strftime("%H:%M:%S")
            output.info(
                f"Grant expires at {expires}. Prefer delegation for anything "
                f"the repo's own agent could do.{note}"
            )
        return 0

    output.err(f"Unknown repos subcommand: {sub}")
    _repos_usage()
    return 1


def _accounts_usage() -> None:
    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"
    print(f"Usage: {project} accounts <command>")
    print()
    print("Catalog of gh account identities and their (re)login flows")
    print("(~/.agent-worktrees/accounts.yaml). The owner->account MAP lives in")
    print("repos.yaml (see 'repos account'); this catalog describes the logins")
    print("that map points at -- host, expected scopes, and how to (re)login.")
    print()
    print("Commands:")
    print("  list                                List catalogued accounts")
    print("  show <login>                        Show one account's details")
    print("  set <login> [--host H] [--scopes a,b] [--login-flow CMD] [--notes T]")
    print("                                      Add or update an account entry")
    print("  remove <login>                      Remove an account entry")
    print()
    print("Examples:")
    print(f"  {project} accounts set ThomasMichon --scopes codespace,repo,workflow \\")
    print("      --login-flow 'gh auth login -h github.com'")
    print(f"  {project} accounts list")


def cmd_accounts_dispatch(argv: list[str]) -> int:
    """Route the top-level ``accounts`` catalog subcommands."""
    from . import accounts

    if argv and argv[0] in ("--help", "-h"):
        _accounts_usage()
        return 0
    sub = argv[0] if argv else "list"
    rest = argv[1:] if argv else []
    if "--help" in rest or "-h" in rest:
        _accounts_usage()
        return 0

    def _opt(flag: str) -> str | None:
        if flag in rest:
            idx = rest.index(flag)
            if idx + 1 < len(rest):
                return rest[idx + 1]
        return None

    if sub == "list":
        entries = accounts.list_accounts()
        if "--json" in rest:
            _core()._json_output(
                {
                    "accounts": [
                        {
                            "login": e.login,
                            "host": e.host,
                            "scopes": e.scopes,
                            "login_flow": e.login_flow,
                            "notes": e.notes,
                        }
                        for e in entries
                    ]
                }
            )
            return 0
        if not entries:
            print("No accounts catalogued.")
            print("Add one with: accounts set <login> [--scopes ...] [--login-flow ...]")
            return 0
        output.header("Accounts catalog")
        for e in entries:
            scopes = ",".join(e.scopes) if e.scopes else "(none)"
            print(f"  {e.login:<20} host={e.host}  scopes={scopes}")
            if e.login_flow:
                print(f"  {'':20} login: {e.login_flow}")
        return 0

    if sub == "show":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: accounts show <login>")
            return 1
        e = accounts.find_account(rest[0])
        if not e:
            output.err(f"No account '{rest[0]}' in accounts.yaml")
            return 1
        if "--json" in rest:
            _core()._json_output(
                {
                    "login": e.login,
                    "host": e.host,
                    "scopes": e.scopes,
                    "login_flow": e.login_flow,
                    "notes": e.notes,
                }
            )
            return 0
        output.header(f"Account: {e.login}")
        print(f"  host:       {e.host}")
        print(f"  scopes:     {','.join(e.scopes) if e.scopes else '(none)'}")
        print(f"  login_flow: {e.login_flow or '(none)'}")
        if e.notes:
            print(f"  notes:      {e.notes}")
        return 0

    if sub == "set":
        if not rest or rest[0].startswith("-"):
            output.err(
                "Usage: accounts set <login> [--host H] [--scopes a,b] "
                "[--login-flow CMD] [--notes T]"
            )
            return 1
        login = rest[0]
        raw_scopes = _opt("--scopes")
        scopes = (
            [s.strip() for s in raw_scopes.split(",") if s.strip()]
            if raw_scopes is not None
            else None
        )
        accounts.set_account(
            login,
            host=_opt("--host"),
            scopes=scopes,
            login_flow=_opt("--login-flow"),
            notes=_opt("--notes"),
        )
        return 0

    if sub in ("remove", "rm"):
        if not rest or rest[0].startswith("-"):
            output.err("Usage: accounts remove <login>")
            return 1
        if accounts.remove_account(rest[0]):
            return 0
        output.err(f"No account '{rest[0]}' in accounts.yaml")
        return 1

    output.err(f"Unknown accounts subcommand: {sub}")
    _accounts_usage()
    return 1
