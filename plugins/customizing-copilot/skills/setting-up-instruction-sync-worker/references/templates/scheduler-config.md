# Scheduler config template (`projection-reflect` sync worker)

Adapt this to whichever scheduled-automation mechanism the adopting repo
already uses. The logic below is mechanism-agnostic; only the trigger (a
systemd timer, a scheduled GitHub Action, cron) differs per repo.

## Module loading

`customizing-copilot` is payload-only: `instruction_projections.py`,
`projection_reflect.py`, `projection_reflect_consent.py`, and
`projection_sync_worker.py` live under the installed plugin's
`skills/reviewing-customizations/scripts/` directory, not as an importable
top-level package. Resolve and add that directory to `sys.path` before
importing them (the same pattern this plugin's own tests use):

```python
import sys
from pathlib import Path

# <plugin-root> resolves the installed customizing-copilot payload -- e.g.
# via the marketplace-plugin install path this repo already resolves other
# plugin payloads through.
scripts_dir = plugin_root / "skills" / "reviewing-customizations" / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from instruction_projections import discover_enabled_sources
from projection_reflect import classify_findings
from projection_reflect_consent import load_consent
from projection_sync_worker import run_sync_pass
```

`agent_dispatch.conflict_dispatch` is a normal installed-plugin import (that
plugin ships a runtime package), so it needs no path setup.

## Required steps, in order

1. **Consent gate (always first).**

   ```python
   consent = load_consent(Path(repo_root))
   if consent is None:
       # No committed opt-in, or it was withdrawn since the last run.
       # Do nothing: no PR, no scheduler side effect, no dispatch.
       return
   ```

2. **Refresh, then one deterministic pass.** Refresh installed payloads for
   every enabled plugin, then let `run_sync_pass` do the rest: it acquires
   `instruction_projections`'s per-repository lock once and holds it across
   the sync, the scan, and both before/after lock-entry reads -- so a
   concurrent worker's own run can never interleave and get misattributed
   to this pass's outcome. It also already builds the correct
   `changed_lock_entries` set (the full lock diff, not merely `sync`'s own
   changed-content list, so a lock-only update such as a version/hash bump
   still gets checked against the trusted-source allowlist) and folds
   `sync`'s own findings in alongside `scan`'s. **Never hand-roll the
   sync/scan/decide sequence separately** -- that was this template's own
   prior recipe and it is exactly the failure mode `run_sync_pass` exists to
   close (a hand-rolled sequence can drop a lock-only change or a sync-side
   failure, or leave a window for a second worker to interleave between
   steps).

   ```python
   sources = discover_enabled_sources(repo_root)
   outcome = run_sync_pass(
       Path(repo_root),
       sources,
       trusted_marketplaces=consent.trusted_marketplaces,
       refresh=refresh_installed_payloads,  # your own repo's refresh step
   )
   if not outcome.needs_pr:
       return  # nothing changed and nothing was found -- true no-op
   ```

3. **Land, or route -- based on *why* it's ineligible, not just that it is.**
   `outcome.bypass_eligible` / `outcome.needs_conflict_dispatch` are already
   the complete decision: an untrusted-source or missing-pin refusal is NOT
   the same as a real conflict (`outcome.needs_conflict_dispatch` is only
   true for a genuine `classify_findings(...).conflict` finding, e.g. a
   hand-edited managed projection) -- only a real conflict is something the
   `projection-reconciler` agent is authorized to resolve. An otherwise-clean
   change from an untrusted source must stay **review-only** -- never
   dispatched to an agent.

   ```python
   if outcome.bypass_eligible:
       # Create/update the stamp-labeled PR (reuse the repo's existing
       # trusted deterministic identity; commit the rendered files + the
       # updated lock outcome.changed/lock_updated already describe).
       open_or_update_stamped_pr(...)
   elif outcome.needs_conflict_dispatch:
       # A genuine conflict exists. Create/update the canonical PR FIRST --
       # even though it will show real git conflicts -- so the dispatch
       # below has an actual PR to point the reconciler at; the recipe's
       # own contract is "take an existing PR the last mile", not "resolve
       # a conflict that has no PR yet".
       pr_number = open_or_update_stamped_pr(...)

       from agent_dispatch.conflict_dispatch import build_dispatch

       dispatch = build_dispatch(
           kind="projection-conflict",
           domain=repo_slug,
           label=consent.dispatch_label,
           repo=repo_slug,
           pr=pr_number,
           branch=sync_branch,
           base=default_branch,
           reconciler_agent=consent.reconciler_agent,
       )
       # shell dispatch.argv to enqueue the task; a label-supervisor spawns it
   else:
       # Ineligible for a reason that is NOT a resolvable conflict (e.g. an
       # untrusted-source finding or a missing/malformed immutable pin
       # alone -- outcome.bypass.reasons names it). Leave this as an
       # ordinary review-only PR/finding -- never dispatch an agent for it.
       open_or_update_stamped_pr(...)  # or just report the finding, per policy
   ```

## Idempotency and cadence

- Safe to run on any cadence (hourly is a reasonable default, matching the
  private `config-reflect` prior art) -- a clean repo produces no diff and no
  PR.
- Never run two instances concurrently against the same repo; use whatever
  single-instance mechanism the repo's own automation already relies on.
