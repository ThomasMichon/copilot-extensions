---
name: setting-up-instruction-sync-worker
description: >
  Scaffold the `projection-reflect` deterministic sync worker for a repo that
  wants its enabled plugins' static instruction-projection content kept
  current automatically, instead of only at the next session's proactive
  resync. Requires the adopting repo's own explicit, committed opt-in before
  scaffolding anything -- never merely an operator request in the current
  session. Use when a repo asks to automate instruction-projection sync, add
  a scheduled projection-reflect worker, or enable a projection bypass
  profile for its review gate.
  Trigger phrases include:
  - 'set up instruction sync'
  - 'automate projection sync'
  - 'scheduled projection-reflect worker'
  - 'projection-reflect bypass profile'
  - 'enable projection sync automation'
  - 'scaffold the sync worker'
---

# Setting Up the Instruction Sync Worker

Scaffolds `projection-reflect` (`efforts/active/ambient-guidance-navigability`
Phase 2) for a repo that wants its own scheduled, non-agentic worker to keep
enabled plugins' checked-in static instruction projections current, closing
the sync-freshness gap the immediate/proactive resync trigger
(`copilot-extensions-harness:cross-repo-debug-tracking`) only covers when a
session happens to be active.

This skill is a **scaffolder**, not a turnkey installer: the scheduler
mechanism (a systemd timer, a scheduled GitHub Action, a cron entry) and the
review-gate bypass mechanism (branch protection exceptions, a labeled-PR
auto-merge rule) are unavoidably repo-specific. What this skill provides is
generic across any adopting repo: the decision layer
(`projection_reflect.py`), the conflict-dispatch primitive
(`agent_dispatch.conflict_dispatch`), the reconciler agent template
(`projection-reconciler-agent-template.md`), and -- the piece unique to this
skill -- the **consent gate** that must guard all of the above, plus templates
for the two repo-specific pieces to adapt.

## The consent gate comes first -- always

**Never scaffold anything without the adopting repo's explicit, committed,
in-repo opt-in already present.** Per
[`docs/patterns/install-vs-adopt-boundary.md`](../../../../docs/patterns/install-vs-adopt-boundary.md):
granting a scheduler repo-write authority and a review-bypass profile is repo
mutation, not a machine-local install/update concern -- and "the operator
asked for it in this session" or "the repo is PR-gated" are **not**
ownership signals (a repo you only contribute to is often PR-gated too).

The ownership signal is a committed `.github/copilot/projection-reflect.json`
matching this schema (validated by
`scripts/projection_reflect_consent.py`'s `load_consent`):

```json
{
  "schema": "copilot-extensions.projection-reflect-consent",
  "version": 1,
  "enabled": true,
  "reconcilerAgent": "projection-reconciler",
  "dispatchLabel": "projection-conflict",
  "trustedMarketplaces": ["copilot-extensions"]
}
```

- **No file present, or `enabled` is not literally `true`:** decline to
  scaffold anything. You may still report what drift exists (`scan
  --from-settings`) -- report-only mode never requires consent -- but never
  open an auto-mergeable PR or install a scheduler.
- **A repo genuinely wants this:** its own maintainer commits this file
  through the repo's normal contribution flow (a PR, reviewed like any other
  change) *before* asking this skill to scaffold anything. If the file is
  missing, walk the operator through authoring and landing it first -- do
  not write it yourself as a side effect of "setting up the worker."
- **Consent is rechecked live, not only at setup time.** Both the scheduled
  worker and the bypass profile call `load_consent` on every run/every PR --
  there is no cache. The moment the file is deleted, edited to
  `"enabled": false`, or otherwise fails validation, both the worker and the
  bypass fail closed on their very next run, without a second `setup`
  invocation. Withdrawing consent is exactly: edit or delete the file.

## What to scaffold, once consent is present

1. **A `projection-reconciler` agent** for the adopting repo, from
   [`projection-reconciler-agent-template.md`](../reviewing-customizations/references/projection-reconciler-agent-template.md).
   Copy it to that repo's `.github/agents/projection-reconciler.agent.md`,
   filling in its own PR-review/merge tooling reference (per the template's
   own "Adapting this template" section) and wiring any MCP servers its own
   review flow needs -- the template ships with none, since PR/issue tooling
   is repo-specific.

2. **A scheduler config**, adapted from
   [`references/templates/scheduler-config.md`](references/templates/scheduler-config.md)
   to whichever mechanism the repo already uses for scheduled automation
   (a systemd timer, a scheduled Action, cron). It must: call
   `load_consent` first and exit immediately (no PR, no scheduler action) if
   it returns `None`; refresh enabled plugin payloads; run `sync` then
   `scan --from-settings`; compute `has_actionable_change` and
   `bypass_decision` (both from `projection_reflect.py`); when eligible, open
   or update the stamp-labeled PR; when not (a conflict-classified finding,
   or an untrusted source), dispatch the conflict-resolution task via
   `agent_dispatch.conflict_dispatch.build_dispatch`, naming the consent
   file's own `reconcilerAgent`/`dispatchLabel`.

3. **A bypass-config profile**, adapted from
   [`references/templates/bypass-profile.md`](references/templates/bypass-profile.md)
   for whichever review gate the repo already uses. It must re-check
   `load_consent` on every PR (not cache a prior check), and restrict itself
   to: the stamp label present, every changed path inside
   `.github/instructions/**` and the lock file, a diff shape of regular-file
   adds/modifies only, a byte-exact recompute match, and every changed
   source's marketplace inside the consent file's own
   `trustedMarketplaces` -- the same conjunction `projection_reflect.py`'s
   `bypass_decision` already proves, never identity alone.

4. **Reuse the repo's existing trusted deterministic identity** for this
   reflect kind, per the Plan's own requirement -- never mint a new one as a
   side effect of this skill. If the repo has no existing reflect-style
   identity, this skill must **decline outright** (report-only mode still
   available) until that identity is provisioned and registered through the
   repo's own normal, reviewed account/credential process -- or walk the
   operator through that one-time registration explicitly. Never do it as an
   automatic side effect of "set up the instruction sync worker."

## Validation before calling it done

- A negative-proof check: with no opt-in file present, confirm the worker
  refuses to scaffold (see `test_projection_reflect_consent.py`'s own
  no-file/`enabled: false` cases for the pattern to replicate against the
  live scheduler/bypass code once built).
- A live-revocation check: opt-in present at setup, then removed -- confirm
  both the worker and the bypass fail closed on their very next run without
  a second `setup` call.
- Re-run `customizing-copilot`'s own test suite
  (`tools/run-plugin-tests.py customizing-copilot`) and this repo's
  `check-version-bump`/`check-version-consistency`/`check-docs-consistency`.

## See also

- `efforts/active/ambient-guidance-navigability/README.md` -- the full Phase
  2 plan and Journal this skill is one slice of.
- `reviewing-customizations`'s own `SKILL.md` -- the `sync`/`scan` mechanism,
  the `projection_reflect.py` decision layer, and the coverage registry this
  automation composes with.
