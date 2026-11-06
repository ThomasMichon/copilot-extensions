# Fixture-Scoped Native Observation

- **Slug:** `fixture-scoped-native-observation`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed SDK source PRs targeting `dev`
- **Created:** 2026-11-06
- **Status:** Active
- **Vision:** [Plugin services](../../../visions/plugin-services/README.md)
- **Umbrella issue:** #10925

## Guiding Intent

Qualify the shared process SDK's supported native fixture effects without
watching unrelated desktop activity. Observation must be scoped at acquisition,
bound to original native identities, and honest about unsupported coverage.
This is an independent SDK objective, not an MCP implementation, readiness,
handover, release or acceptance prerequisite.

The existing root-only source foundation is not full native qualification.
Opaque descendants and unverified console/Terminal hosts remain unqualified.
The old SDK self-profile stays separate; its whole-desktop packet cannot be
relabeled as scoped evidence.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| soldered-ratchet | SDK source, fixture enrollment and synthetic controls | Managed SDK source worktree |
| battered-sprocket | Coordination, review/publication and subsequent scope decisions | Coordinator |

## Coordination

- **Topology:** Independently reviewed SDK source slices; no shared MCP writer.
- **Host (owns PRs):** battered-sprocket retains publication and integration.
- **Delegates:** soldered-ratchet owns SDK-only source changes, not MCP runtime,
  installer, cleanup or context-worker implementation.
- **Handoff:** Return frozen source and private qualification receipts to the
  coordinator. Keep unsupported/native gates open rather than declaring the
  SDK objective complete from a source commit.
- The MCP effort has no dependency on this effort's Plan or Validation Plan.
  Existing MCP journals are historical provenance, not another active SDK plan.

## Context

#10925 originated during an application investigation. The observer-to-MCP
prerequisite was agent-introduced and has been withdrawn. The original request
and subsequent correction remain in the
[historical inception record](../mcp-session-generation-handover/inception-transcript.md).
The generic SDK requirements now have this single canonical home.

The root-only foundation composes the existing atomic Windows launch owner,
native bindings, source owner and event-pump lifecycle. Its lexical observer
context preserves original launch exceptions and independent root/Job custody;
unknown hook/pin cleanup retains evidence and is not retried implicitly.
The [library README](../../../libs/agent-procutil/README.md) owns the current API.
The [Windows launch pattern](../../../docs/patterns/windows-background-process-launch.md)
owns launch-kind and lifetime conventions.

## Request

The operator originally requested:

> Can we make the observer *not* watch the whole desktop?

The later scope correction was:

> We should disconnect anything to do with MCP from terminal host focus; not sure how that got intertwinced

**Capture validation:** independent SDK qualification is the disposition of
#10925, not an added MCP acceptance requirement. Generic observation work moves
here; application-specific generated-CMD/focus/calibration gates are withdrawn,
not treated as implemented or validated. The following implementation choices
are agent-recommended within that corrected scope.

## Scope Approvals

- Current authorization covers source/documentation decoupling, authoring one
  actual SDK-owned managed-root case, synthetic tests and actor-free collection.
- The delegated source worker is limited to code, documentation, tests and local
  commits; this slice does not authorize that worker to publish. The coordinator
  retains normal publication through the repository's reviewed PR flow.
- Neither that publication ownership nor this source slice authorizes debugger
  implementation/execution, new process graphs, native case execution, GUI
  actions, calibration, privilege changes, new daemons, external attach or rollout.
- A prior bounded test-only `DEBUG_PROCESS` choice is historical, not a current
  implementation or execution GO. Further graph/instrumentation work remains held
  pending an independent SDK decision.
- No former MCP-bound visible-control/calibration approval transfers here.
  Its original limits and unused-or-consumed state stay with its original owner;
  SDK native effects need their own applicable authorization.
- A plan, source hash, typed enrollment or collection result grants no effect
  consent. Existing source provenance, containment and lifecycle owners remain
  authoritative.

## Plan

### Phase 1 - Existing root-only foundation

- [x] Retain the reviewed generic contract and existing-owner pre-resume source
  seam, native identity/birth pins and process-filtered acquisition.
- [x] Retain distinct scoped evidence, strict source/epoch binding and original
  self-profile semantics; no global packet equivalence.
- [x] Retain lexical cleanup across failed launch, exact original exceptions and
  independent root/Job custody, with explicit unconfirmed cleanup evidence.

### Phase 2 - Independent SDK ownership

- [x] Transfer the generic requirements from the MCP effort while preserving its
  historical journals and withdrawing application-specific observation gates.
- [x] Replace application-specific enrollment with the literal SDK case
  `libs/agent-procutil/tests/test_caller_owned_native.py::test_scoped_observer_managed_root`.
  Keep the exact selector, declared case, complete deduplicated source closure,
  native-owner admission and strict schema; do not broaden to arbitrary cases.
- [x] Run focused synthetic regression and actor-free collection of the actual
  SDK case. Collection and default skip are not native acceptance.
- [ ] Land the ownership/source-admission correction through review and required
  CI; source validation does not imply native qualification or release.

### Phase 3 - Separately authorized native qualification

- [ ] Review and qualify the SDK driver's source/provenance/consent admission of
  the dedicated case through the existing finite owner.
- [ ] Execute fresh, bounded SDK-managed-root native controls only after a
  separate GO, preserving original deadlines, output and exact cleanup.
- [ ] Qualify positive owned visibility/focus and negative acquisition/coverage
  controls under separately bounded approval; no borrowed application calibration.
- [ ] Resolve unsupported graph/host requirements or transfer them to a named
  SDK objective before closing this effort. No debugger/new graph is implemented
  while the current hold remains.

## Validation Plan

- [ ] Native subscriptions/queries are fixture-bound before relevant effects;
  no desktop-wide hook, global enumeration, unrelated titles, screenshots or
  foreground history enters scoped consumer evidence.
- [ ] Native identity/birth, source/case/epoch and original-owner bindings refuse
  forged, stale, reused, exited, extra-case or rebound inputs.
- [ ] Pump startup, early exit, callback delay/loss, overflow and cleanup gaps
  cannot manufacture zero-window evidence. Retain the post-exit delivery tail.
- [ ] Supported roots qualify only their actual scope. Unadmitted descendants,
  including early-exiting children, and ambiguous/unsupported host associations
  remain unqualified; post-start snapshots are not before-first-effect proof.
- [ ] Any future host extension verifies its native relationship and exact
  process/thread/window boundary. Shared hosts do not grant whole-host observation;
  `GetConsoleWindow` in a pseudoconsole is not displayed-host attribution.
- [ ] Original exceptions/root-Job custody survive observer cleanup uncertainty;
  no unknown native close is replayed, and close without finish cannot pass.
- [ ] Any future calibration separates workload, restoration and retirement
  evidence. Retain at most the minimal pinned original endpoint needed for
  separately authorized restoration, never unrelated foreground history.
- [ ] The actual SDK-managed-root case is enrolled independently; former
  application selectors and the whole-desktop self-packet are refused.
- [ ] Native results, calibration and source-only synthetic/collection evidence
  remain distinguishable. Required unit admission and its 300-second budget stay
  unchanged; native/threaded controls are not smuggled into that packet.
- [ ] SDK documentation, review, required CI and any separately authorized native
  acceptance are resolved or explicitly transferred before Status becomes Done.

## Proposal

The root-only source API remains the supported foundation. Further descendant
admission must precede effects through the existing lifecycle owner and retain
native birth pins. Job completion notifications alone are asynchronous and
cannot establish that barrier. Cooperative creation or a bounded debug-event
extension may be evaluated only under a later independent SDK decision.

No whole-desktop collection followed by filtering, external debugger attachment,
privilege escalation, new controller/daemon or relaxed predicate is an acceptable
fallback. Unsupported console/Terminal association remains explicit. This
proposal neither reintroduces an MCP gate nor authorizes the held extension.

## Journal

### 2026-11-06 - Independent ownership and scope correction

- Adopted the existing local effort template and #10925 as this SDK objective.
  Preserved earlier root-foundation and cleanup source work rather than discarding
  it; its historical journal remains verbatim in the originating effort.
- Captured the operator's decoupling correction. Generic SDK work transfers here;
  MCP-specific observer/focus/host/calibration conditions are withdrawn, not
  completed validation and not MCP release prerequisites.
- Authored the dedicated SDK managed-root case and exact source enrollment.
  Native execution, debugger/new-graph work and calibration remain held.
- The focused synthetic selection passed 327 tests without skips, retaining the
  cleanup/default-profile controls and rejecting former application selectors,
  undeclared cases and missing/unbound driver enrollment. The actual SDK native
  node was collected once, not executed.
- An actual missing `psutil` import required restoration of that existing SDK
  development dependency in a private test venv, with the seven-day policy
  retained and no global environment or dependency-manifest changes. Private
  JUnit, supervisor and exact-command receipts retain the failed prerequisite
  attempt and the successful source qualification.

### 2026-11-06 - Preserve windowless startup and native retirement budget

- Corrected the documented `EnumThreadWindows` empty-thread FALSE result without
  accepting callback/partial enumeration failures. The original held initial
  thread must remain live and identity-matched across the query.
- Admission failure now signals stop and returns to native owner settlement
  before observer teardown can join a stalled pump. Even an exhausted cleanup
  deadline requests exact owned-Job termination through the existing non-waiting
  step, without granting another verification interval. Unconfirmed root/Job
  custody and original exceptions remain retained independently of the observer.
- The authored SDK root uses the base interpreter, avoiding a Windows venv
  redirector's additional child. The case was collected only, not executed.
- New synthetic regressions reproduced the empty-enumeration and suspended-root
  defects before the fix. The complete bounded source selection then passed
  334 tests without skips and confirmed runner cleanup.
- Canonical payload generation repaired the admission copy; canonical footprint
  refresh preserved contract semantics. Only two actually changed admitted
  source bindings were refreshed; the existing 4,239 nodes, 598 sources,
  251 coverage targets and 300-second required-packet budget remain unchanged.
- No debugger, GUI, calibration, rollout or native fixture execution occurred.
  Independent SDK native qualification and the remaining held work stay open.

### 2026-11-06 - Keep expired retirement and locked evidence unconfirmed

- Tightened the expired callback path to request only exact owned-Job termination.
  Accounting-only emptiness is not native all-member exit proof: no accounting
  query, Job close, root wait, success callback or confirmed-cleanup flag follows
  this request. Original process/Job custody and the operation/native errors are
  retained; ordinary nonexpired cleanup and the general Job API are unchanged.
- Failure logging and evidence capture now acquire the observer lock without
  waiting. A callback stalled under that lock cannot block error return beyond
  the original cleanup budget. Unknown evidence is explicit and unqualified,
  with a null raw stream rather than a manufactured empty one; the live observer
  and original exception retain exact unresolved custody.
- Synthetic regressions reproduced both defects before correction. The bounded
  final selection passed 335 tests with no skips and verified cleanup. The locked
  callback control releases and joins its own portable thread in a finalizer.
  The SDK native case was collected only; no native effects were executed.
- Regenerated the admission payload and refreshed footprints through their
  canonical owners. Refreshed only the changed admitted native-owner source
  digest; required packet inventory and its 300-second budget are unchanged.

### 2026-11-06 - Preserve native fixture finally custody after deadline expiry

- Extracted the actual managed-root case's finally cleanup into a directly
  exercised helper. Expired cleanup now requests termination of its exact retained
  Job before reporting unconfirmed cleanup, without accounting, waiting, closing
  handles or claiming exit. Original operation/native errors and process/Job
  custody remain on `OwnedCleanupError`; nonexpired retirement is unchanged.
- Four synthetic expiry/interruption controls failed before the correction.
  The affected SDK selection then passed 68 tests, with bounded runner cleanup
  verified. These controls execute the same fixture retirement helper, not the
  already-corrected spawn error path. The native case itself was collected only.
- Canonical installation-context sync rebuilt the missing/drifted caller-lifetime
  source archives, followed by canonical payload regeneration and footprint
  refresh. No archive or generated package was edited by hand. No admitted
  source binding, inventory, coverage target or 300-second packet budget changed.
- Native GUI, debugger, calibration, rollout and publication remain unauthorized
  for this worker; no such effects were executed.

### 2026-11-06 - Retain interrupted pre-ident observer startup

- Reused the pipe-worker owner's proven-precreation-refusal rule rather than
  treating a missing Python thread identifier as proof that no worker exists.
  Unknown start interruption retains the exact attempted worker and native pins.
- Added started/completed events around the existing observer pump. Native root
  failure settlement still happens first; observer cleanup waits for completion
  and joins only within the original cleanup deadline. Missing completion retains
  unconfirmed observer custody and never releases pins or qualifies evidence.
- Two composed pre-ident interruption controls failed before correction. The
  targeted refusal/interruption controls then passed, followed by 70 affected
  synthetic SDK controls with no skips and verified cleanup. Each pre-ident
  worker is released and joined by its test finalizer; no native fixture ran.
- The dedicated native case was collected only. No required-packet inventory,
  source binding, coverage target, 300-second budget or runtime package source
  changed. Native execution, debugger, calibration and publication remain held
  for the delegated worker; coordinator publication ownership is unchanged.
