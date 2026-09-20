#!/usr/bin/env python3
"""Enforce a hard per-file line-count cap on source modules, with a
shrink-only baseline for pre-existing offenders.

A single module growing without bound (queue.py's ~7,200 lines is the
motivating case, spotted mid Phase-10 work on `review-automation-reliability`
#2357) is a componentization failure this repo had no automated guard
against. This check makes the standard real:

* **Any tracked, non-test ``*.py`` file** (test files are exempt --
  ``TESTING.md`` already directs "split by behavioral contract, not
  arbitrary line count," a different rule for a different failure mode)
  must not exceed :data:`CAP_LINES` lines.
* Because dozens of pre-existing files already exceed that cap (some by an
  order of magnitude), a **shrink-only baseline**
  (``tools/module-size-baseline.json``) grandfathers each one in at its
  *current* line count as a temporary ceiling. The guard still fails if any
  baselined file grows even one line further, or if any non-baselined file
  newly crosses :data:`CAP_LINES` -- so the guard has real teeth against
  *new* growth from day one, without requiring an immediate rewrite of
  every offender. Shrinking a file below its baselined ceiling is always
  allowed and never itself a failure.

Widening a file's ceiling is a **manual, reviewed edit** to the baseline
JSON in the ordinary case, never something an ordinary PR run of this script
does automatically -- growth past the grandfathered size must be a
conscious, visible decision, not a silent side effect of running a refresh.
Plain ``--refresh-baseline`` only ever *lowers* an existing entry (when a
file has shrunk) or *removes* one entirely (when a file has shrunk to or
below the cap); it never raises an entry, even if a file has grown -- that
case is a guard failure to fix, not baseline noise to absorb.

The one exception is the opt-in ``--allow-widen`` flag:
concurrent-agent development can legitimately grow several already-baselined
files within the same short window (a shared module touched by parallel,
unrelated PRs), which then forces every subsequent PR -- even ones that never
touched the grown file -- to carry an unrelated manual baseline-widening
diff just to pass CI. ``--refresh-baseline --allow-widen`` additionally
*raises* a ceiling up to a file's current size when it is over its recorded
ceiling. This is still never silent: it is restricted by convention (see
``.github/workflows/module-size-baseline-widen.yml``) to a scheduled/
post-merge run directly against ``main`` after the fact, producing its own
reviewable PR -- **never** something a PR branch's own CI run does to its
own diff, which would let a PR silently launder its own growth past review.

**PR-time scoping (``--changed-since REF``).** ``--allow-widen`` closes the
gap *after* it opens, but there is still a window between another PR's merge
growing a shared file past its ceiling and the widen job's own PR landing --
during which *every* PR's ordinary (non-``--allow-widen``) CI run sees the
guard fail, even one that never touched the grown file (a PR is blamed for
growth it did not cause and cannot fix without an unrelated, out-of-scope
diff). ``--changed-since REF`` scopes enforcement to exactly the files this
invocation's own diff touches (``git diff --name-only REF...HEAD``, i.e.
relative to the merge-base with ``REF`` -- unaffected by how far ``REF``'s
branch has since moved). A file this PR never touched is fully exempt from
`check()` here regardless of its current size; a file it does touch is still
held to the ordinary cap/ceiling rule, unchanged. This flag only ever
*narrows* which files `check()` considers -- it never changes the pass/fail
rule for a file it does consider, and it is meaningless (and rejected) with
``--refresh-baseline``, whose whole job is auditing the *entire* tree's
baseline, never a PR's own diff scope.

Usage::

    python tools/check-module-size.py                  # enforce (pre-push/CI push/dispatch)
    python tools/check-module-size.py --changed-since REF  # enforce, PR-diff-scoped (CI pull_request)
    python tools/check-module-size.py --refresh-baseline  # tighten after shrinking a file
    python tools/check-module-size.py --refresh-baseline --allow-widen  # post-merge only; see above

Exit code 0 = conformant, 1 = a file exceeds its cap/ceiling.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO / "tools" / "module-size-baseline.json"

#: The hard cap for any file with no baseline entry. Chosen well below the
#: worst pre-existing offenders (some exceed 9,000 and even 28,000 lines) --
#: this is the ceiling new/untouched-by-debt files must meet, not a
#: description of the status quo.
CAP_LINES = 1000

_TEST_PATH_MARKERS = ("/tests/",)
_TEST_FILENAME_PREFIXES = ("test_",)
_TEST_FILENAMES = {"conftest.py"}


def _is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if name in _TEST_FILENAMES or name.startswith(_TEST_FILENAME_PREFIXES):
        return True
    return any(marker in f"/{path}" for marker in _TEST_PATH_MARKERS)


def _tracked_py_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        line for line in out.stdout.splitlines() if line and not _is_test_file(line)
    ]


def _changed_py_files(base_ref: str) -> set[str]:
    """Files this branch's own commits touch, relative to its merge-base with
    ``base_ref`` -- unaffected by how far ``base_ref``'s own branch has moved
    since (triple-dot diff), so a PR is never blamed for a file it never
    touched."""
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD", "--", "*.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return {line for line in out.stdout.splitlines() if line and not _is_test_file(line)}


def _line_count(path: str) -> int:
    text = (REPO / path).read_text(encoding="utf-8", errors="replace")
    if not text:
        return 0
    # A trailing newline should not count as an extra, empty final line.
    return len(text.splitlines())


def _load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{BASELINE_PATH}: expected a JSON object")
    return {str(k): int(v) for k, v in data.items()}


def _write_baseline(baseline: dict[str, int]) -> None:
    ordered = dict(sorted(baseline.items()))
    BASELINE_PATH.write_text(
        json.dumps(ordered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def check(baseline: dict[str, int], *, only_paths: set[str] | None = None) -> list[str]:
    """Return one message per file exceeding its cap/ceiling.

    ``only_paths``, when given, scopes enforcement to that set (see
    ``--changed-since`` above) -- a file outside it is skipped entirely,
    regardless of its current size.
    """
    violations: list[str] = []
    for path in sorted(_tracked_py_files()):
        if only_paths is not None and path not in only_paths:
            continue
        lines = _line_count(path)
        ceiling = baseline.get(path, CAP_LINES)
        if lines > ceiling:
            if path in baseline:
                violations.append(
                    f"{path}: {lines} lines, exceeds its grandfathered ceiling "
                    f"of {ceiling} (baseline is shrink-only -- split the module, "
                    "or make a deliberate, reviewed edit to "
                    "tools/module-size-baseline.json to widen it)"
                )
            else:
                violations.append(
                    f"{path}: {lines} lines, exceeds the {CAP_LINES}-line cap "
                    "-- split this module into smaller components"
                )
    return violations


def refresh_baseline(
    baseline: dict[str, int], *, allow_widen: bool = False,
) -> dict[str, int]:
    """Tighten (and, with ``allow_widen``, ratchet up) the baseline.

    Default behaviour never widens -- see the module docstring. With
    ``allow_widen=True``, a baselined file that has grown past its recorded
    ceiling has that ceiling raised to its current size instead of being left
    as a guard failure. Shrinking always wins over widening for the same file
    in the same call (a file can't have both happened).

    ``allow_widen`` only ever ratchets an EXISTING baseline entry -- it never
    newly grandfathers a file that has never been baselined before, even if
    that file now exceeds the cap. A brand-new offender is a genuinely new
    hard-cap violation (split it or, per the module docstring, a deliberate
    manual JSON edit), never something the automated, main-only widen job
    should silently absorb; that would let ordinary (non-widen) growth past
    the cap sneak in through the automation's own back door. Plain
    ``refresh_baseline`` (``allow_widen=False``) keeps its prior behaviour of
    recording a new offender, since it is always a manual, deliberate,
    reviewed invocation.
    """
    updated = dict(baseline)
    for path in sorted(_tracked_py_files()):
        lines = _line_count(path)
        if path in updated:
            if lines <= CAP_LINES:
                del updated[path]  # graduated: no longer needs grandfathering
            elif lines < updated[path]:
                updated[path] = lines  # shrunk: lock in the improvement
            elif allow_widen and lines > updated[path]:
                updated[path] = lines  # grown: ratchet the ceiling up (opt-in only)
            # else: unchanged, or grown without --allow-widen -- never raised
            # here; a grown file still fails `check()` and must be fixed or
            # manually widened.
        elif lines > CAP_LINES and not allow_widen:
            updated[path] = lines  # newly discovered offender (manual runs only)
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-baseline",
        action="store_true",
        help="Rewrite the baseline, tightening any shrunk entries (never widens).",
    )
    parser.add_argument(
        "--allow-widen",
        action="store_true",
        help=(
            "With --refresh-baseline, also raise a grown file's ceiling to its "
            "current size. Post-merge/main-only by convention -- never run this "
            "against a PR branch's own diff (see module docstring)."
        ),
    )
    parser.add_argument(
        "--changed-since",
        metavar="REF",
        help=(
            "Scope enforcement to files this branch's own commits touch "
            "(git diff --name-only REF...HEAD) -- for a PR-time CI run, so a "
            "PR is never blocked by growth in a file it never touched. "
            "Mutually exclusive with --refresh-baseline."
        ),
    )
    args = parser.parse_args()
    if args.allow_widen and not args.refresh_baseline:
        parser.error("--allow-widen requires --refresh-baseline")
    if args.changed_since and args.refresh_baseline:
        parser.error("--changed-since is incompatible with --refresh-baseline")

    baseline = _load_baseline()

    if args.refresh_baseline:
        updated = refresh_baseline(baseline, allow_widen=args.allow_widen)
        if updated != baseline:
            _write_baseline(updated)
            print(f"[OK] refreshed {BASELINE_PATH.relative_to(REPO)}")
        else:
            print("[OK] baseline already up to date")
        return 0

    only_paths = _changed_py_files(args.changed_since) if args.changed_since else None
    violations = check(baseline, only_paths=only_paths)
    if violations:
        print(f"[FAIL] module size ({CAP_LINES}-line cap, shrink-only baseline):")
        for violation in violations:
            print(f"  - {violation}")
        return 1

    scope = (
        f"the {len(only_paths)} file(s) this PR changed are"
        if only_paths is not None
        else "every source module is"
    )
    print(f"[OK] {scope} within its {CAP_LINES}-line cap/ceiling.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
