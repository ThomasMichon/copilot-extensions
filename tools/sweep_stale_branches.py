#!/usr/bin/env python3
"""Scheduled safety-net sweep for stale merged-PR branches.

This automates the exact manual technique used for a one-time cleanup of
3000+ accumulated branches on `ThomasMichon/copilot-extensions` (see the
`branch-hygiene` effort): cross-reference every merged pull request's head
branch against the branches that still physically exist on `origin`, and
delete the ones whose PR is already merged.

Why this still matters even after `delete_branch_on_merge` is enabled on the
repo and `agent-worktrees`' own merge tooling passes `--delete-branch` by
default: both of those only clean up *going forward*, for merges that go
through those exact paths. This sweep is the safety net for everything else
-- a PR merged through GitHub's own UI before the setting was retroactively
enabled, an external tool/bot that never requested branch deletion, or a
pre-existing backlog on any other repo this pattern gets adopted in.

Deliberately conservative, mirroring `tools/module-health-watchdog.py`'s own
convention:

- Only treats a branch as deletable when its **single most recent** PR
  record (open, closed, or merged -- see ``_latest_pr_per_branch``) is a
  MERGED one; an open or closed-unmerged PR's head branch is never treated
  as deletable, and that veto wins even over a coincidentally-matching OID
  from some older, now-superseded merge (reused/reopened branch names).
- A branch name alone is never a stable identity: a name can be deleted and
  recreated, force-pushed with new commits, picked up by a brand-new open
  PR, or (for a same-named branch on a fork) never have belonged to this
  repository at all. Planning only proposes a branch whose **same-repo,
  MERGED** PR's recorded head commit OID still matches that branch's
  *current* commit OID on `origin` as of the planning snapshot. The actual
  deletion then performs its own atomic, server-side compare-and-delete via
  `git push --force-with-lease=<ref>:<expected-oid>` -- not a separate
  check-then-act GET+DELETE, which a transient GET failure or a race
  between the two calls could silently bypass (fail open). A rejected lease
  (the branch moved between planning and deleting) is treated as a safe
  skip ONLY when it is the lease's own stale-info rejection -- any other
  push rejection (a protected-branch hook declining the delete, a
  permission error, etc.) is a real failure. A compare-and-delete lease
  alone cannot catch a new PR opened against an already-planned-deletable
  branch, since opening a PR never changes that branch's OID -- the actual
  deletion also re-checks live, immediately before each delete, whether a
  same-repository OPEN PR now has that branch as its head (see
  `_has_open_pr`), and skips if so. A fork-originated PR's `headRefName`
  never denotes a branch on this repo at all and is excluded entirely (both
  from deletion and from counting toward "has any PR record" below). When
  a branch name has been reused across multiple PR records
  over time (merged, then closed unmerged, then reopened, etc.), the
  **single most recent** one (by event time) determines both whether the
  branch is deletable at all and, if so, the expected OID -- never an
  arbitrary record.
- `--execute` refuses to push a deletion through a local git remote that
  doesn't resolve to the exact `--repo` host+path (`github.com` plus an
  exact owner/repo match, not a mere suffix), and scrubs ambient
  `GIT_DIR`/`GIT_WORK_TREE`/etc. environment variables before every git
  call so an inherited repository-selection variable can't silently
  redirect it.
- Never touches a protected branch (the repo's configured default branch,
  plus `dev` for this repo's own `main`+`dev` pair -- see
  ``DEFAULT_PROTECTED_EXTRA``).
- Never auto-deletes a branch with **no PR record at all** (open, closed, or
  merged) that merely looks disposable by naming convention
  (``worktree/*``, ``feature/*``, ``pr/*``) -- those need per-case human/agent
  judgment (the one-time sweep found genuinely live, minutes-old branches
  among them). Instead it opens/updates a single tracking issue listing them
  for triage, exactly as cautious as leaving them alone, and closes that
  same issue again once a later run finds nothing left to flag -- so it
  never lingers with a stale branch list.
- Refuses to classify anything -- deletable or flagged -- from a PR-history
  query it cannot prove is complete: a branch absent from a truncated
  ``gh pr list`` result is indistinguishable from a branch with no PR record
  at all, and a merged PR outside a truncated window would be silently
  skipped as "not mergeable" too. See ``_pull_requests`` below.
- Defaults to a dry run; nothing is deleted or filed without ``--execute``.

Usage::

    python tools/sweep_stale_branches.py                  # dry run, prints the plan
    python tools/sweep_stale_branches.py --execute         # actually delete + file
    python tools/sweep_stale_branches.py --repo owner/name # override for local testing

Exit code is 0 for a successful sweep (including a dry run, and including a
run that found nothing to do); nonzero when `--execute` was asked to
actually act and a branch deletion or the tracking-issue file/update itself
failed, or when the PR-history/branch-list queries could not be proven
complete -- so a scheduled run's own CI goes red instead of silently
reporting success while leaving work undone or acting on partial data.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass

DEFAULT_REPO = "ThomasMichon/copilot-extensions"
# This repo's own second long-lived branch, alongside whatever its
# configured default branch is (resolved live -- see `_default_branch`).
DEFAULT_PROTECTED_EXTRA = ("dev",)
FLAG_PATTERNS = ("worktree/*", "feature/*", "pr/*")
TRACKING_LABEL = "stale-branch-triage"
# A `gh pr list`/`gh api` row count landing exactly on the requested
# `--limit` is the signal we use to detect a truncated result (see
# `_pull_requests`/`_remote_branches`) rather than genuinely having fetched
# every record.
DEFAULT_LIMIT = 20000

#: Ambient Git repository-selection variables that must never leak into a
#: `git` subprocess here -- if the calling environment has e.g. `GIT_DIR` or
#: `GIT_WORK_TREE` set, it silently overrides this script's intended target
#: (the current working directory's checkout), so a remote lookup or push
#: could act against an entirely different repository/configuration than
#: the one actually checked out. Mirrors
#: `tools/coverage_guided_selection/ancestor_resolution.py`'s
#: `_REPOSITORY_CONTEXT_ENV`/`scrubbed_git_env()` (kept as an independent
#: copy by that module's own documented convention -- each consumer stays
#: dependency-free rather than importing a sibling tool module).
_REPOSITORY_CONTEXT_ENV = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_NAMESPACE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
})


def _scrubbed_git_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in list(env):
        upper = name.upper()
        if (
            upper in _REPOSITORY_CONTEXT_ENV
            or upper.startswith("GIT_CONFIG_KEY_")
            or upper.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(name, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


@dataclass(frozen=True)
class PullRequestHead:
    branch: str
    head_oid: str
    same_repo: bool
    state: str  # "OPEN", "CLOSED", or "MERGED" (gh's own GraphQL enum casing)
    merged_at: str  # ISO8601 (gh's own format, lexically sortable); "" if never merged
    closed_at: str  # ISO8601; "" if still open (also set for a MERGED PR, but unused then)


# Sentinel "event time" for an OPEN PR: an open PR is never resolved, so it
# must always be treated as the most recent event for its branch name --
# lexically greater than any real ISO8601 timestamp gh can report.
_OPEN_EVENT_TIME_SENTINEL = "9999-12-31T23:59:59Z"


def _latest_pr_per_branch(pull_requests: list[PullRequestHead]) -> dict[str, PullRequestHead]:
    """Same-repository branch name -> its single most recently *resolved*
    (or still-open) PR record.

    A branch name can be reused across the repo's history: merged once,
    recreated, then closed unmerged, or reopened, possibly more than once.
    Comparing a still-present branch's current OID against whichever PR
    record happens to come first in `gh pr list`'s own ordering -- or even
    against only the newest MERGED record while ignoring a newer
    closed-unmerged or still-open one -- can either miss a genuinely
    deletable branch (stale OID) or, worse, delete a branch a more recent
    non-merged PR still legitimately owns. This resolves exactly one
    record per branch name: the most recent by event time (an OPEN PR's
    event time is treated as unresolved/maximal -- it always wins), so a
    caller only ever needs to ask "is THIS branch's latest record a merge,
    and does its head OID match?" to decide deletability.
    """
    latest: dict[str, tuple[str, PullRequestHead]] = {}  # branch -> (event_time, pr)
    for pr in pull_requests:
        if not pr.same_repo:
            continue
        if pr.state == "OPEN":
            event_time = _OPEN_EVENT_TIME_SENTINEL
        elif pr.state == "MERGED":
            event_time = pr.merged_at
        else:  # CLOSED, unmerged
            event_time = pr.closed_at
        current = latest.get(pr.branch)
        if current is None or event_time > current[0]:
            latest[pr.branch] = (event_time, pr)
    return {branch: pr for branch, (_event_time, pr) in latest.items()}


def plan_sweep(
    remote_branches: dict[str, str],
    merged_heads: dict[str, str],
    non_merged_branch_names: set[str],
    all_pr_branch_names: set[str],
    protected_branches: set[str],
    flag_patterns: tuple[str, ...] = FLAG_PATTERNS,
) -> tuple[list[str], list[str]]:
    """Return ``(deletable, flagged)`` branch names, both sorted.

    ``remote_branches``: current branch name -> current commit OID on
    ``origin``, as of the moment the caller fetched it.

    ``merged_heads``: a **same-repository** branch name -> the head OID of
    its single most recently resolved PR record, ONLY when that record is a
    MERGED one (see :func:`_latest_pr_per_branch`). A branch whose most
    recent record is anything else (open, closed-unmerged, or a
    fork-originated PR) must never appear here (callers are responsible for
    excluding it).

    ``non_merged_branch_names``: every branch name whose single most recent
    PR record is OPEN or CLOSED-unmerged. Checked as an explicit,
    independent veto -- defense in depth alongside ``merged_heads``
    already excluding these -- so a branch currently backing a live (open
    or recently-closed-unmerged) PR is never deleted even if some older,
    now-superseded merge happened to leave a matching OID lying around.

    ``deletable``: a branch whose *current* OID on origin still exactly
    matches its most recent record's head OID -- the identity check
    described in this module's docstring -- AND whose most recent record is
    a merge (i.e. not vetoed by ``non_merged_branch_names``). A branch
    reused since (force-pushed, or picked up by a new open/closed PR with
    new commits) has a different current OID and is correctly left alone
    regardless. Excludes anything in ``protected_branches``. This is a
    *planning-time* decision only; the actual deletion performs its own
    atomic compare-and-delete lease check (see :func:`_delete_branch`),
    since a branch can still move between planning and acting.

    ``flagged``: a branch matching one of ``flag_patterns`` with **no** PR
    record at all (not even open or closed-unmerged) -- never deleted here,
    only surfaced for a human/agent to triage case-by-case.
    """
    deletable = sorted(
        branch
        for branch, current_oid in remote_branches.items()
        if branch not in protected_branches
        and branch not in non_merged_branch_names
        and branch in merged_heads
        and merged_heads[branch] == current_oid
    )
    flagged = sorted(
        branch
        for branch in remote_branches
        if branch not in protected_branches
        and branch not in all_pr_branch_names
        and any(fnmatch.fnmatchcase(branch, pattern) for pattern in flag_patterns)
    )
    return deletable, flagged


class GhCallFailed(RuntimeError):
    """A `gh` invocation needed to compute or act on the plan failed."""


class TruncatedResult(RuntimeError):
    """A `gh` query returned exactly its requested limit's worth of rows --
    indistinguishable from a complete result, so this run refuses to
    classify anything from it rather than silently act on partial data."""


def _gh_json(args: list[str]) -> object:
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise GhCallFailed(f"gh {' '.join(args)} failed: {out.stderr.strip()}")
    return json.loads(out.stdout or "null")


def _pull_requests(repo: str, limit: int) -> list[PullRequestHead]:
    """Every PR's head info (open, closed, and merged), fully resolved.

    Raises :class:`TruncatedResult` if the row count exactly equals
    ``limit`` -- that is indistinguishable from "there were exactly this
    many", so this run cannot prove it saw every PR and must not classify
    anything (a merged PR outside the window looks absent; so does a branch
    with a real, un-merged PR outside the window).
    """
    rows = _gh_json(
        [
            "pr", "list", "--repo", repo, "--state", "all",
            "--json", "headRefName,headRefOid,isCrossRepository,state,mergedAt,closedAt",
            "--limit", str(limit),
        ]
    )
    if len(rows) == limit:
        raise TruncatedResult(
            f"gh pr list --state all returned exactly --limit {limit} rows for {repo} -- "
            "cannot prove this is a complete PR history; refusing to classify anything "
            "from it. Re-run with a higher --limit."
        )
    return [
        PullRequestHead(
            branch=row["headRefName"],
            head_oid=row["headRefOid"],
            same_repo=not row["isCrossRepository"],
            state=row["state"],
            merged_at=row.get("mergedAt") or "",
            closed_at=row.get("closedAt") or "",
        )
        for row in rows
    ]


def _default_branch(repo: str) -> str:
    row = _gh_json(["repo", "view", repo, "--json", "defaultBranchRef"])
    return row["defaultBranchRef"]["name"]


def _remote_branches(repo: str, limit: int) -> dict[str, str]:
    """Every branch currently on ``origin`` -> its current commit OID.

    Raises :class:`TruncatedResult` on the same "exactly hit the limit"
    signal as :func:`_pull_requests` -- a truncated branch list would make a
    genuinely stale branch outside the window invisible to this sweep
    entirely (silently under-acting, not over-acting, but still an
    unproven result this run must not pretend is complete).
    """
    out = subprocess.run(
        [
            "gh", "api", "--method", "GET", f"repos/{repo}/branches",
            "--paginate", "-F", "per_page=100",
            "--jq", r'.[] | .name + "\t" + .commit.sha',
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise GhCallFailed(f"gh api repos/{repo}/branches failed: {out.stderr.strip()}")
    lines = [line for line in out.stdout.splitlines() if line]
    if len(lines) >= limit:
        raise TruncatedResult(
            f"repos/{repo}/branches returned {len(lines)} rows (>= the {limit} safety "
            "bound) -- refusing to assume this sweep saw every branch. Re-run with a "
            "higher --limit."
        )
    branches: dict[str, str] = {}
    for line in lines:
        name, _, sha = line.partition("\t")
        branches[name] = sha
    return branches


GITHUB_HOST = "github.com"
_SCP_STYLE_RE = re.compile(r"^(?:[^@]+@)?(?P<host>[^:/]+):(?P<path>.+)$")


def _parse_git_remote_owner_repo(url: str) -> tuple[str, str] | None:
    """Return ``(host, "owner/repo")`` for a git remote URL (https, ssh://,
    or scp-style ``git@host:owner/repo``), or ``None`` if it can't be
    parsed. Always an exact host + exact two-segment path -- never a mere
    suffix match, which a URL like ``https://example.invalid/owner/repo``
    would otherwise pass against a careless ``str.endswith`` check."""
    url = url.strip()
    if url.endswith(".git"):
        url = url[: -len(".git")]
    scp_match = _SCP_STYLE_RE.match(url) if "://" not in url else None
    if scp_match:
        host = scp_match.group("host")
        path = scp_match.group("path")
    else:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if not host or len(segments) != 2:
        return None
    return host.lower(), f"{segments[0]}/{segments[1]}".lower()


def _verify_remote_matches_repo(remote: str, repo: str) -> None:
    """Refuse to push a deletion through a local ``remote`` that isn't
    actually this ``repo`` -- a mismatched local checkout (e.g. ``--repo``
    pointed elsewhere than the directory this script happens to run in)
    must never result in deleting branches on the wrong repository. Checks
    both the host (must be ``github.com``) and an EXACT owner/repo path --
    never a suffix match, which a lookalike host would otherwise pass."""
    out = subprocess.run(
        ["git", "remote", "get-url", remote], capture_output=True, text=True, env=_scrubbed_git_env()
    )
    if out.returncode != 0:
        raise GhCallFailed(f"git remote get-url {remote} failed: {out.stderr.strip()}")
    url = out.stdout.strip()
    parsed = _parse_git_remote_owner_repo(url)
    if parsed is None:
        raise GhCallFailed(f"git remote '{remote}' ({url}) could not be parsed as an owner/repo URL -- refusing to push deletions.")
    host, owner_repo = parsed
    if host != GITHUB_HOST or owner_repo != repo.lower():
        raise GhCallFailed(
            f"git remote '{remote}' ({url}) does not match --repo {repo} -- refusing to "
            "push deletions against a mismatched local checkout."
        )


def _has_open_pr(repo: str, branch: str) -> bool:
    """True if ``repo`` currently has an OPEN PR whose head is ``branch``.

    A compare-and-delete lease alone cannot catch this case: opening a new
    PR against an existing branch does not change that branch's commit
    OID, so a branch that was safely deletable at planning time can gain a
    brand-new open PR later in the same run (sweeps over large backlogs can
    take meaningful wall-clock time) without ever failing the lease. Must
    be re-checked live, immediately before each delete.
    """
    out = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "number"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        # Fail closed: an inability to confirm "no open PR" must never be
        # treated as "confirmed none" -- skip the delete rather than risk
        # closing a live PR on a transient `gh` hiccup.
        return True
    try:
        rows = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return True
    return len(rows) > 0


def _delete_branch(remote: str, repo: str, branch: str, expected_oid: str) -> bool:
    """Delete ``branch`` on ``remote`` via an atomic compare-and-delete:
    ``git push --force-with-lease`` fails the update server-side unless the
    remote ref is still exactly at ``expected_oid`` at push time -- a real
    lease, not a separate check-then-act GET+DELETE (which a transient GET
    failure or a race between the two calls could silently bypass). Also
    re-checks live for a newly-opened PR immediately before deleting (see
    :func:`_has_open_pr`) -- the lease alone cannot catch that case, since
    opening a PR against an existing branch never changes its OID."""
    if _has_open_pr(repo, branch):
        print(f"[SKIP] {branch} now has an open PR (opened since planning) -- not deleting.")
        return True
    lease = f"refs/heads/{branch}:{expected_oid}"
    out = subprocess.run(
        ["git", "push", remote, f"--force-with-lease={lease}", f":refs/heads/{branch}"],
        capture_output=True,
        text=True,
        env=_scrubbed_git_env(),
    )
    if out.returncode == 0:
        print(f"[OK] deleted {branch}")
        return True
    combined = out.stdout + out.stderr
    if "remote ref does not exist" in combined or ("unable to delete" in combined and "does not exist" in combined):
        print(f"[OK] {branch} already gone -- nothing to do.")
        return True
    # Only the lease's own stale-info rejection is a safe "someone else
    # already moved this ref" outcome. ANY other rejection (a protected-
    # branch hook declining the delete, a permission error reported as a
    # push rejection, etc.) is a real failure and must not be swallowed --
    # treating every "rejected" as a skip would let the branch silently
    # survive while this job still reports success.
    if "(stale info)" in combined:
        print(f"[SKIP] {branch} lease rejected (moved since planning) -- not deleting.")
        return True
    print(f"[ERROR] failed to delete {branch}: {combined.strip()}", file=sys.stderr)
    return False


def _existing_tracking_issue(repo: str) -> int | None:
    rows = _gh_json(
        ["issue", "list", "--repo", repo, "--label", TRACKING_LABEL, "--state", "open", "--json", "number"]
    )
    return rows[0]["number"] if rows else None


#: GitHub's own issue-body hard cap is 65,536 characters. A representative
#: backlog (the 3000+-branch scale this tool was built to sweep) could
#: render well past that if every flagged branch were listed unbounded,
#: permanently failing every create/edit attempt (and thus silently
#: breaking the triage channel this issue exists to provide). Cap the
#: listed entries and report an omitted count instead -- a run's own
#: stdout always has the complete list regardless.
MAX_FLAGGED_LISTED = 200


def _file_or_update_tracking_issue(repo: str, flagged: list[str]) -> bool:
    listed = flagged[:MAX_FLAGGED_LISTED]
    omitted = len(flagged) - len(listed)
    omitted_note = (
        f"\n- ... and {omitted} more branch(es) not listed here (see this run's own "
        "stdout, or re-run `python tools/sweep_stale_branches.py` locally, for the "
        "complete list).\n"
        if omitted > 0 else "\n"
    )
    body = (
        "## Branches with no pull-request record at all\n\n"
        "The scheduled stale-branch sweep (`tools/sweep_stale_branches.py`, "
        "`.github/workflows/stale-branch-sweep.yml`) found these branches "
        "matching a disposable-looking naming convention "
        f"(`{'`, `'.join(FLAG_PATTERNS)}`) with **no** associated pull "
        "request -- open, closed, or merged. That makes them unsafe to "
        "auto-delete (a one-time manual sweep found genuinely live, "
        "minutes-old branches in exactly this shape); each needs per-case "
        "human/agent judgment before deletion.\n\n"
        + "\n".join(f"- `{branch}`" for branch in listed)
        + omitted_note
        + "\nThis issue is updated in place on each scheduled run -- do not "
        "expect a new issue every time.\n"
    )
    existing = _existing_tracking_issue(repo)
    if existing is not None:
        out = subprocess.run(
            ["gh", "issue", "edit", str(existing), "--repo", repo, "--body", body],
            capture_output=True,
            text=True,
        )
        if out.returncode != 0:
            print(f"[ERROR] failed to update tracking issue #{existing}: {out.stderr.strip()}", file=sys.stderr)
            return False
        print(f"[OK] updated tracking issue #{existing}")
        return True
    subprocess.run(
        ["gh", "label", "create", TRACKING_LABEL, "--repo", repo,
         "--color", "D4C5F9",
         "--description", "No-PR-record branch flagged by the stale-branch sweep for manual triage",
         "--force"],
        capture_output=True,
        text=True,
    )
    out = subprocess.run(
        [
            "gh", "issue", "create", "--repo", repo,
            "--title", "Stale-branch sweep: branches with no PR record need triage",
            "--label", TRACKING_LABEL,
            "--body", body,
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        print(f"[ERROR] failed to file tracking issue: {out.stderr.strip()}", file=sys.stderr)
        return False
    print(f"[OK] filed {out.stdout.strip()}")
    return True


def _clear_tracking_issue(repo: str) -> bool:
    """Close the existing no-PR-record tracking issue, if any, when a run
    finds nothing left to flag -- otherwise an already-filed issue's branch
    list silently goes stale forever (the branches it named may since have
    been deleted or gained a real PR)."""
    existing = _existing_tracking_issue(repo)
    if existing is None:
        return True
    out = subprocess.run(
        [
            "gh", "issue", "close", str(existing), "--repo", repo,
            "--comment", "No branches currently match the no-PR-record criteria -- closing (the stale-branch sweep re-files/reopens this automatically if that changes).",
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        print(f"[ERROR] failed to close tracking issue #{existing}: {out.stderr.strip()}", file=sys.stderr)
        return False
    print(f"[OK] closed tracking issue #{existing} (nothing left to flag)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"owner/name to sweep (default {DEFAULT_REPO})")
    parser.add_argument(
        "--remote", default="origin",
        help="local git remote name to push deletions through (default origin); must resolve to --repo",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=f"safety bound on PRs/branches fetched; a result hitting it aborts as possibly truncated (default {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually delete branches and file/update the tracking issue (default: dry-run/report only)",
    )
    args = parser.parse_args()

    try:
        pull_requests = _pull_requests(args.repo, args.limit)
        remote_branches = _remote_branches(args.repo, args.limit)
        default_branch = _default_branch(args.repo)
        if args.execute:
            _verify_remote_matches_repo(args.remote, args.repo)
    except (GhCallFailed, TruncatedResult) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1

    latest_per_branch = _latest_pr_per_branch(pull_requests)
    merged_heads = {
        branch: pr.head_oid
        for branch, pr in latest_per_branch.items()
        if pr.state == "MERGED" and pr.head_oid
    }
    non_merged_branch_names = {
        branch for branch, pr in latest_per_branch.items() if pr.state != "MERGED"
    }
    # "Has any PR record at all" must only count a SAME-REPOSITORY PR's
    # branch -- a fork PR's `headRefName` belongs to the fork, not to a
    # same-named branch in this repository, so counting it here would
    # wrongly suppress triage for a local disposable branch that truly has
    # no same-repo PR of its own.
    all_pr_branch_names = {pr.branch for pr in pull_requests if pr.same_repo}
    protected = {default_branch, *DEFAULT_PROTECTED_EXTRA}

    deletable, flagged = plan_sweep(remote_branches, merged_heads, non_merged_branch_names, all_pr_branch_names, protected)

    print(f"[INFO] {len(remote_branches)} remote branch(es) examined; protected: {sorted(protected)}.")
    print(f"[INFO] {len(deletable)} deletable (merged PR, branch unchanged since merge):")
    for branch in deletable:
        print(f"  - {branch}")
    print(f"[INFO] {len(flagged)} flagged for triage (no PR record, disposable-looking name):")
    for branch in flagged:
        print(f"  - {branch}")

    if not args.execute:
        print("[INFO] dry run -- pass --execute to actually delete and file/update the tracking issue.")
        return 0

    failed = False
    for branch in deletable:
        if not _delete_branch(args.remote, args.repo, branch, merged_heads[branch]):
            failed = True

    if flagged:
        if not _file_or_update_tracking_issue(args.repo, flagged):
            failed = True
    elif not _clear_tracking_issue(args.repo):
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
