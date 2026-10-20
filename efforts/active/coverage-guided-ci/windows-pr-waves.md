# Reviewed waves for the remaining Windows PR work

**Status:** Reviewed and merged in #7338 as
`5a6f4e374893999fb1176408acfa6b8e44b5aabb`. The
[pinned residual census](windows-residual-ledger.md) is the next entry gate;
plan approval alone does not admit an implementation batch.

**Owner:** the Primary maintainer in the [canonical effort](README.md).
**Tracking:** #5694, #5682 and umbrella #4453. This document is a sequencing
amendment inside that effort, not a new campaign or a second owner.

The operator asked for a submitted plan followed by incremental PR batches
after the large PR accumulated many review rounds. The exact request is in
the effort's [Request](README.md#request). The decomposition, ordering and
batch boundaries below are **agent-recommended**.

## Why change the delivery shape

The preserved source work mixes fixture reuse and file moves, test-runner
containment, native command transport, fresh Git evidence, installer
preparation, coverage selection and substantial historical journaling.
Those are different correctness contracts. A passing review of one does not
establish another, and pushing them together repeatedly invalidates otherwise
settled review evidence.

One useful extraction is already complete: #7304 merged as
`55a93b92cda57031cc6f00d225da62b719f0de51`, after current-head approval and
green required CI. It changes only optional PowerShell cleanup discovery.
Do not republish it or treat it as completion of #5682's performance gate.

The original published head is `339dc00123964d84a117afd55197a192895ebac8`.
The preserved local continuation contains later reconciliation and journal
commits. Its 177-path diff against its recorded `6e2e59ceed` base is an
**inventory input**, not a patch to apply wholesale onto today's `dev`.
Whole-file differences do not prove that every contained hunk remains
unlanded; generated declarations and later upstream edits require hunk-level
reconciliation.

## Non-negotiable boundaries

- Preserve the complete original Windows acceptance: repeated complete
  same-head runs at or below 900 seconds including preparation and cleanup,
  every group at or below 240 seconds, and exactly two Worktrees workers.
- Keep the 30-second item limit, 300-second runner group ceiling,
  900-second plugin ceiling, 128-process ceiling, 4096 MiB process-tree memory
  and 2048 MiB temporary-storage limits, native child deadlines and existing
  test-mark semantics. The stricter 240-second measured group margin above
  remains part of the original acceptance. Do not
  add skips, exclusions, reduced selections, fabricated coverage or flake
  waivers to make a qualification green. Report existing unavailable or
  opt-in cases explicitly; they are not passing native evidence.
- Current required Source CI is the positively admitted unit packet.
  Retained native/integration contracts do not become eligible units or a
  fallback PR/promotion lane. Do not restore retired hosted jobs or invent
  Windows container capacity.
- Keep Git and ownership evidence fresh at each decision and mutation
  boundary. Immutable synthetic test inputs may be shared; mutable authority,
  remote heads, leases and publication proof may not be cached across calls.
- Preserve published heads, recovery refs, uncommitted work and review
  history. No shared stash, hard reset, raw administrative merge or
  installed-runtime patch substitutes for the managed contribution flow.
- A slice may land on its own reviewed correctness evidence; it must not
  claim the parent performance gate passed. The original #5694/#5682
  completion obligation remains open until the final qualification.

## Entry gate: turn the preserved PR into a hunk ledger

After this plan merges, before publishing any implementation batch:

1. Pin current `dev`, the original PR head and the preserved continuation.
   Compare candidate hunks, not just commit ancestry or file names.
2. Give each hunk one disposition: already merged with its actual PR/commit;
   retained in one named batch below; blocked by a named existing owner;
   or proposed retirement requiring an explicit rationale and review.
3. Assign every test move, helper, documentation paragraph, generated binding
   and changefile to its owning behavioral batch. Do not carry a historical
   multi-plugin changefile into unrelated extractions.
4. Publish the ledger in this document or a linked sibling before the first
   implementation PR. Its batch rows must name exact paths and assertions;
   ambiguous membership blocks that batch rather than expanding its scope.

This inventory is a read-only reconciliation step, not permission to discard
work or to classify a still-open contributor PR as superseded.

## Delivery waves

Each row is a wave, **not a license for one PR containing the whole row**.
Within a wave, publish one independently reviewable contract per PR, with
only its directly dependent helpers, tests, docs and changefile. Open the
next implementation PR after the preceding one actually merges; use current
`dev`, not an unmerged sibling branch or a private validation overlay.

| Wave | Candidate contract and concrete source anchors | Required exit evidence |
|------|-----------------------------------------------|------------------------|
| 1. Containment foundations | `tools/plugin_test_containment.py`, `tools/run-plugin-tests.py` and their tests: private temporary parents, fresh bounded Windows storage accounting, and reader/process ownership through cancellation. Separate unrelated fixes. | Real native timeout/cancellation/cleanup and boundary-negative cases; retain incoming `scandir` and symlink protections as well as owned regressions. No budget or scheduling exception. |
| 2. Immutable fixture batches | Worktrees `tests/conftest.py`, `pr_test_helpers.py`, fixture regressions and the `test_pr_ops.py` decomposition; separately Fleet's immutable allocation/wheel fixtures. First separate mechanical moves from fixture semantics where independently viable. | Collection/parameter/assertion parity for moved cases; private Git stores and repaired worktree links; mutation-isolation regressions; real per-case publication, lease and native operations remain real. A cached fixture is not cached authority. |
| 3. One-decision production batches | Separate PRs for finalization object reads (`finalize_ref.py` / `finalize_open_pr_gate.py`), publication transport evidence (`pr_publish.py`), recovery/replay (`pr_recovery.py`, `pr_rebase.py`, `pr_rewrite.py`), and committed config/hook reads (`inrepo_config_source.py`, `config.py`, `hooks.py`). | Each actual consumer plus changed-between-call and mutation-boundary refusals; preserve literal/NUL framing, priority, diagnostics, parent reconstruction and independent final checks. Never combine all four contracts merely because they save Git starts. |
| 4. Native and installer integration batches | Independently review native pane ownership, payload invocation/command binding, native attribution and remaining dependency/snapshot preparation. Anchors include `pane-wrapper.ps1`, `libs/payload-invocation/`, `emit-command-catalog.ps1`, `libs/machine-transport/`, `installer_dependency_policy.py` and the affected installer fixtures. | Real native launch/stdio, kernel ownership/reaping and argument/path cases; fresh installation/materialization when affected; complete dependency graph and seasoning unchanged. Reuse merged #7304; coordinate the existing #6909 installer owner rather than create a competing fix. |
| 5. Coverage provenance and current-policy compatibility | `tools/coverage_guided_selection/execution.py` and its strict-consumer regression: selected modules changed since the measured baseline become whole-file selections while unknown nodes elsewhere still fail. Keep historical workflow-contract repairs separate from this producer fix. | Real changed Git history and actual exact-selection execution, retaining original requested nodes and explicit whole-file provenance. No prefix admission, invented baseline or revival of integration CI. Resolve affected legacy workflow fixtures with #6237/#1303 owners. |
| 6. Parent qualification and residual settlement | Reconstruct the remaining candidate on current `dev` with every landed wave and separately landed dependency; no private overlays. Keep #5694 as the residual/integration owner. | Repeated complete exact-head native runs satisfy all original thresholds, required CI and a fresh full PR review pass, all residual hunks have dispositions, and the owning effort's actual remaining items are resolved. Only then settle the original PR. |

Waves 1-5 each depend on the reviewed plan and their completed hunk census.
Fixture batches additionally require any containment correction they actually
exercise; a batch in wave 3 or 4 requires any fixture/helper change it
actually imports. The coverage producer depends on its actual strict
consumer contract, not every unrelated native batch. Wave 6 depends on all
retained batches and their genuinely required external dependencies.
The serial publication rule bounds review churn; it does not invent code
dependencies between otherwise independent contracts.

## Batch admission and validation

Before opening each implementation PR, record its exact baseline, one
observable contract, owned hunks, existing dependencies and exclusions.
Include regression assertions that would fail without that batch's change.
If it needs another contract to pass, identify that prerequisite explicitly;
do not silently broaden the diff during review.

Use the smallest existing bounded runner covering that contract, including
real native/simple end-to-end consumers when affected. Record clean-room and
external-venue evidence where applicable, or the concrete reason a tier is
unavailable. Refresh canonical generated files only for actual source edits.
Run the existing lint/install/structural gates appropriate to the slice.
Changed plugins receive a newly scoped changefile, never hand-edited versions.

Required CI and a genuine current-head full Copilot review remain separate
from local validation. Read every overview and inline finding; resolve
Medium/High findings and all threads, holds, draft and ownership gates.
Use the canonical managed publication, merge and finalization commands.
After merge, journal the actual commit and remove its hunks from the residual
ledger before selecting the next batch.

For a mechanical test split, publish a bijection from old test identities to
new identities, retaining every parameter value and fixture dependency;
duplicate or missing cases fail the comparison. A matching test count alone
is insufficient. Changed paths must also retain conservative coverage
selection behavior rather than crediting nonexistent old node IDs.

If review reveals a new contract, an external author's open implementation,
or a substantive supersession, stop that extraction and amend the reviewed
plan or ask the operator as applicable. Do not create another growing
catch-all PR or supersede a contributor's live work.

## Evidence that remains red or incomplete

- The latest complete Worktrees attempt stopped in group 3 of 19 at its
  240-second ceiling, returning failure after 495.05 seconds including
  cleanup. It is not a complete-suite performance result.
- The first-touch control-plane case crashed a worker in that run. Later
  one-case and whole-file diagnostics pass, including four cases with two
  workers, but do not erase the failed group or establish its cause.
- Retained root-workflow tests report 167 failures and 11 fixture errors
  against removed CI job shapes and missing Main-only lineage in Dev.
  The concrete report is on #6237, comment `6282693571`. They need a
  policy-correct disposition, not retired jobs or fabricated state.
- Standard runtime reconciliation installed the merge-helper repair and
  completed #7304. The unified update still exited nonzero during Terminal
  profile finishing; the existing #7305 owner retains that separate repair.
  This wave plan does not authorize a global updater-repair campaign.
- A sibling installer's private source/materialized comparison remains
  diagnostic evidence only. Only its own merged, publishable source and
  complete native portfolio can satisfy a dependency.

## Completing, rolling back and stopping

A failed batch stays open under its owner. Narrow corrective commits retain
that batch's scope; a necessary rollback uses the repository's normal reviewed
revert flow. Do not revert unrelated landed work or loosen a guard to advance
the wave number.

For the parent qualification, an agent-recommended minimum is two complete
runs of the same immutable final head, including cleanup, with the unchanged
native coverage and limits above. Record each group's wall time and every
failure/unavailable case. A new head invalidates the old head's qualification.
Faster isolated cases, Linux results and cumulative per-wave timings are not
substitutes.

Do not close or abandon #5694 simply because extracted PRs merged. If all
functional hunks land and its remaining diff becomes empty, surface that
substantive supersession to the operator instead of manufacturing a dummy
final PR or silently releasing its obligation. The performance requirement
and unresolved failures remain tracked until genuinely resolved or explicitly
transferred. The [effort](README.md) stays open.
