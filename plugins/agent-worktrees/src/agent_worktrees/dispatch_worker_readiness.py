"""Nonfinalizing managed-worker readiness; no task or process state changes."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path

from . import (
    claim_handoffs,
    finalize_landing,
    finalized_checkout,
    git_ops,
    resource_relationship,
    tracking,
)
from . import config as cfg
from . import finalize_open_pr_gate as pr_gate
from .dispatch_worker_closeout import OwnWorker, _text
from .record_read import read_exact_record, read_workspace_record

CONTRACT = "agent-worktrees.dispatch-worker-readiness"
OPERATIONS = ("observe-ready", "check-own-ready")


def describe_contract() -> dict:
    """Describe only the readiness operations provided by this executing runtime."""
    return {
        "contract": CONTRACT,
        "version": 1,
        "operations": list(OPERATIONS),
        "native_proof": "inuse-hold-v1",
        "platforms": ["linux", "wsl"],
        "paired_readiness": False,
        "finalized": False,
        "task_release": False,
        "process_retirement": False,
    }


def require_task_claim(
    record: tracking.WorktreeRecord,
    task_id: str,
    expected_ref: str,
) -> tracking.ResourceClaim:
    """Except only the exact stored, bound, protected assignment relationship."""
    matches = [claim for claim in record.resources if claim.ref == expected_ref]
    if len(matches) != 1:
        raise ValueError("Readiness requires its exact stored task responsibility")
    claim = matches[0]
    metadata = claim.producer
    creator = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    if (
        claim.kind != "task"
        or claim.state != "active"
        or not claim.is_unsettled
        or claim.metadata_opaque
        or claim.handoff_bundle
        or not resource_relationship.valid_metadata(metadata)
        or metadata["phase"] != "bound"
        or metadata["creator"] != creator
        or metadata["namespace"] not in {"dispatch-local-task", "dispatch-central-task"}
        or resource_relationship.claim_ref(metadata) != expected_ref
    ):
        raise ValueError("Readiness requires its original protected task assignment")
    envelope = resource_relationship.decode_ref(expected_ref)
    recovery = envelope["recovery"]
    identity = envelope["identity"]
    if (
        set(recovery)
        != {
            "schema",
            "version",
            "authority",
            "queue_identity",
            "task_id",
            "assignment_event_id",
            "worker_id",
        }
        or recovery["schema"] != "dispatch-task-assignment"
        or type(recovery["version"]) is not int
        or recovery["version"] != 2
        or metadata["namespace"] != f"dispatch-{recovery['authority']}-task"
        or recovery["task_id"] != task_id
        or re.fullmatch(r"[0-9a-f]{32}", task_id) is None
        or not isinstance(recovery["queue_identity"], str)
        or re.fullmatch(r"[0-9a-f]{32}", recovery["queue_identity"]) is None
        or type(recovery["assignment_event_id"]) is not int
        or not 1 <= recovery["assignment_event_id"] <= 2**63 - 1
        or recovery["worker_id"] != f"{record.execution_machine}/{record.worktree_id}"
        or set(identity) != {"assignment_event_id", "worker_id"}
        or type(identity["assignment_event_id"]) is not int
        or identity != {key: recovery[key] for key in identity}
    ):
        raise ValueError("Readiness task assignment does not match its original owner")
    return claim


def _require_ready(
    worker: OwnWorker, record: tracking.WorktreeRecord, config: cfg.Config, ref: str
) -> None:
    if config.platform not in {"linux", "wsl"} or record.status != "active":
        raise ValueError("Readiness requires a qualified active managed workspace")
    worker._require_custody(record, config, readiness_claim=ref)
    own_ref = tracking.format_claim_ref(
        record.execution_machine,
        record.repo,
        record.worktree_id,
        session=worker.native_id,
    )
    retained = require_task_claim(record, worker.task_id, ref)
    if any(
        claim.metadata_opaque
        or claim.handoff_bundle
        or (
            claim.is_unsettled
            and claim is not retained
            and not (claim.kind == "session" and claim.ref == own_ref and not claim.has_producer)
        )
        for claim in record.resources
    ):
        raise ValueError("Readiness retains unsettled downstream or shared responsibility")
    creator = tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id)
    if claim_handoffs.active_bundle_ids_for_source(creator):
        raise ValueError("Readiness retains a nonterminal claim handoff")
    workspace = read_workspace_record(record.worktree_path)["worktree"]
    if not isinstance(workspace, dict) or (
        workspace["id"] != worker.worktree_id
        or workspace["creation_nonce"] != worker.creation_nonce
        or Path(workspace["path"]).resolve(strict=True) != worker.path
    ):
        raise ValueError("Readiness requires its actual managed checkout")


def _branch_ref(name: str) -> str:
    if not name or name.startswith("refs/heads/"):
        return name
    if name.startswith("refs/"):
        raise ValueError("Readiness requires a local tracked branch")
    return f"refs/heads/{name}"


def _landed(record: tracking.WorktreeRecord, repo, checkout: dict) -> dict:
    # Existing merge-boundary probes are read-only but accept inherited Git
    # selectors. Refuse those contexts rather than changing process-global env.
    if any(
        key.upper() in git_ops._REPOSITORY_CONTEXT_ENV
        or key.upper().startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
        for key in os.environ
    ):
        raise ValueError("Readiness requires an unredirected Git observation context")
    observed = deepcopy(record)
    observed.branch = _branch_ref(observed.branch)
    entries = [observed.pr] if observed.pr is not None else []
    entries += [pr for pr in observed.prs if all(pr is not entry for entry in entries)]
    for pr in entries:
        pr.branch = _branch_ref(pr.branch)
    with pr_gate.one_read_per_pr():
        for pr in entries:
            pulled = pr_gate.pull_for(pr, repo)
            if not pulled:
                raise ValueError("Readiness cannot freshly confirm a tracked PR")
            result = pulled[-1]
            head = getattr(result, "head_sha", "")
            if (
                not (
                    getattr(result, "merged", False) is True
                    or getattr(result, "state", "") == "merged"
                )
                or not isinstance(head, str)
                or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head) is None
            ):
                raise ValueError("Readiness retains an open, unmerged or unconfirmed PR")
            pr.state, pr.head_sha = "merged", head
        bases = [finalize_landing.remote_ref(repo, repo.default_branch)]
        for pr in entries:
            base = pr_gate.pr_base_ref(pr, repo)
            if not base:
                raise ValueError("Readiness cannot confirm the tracked PR landing base")
            bases.append(finalize_landing.remote_ref(repo, base))
        bases = tuple(
            base
            for base in dict.fromkeys(bases)
            if git_ops.ref_exists(base, cwd=record.worktree_path)
        )
        if not bases:
            raise ValueError("Readiness cannot confirm any published landing base")
        if entries:
            if observed.pr is None or pr_gate.content_exceeds_merged_head_any(
                observed,
                checkout["head"],
                bases,
                cwd=record.worktree_path,
            ):
                raise ValueError("Readiness retains unpublished or unconfirmed committed content")
        else:
            # With no PR, ancestry is sufficient and cannot write probe objects.
            refs = {checkout["head"], observed.branch}
            if not all(
                any(
                    git_ops.is_commit_ancestor(ref, base, cwd=record.worktree_path)
                    for base in bases
                )
                for ref in refs
            ):
                raise ValueError(
                    "Readiness cannot confirm committed content on its published base"
                )
    return {
        "bases": list(bases),
        "prs": [
            {"number": pr.number, "head_sha": pr.head_sha, "state": pr.state} for pr in entries
        ],
    }


def _refs(path: str) -> str:
    return git_ops.git(
        "for-each-ref",
        "--format=%(refname)%09%(objectname)",
        "refs/heads/",
        "refs/remotes/",
        cwd=path,
        timeout=5,
        isolated_repository=True,
    ).stdout


def observe_ready(
    worktree_id: str,
    task_id: str,
    reservation_key: str,
    native_id: str,
    *,
    creation_nonce: str,
    head_revision: int,
    task_claim_ref: str,
    own_caller: bool = False,
) -> dict:
    """Independently read current readiness, never accept a worker's receipt.

    Callers must bind this observation to their immutable preparation and
    separately recheck queue admission; readiness grants no transition authority.
    """
    for name, value in (
        ("task ID", task_id),
        ("reservation key", reservation_key),
        ("native ID", native_id),
        ("creation nonce", creation_nonce),
    ):
        _text(value, name)
    if type(head_revision) is not int or head_revision < 0:
        raise ValueError("Readiness requires an exact head revision")
    if not isinstance(task_claim_ref, str) or not 1 <= len(task_claim_ref) <= 4096:
        raise ValueError("Readiness requires a bounded original task claim")
    metadata = read_exact_record(worktree_id)
    path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    with tracking._RecordLock(path, require_sidecar=True):
        record = tracking.load_record(path)
        config = cfg.load_config()
        entry = record.session_entry(native_id)
        sources = (
            [
                activation.source_identity
                for activation in entry.activations
                if activation.source_identity
                and not activation.source_ambiguous
                and activation.ended_at is None
            ]
            if entry is not None
            else []
        )
        if (
            len(sources) != 1
            or record.dispatch_attempt is None
            or type(record.head_revision) is not int
            or metadata["path"] != record.worktree_path
            or metadata["creation_nonce"] != creation_nonce
        ):
            raise ValueError(
                "Readiness requires the original allocation and canonical native source"
            )
        worker = OwnWorker(
            task_id,
            reservation_key,
            native_id,
            worktree_id,
            creation_nonce,
            Path(record.worktree_path).resolve(strict=True),
            sources[0],
            head_revision,
            record.dispatch_attempt,
        )
        _require_ready(worker, record, config, task_claim_ref)
        if own_caller:
            worker.require_caller()
        before = deepcopy(record)
        checkout = finalized_checkout.snapshot(record.worktree_path)
        refs = _refs(record.worktree_path)
        repo = config.repos[record.repo]
    landing = _landed(before, repo, checkout)
    with tracking._RecordLock(path, require_sidecar=True):
        fresh = tracking.load_record(path)
        fresh_config = cfg.load_config()
        if fresh_config != config:
            raise ValueError("Readiness configuration changed during observation")
        _require_ready(worker, fresh, fresh_config, task_claim_ref)
        if own_caller:
            worker.require_caller()
        if (
            fresh != before
            or finalized_checkout.snapshot(before.worktree_path) != checkout
            or _refs(before.worktree_path) != refs
        ):
            raise ValueError("Readiness custody or checkout changed during observation")
        return {
            "contract": CONTRACT,
            "version": 1,
            "operation": "check-own-ready" if own_caller else "observe-ready",
            "ready": True,
            "task_id": task_id,
            "reservation_key": reservation_key,
            "native_id": native_id,
            "worktree_id": worktree_id,
            "creation_nonce": creation_nonce,
            "head_revision": head_revision,
            "task_claim_ref": task_claim_ref,
            "project": record.repo,
            "path": record.worktree_path,
            "machine": record.execution_machine,
            "allocation": record.dispatch_attempt.to_dict(),
            "checkout": checkout,
            "landing": landing,
            "checkout_retained": True,
            "task_release": False,
            "process_retirement": False,
            "finalized": False,
        }
