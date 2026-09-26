"""``agent-worktrees claims fleet-audit`` (worktree-finality-and-obligations
Phase 6): a read-only fleet-wide inventory report, split into its own module
to keep ``claims_cli.py`` under the repo's 1000-line module-size cap."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import config as cfg
from . import git_ops, obligations, prune, tracking


def _core():
    from . import __main__ as core

    return core


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def cmd_fleet_audit(args: argparse.Namespace) -> int:
    """Read-only fleet-wide inventory: surfaces the four obligation
    categories the effort's own Plan names as needing a preview, in one
    report, so an operator (or a periodic audit) does not have to remember
    to check each one separately:

    - **legacy boolean follow-ups**: ``follow_up: true`` with no itemized
      ``follow_ups`` entries (pre-Phase-3 records) -- informational only; no
      automated apply exists (materializing one requires a human-written
      summary, a judgment call this command deliberately does not guess at).
    - **active effort bindings**: ``rec.active_effort`` set -- informational;
      an operator decides whether the binding is stale.
    - **at-rest claims**: any held claim in ``at-rest`` state -- the ONE
      category with an existing apply path, ``claims reconcile-at-rest``;
      this report points at it rather than duplicating its logic.
    - **stale-finalized**: ``status == "finalized"`` records whose
      (freshly, locally re-classified -- no network fetch) `cleanup_disposition`
      is no longer cleanable -- e.g. new commits landed on the branch, or a
      held claim/follow-up was added, since the record was finalized. Keyed
      off `cleanup_disposition` rather than the closure descriptor's
      `final` flag deliberately: `final` requires fresh (fetched) evidence
      to ever report `True`, which a no-fetch audit could never satisfy for
      ANY record -- `cleanup_disposition` has no such freshness gate and is
      the practical "has this record's invariant drifted" signal instead.

    Read-only: never mutates a record. ``--apply`` is intentionally NOT
    accepted here (unlike ``reconcile-at-rest``/``sweep``/``cleanup``) --
    each category either has its own dedicated apply command already, or
    requires a judgment call this report does not make for the operator.
    """
    tdir = cfg.tracking_dir()
    records = [r for r in tracking.list_records(tdir) if r.kind not in tracking.MANAGED_KINDS]

    legacy_follow_ups: list[str] = []
    active_effort_bindings: list[dict[str, str]] = []
    at_rest_claims: list[dict[str, str]] = []
    stale_finalized: list[dict[str, str]] = []

    for rec in records:
        if rec.follow_up and not rec.follow_ups:
            legacy_follow_ups.append(rec.worktree_id)
        if rec.active_effort is not None:
            active_effort_bindings.append({
                "worktree_id": rec.worktree_id,
                "path": rec.active_effort.path,
            })
        for claim in rec.resources:
            if claim.state == obligations.AT_REST:
                at_rest_claims.append({
                    "worktree_id": rec.worktree_id, "kind": claim.kind, "ref": claim.ref,
                })
        if rec.status != "finalized":
            continue
        if rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path, rec.branch, fetch=False,
            )
            info = _core()._apply_tracking_override(rec, info)
        else:
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
        disp = prune.cleanup_disposition(rec, info)
        # Deliberately keyed off `disp.cleanable`/`disp.bucket`, NOT the
        # closure descriptor's `final` -- this report never fetches, so
        # `final` (which requires BOTH upstream-containment AND open-claims
        # evidence to be independently CONFIRMED-fresh) would report
        # unconfirmed, and therefore never-final, for every single record
        # regardless of its actual state. `cleanup_disposition` has no such
        # freshness gate -- exactly the practical "has this record's
        # invariant drifted since it was finalized" signal this bullet asks
        # for.
        if not disp.cleanable:
            stale_finalized.append({
                "worktree_id": rec.worktree_id,
                "bucket": disp.bucket,
                "reason": disp.reason,
            })

    report = {
        "legacy_follow_ups": legacy_follow_ups,
        "active_effort_bindings": active_effort_bindings,
        "at_rest_claims": at_rest_claims,
        "stale_finalized": stale_finalized,
    }
    if args.json:
        _json_output(report)
        return 0

    print("Fleet audit (worktree-finality-and-obligations Phase 6, read-only):")
    print(f"  legacy boolean follow-ups: {len(legacy_follow_ups)}")
    for wt_id in legacy_follow_ups:
        print(f"    · {wt_id}")
    print(f"  active effort bindings: {len(active_effort_bindings)}")
    for b in active_effort_bindings:
        print(f"    · {b['worktree_id']}: {b['path']}")
    print(f"  at-rest claims: {len(at_rest_claims)} "
          f"(pass 'claims reconcile-at-rest --apply' to release)")
    for c in at_rest_claims:
        print(f"    · {c['worktree_id']}: {c['kind']} {c['ref']}")
    print(f"  finalized records no longer final: {len(stale_finalized)}")
    for s in stale_finalized:
        print(f"    · {s['worktree_id']}: {s['bucket']} -- {s['reason']}")
    return 0
