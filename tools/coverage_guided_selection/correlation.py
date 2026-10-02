"""Where a coverage baseline lives on `main`, and how it is correlated.

Realizes this effort's own 2026-10-01 Phase 0 storage/correlation decision
(see `efforts/active/coverage-guided-ci/README.md`'s Journal): a baseline
generation is checked into `main`'s own tree, piggybacking on the
promotion pipeline's existing commit-per-promotion + `promote-<timestamp>-
<sha>` tag, rather than a new GitHub-Release-asset mechanism. This module
owns only the **path convention and the correlation contract**; actually
writing to `main` during a real promotion run is Phase 1 (or a later
Phase 0 increment) -- wiring this into `validate-and-promote.yml` for real
is explicitly still open.

This repo's existing `.github/release-pipeline-state.json` already records
`last_promotion.dev_head` -- the `dev` commit each `main` promotion was
measured/built against -- and is the authoritative cross-check. A
baseline's own embedded `measured_commit` (see `baseline.py`) is a
self-contained, redundant copy of that same fact: a reader needs only the
one baseline file to know what it was measured against, without also
reading the pipeline state file, while the two remaining consistent (the
same promotion run that commits `last_promotion.dev_head` to the pipeline
state file is what also writes the baseline with the same SHA) is a
Phase 1 wiring invariant, not something this module can enforce on its
own.
"""

from __future__ import annotations

# `.github/` already hosts `release-pipeline-state.json` -- this directory
# lives alongside it rather than inventing a separate top-level location,
# since both are promotion-pipeline-owned, `main`-resident bookkeeping.
BASELINE_DIR_ON_MAIN = ".github/coverage-baselines"


def baseline_path_on_main(plugin: str) -> str:
    """The repository-relative path a `plugin`'s latest baseline generation
    lives at on `main`. One file per plugin, overwritten each promotion --
    prior generations remain inspectable through `main`'s own git history,
    the same way `release-pipeline-state.json` itself is versioned, rather
    than accumulating a separate file per generation.
    """
    return f"{BASELINE_DIR_ON_MAIN}/{plugin}.json"


class BaselineCorrelationError(ValueError):
    """Raised when a baseline cannot be trusted as self-correlating."""


def require_measured_commit(baseline: dict) -> str:
    """Return `baseline["measured_commit"]`, or raise if it is missing.

    A baseline with no `measured_commit` is fine as a local/manual
    artifact (see `baseline.collect_baseline`'s own docstring), but is
    never eligible to be checked into `main`'s own correlation-bearing
    tree -- an uncorrelated baseline there would silently defeat the
    vision's own "baseline reachable from any fork point" Feature for
    every later reader.
    """
    measured_commit = baseline.get("measured_commit")
    if not measured_commit:
        raise BaselineCorrelationError(
            "baseline has no measured_commit -- cannot be checked into "
            f"{BASELINE_DIR_ON_MAIN} without a correlatable dev commit"
        )
    return measured_commit
