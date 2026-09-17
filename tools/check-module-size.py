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
JSON, never something this script does automatically -- growth past the
grandfathered size must be a conscious, visible decision, not a silent
side effect of running a refresh. ``--refresh-baseline`` only ever *lowers*
an existing entry (when a file has shrunk) or *removes* one entirely (when
a file has shrunk to or below the cap); it never raises an entry, even if a
file has grown -- that case is a guard failure to fix, not baseline noise
to absorb.

Usage::

    python tools/check-module-size.py                  # enforce (pre-push/CI)
    python tools/check-module-size.py --refresh-baseline  # tighten after shrinking a file

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


def check(baseline: dict[str, int]) -> list[str]:
    """Return one message per file exceeding its cap/ceiling."""
    violations: list[str] = []
    for path in sorted(_tracked_py_files()):
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


def refresh_baseline(baseline: dict[str, int]) -> dict[str, int]:
    """Tighten (never widen) the baseline against current file sizes."""
    updated = dict(baseline)
    for path in sorted(_tracked_py_files()):
        lines = _line_count(path)
        if path in updated:
            if lines <= CAP_LINES:
                del updated[path]  # graduated: no longer needs grandfathering
            elif lines < updated[path]:
                updated[path] = lines  # shrunk: lock in the improvement
            # else: unchanged or grown -- never raised here; a grown file
            # still fails `check()` and must be fixed or manually widened.
        elif lines > CAP_LINES:
            updated[path] = lines  # newly discovered offender
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-baseline",
        action="store_true",
        help="Rewrite the baseline, tightening any shrunk entries (never widens).",
    )
    args = parser.parse_args()

    baseline = _load_baseline()

    if args.refresh_baseline:
        updated = refresh_baseline(baseline)
        if updated != baseline:
            _write_baseline(updated)
            print(f"[OK] refreshed {BASELINE_PATH.relative_to(REPO)}")
        else:
            print("[OK] baseline already up to date")
        return 0

    violations = check(baseline)
    if violations:
        print(f"[FAIL] module size ({CAP_LINES}-line cap, shrink-only baseline):")
        for violation in violations:
            print(f"  - {violation}")
        return 1

    print(f"[OK] every source module is within its {CAP_LINES}-line cap/ceiling.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
