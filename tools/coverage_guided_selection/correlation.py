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
network I/O, a separate explicit step -- see `fetch_baseline_asset`). See
`efforts/active/coverage-guided-ci/README.md`'s own Journal for how and why
this design was chosen (and later revised).

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

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

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

    Uses the **full** `measured_commit` SHA, never a truncated prefix: a
    shortened prefix risks two distinct commits colliding on the same
    release tag, which would silently overwrite ("`--clobber`") an
    unrelated commit's already-published baseline assets, and would leave
    every pointer that already names that tag resolving to the wrong
    payload.
    """
    return f"coverage-baselines-{measured_commit}"



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


class BaselineFetchError(RuntimeError):
    """Raised when a baseline's Release asset cannot be downloaded, read,
    or validated as a genuine, correctly-correlated baseline document."""


def fetch_baseline_asset(repo: str, pointer: dict, *, timeout_s: float = 300.0) -> dict:
    """Download and parse the **full** baseline document a `pointer`
    references.

    `pointer` is the small document `build_pointer`/
    `ancestor_resolution.resolve_nearest_baseline` produce (``schema``,
    ``plugin``, ``measured_commit``, ``release_tag``, ``asset``) -- never
    the full baseline itself; this is the one function in the package that
    actually fetches the real coverage map the pointer only ever points at.

    Performs real network I/O via ``gh release download``, bounded by
    `timeout_s` (matching `baseline.collect_baseline`'s own convention for
    bounding a subprocess call -- a stalled `gh` connection must not block
    the calling CI decision process indefinitely); raises
    `BaselineFetchError` on any failure -- a missing `gh` executable (an
    `OSError` `subprocess` itself would raise), a timed-out download, a
    non-string/empty pointer field (which would otherwise reach
    `subprocess`/`Path` and raise a raw `TypeError`), a missing
    release/asset, a non-UTF-8 or malformed-JSON payload, or a
    syntactically-valid-but-wrong document (not a dict, a
    non-mapping/absent `coverage`/`tests` or malformed nested entries
    within either (a non-mapping per-file coverage entry, a non-list/
    non-string-list per-line test-id list, or a non-mapping test record),
    a `generated_at` that isn't a
    timezone-aware, parseable ISO8601 timestamp -- a naive one would be
    interpreted in whichever timezone the *consuming* host happens to run
    in, making coverage age environment-dependent -- or whose own
    `plugin`/`measured_commit` don't match the pointer that named it -- the
    correlation invariant a pointer and its asset must agree on) -- rather
    than returning a partial/empty/mismatched baseline a caller could
    mistake for "nothing covered" or silently select against the wrong
    generation.

    Note: this only proves the asset agrees with the *pointer that named
    it*. It does NOT prove that pointer was itself resolved for the
    plugin a caller actually asked about -- `decide()` is responsible for
    that outer check (comparing the resolved pointer's own `plugin` against
    the plugin it resolved `resolve_nearest_baseline` for) before ever
    calling this function.
    """
    release_tag = pointer.get("release_tag")
    asset = pointer.get("asset")
    pointer_plugin = pointer.get("plugin")
    pointer_measured_commit = pointer.get("measured_commit")
    for name, value in (
        ("release_tag", release_tag), ("asset", asset),
        ("plugin", pointer_plugin), ("measured_commit", pointer_measured_commit),
    ):
        if not isinstance(value, str) or not value:
            raise BaselineFetchError(
                f"pointer's {name!r} must be a non-empty string, got {value!r}: {pointer!r}"
            )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(
                [
                    "gh", "release", "download", release_tag,
                    "--repo", repo, "--pattern", asset, "--dir", tmp, "--clobber",
                ],
                capture_output=True, text=True, check=False, timeout=timeout_s,
            )
            if out.returncode != 0:
                raise BaselineFetchError(
                    f"gh release download {release_tag} --pattern {asset} failed: "
                    f"{out.stderr.strip()}"
                )
            asset_path = Path(tmp) / asset
            try:
                content = asset_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise BaselineFetchError(
                    f"downloaded asset at {asset_path} could not be read as UTF-8 text: {error}"
                ) from error
    except subprocess.TimeoutExpired as error:
        raise BaselineFetchError(
            f"gh release download {release_tag} --pattern {asset} timed out after {timeout_s}s"
        ) from error
    except OSError as error:
        # subprocess.run itself raises OSError (e.g. FileNotFoundError) when
        # `gh` isn't on PATH at all -- must not bypass BaselineFetchError
        # and reach decide()'s caller as an unhandled exception.
        raise BaselineFetchError(f"failed to launch gh release download: {error}") from error

    try:
        baseline = json.loads(content)
    except json.JSONDecodeError as error:
        raise BaselineFetchError(
            f"downloaded asset {asset} is not valid JSON: {error}"
        ) from error

    if not isinstance(baseline, dict):
        raise BaselineFetchError(
            f"downloaded asset {asset} is not a JSON object (got {type(baseline).__name__})"
        )

    generated_at = baseline.get("generated_at")
    if not isinstance(generated_at, str):
        raise BaselineFetchError(
            f"downloaded asset {asset}'s generated_at is missing or not a string: {generated_at!r}"
        )
    try:
        parsed_generated_at = datetime.fromisoformat(generated_at)
    except ValueError as error:
        raise BaselineFetchError(
            f"downloaded asset {asset}'s generated_at is not a valid ISO8601 timestamp: "
            f"{generated_at!r} ({error})"
        ) from error
    if parsed_generated_at.tzinfo is None or parsed_generated_at.utcoffset() is None:
        raise BaselineFetchError(
            f"downloaded asset {asset}'s generated_at is not timezone-aware: {generated_at!r} "
            "-- a naive timestamp would be interpreted in whichever timezone the consuming "
            "host happens to run in"
        )

    for field_name in ("coverage", "tests"):
        value = baseline.get(field_name)
        if not isinstance(value, dict):
            raise BaselineFetchError(
                f"downloaded asset {asset}'s {field_name!r} is missing or not a mapping "
                f"(got {type(value).__name__})"
            )

    # Validate the NESTED shape too, not just the two top-level mappings --
    # a corrupt-but-shallow-valid payload (e.g. `coverage: {"f.py": []}`)
    # would otherwise pass here and only fail later with an unrelated raw
    # exception (`.items()` on a list inside
    # `ancestor_resolution.remap_or_invalidate_baseline`, or `.get()` on a
    # non-dict test record inside `fallback.compute_fallback_set`),
    # bypassing `decide()`'s documented fetch-failure fallback path.
    coverage = baseline["coverage"]
    for file_path, per_line in coverage.items():
        if not isinstance(file_path, str) or not isinstance(per_line, dict):
            raise BaselineFetchError(
                f"downloaded asset {asset}'s coverage entry for {file_path!r} is not a "
                f"line-number -> test-list mapping (got {type(per_line).__name__})"
            )
        for line_key, test_ids in per_line.items():
            if not isinstance(test_ids, list) or not all(isinstance(t, str) for t in test_ids):
                raise BaselineFetchError(
                    f"downloaded asset {asset}'s coverage[{file_path!r}][{line_key!r}] is not "
                    "a list of test-id strings"
                )
    tests = baseline["tests"]
    for test_id, record in tests.items():
        if not isinstance(test_id, str) or not isinstance(record, dict):
            raise BaselineFetchError(
                f"downloaded asset {asset}'s tests entry for {test_id!r} is not a mapping "
                f"(got {type(record).__name__})"
            )

    if baseline.get("plugin") != pointer_plugin:
        raise BaselineFetchError(
            f"downloaded asset's plugin {baseline.get('plugin')!r} does not match "
            f"pointer's plugin {pointer_plugin!r} -- correlation invariant violated"
        )
    if baseline.get("measured_commit") != pointer_measured_commit:
        raise BaselineFetchError(
            f"downloaded asset's measured_commit {baseline.get('measured_commit')!r} does not "
            f"match pointer's measured_commit {pointer_measured_commit!r} -- correlation "
            "invariant violated"
        )
    return baseline

