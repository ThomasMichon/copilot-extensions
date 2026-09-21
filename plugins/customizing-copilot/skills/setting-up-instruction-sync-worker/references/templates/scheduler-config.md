# Scheduler config template (`projection-reflect` sync worker)

Adapt this to whichever scheduled-automation mechanism the adopting repo
already uses. The logic below is mechanism-agnostic; only the trigger (a
systemd timer, a scheduled GitHub Action, cron) differs per repo.

## Required steps, in order

1. **Consent gate (always first).**

   ```python
   from pathlib import Path
   from projection_reflect_consent import load_consent

   consent = load_consent(Path(repo_root))
   if consent is None:
       # No committed opt-in, or it was withdrawn since the last run.
       # Do nothing: no PR, no scheduler side effect, no dispatch.
       return
   ```

2. **Refresh + sync + scan.** Refresh installed payloads for every enabled
   plugin, then:

   ```python
   from instruction_projections import discover_enabled_sources, sync_repository, scan_repository

   sources = discover_enabled_sources(repo_root)
   sync_result = sync_repository(repo_root, sources)
   scan_result = scan_repository(repo_root, sources)
   ```

3. **Decide.**

   ```python
   from projection_reflect import has_actionable_change, bypass_decision

   if not has_actionable_change(
       changed=sync_result.changed, lock_updated=sync_result.lock_updated
   ):
       return  # nothing to report at all

   changed_lock_entries = [...]  # the lock entries for sync_result.changed
   decision = bypass_decision(
       findings=scan_result.findings,
       changed_lock_entries=changed_lock_entries,
       trusted_marketplaces=consent.trusted_marketplaces,
   )
   ```

4. **Act.**
   - `decision.eligible` -- open or update the stamp-labeled PR (reuse the
     repo's existing trusted deterministic identity; commit
     `sync_result`'s rendered files + the updated lock).
   - not `decision.eligible` -- dispatch the conflict-resolution task:

     ```python
     from agent_dispatch.conflict_dispatch import build_dispatch

     dispatch = build_dispatch(
         kind="projection-conflict",
         domain=repo_slug,
         label=consent.dispatch_label,
         repo=repo_slug,
         pr=open_pr_number,
         branch=sync_branch,
         base=default_branch,
         reconciler_agent=consent.reconciler_agent,
     )
     # shell dispatch.argv to enqueue the task; a label-supervisor spawns it
     ```

## Idempotency and cadence

- Safe to run on any cadence (hourly is a reasonable default, matching the
  private `config-reflect` prior art) -- a clean repo produces no diff and no
  PR.
- Never run two instances concurrently against the same repo; use whatever
  single-instance mechanism the repo's own automation already relies on.
