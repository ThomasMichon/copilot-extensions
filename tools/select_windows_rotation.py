"""Deterministic, stateless plugin selection for the Windows CI rotation
(see ``.github/workflows/windows-coverage-rotation.yml``).

No existing CI job in this repo runs a plugin's FULL test suite on
Windows -- every "full suite" job (the per-PR ``smoke`` matrix in
``ci.yml``, and the ``dev``-to-``main`` ``full`` promotion matrix in
``validate-and-promote.yml``) runs on ``ubuntu-latest`` only, and the
narrow ``windows-latest`` jobs that do exist cover only pre-marked
``windows_only`` tests, never a whole plugin (coverage-guided-ci effort,
Phase 3.5 follow-up, 2026-10-07: a Windows-only ``os.kill(pid, 0)``
process-killing crash in ``agent-machines``, and Windows ``MAX_PATH``/
PATH-resolution issues in ``agent-ssh``/``agent-vault``, were found only
by a manual local Windows full-matrix run -- no existing CI gate would
ever have caught any of them).

Rather than duplicating every plugin's full suite on both OSes every run
(true 2x wall-clock cost, for near-zero marginal benefit on OS-agnostic
plugins), this picks a small, weighted, rotating subset each day: plugins
with real OS-divergent code (dual ``.ps1``/``.sh`` installers,
subprocess/signal handling, path manipulation) rotate through more often
than pure-Python-logic plugins.

Deterministic and stateless: the same date always picks the same
plugin(s), so no cross-run state (a counter, a cursor file) is needed -- a
missed day's run (e.g. a workflow outage) doesn't permanently skip that
day's pick, it just reselects it next time that date recurs in the cycle.
"""

from __future__ import annotations

import argparse
import datetime
import json

# Real OS-divergent code: dual .ps1/.sh installers, subprocess/signal
# handling, path manipulation -- proven by this session's findings
# (agent-machines' os.kill crash, agent-ssh/agent-vault's MAX_PATH
# instances). Rotates on a short cycle so each gets Windows coverage
# roughly every few days.
TIER_A: tuple[str, ...] = (
    "agent-machines",
    "agent-ssh",
    "agent-vault",
    "agent-dispatch",
    "agent-index",
    "agent-worktrees",
    "agent-logger",
    "agent-mcp",
    "agent-bridge",
    "agent-codespaces",
    "agent-containers",
)

# Lower OS risk (little/no subprocess, signal, or path-manipulation
# surface) -- still rotated, just on a longer cycle. Disjoint from TIER_A
# by construction.
TIER_B: tuple[str, ...] = (
    "ai-attribution",
    "budget-guidance",
    "context-handoff",
    "copilot-extensions-harness",
    "customizing-copilot",
    "efforts",
    "harness-knowledge",
    "agent-pull-requests",
    "agent-conduct-guidance",
)

# How many Tier A picks per run (Tier B always contributes exactly one).
TIER_A_PICKS_PER_RUN = 2


def select_plugins(date: datetime.date) -> list[str]:
    """Return today's rotation picks: ``TIER_A_PICKS_PER_RUN`` plugins from
    Tier A (consecutive by the date's ordinal day number, wrapping) plus
    exactly one from Tier B. Deterministic -- the same date always returns
    the same list."""
    day = date.toordinal()
    picks = [TIER_A[(day + offset) % len(TIER_A)] for offset in range(TIER_A_PICKS_PER_RUN)]
    picks.append(TIER_B[day % len(TIER_B)])
    return picks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=datetime.date.fromisoformat,
        default=None,
        help="ISO date (YYYY-MM-DD) to select for; defaults to today (UTC).",
    )
    parser.add_argument(
        "--format",
        choices=("json", "github-output"),
        default="json",
        help="json: a JSON array to stdout. github-output: a "
        "'plugins=<json>' line suitable for appending to $GITHUB_OUTPUT.",
    )
    args = parser.parse_args()
    date = args.date or datetime.datetime.now(datetime.timezone.utc).date()
    picks = select_plugins(date)
    if args.format == "github-output":
        print(f"plugins={json.dumps(picks)}")
    else:
        print(json.dumps(picks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
