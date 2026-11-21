"""Owner-confirmed retirement of an ambiguous legacy publication attempt.

The original publication evidence remains untouched. Retirement says nothing
about the outcome of its transport, and never creates a provider association.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass, replace
from contextlib import ExitStack
from pathlib import Path
from subprocess import CompletedProcess
import subprocess
from typing import Any

import yaml

from . import config as cfg, git_ops, pr_authority, pr_creation_state as state
from . import execution_spaces, pr_legacy_admission, pr_publication_state, pr_publish, pr_transport_fence, providers, tracking, worktree_lifecycle

CONTRACT = "agent-worktrees.pr-creation-reconciliation.v1"
_KEYS = {
    "recovery_contract", "recovery_reason", "recovery_actor", "recovery_at",
    "recovery_revision", "recovery_head", "recovery_upstream", "recovery_merged_pr",
    "recovery_branch",
}
_OWNER_STATUSES = frozenset({"active", "complete", "completed", "pushed"})


def _original(pr: tracking.PRRecord) -> tracking.PRRecord:
    return replace(
        pr, state="creating", closed_at="", branch=pr.creation.get("recovery_branch", pr.branch),
        creation={k: v for k, v in pr.creation.items() if k not in _KEYS},
    )


def _legacy(pr: tracking.PRRecord, record: tracking.WorktreeRecord | None = None) -> bool:
    return bool(
        set(pr.creation) == state._RESERVATION_KEYS
        and state._bound(pr) and pr.creation["phase"] == "publishing"
        and pr_publication_state.unpublished(pr)
        and not pr.closed_at
        and (record is None or pr.creation["owner"] == state.owner(record))
    )


def retired(pr: tracking.PRRecord) -> bool:
    """Recognize only a complete local receipt, never a provider merge."""
    if not getattr(pr, "creation", None) or not (
        set(pr.creation) == state._RESERVATION_KEYS | _KEYS
        and pr.creation.get("recovery_contract") == CONTRACT
        and pr.state == "creating" and not pr.branch and not pr.closed_at
        and _legacy(_original(pr))
        and all(pr.creation[k].strip() for k in _KEYS - {"recovery_merged_pr"})
    ):
        return False
    head = pr.creation["recovery_head"]
    if len(head) not in (40, 64) or any(c not in "0123456789abcdef" for c in head):
        return False
    revision = pr.creation["recovery_revision"]
    return revision.isascii() and revision.isdecimal() and int(revision) + 1 == pr.pr_revision


def requires_qualification(pr: tracking.PRRecord) -> bool:
    """Missing/partial proof must not turn a local retirement into a bypass."""
    return bool(
        any(k.startswith("recovery_") for k in getattr(pr, "creation", {}))
        or (
            getattr(pr, "state", "") == "creating" and not getattr(pr, "branch", "")
            and getattr(pr, "number", None) is None
        )
    )


def source_branch(pr: tracking.PRRecord) -> str:
    return _original(pr).branch if retired(pr) else pr.branch


def qualified(record: tracking.WorktreeRecord, pr: tracking.PRRecord) -> bool:
    """Only the current operation's held cleanup fences can settle a receipt."""
    scope = pr_transport_fence.current.get()
    if scope is None or str(record.yaml_path.resolve()) not in scope.protected or not retired(pr):
        return False
    proof = scope.proofs.get(str(record.yaml_path.resolve()))
    return bool(
        isinstance(proof, _Proof) and proof.owner == state.owner(record)
        and proof.authority == pr_authority._projection(record)
        and pr.pr_id in proof.observations
    )


def publication_error(record: tracking.WorktreeRecord | None, *, new: bool, branch: str = "") -> str | None:
    if record is None:
        return None
    held = [pr for pr in record.prs if requires_qualification(pr)]
    if any(not retired(pr) for pr in held):
        return "A damaged local retirement must be qualified before publishing new work."
    if branch and any(source_branch(pr) == branch for pr in held):
        return "A retired attempt's original head cannot be replayed; choose a distinct new head."
    active = record.active_pr()
    if not new and active is not None and retired(active):
        return "The selected attempt is locally retired; use explicit --new with a distinct head for new work."
    return None


def _git(cwd: str, *args: str, check: bool = True) -> CompletedProcess[str]:
    """Observe without optional index writes, monitor activation or replacement objects."""
    return git_ops.git(
        "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false", *args,
        cwd=cwd, check=check, isolated_repository=True,
        timeout=30, kill_tree=True,
    )


def _source(record: tracking.WorktreeRecord, repo: Any, *, expected_head: str = "") -> str:
    cwd = record.worktree_path
    root = Path(cwd).resolve()
    absent = not root.exists()
    if absent:
        if expected_head or record.status not in ("finalized", "archived"):
            raise ValueError("A missing non-finalized source cannot be qualified.")
        cwd = repo.anchor
        root = Path(cwd).resolve()
    anchor_root = Path(repo.anchor).resolve()
    anchor_prefix = () if (anchor_root / ".git").exists() else ("--git-dir", str(anchor_root))
    prefix = anchor_prefix if absent else ()
    if absent:
        layout = _git(
            cwd, *prefix, "rev-parse", "--path-format=absolute",
            "--absolute-git-dir", "--git-common-dir", "--is-bare-repository",
        ).stdout.splitlines()
        if len(layout) != 3 or layout[2] not in ("true", "false"):
            raise ValueError("The configured anchor has invalid Git layout.")
        toplevel = layout[0] if layout[2] == "true" else _git(cwd, "rev-parse", "--show-toplevel").stdout.strip()
        common, head = layout[1], ""
    else:
        layout = _git(
            cwd, "rev-parse", "--path-format=absolute", "--show-toplevel",
            "--git-common-dir", "--verify", "HEAD^{commit}",
        ).stdout.splitlines()
        if len(layout) != 3:
            raise ValueError("The owning checkout has invalid Git layout.")
        toplevel, common, head = layout
    if Path(toplevel).resolve() != root:
        raise ValueError("The owning checkout is not its recorded Git root.")
    anchor_common = _git(repo.anchor, *anchor_prefix, "rev-parse", "--git-common-dir").stdout.strip()
    if Path(root, common).resolve() != Path(repo.anchor, anchor_common).resolve():
        raise ValueError("The owning checkout is unrelated to the configured anchor.")
    if not absent:
        if _git(cwd, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none").stdout.strip():
            raise ValueError("Dirty source is not eligible; retain the attempt.")
        branch = _git(cwd, "symbolic-ref", "--short", "HEAD").stdout.strip()
        if branch != record.branch:
            raise ValueError("The owning checkout is not on its recorded branch.")
    if expected_head and head != expected_head:
        raise ValueError("Source HEAD changed from the explicitly confirmed --expected-head.")
    upstream = f"refs/remotes/{repo.remote}/{repo.default_branch}"
    refs = {f"refs/heads/{record.branch}"} | {
        f"refs/heads/{source_branch(p)}" for p in record.prs if p.branch or retired(p)
    }
    existing = set(_git(cwd, *prefix, "for-each-ref", "--format=%(refname)", "refs/heads/").stdout.splitlines())
    if not absent and f"refs/heads/{record.branch}" not in existing:
        raise ValueError("The owning source branch is missing.")
    sources = refs & existing
    for pr in record.prs:
        if requires_qualification(pr):
            if not retired(pr):
                raise ValueError("The source has invalid retirement proof.")
            sources.add(pr.creation["recovery_head"])
    landed = _git(cwd, *prefix, "rev-list", "--max-count=1", *sorted(sources), "--not", upstream, check=False)
    if landed.returncode or landed.stdout.strip():
        raise ValueError(
            "Every owned local source/PR ref must be reachable from upstream. "
            "Preserve useful history and obtain explicit authority before reconciling "
            "a superseded ref; tree equality is not semantic-source proof."
        )
    return head


def finalization_error(record: tracking.WorktreeRecord, repo: Any) -> str | None:
    """A retained local disposition never waives later source or owner drift."""
    selected = [
        p for p in (getattr(record, "prs", None) or [])
        if requires_qualification(p)
    ]
    if not selected:
        return None
    try:
        scope = pr_transport_fence.current.get()
        key = str(record.yaml_path.resolve())
        with worktree_lifecycle.guard(record.yaml_path, reentrant=True):
            for pr in selected:
                if not retired(pr) or pr.creation["owner"] != state.owner(record):
                    raise ValueError("Legacy creation retirement proof is invalid for this worktree.")
                if pr.creation["recovery_upstream"] != f"{repo.remote}/{repo.default_branch}":
                    raise ValueError("Legacy creation retirement upstream changed.")
            if scope is not None and scope.cleanup:
                cached = scope.proofs.get(key)
                if not isinstance(cached, _Proof):
                    raise ValueError("Retirement prequalification is missing; retry before cleanup.")
                _validate_proof(record, repo, cached)
            else:
                admission = pr_legacy_admission.require_quiescence()
                observations = {}
                for pr in selected:
                    merged = pr.creation["recovery_merged_pr"]
                    observations[pr.pr_id] = _observe(
                        record, _original(pr), repo, int(merged) if merged else None,
                        initialize_generation=scope is not None,
                    )
                proof = _Proof(pr_authority._projection(record), state.owner(record), observations, admission)
                heads = tuple({observed.head for observed in observations.values()})
                with ExitStack() as lifetime:
                    for head in sorted(heads, key=lambda h: h.directory):
                        lifetime.enter_context(head.guard())
                    cwd = record.worktree_path if Path(record.worktree_path).exists() else repo.anchor
                    with pr_publish.publish_lock(cwd, acquire_timeout=0):
                        _validate_proof(tracking.load_record(record.yaml_path), repo, proof)
                if scope is not None:
                    scope.proofs[key] = proof
                    scope.heads[key] = heads
    except (OSError, ValueError, TimeoutError, providers.ProviderError, git_ops.GitError, subprocess.SubprocessError) as exc:
        return f"Legacy creation retirement requires renewed qualification: {exc}"
    return None


@dataclass(frozen=True)
class _Destination:
    fetch_url: str
    push_urls: tuple[str, ...]
    identity: str
    source_identity: str
    settings: tuple[tuple[str, str], ...]
    cached_tip: str


@dataclass(frozen=True)
class _Observation:
    destination: _Destination
    head: pr_transport_fence.Head
    generation: str


@dataclass(frozen=True)
class _Proof:
    authority: tuple[object, ...]
    owner: str
    observations: dict[str, _Observation]
    admission: pr_legacy_admission.Seal


def _validate_proof(record: tracking.WorktreeRecord, repo: Any, proof: _Proof) -> None:
    pr_legacy_admission.require_quiescence(expected=proof.admission)
    if pr_authority._projection(record) != proof.authority or state.owner(record) != proof.owner:
        raise ValueError("Retirement authority changed during observation.")
    for pr in record.prs:
        if not requires_qualification(pr):
            continue
        _provider(_original(pr), repo)
        observed = proof.observations.get(pr.pr_id)
        if (
            observed is None or not retired(pr)
            or pr_transport_fence.generation(observed.head) != observed.generation
            or _destination(record, _original(pr), repo) != observed.destination
        ):
            raise ValueError("Publication intervened or retirement destination changed; renew qualification.")
    _source(record, repo)


def _destination(record: tracking.WorktreeRecord, pr: tracking.PRRecord, repo: Any) -> _Destination:
    remote, cwd = pr.creation["remote"], record.worktree_path
    if not Path(cwd).exists():
        cwd = repo.anchor
    fetch, urls, destination = pr_publish._remote_snapshot(remote, cwd=cwd)
    identity = destination.head_identity
    return _Destination(
        fetch.stdout.rstrip("\r\n"), tuple(urls.stdout.splitlines()), identity,
        identity if remote == repo.remote else pr_publish.push_identity(repo.remote, cwd=cwd),
        pr_publish._transport_settings(remote, cwd=cwd),
        _git(cwd, "rev-parse", "--verify",
             f"refs/remotes/{repo.remote}/{repo.default_branch}^{{commit}}").stdout.strip(),
    )


def _provider(pr: tracking.PRRecord, repo: Any) -> Any:
    if pr.provider != repo.pr.provider:
        raise ValueError("Publication provider changed.")
    provider = providers.get_provider(pr.provider)
    endpoint = provider.authority_endpoint(repo.pr.api_base)
    if endpoint != pr.creation["api_base"] or pr.creation["identity"].lower() != f"{endpoint}/{pr.repo}".lower():
        raise ValueError("Only the unchanged same-repository publication destination is supported.")
    return provider


def _observe(
    record: tracking.WorktreeRecord, pr: tracking.PRRecord, repo: Any, merged_pr: int | None,
    *, initialize_generation: bool = False,
) -> _Observation:
    from .git_push_transport import pinned_destination

    prcfg = repo.pr
    provider = _provider(pr, repo)
    confirm = getattr(provider, "confirm_reconciled_head_v1", None)
    if not callable(confirm):
        raise ValueError("Selected provider cannot qualify legacy-attempt reconciliation.")
    merged_head = ""
    if merged_pr is not None:
        matches = [
            p for p in record.prs if p.pr_id != pr.pr_id and p.number == merged_pr
            and p.branch == pr.branch and p.repo.casefold() == pr.repo.casefold() and p.provider == pr.provider
            and p.state == "merged" and p.head_sha and p.url
        ]
        if len(matches) != 1:
            raise ValueError("--merged-pr must select one existing, exact-head merged association.")
        merged_head = matches[0].head_sha
    siblings = [
        p for p in record.prs if p.pr_id != pr.pr_id and p.branch == pr.branch
        and not tracking._pr_is_terminal(p)
    ]
    if siblings:
        raise ValueError("Another live association owns this publication head.")
    remote, cwd = pr.creation["remote"], record.worktree_path
    if not Path(cwd).exists():
        cwd = repo.anchor
    head = pr_transport_fence.Head(pr.creation["identity"], pr.branch)
    with head.guard(), pr_authority.guard(timeout=0):
        generation = pr_transport_fence.generation(head, initialize=initialize_generation)
    with pr_publish.publish_lock(cwd):
        destination = _destination(record, pr, repo)
    if (
        destination.identity.casefold() != pr.creation["identity"].casefold()
        or destination.source_identity.casefold() != pr.creation["identity"].casefold()
    ):
        raise ValueError("Publication remote changed or is unreadable.")
    with pinned_destination(remote, destination.push_urls, destination.settings, fetch_url=destination.fetch_url):
        tip = pr_publish._tip(remote, pr.branch, cwd)
        upstream_tip = pr_publish._tip(remote, repo.default_branch, cwd)
    if tip is None or (tip and (not merged_head or tip != merged_head)):
        raise ValueError("Remote publication head is unknown or not the exact confirmed merged head.")
    if not upstream_tip or upstream_tip != destination.cached_tip:
        raise ValueError("Upstream observation is unavailable or stale; use normal sync before requalification.")
    token = providers.account_token_for_slug(pr.repo, prcfg)
    if confirm(
        pr.repo, pr.branch, number=merged_pr, head_sha=merged_head,
        api_base=prcfg.api_base, token=token,
    ) is not True:
        raise ValueError("All-state exact-head provider observation is incomplete, open or ambiguous.")
    return _Observation(destination, head, generation)


def reconcile(
    worktree_id: str, config: cfg.Config, *, creation_id: str,
    expected_revision: int | None, expected_head: str, merged_pr: int | None,
    reason: str, confirm: bool,
) -> dict[str, Any]:
    base: dict[str, Any] = {"success": False, "worktree_id": worktree_id, "contract": CONTRACT}
    if not reason.strip():
        return {**base, "error": "A specific --reason is required."}
    if not confirm:
        return {**base, "needs_confirm": True, "error": (
            "Retain or resume useful work first. This operation retires only an exact "
            "legacy local attempt with UNKNOWN publication outcome, not a provider PR. "
            "Obtain explicit approval for this attempt, source and revision before --confirm."
        )}
    if (
        not creation_id or type(expected_revision) is not int
        or not 0 <= expected_revision < tracking.MAX_PERSISTED_COUNTER or not expected_head
    ):
        return {**base, "error": "Exact creation ID, --expected-revision and full --expected-head are required."}
    repo = config.default_repo
    path = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    try:
        with worktree_lifecycle.guard(path):
            record = tracking.load_record(path)
            execution_spaces.require_record_mutation(record, config, recover_receiving=False)
            if record.worktree_id != worktree_id or record.repo != config.repo_name:
                raise ValueError("Owning worktree identity differs from the selected project.")
            if record.status not in _OWNER_STATUSES:
                raise ValueError("The owning worktree is not an eligible non-finalized owner.")
            matches = [p for p in record.prs if p.pr_id == creation_id]
            if len(matches) != 1:
                raise ValueError("Exact immutable creation ID is missing or ambiguous.")
            pr = matches[0]
            admission = pr_legacy_admission.require_quiescence()
            if retired(pr):
                if (
                    pr.creation["owner"] != state.owner(record)
                    or pr.creation["recovery_revision"] != str(expected_revision)
                    or pr.creation["recovery_head"] != expected_head
                    or pr.creation["recovery_reason"] != reason.strip()
                    or pr.creation["recovery_merged_pr"] != str(merged_pr or "")
                ):
                    raise ValueError("Existing receipt differs from this confirmation.")
                return _result(base, pr, already=True)
            if pr.pr_revision != expected_revision or not _legacy(pr, record):
                raise ValueError("Expected revision or owner-stamped legacy publication evidence differs; retain the attempt.")
            head = _source(record, repo, expected_head=expected_head)
            destination = _observe(record, pr, repo, merged_pr, initialize_generation=True)
            # The record writer acquires installation authority only for its
            # atomic association write, never for the source scan.
            with destination.head.guard(), pr_publish.publish_lock(record.worktree_path, acquire_timeout=0), \
                    pr_publish.metadata_lock(worktree_id, project=config.repo_name):
                _provider(pr, repo)
                if (
                    pr_transport_fence.generation(destination.head) != destination.generation
                    or _destination(record, pr, repo) != destination.destination
                ):
                    raise ValueError("Publication intervened or its destination changed during observation.")
                with tracking._RecordLock(path, require_sidecar=True):
                    fresh = tracking.load_record(path)
                    execution_spaces.require_record_mutation(fresh, config, recover_receiving=False)
                    current = pr_publish._publication_pr(fresh, pr)
                    if (
                        fresh.status not in _OWNER_STATUSES
                        or pr_authority._projection(fresh) != pr_authority._projection(record)
                        or state.owner(fresh) != state.owner(record) or current is None
                        or current.pr_revision != expected_revision
                        or not pr_publication_state.matches(current, pr)
                        or not _legacy(current, fresh)
                    ):
                        raise ValueError("Publication authority changed during observation.")
                    if _source(fresh, repo, expected_head=expected_head) != head:
                        raise ValueError("Source changed during observation.")
                    pr_legacy_admission.require_quiescence(expected=admission)
                    at = tracking._now_iso()
                    current.creation = {
                        **current.creation, "recovery_contract": CONTRACT,
                        "recovery_reason": reason.strip(), "recovery_at": at,
                        "recovery_actor": os.environ.get("COPILOT_AGENT_SESSION_ID") or f"user:{getpass.getuser()}",
                        "recovery_revision": str(expected_revision), "recovery_head": head,
                        "recovery_upstream": f"{repo.remote}/{repo.default_branch}",
                        "recovery_merged_pr": str(merged_pr or ""),
                        "recovery_branch": current.branch,
                    }
                    current.branch = ""
                    current.pr_revision += 1
                    tracking.save_record(fresh)
                    return _result(base, current, already=False)
    except (OSError, ValueError, TimeoutError, yaml.YAMLError, providers.ProviderError,
            git_ops.GitError, execution_spaces.ExecutionSpaceError, subprocess.SubprocessError) as exc:
        detail = exc.stderr if isinstance(exc, git_ops.GitError) else str(exc)
        return {**base, "error": f"Legacy creation reconciliation refused: {detail}"}


def _result(base: dict[str, Any], pr: tracking.PRRecord, *, already: bool) -> dict[str, Any]:
    return {
        **base, "success": True, "pr_id": pr.pr_id, "pr_revision": pr.pr_revision,
        "state": pr.state, "creation_reconciled": True, "already_reconciled": already,
        "local_disposition": "retired",
        "publication_outcome": "unknown", "creation_cancelled": False,
        "provider_closed": False, "claim_released": False, "receipt": dict(pr.creation),
    }
