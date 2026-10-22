# Picker Pending Binding

- **Slug:** `picker-pending-binding`
- **Repo:** copilot-extensions
- **Branch(es):** independent plan and implementation PRs targeting `dev`
- **Created:** 2026-10-22
- **Status:** Draft
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

- [ ] Land this plan through the repository review gate before implementation.
- [ ] Record the existing targeted-test baseline and exact startup call ordering.

### Phase 2 — Pending binding and current feed

Implementation details below are agent-recommended means of meeting the request.

- [ ] Introduce a lightweight pending-binding screen within the same production
  Textual application, not a second executable or a separate GUI lifecycle.
  It identifies the requested project (or unresolved selection), reports loading,
  accepts Escape, and exposes no operational actions until authority is bound.
- [ ] Defer project roster/default selection and engine bootstrap onto a worker
  after the first refresh. Preserve ambiguous/unknown/no-project failures and the
  engine's project, cwd and default-live decisions. Keep mock/capture compatibility.
- [ ] Marshal successful binding onto the render thread before constructing the
  operational PickerScreen. A cancelled/unmounted pending screen must reject late
  completion; a worker must not change process cwd or start housekeeping after
  cancellation. Bootstrap failure remains visible and returns failure on dismissal.
- [ ] Remove the redundant advisory one-shot read from initial live setup; the
  authoritative snapshot-plus-follow loader is the sole initial row producer.
  Keep pivot discovery independent and retain local-first loading of remote tabs.
  No stale advisory payload then exists to overwrite current rows.
- [ ] Accept optional engine bootstrap `project_config` fields `config_dir`,
  `repo_dir`, `machine`; validate supplied values and bind them to the resolved
  project. Existing memoized scalar calls remain the compatible fallback, off the
  input thread, with fallback counts visible in validation. Exact producer shape
  is coordinated with #7974 before wiring, not invented as a competing service.

### Phase 3 — Qualification and publication

- [ ] Run targeted regressions and adjacent startup/config/mock/capture tests,
  lint and install-contract checks; add a Manager patch changefile.
- [ ] Document the pending-binding behavior, authority transition, error/cancel
  semantics and measurement boundaries. Reuse existing launch/frame-health events;
  introduce no telemetry SDK, sink, network request or per-event child process.
- [ ] Open and steward the implementation PR through passing review, required CI
  and actual merge. Record source commit separately from release and installed state.
- [ ] Transfer any remaining engine-producer or installed integration qualification
  explicitly to #7938; do not close that issue or claim the full latency budget
  from a synthetic frame-only measurement.

## Validation Plan

- [ ] Regression-first: gated two-second project/bootstrap work must not delay a
  useful first frame or Escape; engine failure and cancellation discard late
  success without exposing actions or starting post-binding maintenance.
- [ ] Regression-first: a blocked advisory hook must not be called or delay a ready
  authoritative local loader; exactly one initial loader start, current rows
  retained, and slow remote sources do not delay the local source.
- [ ] Scalar controls: complete optional payload uses zero legacy scalar calls;
  older payload uses at most one call per scalar per bound project; project switch
  invalidates binding and caches; malformed optional values do not select paths.
- [ ] Run the actual Manager entry and real Textual consumer under isolated
  synthetic engine/registry inputs: cold and warm samples at small, medium and
  large roster cardinalities. Report entry-to-interactive and entry-to-current
  rows separately, engine-call counts, and input latency; label fixture limits.
- [ ] Preserve explicit project/default/ambiguous/unknown selection, live/local,
  mock/preview and screenshot behavior. Verify exit status and cwd semantics.
- [ ] Record unit, lightweight integration, clean-room and native/live tiers
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
