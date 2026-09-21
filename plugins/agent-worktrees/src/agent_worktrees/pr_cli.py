"""``agent-worktrees pr*`` -- manual PR-family CLI dispatch helpers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as cfg
from . import git_ops, output, providers, tracking


def _core():
    """Lazily resolve ``agent_worktrees.__main__`` -- **never** at module load.

    Cross-module CLI helpers (``_json_output``, ``_resolve_repo_remote``,
    ``_resolve_worktree_id``, ...) are defined in sibling modules but
    re-exported onto ``__main__`` so the test suite's ``monkeypatch.setattr(m,
    "_name", ...)`` convention keeps working uniformly regardless of where a
    name's real implementation lives. Routing every cross-module call through
    this accessor (instead of importing the sibling module directly) preserves
    that convention. It must stay a **deferred**, call-time import: a
    module-level ``from . import __main__ as core`` here would force a second,
    independent execution of ``__main__.py`` (under the distinct module name
    ``agent_worktrees.__main__``, since ``-m agent_worktrees`` already runs it
    once as ``__main__``) that observes this module's own not-yet-defined late
    bindings and crashes -- exactly copilot-extensions#2614's regression.
    """
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    """Register help-surface parser stubs for the manual PR command family."""
    # pr-watch / pr -- dispatched pre-argparse (see cmd_pr_watch_dispatch /
    # cmd_pr_dispatch). Registered here only so they surface in --help.
    sub.add_parser("pr-watch", help="Block until a PR moves (run 'pr-watch' for usage)")
    sub.add_parser(
        "pr-merge", help="Signal merge consent on an approved PR (run 'pr-merge' for usage)"
    )
    sub.add_parser(
        "pr-research", help="Inspect a repo's provider settings -> policy matrix (read-only)"
    )
    sub.add_parser("pr", help="Author-side PR command family (run 'pr' for usage)")


def _pr_watch_usage() -> None:
    out = sys.stderr
    print("Usage: <project> pr-watch <wait|cursor> <owner/name> <pr> [options]", file=out)
    print(file=out)
    print("Block until a pull request moves, then wake the caller. The review", file=out)
    print("backend (host, token) is the repo's PR binding (.agent-worktrees/", file=out)
    print("config.yaml: provider / api_base / token_command).", file=out)
    print(file=out)
    print("  wait <repo> <pr>    Block until a transition or timeout.", file=out)
    print("    --until LIST      Comma-list of transitions or 'any' (default:", file=out)
    print("                      role/policy-aware -- see default_until())", file=out)
    print("    --since CURSOR     Baseline cursor (race-proof); omit to auto-baseline", file=out)
    print("    --timeout SECS     Max seconds to block (0 = no limit; default 3600)", file=out)
    print("    --interval SECS    Poll interval (> 0; default 20)", file=out)
    print("    --json             Emit only the result JSON on stdout", file=out)
    print("  cursor <repo> <pr>  Print the current baseline cursor for a PR.", file=out)
    print(file=out)
    print("  Overrides: --host URL (api base), --token TOKEN.", file=out)
    print(file=out)
    print("  One-shot read: `wait ... --timeout 1` returns the current-state", file=out)
    print("  snapshot (verdict/merge/consent) even on timeout; or use the", file=out)
    print("  worktree-scoped `pr-status` for the same live state without waiting.", file=out)
    print(file=out)
    print("  The result payload carries a 'merge' block describing what stands", file=out)
    print("  between the PR and a merge -- act on it after a review lands:", file=out)
    print("    needs_consent   true => approved+unblocked but the merge-consent", file=out)
    print("                    label is not applied yet; YOU must add it (the PR", file=out)
    print("                    will NOT merge on its own).", file=out)
    print("    consent_action  apply | already | skip  (+ 'reason').", file=out)
    print("    clear_to_merge  true when only consent stands in the way.", file=out)


def _pr_parse_repo(value: str) -> str:
    if value.count("/") != 1 or not all(value.split("/")):
        raise ValueError("repo must be a 'owner/name' (or ADO 'project/repo') slug")
    return value


def _infer_active_repo_slug(config: cfg.Config) -> str | None:
    """Provider-correct PR repo slug for the active project, or None.

    Lets the pr-* / ``repos`` verbs omit the explicit ``owner/name`` positional:
    resolves the active project's canonical remote (``_resolve_repo_remote`` --
    the registry remote, else the anchor's git origin) and parses the hosting
    slug from the URL. Provider-correct for **both** GitHub (``owner/name``) and
    Azure DevOps (``project/repo`` -- e.g. ``ExampleProject/example-repo``).
    Returns None when the remote is unresolvable (caller then requires the
    positional).
    """
    try:
        remote = _core()._resolve_repo_remote(config, config.default_repo)
    except Exception:
        return None
    return git_ops.slug_from_url(remote)


def _infer_active_github_slug(config: cfg.Config) -> str | None:
    """Like :func:`_infer_active_repo_slug`, but ``None`` unless the active
    project's remote is actually a github.com remote.

    GitHub account resolution (``repos account-for``/``repos gh``) is
    GitHub-only: handing it a non-GitHub provider slug (e.g. an Azure DevOps
    ``Project/repo``) would treat ``Project`` as a bogus GitHub owner and can
    print/mint an identity for the wrong account (#3032 follow-up). Use this
    instead of the provider-generic inference for those two commands.
    """
    from . import repos

    try:
        remote = _core()._resolve_repo_remote(config, config.default_repo)
    except Exception:
        return None
    if not repos.github_owner(remote):
        return None
    return git_ops.slug_from_url(remote)


def _tracked_pr_head_evidence(
    config: cfg.Config,
    repo: str,
    number: int,
    provider: str,
    api_base: str,
) -> tuple[str, str]:
    """Return locally recorded publication evidence for one PR's current head."""
    try:
        authority_endpoint = providers.get_provider(provider).authority_endpoint(api_base)
    except (providers.ProviderError, ValueError, AttributeError):
        return "", ""
    worktree_id = _core()._infer_worktree_id_from_cwd(config)
    if not worktree_id:
        return "", ""
    try:
        record = tracking.load_record(
            cfg.tracking_dir() / f"{_core()._resolve_worktree_id(worktree_id)}.yaml"
        )
    except Exception:
        return "", ""
    for pr in record.prs:
        target_repo = pr.repo or record.repo
        if (
            pr.number == number
            and target_repo == repo
            and (pr.provider or provider) == provider
            and pr.head_observed_api_base == authority_endpoint
        ):
            return pr.head_sha, pr.head_observed_at
    return "", ""


def _classify_pr_operands(operands: list[str]) -> tuple[str | None, int | None]:
    """Split free-form pr-merge operands into ``(repo_slug, pr_number)``.

    A token containing ``/`` is the provider repo slug (``owner/name`` or ADO
    ``project/repo``); an all-digit token is the PR number. This is what lets
    the repo be **omitted** (inferred from the active project) while a bare PR
    number is never mistaken for a slug -- e.g. ``pr-merge 2333486`` resolves to
    ``(None, 2333486)``, not a bogus repo. Raises ``ValueError`` on a duplicate
    or unrecognized token.
    """
    repo: str | None = None
    pr: int | None = None
    for tok in operands:
        if "/" in tok:
            if repo is not None:
                raise ValueError(f"unexpected extra repo argument {tok!r}")
            repo = _pr_parse_repo(tok)
        elif tok.isdigit():
            if pr is not None:
                raise ValueError(f"unexpected extra PR number {tok!r}")
            pr = int(tok)
        else:
            raise ValueError(
                f"unrecognized argument {tok!r} (expected a repo slug "
                "-- owner/name or ADO project/repo -- or a PR number)"
            )
    return repo, pr


def _pr_watch_review_blocking(config, prcfg, args) -> bool:
    """Effective review-blocking posture for a ``pr-watch wait``.

    A ``pr-self-merge`` repo whose review is marked non-blocking (e.g. GitHub
    Copilot code review, which cannot render APPROVED/CHANGES_REQUESTED on an
    owner-authored PR) only skips waiting for a real verdict for an actor who
    actually holds merge authority -- a contributor without it still needs a
    human maintainer's APPROVED/CHANGES_REQUESTED. Mirrors
    ``_pr_merge_now``'s live-authority check; fails open on an unknown/failed
    permission read, same as that command.
    """
    review_blocking = bool(getattr(prcfg, "review_blocking", False))
    flow = _core()._pr_flow_profile(config.default_repo)
    from . import pr_contract as pc
    if review_blocking or flow.profile != pc.PROFILE_PR_SELF_MERGE:
        return review_blocking
    from .providers import account_token_for_slug, actor_viewer_permission, get_provider
    try:
        provider = get_provider(getattr(prcfg, "provider", "gitea") or "gitea")
        base = (args.host or getattr(prcfg, "api_base", "") or "").strip()
        tok = args.token if args.token is not None else account_token_for_slug(args.repo, prcfg)
        live_permission = actor_viewer_permission(provider, args.repo, api_base=base, token=tok)
    except Exception:
        return False
    return pc.actor_merge_authority(live_permission) is False


def cmd_pr_watch_dispatch(argv: list[str]) -> int:
    """Route `pr-watch` verbs (wait / cursor) -- the provider-generic watcher.

    The network+timing loop lives in :mod:`agent_worktrees.pr_watch`; the pure
    transition logic in :mod:`agent_worktrees.pr_contract`; the provider read in
    the provider plugins. This dispatcher wires the CLI onto the repo's PR
    binding (host/token/provider from config).
    """
    import json as _json

    from . import pr_contract as pc
    from . import pr_watch as prw
    from .providers import ProviderError

    if not argv or argv[0] in ("--help", "-h", "help"):
        _pr_watch_usage()
        return 0 if argv and argv[0] in ("--help", "-h", "help") else 1

    verb = argv[0]
    if verb not in ("wait", "cursor"):
        output.err(f"Unknown pr-watch subcommand: {verb}")
        _pr_watch_usage()
        return 1

    p = argparse.ArgumentParser(prog=f"pr-watch {verb}", add_help=True)
    p.add_argument(
        "repo",
        type=_pr_parse_repo,
        nargs="?",
        default=None,
        help="repo slug -- owner/name or ADO project/repo (optional; "
        "inferred from the active project)",
    )
    p.add_argument("pr", type=int, help="PR number")
    p.add_argument(
        "--host", default="", help="API base URL override (else the binding's api_base)"
    )
    p.add_argument("--token", default=None, help="Provider token override (else the binding)")
    p.add_argument("--config", default=None)
    if verb == "wait":
        p.add_argument(
            "--until",
            default=None,
            help="comma-list of transitions or 'any' (default: role/policy-aware)",
        )
        p.add_argument("--since", default=None, help="baseline cursor (omit to auto-baseline)")
        p.add_argument(
            "--timeout", type=float, default=3600.0, help="max seconds to block (0 = no limit)"
        )
        p.add_argument("--interval", type=float, default=20.0, help="poll interval seconds")
        p.add_argument("--json", action="store_true", help="emit only the result JSON")
    try:
        args = p.parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code or 0)

    if verb == "wait":
        if args.timeout < 0:
            output.err("--timeout must be >= 0 (0 = no limit)")
            return 2
        if args.interval <= 0:
            output.err("--interval must be > 0")
            return 2

    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
        prcfg = config.default_repo.pr
        if args.repo is None:
            args.repo = _infer_active_repo_slug(config)
            if not args.repo:
                output.err(
                    "pr-watch: could not infer the repo from the active "
                    "project; pass an explicit repo slug"
                )
                return 2
        review_blocking = _pr_watch_review_blocking(config, prcfg, args) if verb == "wait" else True

        # Parse/validate --until now that its role/policy-aware default is known.
        if verb == "wait":
            raw = (args.until if args.until is not None
                   else ",".join(pc.default_until(review_blocking))).strip().lower()
            if raw == "any":
                until = ["any"]
            else:
                until = [v.strip() for v in raw.split(",") if v.strip()]
                bad = [v for v in until if v not in pc.ALL_TRANSITIONS]
                if bad:
                    output.err(
                        f"unknown transition(s) {bad}; choose from "
                        f"{', '.join(pc.ALL_TRANSITIONS)} or 'any'"
                    )
                    return 2

        fetch = prw.build_fetch(prcfg, args.repo, args.pr, api_base=args.host, token=args.token)
        if verb == "cursor":
            snap = fetch()
            print(pc.Baseline.from_snapshot(snap).to_cursor())
            return 0

        baseline = pc.Baseline.from_cursor(args.since) if args.since else None
        tracked_head_sha, head_observed_at = _tracked_pr_head_evidence(
            config,
            args.repo,
            args.pr,
            prcfg.provider,
            args.host or prcfg.api_base,
        )
        if not args.json:
            mode = f"since {args.since}" if args.since else "auto-baseline"
            print(
                f"pr-watch: watching {args.repo}#{args.pr} for [{', '.join(until)}] "
                f"({mode}, every {args.interval:g}s, timeout {args.timeout:g}s)",
                file=sys.stderr,
            )
        result = prw.run_wait(
            repo=args.repo,
            pr=args.pr,
            until=until,
            baseline=baseline,
            fetch=fetch,
            timeout=args.timeout,
            interval=args.interval,
            automerge_label=getattr(prcfg, "automerge_label", "") or "",
            hold_labels=tuple(getattr(prcfg, "hold_labels", ()) or ()),
            wip_title_prefixes=tuple(getattr(prcfg, "wip_title_prefixes", ()) or ()),
            approval_required=bool(getattr(prcfg, "approval_required", True)),
            allow_stale_approval=bool(getattr(prcfg, "allow_stale_approval", False)),
            stale_approval_head_sha=tracked_head_sha,
            stale_approval_head_observed_at=head_observed_at,
            review_blocking=review_blocking,
            on_error=lambda e: print(f"pr-watch: poll error (will retry): {e}", file=sys.stderr),
        )
        if not result.matched:
            # #3486: a timeout still carries the current-state snapshot (verdict
            # / merge state / consent / labels) when a poll succeeded, so a
            # short-timeout pr-watch doubles as a one-shot read. Preserve the
            # legacy {repo, pr, timed_out} keys and enrich with the snapshot.
            payload = dict(result.payload) if result.payload else {}
            payload.setdefault("repo", args.repo)
            payload.setdefault("pr", args.pr)
            payload["timed_out"] = True
            print(_json.dumps(payload))
            if not args.json:
                print(f"pr-watch: timed out after {args.timeout:g}s", file=sys.stderr)
                merge = payload.get("merge") or {}
                if merge:
                    conflict = " (conflict)" if merge.get("conflict") else ""
                    print(
                        f"pr-watch: current state -> verdict "
                        f"{merge.get('verdict', '?')}, merge "
                        f"{merge.get('merge_state', '?')}, consent "
                        f"{merge.get('consent_action', '?')}{conflict}",
                        file=sys.stderr,
                    )
                    print(
                        "pr-watch: (this is a one-shot read; `pr-status` gives "
                        "the same live state without waiting)",
                        file=sys.stderr,
                    )
            return 124
        print(_json.dumps(result.payload))
        if not args.json:
            print(
                f"pr-watch: {args.repo}#{args.pr} -> {', '.join(result.payload['transitions'])}",
                file=sys.stderr,
            )
            # Surface the next action so a woken caller doesn't assume "approved
            # == done": an approved+unblocked PR still needs merge consent.
            merge = result.payload.get("merge") or {}
            if merge.get("needs_consent"):
                label = merge.get("consent_label") or "the merge-consent label"
                print(
                    f"pr-watch: NEXT -> grant merge consent (add label "
                    f"'{label}') -- the PR will not merge until you do "
                    f"({merge.get('reason', '')})",
                    file=sys.stderr,
                )
            elif merge.get("consent_action") == "already":
                print(
                    "pr-watch: merge consent already granted; the merge gate will proceed",
                    file=sys.stderr,
                )
        return 0
    except ProviderError as exc:
        output.err(f"pr-watch: {exc}")
        return 3
    except ValueError as exc:
        output.err(f"pr-watch: {exc}")
        return 2


from .pr_merge_cli import (  # noqa: E402 -- mechanical extraction re-import
    _pr_merge_now,  # noqa: F401 -- re-exported for __main__.py test-facing binding
    _pr_merge_print_human,  # noqa: F401 -- re-exported for __main__.py test-facing binding
    _pr_merge_usage,  # noqa: F401 -- re-exported for __main__.py test-facing binding
    cmd_pr_merge_dispatch,
)


def _pr_usage() -> None:
    out = sys.stderr
    print("Usage: <project> pr <verb> [args...]", file=out)
    print(file=out)
    print("Author-side PR command family (verbs also available flat as pr-*):", file=out)
    print("  create   Open a PR from the worktree (= create-pr)", file=out)
    print("  watch    Block until the PR moves (= pr-watch)", file=out)
    print("  merge    Signal merge consent on an approved PR (= pr-merge)", file=out)
    print("  status   Read tracked PR metadata (= pr-status)", file=out)
    print("  complete Reconcile the worktree after merge (= pr-complete)", file=out)
    print("  ready    Move a PR out of draft, ready-for-review (= pr-ready)", file=out)
    print(
        "  research Inspect the repo's provider settings -> policy matrix (= pr-research)",
        file=out,
    )


def cmd_pr_research_dispatch(argv: list[str]) -> int:
    """Route ``pr-research`` -- read the repo's live provider settings and derive
    the policy matrix to match (#225). Read-only: it PRINTS the suggested ``pr:``
    policy keys (never writes config), so an operator/agent can align the config
    with the repo's real settings instead of a guess.
    """
    import json as _json

    from . import pr_contract as pc
    from .providers import ProviderError, account_token_for_slug, get_provider

    if argv and argv[0] in ("--help", "-h", "help"):
        print(
            "Usage: <project> pr-research [repo] [--default-branch B] "
            "[--host URL] [--token T] [--json]",
            file=sys.stderr,
        )
        print(file=sys.stderr)
        print(
            "Read the repo's live provider settings (allowed merge methods, native auto-merge,",
            file=sys.stderr,
        )
        print(
            "delete-branch-on-merge, required reviews/checks) and derive the repo-overridable",
            file=sys.stderr,
        )
        print(
            "`pr:` policy matrix to match. Read-only -- prints a suggestion; "
            "writes nothing. The repo is inferred from the active project when "
            "omitted.",
            file=sys.stderr,
        )
        return 0

    p = argparse.ArgumentParser(prog="pr-research", add_help=True)
    p.add_argument(
        "repo",
        type=_pr_parse_repo,
        nargs="?",
        default=None,
        help="repo slug -- owner/name or ADO project/repo (optional; "
        "inferred from the active project)",
    )
    p.add_argument(
        "--default-branch",
        default="",
        dest="default_branch",
        help="branch to read protection from (defaults to repo config)",
    )
    p.add_argument("--host", default="", help="API base URL override")
    p.add_argument("--token", default=None, help="Provider token override")
    p.add_argument("--json", action="store_true", help="emit the result JSON")
    p.add_argument("--config", default=None)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
        if args.repo is None:
            args.repo = _infer_active_repo_slug(config)
            if not args.repo:
                raise ValueError(
                    "could not infer the repo from the active project; pass an explicit repo slug"
                )
        repo_cfg = config.default_repo
        prcfg = repo_cfg.pr
        provider = get_provider(getattr(prcfg, "provider", "gitea") or "gitea")
        base = (args.host or getattr(prcfg, "api_base", "") or "").strip()
        branch = args.default_branch or repo_cfg.default_branch or ""
        tok = args.token if args.token is not None else account_token_for_slug(args.repo, prcfg)
        policy = provider.get_repo_policy(
            args.repo, default_branch=branch, api_base=base, token=tok
        )
    except ProviderError as exc:
        output.err(f"pr-research: {exc}")
        return 1
    except Exception as exc:  # config / resolution failure
        output.err(f"pr-research: {exc}")
        return 1

    matrix = pc.derive_policy_matrix(policy)
    settings = {
        "supported": policy.supported,
        "allow_squash": policy.allow_squash,
        "allow_merge_commit": policy.allow_merge_commit,
        "allow_rebase": policy.allow_rebase,
        "allow_auto_merge": policy.allow_auto_merge,
        "delete_branch_on_merge": policy.delete_branch_on_merge,
        "required_approving_reviews": policy.required_approving_reviews,
        "has_required_status_checks": policy.has_required_status_checks,
    }
    if args.json:
        print(
            _json.dumps(
                {
                    "repo": args.repo,
                    "provider": getattr(prcfg, "provider", ""),
                    "supported": policy.supported,
                    "error": policy.error,
                    "settings": settings,
                    "suggested_matrix": matrix,
                }
            )
        )
        return 0 if policy.supported else 1

    if not policy.supported:
        output.err(f"pr-research: {policy.error}")
        return 1
    output.header(f"PR-policy research: {args.repo}")
    print("  Live settings:")
    for k, v in settings.items():
        if k == "supported":
            continue
        print(f"    {k}: {v}")
    print("  Suggested pr: policy (drop into .copilot-extensions/agent-worktrees/config.yaml):")
    if matrix:
        for k, v in matrix.items():
            print(f"    {k}: {str(v).lower() if isinstance(v, bool) else v}")
    else:
        print("    (no confident derivation -- keep the defaults)")
    return 0


# pr <verb> namespace -> canonical top-level verb (or manual dispatcher).
_PR_NAMESPACE = {
    "create": "create-pr",
    "status": "pr-status",
    "complete": "pr-complete",
    "ready": "pr-ready",
}


def cmd_pr_dispatch(argv: list[str]) -> int:
    """Route the `pr <verb>` namespace onto the flat pr-* command family."""
    if not argv or argv[0] in ("--help", "-h", "help"):
        _pr_usage()
        return 0 if argv and argv[0] in ("--help", "-h", "help") else 1
    verb = argv[0]
    if verb == "watch":
        return cmd_pr_watch_dispatch(argv[1:])
    if verb == "merge":
        return cmd_pr_merge_dispatch(argv[1:])
    if verb == "research":
        return cmd_pr_research_dispatch(argv[1:])
    canonical = _PR_NAMESPACE.get(verb)
    if not canonical:
        output.err(f"Unknown pr subcommand: {verb}")
        _pr_usage()
        return 1
    parser = _core().build_parser()
    try:
        args = parser.parse_args([canonical, *argv[1:]])
    except SystemExit as exc:
        return int(exc.code or 0)
    handler = _core().COMMAND_MAP.get(args.command)
    if not handler:
        _pr_usage()
        return 1
    return handler(args)
