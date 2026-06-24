"""Pull-request workflow git operations (PR mode).

This module owns the *git* side of the PR workflow -- it never talks to a
provider API.  The agent (via a Gitea/GitHub/ADO sub-agent) creates the actual
pull request and records its URL/number back via ``set-pr``.

Branch topology (PR mode)::

    origin/master  <-  worktree/{id}  <-  feature/{slug}-{suffix}
      (upstream)       (local base,        (the PR branch: one squashed
                        tracks master)      work commit, pushed to remote)

``create_pr`` squashes the worktree's commits into one, rebases that commit
onto the upstream default branch, creates the feature branch at it, resets the
worktree base branch back to the upstream tip, checks out the feature branch,
and pushes it.  See ``docs/plans/pr-workflow.md`` in aperture-labs.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config as cfg
from . import git_ops, hooks, tracking
from .config import Config
from .tracking import PRRecord

__all__ = ["create_pr", "feature_branch_name", "pr_status", "set_pr", "slugify"]


def slugify(text: str, *, max_len: int = 40) -> str:
    """Sanitize *text* into a branch-safe slug (ascii, lowercase, dashes)."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or "change"


def feature_branch_name(prefix: str, title: str, worktree_id: str) -> str:
    """Build ``{prefix}/{slug}-{worktree_id_suffix}``.

    The suffix is the final dash-delimited token of the worktree id (its
    short hash), which keeps feature branches unique per worktree.
    """
    suffix = worktree_id.rsplit("-", 1)[-1] if "-" in worktree_id else worktree_id
    slug = slugify(title)
    return f"{(prefix or 'feature')}/{slug}-{suffix}"


def _rollback(worktree_path: str, wt_branch: str, orig_sha: str | None) -> None:
    """Restore the worktree branch to its pre-create-pr commit."""
    if orig_sha:
        git_ops.git("checkout", wt_branch, "--quiet", cwd=worktree_path, check=False)
        git_ops.git("reset", "--hard", orig_sha, "--quiet", cwd=worktree_path, check=False)
    git_ops.git("update-ref", "-d", "refs/pre-squash-backup", cwd=worktree_path, check=False)


def _rev(ref: str, *, cwd: str) -> str:
    r = git_ops.git("rev-parse", ref, cwd=cwd, check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def create_pr(
    worktree_id: str,
    config: Config,
    *,
    title: str | None = None,
    branch: str | None = None,
    target_repo: str | None = None,
    new: bool = False,
    body: str | None = None,
    open_pr: bool | None = None,
    attribution: bool = True,
    dry_run: bool = False,
) -> dict:
    """Squash worktree commits, create + push a feature branch for a PR.

    Returns a JSON-friendly result dict.  On success it includes ``branch``,
    ``remote``, ``base_sha``, ``head_sha``, ``provider`` and ``default_branch``.

    When a provider is configured and ``pr.auto_open`` is on (and ``open_pr``
    is not False), the matching provider plugin **opens the PR** right after
    the push -- embedding a hidden source-worktree attribution marker in the
    body and **auto-recording** the resulting url/number on the worktree (no
    skippable manual ``set-pr``).  Provider failure is non-fatal: the feature
    branch is already pushed, so the result carries ``pr_open_error`` and the
    agent can fall back to delegating PR creation manually.

    A worktree can track multiple PRs.  When the active PR is **terminal**
    (merged/closed) -- or ``new`` is set, or none exists -- a *fresh* PR is
    appended (new branch off the current default-branch tip).  When a **live**
    (open/creating) PR exists, its branch is reused and the call iterates it.

    ``target_repo`` (``--repo owner/name``) records the PR's target repo;
    it defaults to the worktree's own repo.

    Idempotent: safe to re-run.  If the worktree is already on the feature
    branch (a prior run pushed it or failed after checkout), the branch is
    simply (re)pushed and the tracking state advanced to ``open``.
    """
    repo = config.default_repo
    prcfg = repo.pr
    remote = repo.remote
    upstream = f"{remote}/{repo.default_branch}"
    worktree_path = str(Path(repo.worktree_root) / worktree_id)
    wt_branch = f"worktree/{worktree_id}"

    base: dict = {"success": False, "worktree_id": worktree_id}

    if not prcfg.enabled:
        return {**base, "error": (
            "PR mode is not enabled for this repo. Set pr.enabled: true in "
            "the repo config to use create-pr."
        )}

    if not Path(worktree_path).exists():
        return {**base, "error": f"Worktree path not found: {worktree_path}"}

    # Load tracking record (optional but expected).
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    record: tracking.WorktreeRecord | None = None
    if yaml_path.exists():
        try:
            record = tracking.load_record(yaml_path)
        except Exception:
            record = None

    if title and record:
        record.title = title.replace("\n", " ").strip()

    eff_title = title or (record.title if record else None) or worktree_id

    # Resolve the active PR and whether it is still live (can receive pushes).
    # A *terminal* active PR (merged/closed) must NOT have its branch reused --
    # pushing onto a merged branch does not reopen it (the #1088->#1104 bug).
    active = record.active_pr() if record else None
    active_is_live = active is not None and not tracking._pr_is_terminal(active)

    # Resolve the feature branch name: explicit > live active PR > derived.
    if branch:
        feature_branch = branch
    elif active_is_live and not new and active.branch:
        feature_branch = active.branch
    else:
        feature_branch = feature_branch_name(prcfg.branch_prefix, eff_title, worktree_id)

    if dry_run:
        return {
            **base, "success": True, "dry_run": True,
            "branch": feature_branch, "remote": remote,
            "provider": prcfg.provider, "default_branch": repo.default_branch,
        }

    if not git_ops.is_clean(cwd=worktree_path):
        return {**base, "error": (
            "Working tree has uncommitted changes; commit or stash them "
            "before create-pr."
        )}

    # Best-effort fetch so the rebase targets current upstream.
    if git_ops.has_remote(remote, cwd=worktree_path):
        try:
            git_ops.fetch(remote, cwd=worktree_path)
        except git_ops.GitError:
            pass

    head_branch = git_ops._get_current_branch_safe(worktree_path)

    # --- Re-run path: already on the feature branch -> (re)push + record. ---
    if head_branch == feature_branch:
        return _push_existing_feature(
            worktree_path, feature_branch, remote, repo, prcfg, record, base
        )

    if head_branch != wt_branch:
        return {**base, "error": (
            f"Worktree HEAD is on '{head_branch}', expected '{wt_branch}'. "
            f"Checkout '{wt_branch}' before create-pr."
        )}

    reusing = bool(active_is_live and not new and active and active.branch == feature_branch)
    if not reusing:
        if git_ops.local_branch_exists(feature_branch, cwd=worktree_path) or \
                git_ops.remote_branch_exists(remote, feature_branch, cwd=worktree_path):
            return {**base, "error": (
                f"Feature branch '{feature_branch}' already exists locally or on "
                f"'{remote}'. Pass --branch to choose a different name."
            )}

    ahead = git_ops.get_commits_ahead(wt_branch, upstream, cwd=worktree_path)
    if not ahead:
        return {**base, "error": (
            f"No commits on {wt_branch} ahead of {upstream} -- nothing to "
            f"open a PR for."
        )}

    orig_sha = _rev(wt_branch, cwd=worktree_path)

    # Resolve the target PRRecord: reuse the live active PR, or append a fresh
    # one (serial re-PR / parallel / explicit --new).  Record the transitional
    # 'creating' state up front so a later failure is recoverable.
    default_pr_repo = target_repo or (record.repo if record else "") or ""
    target_pr: PRRecord | None = None
    if record is not None:
        if reusing and active is not None:
            target_pr = active
            target_pr.state = "creating"
            target_pr.branch = feature_branch
            if not target_pr.provider:
                target_pr.provider = prcfg.provider
            if target_repo:
                target_pr.repo = target_repo
            if not target_pr.opened_at:
                target_pr.opened_at = tracking._now_iso()
        else:
            target_pr = PRRecord(
                state="creating", branch=feature_branch,
                provider=prcfg.provider, repo=default_pr_repo,
                opened_at=tracking._now_iso(),
            )
            record.prs.append(target_pr)
        tracking.save_record(record)

    # 1. Squash all worktree commits into one (always, regardless of strategy).
    squash_msg = (record.title if record and record.title else None) \
        or (eff_title if eff_title != worktree_id else f"{worktree_id} changes")
    if len(ahead) > 1:
        squashed, squash_reason = git_ops.squash_branch(
            upstream, squash_msg, cwd=worktree_path
        )
        if not squashed:
            _rollback(worktree_path, wt_branch, orig_sha)
            detail = f" {squash_reason}" if squash_reason else ""
            return {**base, "error": f"Failed to squash worktree commits.{detail}"}

    # 2. Rebase the squashed commit onto the upstream default branch so the
    #    feature branch is based on the latest master.
    base_sha = ""
    if git_ops.ref_exists(upstream, cwd=worktree_path):
        if not git_ops.rebase(upstream, cwd=worktree_path):
            _rollback(worktree_path, wt_branch, orig_sha)
            return {**base, "error": (
                f"Rebase onto {upstream} hit conflicts. Resolve them on "
                f"'{wt_branch}' and retry create-pr."
            )}
        base_sha = _rev(upstream, cwd=worktree_path)

    head_sha = _rev("HEAD", cwd=worktree_path)

    # 3. Create (or move) the feature branch at the squashed work commit.
    git_ops.git("branch", "-f", feature_branch, "HEAD", cwd=worktree_path, check=False)

    # 4. Checkout the feature branch (worktree/{id} stays as the local base).
    git_ops.checkout(feature_branch, cwd=worktree_path)

    # 5. Reset the worktree base branch to the upstream tip -- it is a
    #    local-only base that tracks master and is never pushed.
    if base_sha:
        git_ops.git("branch", "-f", wt_branch, upstream, cwd=worktree_path, check=False)

    # 6. Push the feature branch.
    with hooks.allow_pr_push():
        pushed = git_ops.push(
            remote, feature_branch, cwd=worktree_path, force_with_lease=reusing
        )
    if not pushed:
        return {**base, "error": (
            f"Failed to push '{feature_branch}' to '{remote}'. The feature "
            f"branch exists locally; tracking state left as 'creating' for "
            f"retry (re-run create-pr)."
        )}

    # 7. Record the open state on the target PR (preserving any url/number
    #    already recorded for a reused live PR).
    if record is not None and target_pr is not None:
        target_pr.state = "open"
        target_pr.branch = feature_branch
        target_pr.base_sha = base_sha
        target_pr.head_sha = head_sha
        if not target_pr.provider:
            target_pr.provider = prcfg.provider
        tracking.save_record(record)

    git_ops.delete_backup_ref(cwd=worktree_path)

    result = {
        **base, "success": True, "state": "open",
        "branch": feature_branch, "remote": remote,
        "base_sha": base_sha, "head_sha": head_sha,
        "provider": prcfg.provider, "default_branch": repo.default_branch,
        "repo": (target_pr.repo if target_pr else default_pr_repo),
        "pr_count": len(record.prs) if record else 0,
    }

    # 8. Auto-open the PR via the configured provider plugin (Phase 2/3):
    #    open the PR, embed the source-worktree attribution marker, and
    #    auto-record the url/number on the worktree. Non-fatal on failure --
    #    the branch is already pushed, so the agent can fall back to a manual
    #    provider sub-agent + set-pr.
    want_open = prcfg.auto_open if open_pr is None else open_pr
    if want_open and target_pr is not None:
        _open_via_provider(
            result, config, record, target_pr, eff_title, body, worktree_id,
            head_sha, attribution=attribution,
        )

    return result


def _open_via_provider(
    result: dict,
    config: Config,
    record: tracking.WorktreeRecord | None,
    target_pr: PRRecord,
    title: str,
    body: str | None,
    worktree_id: str,
    head_sha: str,
    *,
    attribution: bool = True,
) -> None:
    """Open the PR through the provider plugin and auto-record it (best-effort)."""
    from . import providers
    from .providers import attribution as attr

    prcfg = config.default_repo.pr
    machine = record.machine if record else ""
    session = ""
    if record and record.sessions:
        live = [s for s in record.sessions if not s.ended_at]
        session = (live[-1] if live else record.sessions[-1]).session_id

    if attribution:
        marker = attr.build_marker(
            worktree_id, machine=machine, session=session, head=head_sha,
        )
        full_body = attr.append_marker(body or "", marker)
    else:
        full_body = body or ""
    scope = providers.scope_from_create_result(
        result, title=title, body=full_body, prcfg=prcfg, machine=machine,
    )
    try:
        provider = providers.get_provider(prcfg.provider)
        token = providers.resolve_token(prcfg)
        pull = provider.create_pull(scope, token=token)
    except providers.ProviderError as e:
        result["pr_open_error"] = str(e)
        result["pr_opened"] = False
        return

    target_pr.url = pull.url
    target_pr.number = pull.number
    if pull.state:
        target_pr.state = pull.state
    if record is not None:
        tracking.save_record(record)
    result["pr_opened"] = True
    result["url"] = pull.url
    result["number"] = pull.number
    result["state"] = pull.state or result.get("state")


def _load_record_or_none(worktree_id: str) -> tracking.WorktreeRecord | None:
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return None
    try:
        return tracking.load_record(yaml_path)
    except Exception:
        return None


_VALID_PR_STATES = ("creating", "open", "merged", "closed")


def set_pr(
    worktree_id: str,
    *,
    url: str | None = None,
    number: int | None = None,
    state: str | None = None,
    provider: str | None = None,
    branch: str | None = None,
    select_number: int | None = None,
    select_branch: str | None = None,
) -> dict:
    """Record PR metadata (URL/number/state/provider) on a worktree record.

    Called by the agent after a provider sub-agent creates the PR.  Updates
    the **active** PR by default, or a specific one selected by ``--pr`` /
    ``--branch``, so create-pr's branch/base/head SHAs are preserved.  When a
    state transition reaches a terminal state, ``closed_at`` is stamped.
    """
    base: dict = {"success": False, "worktree_id": worktree_id}
    record = _load_record_or_none(worktree_id)
    if record is None:
        return {**base, "error": f"No tracking record found for '{worktree_id}'."}

    if state is not None and state not in _VALID_PR_STATES:
        return {**base, "error": (
            f"Invalid PR state '{state}'. Expected one of: "
            f"{', '.join(_VALID_PR_STATES)}."
        )}

    # Select which PR to update: explicit selector > active > new.
    pr: PRRecord | None
    if select_number is not None:
        pr = next((p for p in record.prs if p.number == select_number), None)
        if pr is None:
            return {**base, "error": (
                f"No tracked PR #{select_number} for '{worktree_id}'."
            )}
    elif select_branch is not None:
        pr = next((p for p in record.prs if p.branch == select_branch), None)
        if pr is None:
            return {**base, "error": (
                f"No tracked PR on branch '{select_branch}' for '{worktree_id}'."
            )}
    else:
        pr = record.active_pr()
        if pr is None:
            pr = PRRecord()
            record.prs.append(pr)

    if url is not None:
        pr.url = url
    if number is not None:
        pr.number = number
    if provider is not None:
        pr.provider = provider
    if branch is not None:
        pr.branch = branch
    if state is not None:
        pr.state = state
    elif not pr.state:
        # First time recording metadata with no explicit state -> open.
        pr.state = "open"
    if not pr.opened_at:
        pr.opened_at = tracking._now_iso()
    if tracking._pr_is_terminal(pr) and not pr.closed_at:
        pr.closed_at = tracking._now_iso()

    tracking.save_record(record)
    return {**base, "success": True, **_pr_to_dict(pr)}


def pr_status(worktree_id: str, *, all_prs: bool = False) -> dict:
    """Return the tracked PR metadata for a worktree (for pr-status).

    Returns the **active** PR by default.  With ``all_prs`` the full ``prs``
    history is included alongside the active one.  ``pr_count`` is always
    present so the orphan-detection probe can key on existence.
    """
    base: dict = {"worktree_id": worktree_id}
    record = _load_record_or_none(worktree_id)
    if record is None:
        return {**base, "has_pr": False, "pr_count": 0,
                "error": f"No tracking record found for '{worktree_id}'."}
    active = record.active_pr()
    result = {**base, "has_pr": active is not None, "pr_count": len(record.prs)}
    if active is not None:
        result.update(_pr_to_dict(active))
    if all_prs:
        result["prs"] = [_pr_to_dict(p) for p in record.prs]
    return result


def _pr_to_dict(pr: PRRecord) -> dict:
    return {
        "state": pr.state,
        "branch": pr.branch,
        "base_sha": pr.base_sha,
        "head_sha": pr.head_sha,
        "url": pr.url,
        "number": pr.number,
        "provider": pr.provider,
        "repo": pr.repo,
        "opened_at": pr.opened_at,
        "closed_at": pr.closed_at,
    }


def _push_existing_feature(
    worktree_path: str,
    feature_branch: str,
    remote: str,
    repo,
    prcfg,
    record: tracking.WorktreeRecord | None,
    base: dict,
) -> dict:
    """Re-run helper: push an already-created feature branch and record state."""
    head_sha = _rev("HEAD", cwd=worktree_path)
    with hooks.allow_pr_push():
        pushed = git_ops.push(remote, feature_branch, cwd=worktree_path, force_with_lease=True)
    if not pushed:
        return {**base, "error": (
            f"Failed to (re)push '{feature_branch}' to '{remote}'."
        )}
    # Match the PRRecord for this branch (a worktree may track several); update
    # it in place rather than clobbering an unrelated active PR.
    target: PRRecord | None = None
    if record is not None:
        target = next((p for p in record.prs if p.branch == feature_branch), None)
        if target is None:
            target = PRRecord(
                branch=feature_branch, provider=prcfg.provider,
                repo=record.repo or "", opened_at=tracking._now_iso(),
            )
            record.prs.append(target)
        target.state = "open"
        target.head_sha = head_sha
        if not target.provider:
            target.provider = prcfg.provider
        tracking.save_record(record)
    base_sha = target.base_sha if target else ""
    return {
        **base, "success": True, "state": "open", "rerun": True,
        "branch": feature_branch, "remote": remote,
        "base_sha": base_sha, "head_sha": head_sha,
        "provider": prcfg.provider, "default_branch": repo.default_branch,
    }
