"""Explicit existing-task pair recovery; New prompt custody is not involved."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path
import re
from typing import Any

from . import config as cfg, creation_repair_identity as identity
from . import creation_tail_repair as creation, execution_spaces, finalize_lock
from . import git_ops, launch_admission, paired_allocation_location
from . import paired_claims, procs, repos, sessions, state_root, tracking, worktree_creation
from . import worktree_lifecycle
from . import checkout_hydration_catalog
from . import paired_retained_checkout
from .git_repository_environment import repository_identity_scope


def _subject(config: cfg.Config, worktree_id: str) -> tuple[Path, tracking.WorktreeRecord]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", worktree_id) or ".." in worktree_id:
        raise ValueError("pair recovery requires an exact worktree ID")
    execution_spaces.require_current_execution_space(config)
    path = cfg.project_dir(config.repo_name) / "worktrees" / f"{worktree_id}.yaml"
    if path.is_symlink():
        raise ValueError("pair recovery refuses a redirected record")
    record = tracking.load_record(path)
    if (
        record.repo != config.repo_name or record.worktree_id != worktree_id
        or record.kind != "session" or record.status != "active"
        or not record.checkout_managed
        or not identity._repair_namespace_matches(record, config)
    ):
        raise ValueError("pair recovery requires this project's active managed harness")
    identity.require_retained_record(record, config)
    creation._same_repository(config.default_repo.anchor, record.worktree_path, branch=record.branch)
    from .paired_cleanup import journal_path

    if journal_path(record).exists():
        raise ValueError("interrupted joint cleanup must be resolved before pair recovery")
    return path, record


def _plan(
    config: cfg.Config, record: tracking.WorktreeRecord,
) -> tuple[state_root.StateRoot, tuple[str | None, str, str], tracking.WorktreeRecord | None, Path, str]:
    root = state_root.resolve_state_root(config)
    if not (root.requires_external and root.bound and root.repo and root.path):
        raise ValueError(root.error or "pair recovery requires a resolved external knowledge binding")
    entry = repos.find_repo(root.repo)
    if entry is None or repos.normalize_class(entry.repo_class) not in {"worktree", "knowledge"}:
        raise ValueError("pair recovery requires a canonically registered worktree-capable companion")
    identity.require_companion_receiver(record, root)
    allocation = creation._allocation(record)
    child = creation._retained_sibling(config, record, root, *allocation, require_checkout=False)
    retained_target = paired_allocation_location.original(record, root, legacy=False)
    legacy_target = paired_allocation_location.original(record, root)
    receiving = cfg.load_project_config(root.repo, include_control_plane_related_pr=False)
    target = retained_target or (
        Path(child.worktree_path) if child is not None
        else Path(receiving.default_repo.worktree_root) / f"{record.worktree_id}-k"
    )
    branch = f"worktree/{record.worktree_id}-k"
    catalog_path = checkout_hydration_catalog.entry_for_checkout(target)
    if catalog_path.exists() or catalog_path.is_symlink():
        raise ValueError("companion has retained dehydration evidence; use the native hydration recovery owner")
    if child is None:
        holder = checkout_hydration_catalog.dehydrated_branch_holder(
            root.path, branch, config=receiving,
        )
        if holder is not None:
            raise ValueError("companion branch is reserved by dehydration; use the native hydration recovery owner")
        if any((record.pair_id, record.pair_ref, record.pair_role, record.pair_kind)):
            raise ValueError("paired companion record is missing; retained claims cannot be reconstructed")
        if target.exists() or target.is_symlink() or legacy_target is not None and legacy_target.exists():
            raise ValueError("untracked retained companion data exists; refusing a fresh replacement")
        for claim in record.resources:
            if claim.kind != "worktree":
                continue
            ref = tracking.parse_claim_ref(claim.ref)
            if ref is None:
                raise ValueError("unverifiable retained worktree claim prevents branch adoption")
            if ref.worktree_id == f"{record.worktree_id}-k":
                raise ValueError("retained companion claim requires its original record; adoption cannot replace history")
        if git_ops.local_branch_exists(branch, cwd=root.path):
            return root, allocation, None, target, "adopt-retained-branch"
        return root, allocation, None, target, "allocate"
    child_ref = child.pair_claim_ref
    if child_ref is None:
        raise ValueError("retained companion has no qualified harness reference")
    expected = {
        "pair_id": child.pair_id, "pair_role": "harness", "pair_kind": "worktree",
        "pair_ref": tracking.format_claim_ref(child_ref.machine, child.repo, child.worktree_id),
    }
    if any((record.pair_id, record.pair_ref, record.pair_role, record.pair_kind)) and any(
        getattr(record, key) != value for key, value in expected.items()
    ):
        raise ValueError("harness and retained companion identities conflict")
    if Path(child.worktree_path) != target or not child.checkout_managed:
        raise ValueError("retained companion allocation changed")
    live = sessions.verify_worktree_active(child, strict=True)
    if live.active or not live.probes_ok or not live.mux_probe_ok:
        raise ValueError("retained companion execution is live or unknown")
    if target.exists():
        if reason := procs.checkout_retirement_blocker(child.worktree_path):
            raise ValueError(f"retained companion is in use or unknown: {reason}")
    if target.exists():
        creation._same_repository(root.path, str(target), branch=child.branch)
        if not git_ops.is_clean(cwd=target):
            raise ValueError("retained companion is dirty; preserve its data before reassociation")
        action = "reassociate" if not record.is_paired else "compose"
    else:
        if not git_ops.local_branch_exists(child.branch, cwd=root.path):
            raise ValueError("lost companion checkout has no retained branch; refusing an empty replacement")
        paired_retained_checkout.require_restore_registration(root.path, str(target), child.branch)
        action = "restore-retained-branch"
    return root, allocation, child, target, action


@repository_identity_scope()
def recover(
    worktree_id: str, *, apply: bool = False, creation_nonce: str | None = None,
    adopt_retained_head: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {"version": 1, "worktree_id": worktree_id, "applied": False, "error": None}
    try:
        config = cfg.load_config()
        path, record = _subject(config, worktree_id)
        report["creation_nonce"] = record.creation_nonce
        if apply and (not creation_nonce or creation_nonce != record.creation_nonce):
            raise ValueError("pair recovery --apply requires the inspected --creation-nonce")
        root, allocation, child, target, action = _plan(config, record)
        assert root.repo and root.path
        report.update(action=action, companion_repo=root.repo, companion_path=str(target))
        retained_head = None
        if action == "adopt-retained-branch":
            retained_head = git_ops.git(
                "rev-parse", "--verify", f"refs/heads/worktree/{worktree_id}-k^{{commit}}",
                cwd=root.path,
            ).stdout.strip()
            report["retained_head"] = retained_head
            if apply and adopt_retained_head != retained_head:
                raise ValueError("retained branch adoption requires the inspected --adopt-retained-head")
        elif adopt_retained_head is not None:
            raise ValueError("--adopt-retained-head requires an unrecorded retained companion branch")
        if not apply:
            return report
        paired_retained_checkout.require_local_checkout_support(root.path)
        receiving = cfg.load_project_config(root.repo, include_control_plane_related_pr=False)
        child_path = cfg.project_dir(root.repo) / "worktrees" / f"{worktree_id}-k.yaml"
        initial = (record.creation_nonce, record.worktree_path, record.branch, record.pair_id, record.pair_ref)
        with ExitStack() as locks:
            for lock in sorted({
                Path(config.default_repo.worktree_root) / ".finalize.lock",
                Path(receiving.default_repo.worktree_root) / ".finalize.lock",
            }):
                locks.enter_context(finalize_lock.FinalizeLock(lock, timeout=3, stale_after=3600))
            for member_path in sorted((path, child_path)):
                if member_path == path:
                    locks.enter_context(launch_admission.existing_harness_guard(record))
                else:
                    locks.enter_context(launch_admission.guard(member_path))
                    locks.enter_context(worktree_lifecycle.guard(member_path))
            path, record = _subject(config, worktree_id)
            if initial != (
                record.creation_nonce, record.worktree_path, record.branch, record.pair_id, record.pair_ref,
            ):
                raise ValueError("harness allocation changed; inspect again")
            fresh = _plan(config, record)
            if (fresh[0].repo, fresh[0].path, fresh[3], fresh[4]) != (root.repo, root.path, target, action):
                raise ValueError("pair recovery evidence changed; inspect again")
            root, allocation, child, target, action = fresh
            prepared_source = None
            if child is None and retained_head is None:
                entry = repos.find_repo(root.repo)
                if entry is None or root.path is None:
                    raise ValueError("companion source registration disappeared")
                remote = git_ops.resolve_remote_name(entry.remote or "origin", cwd=root.path)
                branch = entry.default_branch or "main"
                base = worktree_creation._prepare_worktree_source(
                    root.path, remote=remote, default_branch=branch,
                    fast_forward_anchor=config.auto_fast_forward,
                    label=f"paired knowledge repo '{root.repo}'",
                )
                prepared_source = (root.path, remote, branch, base)
            assert root.path is not None
            locks.enter_context(checkout_hydration_catalog.reservation_lock(root.path))
            for member_path in sorted((path, child_path)):
                locks.enter_context(tracking._RecordLock(member_path, require_sidecar=True))
            fresh_config = cfg.load_config()
            path, current = _subject(fresh_config, worktree_id)
            if initial != (
                current.creation_nonce, current.worktree_path, current.branch, current.pair_id, current.pair_ref,
            ):
                raise ValueError("harness allocation changed under record admission")
            locked_plan = _plan(fresh_config, current)
            if (locked_plan[0].repo, locked_plan[0].path, locked_plan[3], locked_plan[4]) != (
                root.repo, root.path, target, action,
            ):
                raise ValueError("pair recovery evidence changed under record admission")
            config, record = fresh_config, current
            root, allocation, child, target, action = locked_plan
            assert root.repo and root.path
            if child is None:
                worktree_creation._paired_knowledge_allocation_preflight(config)
                if paired_allocation_location.original(record, root, legacy=False) is None:
                    paired_allocation_location.reserve(config, worktree_id, record.creation_nonce)

                def revalidate() -> None:
                    fresh_config = cfg.load_config()
                    _, current = _subject(fresh_config, worktree_id)
                    plan = _plan(fresh_config, current)
                    if (
                        current.creation_nonce != record.creation_nonce
                        or (plan[0].repo, plan[0].path, plan[2], plan[3], plan[4])
                        != (root.repo, root.path, None, target, action)
                    ):
                        raise ValueError("pair allocation changed during source preparation")

                stamp = worktree_creation._carve_paired_knowledge(
                    config, harness_id=worktree_id, timestamp=allocation[0], suffix=allocation[1],
                    plat=record.platform, plat_short=allocation[2],
                    machine_identity=record.machine_identity,
                    legacy_machine=record.machine if record.machine_identity is None else None,
                    retained_pair_path=target,
                    revalidate_creation=revalidate,
                    prepared_source=prepared_source,
                    retained_branch_head=retained_head,
                )
                if not stamp:
                    raise ValueError("native companion allocation did not complete; inspect retained effects")
                child = tracking.load_record(child_path)
            else:
                if action == "restore-retained-branch":
                    # A single --force permits reusing a missing registration,
                    # but never overrides a locked worktree or an existing path.
                    paired_retained_checkout.restore(root.path, str(target), child.branch)
                child_ref = child.pair_claim_ref
                if child_ref is None:
                    raise ValueError("retained companion has no qualified harness reference")
                stamp = {
                    "pair_id": child.pair_id, "pair_role": "harness",
                    "pair_kind": "worktree",
                    "pair_ref": tracking.format_claim_ref(
                        child_ref.machine, child.repo, child.worktree_id,
                    ),
                }
            creation._prepare_permissions(root.path, str(target))
            result = worktree_creation._stamp_and_compose_paired_knowledge(
                config, record, record.worktree_path, stamp,
            )
            creation._require_composition(result or {})
            if paired_claims.reciprocal_sibling(record) is None:
                raise ValueError("native pair publication remains incomplete; retain both records")
            report["applied"] = True
    except creation._REPAIR_ERRORS as exc:
        report["error"] = str(exc)
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-worktrees knowledge repair-pair")
    parser.add_argument("--worktree-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Apply the inspected native pair recovery")
    parser.add_argument("--creation-nonce", help="Exact harness incarnation returned by inspection")
    parser.add_argument(
        "--adopt-retained-head",
        help="Explicitly adopt an unrecorded companion branch at this inspected commit",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = recover(
        args.worktree_id, apply=args.apply, creation_nonce=args.creation_nonce,
        adopt_retained_head=args.adopt_retained_head,
    )
    if args.json:
        from . import output

        output._json_output(result)
    else:
        print(result["error"] or f"{args.worktree_id}: {result['action']}")
    return 1 if result["error"] else 0
