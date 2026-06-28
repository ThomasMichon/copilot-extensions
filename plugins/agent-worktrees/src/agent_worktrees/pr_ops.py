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
    # First reconcile the active PR's state against the provider: a PR merged
    # *externally* (e.g. Gitea API + auto-merge label) leaves the local record
    # stale at 'open', which would otherwise reuse + force-push a merged branch
    # and open no new PR (#1163).
    _reconcile_active_pr(record, config)
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
            worktree_path, feature_branch, remote, repo, prcfg, record, base,
            config=config, worktree_id=worktree_id, title=eff_title, body=body,
            open_pr=open_pr, attribution=attribution,
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
    # Resolve the PR's target repo as the hosting ``owner/name`` slug (what the
    # provider API needs), in order: explicit --repo > the remote's slug > a
    # previously-recorded value > the local project name (last-resort).
    host_slug = git_ops.remote_slug(remote, cwd=worktree_path)
    default_pr_repo = (
        target_repo or host_slug or (record.repo if record else "") or ""
    )
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
    #    provider sub-agent + set-pr. If the target PR is already open on the
    #    provider, its number/url is surfaced (never re-created) so the caller
    #    does not open a duplicate.
    _finish_auto_open(
        result, config, record, target_pr, title=eff_title, body=body,
        worktree_id=worktree_id, head_sha=head_sha, open_pr=open_pr,
        attribution=attribution,
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
    # The PR opened, but a required label (auto-merge / source:<machine>) may
    # have failed to apply. Surface it rather than swallowing -- the merge gate
    # and source attribution depend on these labels.
    if getattr(pull, "label_error", ""):
        result["pr_label_error"] = pull.label_error


def _finish_auto_open(
    result: dict,
    config: Config,
    record: tracking.WorktreeRecord | None,
    target_pr: PRRecord | None,
    *,
    title: str,
    body: str | None,
    worktree_id: str,
    head_sha: str,
    open_pr: bool | None,
    attribution: bool,
) -> None:
    """Open the PR (when pending) or surface an already-open PR's number/url.

    Shared by the first-run and the re-run paths so neither silently leaves a
    pushed branch without reporting its PR:

    * ``open_pr``/``pr.auto_open`` off -> no-op (manual flow).
    * target PR has no number yet      -> open it via the provider.
    * target PR already opened          -> surface its number/url on ``result``
                                           (never re-create -> no duplicate, #1167).
    """
    prcfg = config.default_repo.pr
    want_open = prcfg.auto_open if open_pr is None else open_pr
    if not want_open or target_pr is None:
        return
    if target_pr.number is None:
        _open_via_provider(
            result, config, record, target_pr, title, body, worktree_id,
            head_sha, attribution=attribution,
        )
        return
    # The PR is already open on the provider -- report it so the caller trusts
    # create-pr's result and does not open a second PR for the same branch.
    result["pr_opened"] = True
    result["number"] = target_pr.number
    if target_pr.url:
        result["url"] = target_pr.url
    if target_pr.state:
        result["state"] = target_pr.state


def _reconcile_active_pr(
    record: tracking.WorktreeRecord | None,
    config: Config,
) -> None:
    """Refresh the active PR's state from the provider (best-effort).

    A PR merged or closed *externally* (e.g. via the Gitea API + the
    ``auto-merge`` label, bypassing ``finalize``/``pr-watch``) leaves the local
    record stale at ``open``.  Branch selection in :func:`create_pr` would then
    reuse and force-push that already-merged branch and open no new PR (#1163).
    Querying the provider and writing back a terminal state makes the active PR
    correctly *terminal* so the append-a-fresh-PR path is taken instead.

    No-op (falls back to the local state) when there is no active PR, it has no
    number yet, it is already terminal, or the provider is unconfigured/
    unreachable.
    """
    if record is None:
        return
    active = record.active_pr()
    if active is None or active.number is None:
        return
    if tracking._pr_is_terminal(active):
        return
    prcfg = config.default_repo.pr
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or (record.repo or "")
    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.resolve_token(prcfg)
        pull = provider.get_pull(
            target_repo, active.number,
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    except Exception:
        # Provider unconfigured/unreachable -- keep the local state rather than
        # guessing.  (Conservative: an unverifiable open PR is still iterated.)
        return
    state = (pull.state or "").strip().lower()
    if state and state not in tracking._PR_NON_TERMINAL:
        active.state = state
        if not active.closed_at:
            active.closed_at = tracking._now_iso()
        tracking.save_record(record)


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
    *,
    config: Config,
    worktree_id: str,
    title: str,
    body: str | None,
    open_pr: bool | None,
    attribution: bool,
) -> dict:
    """Re-run helper: push an already-created feature branch and record state.

    Also completes auto-open: if the matched PR has not been opened yet it is
    opened now; if it is already open its number/url is surfaced.  This keeps a
    re-run from leaving a pushed branch with no reported PR -- which otherwise
    leads the agent to open a duplicate (#1167).
    """
    head_sha = _rev("HEAD", cwd=worktree_path)
    with hooks.allow_pr_push():
        pushed = git_ops.push(remote, feature_branch, cwd=worktree_path, force_with_lease=True)
    if not pushed:
        return {**base, "error": (
            f"Failed to (re)push '{feature_branch}' to '{remote}'."
        )}
    # Match the PRRecord for this branch (a worktree may track several); update
    # it in place rather than clobbering an unrelated active PR. A *terminal*
    # PR for this branch (merged/closed externally, e.g. via the auto-merge
    # label) must NOT be reused -- surfacing it would report the merged PR as if
    # freshly opened and open no PR for the new commits (#1336). In that case we
    # append a FRESH record so the auto-open tail opens a new PR for the push.
    target: PRRecord | None = None
    if record is not None:
        target = next(
            (p for p in record.prs
             if p.branch == feature_branch and not tracking._pr_is_terminal(p)),
            None,
        )
        if target is None:
            target = PRRecord(
                branch=feature_branch, provider=prcfg.provider,
                repo=record.repo or "", opened_at=tracking._now_iso(),
            )
            record.prs.append(target)
        # target is always non-terminal here (a live match or a fresh record).
        target.state = "open"
        target.head_sha = head_sha
        if not target.provider:
            target.provider = prcfg.provider
        tracking.save_record(record)
    base_sha = target.base_sha if target else ""
    result = {
        **base, "success": True, "state": "open", "rerun": True,
        "branch": feature_branch, "remote": remote,
        "base_sha": base_sha, "head_sha": head_sha,
        "provider": prcfg.provider, "default_branch": repo.default_branch,
        "repo": (target.repo if target else ""),
        "pr_count": len(record.prs) if record else 0,
    }
    _finish_auto_open(
        result, config, record, target, title=title, body=body,
        worktree_id=worktree_id, head_sha=head_sha, open_pr=open_pr,
        attribution=attribution,
    )
    return result
