"""Claim-ledger CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from . import activity, claim_handoffs, obligations, output, tracking
from . import config as cfg, state_root as state_root_mod


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _infer_worktree_id(*args, **kwargs):
    return _core()._infer_worktree_id(*args, **kwargs)


def _json_error(*args, **kwargs):
    return _core()._json_error(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "claims",
        help="Show a worktree's full claim ledger (outbound resources + its "
        "owner + inbound tasks; best-effort via agent-dispatch). Defaults "
        "to the current worktree; pass an id for another. "
        "`claims release <ref>` retires one outbound claim.",
    )
    p.add_argument(
        "target",
        nargs="*",
        default=None,
        help="[worktree_id] to show, OR 'add <kind> <ref>' to journal "
        "a new outbound claim, OR 'release <ref>' to retire one, "
        "OR 'settle <ref>' to mark it at-rest (settled) / released, "
        "OR 'sweep' to reclaim provably-gone+safe obligations "
        "(never-wedge), OR 'reconcile-at-rest [<worktree-id> ...]' to "
        "release lingering at-rest claims on existing records (never "
        "active; no selector = all; --apply to act), OR 'orphans' to "
        "list obligations re-homed by an --abandon finalize (pending "
        "cleanup), OR 'cleanup [<ref-or-source-worktree> ...]' to "
        "reclaim matching re-homed obligations (no selector = all; "
        "--apply to act)",
    )
    p.add_argument(
        "--remove",
        action="store_true",
        help="with release: drop the claim entry entirely instead of marking it released",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="with sweep/cleanup/reconcile-at-rest: write the abandonments "
        "/ reclaim the orphaned resources / release the at-rest claims "
        "(default: dry-run preview only)",
    )
    p.add_argument("--note", default="", help="with add: an optional human label for the claim")
    p.add_argument(
        "--status",
        default=None,
        help="with mirror-status: the disposition to mirror onto the "
        "claim's cross-machine discovery store (e.g. active|at-rest|released)",
    )
    p.add_argument(
        "--holder",
        default=None,
        dest="claim_holder",
        help="with mirror-status: an opaque holder identity for the mirrored "
        "lease (default: 'agent-dispatch')",
    )
    p.add_argument(
        "--released",
        action="store_true",
        help="with settle: mark the claim released rather than at-rest",
    )
    p.add_argument(
        "--worktree",
        default=None,
        dest="release_worktree",
        help="with release/settle: the owner worktree (default: current)",
    )
    p.add_argument(
        "--owner-ref",
        default=None,
        dest="claim_owner_ref",
        help="with add/settle: journal/settle onto the owner named by "
        "this qualified ref (machine/project/worktree_id) instead "
        "of the current project's cwd-inferred worktree -- resolves "
        "cross-project on THIS machine (a cross-machine owner is "
        "deferred to the lease mirror). For a call-site (e.g. "
        "agent-codespaces on CodeSpace borrow/disconnect) whose cwd "
        "is not the borrowing worktree.",
    )
    p.add_argument(
        "--to",
        nargs="+",
        default=None,
        dest="handoff_to",
        metavar="VALUE",
        help="with handoff offer: qualified consumer "
        "machine/project/worktree_id followed by claim refs; "
        "values end at the next option",
    )
    p.add_argument(
        "--reason", default="", help="with handoff decline/cancel: required explanation"
    )
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")


def _dispatch_assigned_tasks(machine: str, worktree_id: str, cwd: str) -> dict:
    """Best-effort inbound tasks a worktree claims, via agent-dispatch.

    Distinct from agent-worktrees' OWN claims ledger (outbound resource
    claims) -- this reads agent-dispatch's separate assigned/owned TASK
    concept, hence the name (renamed from ``_inbound_claims``, which
    conflated the two, as part of the claim-provider-pattern effort).

    Resolves agent-dispatch's own payload-local binstub via the
    ``dispatch-task:`` claim-provider registry entry -- never an ambient
    ``PATH`` lookup -- closing the tier-3 -> tier-9 upward call this effort
    exists to fix. Degrades identically to the prior ``shutil.which``
    behavior when no provider is registered (e.g. agent-dispatch not
    installed): ``{"available": False, "reason": "..."}``.

    Unlike ``resolve_claim_status``'s own ``claim-status <ref>`` contract,
    this drives a DIFFERENT subcommand shape (``worktree-status --machine
    ... --worktree ...``) -- ``claim_providers.build_provider_argv`` resolves
    the binstub AND validates ``machine``/``worktree_id`` (persisted identity
    values, not literal constants) with ``is_safe_argument`` in one guarded
    step, before reaching a possibly cmd.exe-wrapped argv (see that helper's
    own docstring for why).
    """
    from . import claim_providers

    full_argv = claim_providers.build_provider_argv(
        "dispatch-task",
        ("worktree-status", True), ("--machine", True), (machine, False),
        ("--worktree", True), (worktree_id, False),
        kind="status")
    if full_argv is None:
        return {"available": False, "reason": "agent-dispatch not installed"}
    try:
        proc = subprocess.run(
            full_argv,
            cwd=cwd if cwd and Path(cwd).exists() else None,
            capture_output=True,
            text=True,
            timeout=15,
            env=claim_providers.peer_env(),
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"available": False, "reason": f"agent-dispatch call failed: {e}"}
    if proc.returncode != 0:
        return {
            "available": False,
            "reason": (proc.stderr or "").strip() or "agent-dispatch error",
        }
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return {"available": False, "reason": "unparseable agent-dispatch output"}
    assigned = data.get("assigned") or []
    owned = data.get("owned") or []
    return {"available": True, "assigned": assigned, "owned": owned}


def cmd_claims(args: argparse.Namespace) -> int:
    """Dispatch the claims verb."""
    target = list(getattr(args, "target", None) or [])
    if target and target[0] == "handoff":
        return _claims_handoff(args, target[1:])
    if target and target[0] == "add":
        if len(target) < 3:
            if args.json:
                return _json_error("claims add: usage 'add <kind> <ref>'", 2)
            output.err(
                "claims add: usage 'add <kind> <ref>' "
                "(kind: worktree|codespace|container|ssh|workdir|pr)"
            )
            return 2
        return _claims_add(args, target[1], target[2])
    if target and target[0] == "release":
        if len(target) < 2:
            if args.json:
                return _json_error("claims release: missing <ref>", 2)
            output.err("claims release: missing <ref>. Usage: claims release <ref> [--remove]")
            return 2
        return _claims_release(args, target[1])
    if target and target[0] == "settle":
        if len(target) < 2:
            if args.json:
                return _json_error("claims settle: missing <ref>", 2)
            output.err("claims settle: missing <ref>. Usage: claims settle <ref> [--released]")
            return 2
        return _claims_settle(args, target[1])
    if target and target[0] == "sweep":
        return _claims_sweep(args)
    if target and target[0] == "reconcile-at-rest":
        return _claims_reconcile_at_rest(args)
    if target and target[0] == "mirror-status":
        if len(target) < 3:
            msg = (
                "claims mirror-status: usage 'mirror-status <kind> <ref> "
                "--status <disposition>'"
            )
            if args.json:
                return _json_error(msg, 2)
            output.err(msg)
            return 2
        return _claims_mirror_status(args, target[1], target[2])
    if target and target[0] == "cleanup":
        return _claims_cleanup(args)
    if target and target[0] == "orphans":
        return _claims_orphans(args)
    worktree_id = target[0] if target else None
    return _claims_show(args, worktree_id)


def _claim_handoff_actor(config: cfg.Config, explicit_worktree: str | None) -> str:
    worktree_id = _infer_worktree_id(explicit_worktree, config)
    if not worktree_id:
        raise claim_handoffs.ClaimHandoffError(
            "cannot infer the acting worktree; run inside it or pass --worktree"
        )
    project = config.repo_name or cfg.project_name()
    return tracking.format_claim_ref(config.machine, project, worktree_id)


def _require_coordination_readiness(
    config: cfg.Config,
    *,
    json_out: bool,
) -> int | None:
    readiness = state_root_mod.coordination_readiness(config)
    if readiness.ready:
        return None
    return _emit_coordination_rejection(readiness, json_out=json_out)


def _emit_coordination_rejection(
    readiness: state_root_mod.CoordinationReadiness,
    *,
    json_out: bool,
) -> int:
    if json_out:
        _json_output(
            {
                "error": readiness.error,
                "code": readiness.code,
                "coordination_readiness": readiness.as_dict(),
            }
        )
    else:
        output.err(f"{readiness.code}: {readiness.error}")
    return 3


class CoordinationReadinessFailure(RuntimeError):
    """A claim-producing operation lacks a durable coordination identity."""

    def __init__(
        self,
        readiness: state_root_mod.CoordinationReadiness,
    ) -> None:
        self.readiness = readiness
        super().__init__(readiness.error or readiness.code)


def _coordination_readiness_for_owner_ref(
    owner_ref: str,
    config: cfg.Config,
) -> state_root_mod.CoordinationReadiness:
    """Resolve readiness in the project that owns a produced resource."""
    parsed_owner = tracking.parse_claim_ref(owner_ref)
    if parsed_owner is None or not parsed_owner.is_qualified:
        raise ValueError(
            f"--owner-ref must be a qualified machine/project/worktree_id ref (got {owner_ref!r})"
        )
    readiness_config = config
    if parsed_owner.machine == config.machine:
        try:
            readiness_config = cfg.load_project_config(parsed_owner.project)
        except (OSError, RuntimeError, ValueError) as exc:
            root = state_root_mod.StateRoot(
                None,
                "knowledge_repo",
                parsed_owner.project,
                False,
                True,
                False,
                error=str(exc),
            )
            return state_root_mod.CoordinationReadiness(
                False,
                "state_root_resolution_failed",
                root,
                error=(
                    f"Could not resolve owner project "
                    f"'{parsed_owner.project}' for durable coordination: {exc}"
                ),
            )
    return state_root_mod.coordination_readiness(readiness_config)


def _claims_handoff(args: argparse.Namespace, target: list[str]) -> int:
    """Dispatch same-machine claim-bundle offer/show/decline/cancel."""
    if not target:
        msg = "claims handoff: missing action (offer|show|decline|cancel)"
        if args.json:
            return _json_error(msg, 2)
        output.err(msg)
        return 2
    action = target[0]
    if action not in {"offer", "show", "decline", "cancel"}:
        msg = f"claims handoff: unknown action {action!r} (expected offer|show|decline|cancel)"
        if args.json:
            return _json_error(msg)
        output.err(msg)
        return 1
    try:
        if action == "show":
            if len(target) != 2:
                raise claim_handoffs.ClaimHandoffError(
                    "claims handoff show: usage 'show <bundle-id>'"
                )
            bundle = claim_handoffs.show(target[1])
            created = None
        else:
            config = cfg.load_config()
            if action == "offer":
                blocked = _require_coordination_readiness(config, json_out=args.json)
                if blocked is not None:
                    return blocked
            actor = _claim_handoff_actor(config, getattr(args, "release_worktree", None))
            if action == "offer":
                handoff_values = list(getattr(args, "handoff_to", None) or [])
                if not handoff_values:
                    raise claim_handoffs.ClaimHandoffError(
                        "claims handoff offer requires --to <machine/project/worktree>"
                    )
                consumer, *trailing_refs = handoff_values
                bundle, created = claim_handoffs.offer(
                    actor,
                    consumer,
                    [*target[1:], *trailing_refs],
                    machine=config.machine,
                )
            elif action in {"decline", "cancel"}:
                if len(target) != 2:
                    raise claim_handoffs.ClaimHandoffError(
                        f"claims handoff {action}: usage '{action} <bundle-id> --reason <text>'"
                    )
                bundle = claim_handoffs.transition(
                    target[1],
                    actor=actor,
                    action="declined" if action == "decline" else "cancelled",
                    reason=getattr(args, "reason", "") or "",
                )
                created = None
    except claim_handoffs.ClaimHandoffError as exc:
        if args.json:
            return _json_error(str(exc))
        output.err(str(exc))
        return 1
    if action == "offer":
        activity.log_event(
            "claim_handoff_offered",
            worktree_id=bundle.source,
            bundle_id=bundle.bundle_id,
            consumer=bundle.consumer,
            refs=[claim["ref"] for claim in bundle.claims],
            created=created,
        )
    elif action == "decline":
        activity.log_event(
            "claim_handoff_declined",
            worktree_id=bundle.source,
            bundle_id=bundle.bundle_id,
            consumer=bundle.consumer,
            reason=bundle.reason,
        )
    elif action == "cancel":
        activity.log_event(
            "claim_handoff_cancelled",
            worktree_id=bundle.source,
            bundle_id=bundle.bundle_id,
            consumer=bundle.consumer,
            reason=bundle.reason,
        )
    payload = bundle.to_dict()
    if created is not None:
        payload["created"] = created
    if args.json:
        _json_output(payload)
        return 0
    refs = ", ".join(claim["ref"] for claim in bundle.claims)
    if action == "show":
        print(
            f"Claim bundle {bundle.bundle_id}: {bundle.state}\n"
            f"  source: {bundle.source}\n"
            f"  consumer: {bundle.consumer}\n"
            f"  claims: {refs}"
        )
    elif action == "offer":
        verb = "offered" if created else "already offered"
        print(f"Claim bundle {bundle.bundle_id} {verb} to {bundle.consumer}: {refs}")
    else:
        print(f"Claim bundle {bundle.bundle_id}: {bundle.state}")
    return 0


def _resolve_owner_ref_record_path(
    owner_ref: str,
    config: cfg.Config,
) -> tuple[Path | None, str, str | None]:
    """Resolve a qualified owner-ref to a local tracking record path."""
    parsed = tracking.parse_claim_ref(owner_ref)
    if parsed is None or not parsed.is_qualified:
        return (
            None,
            "",
            f"--owner-ref must be a qualified machine/project/worktree_id ref (got {owner_ref!r})",
        )
    if parsed.machine != config.machine:
        return (None, parsed.worktree_id, None)
    path = cfg.project_dir(parsed.project) / "worktrees" / f"{parsed.worktree_id}.yaml"
    return (path, parsed.worktree_id, None)


def _claims_add(args: argparse.Namespace, kind: str, ref: str) -> int:
    """Journal a new outbound resource claim on a worktree."""
    valid_kinds = {"worktree", "codespace", "container", "ssh", "workdir", "pr", "task"}
    if kind not in valid_kinds:
        msg = (
            f"claims add: unknown kind {kind!r} (expected one of {', '.join(sorted(valid_kinds))})"
        )
        if args.json:
            return _json_error(msg, 2)
        output.err(msg)
        return 2
    config = cfg.load_config()
    owner_ref = getattr(args, "claim_owner_ref", None)
    if owner_ref:
        rec_path, wt_id, err = _resolve_owner_ref_record_path(owner_ref, config)
        if err:
            if args.json:
                return _json_error(err, 2)
            output.err(err)
            return 2
        readiness = _coordination_readiness_for_owner_ref(owner_ref, config)
        if not readiness.ready:
            return _emit_coordination_rejection(readiness, json_out=args.json)
        if rec_path is None:
            if args.json:
                _json_output(
                    {
                        "worktree_id": wt_id,
                        "kind": kind,
                        "ref": ref,
                        "deferred": True,
                        "reason": "cross-machine-owner",
                    }
                )
                return 0
            output.warn(
                f"owner-ref {owner_ref} is on another machine -- claim deferred to the lease mirror (no local ledger write)"
            )
            return 0
    else:
        blocked = _require_coordination_readiness(config, json_out=args.json)
        if blocked is not None:
            return blocked
        wt_id = _infer_worktree_id(getattr(args, "release_worktree", None), config)
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    with tracking._RecordLock(rec_path, require_sidecar=True):
        rec = tracking.load_record(rec_path)
        if rec.status in {"finalizing", "orphaned"}:
            msg = (
                f"claims add: owner worktree {wt_id} is {rec.status}; "
                "creator ownership is frozen and cannot accept new resources"
            )
            if args.json:
                return _json_error(msg)
            output.err(msg)
            return 1
        was_finalized = rec.status == "finalized"
        claim = tracking.ResourceClaim(
            kind=kind,
            ref=ref,
            created_at=tracking._now_iso(),
            state=obligations.ACTIVE,
            note=getattr(args, "note", "") or "",
        )
        tracking.add_resource_claim(rec, claim, save=False)
        reopened = was_finalized and rec.status == "active"
        # worktree-finality-and-obligations Phase 2: on reopen, surface what
        # the earlier finalize's `release_all_resources` cascade let go --
        # reopening restores the worktree to `active`, never those resources.
        released_by_finalize = (
            list(rec.last_finalize_released) if reopened else []
        )
        tracking.save_record(rec, rec_path)
    activity.log_event(
        "claim_added",
        worktree_id=wt_id,
        kind=kind,
        ref=ref,
        state=obligations.ACTIVE,
        reopened=reopened,
    )
    if args.json:
        _json_output(
            {
                "worktree_id": wt_id,
                "kind": kind,
                "ref": ref,
                "state": obligations.ACTIVE,
                "reopened": reopened,
                "released_by_earlier_finalize": [
                    {"kind": c.kind, "ref": c.ref, "note": c.note}
                    for c in released_by_finalize
                ],
            }
        )
        return 0
    print(f"added outbound claim {kind}:{ref} on {wt_id}")
    if reopened:
        print(f"  reopened {wt_id}: finalized -> active (new held claim)")
        if released_by_finalize:
            print(
                "  resources released by the earlier finalize (not restored "
                "-- review/re-claim if still needed):"
            )
            for c in released_by_finalize:
                label = f"    · {c.kind}: {c.ref}"
                if c.note:
                    label += f" ({c.note})"
                print(label)
    return 0


def _claims_mirror_status(args: argparse.Namespace, kind: str, ref: str) -> int:
    """Mirror a claim's disposition onto its cross-machine discovery store."""
    status = getattr(args, "status", None)
    if not status:
        msg = "claims mirror-status: --status is required"
        if args.json:
            return _json_error(msg, 2)
        output.err(msg)
        return 2
    if kind != "task":
        msg = (
            f"claims mirror-status: unsupported kind {kind!r} "
            "(only 'task' is externally mirrored today)"
        )
        if args.json:
            return _json_error(msg, 2)
        output.err(msg)
        return 2
    from . import task_claim_registry

    holder = getattr(args, "claim_holder", None) or "agent-dispatch"
    ok = task_claim_registry.set_task_claim_status(ref, status, holder=holder)
    if args.json:
        _json_output({"kind": kind, "ref": ref, "status": status, "mirrored": ok})
        return 0 if ok else 1
    if ok:
        print(f"mirrored {kind}:{ref} disposition -> {status}")
        return 0
    output.err(f"claims mirror-status: failed to mirror {kind}:{ref} -> {status}")
    return 1


def _claims_release(args: argparse.Namespace, ref: str) -> int:
    """Retire a single outbound resource claim by ref from a worktree's record."""
    config = cfg.load_config()
    wt_id = _infer_worktree_id(getattr(args, "release_worktree", None), config)
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    with tracking._RecordLock(rec_path, require_sidecar=True):
        rec = tracking.load_record(rec_path)
        match = next((c for c in rec.resources if c.ref == ref), None)
        if match is None:
            if args.json:
                return _json_error(f"no outbound claim with ref: {ref}")
            output.err(f"no outbound claim with ref: {ref} on {wt_id}")
            return 1
        reservation = tracking.claim_handoff_reservation(rec, match)
        if reservation:
            msg = (
                f"claim {ref} is reserved by offered handoff bundle "
                f"{reservation}; accept, decline, or cancel it first"
            )
            if args.json:
                return _json_error(msg)
            output.err(msg)
            return 1
        remove = getattr(args, "remove", False)
        kind = match.kind
        if remove:
            rec.resources = [c for c in rec.resources if c.ref != ref]
            action = "removed"
        else:
            match.state = "released"
            action = "released"
        tracking.save_record(rec, rec_path)
    activity.log_event(
        "claim_released",
        worktree_id=wt_id,
        kind=kind,
        ref=ref,
        action=action,
    )
    if args.json:
        _json_output({"worktree_id": wt_id, "ref": ref, "action": action})
        return 0
    print(f"{action} outbound claim {ref} on {wt_id}")
    return 0


def _claims_settle(args: argparse.Namespace, ref: str) -> int:
    """Settle one outbound resource claim's disposition by ref."""
    config = cfg.load_config()
    owner_ref = getattr(args, "claim_owner_ref", None)
    if owner_ref:
        rec_path, wt_id, err = _resolve_owner_ref_record_path(owner_ref, config)
        if err:
            if args.json:
                return _json_error(err, 2)
            output.err(err)
            return 2
        if rec_path is None:
            if args.json:
                _json_output(
                    {
                        "worktree_id": wt_id,
                        "ref": ref,
                        "deferred": True,
                        "reason": "cross-machine-owner",
                    }
                )
                return 0
            output.warn(
                f"owner-ref {owner_ref} is on another machine -- settle deferred "
                f"to the lease mirror (no local ledger write)"
            )
            return 0
    else:
        wt_id = _infer_worktree_id(getattr(args, "release_worktree", None), config)
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    disposition = obligations.RELEASED if getattr(args, "released", False) else obligations.AT_REST
    with tracking._RecordLock(rec_path, require_sidecar=True):
        rec = tracking.load_record(rec_path)
        match = next((c for c in rec.resources if c.ref == ref), None)
        reservation = tracking.claim_handoff_reservation(rec, match) if match is not None else ""
        if reservation:
            msg = (
                f"claim {ref} is reserved by offered handoff bundle "
                f"{reservation}; accept, decline, or cancel it first"
            )
            if args.json:
                return _json_error(msg)
            output.err(msg)
            return 1
        settled = tracking.settle_resource_claim(rec, ref, disposition, save=False)
        if settled is not None:
            tracking.save_record(rec, rec_path)
    if settled is None:
        if args.json:
            return _json_error(f"no outbound claim with ref: {ref}")
        output.err(f"no outbound claim with ref: {ref} on {wt_id}")
        return 1
    activity.log_event(
        "claim_settled",
        worktree_id=wt_id,
        kind=settled.kind,
        ref=ref,
        disposition=disposition,
    )
    if args.json:
        _json_output({"worktree_id": wt_id, "ref": ref, "disposition": disposition})
        return 0
    print(f"settled outbound claim {ref} on {wt_id} -> {disposition}")
    return 0


def _claims_sweep(args: argparse.Namespace) -> int:
    """Never-wedge reclaim sweep over local ledgers (resource-obligation Ph4)."""
    config = cfg.load_config()
    apply = getattr(args, "apply", False)
    from . import sweep as sweep_mod

    gone_of, safe_of = sweep_mod.make_resolvers(config)

    reclaimed: list[dict[str, str]] = []
    tdir = cfg.tracking_dir()
    for rec in tracking.list_records(tdir):
        rec_path = tdir / f"{rec.worktree_id}.yaml"
        verdicts: dict[str, tuple[bool | None, bool | None]] = {}
        for claim in rec.resources:
            if not claim.is_unsettled or tracking.claim_handoff_reservation(rec, claim):
                continue
            try:
                gone = gone_of(claim)
            except Exception:
                gone = None
            try:
                safe = safe_of(claim)
            except Exception:
                safe = None
            verdicts[claim.ref] = (gone, safe)

        def _gone(claim):
            return verdicts.get(claim.ref, (None, None))[0]

        def _safe(claim):
            return verdicts.get(claim.ref, (None, None))[1]

        if apply:
            with tracking._RecordLock(rec_path, require_sidecar=True):
                rec = tracking.load_record(rec_path)
                flipped = tracking.sweep_abandoned_obligations(
                    rec,
                    gone_of=_gone,
                    safe_of=_safe,
                    save=False,
                )
                if flipped:
                    tracking.save_record(rec, rec_path)
        else:
            before = {c.ref: c.state for c in rec.resources}
            flipped = tracking.sweep_abandoned_obligations(
                rec,
                gone_of=_gone,
                safe_of=_safe,
                save=False,
            )
        for c in flipped:
            reclaimed.append({"owner": rec.worktree_id, "kind": c.kind, "ref": c.ref})
        if flipped and not apply:
            for c in rec.resources:
                if c.ref in before:
                    c.state = before[c.ref]

    if apply:
        for r in reclaimed:
            activity.log_event(
                "claim_abandoned",
                worktree_id=r["owner"],
                kind=r["kind"],
                ref=r["ref"],
            )
    if args.json:
        _json_output({"applied": apply, "reclaimed": reclaimed, "count": len(reclaimed)})
        return 0
    if not reclaimed:
        print("claims sweep: no abandonable obligations found.")
        return 0
    verb = "Abandoned" if apply else "Would abandon (dry-run; pass --apply)"
    print(f"{verb} {len(reclaimed)} obligation(s):")
    for r in reclaimed:
        print(f"  · {r['owner']}: {r['kind']} {r['ref']}")
    return 0


def _claims_reconcile_at_rest(args: argparse.Namespace) -> int:
    """Preview/apply release of lingering at-rest claims on existing records
    (worktree-finality-and-obligations Phase 4 / design.md's dedicated
    reconciliation command). Never touches an ``active`` claim -- finalize's
    own freeze already releases at-rest claims automatically when it runs;
    this is the explicit, operator-driven catch-up for records that never
    went through that (an older-version finalize, or at-rest claims that
    accumulated afterward). Optional worktree-id selectors narrow the scope;
    no selector reconciles every tracked record."""
    apply = getattr(args, "apply", False)
    target = list(getattr(args, "target", None) or [])
    selectors = set(target[1:])
    tdir = cfg.tracking_dir()
    released: list[dict[str, str]] = []
    for rec in tracking.list_records(tdir):
        if selectors and rec.worktree_id not in selectors:
            continue
        rec_path = tdir / f"{rec.worktree_id}.yaml"
        if apply:
            with tracking._RecordLock(rec_path, require_sidecar=True):
                rec = tracking.load_record(rec_path)
                flipped = tracking.release_at_rest_resources(rec, save=False)
                if flipped:
                    tracking.save_record(rec, rec_path)
        else:
            before = {c.ref: c.state for c in rec.resources}
            flipped = tracking.release_at_rest_resources(rec, save=False)
            for c in rec.resources:
                if c.ref in before:
                    c.state = before[c.ref]
        for c in flipped:
            released.append({"owner": rec.worktree_id, "kind": c.kind, "ref": c.ref})

    if apply:
        for r in released:
            activity.log_event(
                "claim_at_rest_reconciled",
                worktree_id=r["owner"],
                kind=r["kind"],
                ref=r["ref"],
            )
    if args.json:
        _json_output({"applied": apply, "released": released, "count": len(released)})
        return 0
    if not released:
        print("claims reconcile-at-rest: no lingering at-rest claims found.")
        return 0
    verb = "Released" if apply else "Would release (dry-run; pass --apply)"
    print(f"{verb} {len(released)} at-rest claim(s):")
    for r in released:
        print(f"  · {r['owner']}: {r['kind']} {r['ref']}")
    return 0


def _claims_cleanup(args: argparse.Namespace) -> int:
    """Reclaim re-homed (abandoned) obligations from the durable orphanage."""
    config = cfg.load_config()
    apply = getattr(args, "apply", False)
    target = list(getattr(args, "target", None) or [])
    selectors = set(target[1:])
    from . import cleanup as cleanup_mod

    rows = cleanup_mod.cleanup_orphanage(config, apply=apply, selectors=selectors or None)

    reclaimed = [r for r in rows if r["status"] == "reclaimed"]
    if apply:
        for r in reclaimed:
            activity.log_event(
                "claim_reclaimed",
                worktree_id=r.get("source_worktree"),
                kind=r.get("kind"),
                ref=r.get("ref"),
                handoff_to=r.get("handoff_to"),
            )
    if args.json:
        _json_output(
            {
                "applied": apply,
                "results": rows,
                "selectors": sorted(selectors),
                "reclaimed": len(reclaimed),
                "count": len(rows),
            }
        )
        return 0
    if not rows:
        if selectors:
            print("claims cleanup: no re-homed obligations matched: " + ", ".join(sorted(selectors)))
        else:
            print("claims cleanup: no re-homed obligations to reclaim (the orphanage is empty).")
        return 0
    verb = "Reclaimed" if apply else "Would reclaim (dry-run; pass --apply)"
    print(f"claims cleanup -- {len(rows)} orphaned obligation(s):")
    for r in rows:
        mark = {
            "reclaimed": "✓",
            "failed": "✗",
            "skipped": "–",
            "unsupported": "?",
        }.get(r["status"], "?")
        line = f"  {mark} {r['kind']}: {r['ref']}  [{r['status']}]"
        if r["detail"]:
            line += f" -- {r['detail']}"
        print(line)
    if reclaimed:
        print(f"{verb}: {len(reclaimed)} of {len(rows)}.")
    return 0


def _claims_orphans(args: argparse.Namespace) -> int:
    """List the durable orphanage -- obligations re-homed by an ``--abandon`` finalize."""
    orphans = tracking.load_orphaned_obligations()
    if args.json:
        _json_output({"orphaned": orphans, "count": len(orphans)})
        return 0
    if not orphans:
        print(
            "claims orphans: no re-homed obligations "
            "(nothing has been --abandon'd, or the registry is empty)."
        )
        return 0
    print(f"Re-homed (abandoned) obligations -- {len(orphans)} pending cleanup:")
    for e in orphans:
        line = f"  · {e.get('kind')}: {e.get('ref')}"
        src = e.get("source_worktree")
        when = e.get("abandoned_at")
        meta = ", ".join(
            x for x in (f"from {src}" if src else "", f"@ {when}" if when else "") if x
        )
        if meta:
            line += f"  ({meta})"
        if e.get("handoff_to"):
            line += f" -> handoff: {e['handoff_to']}"
        if e.get("note"):
            line += f" -- {e['note']}"
        print(line)
    return 0


def _claims_show(args: argparse.Namespace, worktree_id: str | None) -> int:
    """Render a worktree's full claim ledger (agent-fabric resource-claims)."""
    config = cfg.load_config()
    wt_id = _infer_worktree_id(worktree_id, config)
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    rec = tracking.load_record(rec_path)
    readiness = state_root_mod.coordination_readiness(config)

    outbound = [
        {
            "kind": c.kind,
            "ref": c.ref,
            "state": c.state,
            "created_at": c.created_at,
            **({"note": c.note} if c.note else {}),
        }
        for c in rec.resources
    ]
    inbound = _core_helper("_dispatch_assigned_tasks", _dispatch_assigned_tasks)(
        rec.machine or config.machine,
        wt_id,
        rec.worktree_path,
    )

    ledger = {
        "worktree_id": wt_id,
        "repo": rec.repo,
        "machine": rec.machine,
        "owner_ref": rec.owner_ref,
        "outbound": outbound,
        "inbound": inbound,
        "coordination_readiness": readiness.as_dict(),
    }

    if args.json:
        _json_output(ledger)
        return 0

    print(f"Claim ledger for {wt_id}  ({rec.repo} @ {rec.machine})")
    print(f"  coordination readiness: {readiness.code}")
    if rec.owner_ref:
        print(f"  owned as a resource by: {rec.owner_ref}")
    print("  Outbound (resources this worktree owns):")
    if outbound:
        for c in outbound:
            state = "" if c["state"] == "active" else f" [{c['state']}]"
            note = f"  -- {c['note']}" if c.get("note") else ""
            print(f"    - {c['kind']}: {c['ref']}{state}{note}")
    else:
        print("    (none)")
    print("  Inbound (tasks this worktree claims):")
    if not inbound.get("available"):
        print(f"    (unavailable: {inbound.get('reason', 'n/a')})")
    else:
        rows = [("assigned", t) for t in inbound.get("assigned", [])] + [
            ("owned", t) for t in inbound.get("owned", [])
        ]
        if rows:
            for kind, t in rows:
                tid = t.get("id", "?") if isinstance(t, dict) else str(t)
                title = t.get("title", "") if isinstance(t, dict) else ""
                st = t.get("status", "") if isinstance(t, dict) else ""
                print(f"    - [{kind}] {tid} {st}  {title}".rstrip())
        else:
            print("    (none)")
    return 0
