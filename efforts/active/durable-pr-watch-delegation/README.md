# Durable PR-Watch Delegation

- **Slug:** `durable-pr-watch-delegation`
- **Repo:** copilot-extensions
- **Branch(es):** Separate proposal, notification-contract, and integration PRs against `dev`
- **Created:** 2026-10-09
- **Status:** Active
- **Vision:** `visions/plugins/agent-dispatch/README.md` / `hibernate-the-wait`; `visions/plugins/agent-worktrees/pull-requests/README.md` / `durable-shared-pr-transition-subscriptions`; `visions/plugins/agent-pull-requests/README.md`; `visions/machine-fleet/README.md` / `single-operator-fleet`
- **Umbrella issue:** #6007
- **Sub-issues:** #6286 (PR-owner installation-cell adoption); #7188 (first-class PR service and fleet deployment)

## Guiding Intent

Resume the deferred PR-watch delegation work without trading away wake
reliability. Eligible suspended dispatch tasks should share the existing PR
watch owner's observation stream rather than retain one operating-system
wait process per task. Build out `agent-pull-requests` as the first-class PR
plugin and independently operable service, including a centralized watcher and
subscription registrar deployable in containers through fleet-manager on
explicitly assigned fleet machines. A fleet belongs to exactly one operator;
it never hosts another operator's state or credentials. Ordinary process
waiters and supported native PR operation remain working alternatives.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Delegation driver | Proposal, implementation, validation, PR stewardship, deployment | This effort's source worktree and linked issue |
| PR-service plan steward | First-class plugin/service and fleet-consumer scope amendment, review and integration | Separate managed planning worktree; #7188 |

## Coordination

- **Topology:** Serial, independently reviewable PRs against `dev`.
- **Host (owns PRs):** Delegation driver.
- **Delegates:** Any bounded implementation delegate receives an exclusive slice; the driver owns integration and completion.
- **Fleet expansion:** The PR-service plan steward owns the
  `Fleet-hosted PR service expansion` slice. #5789, #6030/#5861,
  #6236/#6240/#6241 and #7051 retain routing, deployment, integrated lifecycle
  and PR-conformance ownership. This effort consumes their contracts rather
  than taking over those campaigns.
- **Review sequencing:** Land the fleet vision/plan amendment before its runtime
  implementation. Keep this proposal separate from the existing installer and
  Windows receipt-read investigation; neither scope expansion nor a passing
  rerun waives that prerequisite.
- **Handoff:** Continue from this README, its next unchecked item, and the current PR state. Do not replay the preserved branch wholesale.

## Context

The delegation implementation was split out of #5701 after review identified
an architectural reliability gap and rolling-upgrade/credential defects.
The local `pr-watch-daemon-delegation-wip` branch is reference material, not a
ready patch. The steer-idempotency fix from that PR has already landed and
must not be reintroduced as new work.

The existing watch owner persists pending subscriptions, but
`WatchRegistry.apply_snapshot()` removes a fired subscriber before
`WatchDaemon` invokes its callback. `default_notify()` does not check the
callback's exit status. A callback failure can therefore permanently strand
a delegated suspended task after the subscription has disappeared.

The current process waiter already prepares suspension transactionally and
uses generation-fenced arm/finish operations. Delegation must compose with
those contracts rather than invent a second task lifecycle or a sentinel
machine identity.

The [fleet service plan](fleet-pr-service.md) extends this campaign beyond
dispatch's optional wait optimization. The centralized registrar owns PR
subscriptions, not a second dispatch task registry or fleet enrollment store.
Services supply independently usable artifacts and consume fleet-owned role
placement, routing, authentication and lifecycle.

This work closes the named PR, dispatch and fleet vision items. It follows the patterns'
independent-installation, optional-composition, attributable-command,
cross-platform, and graceful-cutover invariants. It extends and packages the
existing PR-watch service; it does not introduce a parallel watch owner,
fleet controller or credential issuer.

## Request

Operator continuation, verbatim:

> Okay. Resume driving

The driver identified the deferred PR-watch delegation work as the resumed
subject and proposed preserving process fallback while fixing delivery
reliability rather than accepting a regression. The operator confirmed the
effort slug:

> durable-pr-watch-delegation

The detailed protocol and validation work below are **agent-recommended
implementation requirements**, not additional operator quotations.

Follow-up scope selection, verbatim:

> Adopt installation cells for agent-pull-requests before finishing delegation

The real installation-cell integration test found that the PR owner had not
adopted the shared installation-context payload contract. The operator selected
adoption rather than a legacy-runtime-only optimization.

Service-lifecycle scope selection, verbatim:

> governed-cutover

This selects the offered option to add and review canonical graceful cutover,
then continue adoption. It does not waive the installation-cell lifecycle
contract or authorize a stop/start exemption.

Lease-only ownership boundary selection, verbatim:

> refuse-unprovable-owner

An owner without verifiable same-cell ownership must remain untouched. This
replaces first-upgrade success for that legacy owner with explicit refusal;
it does not waive governed upgrades or state preservation.

Fleet-service scope expansion, verbatim:

> Got it, nice. So this effort should also roll in building out `agent-pull-requests`, the first-class PR-focused plugin and associated service daemon. We'll need to expand the scope a little bit: we want services to become compatible with the fleet-manager, deployable into containers on the assigned fleet machines. A centralized PR-watcher and registrar makes a ton of since, as we can have one centralized watcher that's mostly IO-based.

The operator selected `single-operator` when asked about service tenancy, then
clarified the standing fleet boundary verbatim:

> The intent of the fleet is that it all belongs to one single operator. If we ever introduce multiplayer, it will be by having one operator's fleet talk to another operator's fleet, never holding another operator's state or credentials in the same fleet.

This is an invariant of the entire fleet, not merely an initial service
default. Future collaboration means federation between separately owned fleets,
not shared tenancy. Federation implementation and concrete live deployment are
not authorized by this plan amendment. The implementation sequencing and
validation details below and in the linked plan are **agent-recommended**.

## Plan

### Governed watch-owner cutover prerequisite

_Agent-recommended design and validation, explicitly selected for review._

- [x] Review and merge the [watch-owner lifecycle plan](watch-owner-cutover.md) before implementing the service transaction (confirmed by #6291).
- [x] Land the canonical pre-adoption drain, full-transaction borrowed authority and custody-preserving rollback prerequisite before the owner adapter (confirmed by #6396).
- [x] Land inactive watch-state construction, final read-ready reload and bounded complete quiescence as internal groundwork, without enabling a production service or installation-cell adapter (confirmed by #6714).
- [x] Land non-signaling native process-exit references captured before graceful control, without granting installation or retirement authority (confirmed by #6720).
- [ ] Adopt canonical graceful-cutover primitives with installer-driven activation, passive health, exact-instance promotion and retirement, and crash-consistent marker/manifest/route recovery.
- [ ] Define and enforce one installation-scoped watch-state writer across predecessor drain and successor subscription/delivery adoption; preserve event and registration identities without overlapping live-generation delivery.
- [ ] Prove rollback, commit-forward, governance refusal, crash recovery, callback drain, and refusal of lease-only owners without verifiable same-cell ownership on Windows and Linux before landing the cutover prerequisite.
- [ ] Land and deploy the governed lifecycle prerequisite as part of PR-owner adoption before enabling installation-cell dispatch delegation.

### Installation-cell prerequisite

_Agent-recommended prerequisite, explicitly selected by the operator._

- [x] Review and merge the installation-cell plan amendment before adoption implementation (confirmed by #6146; the lifecycle amendment above remains a separate review gate).
- [ ] Adopt the canonical installation-context payload, installer/runtime-resolution and state-root contract in `agent-pull-requests`, preserving supported legacy installs without silent cell-to-legacy fallback.
- [ ] Include the PR owner in the generated same-cell peer-launch contract and declare release impact for every generated consumer.
- [ ] Prove first installation, upgrade, invalid/foreign receipt refusal, state isolation, actual two-cell PR-owner invocation, and pending watch-subscription recovery using the shipped payload rather than a fabricated sibling contract.
- [ ] Land and deploy the adoption prerequisite before enabling installation-cell dispatch delegation. Keep the saved dispatch integration resumable while this prerequisite clears.

### Phase 1 - Review the recovery contract

- [x] Land this proposal through automated review before implementation.
- [x] Reconcile the preserved implementation with current source and enumerate only the delegation changes still needed.

### Phase 2 - Reliable watch notification delivery

_Agent-recommended implementation requirements._

- [x] Introduce an opt-in, capability-advertised acknowledged notification contract owned by `agent-pull-requests`; legacy subscriptions retain their existing wire behavior.
- [x] Persist the fired event before attempting delivery. Retain a stable event identity and replay the same payload until a successful callback acknowledgement, explicit cancellation, or a visibly recorded terminal recovery outcome.
- [x] Keep callback execution bounded; retry failures with bounded backoff without blocking unrelated subscriptions or holding persistence locks during subprocess execution.
- [x] Recover pending deliveries across daemon restart and supported cutover. Guard acknowledgement against unregister/re-register races so an old delivery cannot remove a newer registration.
- [x] Validate notification specifications and timeout inputs explicitly. Report delivery failures and pending retry state without logging credentials.
- [x] Land and verify the notification-contract PR before enabling dispatch delegation.

### Fleet-hosted PR service expansion

_Operator-required scope; agent-recommended implementation and validation._

- [ ] Review and land the first-class PR plugin/service vision, single-operator
  fleet invariant and [fleet service plan](fleet-pr-service.md) before building
  its new runtime paths.
- [ ] Build out the existing PR plugin as the PR-domain client/service surface;
  preserve supported worktree publication policy and provider behavior through
  shared contracts rather than another forge implementation.
- [ ] Supply an independently installable PR service and container role for
  fleet-manager's declared placement and lifecycle, without requiring a Copilot
  session, host-side plugin supervisor or parallel fleet controller.
- [ ] Implement centralized, authorization-scoped observation and durable
  registration with transport-appropriate replay/ACK delivery; do not execute a
  remote subscriber's local callback argv in the service container.
- [ ] Integrate scoped, renewable forge credentials and typed client/dispatch
  access through the fleet-owned Gateway contracts, without hosting any other
  operator's state or credentials.
- [ ] Prove standalone/container lifecycle, restart and upgrade continuity,
  account/fleet isolation, observation sharing, disconnection recovery and the
  real client/Gateway/service path before treating fleet deployment as complete.

### Phase 3 - Fenced dispatch delegation _(agent-recommended)_

- [ ] Recognize only supported PR-watch invocations and discover the optional watch owner through its attributable installation boundary, never ambient `PATH`.
- [ ] Negotiate support from both owners before suspension. Missing, legacy, or incompatible capabilities select the existing process waiter without sending new fields to strict legacy schemas.
- [ ] Record an explicit validated waiter kind and reuse the coordinator's prepare/arm/finish generation fences. Prepare before subscribing; make callback completion retryable and idempotent for that exact generation.
- [ ] Preserve explicit `--token` and control-token behavior without persisting secrets in subscriptions or callback argv. Use process fallback when the daemon cannot reconstruct the caller's credential context safely.
- [ ] On ambiguous prepare/register failures, reconcile or abort the exact prepared generation before considering process fallback. Never arm two independent wait owners for one task.
- [ ] Validate a finite positive PR-watch timeout, preserve timeout wake behavior, and provide visible recovery for delegated waits whose subscription or delivery owner is unavailable.
- [ ] Keep arbitrary-command detached waits, ordinary non-detached waits, and independent installation unchanged; do not add an inert manifest `dependencies` field.
- [ ] Update the owning plugin documentation, CLI help, and changefiles; land the integration PR after the required review and checks.

### Phase 4 - Deploy and close _(agent-recommended)_

- [ ] Confirm promotion of the changed source to the release branch by content, then deploy through the unified update surface.
- [ ] Exercise the deployed contract in isolated state without interrupting shared coordinators, watch subscriptions, or peer sessions.
- [ ] Complete the fleet service acceptance in the linked plan on explicitly
  approved capacity, including persistent registration/delivery and the actual
  Gateway path. Local delegation alone does not complete the expanded effort.
- [ ] Synchronize consumer guidance when changed payload provenance requires it, through the consumer's own PR flow.
- [ ] Record outcomes, resolve every validation item or transfer it to a named tracked objective, archive this effort, release its binding, and finalize owned worktrees.

## Validation Plan

All items below are **agent-recommended** acceptance checks.

- [ ] Unit: failing/nonzero/timeout callback stays pending; acknowledgement removes only its own registration; replay preserves event identity and payload; cancellation and re-registration are generation-safe.
- [ ] Unit: durable event survives crash before delivery, crash after remote commit before acknowledgement, restart, and supported daemon cutover; unrelated subscribers continue to receive notifications.
- [ ] Unit: already-terminal PR, finite timeout, polling outage, expired wait, and malformed inputs produce the intended explicit results.
- [ ] Compatibility: old/new client, coordinator, and watch-owner combinations retain legacy schemas and a functioning process fallback; missing optional plugin affects only the optimization.
- [ ] Credentials: explicit CLI credentials and command-backed configuration still authenticate; no credential value is persisted in callback argv, subscription state, result diagnostics, or logs.
- [ ] Integration: real isolated watch owner and coordinator exercise subscribe/prepare/arm/finish, coordinator outage then recovery, duplicate delivery, stale-generation callback, and registration failure without a duplicate wait owner.
- [ ] Fresh state: the supported first-touch path establishes only the owning plugin's state; two independent subscribers share one PR observation stream and need no per-task waiter process.
- [ ] Platform: bounded callback launch and shutdown/restart behavior pass on Windows and Linux, using existing contained test tooling.
- [ ] Source gates: touched-plugin tests, fatal Python lint, module-size, headless-launch, install-contract, and required CI pass; add a changefile for each runtime plugin changed.
- [ ] Live deployment: exercise an existing, authorized PR target with isolated subscriber/coordinator state when available; otherwise record the exact unavailable lane and a named follow-up before closure.
- [ ] Documentation: review the final diff's impact on plugin behavior, failure/retry semantics, fallback, configuration, CLI help, and source-of-truth docs.
- [ ] Fleet service: resolve every acceptance item in
  [the fleet plan](fleet-pr-service.md#validation-plan-agent-recommended),
  including the single-operator ownership boundary, without substituting mocks
  or a healthy container for the requested deployed path.

## Proposal

Reliable delivery is a prerequisite, not an optional follow-up. The watch
owner retains a durable event until the callback acknowledges it. Dispatch
callbacks must tolerate replay because a remote commit can succeed even if
the local acknowledgement is lost. Delivery is therefore at-least-once;
task-generation fencing and idempotent completion prevent duplicate wake
effects. This does not claim exactly-once subprocess execution.

The optimization is negotiated and optional. A caller whose credentials
cannot safely be recovered by the watch owner keeps the process waiter,
which already forwards those credentials through its process environment.
This preserves functionality without making a secret-bearing durable
callback an implicit credential store.

## Journal

### 2026-10-18 - First-class PR service and single-operator fleet scope

- The operator expands this same effort to build out the PR-focused plugin and
  service, with a centralized IO-oriented watcher and registrar deployable into
  containers on assigned fleet machines. #7188 owns the service-consumer slice;
  existing routing, fleet, lifecycle and PR-conformance owners retain their work.
- The operator clarifies that one operator owns the entire fleet, permanently
  excluding other operators' state and credentials from that fleet. Future
  multiplayer would connect separate fleets, not add tenants to this one.
- This amendment proposes implementation and validation before runtime changes.
  The existing installation-cell prerequisite, unresolved Windows stable-read
  observation, saved dispatch integration, release and authorized deployment
  gates remain open. A plan merge will not complete the parent effort.

### 2026-10-14 - Watch-state core merged; exact exit proof continues

- #6714 merged into `dev` at `03c6fadd1507d5eca4297bf17add3c4839451ddc` after exact-head approval `5519958898` with zero open findings and successful current-head CI `38583871208`. The internal state-core milestone is complete; no production installation-cell or service adapter has been enabled.
- The next owner boundary needs a native process reference captured before graceful control, then bounded exit confirmation through that same reference. Existing diagnostic termination opens and signals its own handle; it cannot confirm an already-requested graceful shutdown without a late PID lookup. Add the non-signaling shared reference prerequisite on Windows and Linux, refusing unsupported native identity proof, before wiring exact owner retirement. Receipt, governance, writer transfer, transaction recovery, adoption, dispatch and deployment remain open.
- Implemented that shared reference without any service opt-in or signal capability. Native Windows tests retain the same query/synchronization handle across graceful test-child exit and reject a mismatched birth token without affecting the child. Linux uses canonical pidfd capture; its real graceful/high-descriptor cases remain current-head native CI gates. Unsupported platforms explicitly refuse. Reference-lock contention consumes the caller's finite wait budget; a controlled zero-budget case reproduced an overrun before correction.
- The coupled shared POSIX wait now uses native polling rather than `select`'s descriptor limit and refuses native error flags as exit evidence. Four controlled cases failed before correction; the full contained Windows canonical suite passes 593 tests with 27 platform/stress skips. The actual materialized vendor plus new API/exit cases passes 154 with ten platform skips. Fatal lint, vendor parity, module-size, headless-launch and install-contract gates pass. Canonical generation is limited to ZDD, and this PR declares release impact for all ten generated consumers; no installed runtime or shared live owner was changed.
- Further validation found an inherited reference could touch an orphaned parent-thread lock after fork. Wait, close and context entry now check the creating process before any lock or native callback. Three controlled cases failed before correction; the new real Linux fork case must pass in current-head CI. The revised complete Windows canonical suite passes 596 tests with 28 platform/stress skips, and the actual vendor plus new exit cases passes 157 with eleven skips. No process-affinity failure closes or signals the parent's reference. Current-head review and native Linux CI still gate the shared reference; the owner adapter and parent completion remain open.

### 2026-10-14 - Inactive watch-state and bounded quiescence groundwork

- Current-head review found a real shutdown/close race could set the terminal events between activation's early check and worker-stop clearing. The terminal recheck and clear now share shutdown's admission synchronization; close publishes both stop events through that same condition before waiting for lifecycle serialization. Its budget is validated before state mutation. Two real-scheduler event-controlled races and six invalid-close cases failed before correction. The complete contained Windows suite now passes 218 tests with two existing shell-availability skips; fresh review and native CI remain gates.
- Implemented the internal state-core boundary under the reviewed lifecycle design, not an installer or service opt-in. Passive construction reads no subscriptions and starts no effects. Final state reload and effect authorization are separate operations. Mutation admission/counters drain accepted registration/cancellation calls while the authenticated lifecycle listener and foreground wait remain available.
- Budget-bound worker quiescence retains live callbacks and queued legacy batches on timeout, waits for ACK persistence, and requires the final state-write acknowledgement before reporting quiescence. Same-owner abort resumes retained objects instead of replacing a registry with accepted work. Strict read-ready adoption rejects malformed legacy/acknowledged state; failed or late reads cannot activate a stale cached registry. Partial or late startup leaves admission closed and resources attributable for cleanup/retry.
- Thirty-six focused cases exercise passive no-effects startup, final-snapshot selection, real authenticated control RPC, handler/callback/ACK drain, lossless incomplete-drain resume, deadline refusal, serialized lifecycle contention and partial-start retry. The initial twelve cases failed against the missing API; two additional late-I/O regressions reproduced false completion before deadline checks were added. A logical-clock teardown defect was corrected by restoring the real clock before fixture cleanup rather than weakening production joining. The complete contained Windows suite passes 210 tests with two existing shell-availability skips on Python 3.14.7; fatal lint, module size, headless-launch and install-contract gates pass.
- The owner still owes exact installation/instance authorization, native writer-lease transfer, receipt/governance checks, the durable activation/selection journal, recovery, installer/cell wiring and actual shipped-payload validation. Those remain open. The saved dispatch implementation is unchanged.

### 2026-10-14 - Shared prerequisite merged; owner adoption continues

- #6396 merged into `dev` at `c29eca6f51f326752a70c55ed9fdd275b1e25316` after exact-head clean owner-authored Copilot review `5517868277`, all required checks and resolved review threads. CI run `38562889167` passed. Its native Ubuntu canonical ZDD lane exercised `python -m pytest -q libs/zdd/tests` with the canonical source path and smoke containment: 581 passed, four stress-lane skips, including the native fork lease-retention case. Windows source validation passed 538 tests with 25 platform skips; the actual materialized vendor passed 113 with four skips.
- Pulled the source worktree onto the merged contribution branch. The initial multi-commit replay conflicted and was safely aborted; a retained local backup protected the exact published tip. Reconciled the source PR's actual merged state and completed the supported managed sync. No unmerged work was discarded, no remote history was rewritten, and the saved dispatch worktree remains unchanged.
- This closes only the shared-library prerequisite. No service opts into it yet. The next work is the reviewed watch-owner adapter and installation transaction: passive no-effects startup, bounded complete writer drain, exact lease transfer/final reload, owner-led crash reconciliation and installer/cell wiring. Adoption, release/deployment, dispatch integration, live evidence and parent close-out remain unchecked.

### 2026-10-14 - Released repair and shared prerequisite publication

- Review identified the generic diagnostic-apply consumer still inferred native authority from a non-null handle instead of entering the public borrow fence. Closed, nested, foreign-thread and inherited-process guards now refuse before repair and are not released by the rejected borrower. Successfully acquired callback guards remain borrowed through repair, leave that context before release, and retain cleanup on exceptions; a distinct consumer guard composes with native installation authority. Eight regressions failed before correction, including four observed unauthorized mutation calls. The complete canonical Windows suite passes 538 tests with 25 platform skips. Other canonical guard consumers were checked for the same raw-handle bypass. Current-head review, native CI and parent adoption remain open.
- The next review found route ownership was revalidated only before the passive-start interval. The drain invocation witness now first rechecks the entire captured routing table after resolving the client and method; a changed route cleans up only this attempt's passive, leaving predecessor admission and replacement routing untouched. Eight port/spawn/health/client race cases failed before correction and now pass for confirmed and unconfirmed passive exit. Native publication-witness initialization precedes the captured selection, with a separate legacy-table case preventing a false refusal on newly added history. The canonical Windows suite passes 530 tests with 25 platform skips. Fresh review and native CI remain gates; adoption and parent completion remain open.
- Review then identified coercible but invalid endpoint fields in its previously-missed Medium findings, despite reporting zero open inline findings. Mutation now reuses the canonical publication-custody validator's exact types and bounds instead of tolerant endpoint coercion, with explicit optional-version validation. Forty-six active/previous field regressions reproduced the gap; twelve real-write boundary cases preserve valid extrema, optional fields and null PIDs. The canonical Windows suite passes 521 tests with 25 platform skips. A zero-open-inline label is not treated as a passing review while substantive Medium findings remain; fresh review and native CI remain gates.
- The subsequent review found malformed endpoint rows could be treated as absence. Stateful selection now validates both rows before spawn/drain and skips the legacy watchdog that could heal a malformed active value before validation. Shared routing mutation validates persisted and proposed rows, including a malformed row introduced during drain; read-only fallback remains tolerant and null/missing rows remain supported absence. The initial matrix reproduced ten success-shaped overwrites and two insufficient diagnostics. Canonical Windows validation passes 463 tests with 25 platform skips. Atomic-replacement fixtures now use real endpoint records while retaining native retry, durability and custody-history assertions. Current-head review and native CI still gate this prerequisite; parent adoption is not complete.
- The next review exposed a narrower pre-drain boundary: constructing the control client or resolving its drain method could fail after the invocation witness was set. The witness now runs only after both operations succeed and immediately before invoking drain; an incomplete or raised drain still retains reconciliation custody. Four cases reproduced the unintended predecessor route mutation before correction. The expanded canonical Windows suite passes 427 tests with 25 platform skips. Current-head review, native CI, adoption and parent close-out remain open.
- Current-head review found two additional defects (each repeated for the physical vendor): release did not enforce acquiring execution identity, and pre-drain failure could restore or undrain an untouched predecessor. Release now checks PID before the inherited local lock and rechecks the exact acquiring thread/owner under that lock. Rollback and refusal use an explicit drain-invoked witness; pre-invocation failures only terminate and confirm the passive. Seven regressions reproduced the defects before correction. Full Windows canonical suites pass 417 tests with 25 platform skips on Python 3.11 and 3.14; the real vendor passes 113 with four skips. Contention fixtures retain real native exclusion while acquiring and releasing on the same owner thread. The new real-fork lease-retention case requires the native Linux CI lane; current-head review and CI remain gates.
- The normal authorized update deployed the released Worktrees repair surface. The operator-selected single-record dry-run passed; an active publication initially held the native authority guard, so the repair waited for that publisher to exit rather than overriding it. The supported targeted repair then succeeded with its immutable backup. Independent read-only comparison confirms the exact agent text and every surrounding field were preserved; a subsequent public integrity check reports the record valid.
- Published the reconciled shared-prerequisite head through the supported exact saved-lease rewrite. Fresh current-head review and required CI remain merge gates. This unblocks publication only: watch-owner adoption, installation transactions, dispatch integration, service-health evidence and parent close-out remain open.

### 2026-10-13 - Shared foundation integration

- Closed the previously recorded Python 3.14 Windows fixture failure rather than carrying it as an untracked exception: the singleton-manager exec test now supplies a host-native absolute temporary executable and asserts the exact forwarded argv. Production absolute-path admission is unchanged. The full canonical suite passes on the existing Python 3.14.7 environment (406 tests, 24 platform skips), and the focused regression also passes on Python 3.11.
- The later current-head Lite review found a genuine cold-start rollback defect: after publication, `old is None` caused the successor to be terminated despite being the only possible serving generation. The opt-in rollback now retains a published successor for guarded commit-forward or unresolved recovery; pre-publication failures still confirm termination. Three real-routing regressions reproduce the original failure and pass after correction. Canonical Windows validation passes 406 tests with 24 platform skips. The annotation finding is already covered by postponed module annotations; lock-path validation now uses the existing helper without constructing a new lock.
- The containing release run `38479483177` passed all nine full plugin suites and first-party artifact build/attestation, then refused promotion because Bridge's coverage collector failed to import a sibling test fixture and produced no enrolled baseline. The existing #6535 repair owner already reserved this exact import defect; no duplicate patch or release bypass was opened. Publication remains blocked until the released public record-repair command is available.
- The operator selected a narrowly scoped Worktree Manager repair after a malformed legacy tracking record blocked the intentional source-owned rewrite. #6555 merged at `3d5a310ce151bef718e3de376af4fd4ca5ff9349` after current-head approval with zero findings, successful required CI, and 671 passing native Linux guard tests. The repair safely serializes agent text and provides explicitly bounded, locked, backed-up record repair; it does not waive publication ownership.
- Release-content proof still refuses the repair: `main` does not yet contain its module. The promotion backlog's six incomplete launch fixtures have already been fixed by #6578; no competing fix was opened. Await successful promotion and the released public repair command before changing the named host record or publishing #6396. Parent adoption, dispatch, deployment and close-out remain open.
- Rebasing #6396 preserves #6164's conditional capture, receiver-bound drain, publication witnesses, process birth tokens, native exit confirmation and uncertain-publication fences. Conditional cutover publishes before drain; the watch-owner seam drains before publication. Combining those opt-ins refuses before mutation rather than silently mixing their contracts.
- Owned and caller-held guards now enter the same preparation and recovery checks. Added conditional borrowed-guard, corrupt-recovery, incompatible-ordering and stateful uncertain-publication regressions. The rollback methods retain their compatibility entry points while delegating to a focused helper, keeping the orchestrator within the existing 1,000-line cap.
- Windows validation passes the integrated canonical suite (403 tests, 24 platform skips), actual Worktrees vendor (113 tests, four skips), and default plus conditional Bridge consumers (16 tests). Updated native replacement test interception for Windows and retained Linux-only directory-fsync cases in the native Linux lane. Updated stale vendor fixtures to supply birth/witness/exit evidence and to retain promoted generations; no production custody guard was weakened.
- Fatal lint, module size, headless launch, install contract, changefile coverage and vendor synchronization pass. Fresh current-head review and required native Linux/Windows CI still gate merge. The watch-owner recovery adapter, installation-cell adoption, rollout and saved dispatch integration remain open; this foundation does not complete the parent effort.

### 2026-10-12 - Governed lifecycle intent approved

- The next current-head review identified numeric thread-ID recycling in the borrowed-guard boundary. Ownership now retains the acquiring `Thread` object alongside its PID and compares object identity; an unrelated execution with the same numeric identifier cannot borrow the guard. A deterministic regression preserves the original execution's borrow and native contender exclusion. Adoption remains gated on this shared slice's current-head review and CI.
- Current-head review confirmed the four publication/guard findings resolved but identified a committed-record crash window before predecessor retirement. Stateful committed records now remain fenced against reentry and both legacy recovery helpers. Post-commit errors and availability-only commit-forward preserve that evidence; only an owner's explicit exact-retirement confirmation permits normal breadcrumb cleanup. Crash, lost-response, unconfirmed-retirement and commit-forward regressions pass in the canonical Windows suite (266 tests, 15 platform skips); current-head review and CI remain required.
- The next review ran in degraded Lite mode after runner startup timed out. Its four findings are addressed: opt-in publication accepts a direct exact endpoint or the canonical tuple and documents uncertain-result refusal; an externally closed guard is neither borrowable nor reported held; the logical-clock fixture preserves all other time-module attributes without altering the process-global clock. Three new regressions cover direct results, unknown-result evidence, and closed guards. Canonical Windows validation passes 262 tests with 15 platform skips; current-head review and CI remain required.
- The next current-head review identified a predecessor-selection race between the CAS snapshot and resolved endpoint. The opt-in path now refuses before spawn or drain unless the selected predecessor agrees with that snapshot and a fresh table read is unchanged. Added changed-route, ABA-selection and stable previous-only regressions; the canonical suite passes 259 tests with 15 Windows platform skips, and the actual vendored suite passes 113 with four platform skips. Adoption remains gated on current-head review and CI.
- Shared-seam review identified a legacy repair hazard: an unresolved pre-adoption breadcrumb could reopen the predecessor without proving successor exit. Opt-in breadcrumbs now require installation-owner reconciliation under the shared guard; legacy repair and cutover reentry refuse to overwrite that evidence. Guarded route restoration and explicit undrain confirmation must both succeed before rollback is terminal. The adapter remains a required adoption item, not an implemented shared-library capability.
- The preceding shared-seam head passed native Linux and Windows CI (run 38330331103). Added regression coverage for recovery refusal, displaced ownership, failed restoration/undrain, inconsistent probes and nonterminal double failure; the revised head still requires fresh review and CI before merge.
- Fresh review confirmed those ten findings resolved and identified two additional publication-identity gaps. Pre-adoption publication now requires CAS even without a refusal callback; rollback and commit-forward retain the published successor's full endpoint identity. The revised canonical suite passes 256 tests with 15 Windows platform skips, including changed start-token/version/generation, route displacement during drain and displaced commit-forward regressions. Native Linux/Windows shared-library lanes passed on the preceding recovery head; the final CAS revision still needs current-head review and required CI.

- #6291 merged at `612d71cd39f75c9684f492a7bd7f24ae3373483d` after a current-head approving review with zero findings and successful required CI.
- The reviewed plan includes full-transaction serialization shared with mutating recovery and on-demand startup, explicit cold runtime selection without daemon activation, dedicated identity and owner termination tests, and an explicit macOS fail-closed contract and validation lane.
- Pulled the adoption worktree forward after proving the squash-merged effort files match the reviewed branch; preserved the pre-sync recovery reference instead of replaying landed plan commits.
- Beginning canonical graceful-cutover and installation-cell implementation. Adoption, release/rollout, saved dispatch integration, remaining validation, archive, and finalization stay open.
- The first implementation slice adds opt-in pre-publication drain and reuse of a caller-held canonical cutover guard. Default cutover ordering stays unchanged; stateful rollback never resumes the predecessor before confirming successor exit. The PR owner does not opt in or change activation yet.
- Shared-library validation on Windows passes, including held/unheld/foreign guard, forced or unclean drain refusal, confirmed/unconfirmed successor cleanup, and durable unresolved-phase cases. Corrected a controlled Linux-cleanup test fixture to supply Linux signal constants on non-Linux test hosts; no production process-signaling behavior changed. Native Linux validation remains the existing CI shared-library lane.

### 2026-10-12 - Adoption service-boundary investigation

- Stopped before implementation at the service-cutover architecture gate. `agent-pull-requests serve` starts the watch owner: it immediately acquires its singleton lease, restores subscriptions, starts polling/delivery, and publishes its authenticated control endpoint through the installation-owned rendezvous record. `agent-pull-requests serve status --json` probes that endpoint. Its current `serve restart` stops the predecessor and waits for lease release before starting a successor; it has no passive-instance promotion, route-bound ownership attestation, or installer-driven service transaction.
- The installation-cell pattern's lifecycle contract and `docs/install-contract.md` requirements 10–12 require a durable marker/manifest selection transaction, passive health and exact-instance promotion, governance rechecks, and attributable recovery. The graceful-cutover pattern's invariant 0 applies to this endpoint-serving daemon even though its subscriptions and pending events are durable.
- Reusable canonical boundaries are `validate_context_receipt`, `resolve_installation_mode`, `compare_and_swap_activation`, `provision_runtime_slot`, `complete_runtime_slot`, and `cutover_runtime_slot`, plus `peer-launch`'s receipt/governance validation and rebound child environment. Slot cutover publishes runtime markers; it does not supply a watch-service selection transaction or transfer its singleton lease. The shared installer engine supplies build/manifest helpers, not a generic service coordinator. Agent Index's selection/service coordinator remains plugin-specific and must not be copied or treated as a standalone PR-owner adapter.
- Implementation stopped pending a scope decision: review a watch-owner service transaction using the canonical graceful-cutover primitives, including the safe subscription/delivery adoption boundary, or explicitly review a different lifecycle contract before coding. Merely vendoring validators, adding a peer name, or redirecting state would not satisfy adoption. No implementation or validation item was marked complete; no runtime, subscription, generated consumer, or release metadata was changed.
- The operator then selected `governed-cutover`. Added the agent-recommended lifecycle plan for review before resuming implementation; the approved adoption and saved dispatch integration remain the parent objective.

### 2026-10-12 - Adoption prerequisite authorized

- #6146 merged after a current-head approving review with zero findings and successful required CI. The reviewed installation-cell prerequisite now governs implementation.
- Claimed #6286 for PR-owner installation-cell adoption after checking for overlapping issues and pull requests.
- Continuing from the merged plan; saved dispatch integration remains separate and gated on adoption.

### 2026-10-12 - Adoption amendment review reconciliation

- Refreshed the amendment onto current `dev` after review exposed the old payload working directory in its source head.
- The separately merged #6183 resolves #6161 with profile-directory execution on both platforms and absolute coordinator inputs. Retained that upstream fixture unchanged and removed this amendment's redundant agent-index changefile.
- The adoption amendment remains documentation-only; adoption implementation still awaits this PR's current-head review and merge.

### 2026-10-10 - Installation-cell prerequisite selected

- The notification prerequisite #6019 merged. Dispatch integration remains saved locally, not publication-ready.
- Reconciled its six completed Phase 2 Plan items; the remaining adoption, integration, and deployment items stay open.
- The real peer-boundary test found missing PR-owner installation-context payload support, so merely adding the peer name cannot satisfy cell-scoped delegation.
- Operator selected full PR-owner installation-cell adoption before finishing delegation. This amendment adds that reviewed prerequisite without discarding the saved integration.

### 2026-10-09 - Resumed deferred work

- Operator confirmed the effort slug and authorized continuation.
- Claimed #6007 after checking for overlapping issues and open PRs.
- Read the current watch owner and dispatch preparation path; confirmed that fired callbacks are not durably acknowledged today.
- Created this proposal for review. No implementation has been replayed from the preserved branch.

### 2026-10-09 - Proposal approved and merged

- #6011 merged after an approving automated review with zero findings and successful CI.
- Pulled the source worktree forward onto the reviewed proposal.
- Beginning the watch owner's acknowledged-delivery contract first; dispatch integration remains gated on that prerequisite.

### 2026-10-09 - Notification contract implementation

- Implemented opt-in `acknowledged_notifications/v1`, immutable durable fired events, independent retry workers, generation-safe cancellation, and legacy wire compatibility.
- Independent review reproduced callback shutdown and failed-registration persistence gaps. Corrected the drain budgets and rollback behavior and added boundary regressions before publication.
- The preserved dispatch patch cannot be replayed wholesale: current transactional prepare/arm/finish must be extended with a negotiated waiter kind, authenticated process fallback, and explicit delegated-owner recovery. Steer-idempotency work remains excluded.
- Full contained verification is waiting behind another live, bounded test-runner admission owner; structural and fatal-lint gates have passed. No shared peer was interrupted.
- Coordinated a short admission gap with the owning peer; full contained verification completed with 114 passing tests. The slot was returned to that peer after the run.
- Review-driven refinements now validate persisted baselines and protocol echoes, persist cancellation before memory removal, sync the POSIX state directory, and bound callback concurrency to eight workers. Shutdown ignores snapshots fetched after draining begins; malformed absolute deadline state fails closed. Full contained verification reached 127 passing tests.
