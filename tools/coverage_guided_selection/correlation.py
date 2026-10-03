"""Where a coverage baseline's correlation *pointer* lives on `main`, and
how it is correlated.

`main`'s tree carries one small, git-history-walkable **pointer** file per
plugin at `baseline_path_on_main` -- never the full per-line coverage map,
only `measured_commit` + `release_tag` + `asset` (a few hundred bytes,
regardless of plugin suite size). The actual per-line coverage data is
published separately as a **GitHub Release asset**, tagged on the
measured `dev` commit itself (see `release_tag_for`), decoupled from the
promotion pipeline's own `main`-side tag. A reader resolves the nearest
qualifying pointer via ordinary git history
(`ancestor_resolution.resolve_nearest_baseline`, no network I/O), then
fetches that generation's real coverage map from its Release asset (new
network I/O, a separate explicit step -- Phase 3 wiring, not yet
implemented here). See `efforts/active/coverage-guided-ci/README.md`'s own
Journal for how and why this design was chosen (and later revised).

This repo's existing `.github/release-pipeline-state.json` already records
`last_promotion.dev_head` -- the `dev` commit each `main` promotion was
measured/built against -- and is the authoritative cross-check. A
pointer's own embedded `measured_commit` is a self-contained, redundant
copy of that same fact: a reader needs only the one pointer file to know
what it was measured against, without also reading the pipeline state
file, while the two remaining consistent (the same promotion run that
commits `last_promotion.dev_head` to the pipeline state file is what also
writes the pointer with the same SHA) is a wiring invariant, not something
this module can enforce on its own.
"""

from __future__ import annotations

# `.github/` already hosts `release-pipeline-state.json` -- this directory
# lives alongside it rather than inventing a separate top-level location,
# since both are promotion-pipeline-owned, `main`-resident bookkeeping.
# Still holds one pointer file per plugin (see module docstring) -- the
# path/directory convention itself didn't change, only the file contents.
BASELINE_DIR_ON_MAIN = ".github/coverage-baselines"

#: Schema tag for the small pointer document committed at
#: `baseline_path_on_main(plugin)` -- distinguishes it from the much larger
#: full baseline document (`coverage_guided_selection.baseline`'s own
#: output) that a pointer only ever references, never embeds.
POINTER_SCHEMA = "copilot-extensions.coverage-baseline-pointer"


def baseline_path_on_main(plugin: str) -> str:
    """The repository-relative path a `plugin`'s latest baseline pointer
    lives at on `main`. One file per plugin, overwritten each promotion --
    prior generations remain inspectable through `main`'s own git history,
    the same way `release-pipeline-state.json` itself is versioned, rather
    than accumulating a separate file per generation.
    """
    return f"{BASELINE_DIR_ON_MAIN}/{plugin}.json"


def release_tag_for(measured_commit: str) -> str:
    """The GitHub Release tag a `measured_commit`'s full coverage-baseline
    assets are (or will be) published under.

    Deliberately keyed on `measured_commit` alone, **not** the promotion's
    own `main`-side tag (`promote-<timestamp>-<sha>`): that tag's own name
    often isn't knowable until after a real post-merge squash-commit lands
    on a protected `main` (see `tools/promote_release.py`'s own
    `candidate_branch` docstring), whereas `measured_commit` (the `dev`
    commit coverage was actually measured against) is known the moment
    collection finishes -- long before any promotion/merge decision. Using
    it directly means the Release can be published as soon as baselines
    are collected, independent of if/when/whether that `dev` commit's
    promotion itself lands, and both the workflow (which creates the
    Release) and this script (which embeds a pointer to it) can compute
    the identical tag name independently, with nothing to plumb between
    them. `.github/workflows/validate-and-promote.yml`'s own "Publish
    coverage baselines" step and `tools/promote_release.py`'s own
    `_write_coverage_baselines_into_scratch` each replicate this exact
    formula by hand (not imported -- see `promote_release.py`'s own
    `COVERAGE_BASELINES_DIR` comment for why) -- keep all three in sync if
    this ever changes.
    """
    return f"coverage-baselines-{measured_commit[:12]}"


def asset_name_for(plugin: str) -> str:
    """The Release-asset file name a `plugin`'s full baseline is uploaded
    as, under `release_tag_for`'s tag. One asset per plugin per release,
    matching the collection step's own uploaded artifact file name
    (`<plugin>.json`) so there is exactly one naming convention to track
    end to end, not two."""
    return f"{plugin}.json"


def build_pointer(plugin: str, measured_commit: str) -> dict:
    """The small pointer document committed at `baseline_path_on_main` --
    everything a later reader needs to find and fetch the real, full
    baseline, without ever embedding the (potentially huge) coverage map
    itself.
    """
    return {
        "schema": POINTER_SCHEMA,
        "plugin": plugin,
        "measured_commit": measured_commit,
        "release_tag": release_tag_for(measured_commit),
        "asset": asset_name_for(plugin),
    }


class BaselineCorrelationError(ValueError):
    """Raised when a baseline cannot be trusted as self-correlating."""


def require_measured_commit(baseline: dict) -> str:
    """Return `baseline["measured_commit"]`, or raise if it is missing.

    A baseline with no `measured_commit` is fine as a local/manual
    artifact (see `baseline.collect_baseline`'s own docstring), but is
    never eligible to be checked into `main`'s own correlation-bearing
    tree -- an uncorrelated baseline there would silently defeat the
    vision's own "baseline reachable from any fork point" Feature for
    every later reader. Applies equally to a full baseline document or the
    small pointer document above -- both carry this same field, under the
    same name, by design (see `build_pointer`).
    """
    measured_commit = baseline.get("measured_commit")
    if not measured_commit:
        raise BaselineCorrelationError(
            "baseline has no measured_commit -- cannot be checked into "
            f"{BASELINE_DIR_ON_MAIN} without a correlatable dev commit"
        )
    return measured_commit
