"""``agent-worktrees claims find`` -- fleet-wide lookup of which locally
tracked worktrees hold a claim on a PR in a given repo.

The two-hop `claims <id>` -> `owner_ref` -> `claimant-liveness` recipe (see
the `tracing-claimant-graphs` skill) assumes you already know which
worktree opened a PR. This answers the other direction: given a repo, which
of THIS machine's worktrees -- across every registered project -- claim a
PR there matching a state filter. Motivated by
ThomasMichon/copilot-extensions#4086 (a fleet sweep that previously required
a hand-rolled script per investigation).

Scope: this machine's own tracking stores only, across every project
registered here (reusing `claims_owner._iter_records`'s existing all-projects
scan) -- it does NOT reach across a separate cell (e.g. a dual-boot
machine's native install next to its WSL install, which keeps an entirely
separate tracking store); that still needs a separate invocation per cell,
same as `list --include-other-platforms` itself.
"""

from __future__ import annotations

import argparse

from . import claims_owner, output


def _pr_number(pr) -> int | None:
    """The claim's PR number, falling back to parsing it out of ``url`` when
    unset -- some pre-existing records carry a stale/absent ``number`` even
    when the URL parses fine (issue #4086 calls this out explicitly).

    Covers every supported provider's PR URL shape: GitHub/generic
    ``/pull/<n>``, Gitea ``/pulls/<n>`` (see ``providers/gitea.py``'s own
    ``/repos/{repo}/pulls/{number}`` path), Azure DevOps
    ``/pullrequest/<n>`` (see ``providers/azure_devops.py``'s
    ``_pr_web_url``), and a generic ``/pull-requests/<n>`` some hosts use.
    """
    if pr.number:
        return pr.number
    if pr.url:
        import re

        m = re.search(r"/pull(?:s|-?requests?)?/(\d+)", pr.url)
        if m:
            return int(m.group(1))
    return None


def _candidate_prs(repo: str, state: str) -> list[dict]:
    """Every locally-tracked PR claim in ``repo`` whose local ``state``
    matches (or all states, when ``state == "all"``).

    A worktree's locally tracked PR ``state`` can read stale (e.g. still
    "open" long after the PR merged elsewhere) -- this is a CANDIDATE list;
    cross-check against the provider's live state (``--live``, or the PR
    host directly) before treating a match as authoritative.
    """
    wanted_repo = repo.strip().lower()
    matches: list[dict] = []
    for project, record in claims_owner._iter_records():
        for pr in record.prs or []:
            if (pr.repo or "").strip().lower() != wanted_repo:
                continue
            if state != "all" and pr.state != state:
                continue
            matches.append({
                "project": project,
                "worktree_id": record.worktree_id,
                "machine": record.machine,
                "platform": record.platform,
                "status": record.status,
                "owner_ref": record.owner_ref,
                "codename": record.codename,
                "pr": {
                    "number": _pr_number(pr),
                    "url": pr.url,
                    "state": pr.state,
                    "branch": pr.branch,
                    "provider": pr.provider,
                },
            })
    return matches


def _live_pr_state(repo: str, number: int | None, project: str, provider_name: str) -> str | None:
    """Best-effort live PR state ("open"/"closed"/"merged"); ``None`` when
    unnumbered, unconfigured, or unreachable -- never fatal, the caller keeps
    the candidate as unverified rather than dropping it.

    Resolves ``project``'s OWN PR configuration (api_base/token/provider
    default), not the invoking project's -- a fleet scan can surface a
    candidate from a project configured for a different provider (Gitea,
    Azure DevOps) than the one the command was invoked from."""
    if not number:
        return None
    try:
        from . import config as cfg
        from . import providers

        prcfg = cfg.load_project_config(project).default_repo.pr
        provider = providers.get_provider(provider_name or prcfg.provider or "github")
        token = providers.account_token_for_slug(repo, prcfg)
        result = provider.get_pull(
            repo, number, api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    except Exception:
        return None
    if result.merged:
        return "merged"
    return result.state


def _apply_live_check(matches: list[dict], repo: str, state: str) -> list[dict]:
    kept: list[dict] = []
    for m in matches:
        live_state = _live_pr_state(
            repo, m["pr"].get("number"), m["project"], m["pr"].get("provider") or "",
        )
        m["pr"]["live_state"] = live_state
        if live_state is None or state == "all" or live_state == state:
            kept.append(m)
    return kept


def _emit_human(repo: str, state: str, live: bool, matches: list[dict]) -> None:
    if live:
        verified = (
            " (live-checked; entries with live=None could not be verified "
            "and are kept unconfirmed, not authoritative)"
        )
    else:
        verified = " (local tracking -- stale entries possible; use --live to verify)"
    if not matches:
        output.err(f"No worktree claims a '{state}' PR in {repo}{verified}")
        return
    output.header(f"Worktrees claiming '{state}' PRs in {repo}{verified}")
    for m in matches:
        output.info(
            f"{m['project']} / {m['worktree_id']} "
            f"({m['platform']}, {m['status']}, codename={m.get('codename')})"
        )
        pr = m["pr"]
        live_suffix = f" live={pr.get('live_state')}" if live else ""
        output.info(f"  PR #{pr['number']}: {pr['url']} state={pr['state']}{live_suffix}")
        if m.get("owner_ref"):
            output.info(f"  owner_ref: {m['owner_ref']}")


def cmd_claims_find(args: argparse.Namespace, target: list[str]) -> int:
    if not target or target[0] != "pr":
        msg = "claims find: usage 'find pr --repo <owner/name> [--state open|closed|merged|all] [--live]'"
        if args.json:
            output._json_output({"error": msg})
        else:
            output.err(msg)
        return 2
    repo = getattr(args, "claim_repo", None)
    if not repo:
        msg = "claims find pr: --repo <owner/name> is required"
        if args.json:
            output._json_output({"error": msg})
        else:
            output.err(msg)
        return 2
    state = getattr(args, "claim_state", None) or "open"
    live = bool(getattr(args, "claim_live", False))
    matches = _candidate_prs(repo, state)
    if live:
        matches = _apply_live_check(matches, repo, state)
    if args.json:
        output._json_output({
            "repo": repo, "state": state, "live_checked": live, "matches": matches,
        })
    else:
        _emit_human(repo, state, live, matches)
    return 0 if matches else 1
