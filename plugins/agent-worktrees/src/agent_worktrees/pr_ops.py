"""Pull-request workflow git operations (PR mode).

This module owns the *git* side of the PR workflow -- it never talks to a
provider API.  The agent (via a Gitea/GitHub/ADO sub-agent) creates the actual
pull request and records its URL/number back via ``set-pr``.

Branch topology (PR mode)::

    origin/master  <-  worktree/{id}  <-  feature/{slug}-{suffix}
      (upstream)       (local base,        (the PR branch: one squashed
                        tracks master)      work commit, pushed to remote)

``create_pr`` squashes the worktree's commits into one and rebases that commit
onto the upstream default branch.  The local worktree then **always lands on
that squashed commit** -- HEAD stays on ``worktree/{id}`` and the branch is
never reset off it (#1804) -- regardless of ``pr.head_scheme``.  The scheme only
selects how the PR head is *published* (its name + push mechanism):

- ``refspec`` (default, #1815/#1899): push ``worktree/{id}`` straight to the PR
  head ref (``worktree/{id}:refs/heads/{head}``, e.g. ``pr/{slug}``) -- no local
  feature branch.
- ``snapshot`` (legacy/compatible): copy the squashed commit onto a
  ``feature/{slug}-{suffix}`` branch (the older namespace) and push THAT.
  ``worktree/{id}`` is left on the squashed commit (sitting ahead of master
  while the PR is open); a later ``git sync`` reconciles it on merge. Needs no
  pre-push-hook cooperation, so it is the safe opt-out for a repo whose hook
  still blocks the mediated refspec push.

Either way the worktree stays on its own branch at the squashed commit; the
``head_scheme`` toggle is purely about PR-head naming + publish mechanism, not
about whether the worktree is reset.

See ``docs/plans/pr-workflow.md`` in aperture-labs.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config as cfg
from . import git_ops, hooks, tracking
from .config import Config
from .tracking import PRRecord

HOLD_LABEL = "do-not-merge"

__all__ = [
    "HOLD_LABEL",
    "create_pr",
    "feature_branch_name",
    "pr_head_name",
    "pr_ready",
    "pr_status",
    "resolve_head_pattern",
    "set_pr",
    "slugify",
]


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


def _worktree_suffix(worktree_id: str) -> str:
    """The final dash-delimited token of a worktree id (its short hash)."""
    return worktree_id.rsplit("-", 1)[-1] if "-" in worktree_id else worktree_id


def _sanitize_head_ref(name: str) -> str:
    """Collapse a formatted head-name template into a tidy, valid git ref.

    Trims each ``/``-delimited segment and drops empty ones (e.g. an
    unresolved ``{username}`` that expanded to nothing), so a pattern like
    ``user/{username}/{slug}-{suffix}`` never yields a ``//`` or trailing/
    leading slash.
    """
    parts = [seg.strip().strip("-") for seg in name.split("/")]
    parts = [seg for seg in parts if seg]
    return "/".join(parts) or "pr/change"


def _resolve_username(cwd: str | None) -> str:
    """Resolve the ``{username}`` token from the repo's git identity.

    Prefers the local-part of ``user.email`` (e.g. ``cjohnson@...`` ->
    ``cjohnson``), then ``user.name``, slugified; falls back to ``user``.
    """
    if not cwd:
        return "user"
    for key in ("user.email", "user.name"):
        r = git_ops.git("config", key, cwd=cwd, check=False)
        val = r.stdout.strip() if r.returncode == 0 else ""
        if val:
            local = val.split("@", 1)[0]
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", local.lower()).strip("-")
            if slug:
                return slug
    return "user"


def resolve_head_pattern(prcfg) -> str:
    """The PR head-name template for *prcfg* (explicit override or scheme default).

    An explicit ``head_pattern`` wins.  Otherwise the default depends on the
    scheme: ``refspec`` uses the clean ``pr/{slug}-{suffix}`` namespace, while
    ``snapshot`` keeps today's ``{prefix}/{slug}-{suffix}`` (``feature/<slug>``)
    names byte-for-byte.
    """
    if getattr(prcfg, "head_pattern", ""):
        return prcfg.head_pattern
    if getattr(prcfg, "head_scheme", "snapshot") == "refspec":
        return "pr/{slug}-{suffix}"
    return "{prefix}/{slug}-{suffix}"


def pr_head_name(
    prcfg, title: str, worktree_id: str, *, cwd: str | None = None, machine: str = "",
) -> str:
    """Build the PR head branch name from the repo's configured template.

    Resolves the ``head_pattern`` template (scheme-aware default) against the
    ``{prefix}`` / ``{slug}`` / ``{suffix}`` / ``{username}`` / ``{machine}``
    tokens.  With the ``snapshot`` default this returns exactly
    ``feature_branch_name(prefix, title, worktree_id)``.
    """
    pattern = resolve_head_pattern(prcfg)
    tokens = {
        "prefix": (getattr(prcfg, "branch_prefix", "") or "feature"),
        "slug": slugify(title),
        "suffix": _worktree_suffix(worktree_id),
        "username": _resolve_username(cwd),
        "machine": machine or "",
    }
    try:
        name = pattern.format(**tokens)
    except (KeyError, IndexError, ValueError):
        # A malformed template must never break create-pr -- fall back to the
        # legacy default rather than raising.
        name = f"{tokens['prefix']}/{tokens['slug']}-{tokens['suffix']}"
    return _sanitize_head_ref(name)


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
    hold: bool = False,
    draft: bool = False,
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

    Idempotent: safe to re-run.  A successful run leaves HEAD on the worktree
    branch (``worktree/{id}``) at the squashed commit -- it is never reset off
    it -- so a retry after a push-that-failed-to-open lands there with the
    squashed work still in place and is recognized as a re-run of the live
    tracked PR: the head is simply (re)pushed (force-with-lease) with the
    tracking state advanced to ``open``.  Two legacy/migration cases are handled
    the same way: HEAD still on the feature branch (a push that failed before the
    old code returned HEAD), and ``worktree/{id}`` sitting at the upstream tip
    with the feature branch still local (a worktree created under the old
    reset-to-upstream scheme).
    """
    repo = config.default_repo
    prcfg = repo.pr
    remote = repo.remote
    # ``--hold`` is retained as a deprecated alias for ``--draft``: the old
    # "open held with a do-not-merge label" model is retired in favour of
    # Gitea's native draft state (a WIP-prefixed title), which ``pr-ready``
    # clears. Both flags mean the same thing now -- open the PR as a draft.
    want_draft = bool(draft or hold)
    upstream = f"{remote}/{repo.default_branch}"
    worktree_path = tracking.resolve_worktree_path(worktree_id, repo.worktree_root)
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

    # Second line of defense (#1984): the provider reconcile above can still
    # miss an externally-merged PR when its state query *races* the merge (the
    # PR is merged a beat later) or the provider is briefly unreachable -- the
    # record is then left stale at 'open'. Reusing that PR's feature branch
    # would force-push (with lease) onto a ref the host DELETED on merge, which
    # the lease check rejects, wedging tracking at 'creating' with no PR opened
    # (a regression/uncovered variant of #1163 / #1336). So when we would
    # otherwise reuse a "live" PR's branch, verify that branch still exists on
    # the remote: one that is *confirmed gone* (remote reachable, ref absent)
    # means the PR merged and its branch was auto-pruned -- mark it terminal so
    # the fresh-branch-from-title path is taken instead. Only "absent" is
    # authoritative; an unreachable remote ("unknown") keeps the prior
    # (reuse) behavior. Looping lets a stack of stale merged+pruned PRs all
    # reconcile down to the genuinely-live (or no) active PR.
    if not new and not branch and git_ops.has_remote(remote, cwd=worktree_path):
        while active_is_live and active is not None and active.branch:
            if git_ops.remote_branch_state(
                remote, active.branch, cwd=worktree_path
            ) != "absent":
                break
            active.state = "merged"
            if not active.closed_at:
                active.closed_at = tracking._now_iso()
            if record is not None:
                tracking.save_record(record)
            active = record.active_pr() if record else None
            active_is_live = active is not None and not tracking._pr_is_terminal(active)

    # Resolve the feature branch name: explicit > live active PR > derived.
    if branch:
        feature_branch = branch
    elif active_is_live and not new and active.branch:
        feature_branch = active.branch
    else:
        feature_branch = pr_head_name(
            prcfg, eff_title, worktree_id,
            cwd=worktree_path, machine=config.machine,
        )

    if dry_run:
        return {
            **base, "success": True, "dry_run": True,
            "branch": feature_branch, "remote": remote,
            "provider": prcfg.provider, "default_branch": repo.default_branch,
            "draft": want_draft,
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
            open_pr=open_pr, draft=want_draft, attribution=attribution,
        )

    if head_branch != wt_branch:
        return {**base, "error": (
            f"Worktree HEAD is on '{head_branch}', expected '{wt_branch}'. "
            f"Checkout '{wt_branch}' before create-pr."
        )}

    reusing = bool(active_is_live and not new and active and active.branch == feature_branch)
    ahead = git_ops.get_commits_ahead(wt_branch, upstream, cwd=worktree_path)

    # --- Re-run fast path: a live PR whose head is already published and whose
    #     base has nothing new to squash. This is hit by (a) a legacy/migration
    #     worktree created under the old scheme that DID reset worktree/<id> to
    #     upstream (so it now sits at the tip, `not ahead`), and (b) any repo
    #     where the merged content has already synced back. In both cases the
    #     squashed work already lives on the (still-local) feature branch, so
    #     re-push that branch instead of tripping the "already exists" guard or
    #     the "nothing ahead" error below. Under the current scheme a *successful*
    #     create-pr leaves worktree/<id> ONE ahead (the squashed commit is kept
    #     in place, never reset), so a normal iterate/retry has `ahead` non-empty
    #     and falls through to re-squash + force-push onto the reused branch. ---
    if reusing and not ahead and git_ops.local_branch_exists(
        feature_branch, cwd=worktree_path
    ):
        return _push_existing_feature(
            worktree_path, feature_branch, remote, repo, prcfg, record, base,
            config=config, worktree_id=worktree_id, title=eff_title, body=body,
            open_pr=open_pr, draft=want_draft, attribution=attribution,
        )

    if not reusing:
        if git_ops.local_branch_exists(feature_branch, cwd=worktree_path) or \
                git_ops.remote_branch_exists(remote, feature_branch, cwd=worktree_path):
            return {**base, "error": (
                f"Feature branch '{feature_branch}' already exists locally or on "
                f"'{remote}'. Pass --branch to choose a different name."
            )}

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

    # Effective per-invocation head scheme. In a refspec repo, a *parallel* PR
    # (--new while another PR is still live) cannot use worktree/<id> as its
    # head -- that branch is the live refspec head of the other PR -- so it
    # falls back to snapshotting onto a separate feature branch, WITHOUT
    # resetting worktree/<id> (#1815 Phase 3). The single-PR serial flow (no
    # parallel) stays pure refspec.
    parallel_snapshot = bool(
        prcfg.head_scheme == "refspec" and new and active_is_live
    )
    use_refspec = prcfg.head_scheme == "refspec" and not parallel_snapshot

    if use_refspec:
        # Refspec mode (#1815): keep the squashed work ON worktree/<id> and push
        # it directly to the PR head ref. No local feature branch, no checkout
        # dance; HEAD never leaves wt_branch, and wt_branch is NOT reset to
        # upstream -- it legitimately sits ahead of master while the PR is open
        # (a later `git sync` fast-forwards it clean on merge).
        with hooks.allow_pr_push():
            pushed = git_ops.push(
                remote, f"{wt_branch}:refs/heads/{feature_branch}",
                cwd=worktree_path, force_with_lease=reusing,
            )
        if not pushed:
            return {**base, "error": (
                f"Failed to push '{wt_branch}' to '{remote}/{feature_branch}'. "
                f"The squashed work is on '{wt_branch}'; tracking state left as "
                f"'creating' for retry (re-run create-pr)."
            )}
    else:
        # Snapshot publish: the local worktree lands on the squashed commit
        # exactly as in refspec mode -- HEAD never leaves worktree/<id> and
        # worktree/<id> is NOT reset to upstream (it legitimately sits ahead of
        # master while the PR is open; a later `git sync` reconciles it on
        # merge, see finalize._reconcile_merged_pointers). The ONLY thing the
        # scheme changes is HOW the PR head is *published*: snapshot copies the
        # squashed commit onto a separately-named local ``feature/<slug>`` branch
        # (the older namespace) and pushes THAT, whereas refspec pushes
        # worktree/<id> straight to ``pr/<slug>`` via a refspec. "Land on the
        # squashed commit" is universal (#1804); ``head_scheme`` only selects the
        # PR-head NAME + push mechanism, never whether the worktree is reset.
        #
        # This path also serves a refspec repo's parallel --new PR
        # (``parallel_snapshot``): its head cannot be worktree/<id> (that is the
        # first PR's live refspec head), so it snapshots onto its own feature
        # branch -- and, crucially, still leaves worktree/<id> and HEAD alone.
        #
        # No checkout dance: `git push` publishes the named local ref while HEAD
        # stays on worktree/<id>.
        git_ops.git("branch", "-f", feature_branch, "HEAD", cwd=worktree_path, check=False)
        with hooks.allow_pr_push():
            pushed = git_ops.push(
                remote, feature_branch, cwd=worktree_path, force_with_lease=reusing
            )
        if not pushed:
            return {**base, "error": (
                f"Failed to push '{feature_branch}' to '{remote}'. The squashed "
                f"work is on '{wt_branch}' (and the local '{feature_branch}' "
                f"snapshot); tracking state left as 'creating' for retry "
                f"(re-run create-pr)."
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
        "draft": want_draft,
    }
    if reusing:
        # This call iterated an existing *live* PR (re-squash + force-push onto
        # the reused head) rather than opening a fresh one -- flag it so callers
        # recognize the idempotent re-run and don't treat it as a new PR. Mirrors
        # the fast-path re-run signal in ``_push_existing_feature``.
        result["rerun"] = True

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
        draft=want_draft, attribution=attribution,
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
    draft: bool = False,
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
    if draft:
        scope.draft = True
    try:
        provider = providers.get_provider(prcfg.provider)
        token = providers.account_token_for_slug(scope.repo, prcfg)
        pull = provider.create_pull(scope, token=token)
    except (providers.ProviderError, OSError) as e:
        # A provider failure (or a spawn error that slipped past run_cli) must
        # degrade to a recorded pr_open_error, never crash create-pr -- the
        # feature branch is already pushed, so the agent can open the PR
        # manually from the surfaced error.
        result["pr_open_error"] = str(e)
        result["pr_opened"] = False
        result["draft"] = False  # nothing opened -> no draft was created
        return

    target_pr.url = pull.url
    target_pr.number = pull.number
    if pull.state:
        target_pr.state = pull.state
    if record is not None:
        # #1029 backfill: if the worktree never recorded an originating session
        # (e.g. created before this field existed), stamp the session that
        # produced this PR -- but never clobber an explicit one.
        if not record.parent_session and session:
            record.parent_session = session
        tracking.save_record(record)
    result["pr_opened"] = True
    result["url"] = pull.url
    result["number"] = pull.number
    result["state"] = pull.state or result.get("state")
    # Reflect what THIS call actually did: a draft was created only when we asked
    # the provider to open one. (The caller pre-seeds result["draft"] with the
    # request intent for the dry-run/no-open paths; here we make it authoritative
    # for the opened PR so "opened as a DRAFT" can never be reported falsely.)
    result["draft"] = bool(draft)
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
    draft: bool,
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
            head_sha, draft=draft, attribution=attribution,
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
    # This call did not open a PR, so it created no draft -- never let a
    # re-run's ``--draft`` request masquerade as "opened as a DRAFT". Un-drafting
    # an already-open PR is pr-ready's job, not create-pr's.
    result["draft"] = False


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
        token = providers.account_token_for_slug(target_repo, prcfg)
        pull = provider.get_pull(
            target_repo, active.number,
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    except Exception:
        # Provider unconfigured/unreachable -- keep the local state rather than
        # guessing.  (Conservative: an unverifiable open PR is still iterated.)
        return
    state = (pull.state or "").strip().lower()
    # ``merged`` is authoritative: a squash-merged PR reports state="closed" on
    # some providers, so prefer it -- the record should say the work actually
    # *landed* (not merely "closed"), which is what drives the post-merge
    # pull-forward recommendation in :func:`pr_status`.
    if pull.merged:
        resolved = "merged"
    elif state and state not in tracking._PR_NON_TERMINAL:
        resolved = state
    else:
        resolved = ""
    if resolved:
        active.state = resolved
        if not active.closed_at:
            active.closed_at = tracking._now_iso()
        tracking.save_record(record)


def _live_pr_state(
    record: tracking.WorktreeRecord | None,
    active: PRRecord | None,
    config: Config,
) -> dict | None:
    """Best-effort live verdict/conflict/merge read for the active PR.

    Folds ``pr-watch``'s snapshot + the shared classifier into ``pr-status`` so
    one command answers "where is my PR?" -- the review **verdict**, whether it
    has a **conflict**, its **merge state**, and whether merge **consent** is
    present/eligible -- alongside the tracked metadata.  Returns a ``{"live":
    {...}}`` dict, or ``None`` when there is no numbered active PR or the
    provider is unconfigured/unreachable (never fatal: pr-status still reports
    the tracked state).
    """
    if active is None or active.number is None:
        return None
    prcfg = config.default_repo.pr
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or ((record.repo if record else "") or "")
    try:
        from . import pr_contract as pc
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, prcfg)
        snap = provider.get_snapshot(
            target_repo, active.number,
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
    except Exception:
        # Provider unconfigured/unreachable/unsupported -- omit the live block
        # rather than guessing; the tracked state is still reported.
        return None
    st = pc.classify_state(
        snap,
        automerge_label=getattr(prcfg, "automerge_label", "") or "",
        hold_labels=tuple(getattr(prcfg, "hold_labels", ()) or ()),
        wip_title_prefixes=tuple(getattr(prcfg, "wip_title_prefixes", ()) or ()),
        approval_required=bool(getattr(prcfg, "approval_required", True)),
    )
    return {
        "live": {
            "verdict": st.verdict,
            "merge_state": st.merge_state,
            "conflict": st.conflict,
            "mergeable": snap.mergeable,
            "consent_present": st.consent_present,
            "consent_action": st.consent_action,
            "eligible": st.eligible,
            "held": list(st.held),
            "wip": st.wip,
            "reviews": len(snap.reviews),
            "reason": st.reason,
        }
    }


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


def pr_ready(
    worktree_id: str,
    config: Config,
    *,
    target_repo: str | None = None,
    pr_number: int | None = None,
) -> dict:
    """Move a PR out of draft (draft -> ready-for-review).

    ``pr-ready`` is an **un-draft** verb: it clears the native draft state (a
    WIP-prefixed title on Gitea) so the PR becomes reviewable.  It does NOT grant
    merge consent -- that is ``pr-merge``'s separate job.

    Errors (``success: False``) when the action does not apply to the PR's
    current state, so a no-op never masquerades as success:

    * the PR is not in draft (and carries no legacy hold label) -> error;
    * the un-draft provider call fails -> error.

    Backward-compat: a PR opened under the retired ``--hold`` model carries the
    legacy ``do-not-merge`` label instead of draft state.  If such a PR is not a
    draft but does carry that hold label, it is removed (the equivalent
    transition) and reported as a legacy-hold release.
    """
    base: dict = {"success": False, "worktree_id": worktree_id}
    record = _load_record_or_none(worktree_id)
    if record is None:
        return {**base, "error": f"No tracking record found for '{worktree_id}'."}

    _reconcile_active_pr(record, config)
    if pr_number is not None:
        pr = next((p for p in record.prs if p.number == pr_number), None)
        if pr is None:
            return {**base, "error": (
                f"No tracked PR #{pr_number} for '{worktree_id}'."
            )}
    else:
        pr = record.active_pr()
        if pr is None:
            return {**base, "error": f"No tracked PR for '{worktree_id}'."}

    if pr.number is None:
        return {**base, "error": (
            f"Tracked PR for '{worktree_id}' has no PR number recorded."
        )}

    prcfg = config.default_repo.pr
    provider_name = pr.provider or prcfg.provider
    repo = target_repo or pr.repo or record.repo or ""
    if not repo:
        return {**base, "error": (
            f"Tracked PR #{pr.number} for '{worktree_id}' has no target repo."
        )}

    api_base = getattr(prcfg, "api_base", "") or ""
    wip_prefixes = tuple(getattr(prcfg, "wip_title_prefixes", ()) or ())

    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(repo, prcfg)
        snap = provider.get_snapshot(
            repo, pr.number, api_base=api_base, token=token,
        )
    except Exception as exc:
        return {**base, **_pr_to_dict(pr), "repo": repo,
                "provider": provider_name, "error": str(exc)}

    common = {
        **base, **_pr_to_dict(pr), "repo": repo, "provider": provider_name,
    }

    if snap.draft:
        # The intended transition: strip the WIP prefix (un-draft).
        try:
            err = provider.mark_ready(
                repo, pr.number, api_base=api_base, token=token,
                title=snap.title, wip_title_prefixes=wip_prefixes,
            )
        except Exception as exc:
            return {**common, "error": str(exc)}
        if err:
            return {**common, "error": err}
        return {
            **common, "success": True, "transition": "undraft",
            "was_draft": True,
        }

    # Not a draft. Backward-compat: a PR opened under the retired --hold model
    # carries the legacy do-not-merge hold label; releasing it is the equivalent
    # transition. Otherwise this verb does not apply -> error (no false success).
    has_legacy_hold = any(
        lbl.lower() == HOLD_LABEL for lbl in snap.labels
    )
    if has_legacy_hold:
        try:
            label_error = provider.remove_label(
                repo, pr.number, HOLD_LABEL, api_base=api_base, token=token,
            )
        except Exception as exc:
            return {**common, "error": str(exc)}
        if label_error:
            return {**common, "error": label_error, "label_error": label_error}
        return {
            **common, "success": True, "transition": "release-legacy-hold",
            "removed": True, "label": HOLD_LABEL,
        }

    return {
        **common,
        "error": (
            f"PR #{pr.number} in {repo} is not in draft state (and carries no "
            f"legacy hold label); nothing to un-draft. pr-ready only moves a "
            f"draft PR to ready-for-review -- it does not grant merge consent "
            f"(use pr-merge for that)."
        ),
    }


def pr_status(worktree_id: str, *, all_prs: bool = False,
              live: bool = True, config: Config | None = None) -> dict:
    """Return the tracked PR metadata for a worktree (for pr-status).

    Returns the **active** PR by default.  With ``all_prs`` the full ``prs``
    history is included alongside the active one.  ``pr_count`` is always
    present so the orphan-detection probe can key on existence.

    Before reporting, the active PR is reconciled against the provider so a PR
    merged or closed *externally* (e.g. via the ``auto-merge`` label, bypassing
    ``finalize``/``pr-watch``) is reported with its true terminal state rather
    than a stale ``open``.  This is the agent's authoritative "did my PR land?"
    check; when it lands, the result also carries a **pull-forward
    recommendation** (``pull_forward_recommended`` + ``next_action``) directing
    the standard post-merge move -- ``agent-worktrees git sync`` to rebase the
    worktree onto the updated default branch.

    With ``live`` (default), a best-effort ``live`` block is added for the
    active PR carrying its review **verdict**, **conflict**, **merge state**, and
    merge-**consent** eligibility (from the shared ``pr_contract`` classifier),
    so one command answers "where is my PR?".  The block is omitted silently when
    the provider is unconfigured/unreachable.
    """
    base: dict = {"worktree_id": worktree_id}
    record = _load_record_or_none(worktree_id)
    if record is None:
        return {**base, "has_pr": False, "pr_count": 0,
                "error": f"No tracking record found for '{worktree_id}'."}
    if config is None:
        config = cfg.load_config()
    _reconcile_active_pr(record, config)
    active = record.active_pr()
    result = {**base, "has_pr": active is not None, "pr_count": len(record.prs)}
    if active is not None:
        result.update(_pr_to_dict(active))
        rec = _pull_forward_recommendation(record, active, config)
        if rec:
            result.update(rec)
        if live:
            live_block = _live_pr_state(record, active, config)
            if live_block:
                result.update(live_block)
    if all_prs:
        result["prs"] = [_pr_to_dict(p) for p in record.prs]
    return result


def pr_threads(
    worktree_id: str,
    *,
    resolve: bool = False,
    config: Config | None = None,
) -> dict:
    """Read (and optionally resolve) the active PR's review comment threads.

    First-class comment-threading tied to the worktree flow: resolves the
    active PR's provider/repo, lists its threads via ``get_comment_threads``,
    and -- with ``resolve`` -- marks the active (unresolved) ones resolved
    (``resolve_threads``). Returns ``{has_pr, threads: [...], active_count, ...}``
    (never fatal: an unsupported/unreachable provider yields ``supported:
    False`` with a ``reason``).
    """
    base: dict = {"worktree_id": worktree_id}
    record = _load_record_or_none(worktree_id)
    if record is None:
        return {**base, "has_pr": False,
                "error": f"No tracking record found for '{worktree_id}'."}
    if config is None:
        config = cfg.load_config()
    active = record.active_pr()
    if active is None or active.number is None:
        return {**base, "has_pr": False, "threads": [], "active_count": 0}
    prcfg = config.default_repo.pr
    provider_name = active.provider or prcfg.provider
    target_repo = active.repo or (record.repo or "")
    api_base = getattr(prcfg, "api_base", "") or ""
    out: dict = {**base, "has_pr": True, "number": active.number, "repo": target_repo}
    try:
        from . import providers

        provider = providers.get_provider(provider_name)
        token = providers.account_token_for_slug(target_repo, prcfg)
        listing = provider.get_comment_threads(
            target_repo, active.number, api_base=api_base, token=token,
        )
    except Exception as exc:
        return {**out, "supported": False, "reason": str(exc), "threads": [],
                "active_count": 0}
    out["supported"] = listing.supported
    if not listing.supported:
        out["reason"] = listing.error
        out["threads"] = []
        out["active_count"] = 0
        return out
    if listing.error:
        out["reason"] = listing.error
    out["threads"] = [
        {
            "id": t.id, "status": t.status, "file_path": t.file_path,
            "active": t.is_active,
            "comments": [{"author": c.author, "content": c.content}
                         for c in t.comments],
        }
        for t in listing.threads
    ]
    out["active_count"] = len(listing.active)
    if resolve and listing.active:
        try:
            err = provider.resolve_threads(
                target_repo, active.number, api_base=api_base, token=token,
            )
        except Exception as exc:
            err = str(exc)
        out["resolved"] = not err
        if err:
            out["resolve_error"] = err
    return out


def _pull_forward_recommendation(
    record: tracking.WorktreeRecord,
    active: PRRecord,
    config: Config,
) -> dict | None:
    """Recommend the post-merge pull-forward when the active PR has merged.

    Returns recommendation fields, or ``None`` when no nudge is warranted.
    Fires only when the active PR is **merged** and the worktree branch is not
    already rebased on top of the updated default branch -- i.e. there is real
    pull-forward work to do.  Best-effort and side-effect-free (a single
    upstream fetch aside): any git hiccup falls back to recommending, since the
    agent's ``git sync`` is a safe no-op when already current.
    """
    if active.state != "merged":
        return None
    path = record.worktree_path
    if not (path and Path(path).exists()):
        return None
    repo = config.default_repo
    remote = repo.remote
    upstream = f"{remote}/{repo.default_branch}"
    # Refresh the upstream ref so "behind" reflects the just-landed merge.
    if git_ops.has_remote(remote, cwd=path):
        try:
            git_ops.fetch(remote, cwd=path)
        except Exception:
            pass
    behind: int | None = None
    branch = git_ops._get_current_branch_safe(path)
    if branch and git_ops.ref_exists(upstream, cwd=path):
        out = git_ops.git(
            "rev-list", "--count", f"{branch}..{upstream}",
            cwd=path, check=False,
        ).stdout.strip()
        try:
            behind = int(out)
        except ValueError:
            behind = None
    # Already on top of the updated default branch -- nothing to pull forward.
    if behind == 0:
        return None
    rec: dict = {
        "pull_forward_recommended": True,
        "pull_forward_command": "agent-worktrees git sync",
    }
    if behind:
        rec["behind"] = behind
    if not git_ops.is_clean(cwd=path):
        rec["pull_forward_blocked"] = "dirty"
        rec["next_action"] = (
            f"Active PR #{active.number} is merged, but this worktree has "
            "uncommitted changes. Commit or stash them, then run "
            f"`agent-worktrees git sync` to pull forward (rebase onto {upstream})."
        )
    else:
        rec["next_action"] = (
            f"Active PR #{active.number} is merged. Pull this worktree forward: "
            f"`agent-worktrees git sync` (rebase onto {upstream}; the merged "
            "commits drop as already-applied)."
        )
    return rec


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
    draft: bool,
    attribution: bool,
) -> dict:
    """Re-run helper: push an already-created feature branch and record state.

    Also completes auto-open: if the matched PR has not been opened yet it is
    opened now; if it is already open its number/url is surfaced.  This keeps a
    re-run from leaving a pushed branch with no reported PR -- which otherwise
    leads the agent to open a duplicate (#1167).
    """
    # Resolve the head from the feature branch ref, not HEAD: a #1804 re-run
    # from the worktree base branch leaves HEAD off the feature branch, so
    # reading HEAD would record the wrong commit. Invoked from the legacy
    # on-feature-branch path these are identical.
    head_sha = _rev(feature_branch, cwd=worktree_path)
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
        "draft": draft,
    }
    _finish_auto_open(
        result, config, record, target, title=title, body=body,
        worktree_id=worktree_id, head_sha=head_sha, open_pr=open_pr,
        draft=draft, attribution=attribution,
    )
    return result
