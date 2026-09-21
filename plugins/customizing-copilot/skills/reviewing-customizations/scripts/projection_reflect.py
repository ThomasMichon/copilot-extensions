"""projection-reflect: deterministic-sync decision layer.

Phase 2 of ``efforts/active/ambient-guidance-navigability``: a generic
sync-automation recipe modeled on the facility's own proven ``config-reflect``
system (reflect/reconcile split, fail-closed producer, narrow bypass, domain-
deduped conflict dispatch, non-self-merging reconciler).

``instruction_projections.py``'s ``sync_repository()``/``scan_repository()``
already perform the mechanical work (render, write, lock, and detect every
drift/conflict class as a ``Finding``). This module adds the POLICY a
scheduled, non-agentic sync worker needs on top of that mechanism, so it can
decide whether a run is safe to fold into a stamp-labeled, bypass-eligible PR
or must instead route to conflict-dispatch:

* deterministic finding **classification** (fail-closed allowlist: only
  ``projection-missing`` and ``projection-source-update`` are plain drift
  that ``sync`` already resolved -- every other check name, including one
  this module has never seen before, requires conflict-dispatch);
* the **"did anything change" trigger** (``changed or lock_updated``, not
  ``changed`` alone -- a lock-only update, or a `scan` finding with no file
  change, must never be silently treated as a no-op);
* a **trusted-source allowlist** gate, keyed off the lock's own
  ``plugin@marketplace`` identity (never installed-payload trust alone --
  ``discover_enabled_sources`` resolves every repository-enabled
  marketplace, including third-party ones this repo did not author).

This module never renders, writes, or pushes anything itself -- it is a pure
decision layer over an already-produced ``Result`` (or two: `sync`'s and
`scan`'s). It intentionally does not depend on ``instruction_projections``'s
internals: callers pass plain findings/entries, so this stays trivially
testable and reusable if the lock/finding shape is ever renamed.

**Known gap (tracked, not solved here): immutable-pin verification.** The
existing lock schema records a plugin *version* string, not the immutable
upstream commit/release a projection was rendered from, so today's byte-exact
recompute only proves internal self-consistency (the checked-in file matches
its own lock entry -- which ``scan_repository`` already enforces as
``projection-local-modification``, correctly conflict-routed by this
module's allowlist), not that the *source* a reviewer re-fetches later is the
exact one the PR was rendered from. Closing that gap needs either a lock
schema extension or a marketplace-source resolver that can return a commit
SHA, and is deliberately left to a follow-up slice of this same effort (see
``efforts/active/ambient-guidance-navigability``'s Journal) rather than an ad
hoc guess that could itself become a false sense of reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

#: Finding check names ``scan_repository`` may emit that plain ``sync``
#: already resolves and that are safe for a deterministic worker to fold into
#: a bypass-eligible PR without a human/agentic decision. Every other check
#: name -- known today or introduced later -- is conflict-routed by default.
PLAIN_DRIFT_CHECKS = frozenset({"projection-missing", "projection-source-update"})


@dataclass(frozen=True)
class Classification:
    """The split of a scan's findings into plain drift vs. conflict-routed."""

    plain: tuple[object, ...]
    conflict: tuple[object, ...]

    @property
    def is_clean(self) -> bool:
        return not self.conflict


def classify_findings(findings: Iterable[object]) -> Classification:
    """Split findings by their ``.check`` name using the fail-closed allowlist.

    Any check name not explicitly in :data:`PLAIN_DRIFT_CHECKS` -- including
    one this module has never seen before -- is treated as conflict-routed.
    This is deliberate: a newly introduced check in
    ``instruction_projections.py`` must be reviewed and explicitly
    allowlisted here before a deterministic worker may silently resolve it.
    """
    plain: list[object] = []
    conflict: list[object] = []
    for finding in findings:
        check = getattr(finding, "check", None)
        if check in PLAIN_DRIFT_CHECKS:
            plain.append(finding)
        else:
            conflict.append(finding)
    return Classification(tuple(plain), tuple(conflict))


def has_actionable_change(*, changed: Iterable[str], lock_updated: bool) -> bool:
    """The worker's "did anything happen" trigger.

    ``sync`` can report an empty ``changed`` list with a lock-only update
    (e.g. a plugin version bump with no content change), and a no-op sync can
    still be paired with a `scan` that finds something -- callers must check
    findings separately (see :func:`bypass_decision`). This function only
    answers the narrow "did `sync` itself do anything" question; treating
    ``changed`` alone as the signal silently drops lock-only drift.
    """
    return bool(list(changed)) or bool(lock_updated)


def marketplace_of(plugin_identity: str) -> str:
    """Extract the marketplace name from a lock entry's ``plugin`` field.

    Lock entries carry ``"<plugin>@<marketplace>"``, an identity
    ``instruction_projections.py`` already validates on every entry -- this
    is the one place a projection's true origin (not just its plugin name)
    is recorded, so it is the correct key for a trusted-source allowlist.
    """
    _, _, marketplace = plugin_identity.partition("@")
    return marketplace


@dataclass(frozen=True)
class BypassDecision:
    """Whether a sync run's diff may land via a bypass-eligible PR.

    ``eligible=False`` with no findings and no changed/locked state simply
    means there is nothing to report at all -- the caller should not open a
    PR either way in that case; that is not itself a *bypass* refusal.
    """

    eligible: bool
    reasons: tuple[str, ...]


def bypass_decision(
    *,
    findings: Iterable[object],
    changed_lock_entries: Iterable[dict[str, object]],
    trusted_marketplaces: Iterable[str],
) -> BypassDecision:
    """Decide whether this run's diff may land via the bypass path.

    Fails closed on every conjunct, evaluated independently so every
    violation is reported (not just the first): no conflict-classified
    finding, and every changed lock entry's marketplace is in the trusted
    allowlist. An empty ``trusted_marketplaces`` iterable means nothing is
    trusted -- every changed source is then rejected, never treated as
    vacuously trusted.
    """
    reasons: list[str] = []
    classification = classify_findings(findings)
    if classification.conflict:
        checks = sorted({getattr(f, "check", "?") for f in classification.conflict})
        reasons.append(
            f"{len(classification.conflict)} finding(s) require conflict-dispatch: "
            + ", ".join(checks)
        )

    trusted = frozenset(trusted_marketplaces)
    untrusted_sources = sorted(
        {
            str(entry.get("plugin"))
            for entry in changed_lock_entries
            if marketplace_of(str(entry.get("plugin", ""))) not in trusted
        }
    )
    if untrusted_sources:
        reasons.append(
            "changed source(s) outside the trusted-source allowlist: "
            + ", ".join(untrusted_sources)
        )

    return BypassDecision(eligible=not reasons, reasons=tuple(reasons))
