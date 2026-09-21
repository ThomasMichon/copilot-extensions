# Scheduler config template (`projection-reflect` sync worker)

Adapt this to whichever scheduled-automation mechanism the adopting repo
already uses. The logic below is mechanism-agnostic; only the trigger (a
systemd timer, a scheduled GitHub Action, cron) differs per repo.

## Module loading

`customizing-copilot` is payload-only: `instruction_projections.py`,
`projection_reflect.py`, and `projection_reflect_consent.py` live under the
installed plugin's `skills/reviewing-customizations/scripts/` directory, not
as an importable top-level package. Resolve and add that directory to
`sys.path` before importing them (the same pattern this plugin's own tests
use):

```python
import sys
from pathlib import Path

# <plugin-root> resolves the installed customizing-copilot payload -- e.g.
# via the marketplace-plugin install path this repo already resolves other
# plugin payloads through.
scripts_dir = plugin_root / "skills" / "reviewing-customizations" / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from instruction_projections import discover_enabled_sources, sync_repository, scan_repository
from projection_reflect import classify_findings, has_actionable_change, bypass_decision
from projection_reflect_consent import load_consent
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

2. **Refresh + sync + scan.** Refresh installed payloads for every enabled
   plugin, then:

   ```python
   sources = discover_enabled_sources(repo_root)
   sync_result = sync_repository(repo_root, sources)
   scan_result = scan_repository(repo_root, sources)
   ```

3. **Decide whether there is anything to report at all.** `sync` can report
   an empty `changed` list with a lock-only update, and a no-op sync can
   still pair with a `scan` that finds something -- checking
   `has_actionable_change` alone is not sufficient; also check for scan
   findings:

   ```python
   if not (
       has_actionable_change(
           changed=sync_result.changed, lock_updated=sync_result.lock_updated
       )
       or scan_result.findings
   ):
       return  # nothing changed and nothing was found -- true no-op
   ```

4. **Classify, and build the complete changed-lock-entry set.** Build
   `changed_lock_entries` from the **full lock diff** (every lock entry
   whose value differs before vs. after this run), not merely
   `sync_result.changed`'s destination paths -- a lock-only update (a
   version/hash bump with no rendered-file diff) still needs its
   marketplace checked against the trusted-source allowlist, and
   `sync_result.changed` alone would silently omit it:

   ```python
   classification = classify_findings(scan_result.findings)
   changed_lock_entries = [...]  # every lock entry that differs pre/post this run
   decision = bypass_decision(
       findings=scan_result.findings,
       changed_lock_entries=changed_lock_entries,
       trusted_marketplaces=consent.trusted_marketplaces,
   )
   ```

5. **Land, or route -- based on *why* it's ineligible, not just that it is.**
   An untrusted-source finding and a genuine conflict finding are NOT the
   same failure: `bypass_decision.eligible=False` covers both, but only a
   real conflict (`classification.conflict`) is something the
   `projection-reconciler` agent is authorized to resolve. An otherwise-clean
   change from an untrusted source must stay **review-only** -- never
   dispatched to an agent.

   ```python
   if decision.eligible:
       # Create/update the stamp-labeled PR (reuse the repo's existing
       # trusted deterministic identity; commit sync_result's rendered
       # files + the updated lock).
       open_or_update_stamped_pr(...)
   elif classification.conflict:
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
       # untrusted-source finding alone). Leave this as an ordinary
       # review-only PR/finding -- never dispatch an agent for it.
       open_or_update_stamped_pr(...)  # or just report the finding, per policy
   ```

## Idempotency and cadence

- Safe to run on any cadence (hourly is a reasonable default, matching the
  private `config-reflect` prior art) -- a clean repo produces no diff and no
  PR.
- Never run two instances concurrently against the same repo; use whatever
  single-instance mechanism the repo's own automation already relies on.
