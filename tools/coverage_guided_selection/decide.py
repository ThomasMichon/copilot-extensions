"""Phase 3: one orchestrating, auditable decision.

Ties every Phase 1-3 piece together into the single question a CI run
actually needs answered: *given this plugin and this PR's own changed
lines, which tests should run, and why?*

1. `ancestor_resolution.resolve_nearest_baseline` -- find the nearest
   qualifying baseline pointer on `main` (no network I/O).
2. `correlation.fetch_baseline_asset` -- download that generation's full
   coverage map from its Release asset (network I/O).
3. `debt.assess_debt` -- is this resolved generation too stale to trust.
   If so, curate the fallback tier from the full baseline directly
   (step 6) and skip straight past remap/selection below -- there is no
   point remapping attribution this run has already decided not to trust.
4. `ancestor_resolution.remap_or_invalidate_baseline` -- otherwise, carry
   its attribution forward to the fork commit.
5. `selection.select_tests` -- diff-scoped selection against the changed
   lines, using the remapped attribution.
6. `fallback.compute_fallback_set` -- the safety-net tier, whenever step 3
   or step 5 trips it. Always curated from the **full**, un-remapped
   baseline -- remapping (step 4) drops coverage for exactly the files
   this diff touches, which would shrink the fallback universe precisely
   on the riskiest files.

Every path returns one `SelectionDecision`: which tests to run, which mode
produced them (`"selected"` or `"fallback"`), why, and which baseline
generation (if any) was used -- the vision's own "make the selection
auditable" Behavior. Pure orchestration; no new coverage-collection or
git-history logic of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    from ancestor_resolution import ResolvedBaseline, remap_or_invalidate_baseline, resolve_nearest_baseline
    from correlation import BaselineFetchError, fetch_baseline_asset, require_measured_commit
    from debt import assess_debt
    from fallback import compute_fallback_set
    from selection import select_tests
except ModuleNotFoundError:
    from tools.coverage_guided_selection.ancestor_resolution import (
        ResolvedBaseline, remap_or_invalidate_baseline, resolve_nearest_baseline,
    )
    from tools.coverage_guided_selection.correlation import (
        BaselineFetchError, fetch_baseline_asset, require_measured_commit,
    )
    from tools.coverage_guided_selection.debt import assess_debt
    from tools.coverage_guided_selection.fallback import compute_fallback_set
    from tools.coverage_guided_selection.selection import select_tests

#: Reason string for the one case with no coverage evidence to reason
#: about at all -- never silently selected as "nothing to run".
NO_BASELINE_AVAILABLE = "no_baseline_available"
#: Reason for a resolved pointer whose own `plugin` doesn't match the
#: plugin it was resolved for -- a misplaced/corrupt pointer file, never
#: silently trusted even though its asset might itself be internally
#: consistent.
POINTER_PLUGIN_MISMATCH_PREFIX = "pointer_plugin_mismatch: "
#: Reason prefix when a resolved baseline's own asset can't be fetched.
FETCH_FAILED_PREFIX = "fetch_failed: "
#: Reason prefix for the coverage-debt trigger (whole-selection fallback).
COVERAGE_DEBT_PREFIX = "coverage_debt: "
#: Reason prefix for a per-file/per-line selection trigger.
SELECTION_FALLBACK_PREFIX = "selection_fallback: "
#: Reason for a clean, fresh diff-scoped selection.
FRESH_SELECTION = "fresh diff-scoped selection"


@dataclass(frozen=True)
class SelectionDecision:
    """The one auditable record of how a CI run chose its tests.

    `selected_tests` is `None` precisely when there is no curated evidence
    at all to draw a subset from -- no baseline ever resolved, a resolved
    pointer's own `plugin` didn't match the one requested, or its asset
    couldn't be fetched -- the caller must run its own full/default test
    suite in that case, never interpret `None` as "run nothing". A real
    tuple (including a genuinely empty `()`) always means a curation step
    actually ran and produced that exact set, zero tests included (e.g. an
    exhausted runtime budget) -- a meaningfully different, evidenced
    outcome from "no evidence existed to curate from" at all.
    """

    mode: str  # "selected" | "fallback"
    selected_tests: tuple[str, ...] | None
    reason: str
    #: The resolved baseline's own `measured_commit` (the `dev` commit
    #: coverage was collected against), or `None` if no baseline resolved.
    baseline_generation: str | None
    #: Which `main` commit the pointer was read from, or `None`.
    baseline_commit_on_main: str | None
    #: `debt.DebtAssessment.as_dict()`, or `None` on any pre-assessment
    #: fallback path -- no baseline ever resolved, a resolved pointer's own
    #: `plugin` didn't match, or its Release asset couldn't be fetched
    #: (debt is meaningless without a baseline to measure staleness of).
    #: `debt is None` therefore does NOT by itself mean "no baseline
    #: resolved" -- check `mode`/`reason` for which case applies.
    debt: dict | None = None
    selection_fallback_reasons: tuple[dict, ...] = field(default_factory=tuple)
    fallback_set: dict | None = None

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "selected_tests": None if self.selected_tests is None else list(self.selected_tests),
            "reason": self.reason,
            "baseline_generation": self.baseline_generation,
            "baseline_commit_on_main": self.baseline_commit_on_main,
            "debt": self.debt,
            "selection_fallback_reasons": list(self.selection_fallback_reasons),
            "fallback_set": self.fallback_set,
        }


def decide(
    repo_root: Path,
    repo: str,
    plugin: str,
    fork_commit: str,
    changed_lines: dict,
    *,
    main_ref: str = "main",
    commit_volume_threshold: int | None = None,
    age_threshold_seconds: float | None = None,
    fallback_runtime_budget_s: float = 300.0,
    eligible_tests: frozenset[str] | None = None,
) -> SelectionDecision:
    """Decide which tests to run for `plugin` at `fork_commit`.

    `changed_lines` is the same shape `selection.select_tests` takes: a
    file path (relative the same way the baseline's own keys are) -> the
    list of line numbers the diff touched in it.

    Never raises for an *expected* "can't trust this" outcome (no
    qualifying baseline, a failed asset fetch, a stale/invalidated
    generation) -- each of those is a normal, auditable fallback reason,
    not an exception. Only a genuine git/plumbing failure inside the
    helpers this calls propagates.
    """
    resolved = resolve_nearest_baseline(repo_root, plugin, fork_commit, main_ref=main_ref)
    if resolved is None:
        return SelectionDecision(
            mode="fallback",
            selected_tests=None,
            reason=NO_BASELINE_AVAILABLE,
            baseline_generation=None,
            baseline_commit_on_main=None,
        )

    # `fetch_baseline_asset` only proves the DOWNLOADED asset agrees with
    # the pointer that named it -- it has no way to know which plugin this
    # caller actually asked about. A misplaced/corrupt pointer whose own
    # `plugin` field disagrees with the plugin `resolve_nearest_baseline`
    # was asked to resolve for must never be silently trusted just because
    # its own asset happens to be internally self-consistent.
    pointer_plugin = resolved.baseline.get("plugin")
    if pointer_plugin != plugin:
        return SelectionDecision(
            mode="fallback",
            selected_tests=None,
            reason=(
                f"{POINTER_PLUGIN_MISMATCH_PREFIX}resolved pointer's plugin "
                f"{pointer_plugin!r} does not match requested plugin {plugin!r}"
            ),
            baseline_generation=resolved.baseline.get("measured_commit"),
            baseline_commit_on_main=resolved.baseline_commit,
        )

    try:
        full_baseline = fetch_baseline_asset(repo, resolved.baseline)
    except BaselineFetchError as error:
        return SelectionDecision(
            mode="fallback",
            selected_tests=None,
            reason=f"{FETCH_FAILED_PREFIX}{error}",
            baseline_generation=resolved.baseline.get("measured_commit"),
            baseline_commit_on_main=resolved.baseline_commit,
        )

    measured_commit = require_measured_commit(full_baseline)
    generated_at = full_baseline.get("generated_at")
    assessment = assess_debt(
        repo_root, measured_commit, generated_at,
        head=fork_commit,
        commit_volume_threshold=commit_volume_threshold,
        age_threshold_seconds=age_threshold_seconds,
    )

    if assessment.exceeded:
        # Curate from the FULL earned baseline, never `remapped`: the
        # remap/invalidate step (below) drops coverage for exactly the
        # files this diff touches, so curating from it would shrink the
        # fallback universe precisely on the risky files that most need
        # coverage. Curation only needs the full, un-remapped per-test
        # coverage/cost data; remapped line coordinates matter only to
        # diff-scoped `select_tests`, not to `compute_fallback_set`.
        fb = compute_fallback_set(full_baseline, fallback_runtime_budget_s, eligible_tests=eligible_tests)
        return SelectionDecision(
            mode="fallback",
            selected_tests=fb.selected_tests,
            reason=f"{COVERAGE_DEBT_PREFIX}{'; '.join(assessment.reasons)}",
            baseline_generation=measured_commit,
            baseline_commit_on_main=resolved.baseline_commit,
            debt=assessment.as_dict(),
            fallback_set=fb.as_dict(),
        )

    remapped = remap_or_invalidate_baseline(
        repo_root,
        ResolvedBaseline(baseline=full_baseline, baseline_commit=resolved.baseline_commit),
        fork_commit,
    )

    sel = select_tests(remapped, changed_lines)
    if sel.fallback_triggered:
        # Same reasoning as the debt-exceeded branch above: curate from the
        # full baseline, not the remapped/invalidated one.
        fb = compute_fallback_set(full_baseline, fallback_runtime_budget_s, eligible_tests=eligible_tests)
        reasons_str = "; ".join(
            f"{r.file}:{r.line} {r.reason}" for r in sel.fallback_reasons
        )
        # UNION, never replace: `select_tests` can return real, attributed
        # selected_tests for a mixed diff (some changed lines attributed,
        # others not) alongside fallback_triggered=True for the
        # unattributed ones. Discarding `sel.selected_tests` here would
        # silently drop known-good coverage evidence for the attributed
        # lines just because a DIFFERENT line in the same diff forced a
        # fallback -- the fallback must only ever ADD safety-net coverage
        # for what's unattributed, never remove coverage already earned.
        combined_tests = tuple(sorted(set(sel.selected_tests) | set(fb.selected_tests)))
        return SelectionDecision(
            mode="fallback",
            selected_tests=combined_tests,
            reason=f"{SELECTION_FALLBACK_PREFIX}{reasons_str}",
            baseline_generation=measured_commit,
            baseline_commit_on_main=resolved.baseline_commit,
            debt=assessment.as_dict(),
            selection_fallback_reasons=tuple(sel.as_dict()["fallback_reasons"]),
            fallback_set=fb.as_dict(),
        )

    return SelectionDecision(
        mode="selected",
        selected_tests=sel.selected_tests,
        reason=FRESH_SELECTION,
        baseline_generation=measured_commit,
        baseline_commit_on_main=resolved.baseline_commit,
        debt=assessment.as_dict(),
    )
