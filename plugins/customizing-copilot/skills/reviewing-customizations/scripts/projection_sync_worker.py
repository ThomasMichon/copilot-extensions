#!/usr/bin/env python3
"""projection-sync-worker: the one-shot deterministic sync tool.

Phase 2 of ``efforts/active/ambient-guidance-navigability``: the "given a
consumer repo, do everything a scheduled/non-agentic run needs to decide in
one pass" tool the Plan calls for. ``instruction_projections.py`` provides
the mechanism (render/write/lock via ``sync_repository``, validate/compare
via ``scan_repository``) and ``projection_reflect.py`` provides the policy
(classification, the actionable-change trigger, the trusted-source and
immutable-pin conjuncts). This module is the missing piece that composes
both into a **single deterministic pass with a single outcome**, so a caller
never needs more than one invocation -- and therefore never more than one
PR -- to fully resolve one round of upstream change:

* :func:`run_sync_pass` runs ``sync`` (the only mutation: it writes/locks
  whatever it can safely resolve) then ``scan`` (validates the result and
  reports everything it cannot resolve) exactly once, and returns a single
  :class:`SyncOutcome` that already carries the complete decision --
  whether there is nothing to report, a bypass-eligible diff, or a diff that
  needs conflict-dispatch. A caller branches on that outcome's properties;
  it never has to re-run this tool to "finish" a run that already ran.
* This module still performs **no git or PR/dispatch operations** of its
  own (consistent with ``projection_reflect.py``'s own boundary) -- those
  are the calling scheduler's job (Phase 5, per-adopting-repo, out of this
  repo's own scope). What this module guarantees is that the *decision*
  those git-side actions act on is always complete and final after one call,
  never requiring a second round of this tool's own logic to discover more
  work the first call missed.
* Refreshing installed plugin payloads (fetching whatever a marketplace's
  enabled plugins currently publish) is host/environment-specific and is
  therefore the caller's responsibility too, via the optional ``refresh``
  callback run before the sync/scan pass -- never implicit, and never
  retried mid-pass (a flaky refresh should fail the whole pass rather than
  silently sync against a half-refreshed source set).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

import instruction_projections as projections
import projection_reflect as reflect


def _load_lock_entries(repo_root: Path) -> list[dict[str, object]]:
    """Best-effort read of the current lock's projection entries.

    Called only right after a ``sync_repository`` call in the same process,
    against a lock ``sync``/``scan`` just validated -- a missing or
    unparsable lock at this point simply yields no entries (the caller's
    ``changed``/finding lists remain the source of truth for whether
    anything happened; this is only used to look up per-destination lock
    fields ``bypass_decision`` needs).
    """
    lock_path = repo_root.joinpath(*projections.LOCK_RELATIVE.parts)
    try:
        raw = lock_path.read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if not isinstance(document, dict):
        return []
    entries = document.get("projections")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


@dataclass(frozen=True)
class SyncOutcome:
    """The single, complete decision from one :func:`run_sync_pass` call.

    A caller reads exactly these properties to decide its next action --
    never re-invoking this tool to learn more about the same run:

    * ``needs_pr`` false: nothing to report; do not open a PR at all.
    * ``needs_pr`` true and ``bypass_eligible`` true: the sync's own diff is
      safe to land via a bypass-eligible, auto-mergeable PR.
    * ``needs_pr`` true and ``bypass_eligible`` false: open the PR (it still
      carries whatever ``sync`` resolved) but route it through
      conflict-dispatch instead of auto-merging -- ``bypass.reasons`` names
      every violated conjunct.
    """

    changed: tuple[str, ...]
    lock_updated: bool
    findings: tuple[object, ...]
    bypass: reflect.BypassDecision

    @property
    def has_actionable_change(self) -> bool:
        return reflect.has_actionable_change(
            changed=self.changed, lock_updated=self.lock_updated
        )

    @property
    def needs_pr(self) -> bool:
        # Matches the Plan's "only when there is truly nothing to report
        # does the worker skip opening a PR": a scan finding with no file
        # change at all still warrants a PR (or at least a report), even
        # when sync itself did nothing.
        return self.has_actionable_change or bool(self.findings)

    @property
    def bypass_eligible(self) -> bool:
        return self.needs_pr and self.bypass.eligible

    @property
    def needs_conflict_dispatch(self) -> bool:
        return self.needs_pr and not self.bypass.eligible

    def to_dict(self) -> dict[str, object]:
        return {
            "changed": list(self.changed),
            "lockUpdated": self.lock_updated,
            "findingCount": len(self.findings),
            "needsPr": self.needs_pr,
            "bypassEligible": self.bypass_eligible,
            "needsConflictDispatch": self.needs_conflict_dispatch,
            "bypassReasons": list(self.bypass.reasons),
        }


def run_sync_pass(
    repo_root: Path,
    sources: Iterable[object],
    *,
    trusted_marketplaces: Iterable[str],
    pinned_commits: Mapping[str, str] | None = None,
    refresh: Callable[[], None] | None = None,
) -> SyncOutcome:
    """Run one complete sync -> scan -> decide pass; return the one outcome.

    ``sync_repository`` is the only mutation this function performs (it
    writes/locks whatever it can safely resolve); ``scan_repository`` then
    validates the result and reports everything sync could not resolve.
    Both run exactly once per call -- this function is otherwise a pure
    composition of ``instruction_projections`` and ``projection_reflect``,
    so re-running it on an unchanged repo is idempotent and produces the
    same (empty) outcome, never an accumulating side effect.
    """
    if refresh is not None:
        refresh()

    sources = list(sources)
    sync_result = projections.sync_repository(repo_root, sources)
    scan_result = projections.scan_repository(repo_root, sources)

    changed_lock_entries: list[dict[str, object]] = []
    if sync_result.changed:
        changed_destinations = frozenset(sync_result.changed)
        changed_lock_entries = [
            entry
            for entry in _load_lock_entries(repo_root)
            if entry.get("destination") in changed_destinations
        ]

    decision = reflect.bypass_decision(
        findings=scan_result.findings,
        changed_lock_entries=changed_lock_entries,
        trusted_marketplaces=trusted_marketplaces,
        pinned_commits=pinned_commits,
    )

    return SyncOutcome(
        changed=tuple(sync_result.changed),
        lock_updated=sync_result.lock_updated,
        findings=tuple(scan_result.findings),
        bypass=decision,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument(
        "--trusted-marketplace",
        action="append",
        default=[],
        dest="trusted_marketplaces",
        help="a marketplace name to trust for the bypass allowlist "
        "(repeatable; default: none, so every source is review-only)",
    )
    parser.add_argument(
        "--installed-root",
        type=Path,
        help="override the installed plugin payload root",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    try:
        projections.validate_repository_root(root)
        sources = projections.discover_enabled_sources(
            root,
            installed_root=(
                args.installed_root.expanduser().resolve()
                if args.installed_root is not None
                else None
            ),
            require_trust=False,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    outcome = run_sync_pass(
        root, sources, trusted_marketplaces=args.trusted_marketplaces
    )
    if args.json:
        print(json.dumps(outcome.to_dict(), indent=2, sort_keys=True))
    else:
        if not outcome.needs_pr:
            print("[OK] nothing to sync")
        elif outcome.bypass_eligible:
            print(
                f"[BYPASS] {len(outcome.changed)} destination(s) changed; "
                "safe to auto-merge"
            )
        else:
            print(
                f"[CONFLICT] {len(outcome.changed)} destination(s) changed, "
                f"{len(outcome.findings)} finding(s); route to "
                "conflict-dispatch:"
            )
            for reason in outcome.bypass.reasons:
                print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
