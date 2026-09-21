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
  Both ``sync``'s and ``scan``'s own findings feed that one decision --
  a sync-side failure (a rejected ownership conflict, a failed lock
  acquisition, a budget overrun) must never look like a clean, do-nothing
  run just because a subsequent scan has nothing new of its own to report.
  Likewise, a **lock-only update** (e.g. a plugin version bump that renders
  identical content, so no destination appears in ``sync``'s own `changed`
  list) still moves that source's locked identity -- this module diffs the
  lock's entries before and after the pass so the trusted-source and
  immutable-pin conjuncts are evaluated against it too, never silently
  skipped because no file byte happened to change.
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
* :func:`run_sync_pass` is a general-purpose library function: it takes an
  explicit ``trusted_marketplaces`` value from any caller (a test, or a
  scheduler that has already resolved consent) and performs no consent
  check itself. The CLI entry point (``main()``/``__main__``) is different:
  it is the actual consent-gated scheduled-worker surface, so it calls
  ``projection_reflect_consent.load_consent()`` first and refuses to run at
  all -- no mutation, no trust decision -- when this repo has no live,
  committed opt-in; when consent is present, the CLI derives
  ``trusted_marketplaces`` from that consent object, never from a
  caller-suppliable command-line flag.
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
import projection_reflect_consent
import scan_plugin_sources


def _policy_relevant_destinations(
    *,
    changed: Iterable[str],
    entries_before: Mapping[str, dict[str, object]],
    entries_after: Mapping[str, dict[str, object]],
) -> set[str]:
    """Every destination whose lock entry moved, not just content-changed ones.

    ``sync``'s own ``changed`` list only names destinations whose *rendered
    file bytes* differ -- but a lock-only update (any lock-entry field
    change not reflected in the rendered content) moves that source's
    locked identity too, and must not be silently exempted from the
    trusted-source/immutable-pin conjuncts just because no file byte
    happened to change. Comparing the lock's own entries before and after
    the pass catches that case precisely, without guessing at *why* an
    entry moved.
    """
    relevant = set(changed)
    for destination, after_entry in entries_after.items():
        if entries_before.get(destination) != after_entry:
            relevant.add(destination)
    return relevant


@dataclass(frozen=True)
class SyncOutcome:
    """The single, complete decision from one :func:`run_sync_pass` call.

    A caller reads exactly these properties to decide its next action --
    never re-invoking this tool to learn more about the same run:

    * ``needs_pr`` false: nothing to report; do not open a PR at all.
    * ``needs_pr`` true and ``bypass_eligible`` true: the sync's own diff is
      safe to land via a bypass-eligible, auto-mergeable PR.
    * ``needs_pr`` true, ``bypass_eligible`` false, and
      ``needs_conflict_dispatch`` true: open the PR (it still carries
      whatever ``sync`` resolved) and route it through conflict-dispatch --
      a real conflict-classified finding (e.g. a hand-edited managed
      projection, or a failed sync) needs the reconciler's attention.
    * ``needs_pr`` true, ``bypass_eligible`` false, and
      ``needs_conflict_dispatch`` false: open a normal, review-only PR
      without dispatching anything -- the bypass was refused for a reason
      no reconciler can resolve (an untrusted-marketplace source, or a
      missing/malformed immutable-pin), not because of an actual conflict
      finding.
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
        # Only a real conflict-classified finding (per
        # `classify_findings`'s fail-closed allowlist) is reconciler-
        # resolvable and warrants dispatch. A bypass refusal for an
        # untrusted marketplace or a missing/malformed immutable pin is
        # correctly review-only -- there is no hand-edit or git-level
        # conflict for a reconciler to act on, so it must not be
        # dispatched merely because `bypass.eligible` is False.
        return self.needs_pr and bool(
            reflect.classify_findings(self.findings).conflict
        )

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
    resolve_pins: Callable[[list[object]], Mapping[str, str] | None] | None = None,
    refresh: Callable[[], None] | None = None,
) -> SyncOutcome:
    """Run one complete sync -> scan -> decide pass; return the one outcome.

    ``sync_repository_locked`` is the only mutation this function performs
    (it writes/locks whatever it can safely resolve); ``scan_repository``
    then validates the result and reports everything sync could not
    resolve. Both run exactly once per call, **inside the same held
    per-repository lock** as the before/after lock-entry reads -- so a
    concurrent worker's own sync can never interleave between this pass's
    sync and its scan/lock-read and get misattributed to this call's
    outcome. This function is otherwise a pure composition of
    ``instruction_projections`` and ``projection_reflect``, so re-running it
    on an unchanged repo is idempotent and produces the same (empty)
    outcome, never an accumulating side effect.

    ``resolve_pins`` (preferred over a precomputed ``pinned_commits``) is
    called with the same ``sources`` list *after* ``refresh`` and *inside*
    the held lock, immediately before the locked sync -- resolving pins any
    earlier (e.g. before ``refresh``, or entirely outside this function)
    leaves a window where a refresh or a concurrent update changes a
    directory-marketplace payload after its commit was captured, so the
    stale-but-well-formed SHA in a precomputed map would still pass
    ``bypass_decision``'s pin conjunct even though it no longer describes
    what this pass actually renders. When both ``resolve_pins`` and
    ``pinned_commits`` are given, ``resolve_pins`` wins for this call (a
    caller doing fresh resolution should not also pass a possibly-stale
    map); ``pinned_commits`` alone remains for callers (tests, or a
    resolver-free scheduler) that accept a caller-supplied map without this
    revalidation. A ``resolve_pins`` call that returns ``None`` is
    normalized to an empty map, never treated as "no ``pinned_commits`` was
    ever supplied": a resolver failure must still leave the pin conjunct
    active (fail-closed, review-only for every changed source), not
    silently disable pin enforcement for a caller that explicitly opted
    into it.
    """
    if refresh is not None:
        refresh()

    sources = list(sources)
    try:
        root = projections.validate_repository_root(repo_root)
    except ValueError as exc:
        result = projections.Result(operation="sync")
        result.add(projections.BLOCKING, "projection-root", repo_root, str(exc))
        decision = reflect.bypass_decision(
            findings=result.findings,
            changed_lock_entries=[],
            trusted_marketplaces=trusted_marketplaces,
            pinned_commits=pinned_commits,
        )
        return SyncOutcome(
            changed=(), lock_updated=False, findings=tuple(result.findings),
            bypass=decision,
        )

    with_lock = projections.repository_sync_lock(root)
    try:
        with_lock.__enter__()
    except (BlockingIOError, OSError) as exc:
        result = projections.Result(operation="sync")
        result.add(
            projections.BLOCKING,
            "projection-sync-lock",
            root,
            f"cannot acquire repository synchronization lock: {exc}",
        )
        decision = reflect.bypass_decision(
            findings=result.findings,
            changed_lock_entries=[],
            trusted_marketplaces=trusted_marketplaces,
            pinned_commits=pinned_commits,
        )
        return SyncOutcome(
            changed=(), lock_updated=False, findings=tuple(result.findings),
            bypass=decision,
        )
    try:
        if resolve_pins is not None:
            # A resolver that returns None (a transient failure, or simply
            # "nothing resolved") must NOT be treated as "no pinned_commits
            # was ever supplied" -- bypass_decision() reads pinned_commits
            # is None as the opt-out/default path, which would silently
            # disable the pin conjunct entirely for a caller that
            # explicitly opted into it via resolve_pins. Normalize to an
            # empty map instead: the conjunct stays active, and an empty
            # map correctly treats every changed source as unpinned
            # (fail-closed, review-only) rather than vacuously trusted.
            resolved = resolve_pins(sources)
            pinned_commits = resolved if resolved is not None else {}
        entries_before = projections.load_lock_entries(root)
        sync_result = projections.sync_repository_locked(root, sources)
        scan_result = projections.scan_repository(root, sources)
        entries_after = projections.load_lock_entries(root)
    finally:
        with_lock.__exit__(None, None, None)

    # Policy-relevant destinations are `sync`'s own changed-content list
    # *plus* any destination whose lock entry differs before vs. after --
    # a lock-only update (e.g. a plugin version bump with identical
    # rendered content) leaves `changed` empty but still moves that
    # source's identity, and must not silently skip the trust/pin
    # conjuncts below just because no file byte changed.
    policy_relevant = _policy_relevant_destinations(
        changed=sync_result.changed,
        entries_before=entries_before,
        entries_after=entries_after,
    )
    changed_lock_entries = [
        entries_after[destination]
        for destination in sorted(policy_relevant)
        if destination in entries_after
    ]

    # `sync`'s own findings (e.g. a failed-to-acquire sync lock, a rejected
    # ownership conflict, or a budget overrun) must feed the same decision
    # as `scan`'s: a sync failure followed by an otherwise-clean scan must
    # never look like a successful no-op just because scan itself found
    # nothing new to report.
    all_findings = tuple(sync_result.findings) + tuple(scan_result.findings)

    decision = reflect.bypass_decision(
        findings=all_findings,
        changed_lock_entries=changed_lock_entries,
        trusted_marketplaces=trusted_marketplaces,
        pinned_commits=pinned_commits,
    )

    return SyncOutcome(
        changed=tuple(sync_result.changed),
        lock_updated=sync_result.lock_updated,
        findings=all_findings,
        bypass=decision,
    )


def _emit_error(message: str, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}, indent=2, sort_keys=True))
    else:
        print(f"error: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser()
    if not root.is_dir():
        _emit_error(f"{root} is not a directory", as_json=args.json)
        return 2

    # The CLI is the consent-gated scheduled-worker entry point: it must
    # never mutate the repo, and never derive its trusted-source allowlist
    # from a caller-suppliable flag, without this repo's own live, committed
    # opt-in (see projection_reflect_consent.py and the
    # setting-up-instruction-sync-worker skill). `run_sync_pass()` itself
    # stays a general-purpose library function that any caller (including a
    # test) may drive with an explicit trusted_marketplaces value; this gate
    # belongs at the CLI boundary, not inside the pure composition. There is
    # deliberately no `--installed-root`-style override here: allowing the
    # bypass-eligible path to source projections from a caller-chosen
    # payload root (rather than the repo's own settings-resolved,
    # consent-trusted marketplaces) would let a substituted, unverified
    # payload tree ride the same auto-merge surface a real trusted source
    # gets.
    consent = projection_reflect_consent.load_consent(root)
    if consent is None:
        consent_path = "/".join(projection_reflect_consent.CONSENT_PATH_PARTS)
        _emit_error(
            f"no live projection-reflect consent found ({consent_path}); "
            "refusing to sync -- see the setting-up-instruction-sync-worker "
            "skill",
            as_json=args.json,
        )
        return 2

    try:
        projections.validate_repository_root(root)
        # discover_enabled_sources() already hardcodes include_user=False
        # and include_local=False internally when it calls
        # assemble_enabled_plugins() -- machine-global (~/.copilot/
        # settings.json) and repo-local-untracked (settings.local.json)
        # layers are unconditionally excluded here, regardless of caller;
        # only this repo's own committed settings can enable a source for
        # this consent-gated, bypass-capable path. Covered by
        # test_instruction_projections.py's discover_enabled_sources
        # settings-layer test (repo-committed plugin included,
        # user/local-only plugin excluded).
        sources = projections.discover_enabled_sources(root, require_trust=False)
    except ValueError as exc:
        _emit_error(str(exc), as_json=args.json)
        return 2

    # Only resolve/enforce pins when this repo's consent explicitly opts
    # in (require_immutable_pin, default False): most adopters sync
    # externally-installed marketplace plugins, which today's resolver
    # cannot pin at all -- enforcing it unconditionally would silently
    # disable the bypass path entirely for that common case. A source the
    # resolver can't pin is simply absent from the map, so
    # bypass_decision's pin conjunct correctly treats it as unpinned
    # (fail-closed, review-only) rather than silently exempting it.
    # Passed as resolve_pins (not a precomputed pinned_commits map) so
    # run_sync_pass resolves it after refresh and inside its held lock --
    # never against a payload snapshot taken before either.
    resolve_pins = (
        scan_plugin_sources.resolve_pinned_commits
        if consent.require_immutable_pin
        else None
    )

    outcome = run_sync_pass(
        root,
        sources,
        trusted_marketplaces=consent.trusted_marketplaces,
        resolve_pins=resolve_pins,
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
        elif outcome.needs_conflict_dispatch:
            print(
                f"[CONFLICT] {len(outcome.changed)} destination(s) changed, "
                f"{len(outcome.findings)} finding(s); route to "
                "conflict-dispatch:"
            )
            for reason in outcome.bypass.reasons:
                print(f"  - {reason}")
        else:
            print(
                f"[REVIEW] {len(outcome.changed)} destination(s) changed; "
                "not conflict-classified, but not bypass-eligible either:"
            )
            for reason in outcome.bypass.reasons:
                print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
