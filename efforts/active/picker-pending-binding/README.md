# Picker Pending Binding

- **Slug:** `picker-pending-binding`
- **Repo:** copilot-extensions
- **Branch(es):** independent plan and implementation PRs targeting `dev`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** `visions/picker/README.md` — responsive front door and decision support before cost
- **Umbrella issue:** #7938

## Guiding Intent

Make the actual Manager entry responsive before project enumeration and engine
bootstrap finish. First interactive chrome and first authoritative rows are
different milestones; neither is evidence of the other. Keep operational
authority in the engine and retain explicit bootstrap failure.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| patient-piston | Consumer startup implementation, tests, source review and merge | #7938 |
| crooked-sprocket | Completed, separately owned engine bootstrap optimization; no live-session contact | #7974 |

## Coordination

- **Topology:** independent source PRs; consume compatible optional engine data.
- **Host (owns PRs):** patient-piston.
- **Delegates:** none.
- **Handoff:** #7938 records source/release status and remaining integration qualification.
- The #7974 engine optimization is merged. A narrowly authorized scalar-producer
  follow-up may use unclaimed bootstrap files, but only already-resolved authority.
  Open #7449 retains `manager_launch_cli.py`; #7788 retains native launch
  maintenance. Neither claim may be taken over. No shared runtime deployment is
  authorized by this effort.
- Overlap checks use read-only forge/task state. Do not contact or inspect
  pre-existing agent sessions, use agent-chat, or dispatch remote workers.
  Interface conflicts return to the campaign coordinator rather than another
  owner's live session; this slice has no delegated agents.

## Context

At the inspected source baseline, `_cmd_picker` builds the project roster before
calling the production runner. `runner._prepare` then executes `picker-bootstrap`
before Textual starts. After mount, `_setup_live_async` waits for a one-shot
cache-only row read before source construction and the held loader start.
Cold `project_config` separately requests config-dir, repo-dir and machine.

The existing bootstrap payload has project/cwd/workspace/default-live decisions,
but not the three scalar config values. The consumer must tolerate older engines
without importing engine modules or fabricating authority.

## Request

Public-safe summary of the authorized scope: implement immediate useful
pending-binding chrome, keep slow work off the input thread, enroll the current
local feed without waiting for advisory preload, preserve late-result ordering
and engine authority, and measure executable-entry/interactive/current-row
boundaries with cold/warm and increasing-cardinality controls. Narrow constructor,
compose and startup plumbing is included; actions and maintenance are not.
Consume optional authoritative bootstrap scalars with a memoized off-thread legacy
fallback. Initially engine scalar batching remained with #7974's owner.
Review and merge the source changes; installed rollout is separately owned.

Subsequent authorized scope, public-safe summary: after #7974 merged and a fresh
read-only collision check, this same effort may add optional authoritative
bootstrap scalars from already-resolved inputs. No additional discovery, hashes,
provenance scans or invented values; respect existing file claims. Finish the
consumer review corrections first and use a separate minimal producer PR if
needed. A headless refresh or reduced command count is never a two-second pass.

The resolved scope is **path fields only**: no new machine-config read or
discovery. Use the unclaimed `context_cli.py` seam; do not touch the actively
owned `manager_launch_cli.py` or contact its agent. Keep the machine field absent,
retain the bounded memoized worker fallback, and measure the remaining query.

Final narrowed admission: **`config_dir` only**. Do not guess `repo_dir` from a
registry anchor. Preserve both effective repo-dir and machine fallbacks, measure
two remaining queries, and leave the one-query, cold/native and release gates
open. No reserved producer file, existing agent session or shared runtime may
be touched.

## Plan

### Phase 1 — Reviewed source contract

- [x] Land this plan through the repository review gate before implementation.
- [x] Record the existing targeted-test baseline and exact startup call ordering.

### Phase 2 — Pending binding and current feed

Implementation details below are agent-recommended means of meeting the request.

- [x] Introduce a lightweight pending-binding screen within the same production
  Textual application, not a second executable or a separate GUI lifecycle.
  It identifies the requested project (or unresolved selection), reports loading,
  accepts Escape, and exposes no operational actions until authority is bound.
- [x] Defer project roster/default selection and engine bootstrap onto a worker
  after the first refresh. Preserve ambiguous/unknown/no-project failures and the
  engine's project, cwd and default-live decisions. Keep mock/capture compatibility.
- [x] Marshal successful binding onto the render thread before constructing the
  operational PickerScreen. A cancelled/unmounted pending screen must reject late
  completion; a worker must not change process cwd or start housekeeping after
  cancellation. Bootstrap failure remains visible and returns failure on dismissal.
- [x] Remove the redundant advisory one-shot read from initial live setup; the
  authoritative snapshot-plus-follow loader is the sole initial row producer.
  Keep pivot discovery independent and retain local-first loading of remote tabs.
  No stale advisory payload then exists to overwrite current rows.
- [x] Accept optional engine bootstrap `project_config` fields `config_dir`,
  `repo_dir`, `machine`; validate supplied values and bind them to the resolved
  project. Existing memoized scalar calls remain the compatible fallback, off the
  input thread, with fallback counts visible in validation. Producer capability
  is checked through source/forge evidence; absent fields remain fallbacks.

### Phase 3 — Qualification and publication

- [x] Run targeted regressions and adjacent startup/config/mock/capture tests,
  lint and install-contract checks; add a Manager patch changefile.
- [x] Document the pending-binding behavior, authority transition, error/cancel
  semantics and measurement boundaries. Reuse existing launch/frame-health events;
  introduce no telemetry SDK, sink, network request or per-event child process.
- [x] Open and steward the implementation PR through passing review, required CI
  and actual merge. Record source commit separately from release and installed state.
- [x] Deferred to `#7938`: Transfer any remaining engine-producer or installed integration qualification
  explicitly to #7938; do not close that issue or claim the full latency budget
  from a synthetic frame-only measurement.

### Phase 4 — Conditional scalar producer

- [x] Resolve the authority/scope blocker: bootstrap currently has no resolved
  machine configuration, and open #7449 owns the inspected Manager-launch seam.
  Only `config_dir` is admitted; effective repo-dir and machine remain fallbacks.
- [ ] After that decision and a fresh collision check, implement only the admitted
  optional producer fields with Worktrees tests/changefile, remeasure the actual
  consumer, and steward its separate source PR through review and merge.
  This is the config-dir-only incremental slice, not the superseded one-query
  target. Do not substitute the registry anchor.

## Validation Plan

- [x] Regression-first: gated two-second project/bootstrap work must not delay a
  useful first frame or Escape; engine failure and cancellation discard late
  success without exposing actions or starting post-binding maintenance.
- [x] Regression-first: a blocked advisory hook must not be called or delay a ready
  authoritative local loader; exactly one initial loader start, current rows
  retained, and slow remote sources do not delay the local source.
- [x] Scalar controls: complete optional payload uses zero legacy scalar calls;
  older payload uses at most one call per scalar per bound project; project switch
  invalidates binding and caches; malformed optional values do not select paths.
- [x] Run the actual Manager entry and real Textual consumer under isolated
  synthetic engine/registry inputs: cold and warm samples at small, medium and
  large roster cardinalities. Report entry-to-interactive and entry-to-current
  rows separately, engine-call counts, and input latency; label fixture limits.
- [x] Preserve explicit project/default/ambiguous/unknown selection, live/local,
  mock/preview and screenshot behavior. Verify exit status and cwd semantics.
- [x] Record unit, lightweight integration, clean-room and native/live tiers
  exercised or explicit reasons for deferral; source proof is not rollout proof.
- [x] Prove each producer field equals its existing authoritative scalar contract
  without adding discovery; do not report three eliminated calls unless all three
  fields are actually supplied and accepted.

## Proposal

Retain the established engine process boundary and existing snapshot/follow data
owner. This advances the Picker responsiveness vision without changing its intent.
The pending screen is presentation-only; optional scalar adoption is backwards
compatible. No new service, thread-per-row work, provider authority or installer
contract is introduced.

## Journal

### 2026-10-22 — Intake

- Claimed #7938 as patient-piston and checked public comments and overlapping PRs.
- Confirmed the two serial startup barriers and the missing batch-scalar producer.
- Scope includes narrowly dependent constructor/compose startup changes; engine
  producer work and installed rollout remain separately owned.

### 2026-10-22 — Reviewed plan and consumer qualification

- Plan #7993 received an approving review with no findings and merged before
  implementation. Existing startup/config/transplant baseline: 109 passed.
- Initial pending-binding regression failed before the new binding contract;
  the expanded startup/project/mock/capture cohort subsequently passed 148 tests.
  Final adjacent mailbox and preferred-local-source expansion: **249 passed**.
- Native Windows fixture qualification: six parameter cases passed, yielding
  twelve cold/warm samples at 1/100/1,000 rows with/without batched scalars.
  Each invocation used one held current-list subscription. Legacy scalar calls:
  three per invocation versus zero for the optional complete payload.
- Observed cold-process entry-to-headless-refresh ranged **4,605.71–8,753.80 ms**;
  warm-process refresh **35.68–40.21 ms**. Input acknowledgement while the
  two-second bootstrap gate was held: **0.96–1.44 ms**. Cold entry-to-current-row
  render ranged **11,463.45–26,956.27 ms**; warm **7,994.68–16,432.25 ms**.
- These are individual Windows source-fixture observations, not tail estimates.
  Cold-process measurements include interpreter/import and fixture admission;
  warm samples reuse the process, not a claim of an OS-cold disk cache.
  The native harness calls the real CLI entry, runner, Textual application and
  held-feed parser with synthetic engine/roster inputs. Optional maintenance,
  update checks and pivot discovery are isolated out. Native compositor
  visibility, actual remote-host slowness and installed-runtime rollout were not
  exercised; these remain #7938 integration qualification.
- **The cold first-frame vision budget is not met by these observations.**
  Import/startup profiling and complete installed-engine/native acceptance remain
  with #7938, alongside coordination of the optional scalar producer in #7974.
  This source slice removes the serial engine/advisory barriers; it does not close
  the broader issue or claim deployed performance.
- Clean first-touch state was provided by the contained test runner and fresh
  fixture roots. No installer or provisioning behavior changed; a complete
  clean-room installation was not needed for this source-only consumer slice.

### 2026-10-23 — Review corrections

- Reproduced the review's two functional findings: non-live setup/reload discarded
  accepted scalar values before locality input, and environment-enabled mock
  mode skipped implicit project selection.
- Bound scalar lifetime now ends only at a new binding, not ordinary file-cache
  invalidation. A real Textual non-live setup/reload regression exercises locality
  input after both applications; no scalar subprocess is permitted.
- Both environment mock switches now preserve implicit selection and ambiguity
  refusal. Four parameter cases cover one and multiple registered projects.
- Final targeted startup and adjacent mailbox/local-source cohort: **255 passed**.
  Touched-file identifier scanning also exposed legacy fixture names; those test
  values were made generic without changing the roster assertions.
- A subsequent review identified old in-flight scalar reads blocking rebinding
  and failed reads surviving invalidation. Both were reproduced before repair.
  Cache ownership is now binding-scoped: rebinding replaces the state without
  waiting on an old reader, scalar and derived-file memoization keys retain
  binding/epoch identity, accepted values survive reload, and negative lookup
  results retry in the next reload epoch. Gated old-reader, late-file-result and
  timeout/recovery regressions pass; the complete targeted cohort is **258 passed**.
- The same fast-path rule now refuses uncached scalar reads from an event loop
  rather than starting or waiting on a process. Worker prefetch makes subsequent
  input reads available; its regression brings the final cohort to **259 passed**.
- Read-only integration inspection after #7974 merged found **no optional scalar
  fields** in `cmd_picker_bootstrap` at Dev `41913ba4c176c3f2e17ad7c375f9406ac6feba30`.
  Its targeted invocation-record improvement is compatible with this consumer,
  but cannot remove the three fallback scalar commands. Optional producer work
  and the complete cold/native budget remain explicitly with #7938; no existing
  agent session was contacted or inspected.
- Review also reproduced alias-locality loss when a failed/empty legacy scalar's
  prepared fallback was invalidated at setup publication. Binding-scoped fallback
  snapshots now remain available to input consumers while workers retry by epoch;
  recovered path values select fresh file-cache entries. Real non-live
  setup/reload tests cover complete, failed and empty scalar paths and the actual
  alias-based resume decision. Worker recovery after an intervening input read is
  covered separately. The final targeted cohort is **262 passed**.

### 2026-10-23 — Consumer merged; producer scope held

- #8017 received an approving current-head review with no remaining findings and
  merged to Dev at `68ef28f3c3968c057d2f524567b0293c6b99daac`.
- Read-only open-PR file inspection found #7449 owns `manager_launch_cli.py`.
  No candidate in that inspected open-PR set touched `context_cli.py`; no agent
  session was contacted. The producer expansion itself has not begun.
- `cmd_picker_bootstrap` and its front-door resolution carry the project,
  anchor, cwd decision and tracked invocation record, not the layered machine
  config required by `get machine`. Substituting the record's machine or raw
  hostname would not preserve that authority. Emitting all three scalars therefore
  needs the named authority/scope decision, not an invented value or a takeover.
- Release check: Main `9489a0fe8e4284538c3e00d5aaad2ac0cf474f75`, Manager
  `0.6.85-dev1`, did not match any of the six changed consumer source files.
  The new source is merged, not claimed released or installed. No shared runtime
  deployment occurred; installed-state qualification remains with the rollout owner.
- This worktree and canonical #7938 task remain retained for the producer
  decision. The cold/native budget is still open; no overall completion claim.

Initial native headless matrix, milliseconds (one observation per cell; controlled
two-second bootstrap included in current-row time). These are source-fixture
observations, not a native-compositor or installed-runtime budget pass:

| Optional scalars | Rows | Cold refresh | Cold current rows | Warm refresh | Warm current rows | Cold/warm input ack |
|---|---:|---:|---:|---:|---:|---:|
| Absent | 1 | 5974.40 | 26956.27 | 39.05 | 15869.72 | 1.22 / 1.03 |
| Absent | 100 | 8753.80 | 22717.56 | 37.84 | 16432.25 | 1.38 / 1.24 |
| Absent | 1000 | 7225.37 | 25170.34 | 35.68 | 11757.98 | 1.38 / 1.28 |
| Complete fixture | 1 | 4605.71 | 11463.45 | 37.80 | 7994.68 | 1.44 / 1.36 |
| Complete fixture | 100 | 6094.90 | 15739.28 | 40.21 | 9031.56 | 1.03 / 0.96 |
| Complete fixture | 1000 | 7008.07 | 17905.37 | 36.35 | 8783.78 | 1.13 / 0.99 |

Every sample used one held current-list call. Absent-field samples used three
scalar calls; complete-fixture samples used zero. The real merged engine still
does not supply that complete fixture payload.

### 2026-10-23 — Path-only follow-up resumed

- Resumed the same canonical task without sending a Bridge wake. The source
journal from `e95e5e0e1` is preserved on a backup ref and cherry-picked onto
current Dev; only the adjacent shared index required reconciliation.
- A fresh read-only scan of all 33 open PRs found no `context_cli.py` change.
#7449 still owns `manager_launch_cli.py`; that file and `worktree_identity.py`
remain untouched. The path-only continuation claim was posted and read back
on #7938 without a competing claim.
- The candidate source change added only the canonical project state path and known anchor
to the optional JSON handoff. Missing anchors omit `repo_dir`; no cwd guess,
machine-config load, new discovery or machine field is introduced.
- Two producer tests reproduced the absent handoff before implementation.
Validation covered missing-anchor behavior, forbidden config/topology reads,
unchanged invocation contracts and partial-field consumer fallback counts.
Native path-only scaffolding was prepared to execute the actual source producer
over a process boundary, but was not executed before the authority-equivalence
control below blocked this candidate.

### 2026-10-23 — Effective path authority blocks the path-only candidate

- The candidate added `config_dir` plus the already-resolved registry anchor as
`repo_dir`, without a config or topology read. The bootstrap/context packet
passed 68 cases and the partial-field consumer/pending packet passed 20 cases.
A further authority-equivalence control then **failed**: with a legitimate
machine-local `repos.<project>.anchor` override, `cmd_get("repo-dir")` returned
`configured-anchor`, while the candidate bootstrap returned `anchor`.
- This is supported precedence, not an invented hostile fixture:
`config._load_config_uncached` selects
`machine_repo.get("anchor") or _resolve_anchor_from_registry(...)`.
In contrast, `front_door_cli._anchor_for_project` first uses the project
registry. The bootstrap's resolved anchor therefore cannot unconditionally
replace the established effective `get repo-dir` value.
- The unsafe producer and its unqualified one-query measurement scaffolding were
withdrawn before publication. No producer/runtime code or changefile is left
in this follow-up; #8017's delivered consumer is unchanged. The one-query
target is **not** claimed. No machine-config read was added.
- Smallest identified no-new-read handoff: retain effective paths from the
canonical config already loaded by `manager_launch_cli._is_headless_project`
and carry them through `_exec_worktree_manager`. Open #7449 owns that file, so
it was not edited and its agent was not contacted. Direct Manager invocations
that do not pass through that owner still need the existing fallback.
- Required next decision/dependency: admit a config-dir-only slice with **two**
remaining scalar queries, or retain this producer hold until an authorized
effective-path snapshot is available through the reserved launch seam. A
fresh config read remains outside the granted scope.
- Validation environment note: checkout-local scratch initially allowed Git
probes to discover the surrounding real worktree, invalidating seven
outside-repository controls. A disposable bare Git boundary at the owned
scratch root restored isolation; the complete 68-case packet then passed.
Neither production identity logic nor the assertions were weakened.

### 2026-10-23 — Config-dir-only increment admitted

- Resumed the same task with no Bridge wake and rebased the retained journals.
Fresh inspection of all 31 open PRs found no `context_cli.py` overlap; #7449
still owns `manager_launch_cli.py`. The narrowed claim was published on #7938.
- The production delta is one additive JSON field in the unclaimed context seam:
`project_config.config_dir` from `cfg.project_dir(resolved)`. Plain-text output
is unchanged; there is no repo-dir guess, machine-config load, topology scan,
new dependency or modification to the reserved launch/identity files.
- The new producer regressions failed for the absent JSON handoff before the
change. They also prove that a configured anchor can differ from the registry
anchor and is not exported as the wrong repo-dir. The changed producer packet
passes 18 cases; the consumer/pending packet passes 20 cases.
- Native qualification uses the actual source producer across a separate process
and the actual Manager/held-feed/render path. Its configured-anchor fixture
deliberately differs from the registry anchor; the consumer must retain that
override and make two scalar queries, not one or zero.
- The actual-source native run produced **one passing case and two deadline
failures**, not a full qualification. At one row, cold/warm entry-to-headless
refresh was **9053.01 / 37.87 ms**, rendered current rows
**31427.76 / 23962.83 ms**, and input acknowledgement **1.37 / 1.13 ms**.
Both samples retained the configured anchor and exactly `get repo-dir` plus
`get machine`; no `get config-dir` occurred.
- The 100-row case reached current model rows but missed the existing 25-second
rendered-row criterion. The 1,000-row case failed its warm row deadline before
loader publication. Their unsaved per-sample timings are unavailable, not
zero. No deadline or performance budget was increased. These failures remain
the explicitly retained #7938 native/performance acceptance gap; they are not
reported as passing source-fixture qualification.
- This lane uses a real producer handler where the earlier matrix used a synthetic
bootstrap. It is not a controlled before/after latency comparison. The measured
increment is the eliminated config-dir command with effective repo authority
preserved; cold startup and larger-cardinality acceptance remain open.
