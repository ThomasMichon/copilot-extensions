#!/usr/bin/env python3
"""Proactively flag the single worst module-size offender for decomposition.

`tools/check-module-size.py`'s PR-time gate (Phase 1, `--changed-since`)
fairly attributes *growth*, but growth is only half the problem: a module
already at or near its cap is organic, cumulative drift that no single small
PR caused, so no PR-time gate can ever surface it -- it just sits there until
someone happens to touch it and gets blocked by a decade of pre-existing
size. This script is the "detect" half of the watchdog: it finds the worst
offender and opens (or leaves alone, if one is already open) a tracking issue
a **dedicated decomposition worker** can pick up -- never diffuse pressure on
whichever future PR happens to touch the file next.

It intentionally does the least possible: no state file, no database, no new
service. Idempotency comes entirely from asking GitHub itself whether an open
``needs-decomposition``-labeled issue already names the candidate path (an
exact anchor line, ``Module: <path>``), via ``gh issue list --search``.

Priority order: a file already *failing* `check-module-size.py` (negative
margin -- literally blocking someone's CI right now) always outranks a file
merely *near* its cap (proactive, before anyone is blocked). Ties within each
group break by the largest negative/smallest positive margin (worst first).

Usage::

    python tools/module-health-watchdog.py                 # dry run, prints only
    python tools/module-health-watchdog.py --near-cap 25    # proactive threshold
    python tools/module-health-watchdog.py --file-issue     # actually open the issue (needs gh + GH_TOKEN)
    python tools/module-health-watchdog.py --repo owner/name  # override for local testing

Exit code is 0 for detection/reporting and a successful (or skipped/deduped)
filing; nonzero only when `--file-issue` was asked to actually file and the
`gh issue create` call itself failed (so a scheduled run's own CI goes red
instead of silently reporting success while filing nothing). A genuinely new
hard-cap violation is `check-module-size.py`'s job to fail CI over, not this
script's -- this script only ever reports or files, never blocks a build.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_CHECK_MODULE_SIZE = REPO / "tools" / "check-module-size.py"

ISSUE_LABEL = "needs-decomposition"
DEFAULT_REPO = "ThomasMichon/copilot-extensions"


def _load_check_module_size():
    spec = importlib.util.spec_from_file_location(
        "check_module_size", _CHECK_MODULE_SIZE
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def worst_candidate(cms) -> tuple[str, int, int, int] | None:
    """Return ``(path, lines, ceiling, margin)`` for the single worst offender,
    or ``None`` only when there is no tracked (non-test) Python module at all.

    ``margin`` is ``ceiling - lines``: negative means already failing
    `check()`; the smallest (most negative, then smallest positive) margin
    wins. A comfortably-small codebase still returns its least-comfortable
    file -- this is a ranking, not a threshold; callers decide what to do
    with the result (the CLI below only files an issue when ``margin`` is
    at or under the configured near-cap threshold, or already negative).
    """
    baseline = cms._load_baseline()
    rows: list[tuple[str, int, int, int]] = []
    for path in cms._tracked_py_files():
        lines = cms._line_count(path)
        ceiling = baseline.get(path, cms.CAP_LINES)
        margin = ceiling - lines
        rows.append((path, lines, ceiling, margin))
    if not rows:
        return None
    rows.sort(key=lambda r: r[3])
    return rows[0]


class LookupFailed(RuntimeError):
    """The existing-issue search itself could not be completed reliably.

    Distinct from "searched and found nothing" -- a caller must never treat
    this the same as "no duplicate exists" (that would file a real
    duplicate on a transient API hiccup or a not-yet-created label).
    """


def _existing_issue_number(repo: str, path: str) -> int | None:
    """Return an already-open tracking issue's number for ``path``, or None
    if the search completed successfully and found no match.

    Raises :class:`LookupFailed` when the search itself could not be
    completed -- callers must abort filing rather than treat that the same
    as a confirmed "no match".
    """
    out = subprocess.run(
        [
            "gh", "issue", "list",
            "--repo", repo,
            "--label", ISSUE_LABEL,
            "--state", "open",
            "--search", f'"Module: {path}"',
            "--json", "number",
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise LookupFailed(out.stderr.strip() or f"gh issue list exited {out.returncode}")
    try:
        matches = json.loads(out.stdout or "[]")
    except json.JSONDecodeError as error:
        raise LookupFailed(f"unparseable gh issue list output: {error}") from error
    return matches[0]["number"] if matches else None


def _file_issue(repo: str, path: str, lines: int, ceiling: int, margin: int) -> bool:
    """File the tracking issue. Returns True on success, False on failure --
    the caller must propagate a failure as a nonzero exit so a scheduled run
    never reports success while silently filing nothing."""
    status = "already over its cap/ceiling" if margin < 0 else "approaching its cap/ceiling"
    body = (
        f"## Summary\n\n"
        f"`{path}` is {status}: **{lines}** lines against a "
        f"**{ceiling}**-line cap/ceiling (margin **{margin}**).\n\n"
        f"Module: {path}\n\n"
        "## Why this is filed automatically\n\n"
        "A module's size is organic, cumulative drift -- many individually "
        "reasonable contributions summing past a ceiling over time, not any "
        "single PR's fault. `tools/check-module-size.py`'s PR-time gate "
        "(`--changed-since`) fairly attributes *growth* to whichever PR "
        "causes it, but it cannot surface a module that is simply *already* "
        "large; nothing blocks a PR that never touches it. This scheduled "
        "watchdog (`tools/module-health-watchdog.py`) exists to surface "
        "that drift proactively instead of leaving it as diffuse pressure on "
        "whichever future PR happens to touch the file next.\n\n"
        "## What's needed\n\n"
        "A cohesive split by behavioral responsibility -- see "
        "CONTRIBUTING.md's Code Style section (the componentization "
        "discipline, including the CLI/registration-surface pattern) and "
        "`python tools/rank-module-size.py` for sibling offenders. Not "
        "prescriptive about the exact seams; that judgment belongs to "
        "whoever picks this up.\n\n"
        "## Documentation impact\n\n"
        "None expected from filing this issue; the eventual split PR "
        "carries its own Documentation impact statement.\n"
    )
    out = subprocess.run(
        [
            "gh", "issue", "create",
            "--repo", repo,
            "--title", f"Componentize {path} ({lines} lines, cap/ceiling {ceiling})",
            "--label", ISSUE_LABEL,
            "--body", body,
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        print(f"[ERROR] gh issue create failed: {out.stderr.strip()}", file=sys.stderr)
        return False
    url = out.stdout.strip()
    print(f"[OK] filed {url}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"owner/name to file against (default {DEFAULT_REPO})",
    )
    parser.add_argument(
        "--near-cap",
        type=int,
        default=50,
        metavar="N",
        help=(
            "only file an issue when the worst offender's margin is at or "
            "under N lines (already-negative/over-cap always qualifies "
            "regardless of N; default 50)"
        ),
    )
    parser.add_argument(
        "--file-issue",
        action="store_true",
        help="actually open the tracking issue via `gh` (default: dry-run/report only)",
    )
    args = parser.parse_args()

    cms = _load_check_module_size()
    candidate = worst_candidate(cms)
    if candidate is None:
        print("[OK] no tracked module has a baseline/cap entry -- nothing to report.")
        return 0

    path, lines, ceiling, margin = candidate
    status = "OVER its cap/ceiling" if margin < 0 else "within its cap/ceiling"
    print(f"[INFO] worst offender: {path} ({lines} lines, ceiling {ceiling}, margin {margin}, {status})")

    if margin >= 0 and margin > args.near_cap:
        print(f"[OK] worst margin ({margin}) is comfortably above the --near-cap threshold ({args.near_cap}) -- nothing to file.")
        return 0

    if not args.file_issue:
        print("[INFO] dry run -- pass --file-issue to actually open a tracking issue.")
        return 0

    try:
        existing = _existing_issue_number(args.repo, path)
    except LookupFailed as error:
        # Never treat "couldn't confirm" as "confirmed absent" -- that would
        # risk filing a real duplicate on a transient API hiccup or a
        # not-yet-created label. Report and abort without filing; the next
        # scheduled run tries again.
        print(f"[WARN] existing-issue lookup failed, aborting without filing: {error}", file=sys.stderr)
        return 0
    if existing is not None:
        print(f"[OK] already tracked: #{existing} -- not filing a duplicate.")
        return 0

    return 0 if _file_issue(args.repo, path, lines, ceiling, margin) else 1


if __name__ == "__main__":
    sys.exit(main())
