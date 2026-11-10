# Launch Observation Regressions

## Goal

"We need to undo whatever change is causing us to show \"MUX(?)\" in
worktree-manager. It really feels like the `has-session` bug may be
self-inflicted. We need to sweep how we interface with PSMux to ensure this
isn't a \"self-own\", and restore the correct mux status reporting"

Also investigate the reported `embody` regression and New's required-application
observation failure without bypassing admission or affecting existing sessions.

- **Slug:** `launch-observation-regressions`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed source slices targeting `dev`
- **Created:** 2026-11-09
- **Status:** Active
- **Vision:** [Picker](../../../visions/picker/README.md);
  [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #11722
- **Related owners:** #11564 finite mux observation; #11718 legacy PID-sentinel compatibility

## Guiding Intent

Audit this suite's own behavior before attributing failures to PSMux. Restore
actual observations, not optimistic badges. Keep New's required readiness,
selected-runtime compatibility and finite admission budget intact.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source maintainer | Audit, bounded correction, validation and PR stewardship | This effort's source worktree and reviewed PRs |

## Coordination

- **Topology:** One owner, sequential reviewed source slices.
- **Host (owns PRs):** Source maintainer.
- **Delegates:** None.
- **Handoff:** This canonical effort and its remaining acceptance items.
- Do not duplicate #11718's independently owned legacy registration correction.

## Context

- #9155 changed architecture documentation, not executable `embody` behavior.
- An installed Worktrees generation explicitly returns no Windows listing;
  its display adapter therefore reports unknown. #11564 already replaces that
  producer with finite native observation. A merged fix is not installed proof.
- The selected Machines runtime declares required read-only observation support,
  and profile selection succeeds. Discovery alone exceeded a bounded 50-second
  diagnostic; a separate bounded stack capture located full-payload hashing in
  `installation_repair_admission.payload_identity`. Application checks had not run.
- The New caller currently conflates timeouts, selection failures and other
  observation errors into an unavailable-or-unsupported message.

## Request

The Goal preserves the mux request verbatim. The other requests were to
investigate `embody` and this New failure:

> New authoring requires application readiness before allocation. Required
> application observation is unavailable or unsupported by the selected runtime.

Private invocation paths and an unrelated quoted task seed are omitted. The
seed is diagnostic input, not authorization for the action it describes.

## Scope Approvals

Source diagnosis, bounded isolated regression tests and ordinary reviewed
publication are in scope. Existing operational holds remain: no shared-host
PSMux probes, live embodiment/launch, update/install, restart, teardown,
registration reconstruction, service changes, access changes or release of
retained resources. Source completion does not claim installed recovery.

## Plan

### Phase 1 - Review the correction
- [x] Land this proposal through the normal review and CI gate (#11725).

### Phase 2 - Audit and repair
- [ ] Audit mux callers' targets, arguments, environment/CWD, observation provenance, budgets and cleanup; identify installed-versus-source differences and preserve independent owners.
- [ ] Remove redundant readiness-discovery filesystem work without weakening payload identity, linked-path refusal, selected-owner qualification or the existing admission deadline.
- [ ] Make readiness failures identify their stage and timeout versus unsupported selection without exposing raw output or changing refusal semantics.

### Phase 3 - Read-only New observation
- [ ] Review a readiness-only observation scope before implementing it: bind enabled-owner declarations and the actual readiness invocation without constructing an installer plan.
- [ ] Keep full payload identity and executable/installer qualification for ordinary repair planning/apply; a readiness-only digest must never authorize apply.
- [ ] Prove the New observer finishes within the original aggregate budget against a representative payload roster; reject changed declarations, unsupported selected runtimes and incomplete readiness.

### Phase 4 - Deliver and close
- [ ] Land the bounded correction after passing review and required CI.
- [ ] Record source results and any separately owned operational acceptance; archive this effort only after every item is resolved or transferred to a named tracker.

## Validation Plan

- [ ] Prove the payload digest remains content-sensitive and deterministic, and linked/reparse/nonregular/oversized inputs still refuse.
- [ ] Add a deterministic work-count regression for repeated ancestor inspection; use representative real-filesystem evidence to show discovery fits the original budget.
- [ ] Exercise the actual readiness caller boundary with synthetic, isolated owners: supported success, timeout and unsupported selection all preserve admission.
- [ ] Verify mux consumer truthfulness and caller contracts with focused existing tests, without shared-host probes or pretending a source test proves rollout.
- [ ] Record unit, simple integration, clean-room and available external/native tiers, with explicit skipped-tier reasons; preserve unrelated sessions and resources.

## Proposal

The initial ancestor-walk optimization was rejected after review demonstrated
a directory-replacement race. Retain the original per-leaf ancestor checks and
full payload hashing for installer plans. Do not solve latency by increasing the admission timeout, disabling
required readiness, adding a resident cache or silently trusting a newer payload
instead of the selected runtime.

The first bounded source optimization did not meet the real admission budget:
discovery still timed out at 55 seconds, and a shorter instrumented control
remained opening files in the first owner's payload tree. Phase 3 therefore
proposes separating New's explicitly read-only observation from an installer
plan. The former needs the current owner/roster declarations and the exact
readiness action; it must not recursively hash unrelated tests, documentation
or installer inputs. The latter retains its complete existing identity and
effect gates. These scopes must be non-interchangeable and tested before any
apply. This direction is proposed for review, not implemented by the first
source slice.

This closes existing responsiveness and truthful-observation intent rather
than extending a vision. The service-contract and performance-measurement
patterns govern the implementation. No new CLI or wire generation is proposed;
current/prior providers retain the same required observation contract. Runtime
replacement and rollout remain with their existing owners.

## Journal

### 2026-11-09 - Native scope review corrections
- Review identified creation-namespace drift and a scrubbed teammate flag that
  still participated in the worker hash. Native reads now receive the exact
  creation namespace without changing shared environment; both context writers
  treat the flag as transport state.
- The private Windows lifetime fixture now uses a deliberately different
  ambient observer namespace and a parent teammate flag. It must confirm the
  actual private native incarnation before publishing readiness.
- The original 89-test hosted Windows result covers the first source slice;
  changed namespace and worker-scope behavior needs fresh qualification.
- The revised private native lifetime packet passed all six Windows cases
  and four Linux cases (two Windows-only cases skipped on Linux). The enclosing
  workflow then failed in an old active-pane control that still expected a
  PSMux CLI probe. That control is being corrected to assert the native caller
  contract while retaining the TMux syntax check; the workflow is not called green.
- A controlled directory-replacement finding invalidated the attempted
  ancestor-walk optimization. It did not meet the launch budget anyway, so
  restore the original full-plan traversal instead of adding handle/attestation
  machinery. Add deterministic symlink/reparse replacement controls. Phase 3's
  readiness-only scope remains the proposed latency correction.

### 2026-11-09 - First source slice and remaining budget failure
- Initially removed repeated per-file ancestor walks; payload bytes and digest remain
  unchanged. Existing repair-boundary selection passed 120 tests, with 17
  platform/tier skips.
- New diagnostics and the legacy updater controls passed 87 tests, with six
  platform/tier skips. Warm Picker and badge assertions passed 89 tests, but
  local teardown failed on a synthetic launcher executable; a one-case control
  repeated that teardown failure. This is not a clean local suite pass.
- The real discovery-only candidate still exceeded its 55-second execution
  allowance. A 30-second per-owner control found the first payload hash still
  opening files; no application readiness script was invoked. Phase 3 is the
  remaining correction, not a reason to enlarge the deadline.
- Compared #11710's sole production change with its parent: the added
  post-lock expiry check handles four send-only mutation notices, not the
  pre-allocation application observer. It does not explain the captured New
  failure; no conclusion is claimed about a separately unobserved launch error.

### 2026-11-09 - Proposal accepted
- #11725 merged after an approving Copilot review and green required CI.
- Continue Phase 2 in the same source worktree; no operational hold changed.
- The caller sweep also found legacy status-updater fallback and warm Picker
  observation paths that still execute PSMux CLI reads. Confirm their actual
  consumers and add focused source controls rather than treating nominal reads
  as harmless or duplicating #11718's inventory work.

### 2026-11-09 - Diagnosis and proposal
- Separated intentional installed mux-listing suppression from a PSMux failure.
- Isolated profile selection and selected runtime support successfully.
- Discovery-only timeout and stack capture identify the next correction stage;
  no application checks, live mux probes or installation effects were performed.
