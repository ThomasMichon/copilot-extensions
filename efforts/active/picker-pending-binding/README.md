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
| crooked-sprocket | Separately owned engine bootstrap optimization and optional scalar producer | #7974 |

## Coordination

- **Topology:** independent source PRs; consume compatible optional engine data.
- **Host (owns PRs):** patient-piston.
- **Delegates:** none.
- **Handoff:** #7938 records source/release status and remaining integration qualification.
- Engine files owned by #7974 and native launch maintenance owned by #7788
  remain untouched. No shared runtime deployment is authorized by this effort.
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
fallback. Engine scalar batching remains with #7974's owner, not this consumer.
Review and merge the source changes; installed rollout is separately owned.

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
  input thread, with fallback counts visible in validation. Exact producer shape
  is coordinated with #7974 before wiring, not invented as a competing service.

### Phase 3 — Qualification and publication

- [x] Run targeted regressions and adjacent startup/config/mock/capture tests,
  lint and install-contract checks; add a Manager patch changefile.
- [x] Document the pending-binding behavior, authority transition, error/cancel
  semantics and measurement boundaries. Reuse existing launch/frame-health events;
  introduce no telemetry SDK, sink, network request or per-event child process.
- [ ] Open and steward the implementation PR through passing review, required CI
  and actual merge. Record source commit separately from release and installed state.
- [x] Deferred to `#7938`: Transfer any remaining engine-producer or installed integration qualification
  explicitly to #7938; do not close that issue or claim the full latency budget
  from a synthetic frame-only measurement.

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

## Proposal

Retain the established engine process boundary and existing snapshot/follow data
owner. This closes the Picker responsiveness vision without changing its intent.
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
