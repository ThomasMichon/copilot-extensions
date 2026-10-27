"""Explicit, exact-attempt cancellation through the existing PR lifecycle fences."""

from __future__ import annotations

import getpass
import os

import yaml

from . import config as cfg, git_ops, pr_authority, pr_creation_state as state
from . import pr_publication_state, pr_publish, providers, tracking


def _result(base: dict, pr: tracking.PRRecord, *, already: bool) -> dict:
    return {
        **base, "success": True, "state": pr.state, "pr_id": pr.pr_id,
        "pr_revision": pr.pr_revision, "creation_cancelled": True,
        "already_cancelled": already, "provider_closed": False, "claim_released": False,
    }


def _absence(record, pr, config) -> None:
    from .git_push_transport import pinned_destination

    prcfg = config.default_repo.pr
    if pr.provider != prcfg.provider:
        raise ValueError("Creation provider changed; cancellation is not qualified.")
    provider = providers.get_provider(pr.provider)
    endpoint = provider.authority_endpoint(prcfg.api_base)
    if endpoint != pr.creation["api_base"]:
        raise ValueError("Creation provider endpoint changed; cancellation is not qualified.")
    # This first contract admits only same-repository heads. A fork needs its own
    # authoritative cross-repository search, not a best-effort head lookup.
    if pr.creation["identity"].lower() != f"{endpoint}/{pr.repo}".lower():
        raise ValueError("Creation destination is not the provider's own repository; cancellation is unsupported.")
    confirm_absence = getattr(provider, "confirm_no_pull_by_head_v1", None)
    if not callable(confirm_absence):
        raise ValueError("Provider cannot authoritatively confirm PR-head absence for this cancellation contract.")
    remote = pr.creation["remote"]
    cwd = record.worktree_path
    with pr_publish.publish_lock(cwd):
        fetch, urls, destination = pr_publish._remote_snapshot(remote, cwd=cwd)
        if destination.head_identity != pr.creation["identity"]:
            raise ValueError("Creation remote destination changed or is unreadable; cancellation refused.")
        destinations = tuple(urls.stdout.splitlines())
        settings = pr_publish._transport_settings(remote, cwd=cwd)
    local = git_ops.git(
        "show-ref", "--verify", "--quiet", f"refs/heads/{pr.branch}",
        cwd=cwd, check=False, isolated_repository=True,
    )
    if local.returncode != 1:
        raise ValueError("Local PR-head absence is not confirmed; cancellation refused.")
    with pinned_destination(remote, destinations, settings, fetch_url=fetch.stdout.rstrip("\r\n")):
        tip = pr_publish._tip(remote, pr.branch, cwd)
    if tip is None:
        raise ValueError("Cannot confirm remote head absence; cancellation refused.")
    if tip:
        raise ValueError("The remote PR head exists; cancellation refused.")
    token = providers.account_token_for_slug(pr.repo, prcfg)
    if confirm_absence(pr.repo, pr.branch, api_base=prcfg.api_base, token=token) is not True:
        raise ValueError("Provider PR-head absence is not confirmed; cancellation refused.")


def _settle(base, path, record, pr, expected_revision, reason) -> dict:
    with tracking._RecordLock(path, require_sidecar=True):
        fresh = tracking.load_record(path)
        current = pr_publish._publication_pr(fresh, pr)
        if (
            state.owner(fresh) != state.owner(record)
            or not pr_publication_state.matches(current, pr)
            or current.pr_revision != expected_revision
            or not state.reserved(current, fresh)
        ):
            raise ValueError("Creation authority changed during observation; cancellation refused.")
        at = tracking._now_iso()
        actor = os.environ.get("COPILOT_AGENT_SESSION_ID") or f"user:{getpass.getuser()}"
        current.creation = {
            **current.creation, "phase": "cancelled", "reason": reason.strip(),
            "at": at, "actor": actor, "revision": str(expected_revision),
        }
        current.state, current.closed_at = "abandoned", at
        current.pr_revision += 1
        tracking.save_record(fresh)
        return _result(base, current, already=False)


def cancel_creation(worktree_id, config, *, creation_id, expected_revision, reason, confirm) -> dict:
    """Cancel only an owner-stamped unpublished reservation; never close a PR."""
    from .pr_abandon_ops import _ABANDON_WARNING

    base = {"success": False, "worktree_id": worktree_id, "contract": state.CONTRACT}
    if not (reason or "").strip():
        return {**base, "error": "pr-abandon: --reason is required for local creation cancellation."}
    if not confirm:
        return {**base, "error": _ABANDON_WARNING + (
            "\nThis mode cancels only one local creation reservation, not a provider PR. "
            "After exact operator approval, also pass its --expected-revision from "
            "pr-status --all --no-live --json."
        ), "needs_confirm": True}
    if not creation_id or type(expected_revision) is not int or expected_revision < 0:
        return {**base, "error": "Local cancellation requires an exact --creation ID and --expected-revision."}
    path = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    try:
        with pr_authority.guard(), pr_publish.metadata_lock(worktree_id, project=config.repo_name):
            record = tracking.load_record(path)
            if record.worktree_id != worktree_id or record.repo != config.repo_name:
                raise ValueError("Owning worktree identity differs from the selected project.")
            candidates = [p for p in record.prs if p.pr_id == creation_id]
            if len(candidates) != 1:
                raise ValueError("The exact local creation ID is missing or ambiguous; reload its status.")
            pr = candidates[0]
            if state.cancelled(pr):
                if (
                    pr.creation["owner"] != state.owner(record)
                    or pr.creation["revision"] != str(expected_revision)
                    or pr.creation["reason"] != reason.strip()
                ):
                    raise ValueError("Cancellation receipt differs from this confirmation; reload its status.")
                return _result(base, pr, already=True)
            if pr.pr_revision != expected_revision:
                raise ValueError("Creation revision changed; obtain confirmation for its current revision.")
            if not state.reserved(pr, record):
                raise ValueError(
                    "No qualified unpublished reservation: legacy, changed, published or uncertain "
                    "attempts must be reconciled or retained, not relabeled never-published."
                )
            _absence(record, pr, config)
            with pr_publish.publish_lock(record.worktree_path):
                if pr_publish.push_identity(pr.creation["remote"], cwd=record.worktree_path) != pr.creation["identity"]:
                    raise ValueError("Creation destination changed during observation; cancellation refused.")
                return _settle(base, path, record, pr, expected_revision, reason)
    except (OSError, ValueError, TimeoutError, yaml.YAMLError, providers.ProviderError, git_ops.GitError) as exc:
        detail = exc.stderr if isinstance(exc, git_ops.GitError) else str(exc)
        return {**base, "error": f"Local creation cancellation refused: {detail}"}
