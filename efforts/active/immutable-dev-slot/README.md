# Immutable Dev Slot

- **Slug:** `immutable-dev-slot`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs to `dev`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** [Immutable runtime](../../../visions/plugin-services/README.md#immutable-versioned-runtime), [bidirectional cutover](../../../visions/plugin-services/README.md#zero-downtime-cutover), [source assembly](../../../visions/plugin-services/runtime-assembly/README.md#selected-development-source-assembly)
- **Coordination:** #5472 (retained, not a replacement issue)
- **Related effort:** [Source-Managed Runtime Installs](../source-managed-runtime-installs/README.md), #7028/#7029

## Guiding Intent

Dev identifies source, not a mutable environment. Deploy only immutable venvs
whose generation identity binds the complete frozen source hash and admitted
build/dependency inputs. Build beside the serving generation or reuse its exact
healthy match, select it as current, and start the incoming owner. Prefer
health-gated handoff and drain, but permit bounded cold takeover when contracts
are incompatible or handoff fails. A running owner is superseded by full identity
inequality with current selection, not version ordering. Compatible forward and
reverse baton-passing use the same best-effort contract. Interrupted clients
and owned sub-daemons recover through reconnect/retry and durable state.

This replaces the mutable premise, not its unfinished acceptance. Preserve
delivered primitives and all thirteen adopters' construction, admission,
retention, distinct-generation repair, claim restoration and installed evidence.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Intent reconciliation coordinator | Owns this finite vision/effort supersession and publication slice, not runtime implementation | Managed source worktree and repository PR |
| Slot-admission coordinator | Retains shared lifecycle and first-adopter integration responsibility | Existing #5472 coordination |
| Adopter implementers | Own separately assigned installer/service integrations and native acceptance | Existing per-adopter claims and PRs under #5472 |
| Source deployment coordinator | Owns clean merged Dev selection, Manager integration and deployment acceptance | #7028/#7029 and canonical related effort |

## Coordination

The direct #2871 audit is conjunctive across this ROOT and the other named receiving
ROOT: Immutable Dev Slot retains six runtime families and Vendored Installer Engine the
budget-guidance audit-only seventh row. Preserve the combined seven-family ledger, same
#5472/adopter/service/native owners, Worktrees' permanent service-engine exception, and
budget-guidance's non-rollout exception.

- **Topology:** independent coherent PRs; preserve existing implementation and open PR ownership.
- **Host (owns PRs):** each named slice owner; intent reconciliation does not take over the runtime campaign.
- **Delegates:** no new runtime assignments or rollout authority from this plan.
- **Handoff:** read this contract and the assigned slice's evidence before execution; a merged prerequisite is not portfolio completion.
- Reuse the installer-engine, versioned-runtime and service lifecycle owners.
  Keep #6424 descendant settlement and #6389 listener custody intact.
  Source deployment, recurring maintenance and #7758 execution-driver work
  remain in #7028, not a second updater or implementation framework here.

## Context

The [abandoned mutable effort](../../2026/10/22%20mutable-dev-slot/README.md)
preserves delivered Phase 1/2 work and the detailed
[Phase 3 evidence](../../2026/10/22%20mutable-dev-slot/phase-3-runtime-admission.md).
Its unchecked work is transferred, not completed by renaming it. Shared
admission/fingerprinting, kernel construction and retention ownership, and
first-adopter source-generation work are reusable progress, not proof that all
adopters meet the contract. The canonical synchronizer inventory determines
the portfolio; do not infer identical defects across installers from a list.

The [development pattern](../../../docs/patterns/mutable-dev-slot.md) states
the new target and labels remaining editable behavior as legacy reality.
Source-Managed Runtime Installs remains the canonical "deploy from Dev branch"
home. Dev/Main release promotion (#3336) and test portfolio #1303 remain valid,
separate work, not superseded efforts.

## Request

Operator direction, verbatim (2026-10-22):

> So, mutable dev slot should probably be superseded by "immutable dev slot", and the "deploy from dev branch" effort. Basically, always deploy using an immutable venv, tagged with the hash of the source. Just set the new dev slot as current and handle cutover normally by giving the incoming version a start command and letting the old version shut off. We should endeavor to support baton-passing both ways; a version knows it's "old" simply because it's "not current", not via any ordering relationship between the active and owner versions.

Operator execution instruction, verbatim:

> Great. Update relevant visions and efforts to align; close out and abandon superseded efforts and off-course work.

Operator follow-on, verbatim (2026-10-22):

> One rule we need to add about handoffs: handoffs are *best effort*. If the contracts are incompatible, outgoing daemons are welcome to just shut down everything they own and quick-terminate, leaving the newcomer service to recover from cold. Both sides need a degree of fault-tolerance: the outer consumer of the service needs to be able to handle a momentary outage or cut-off of stream (it's like a network failure), and the sub-daemons need to be able to handle an abrupt shutdown and support a retriable flow (it's like a power outage). If we produce a new version of a service in the dev slot, with breaking versions of all contracts, and start it up, we expect it to basically abruptly take over. One way to ensure the "drop on the floor" effect is for the newcomer daemon to force-terminate the old daemon if it can't facilitate a successful handoff. Rather than fighting or refusing handoff or shutdown, outgoing daemons just need to maintain (reasonably) durable state, such that a cold-start form the new version can roughly continue without major chaoes ensuing.

**Accepted interpretation:** graceful handoff is preferred, never an
incompatibility or indefinite-drain veto. A confirmed selected replacement may
retire the old daemon and its owned scope before incoming readiness, including
identity-checked force termination, then recover cold. Clients tolerate network-
like interruption; children tolerate power-loss-like shutdown and retriable
flows. Durable-state compatibility still gates safe recovery/rollback; breaking
handoff contracts alone must not block takeover. This is a documentation rule,
not authorization to terminate live services in this task.
The decomposition and validation matrix below are
**agent-recommended**, preserving earlier reviewed acceptance rather than
adding operator-requested runtime scope to this documentation task.

## Plan

### Phase 0 - Reconcile intent and retire the superseded plan
- [x] Revise the existing service/runtime-assembly visions and development pattern; capture the operator's words without relaxing immutable deployment.
- [x] Abandon/archive the mutable effort with explicit transfers, preserving delivered work and journal history.
- [x] Land this reconciliation through review, then publish the final disposition to existing coordination owners (#8065 and the closing coordination comments below).

### Phase 0a - Best-effort handoff amendment (documentation only)
- [x] Land the revised service vision, binding patterns and effort acceptance, retaining implementation ownership and all unproven native/runtime gates (#8090).
- [x] Validate the literal follow-on, document links and canonical schemas; publish the merged rule to the existing #5472/#7028 coordination owners.

### Phase 1 - Complete shared admission and immutable development
- [x] Reconcile existing shared/first-adopter deliveries against the archived Phase 3 evidence before choosing the next implementation slice; do not redo delivered work.
- [ ] Freeze every declared source, vendor and selected dependency input before construction; identify the immutable non-editable generation by source and assembly hashes separately from package version.
- [ ] Reuse exact healthy completed content without writers; refuse conflicting completed content even under force. A new edit builds a distinct generation, never rewrites or follows a checkout.
- [ ] Complete bounded kernel-backed first-build coalescing and revalidation, retaining the construction descriptor through publication and releasing it on success, failure, cancellation or process death.
- [ ] Preserve atomic ready/rollback retention across construction release, concurrent GC, selection, promotion/drain and rollback; no mtime pins or collection races.
- [ ] Preserve whole-operation repair/cutover authority and consistent lock order with construction and installation governance; broken published identities require a distinct health-gated repair generation.
- [ ] Retain explicit claim ownership, original restore target and deployed release/status accessibility, without retaining editable deployment as a target.
- [ ] Distinguish contention timeouts, malformed evidence, permission/storage failure and genuine health repair; reclaim only owned unpublished candidates.
- [ ] Vendor canonical helpers and enforce the executable install contract without adding a sibling-runtime or checkout dependency.

### Phase 2 - Bidirectional owner cutover
- [ ] Propagate full generation identity through selectors, manifests, discovery, running-owner evidence and recovery; package-version ordering never defines supersession.
- [ ] Current selection starts the incoming owner through the existing service lifecycle. Prefer compatible readiness/handoff/drain, but incompatible or non-settling contracts permit bounded owned-scope shutdown and cold takeover before readiness.
- [ ] **Agent-recommended decomposition of the follow-on:** route cooperative and forced predecessor retirement through existing serialized lifecycle authority, revalidate installation/selection/process identity, confirm old-owner exit or write exclusion, and never terminate unrelated/current incoming owners.
- [ ] Prove forward/reverse and same-package/different-source graceful replacement, breaking-contract cold takeover, interrupted transition recovery and exact claim-release restoration. Failed construction still preserves the working generation; cold-start failure reports an actual outage/recovery outcome.
- [ ] Qualify retriable consumers after lost replies/cut-off streams and crash-consistent child recovery after abrupt owned-scope shutdown; reconcile duplicate/unknown effects rather than promising exactly-once execution.
- [ ] Keep release-channel ordering separate from owner obsolescence; incompatible durable state requires explicit safe recovery or refusal, not an implicit wipe. Handoff incompatibility alone is not state incompatibility.

### Phase 3 - Complete each adopter and reconcile guidance
- [ ] Finish agent-worktrees on PowerShell and POSIX through the full lifecycle, not just shared-core completion.
- [ ] Complete the remaining canonical inventory in independently owned waves; inspect actual admission and fingerprint behavior per adopter and preserve its health/service hooks.
- [ ] For every adopter, prove ordinary reuse/refusal, immutable dev/release restoration, distinct-generation repair, retention and bidirectional cutover before checking it complete.
- [ ] Remove live editable/hot-patch recommendations as implementation lands; keep compatibility commands honest and guidance matched to available behavior.
- [ ] For each implementation wave, obtain review, merge, verify release and complete its separately authorized installed-generation acceptance with the existing rollout owner.
- [x] Deferred to `#7028/#7029 (source-managed-runtime-installs)`: clean merged Dev source selection, Manager/deployment policy, maintenance and assembled native rollout; retain that effort's full remaining Plan/Validation Plan and #7758 driver.

<!-- backlog-reconciliation:7556:transfer:2871:to:efforts/active/immutable-dev-slot/README.md -->

Add this exact six-runtime-family portion of #2871 directly to ROOT Phase 3 Plan AND
corresponding Validation/continuing Coordination, preserving all existing
thirteen-adopter work, #5472 source claims, source-managed deployment ownership, Journal
and exceptions. The seventh budget-guidance audit is separately retained in the existing
Vendored Installer Engine ROOT and is a conjunctive receiving dependency, not silently
omitted.

### Six-runtime-family source/dependency freshness audit retained from #2871
- [ ] Audit agent-worktrees, agent-codespaces, agent-index, agent-vault, agent-ssh and
  agent-logger INDIVIDUALLY on PowerShell and POSIX. Map initial install, update and
  each actually reachable compatibility/fallback branch to its current constructor;
  explicitly document retired/unreachable historical paths rather than blindly patching
  matching text or assuming all wrappers identical. Preserve Index host/engine
  distinctions and each service's own lifecycle hooks.
- [ ] For every applicable route, prove fresh own-package code AND the full declared
  local tool.uv.sources dependency graph after same-package-version source changes.
  Preserve real local package/name/origin/dependency validation and installed consumer
  health; own-package reinstall alone is not dependency freshness.
- [ ] Accept complete frozen, verified, non-editable input-graph construction with
  distinct qualified source identities as the current equivalent of the historical
  --reinstall-package/--refresh-package recommendation. Where legacy mutable/local-path
  compatibility remains genuinely supported, establish its actual
  cache/build/installed-metadata behavior and a justified safe remedy. Flag count or an
  unchanged unchecked document is not defect proof.
- [ ] Preserve exact healthy same-input reuse, completed-content refusal and
  distinct-generation health repair. Never rebuild published numbered content in place,
  delete global caches, apply arbitrary force or revive mutable deployment as the
  target. No unrelated new all-host/adopter completion gate is added to this finite
  audit.
- [ ] Preserve agent-worktrees' permanent service-installer-engine adoption exception:
  freshness/admission qualification does not require adopting that service engine. Keep
  current vendored canonical graph/origin/health mechanisms and native entrypoint
  ownership rather than introducing another installer framework.
- [ ] Credit #6598/#6707 qualified construction progress and #6788's test-runner-only
  explicit reinstall fix at their exact limits. The test-runner's bounded claim does not
  transfer or complete plugin installer/production acceptance.

### Validation and continuing coordination for the six #2871 rows
- [ ] Retain a combined SEVEN-row ledger spanning these six runtime families and
  budget-guidance's separately owned audit-only row. Record both-platform
  initial/update/reachable-compatibility source-to-consumer mapping, own and transitive
  local dependency edits, positive same-input reuse and meaningful negative sensitivity.
  Attribute source/fixture, supported native, release-containing and installed outcomes
  separately; perform no qualification merely by moving the tracker.
- [ ] Preserve #5472/adopter construction/admission/retention/native claims,
  service-specific owners, the permanent Worktrees exception and the budget non-rollout
  exception. Ordinary implementation proceeds through existing independently owned
  reviewed slices, not reassignment or new generic ACKs.

Conjunctive receiving gate: BOTH this complete six-family intake AND the complete
budget-guidance intake in the existing Vendored Installer Engine ROOT must receive NEW
normal reviewed ACTUAL merges plus FULL CURRENT ROOT Plan/Validation/Coordination
readback before #2871 closes. If either intake is incomplete, ineligible or changes
custody, retain the whole source. The actual seven-family freshness audit remains
unchecked; this is not a feature or native PASS.

Current best-effort handoff interpretation: references above to compatible
forward/reverse handoff do not reinstate zero-outage continuity, a compatibility veto,
or incoming successor readiness before every old-runtime retirement. Preserve the
accepted bounded, identity-checked cold-takeover route when handoff breaks, explicit
outage/unknown recovery, and client/child retry obligations. Actual candidate/output CLI
health still gates candidate promotion and completion, and a verified retained
previous-good generation must survive failed construction or cutover. Best-effort
continuity does not make an unhealthy candidate or corrupt current/LKG evidence
acceptable. This records the existing reviewed direction; it authorizes no live force,
service, runtime, or deployment operation.

## Validation Plan

- [ ] Resolve every directly retained #2871 Plan/Validation clause above at its original
  finite scope and evidence tier, preserving alternatives, same owners, holds, and
  current portfolio bounds. Administrative intake is not implementation, native
  acceptance, rollout, or completion of unrelated parent work.

- [ ] **Frozen inputs:** changing/removing the worktree after construction cannot change deployed imports; source-only and external vendor changes produce distinct identities.
- [ ] **Concurrency:** real competing installers yield one exact-input build and bounded healthy reuse; conflicting content refuses after the winner publishes. Exercise cancellation, process death and storage/permission errors separately.
- [ ] **Immutability:** both native entrypoints leave completed numbered and development generations byte-for-byte unchanged on reuse and forced/content-conflicting attempts; legitimate out-of-slot reconciliation still runs.
- [ ] **Retention/repair:** concurrent GC cannot collect ready/rollback-pinned generations; repair retains whole-operation authority through promotion, drain or rollback without lock inversion or changing the old slot.
- [ ] **Baton both ways:** replace numerically higher with lower, same-package different source, and return to the prior generation; prove preferred graceful ordering and bounded cold takeover when all handoff contracts break, without version-ordering or old-owner consent vetoes.
- [ ] **Cold recovery:** incompatible/refused/hung handoff permits owned-scope quick exit or identity-checked termination before incoming health, followed by exclusive recoverable execution, durable/checkpointed progress and explicit readiness or failure.
- [ ] **Fault tolerance:** simulate abrupt child death, truncated streams, lost side-effect replies, replay, replacement-caller death and PID/owner mismatch. Prove reconnect/retry and deduplication/outcome reconciliation, not universal exactly-once or zero-outage behavior.
- [ ] **Claims:** competing owners refuse; iterative builds preserve the original restore target; deployed release works without the source checkout and failed restore retains attributable ownership.
- [ ] **Portfolio:** record every canonical adopter's actual two-entrypoint, native process and installed acceptance evidence. Shared primitives or one pilot never close #5472.
- [ ] **Platforms:** qualify Windows, Linux and macOS separately, with named tracked gaps where a lane is unavailable; use isolated roots and owned processes, never live user state as a fixture.
- [x] **Guidance:** audit new intent, archive transfers, links and literal request capture; retain explicit implementation/rollout gaps instead of claiming documentation deploys behavior.

## Proposal

Extend the existing immutable installer and service lifecycle; no replacement
framework, new mutable slot, parallel updater or new coordination umbrella.
This reconciliation changes intent and effort ownership only. Implementation,
native installation and rollout remain separately assigned and gated.

## Journal

### 2026-10-24 - Preserve the source-size gate without widening a ceiling

Current integration exposed a real 1011-line dependency-policy module after
combining the existing closed-wheel behavior and UV adapter. Its balanced
settings-projection reader moved unchanged into the existing configuration
companion; the policy's private entrypoint and exact error type remain.
No new helper file, resolver behavior, version edit or ceiling waiver was
introduced. All 307 policy/compile controls passed in isolated seven-day-aged
tooling. Canonical synchronization, all 82 contribution-scoped source caps and
pending changefile coverage passed.

### 2026-10-24 - Current closed-wheel admission retained during primary integration

The backed primary rebase retained the development baseline's closed-wheel
construction input alongside the expected frozen-source pin. Both ordinary
admission and pin revalidation bind the selected wheel receipt as well as
source, extras and local-package selection; neither silently becomes
source-only. New positive/negative controls distinguish changed source from
changed closed approval binding. All nineteen closed-wheel controls passed,
including actual offline construction and healthy reuse.

Canonical synchronization also retained the current six Python engine
consumers, including the existing SSH and Pull Requests adopters, and the
remaining dependency-policy/config copies. The rebased freeze/content-pin
packet passed all thirty cases; the mode/guardian packet passed seventy-three
with one platform skip and three separately selected native-repair cases
excluded. These preserve the first-adopter proof boundaries; a current native
480-second legacy pass and real POSIX runs remain pending. Publication will be
draft until those outcomes and normal review/CI gates are resolved.

### 2026-10-24 - Native POSIX lane landed; measured remaining legacy read cost

#8587 merged as `0aab6b7c8661efa4e0bb693c0272bcd9e71c0695` after current-head
approval and green required CI. The initial dynamic runner matrix correctly
failed the trusted-CI guard; the helper was changed to two literal Linux/macOS
jobs, each retaining both native modes, rather than weakening that guard.
All 110 actual-YAML and exact-roster controls passed independently. The helper
was finalized normally. This enables immutable-source native qualification,
not actual POSIX runtime acceptance; dispatch still requires the integrated
runtime candidate to be published.

The packet-repaired legacy run still missed its 480-second outer command
bound, without the earlier acknowledgement permission failure. Read-only,
ancestry-filtered Python stack samples then disproved the suspected post-body
legacy ownership deadlock: the outer lifecycle was still waiting for its
child, which progressed through retained-control verification, real candidate
construction and helper publication. The late sample was reading the complete
source graph in `helper_publication_plan` during native completion.

A bounded read benchmark over the actual fifteen-package, 1552-file graph
(24,081,899 bytes before this follow-up) observed a 43.297-second first serial
read and 1.844-second warmed serial read; the warmed four-reader measurements
were 0.719/0.609 seconds. Cold and warm figures are not interchangeable.
The existing source component now batches only read-only Windows fingerprints,
retaining the exact framed bytes and ordered per-file proof; collector-bearing
admission remains sequential and complete before copying. Actual full framed
hashes matched in every comparison: 1.500/1.281 seconds batched versus
2.110/2.047 seconds serial in the warmed graph. This is not yet a native
480-second performance pass.

All 36 fingerprint controls passed with three platform skips, including
bounded four-reader overlap, exact framing, strict worker errors and sequential
callback sensitivity. All 30 selected engine freeze/content-pin/immutable-dev
controls passed, including real full-graph closure and the original
before-copy mutation barrier. No full-content verification, native
inspect/completion check or production deadline was removed.

### 2026-10-24 - Repair registration packet races before repeating native qualification

Legacy Windows now verified installed control ABI 1 without constructing a
second control environment, but its next full run still failed at `dev-first`.
The captured native trace identified a `PermissionError` opening the published
registration acknowledgement. A deterministic Windows witness reproduced the
cause: the publisher may still hold DELETE access after atomic rename exposes
the name, which the CRT reader's sharing mode refuses. The packet reader now
uses an ordinary read handle with READ/WRITE/DELETE sharing, preserving the
actual ACL failure and opened-file identity checks; it does not retry or hide
permission failures.

An independent read-only custody review also identified a distinct
cancellation race: a client's own deadline may withdraw its request after the
guardian's directory snapshot and before its read. Only that
`FileNotFoundError` now means cancellation; a separate denied-storage
regression still fails explicitly. The real canonical lease must still precede
acknowledgement, and whole-Job settlement still precedes release.

All 71 focused mode/guardian controls passed, with one platform skip and three
separately selected native repair cases excluded. The canonical fix was
synchronized to all four Python engine consumers. The full legacy run remains
unqualified until the packet repair clears the unchanged native budget; the
earlier namespaced pass and these focused witnesses are not substituted for it.

### 2026-10-24 - Guardian native follow-through resolved and contributor PR merged

#8527 merged as `b6baf1aa52ac61096f0728ff8f9f348bf0100a1c`, supplying the
manual, immutable-source macOS packet without widening required CI. The first
actual run, [40085693968](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40085693968),
passed the caller-owned, installer-guardian and shim families but exposed the
companion lifecycle failure: `FinalizeLock` truncated Darwin's
`seconds:microseconds` birth identity and could reclaim a live owner's lock.
The inline roster also needed its existing newly declared elevation entry.
These failures were repaired, not waived.

#8560 merged as `8f53af6dfc7c50957fb412d6925f6d5c0aa23b26` after current-head
approval, resolved feedback and green required CI. Nine focused Windows
lifecycle controls, the original real companion node and all ninety-seven
instruction-delivery controls passed. Its runtime/test fix also passed the
fresh native [40086925741](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40086925741)
packet on immutable `a29c0aaf36ffafe91fcc232c0ad6bb94b8056f05`, Python 3.11.9,
macOS 26.6.2 arm64: caller-owned 88 passed/4 platform skips, installer guardian
10 passed/5 platform skips, real companion 1 passed, inline roster 1 passed
and shim family 6 passed. The snapshot preserves the unchanged contributor
head plus the merged development baseline and separate native-lock repair;
no competing PR or contributor-branch edit was made.

The disclosed follow-through conditions were then recorded on #7753, which
actually merged as `7d747af2f19ff813619562fe6c562e093e23dc74` after its existing
current-head approval and green required CI. The complete ROOT Plan,
Validation and Coordination were re-read after both implementation merges.
These prerequisites close the guardian review/failure-follow-through track,
not the immutable adopter campaign or installed rollout.

The current legacy Windows first-adopter run still failed its unchanged
480-second outer command limit at `dev-first`, after logging target readiness
and settled transition. It retained the duplicate private control construction;
the namespaced success does not qualify this legacy entrypoint. The next
bounded slice admits the same fully retained, ABI/health/origin-verified
installed control on legacy Windows, with the existing lifecycle governance
and whole-Job co-retention intact. Both governance modes passed 68 focused
mode/guardian controls with one platform skip and three separately selected
native-repair cases excluded. Its complete legacy native rerun remains pending;
no deadline was enlarged.

### 2026-10-24 - Complete Windows namespaced immutable lifecycle qualified

The current `b3550d3268` source passed the complete namespaced development
and original-release restoration fixture: one native case passed in 1487.72
seconds, 1515.67 seconds including isolated runner preparation and cleanup.
The 480-second production context watchdog and 2400-second test-item limit
were unchanged. This exercises real installed retained-control reuse across
source-changing generations, exact-input reuse, conflicting-content refusal
and original claim restoration; it is not just a mode-owner unit proxy.
No source or fixture edits occurred during the run.

The corresponding current legacy-entrypoint qualification is running
separately against the same code and unchanged deadlines. The full ROOT
Plan/Validation/Coordination remains Active: this one native platform/mode
does not prove the complete assembly identity, bidirectional service/cold
recovery, other adopters, release-containing evidence or installed rollout.
No live service update or user-owned daemon termination was performed.

### 2026-10-24 - Baseline portability follow-through landed; bootstrap prototype continues

The operator directed, verbatim: "Keep driving into resolved and your effort is completed".
This retains the complete parent Plan/Validation gate; a prerequisite merge is
not completion or live rollout authority.

#8509 merged as `d3395b5b422724d8314a01019e34b6722fbfa39d` after current-head
Copilot approval and green required CI. The separate test-only repair preserves
#7753's contributor branch and EPERM implementation: module-local platform
simulation fixes five Linux-specific procutil parametrizations and the harness
Windows-shim fixture; the inline guard now reflects its existing canonical
declaration. Windows validation passed five focused procutil cases, eighteen
reviewer-source cases and ninety-seven instruction-delivery cases. The original
companion lifecycle/concurrency node passed on Windows without a speculative
patch. Its disclosed macOS failure remains unproved here; source repair and
Windows/CI results do not constitute native macOS revalidation or an accepted
exception. #7753 now has current-head Copilot approval, but its remaining native
follow-through still needs actual disposition.

The full current ROOT Plan, Validation and Coordination were read after this
merge. No runtime/adopter checkbox is closed by the test-only prerequisite.
The next first-adopter prototype reuses an independently health-verified,
retained installed control interpreter under an explicit mode-owner ABI, rather
than transferring the cutover guard. The actual installed owner continues to
hold namespace -> cutover -> construction order, with new frozen construction
provenance kept distinct from its older control build. First-touch,
incompatible-control and damaged-target repair retain the existing cold
bootstrap; this is not a claim that every path now builds only once.

The bounded source scope is declared in
[#5472](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6381219587).
Historical ownership is coordination, not an indefinite ACK gate. The optional
Windows guardian policy permits explicit durable-daemon breakaway for this
whole-operation owner while constructor children remain strict by default;
the existing guardian still retains the control interpreter through actual
owned-child settlement. No replacement supervisor, fake held guard or early
release is introduced. This prototype still requires positive/negative
contract, real caller-death/daemon-lifetime and complete native qualification.
The 480-second production context watchdog is unchanged.

The initial whole-operation Job wrapper exposed a further lifetime obligation:
ending that Job can end a nested guardian before all of its writers are
signalled exited. The finite outer guardian now co-retains each registered
candidate before those nested writers start, and keeps the actual leases until
its whole Job is verified empty. The registration acknowledgement is not
cutover authority. Scope execution is explicitly Windows-only; POSIX keeps the
existing cold path.

Sixty-six contained mode/guardian controls passed with one POSIX-only skip;
three explicit native repair cases were selected out for their separate
portfolio. Uncontained physical proofs also passed success/timeout with strict
and lifecycle Jobs, caller-process death, and two complete caller-Job-close
cycles with nested writers. The latter checks actual duplicated process
handles and the specific caller Job, not any ambient host Job. A passive Win32
observer recorded no new visible console windows or foreground transitions.
Venv redirector handles were rejected as a proxy during fixture development;
the witnesses use actual base-interpreter processes. All finite children were
waited out before cleanup. Complete source-changing development and restoration
still require their unchanged-limit native rerun.

Pull-forward retained the new canonical dependency-profile and compile-export
contracts. The profile-aware aggregate/per-file digest and frozen-pin checks
were integrated together; the deep-Windows adapter now preserves the compile
operation as well as configuration and seasoning. All 328 dependency-policy,
compile and executable controls passed in an isolated, seven-day-seasoned tool
environment after the first attempt exposed missing local build tools.
The combined engine/mode packet reached its outer wall-clock limit after 114
visible passes; it is not a complete pass. Partition its targeted integration
checks rather than silently enlarging a production or item deadline.

The source-size audit then required a real extraction rather than a ceiling
waiver: fingerprint framing moved into its existing source companion and the
deep-UV adapter into its existing configuration component. Public framing,
collector/callback, resolver and compile/install behavior are unchanged.
Initialization is serialized, with a fresh lock after fork; the existing
read-only component loader creates no input bytecode cache. Thirty-two
fingerprint controls passed with three platform skips, and all 328 dependency/
compile controls passed again. Only the affected runtime ceilings were lowered;
unrelated baseline entries were left untouched.

### 2026-10-23 - Coordinate a single-build bootstrap

The operator authorized expanding the first-adopter slice into a coordinated
single-build bootstrap redesign, rather than extending the 480-second production
context-child watchdog. Installed-runtime execution, namespace -> cutover ->
construction ordering, retained generations and process-death/descendant
settlement remain mandatory. This is design authorization, not a transfer of
another owner's source claim or live rollout permission.

Read-only tracing found no existing cross-process cutover-lease adoption API.
The current Windows installer guardian transports caller liveness and retains
the candidate until its strict Job settles; the POSIX guardian retains the
construction descriptor. Neither contract proves that a different process owns
the Worktrees cutover guard. A numeric handle, parent-written ticket, early
release/reacquisition or a fabricated held guard is not an acceptable substitute.
The existing installed mode owner acquires namespace and cutover authority before
candidate allocation and keeps both through settlement.

The bounded coordination request is recorded on
[#5472](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6379439958),
preserving the #6424 guardian and #6389 listener owners. Worktrees orchestration,
design and consumer tests stay with the Slot-admission coordinator; shared
guardian/process-death changes require actual owner agreement first. No guardian
code changed, and no unchanged whole-flow retry was started. The complete
namespaced native gate, review/merge/release and separately authorized installed
acceptance remain open; this investigation does not complete Phase 1.

### 2026-10-23 - Native repair and legacy development qualified

The single-read admission implementation passed native repair through Windows
PowerShell, PowerShell 7 and Git Bash: three cases in 1307.74 seconds, retaining
the approved 480-second command and 650-second item bounds. The full legacy
development lifecycle also passed in 1433.32 seconds with its unchanged
480-second command limits, including immutable iterations, exact reuse,
source-independent installed imports, refusal controls and deployed restoration.
An earlier run missed that wall-clock limit after reporting settled publication;
the deadline-specific observation subsequently found the first invocation
already finished at approximately 393 seconds and the second build underway.
That observation does not establish a persistent post-publication defect or
license removing the deadline.

The namespaced case now completes its first development construction and
selection within the unchanged production context-child deadline. Its following
import observation exposed Windows' implicit executable-name length limit.
The native fixture now supplies an explicit executable for both import probes
and deployed commands, matching the existing lifecycle invocation pattern.
A fresh venv with an executable path longer than 260 characters reproduced
Windows error 206 without that argument and executed successfully with it.
All assertions and timeouts remain; the complete namespaced case still requires
requalification before publication.

That rerun completed both native development builds and their import checks.
It then exposed a fixture assertion that conflated the input-worktree path
with the authorized deployment footprint. The existing bootstrap contract and
its unit control deliberately retain these separately. The native case now
checks the original and both development builds' actual `BUILD_INFO['source']`
paths as well as the manifest's authorized footprint; it does not substitute
authorization content for the source-content or immutable-import assertions.
Complete namespaced reuse, source-deletion and restoration evidence is still
pending.

The following complete attempt again reached the unchanged 480-second
namespaced child watchdog before development construction settled. A proposed
combined fingerprint/copy read was rejected: its mutation control showed that
interleaving copies with the initial fingerprint could admit a change to an
input not yet read. The proposal and its vendor copies were removed completely,
preserving the original before-copy barrier and every existing mutation guard.
There is no safe qualified replacement for that boundary in this slice.
The namespaced deadline remains an explicit blocker, not a passing timing result
or authorization to extend a production watchdog.

A subsequent implementation preserves that barrier: first-fingerprint bytes
are retained in a bounded spool, and no snapshot file is copied until the full
baseline finishes. The pre-change graph measurement contained 1,519 files
totaling 23,484,422 bytes. The buffer retains at most 32 MiB before disk spill,
including explicit rollover before an oversized file is written, and closes before frozen-tree
validation. Independent after-copy source and frozen-content checks remain.
Eighty focused lifecycle/freeze controls passed, including unchanged mutation
refusals, memory/disk copies, corruption, write failure, short I/O, cleanup and
an actual over-limit input. Thirty fingerprint controls passed with two
POSIX-only skips; canonical vendors, touched Python lint and thirteen install
contracts passed. One earlier full attempt missed the owner-death fixture's
unchanged ten-second writer-start oracle; added failure diagnostics, its
two-case rerun and the full 79-case rerun all passed without changing that
bound or the guardian implementation. The separate over-limit case then passed.
Native repair and both complete development modes still require qualification
on the buffered implementation. All production timeouts remain unchanged.

Buffering alone did not clear the native watchdog: at an observed context-child
age of 451 seconds, control construction had consumed approximately 390 seconds
and the candidate owner had run for only 53 seconds. Same-source balanced
buffer/baseline/baseline/buffer freeze profiles measured 119.91, 67.98, 56.55 and
42.33 seconds with identical resulting hashes. Reads dominated; directory
creation stayed below 0.7 seconds. The cold/warm variation does not establish
a native latency-budget pass.

The lifecycle now carries its already-frozen source hash as an expected pin
through both control and candidate construction, avoiding duplicate initial
scans of the same admitted snapshot. Retained source and per-file snapshot
validation still precede construction. Both immediate reuse and reuse after
waiting independently hash the actual source; malformed pins and changed
transitive dependencies refuse without writers or completed-slot changes.
The existing internal CLI carries the optional pin and still accepts callers
without it. All 89 engine/mode controls passed in 223.94 seconds, including
explicit read-count, reuse and stale-pin sensitivity. Touched Python lint,
canonical synchronization and all thirteen install contracts passed. Native
qualification remains open, with no production timeout change.

That native attempt completed both source-changing development builds and
their import/provenance checks, but exact reuse still hit the 480-second
context-child watchdog. Helper publication was additionally recomputing the
entire admitted graph to create its plan baseline. The mode owner now passes
its validated frozen-source pin into that plan instead. Native inspection and
completion still validate actual content, and the parent's before/after
publication validations remain unchanged.

Seventeen focused checks passed in 99.36 seconds. They include both Windows
PowerShell publishers, matching-pin success, wrong-pin and changed-input
refusal, malformed-pin rejection and mode forwarding; the success control
counts both parent content validations while refusing a redundant producer
scan. Another 53 regression controls passed in 375.13 seconds, preserving
unpinned native callbacks, full source-identity refusal and the remaining mode
contract. The complete native development flow remains unqualified until its
unchanged-limit rerun passes.

The next full attempt passed the first build but returned 124 during the
second build (1452.29 seconds total). Named phase logs and process ages showed
the second context child at 471 seconds: control construction had consumed
approximately 331 seconds, and the candidate owner had run for 140 seconds
with its UV child still active. This is construction cost, not a demonstrated
handoff or publication deadlock. Further whole-flow retries without a new
diagnosis are not the next step. Investigate eliminating duplicated control
bootstrap while preserving namespace-before-cutover-before-candidate-construction order,
installed owner execution and descendant settlement. The existing cutover
guard is a kernel-backed `SingleInstance`; it has no borrowed-owner or atomic
handover API. Do not release it early or invent an unchecked parent ticket.
Any required custody changes must preserve the existing guardian owner's scope.

No PR, release or live rollout is claimed. Full dependency/build assembly
identity, the other adopters, bidirectional service takeover and separately
authorized installed acceptance remain open with their existing owners.

### 2026-10-22 - Direct retained acceptance from #2871

- Materialized the complete #2871 receiving portion directly in this ROOT Plan and
  Validation, with continuing coordination. All incumbent obligations and custody
  remain; historical private identifiers, where present, are represented by neutral
  historical placeholders without changing their role or outcome. A new normal reviewed
  merge and full current readback must precede administrative source supersession. No
  behavior, test, native result, or rollout is claimed.

### 2026-10-22 - Shared lifecycle implementation resumed

The implementation draft now uses `dev-<source-sha256>` with ordinary immutable
admission and claim-checked completion/selection. Worktrees publishes the actual
candidate identity instead of `dev`; exact reuse no longer forces a monitor
transition. Release retains the original target through settlement, including
an immutable development target, and keeps ownership after a failed restore.
Canonical vendors and the complete affected-consumer changefile are present.

Focused contained validation passed 70 engine/mode/deployed-CLI controls,
including real non-editable UV installs and competing development builders.
The final restore-target extension separately passed all 18 non-native mode
controls. Full-graph source freezing passed its unchanged 180-second item
budget in an isolated workspace-local temporary root (44.84 seconds).
Install-contract, vendor synchronization, focused Python lint and documentation
consistency checks passed. These are not yet merged or installed results.

Initial native repair qualification missed the existing 240-second command
deadline in both default OS temporary storage and workspace-local storage.
The operator subsequently authorized aligning this test's repair-command
budget with the existing 480-second development-command budget; production
timeouts and the 650-second repair test-item budget remain unchanged.
Owned-process observation at about 158 seconds still showed control-runtime
bootstrap and its retained UV build, before mode ownership; no dead-owner or
handoff defect is established. A separate freeze profile measured 151.78
seconds in OS temporary storage, including 78.52 seconds reading the fresh
frozen files. Continue diagnosing actual construction stages and qualify the
development entrypoints before publication; no production service was changed.

Follow-up diagnosis excluded a validation-only path-length failure: a deeply
nested temporary override exceeded Windows' subprocess working-directory limit;
the standard temporary layout did not. With standard storage, the legacy
development entrypoint constructed and selected its source-hashed generation
and reported settled readiness, but its frontend still missed the unchanged
480-second deadline. The short, private source-volume retry passed the full
legacy native case in 1246.59 seconds under unchanged deadlines: PowerShell
and Git Bash iterations selected distinct source-hashed generations, exact
reuse preserved both, and deployed release restored the original target after
the source worktree was removed. The namespaced native case then exposed the
same subprocess working-directory limit under its real installation root,
independent of the earlier validation-only override. The canonical UV launcher
now delegates deep directory selection to UV from a short native cwd, preserving
CLI/environment directory selection and resolver configuration. Sixteen focused
probe controls passed, followed by all 263 dependency-policy and UV-executable
controls, including four real Windows long-path/configuration variants and
repeated-directory refusal. The first full run exposed a missing setuptools
test prerequisite; it was restored through the existing seven-day dependency
policy before the complete passing rerun. Native repair and namespaced reruns
remain pending.

Pull-forward preserved the newly retained #2871 acceptance and #8092's routed
monitor-owner/publication-interpreter changes. The newly adopted Vault engine
copy and all canonical helpers were resynchronized; the install contract and
touched-file lint passed. On this rebased source, the unchanged 180-second
full-graph item timed out resolving frozen-file parent paths. A per-call parent
resolution cache passed 28 digest/root/symlink/error controls, with two
POSIX-only FIFO controls skipped on Windows, including the new
cache-lifetime/refusal control. The other 63 engine/mode controls passed
separately from the unresolved full-graph timing item.

The full-graph item still missed 180 seconds. A Python 3.14 profile measured
173.49 seconds for the actual snapshot of 1,519 files across 86 parents,
including 157.68 seconds opening files and three complete fingerprints; the
test then performs a fourth independent fingerprint. This is not a passing
full-graph test result. The operator separately authorized a 300-second budget
for this test item before its final rerun; hash frames, source coverage,
independent readback and production timeouts remain unchanged.

The approved full-graph rerun passed in 68.64 seconds, including independent
readback. Repair still missed the approved 480-second command budget. Process
observation showed its owned repair target executing UV at about 392 seconds;
that observation did not establish a stalled handoff or identify the final
blocked stage. The namespaced development run advanced beyond the fixed UV
cwd failure into owned construction, then returned 124 at the existing context
child boundary. No production deadline was changed.

The next bounded optimization computes aggregate and per-file source digests
from the same read, rather than reopening every original file for the second
proof. Both admission comparisons remain, including source mutation, corrupted
snapshot and missing-proof refusals. All 68 non-native engine/mode controls
(including full-graph readback) and 29 fingerprint controls passed; two
POSIX-only FIFO controls remain skipped on Windows. Native qualification is
still pending. Reusing the completed target does not remove the existing
temporary control bootstrap, and no persistent evidence cache or parallel
constructor was introduced.

The final documentation audit removed Worktrees' remaining claim that each
invocation rewrites `versions/dev`. Its finalization warning now names the
claim's explicit root and quoted owner for supported release, rather than
advising manual claim deletion before activation. Two focused warning controls
passed for numbered and source-hashed restore targets without modifying either
claim. This remains a warning-only legacy-home scan, not a new release action or
complete namespaced-claim discovery.

Repair also missed the original 240 seconds in short source-volume storage. A separate
isolated control-bootstrap profile measured 240.28 seconds **before any repair
target construction**: 76.12 seconds freezing inputs, 68.47 seconds retaining
the UV transaction, 72.87 seconds executing UV, plus interpreter construction,
installed preparation and health checks. This identifies construction overhead,
not a demonstrated handoff deadlock. Native qualification remains incomplete;
neither successful unit controls nor an already-published development selector
waives the explicitly approved command-settlement budget.

The Slot-admission coordinator resumed Phase 1 under the existing
[#5472 claim](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6358223564).
Reconciled the delivered #6707/#7234 construction, source and native publication
milestones without treating either as complete adopter acceptance. The next
bounded slice removes the shared engine's always-refresh development exception
and Worktrees' fixed `versions/dev` selection: source-hashed generations use
ordinary immutable admission, installed health, construction/retention custody
and explicit claim ownership. Iterations retain their original restore target.

The retained UV transaction already supplies non-editable source copies; it
does not yet expose a complete selected dependency/build assembly identity.
That Phase 1 gate remains open, as do bidirectional service takeover, guardian
settlement, the other adopters and authorized installed rollout. Preserve the
#6424 and #6389 owners; this slice adds no parallel lifecycle or live service
termination. Validation and publication evidence will follow implementation.

### 2026-10-22 - Best-effort rule landed; finite amendment complete

#8090 merged as `4b78ba2d5dd2f49d695fe312af5f8d34d3c0385d` after current-head
approval and successful required CI. The final diff is twelve Markdown files;
the library and all consuming plugin payloads remain unchanged. The amendment's
schema, documentation consistency, link/anchor, changefile-scope and literal
Request checks passed. Runtime unit/E2E, clean-room and live activation were not
exercised by this documentation task.

Published the actual merged disposition on
[#5472](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6357855668)
and [#7028](https://github.com/ThomasMichon/copilot-extensions/issues/7028#issuecomment-6357856879).
Phase 0a is complete, not Phases 1-3. Existing implementation owners retain the
new unchecked forced-takeover, crash/retry, native and installed-adoption gates.
The next implementation slice reconciles delivered evidence against those
gates; it must not restore unconditional handoff compatibility as a veto.

### 2026-10-22 - Best-effort handoff and cold takeover amendment

Captured the follow-on verbatim and re-read it against the literal operator
input, including prompt owned-scope shutdown, newcomer force termination,
network-like client interruption and power-loss-like child recovery. Replaced
the unconditional successor-readiness-before-retirement premise with a
preferred graceful path plus bounded cold takeover. Existing lifecycle owners
retain identity, serialization and state-recovery duties; no process is
terminated or runtime changed by this finite documentation amendment.

### 2026-10-22 - Reviewed intent landed; finite reconciliation complete

#8065 merged as `738b370a14cda49aefdf82ac785535fc11e87718` after approval and
required CI. Effort/vision structure, documentation consistency, changed local
links, all 18 explicit archival transfers and literal Request capture passed.
No executable behavior changed, so runtime unit/E2E, clean-room provisioning
and live rollout were not exercised by this documentation slice.

Published the final disposition to
[#5472](https://github.com/ThomasMichon/copilot-extensions/issues/5472#issuecomment-6356383975)
and [#7028](https://github.com/ThomasMichon/copilot-extensions/issues/7028#issuecomment-6356384651).
The intent reconciliation coordinator's finite slice is complete. This effort
stays Active: the Slot-admission coordinator and separately assigned adopter
owners retain Phases 1-3 and their unchecked validation; Source-Managed Runtime
Installs retains #7028/#7029's deployment acceptance. Select the next existing
implementation slice against delivered evidence, not another mutable framework.

### 2026-10-22 - Immutable development direction

Captured the operator's direction and compared the Request back to the literal
input. Retained #5472 and #7028/#7029 as the existing coordination homes.
Abandoned the old mutable premise and transferred every unresolved checkbox,
without reverting delivered primitives or declaring the adopter portfolio done.
The bounded open-work inventory identified no additional mutable-only open PR
or issue to close; #3376 is already merged history, not an open closure target.
This finite documentation slice does not change a runtime or terminate a worker.
