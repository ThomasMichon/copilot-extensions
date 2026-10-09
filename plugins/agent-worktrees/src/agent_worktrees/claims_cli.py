"""Claim-ledger CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import (
    activity,
    claim_history,
    claims_annotate,
    claims_find_cli,
    claims_handoff_cli,
    claims_history_cli,
    claims_owner,
    claims_transitive_cli,
    obligations,
    output,
    tracking,
    worktree_identity,
)
from . import config as cfg, state_root as state_root_mod


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


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
        "OR 'annotate <ref> --note NOTE' to update an existing claim's note, "
        "OR 'settle <ref>' to mark it at-rest (settled) / released, "
        "OR 'sweep' to reclaim provably-gone+safe obligations "
        "(never-wedge), OR 'reconcile-at-rest [<worktree-id> ...]' to "
        "release lingering at-rest claims on existing records (never "
        "active; no selector = all; --apply to act), OR 'orphans' to "
        "list obligations re-homed by an --abandon finalize (pending "
        "cleanup), OR 'cleanup [<ref-or-source-worktree> ...]' to "
        "reclaim matching re-homed obligations (no selector = all; "
        "--apply to act), OR 'fleet-audit' for a read-only obligation "
        "inventory, OR 'find pr --repo <owner/name> [--state ...] "
        "[--live]' to sweep every registered project's worktrees for "
        "one holding a claim on a PR there, OR 'transitive [worktree_id]' "
        "to list every unsettled obligation anywhere in a worktree's whole "
        "subtree (itself + every worktree it created, transitively) -- "
        "a diagnostic convenience, never a substitute for the per-hop "
        "finalize gate, OR 'history <ref>' to list the durable, ordered "
        "claim/release/settle event history recorded for a claimed "
        "resource (today: pr-kind only)",
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
        "--reason",
        default="",
        help="with handoff decline/cancel: required explanation",
    )
    p.add_argument("--actor", default="", dest="claim_actor", help=argparse.SUPPRESS)
    p.add_argument("--all-states", action="store_true", help="with owner: include released claims")
    p.add_argument(
        "--repo",
        default=None,
        dest="claim_repo",
        help="with find pr: only PR claims in this repo (owner/name); omitted, every repo",
    )
    p.add_argument(
        "--state",
        default="open",
        dest="claim_state",
        choices=("open", "closed", "merged", "all"),
        help="with find pr: locally-tracked PR state to match (default: open)",
    )
    p.add_argument(
        "--live",
        action="store_true",
        dest="claim_live",
        help="with find pr: cross-check each candidate against the "
        "provider's live PR state instead of trusting local tracking",
    )
    p.add_argument(
        "--remote", action="store_true",
        help="with history: also merge mirrored remote claim-history events (network)",
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

    providers, _findings = claim_providers.discover_claim_providers()
    provider = providers.get("dispatch-task")
    if provider is None:
        return {"available": False, "reason": "agent-dispatch not installed"}
    callback_args = ("worktree-status", "--machine", machine, "--worktree", worktree_id)
    full_argv = claim_providers.build_provider_argv_for_manifest(
        provider, ("worktree-status", True), ("--machine", True), (machine, False),
        ("--worktree", True), (worktree_id, False), kind="status")
    if full_argv is None:
        return {"available": False, "reason": "agent-dispatch call failed"}
    proc = claim_providers._run_provider_process(
        provider,
        callback_args=callback_args,
        legacy_command=full_argv,
        timeout=15,
        cwd=cwd if cwd and Path(cwd).exists() else None,
    )
    if proc is None:
        return {"available": False, "reason": "agent-dispatch call failed"}
    if proc.returncode != 0:
        return {"available": False, "reason": (proc.stderr or "").strip() or "agent-dispatch error"}
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
    if target and target[0] == "owner": return claims_owner.cmd_claims_owner(args, target[1:])
    if target and target[0] == "handoff":
        return _claims_handoff(args, target[1:])
    if target and target[0] == "add":
        if len(target) < 3:
            if args.json:
                return output._json_error("claims add: usage 'add <kind> <ref>'", 2)
            output.err(
                "claims add: usage 'add <kind> <ref>' "
                "(kind: worktree|codespace|container|ssh|workdir|pr)"
            )
            return 2
        return _claims_add(args, target[1], target[2])
    if target and target[0] == "release":
        if len(target) < 2:
            if args.json:
                return output._json_error("claims release: missing <ref>", 2)
            output.err("claims release: missing <ref>. Usage: claims release <ref> [--remove]")
            return 2
        return _claims_release(args, target[1])
    if target and target[0] == "annotate":
        if len(target) < 2:
            if args.json:
                return output._json_error("claims annotate: missing <ref>", 2)
            output.err("claims annotate: missing <ref>. Usage: claims annotate <ref> --note NOTE")
            return 2
        return claims_annotate.claims_annotate(
            args, target[1], worktree_identity._infer_worktree_id, output._json_error, output._json_output, output,
        )
    if target and target[0] == "settle":
        if len(target) < 2:
            if args.json:
                return output._json_error("claims settle: missing <ref>", 2)
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
                return output._json_error(msg, 2)
            output.err(msg)
            return 2
        return _claims_mirror_status(args, target[1], target[2])
    if target and target[0] == "cleanup":
        return _claims_cleanup(args)
    if target and target[0] == "orphans":
        return _claims_orphans(args)
    if target and target[0] == "fleet-audit":
        from . import fleet_audit_cli
        return fleet_audit_cli.cmd_fleet_audit(args)
    if target and target[0] == "find":
        return claims_find_cli.cmd_claims_find(args, target[1:])
    if target and target[0] == "transitive":
        return _claims_transitive(args, target[1] if len(target) > 1 else None)
    if target and target[0] == "history":
        return _claims_history(args, target[1] if len(target) > 1 else None)
    worktree_id = target[0] if target else None
    return _claims_show(args, worktree_id)
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
        output._json_output(
            {
                "error": readiness.error,
                "code": readiness.code,
                "coordination_readiness": readiness.as_dict(),
            }
        )
    else:
        output.err(f"{readiness.code}: {readiness.error}")
    return 3


def _claim_handoff_actor(config: cfg.Config, explicit_worktree: str | None) -> str:
    return claims_handoff_cli._claim_handoff_actor(
        config,
        explicit_worktree,
        infer_worktree_id=worktree_identity._infer_worktree_id,
        format_claim_ref=tracking.format_claim_ref,
    )


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
    return claims_handoff_cli.claims_handoff(
        args,
        target,
        require_coordination_readiness=_require_coordination_readiness,
        infer_worktree_id=worktree_identity._infer_worktree_id,
        format_claim_ref=tracking.format_claim_ref,
        json_error=output._json_error,
        json_output=output._json_output,
    )


def _resolve_owner_ref_record_path(
    owner_ref: str,
    config: cfg.Config,
) -> tuple[Path | None, str, str | None]:
    """Resolve a qualified owner-ref to a local tracking record path."""
    from .execution_spaces import resolve_owner_record_path
    return resolve_owner_record_path(owner_ref, config)


def _dispatch_claim(verb: str, verb_args: dict):
    """Dispatch one of the resource-claim verbs (``claim_add``/``_release``/
    ``_settle``, ``tracking_claim_write.py``) through the daemon's write path
    when reachable, else the identical in-process code (logged). Raises
    ``tracking_write.AmbiguousWriteOutcome`` when a sent request then failed.
    """
    from . import locks as _locks
    from . import status_monitor_runtime as _smr
    from . import tracking_write

    return tracking_write.dispatch(
        verb,
        verb_args,
        read_lock_data=lambda: _locks.read_lock(_smr._monitor_lock_path()),
        ensure_monitor=_smr._ensure_status_monitor if _smr._status_monitor_enabled() else None,
        min_version=2,
    )


def _claims_add(args: argparse.Namespace, kind: str, ref: str) -> int:
    """Journal a new outbound resource claim on a worktree."""
    valid_kinds = {"worktree", "codespace", "container", "ssh", "workdir", "pr", "task"}
    if kind not in valid_kinds:
        msg = (
            f"claims add: unknown kind {kind!r} (expected one of {', '.join(sorted(valid_kinds))})"
        )
        if args.json:
            return output._json_error(msg, 2)
        output.err(msg)
        return 2
    config = cfg.load_config()
    owner_ref = getattr(args, "claim_owner_ref", None)
    if owner_ref:
        rec_path, wt_id, err = _resolve_owner_ref_record_path(owner_ref, config)
        if err:
            if args.json:
                return output._json_error(err, 2)
            output.err(err)
            return 2
        readiness = _coordination_readiness_for_owner_ref(owner_ref, config)
        if not readiness.ready:
            return _emit_coordination_rejection(readiness, json_out=args.json)
        if rec_path is None:
            if args.json:
                output._json_output(
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
        wt_id = worktree_identity._infer_worktree_id(getattr(args, "release_worktree", None), config)
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return output._json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    from . import tracking_write

    try:
        result = _dispatch_claim(
            "claim_add",
            {
                "worktree_id": wt_id,
                "yaml_path": str(rec_path),
                "kind": kind,
                "ref": ref,
                "note": getattr(args, "note", "") or "",
                "session_id": claim_history.current_session_id(),
            },
        )
    except tracking_write.AmbiguousWriteOutcome as exc:
        msg = f"claims add: write to {wt_id} is in an unknown state: {exc}"
        if args.json:
            return output._json_error(msg)
        output.err(msg)
        return 1
    if result.get("error") in ("frozen", "rejected"):
        if args.json:
            return output._json_error(result["message"])
        output.err(result["message"])
        return 1
    reopened = result["reopened"]
    released_by_finalize = result["released_by_finalize"]
    if args.json:
        output._json_output(
            {
                "worktree_id": wt_id,
                "kind": kind,
                "ref": ref,
                "state": result["state"],
                "reopened": reopened,
                "released_by_earlier_finalize": released_by_finalize,
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
                label = f"    · {c['kind']}: {c['ref']}"
                if c["note"]:
                    label += f" ({c['note']})"
                print(label)
    return 0


def _claims_mirror_status(args: argparse.Namespace, kind: str, ref: str) -> int:
    """Mirror a claim's disposition onto its cross-machine discovery store."""
    status = getattr(args, "status", None)
    if not status:
        msg = "claims mirror-status: --status is required"
        if args.json:
            return output._json_error(msg, 2)
        output.err(msg)
        return 2
    if kind != "task":
        msg = (
            f"claims mirror-status: unsupported kind {kind!r} "
            "(only 'task' is externally mirrored today)"
        )
        if args.json:
            return output._json_error(msg, 2)
        output.err(msg)
        return 2
    from . import task_claim_registry

    holder = getattr(args, "claim_holder", None) or "agent-dispatch"
    ok = task_claim_registry.set_task_claim_status(ref, status, holder=holder)
    if args.json:
        output._json_output({"kind": kind, "ref": ref, "status": status, "mirrored": ok})
        return 0 if ok else 1
    if ok:
        print(f"mirrored {kind}:{ref} disposition -> {status}")
        return 0
    output.err(f"claims mirror-status: failed to mirror {kind}:{ref} -> {status}")
    return 1


def _claims_release(args: argparse.Namespace, ref: str) -> int:
    """Retire a single outbound resource claim by ref from a worktree's record."""
    config = cfg.load_config()
    wt_id = worktree_identity._infer_worktree_id(getattr(args, "release_worktree", None), config)
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return output._json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    from . import tracking_write

    try:
        result = _dispatch_claim(
            "claim_release",
            {
                "worktree_id": wt_id,
                "yaml_path": str(rec_path),
                "ref": ref,
                "remove": bool(getattr(args, "remove", False)),
                "session_id": claim_history.current_session_id(),
            },
        )
    except tracking_write.AmbiguousWriteOutcome as exc:
        msg = f"claims release: write to {wt_id} is in an unknown state: {exc}"
        if args.json:
            return output._json_error(msg)
        output.err(msg)
        return 1
    if result.get("error") == "not_found":
        if args.json:
            return output._json_error(f"no outbound claim with ref: {ref}")
        output.err(f"no outbound claim with ref: {ref} on {wt_id}")
        return 1
    if result.get("error") == "reserved":
        if args.json:
            return output._json_error(result["message"])
        output.err(result["message"])
        return 1
    action = result["action"]
    if args.json:
        output._json_output({"worktree_id": wt_id, "ref": ref, "action": action})
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
                return output._json_error(err, 2)
            output.err(err)
            return 2
        if rec_path is None:
            if args.json:
                output._json_output(
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
        wt_id = worktree_identity._infer_worktree_id(getattr(args, "release_worktree", None), config)
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return output._json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    disposition = obligations.RELEASED if getattr(args, "released", False) else obligations.AT_REST
    from . import tracking_write

    try:
        result = _dispatch_claim(
            "claim_settle",
            {
                "worktree_id": wt_id,
                "yaml_path": str(rec_path),
                "ref": ref,
                "disposition": disposition,
                "session_id": claim_history.current_session_id(),
            },
        )
    except tracking_write.AmbiguousWriteOutcome as exc:
        msg = f"claims settle: write to {wt_id} is in an unknown state: {exc}"
        if args.json:
            return output._json_error(msg)
        output.err(msg)
        return 1
    if result.get("error") == "reserved":
        if args.json:
            return output._json_error(result["message"])
        output.err(result["message"])
        return 1
    if result.get("error") == "not_found":
        if args.json:
            return output._json_error(f"no outbound claim with ref: {ref}")
        output.err(f"no outbound claim with ref: {ref} on {wt_id}")
        return 1
    if args.json:
        output._json_output({"worktree_id": wt_id, "ref": ref, "disposition": disposition})
        return 0
    print(f"settled outbound claim {ref} on {wt_id} -> {disposition}")
    return 0


def _claims_sweep(args: argparse.Namespace) -> int:
    """Never-wedge reclaim sweep over local ledgers (resource-obligation Ph4)."""
    config = cfg.load_config()
    apply = getattr(args, "apply", False)
    from . import sweep as sweep_mod
    from .execution_spaces import ExecutionSpaceError, require_record_mutation

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
                try:
                    require_record_mutation(rec, config)
                except ExecutionSpaceError as exc:
                    if args.json:
                        return output._json_error(str(exc))
                    output.err(str(exc))
                    return 1
                flipped = tracking.sweep_abandoned_obligations(
                    rec,
                    gone_of=_gone,
                    safe_of=_safe,
                    save=False,
                )
                if flipped:
                    tracking.save_record(rec, rec_path)
                    # Append immediately, still inside this record's own
                    # lock, using THIS record's own machine (never the
                    # ambient config's -- a renamed/migrated machine can
                    # differ) -- never deferred to a later batch pass,
                    # so a later record's failure can't silently drop an
                    # already-persisted transition's history entry.
                    for c in flipped:
                        claim_history.record_event(
                            kind=c.kind, ref=c.ref, worktree_id=rec.worktree_id,
                            machine=rec.machine, event="released",
                            # A `pr`-kind claim swept this way is a clean,
                            # provably-merged hand-back (RELEASED), never
                            # an involuntary reclaim -- only a non-pr kind
                            # is genuinely `abandoned` here.
                            note="merged" if c.state == obligations.RELEASED else "abandoned",
                            project=rec.repo,  # THIS record's own project (sweep spans projects)
                        )
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
        output._json_output({"applied": apply, "reclaimed": reclaimed, "count": len(reclaimed)})
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
                    # Same immediate-append, same-record-lock,
                    # record-owned-machine discipline as `_claims_sweep`.
                    for c in flipped:
                        claim_history.record_event(
                            kind=c.kind, ref=c.ref, worktree_id=rec.worktree_id,
                            machine=rec.machine, event="released",
                            note="at-rest-reconciled", project=rec.repo,
                        )
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
        output._json_output({"applied": apply, "released": released, "count": len(released)})
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
        output._json_output(
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
        output._json_output({"orphaned": orphans, "count": len(orphans)})
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
    wt_id = worktree_identity._infer_worktree_id(worktree_id, config)
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return output._json_error(f"worktree not found: {wt_id}")
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
        output._json_output(ledger)
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


def _claims_transitive(args: argparse.Namespace, worktree_id: str | None) -> int:
    """``claims transitive [worktree_id]`` -- delegates to
    ``claims_transitive_cli`` (kept a separate module for the module-size
    cap; see that module's docstring for the full design rationale)."""
    return claims_transitive_cli.cmd_claims_transitive(
        args, worktree_id,
        infer_worktree_id=worktree_identity._infer_worktree_id,
        json_error=output._json_error,
        json_output=output._json_output,
    )


def _claims_history(args: argparse.Namespace, ref: str | None) -> int:
    """``claims history <ref>`` -- delegates to ``claims_history_cli``
    (kept a separate module for the module-size cap)."""
    return claims_history_cli.cmd_claims_history(
        args, ref, json_error=output._json_error, json_output=output._json_output,
    )
