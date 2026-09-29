"""Claim-handoff CLI dispatch extracted from ``claims_cli``."""

from __future__ import annotations

import argparse

from . import activity, claim_handoffs, output
from . import config as cfg


def _claim_handoff_actor(
    config: cfg.Config,
    explicit_worktree: str | None,
    *,
    infer_worktree_id,
    format_claim_ref,
) -> str:
    worktree_id = infer_worktree_id(explicit_worktree, config)
    if not worktree_id:
        raise claim_handoffs.ClaimHandoffError(
            "cannot infer the acting worktree; run inside it or pass --worktree"
        )
    project = config.repo_name or cfg.project_name()
    return format_claim_ref(config.machine, project, worktree_id)


def claims_handoff(
    args: argparse.Namespace,
    target: list[str],
    *,
    require_coordination_readiness,
    infer_worktree_id,
    format_claim_ref,
    json_error,
    json_output,
) -> int:
    """Dispatch claim-bundle offer/show/accept/decline/cancel."""
    if not target:
        msg = "claims handoff: missing action (offer|show|accept|decline|cancel)"
        if args.json:
            return json_error(msg, 2)
        output.err(msg)
        return 2
    action = target[0]
    if action not in {
        "offer", "show", "accept", "accept-source", "decline", "cancel"
    }:
        msg = (
            "claims handoff: unknown action "
            f"{action!r} (expected offer|show|accept|decline|cancel)"
        )
        if args.json:
            return json_error(msg)
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
                blocked = require_coordination_readiness(config, json_out=args.json)
                if blocked is not None:
                    return blocked
            actor = _claim_handoff_actor(
                config,
                getattr(args, "release_worktree", None),
                infer_worktree_id=infer_worktree_id,
                format_claim_ref=format_claim_ref,
            )
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
            elif action == "accept":
                if len(target) != 2:
                    raise claim_handoffs.ClaimHandoffError(
                        "claims handoff accept: usage 'accept <bundle-id>'"
                    )
                bundle = claim_handoffs.accept(
                    target[1],
                    actor=actor,
                    machine=config.machine,
                )
                created = None
            elif action == "accept-source":
                if len(target) != 2:
                    raise claim_handoffs.ClaimHandoffError(
                        "claims handoff accept-source: usage "
                        "'accept-source <bundle-id> --actor <consumer-ref>'"
                    )
                explicit_actor = str(getattr(args, "claim_actor", "") or "").strip()
                if not explicit_actor:
                    raise claim_handoffs.ClaimHandoffError(
                        "claims handoff accept-source requires --actor <consumer-ref>"
                    )
                bundle = claim_handoffs.accept_source(
                    target[1],
                    actor=explicit_actor,
                )
                created = None
            else:
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
            return json_error(str(exc))
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
    elif action == "accept":
        activity.log_event(
            "claim_handoff_accepted",
            worktree_id=bundle.consumer,
            bundle_id=bundle.bundle_id,
            source=bundle.source,
            refs=[claim["ref"] for claim in bundle.claims],
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
        json_output(payload)
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
