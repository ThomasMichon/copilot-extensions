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

The direct #2874 unchanged-cursor family keeps waiter and independent periodic-evaluator
consumers distinct, including honest timeout and retry-ceiling escalation. Preserve
existing provider, evaluator, local-callback, remote-replay, credential/service, and
lifecycle owners.

The direct #2797 idle-record consumer stays under this existing host and shared PR
observation/Worktrees write-path owners. Enrollment is independent of session/Picker
demand; preserve every existing claim and guarded-finalization OR ready-indication
alternative.

The direct #2700/#2881 checkout-free operation matrix extends the existing #2400
client/migration lane under the same effort host. Keep #7188 service/consumer, #7051
conformance and Worktrees publication/resource and credential/provider claims bounded
and unchanged. Continue through this Plan/Validation/Journal and eligible open bounded
source claims, not a closed tracker or new generic sign-off rendezvous.

- **Topology:** Serial, independently reviewable PRs against `dev`.
- **Host (owns PRs):** Delegation driver.
- **Delegates:** Any bounded implementation delegate receives an exclusive slice; the driver owns integration and completion.
- **Fleet expansion:** The PR-service plan steward owns the
  `Fleet-hosted PR service expansion` slice. #5789, #6030/#5861,
  #6236/#6240/#6241 and #7051 retain routing, deployment, integrated lifecycle
  and PR-conformance ownership. This effort consumes their contracts rather
  than taking over those campaigns. Retained ownership does not require
  asynchronous sign-off before source contribution: the delegation driver
  closes consumer gaps, including narrowly scoped shared-module changes,
  through normal review and tests. Consult related campaigns without waiting
  for acknowledgements; coordinate concrete conflicts rather than treating
  another issue's owner as an additional permission gate.
- **Review sequencing:** Land the fleet vision/plan amendment before its runtime
  implementation. Keep the fleet scope separate from the installer prerequisite.
  The later operator disposition accepts reader residual #7208 and stops its
  investigation; it does not relax canonical installation validation, current
  checks or review.
- **PR-asset/client migration intake:** The direct #2400 Plan/Validation lane
  below accepts presentation, addressing and staged compatibility work under
  the same effort host. It does not expand #7188's existing bounded source
  claim or take #7051 conformance/Worktrees resource and publication custody.
  Coordinate through this Plan/Journal and eligible open bounded source
  issues, resolving actual overlaps through normal review, not a new sign-off
  rendezvous. Native client work remains useful before fleet deployment.
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

Coordination correction, verbatim:

> We need to break "retained-owner agreements"; it's causing deadlocks

This removes the separate owner-agreement prerequisite for source work. The
driver proposes and implements missing contracts through ordinary reviewed PRs,
preserving campaign/resource boundaries and resolving actual conflicts.
It does not grant credentials, change deployment authority or authorize a
rollout of unrelated machine components.

Revocation failure contract, verbatim:

> Treat revocation like any other network disconnect

This supersedes the initially selected distributed-revocation-barrier approach.
Policy withdrawal stops admission/emission and signals request cancellation;
it does not wait for remote effect settlement or promise rollback. A commit
already in flight remains uncertain and must reconcile its original identity.
Known disconnected work must not start a subsequent lookup/commit, and existing
transaction custody must remain held until that work actually settles.

## Plan

### Governed watch-owner cutover prerequisite

_Agent-recommended design and validation, explicitly selected for review._

- [x] Review and merge the [watch-owner lifecycle plan](watch-owner-cutover.md) before implementing the service transaction (confirmed by #6291).
- [x] Land the canonical pre-adoption drain, full-transaction borrowed authority and custody-preserving rollback prerequisite before the owner adapter (confirmed by #6396).
- [x] Land inactive watch-state construction, final read-ready reload and bounded complete quiescence as internal groundwork, without enabling a production service or installation-cell adapter (confirmed by #6714).
- [x] Land non-signaling native process-exit references captured before graceful control, without granting receipt, installation or retirement authority (confirmed by #6720).
- [x] Adopt canonical graceful-cutover primitives with installer-driven activation, passive health, exact-instance promotion and retirement, and crash-consistent marker/manifest/route recovery (source landed in #7160).
- [x] Define and enforce one installation-scoped watch-state writer across predecessor drain and successor subscription/delivery adoption; preserve event and registration identities without overlapping live-generation delivery (source landed in #7160).
- [x] Prove rollback, commit-forward, governance refusal, crash recovery, callback drain, and refusal of lease-only owners without verifiable same-cell ownership on Windows and Linux before landing the cutover prerequisite (exact-head native acceptance `39226209682`).
- [ ] Land and deploy the governed lifecycle prerequisite as part of PR-owner adoption before enabling installation-cell dispatch delegation. Source landed in #7160 and is released in #7291; complete installed-contract acceptance remains pending.

### Installation-cell prerequisite

_Agent-recommended prerequisite, explicitly selected by the operator._

- [x] Review and merge the installation-cell plan amendment before adoption implementation (confirmed by #6146; the lifecycle amendment above remains a separate review gate).
- [x] Adopt the canonical installation-context payload, installer/runtime-resolution and state-root contract in `agent-pull-requests`, preserving supported legacy installs without silent cell-to-legacy fallback (source landed in #7160).
- [x] Include the PR owner in the generated same-cell peer-launch contract and declare release impact for every generated consumer (source landed in #7160).
- [x] Prove first installation, upgrade, invalid/foreign receipt refusal, state isolation, actual two-cell PR-owner invocation, and pending watch-subscription recovery using the shipped payload rather than a fabricated sibling contract (exact-head native acceptance `39226209682`).
- [ ] Land and deploy the adoption prerequisite before enabling installation-cell dispatch delegation. Source landed in #7160 and is released in #7291; keep dispatch delegation inactive until complete installed-contract acceptance clears.

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

- [x] Review and land the first-class PR plugin/service vision, single-operator
  fleet invariant and [fleet service plan](fleet-pr-service.md) before building
  its new runtime paths (confirmed by #7189).
- [x] Land the PR-owned request reconciliation and bounded consumer replay/ACK
  transaction core without activating a serving adapter (confirmed by #7786).
  Authenticated serving/dispatch and full fleet acceptance remain below.
- [x] Land an opt-in trusted caller handoff in the real Gateway/connector SDK,
  preserving the identity-less HTTP proxy path (confirmed by #7810). PR-specific
  serving and full fleet acceptance remain below.
- [x] Land the optional PR-owned configured-scope Gateway serving adapter and
  durable complete-envelope disk transactions, without activating a daemon,
  credentials or placement (confirmed by #7832). Production enrollment/effect
  fencing, observation, consumer and lifecycle acceptance remain below.
- [x] Reuse the central native PR commands for thin-client status, checkout-free
  creation, policy-gated completion and wait, preserving the default GitHub
  interface without another forge adapter (confirmed by #7850). Broader client,
  serving and deployed conformance remain below.
- [x] Land and exercise connector-owned request lifetime through PR replay
  lookup/commit/result boundaries. Treat current-policy withdrawal as network
  disconnect, preserving uncertain in-flight commits and original-ID recovery
  (source confirmed by #7915; provider and deployed acceptance remain open).
- [x] Bind a distinct PR observation worker to exact typed forge scopes in the
  existing credential exchange, preserving provider validation and recipient
  verification before ACK (#7926). Native service integration and production
  provider/deployment acceptance remain below.
- [x] Land the additive native read-only per-request credential consumer seam,
  including GitHub review/check pages and ADO HTTP continuation, typed
  endpoint/repository/authentication/expiry checks and no ambient fallback (#7954).
  This source item does not close the process-boundary service adapter,
  production-provider validation or installed/fleet acceptance.
- [x] Land the explicit native-command credential adapter, reusing the typed
  worker's public configuration/command and bounded captured output rather
  than importing Fleet into Worktrees. Preserve per-request renewal,
  scope/expiry/refusal and the ordinary unselected CLI paths; service
  activation and production-provider validation remain separate (#7984).
- [x] Land a finite replay-observation adapter over the native credential-backed
  bridge. Coalesce only exact account/repository/PR groups; release disk custody
  across reads, apply only captured registration identities, and retain original
  events/deadlines through provider loss, cancellation and restart. Source
  validation does not close service scheduling, activation or production proof
  (source confirmed by #8028).
- [x] Land the finite existing-profile Gateway caller SDK with current credentials,
  bounded HTTP bodies/concurrency/deadline, no automatic retry or ambient
  fallback, and explicit uncertain mutation results (source confirmed by #8059).
  PR-specific replay consumption and installed/fleet acceptance remain below.
- [x] Land the finite PR-owned typed replay client over that SDK, preserving
  original request criteria and exact completion identities without retries,
  local persistence or a second wait owner. Source client validation does not
  close the shared Dispatch consumer or installed/fleet acceptance (#8072).
- [x] Land the finite shared Dispatch replay consumer over the existing
  prepare/arm/finish transactions, retaining original intent and terminal
  settlement progress through disconnect and restart. This source leaf does
  not close coordinator scheduling, service lifecycle or fleet acceptance (#8085).
- [ ] Land optional replay-consumer scheduling through the coordinator's existing
  waiter-maintenance cadence, active-owner checks and drain/shutdown boundary.
  Keep native recovery independent of remote IO and retain original identities
  after interrupted cycles. This source seam does not configure a production
  caller, attest its namespace, activate a backend or close service/fleet proof.
- [ ] Define and deliver the [PR-service consumer contract](pr-service-contract.md)
  through normal reviewed source changes, including missing shared seams and
  real serving/consumer proof. Consult related campaigns without a separate
  owner-sign-off gate; do not advertise unsupported routes or treat an isolated
  admission API as service authority.
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

- [x] Recognize only supported PR-watch invocations and discover the optional watch owner through its attributable installation boundary, never ambient `PATH` (landed in #7714).
- [x] Negotiate support from both owners before suspension. Missing, legacy, or incompatible capabilities select the existing process waiter without sending new fields to strict legacy schemas (landed in #7714).
- [x] Record an explicit validated waiter kind and reuse the coordinator's prepare/arm/finish generation fences. Prepare before subscribing; make callback completion retryable and idempotent for that exact generation (landed in #7714).
- [x] Preserve explicit `--token` and control-token behavior without persisting secrets in subscriptions or callback argv. Use process fallback when the daemon cannot reconstruct the caller's credential context safely (landed in #7714; deployed credential acceptance remains below).
- [x] On ambiguous prepare/register failures, reconcile or abort the exact prepared generation before considering process fallback. Never arm two independent wait owners for one task (landed in #7714).
- [x] Validate a finite positive PR-watch timeout, preserve timeout wake behavior, and provide visible recovery for delegated waits whose subscription or delivery owner is unavailable (landed in #7714).
- [x] Keep arbitrary-command detached waits, ordinary non-detached waits, and independent installation unchanged; do not add an inert manifest `dependencies` field (landed in #7714).
- [x] Update the owning plugin documentation, CLI help, and changefiles; land the integration PR after the required review and checks (confirmed by #7714).

### Phase 4 - Deploy and close _(agent-recommended)_

- [ ] Confirm promotion of the changed source to the release branch by content, then deploy through the unified update surface. Prerequisite promotion and content are confirmed in #7291. An operator-authorized local unified update installs the PR-owner runtime but exits nonzero on independent construction/profile failures. The dispatch integration source landed in #7714 and is content-confirmed in release #7751 (Dispatch `0.18.41-dev1`, PR-owner `0.3.36-dev1`); authorized rollout and complete isolated installed-contract proof remain open. No new deployed-runtime changes are authorized by the source merge or release.
- [ ] Exercise the deployed contract in isolated state without interrupting shared coordinators, watch subscriptions, or peer sessions.
- [ ] Complete the fleet service acceptance in the linked plan on explicitly
  approved capacity, including persistent registration/delivery and the actual
  Gateway path. Local delegation alone does not complete the expanded effort.
- [ ] Synchronize consumer guidance when changed payload provenance requires it, through the consumer's own PR flow.
- [ ] Record outcomes, resolve every validation item or transfer it to a named tracked objective, archive this effort, release its binding, and finalize owned worktrees.

### Direct PR-asset and staged client-convergence acceptance

<!-- backlog-reconciliation:7556:transfer:2400:to:efforts/active/durable-pr-watch-delegation/README.md -->

This is additive live Plan/Validation intake into the existing Active PR-domain effort,
not completion of a view, migration, service or fleet. Preserve the entire existing
plan, governed lifecycle/installation/native/credential acceptance and its accepted
deferrals. The already delivered dedicated runtime, named-target foreign create/caller
claim and explicit native four-verb adapter are baseline, not blanket legacy parity.
Worktrees retains generic resource/status, source publication and repository policy; PR
conformance retains its current owner. The existing service-planning/consumer claim is
not silently enlarged into presentation or migration custody.

#### Plan

- [ ] Deliver durable PR claim/association/follow state for worktree-created
  cross-repository changes. Surface PR identity and actual status prominently as primary
  work state, not only free text, while preserving caller attribution, ambiguous
  post-create claim recovery and unresolved merge/resource obligations.
- [ ] Deliver the PR-centered view for active cross-repository work through the
  PR-domain client and existing Worktrees presentation contracts. Preserve the original
  durable-major-asset status-driver intent through existing generic resource/status
  ownership; do not discard similar-asset integration merely because the remote
  registrar owns only PRs, and do not create a second generic resource ledger or claim
  unrelated UI frameworks.
- [ ] Keep the dedicated agent-pull-requests noun/verb surface and explicit --repo
  addressing reusable across repositories without checking out the target. Resolve the
  remaining from-any-CWD usability through an explicit supported claimant/target
  selection contract while preserving admitted mutation custody. Current managed-caller
  guards are useful safety baseline, not proof of arbitrary-CWD completion or permission
  to remove ownership checks.
- [ ] Reuse the existing account/credential, provider, publication and policy contracts.
  Native/local operation remains useful without fleet adoption; explicit native
  selection retains actual provider-native votes, approval events, human/policy gates,
  uncertainty and unsupported-flag refusals rather than collapsing them into a GitHub
  verdict or silently switching provider.
- [ ] Inventory every legacy *-pr/pr-* and create-pr behavior and deliver the dedicated
  surface's superset through shared owner contracts or supported compatibility adapters
  before deprecation. Include source attribution, labels/body policy, foreign
  already-pushed creation, claiming, review/wait/watch, merge/consent and
  completion/resource effects wherever supported. Four verbs, a healthy daemon or
  the7850 bridge alone do not certify all-current parity; central
  source-control/publication authority does not move into a remote service.
- [ ] After parity, sweep all consuming documentation and supported
  examples/entrypoints, preserve compatible legacy paths while any dependency remains,
  then deprecate/remove old Worktrees verbs only after the dependency inventory supports
  it. Do not treat a plan, partial docs edit or service rollout as the completed
  migration.
- [ ] Keep owning/observing a foreign PR separate from semantic source import: never
  import its commits into the caller checkout or waive its unresolved merge/claim
  obligation merely because it is tracked or merged. Preserve existing target-policy and
  caller-resource authority.
- [ ] Record presentation/migration intake and continuing coordination explicitly under
  the existing effort host before retiring the source tracker. Existing7188
  service/consumer,7051 conformance and Worktrees publication/resource claims keep their
  scopes and owners; use bounded normal reviewed changes for missing integration,
  nonblocking consultation and concrete conflict resolution, not a new generic signoff
  ceremony or implied takeover of an active PR.

#### Validation

- [ ] Verify actual durable association, prominent identity/status, PR-centered
  cross-repository view and existing generic asset-status integration through supported
  client/presentation consumers, not solely a provider result or plan link.
- [ ] Qualify named-target/no-target-checkout and remaining CWD-independent addressing
  with explicit correct caller custody, account/provider separation and actionable
  refusal. Preserve current direct/native defaults, supported platform argument
  boundaries and uncertainty without weakening central gates.
- [ ] Establish an operation-by-operation legacy parity/compatibility matrix and actual
  consumer documentation/dependency inventory before staged deprecation. Keep finite
  client acceptance separate from already-existing installed/service/fleet acceptance;
  no unrelated whole-parent gate is added to a finite delivered leaf.
- [ ] Verify no foreign source import, lost PR/resource obligation, duplicate
  forge/credential/watch authority or silent owner reassignment. Existing service
  credentials, writer lifecycle, installation and approved deployment gates remain
  exactly as accepted elsewhere in this plan.
- [ ] Close the administrative source only after an actual normally reviewed merge
  accepts every row directly in the live receiving Plan/Validation, continuing effort
  coordination explicitly covers presentation/migration without changing existing
  claims, and a full current section readback confirms no original clause or accepted
  refinement was lost. If that intake is not accepted, retain the source. This proposal,
  a topic link, the old7189 creation receipt or source7850 alone is not acceptance
  delivery.

No implementation, account/credential use, tests, native/model/host probe, install,
runtime update, shared-resource operation or deployment is authorized by this checklist.

### Direct checkout-free operation and inherited ADO matrix from #2700/#2881

<!-- backlog-reconciliation:7556:transfer:2700:to:efforts/active/durable-pr-watch-delegation/README.md -->

Add directly to the existing Active ROOT Plan and Validation beside the accepted2400
client-convergence lane; do not replace any prior row or history. Actual foreign
creation/watch/merge, explicit status/metadata-only bindings and bounded7850 native
four-verb composition are credited baseline, not the full reviewer/legacy matrix.
Preserve7051 conformance,7188 bounded service/consumer, Worktrees publication/resource
and current credential/provider claims. Prior requests to keep checkout-free coverage
open remain binding until this entire direct acceptance is actually delivered and read
back; no feature is declared complete or source custodian changed.

#### Plan

- [ ] Reconcile EVERY relevant existing PR surface: create/track already-pushed remote
  branches, status/watch, ready/update, merge/complete and supported reviewer
  diff/comment/review/thread operations. Label each delivered, unsupported, incomplete
  or unproven entry explicitly, including remaining checkout-free reviewer routes; four
  verbs or a provider primitive alone are not entrypoint parity.
- [ ] Address the target by canonical explicit repository/PR identity OR an approved URL
  binding without a local TARGET checkout. Preserve the caller's own rightful
  claimant/lifetime where required; never equate target-checkout independence with
  removing caller completion/resource ownership.
- [ ] Resolve target provider, hosting endpoint, repository/account identity, branch
  convention and contribution/review policy from its admitted registered/metadata-only
  binding. Missing, malformed, ambiguous, unreachable or unauthorized evidence must
  refuse clearly, never use the caller's provider/config/account or a different target
  as fallback.
- [ ] Retain honest structured ADO build validation, applicable policy evaluations,
  reviewer votes/approval events, active/unresolved threads, mergeability and terminal
  outcomes through the shared provider-neutral surface. Unknown/unsupported evidence is
  not a passing gate; votes, policy and human approval remain distinct.
- [ ] Preserve actual target merge_actor, human-only/maintainer requirements, explicit
  completion authority and accepted native gates. Author self-review, a self-merge
  profile or a returned status cannot bypass real policy or grant missing approval.
- [ ] Preserve already-pushed creation with an already-valid caller-supplied branch OR
  admitted target convention, attribution, tracked PR identity and reciprocal
  obligations. Never publish unrelated caller commits to the foreign target or import
  foreign merged history into caller source merely because the PR is owned or observed.
- [ ] Reuse established scoped credential-provider/relay/account boundaries without
  hand-built auth/REST workarounds, ambient identity substitution or a second
  watcher/client. Wait estimates and polling cadence remain separate from actual
  provider gate evidence and completion ownership.
- [ ] Continue this finite client/matrix lane through the existing effort host, direct
  Plan/Validation/Journal and open bounded source coordination. Related claims keep
  their scopes; source contributions use normal review/nonblocking consultation and
  concrete conflict resolution, not a new generic owner-ACK gate. Keep native commands
  useful before fleet adoption and staged2400 compatibility/deprecation sequencing
  intact.

#### Validation

- [ ] Cover metadata-only/registered/unregistered/ambiguous target binding, no local
  target checkout, correct caller/target distinction, ADO policy/build/vote/thread
  states and multiple-provider parity through real owning front doors and bounded
  contract fixtures. Credit actual7226/7850 witnesses at their stated limits; identify
  remaining reviewer routes and missing native lanes rather than converting them into
  pass or requiring unrelated entire fleet/installer campaigns.
- [ ] Verify explicit target refusal, no caller/provider/account fallback, no foreign
  source import/lost obligation, actual authority/role gates and existing
  credential/observation ownership. No live provider mutation, account/authentication
  probe or deployment is authorized by the matrix.
- [ ] Before administrative closure, a NEW normal reviewed merge must accept EVERY
  original2700 clause and all eight inherited2881 rows directly in this ROOT
  Plan/Validation, followed by CURRENT FULL readback and live continuing
  Coordination/Journal. Old2400/7189/7885 intake, a topic link, proposed patch or
  source7850 alone is not this receipt. If the full intake is not accepted, retain2700.

<!-- backlog-reconciliation:7556:transfer:2797:to:efforts/active/durable-pr-watch-delegation/README.md -->

### Direct idle-worktree merged-PR reconciliation acceptance from #2797

Add directly to this existing Active ROOT Plan/Validation, preserving its full current
body and every existing conformance, service, credential, publication/resource and
rollout claim. This is unassigned consumer acceptance under the same effort host, not a
new implementation/resource assignment or a running poller. Existing shared PR
observation and Worktrees ground truth remain the owners.

#### Plan

- [ ] Enroll every eligible tracked cached-open PR for periodic provider observation
  independently of live mux/session, an open Picker and transient reader demand.
  Preserve source/machine/repository/PR/account identity; local and remote ownership
  cannot be confused.
- [ ] Use the existing resident/collector cadence integration OR admitted shared PR
  observation contract, with rate-aware bounded cadence, backoff, fairness and honest
  unavailable/unknown outcomes. Reuse one authorized target observation stream; do not
  create a second polling service or caller-side fleet scan.
- [ ] Persist authoritative merged/closed PR facts through the owning Worktrees write
  path and surface the actual current result in list/Picker. Reconnect, restart and lost
  observations cannot fabricate closure or silently forget enrollment.
- [ ] Preserve the original guarded auto-finalization OR clear ready-to-finalize
  indication. A merge fact alone is not whole-objective completion, no-new-work proof or
  authority to release unrelated claims, validations, pending handoffs or active
  efforts; any actual settlement/finalization keeps the existing fresh safety/permission
  contract.
- [ ] Retain existing PR-watch/service/conformance and Worktrees lifecycle/publication
  owners and all current claims. Continue the exact leaf through this root's
  Plan/Validation/Journal and open bounded-source flow, resolving concrete overlaps
  through normal review without a new generic ACK ceremony.

#### Validation

- [ ] Exercise actual tracked-record enrollment -> shared provider observation ->
  durable writeback -> current list/Picker consumer with multiple idle/no-session
  cached-open PRs and no open Picker; include missing/invalid/unauthorized provider,
  rate/backoff, restart and duplicate subscription cases.
- [ ] Verify a merged PR with remaining claims/new work/active effort is only an honest
  fact/candidate until canonical guards permit more; preserve terminal, dirty, unknown
  and foreign-source safety. A helper, source counter or open-Picker-only test is not
  all-idle coverage.
- [ ] Before administrative closure, obtain a NEW normal reviewed merge containing this
  entire direct ROOT intake and continuing coordination, then read back CURRENT FULL
  Plan/Validation/Coordination and unchanged custody. Old shared-watch/creation
  receipts, a child link or this proposal are not delivery. No
  monitor/sweeper/provider/UI/runtime action is authorized by this checklist.

<!-- backlog-reconciliation:7556:transfer:2874:to:efforts/active/durable-pr-watch-delegation/README.md -->

### Direct unchanged-cursor and nonevent wake acceptance from #2874

Add directly to this existing Active ROOT Plan/Validation, preserving every prior row,
current host and independent PR/provider, Dispatch waiter/evaluator, credential/service
and worktree claim boundary. Credit existing timeout handling, shared observation and
actual8085 replay progress without claiming the legacy/evaluator nonevent case complete
or silently expanding a bounded source claim.

#### Plan

- [ ] Bind each suspended watch to its exact provider/account/repository/PR, criteria,
  baseline cursor/head/state/review/check context and coordinator/task/waiter
  generation. Rearm/restart cannot borrow another target's baseline or treat unknown
  first observations as a transition.
- [ ] For an unchanged suspension baseline with no matched condition, the waiter and
  periodic evaluator must not manufacture a PR-change completion/wake. Attribute both
  routes independently; preserve fixed-cadence evidence as a hypothesis discriminator
  rather than assume an evaluator or timeout cause.
- [ ] Preserve explicit unmatched timeout/current snapshot, existing124 rearm and
  reviewed retry-ceiling gave-up/error outcomes. A legitimate escalation must say why it
  occurred and not claim a PR delta; operator-requested re-evaluation is also distinct.
  Do not introduce an unconditional never-wake-on-timeout/error rule or silently reset
  the baseline to hide repeats.
- [ ] Make unchanged periodic evaluator decisions explicit NoOp. Keep meaningful new
  commits/reviews/dismissals/comments/check transitions, including pending-to-terminal
  cycles and genuine native history, actionable under their actual criteria. Snapshot
  payload, delivery/ACK or a timer alone is not semantic change evidence.
- [ ] Preserve original event/registration/request/deadline and generation fences across
  duplicate/lost delivery, replay, cancellation and restart. Shared-consumer source
  progress does not grant scheduling, provider credentials, process fallback or fleet
  activation authority.
- [ ] Continue this precise integration through the current root host, direct
  Plan/Validation/Journal and open bounded source scopes. Existing evaluator, local
  process-wait/callback and remote replay owners retain custody; normal source
  review/nonblocking consultation resolves actual overlaps without a new generic ACK.

#### Validation

- [ ] Exercise BOTH actual waiter-to-coordinator and independent evaluator-to-wake
  consumers against repeated identical cursor/head/state/review/check/comment
  observations, across poll intervals, rearm and restart. Assert no false change/resume
  and record independently attributable nonevent/escalation reasons; do not use private
  raw traces.
- [ ] Include unknown-baseline adoption, unmatched timeout/snapshot, retry ceiling,
  genuine failure, deliberate operator re-evaluation, meaningful provider transitions,
  duplicate/frozen replay and stale generations. A passing shared registry or timeout
  helper alone is not full end-to-end acceptance; qualify supported actual source/native
  tiers and label unavailable installed/provider lanes honestly.
- [ ] Before administrative closure obtain a NEW ordinary reviewed merge containing this
  complete direct ROOT two-route acceptance and continuing coordination; independently
  read back CURRENT FULL Plan/Validation/Coordination and all existing claims/holds. Old
  service/replay receipts, a child section/link or this proposal are not acceptance
  delivery. No live wait, evaluator, provider, task wake, service or runtime action is
  authorized.

## Validation Plan

- [ ] Resolve every directly retained #2874 Plan/Validation clause above at its original
  finite scope and evidence tier, preserving alternatives, same owners, holds, and
  current portfolio bounds. Administrative intake is not implementation, native
  acceptance, rollout, or completion of unrelated parent work.

- [ ] Resolve every directly retained #2797 Plan/Validation clause above at its original
  finite scope and evidence tier, preserving alternatives, same owners, holds, and
  current portfolio bounds. Administrative intake is not implementation, native
  acceptance, rollout, or completion of unrelated parent work.

- [ ] Complete every directly retained #2700 Plan/Validation clause above at its original evidence tier and bounds; preserve current owners, holds and unfinished acceptance. Tracker consolidation is not implementation.

- [ ] Complete every directly retained #2400 Plan/Validation clause above at its original evidence tier and bounds; preserve current owners, holds and accepted deferrals. Administrative supersession is not completion.

All items below are **agent-recommended** acceptance checks.

- [x] Native PR client: preserve provider evidence and central publication/claim/
  merge gates; refuse invalid selection and blank pushed heads before effects;
  prove option reparsing and final executable argument preservation (#7850).
- [x] Unit: failing/nonzero/timeout callback stays pending; acknowledgement removes only its own registration; replay preserves event identity and payload; cancellation and re-registration are generation-safe (#6019, #7160 and #7714).
- [x] Unit: durable event survives crash before delivery, crash after remote commit before acknowledgement, restart, and supported daemon cutover; unrelated subscribers continue to receive notifications (#6019, #7160 and #7714).
- [x] Unit: already-terminal PR, finite timeout, polling outage, expired wait, and malformed inputs produce the intended explicit results (#6019 and #7714).
- [x] Compatibility: old/new client, coordinator, and watch-owner combinations retain legacy schemas and a functioning process fallback; missing optional plugin affects only the optimization (#7714).
- [ ] Credentials: explicit CLI credentials and command-backed configuration still authenticate; no credential value is persisted in callback argv, subscription state, result diagnostics, or logs.
- [x] Service disconnect: real Gateway policy withdrawal and channel loss stop
  later replay work, preserve custody of already-started commits, emit no false
  success and reconcile the same request after fresh authorization/reconnect
  (#7915; synthetic issuer and isolated real TLS/disk, not provider/deployment proof).
- [x] Integration: real isolated watch owner and coordinator exercise subscribe/prepare/arm/finish, coordinator outage then recovery, duplicate delivery, stale-generation callback, and registration failure without a duplicate wait owner (#7714).
- [x] Fresh state: the supported first-touch path establishes only the owning plugin's state; two independent subscribers share one PR observation stream and need no per-task waiter process (#7714; installed adoption acceptance remains a separate prerequisite).
- [x] Platform: bounded callback launch and shutdown/restart behavior pass on Windows and Linux, using existing contained test tooling (exact-head source acceptance `39716960139`; installed native-account proof remains open).
- [x] Source gates: touched-plugin tests, fatal Python lint, module-size, headless-launch, install-contract, and required CI pass; add a changefile for each runtime plugin changed (#7714; current unit-only required policy, not a claim that the whole periodic portfolio passed).
- [ ] Live deployment: exercise an existing, authorized PR target with isolated subscriber/coordinator state when available; otherwise record the exact unavailable lane and a named follow-up before closure.
- [x] Documentation: review the final diff's impact on plugin behavior, failure/retry semantics, fallback, configuration, CLI help, and source-of-truth docs (#7714).
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

### 2026-10-22 - Direct retained acceptance from #2874

- Materialized the complete #2874 receiving portion directly in this ROOT Plan and
  Validation, with continuing coordination. All incumbent obligations and custody
  remain; historical private identifiers, where present, are represented by neutral
  historical placeholders without changing their role or outcome. A new normal reviewed
  merge and full current readback must precede administrative source supersession. No
  behavior, test, native result, or rollout is claimed.

### 2026-10-22 - Direct retained acceptance from #2797

- Materialized the complete #2797 receiving portion directly in this ROOT Plan and
  Validation, with continuing coordination. All incumbent obligations and custody
  remain; historical private identifiers, where present, are represented by neutral
  historical placeholders without changing their role or outcome. A new normal reviewed
  merge and full current readback must precede administrative source supersession. No
  behavior, test, native result, or rollout is claimed.

### 2026-10-22 - Compose optional replay scheduling with existing coordinator custody

- Extracted the coordinator's existing startup and coalesced preparing-waiter
  maintenance into one cohesive module; the native recovery cadence and
  ordinary CLI/backend selection remain unchanged.
- An explicit trusted factory supplies one consumer for the same queue and
  admission checkpoint. Existing maintenance ticks admit at most one remote
  coroutine with bounded unavailable backoff; slow remote IO does not block
  native recovery or create per-task workers. Active-owner/drain checks fence
  later work and preparation after capability negotiation.
- The existing drain gate counts the cycle. Coordinator shutdown first closes
  admission, then cancels and awaits owned coroutines. An in-flight service
  commit may still finish; a successor reconciles the original attempted
  request rather than treating cancellation as nonacceptance.
- Initial validation passed 106 focused maintenance/replay/fence/recovery cases
  and nine actual TLS/PoP/Gateway/connector cases. The latter now includes actual
  coordinator lifespans, exact completion and restart after shutdown during a
  held service commit. The existing explicitly selected Linux/Windows manual
  lane includes the new maintenance module; no required-CI matrix is expanded.
- Final scheduler selection passes 15 maintenance and nine actual transport
  cases, including finite cadence, capped/reset backoff and sanitized unexpected
  failures. Native compatibility includes all 22 owner cases: two aggregate
  attempts exceeded their wall budgets without assertion failures, then the
  four unreached cases passed as an explicit bounded selection. Coordinator,
  stop, local CLI and context groups also completed successfully.
- The broader CI-scope audit exposed 15 obsolete expectations for retired native
  jobs. An explicitly approved audit detour replaces those expectations with
  the current unit-only boundary, retained native source/manual qualification
  and unavailable periodic status. The manual Windows script now propagates
  both setup and final pytest failures explicitly. All 57 audit cases pass;
  `TESTING.md` no longer claims those retired jobs gate PR/promotion. This does
  not restore native matrices to required CI or claim unavailable qualification.
- This advances centralized reliable delivery in the PR-service vision and
  reuses the existing coordinator lifecycle and shared cadence. Documentation
  updates the Dispatch replay reference and README. Scheduling introduces no
  process, credential acquisition, new timer or tree/history scan; each admitted
  cycle keeps the existing bounded work contract. Native representative fleet
  CPU/latency and installed lifecycle proof remain separate acceptance work.
- Publication/review and expanded validation remain pending. No source leaf is
  marked landed yet. The independent PR artifact/role, production provider and
  caller/namespace mapping, authorized installed proof, placement/cutover and
  direct-client/provider/reviewer parity remain open. No rollout is performed.

### 2026-10-22 - Merge the shared replay consumer and retain lifecycle delivery

- #8085 merged as `f6926bb53dbbbe58d2b1f985a028adfa383cb95a` at
  `2026-10-23T05:02:29Z`, from published head
  `7e02262e678b6b23f334fb21699b40b3f6319d5f`. Exact-head Copilot review
  `5624059303` approved with no open issues. Read its complete body and the
  complete four-thread page; every thread was resolved. All actual required
  checks passed. Managed completion used only the documented additional
  CODEOWNER exception, not a CI or feedback bypass.
- Final hosted source acceptance `39902732218` passed both selected Linux and
  Windows jobs at that exact head. The final affected replay/wire/recovery
  selection passed 110 cases; the disjoint focused Dispatch portfolio accounts
  for 198 cases, with the 82-call worst-case and indexed-query assertions.
  Source tests, synthetic TLS identity and hosted accounts do not establish
  production-provider permissions, installed service operation or fleet budgets.
- Read this entire parent Plan and Validation after the actual merge. Close
  only the finite shared-consumer source leaf. Preserve every retained
  client/presentation/migration/reviewer matrix, installation, governed owner,
  credential/provider and fleet obligation. #7188 remains open.
- Next: compose the finite PR serving/observation and Dispatch consumption
  boundaries through their existing lifecycle and configuration owners.
  Deliver independently installable service/container operation and shared
  coordinator scheduling rather than a per-task poller or another controller.
  First inspect the reviewed fleet service plan and existing service
  composition contracts; source contributions need no separate owner ACK,
  while concrete overlaps, credentials and deployment authority remain bounded.
- Managed post-merge reconciliation preserved the complete published tip in
  `refs/pre-complete-backup` and removed only the three verified squash-merged
  commits. The same worktree stays with this Active effort; no journal-only PR,
  finalization, runtime update, production authentication, activation or new
  capacity follows from this source delivery.

### 2026-10-22 - Prepare durable shared Dispatch replay consumption

- Added an explicit optional replay composition over the existing typed PR
  client, with no change to ordinary CLI/default imports or the local callback
  optimization. Capability negotiation precedes task suspension. The original
  nonsecret Gateway/route/target/repository/caller-scope binding, request ID,
  issue time and deadline are committed to the same waiter row and generation.
  Caller scope is trusted embedding correlation, never authorization or an
  attested Gateway namespace.
- Indexed unsettled rows retain attempted/registration/settlement progress
  across retirement and restart. Each finite cycle visits at most 32 rows and
  reads one 16-event batch. No database transaction spans the network. The
  existing finish transaction commits the event and wake outbox before ACK;
  lost ACK results reconcile the original receipt even after pending removal.
  Cancellation/supersession affects only the original registration, and local
  callback/PID recovery skips remote preparations and active waits.
- Revocation, channel loss and cancellation retain disconnect semantics:
  stop the failed cycle, retain potentially committed work and reconcile the
  original identity under renewed authorization. No second registration,
  process fallback, rollback or distributed publication barrier is introduced.
- Validation: 198 disjoint focused Dispatch cases passed across the replay,
  migration/recovery, local CLI/context and real owner callback modules. Seven
  final controls use the actual TLS/PoP SDK, Gateway/connector, SQLite and PR
  replay disk owners, including lost register/ACK/cancel responses and policy,
  channel and cancellation loss during commit. Native callback families passed
  separately (16 + 6) without changing item limits. Initial aggregate 240s and
  two-file 180s groups timed out without assertions; those are not passing
  evidence and the complete partitioned runs supersede them. Four targeted
  test-preparation cases and two acceptance-selector cases passed. Install
  contract passed for all 13 plugins; Ruff, headless, module-size and async
  timeout guards passed. The module-size check initially caught 1006 lines in
  the existing waiter module; new replay persistence helpers moved into the
  dedicated replay mixin without changing the enclosing transaction.
- The first focused run exposed cursor-turn latency. The final cursor advances
  only over visited rows, including a failed one; an exhausted scan yields a
  separately admitted pending-only turn, then wraps on the following cycle.
  This prevents an unresolved final row from starving healthy pending events.
  Tests also cover capability-before-prepare,
  immutable intent across awaits, duplicate bindings, stale generations,
  original deadlines, batch bounds and dependency-free default imports.
- Initial publication as #8085 exposed a source-attribution regression in test
  preparation: grouping the Fleet install condition hid the existing PR
  plugin's literal per-consumer source branch from the dependency-age audit.
  Restore explicit named branches; do not broaden the audit's exemption or
  consult public metadata for the first-party source. Thirteen focused
  attribution/preparation cases pass, including the actual three replay
  dependency mappings. The full local audit cannot validate public npm TLS on
  this host; the unchanged hosted audit remains required before merge.
- Hosted installation-context run `39901162463` passed both Linux and Windows
  on initial head `5344136a7da575fbae82eedb7530b5f21d74f2a3`. Retain the result
  as source integration evidence and rerun the branch lane after the real
  test-preparation fix; initial-head evidence is not current-head acceptance.
- Test-preparation fix head `1f90e7dd7f6bc08acdd29898ec0d555e412cf1cd`
  passed the actual required source CI, including the unchanged full dependency
  audit, and both hosted acceptance jobs in run `39901930511`. Reviewer fixes
  below change the head again and retain their own review/CI/acceptance gate.
- Initial review `5623963359` identified three valid issues: final-row
  uncertainty starving pending reads, uncertain terminal ACK/cancel results
  failing to stop that cycle, and duplicate transitions accepted before an
  unrecoverable suspension. Fix all three with four regression cases; keep
  local callback validation unchanged. The 110-case replay/wire/recovery
  selection passes after these fixes. Current-head review, required CI and
  hosted acceptance must still pass before merge.
- Documentation/architecture: this closes the finite-consumer part of the
  reviewed PR service contract without claiming its lifecycle. The optional
  embedding still owns stable authenticated scope, dedicated remote pending
  view, admission, cadence/backoff and graceful shutdown. Same-checkout test
  preparation and the existing manual Linux/Windows selector exercise the new
  modules; no new CI job or production capacity is added.
- Performance: no consumer subprocess, sleeper, per-task thread, tree/history
  scan or installation. Indexed queue work is bounded by the page and each
  cycle performs at most 82 finite SDK calls, including capability, receipt,
  pending and settlement work. A worst-case 32-row/16-event test asserts exactly
  82 calls. An indexed local-only recovery query excludes remote rows before
  projection; its query plan is also asserted. Deterministic work counts and native isolated
  wire behavior are exercised, not representative fleet p95/CPU qualification.
  No installer/lifecycle code changed; isolated fresh disk/dependency
  preparation covers the callable's first touch, not a full service clean room.
- The parent remains Active. Shared scheduling, independently installable role,
  production-provider validation, released/authorized installed proof and the
  complete fleet/native-client/migration/reviewer matrix remain open. No
  runtime update, credential provisioning, activation, new placement or
  worktree finalization occurred.

### 2026-10-22 - Merge typed replay consumption and continue the shared coordinator

- #8072 merged as `0a68c8e3a127be8af4393aa79dfed25f22fa783e` at
  `2026-10-23T03:20:45Z`, from published head
  `523ca0f3394093fdefeed95d105f7ae2ebe47685`. Current-head Copilot review
  `5623493205` approved with no open issues. Read its complete body and the
  complete empty thread page; every required check passed. Managed completion
  used only the documented additional-CODEOWNER exception, not a CI or
  feedback bypass. #7188 remains open.
- Read the full parent Plan/Validation after merge and close only the finite
  typed-client source item. The 205-case replay/client/Gateway selection and
  all source guards are not production-provider or installed fleet proof.
  Retain every presentation/migration, credential, installation, lifecycle
  and fleet acceptance item.
- Next: connect the shared Dispatch consumer through the existing durable
  prepare/arm/finish transactions. Persist the original request/issued/deadline
  with its task generation, reconcile setup uncertainty, and ACK an exact event
  only after durable generation-fenced completion. Remote registrations must
  not be judged missing by the local callback-owner probe or arm a process
  fallback on transport loss.
- Managed post-merge reconciliation preserved the published head in its backup
  and removed only the already-merged commit. The parent remains Active; no
  worktree finalization, runtime update, credentials, activation or new capacity.

### 2026-10-22 - Prepare the finite typed PR replay client

- Added the optional PR-owned `ReplayClient` over Fleet's finite `ServiceCaller`.
  Each operation retains the explicit trusted route/alias and expected repository,
  makes one SDK call and validates bounded JSON, capabilities, receipts or native
  pending events. Immutable original request identity is supplied by the consumer,
  not generated or persisted by this client. No retry/poller, callback process,
  legacy fallback, provider implementation or default-install dependency was added.
- Typed receipts and outcomes retain exact registration/event IDs; pending payload
  copies preserve the native notification. Completion verifies the returned
  generation/event. Consumers still own durable intent, current task-generation
  matching and idempotent effects before ACK.
- The first real TLS run passed 63 cases and failed both policy-withdrawal
  cases: the Gateway correctly returned 403 after dispatch, but the client had
  incorrectly treated only 5xx as uncertain. Fixed that class for all unsuccessful
  HTTP mutation responses. Revocation, channel loss and caller cancellation now
  preserve original-ID reconciliation both before lookup and after commit starts;
  no negative acknowledgement, rollback claim or replacement registration.
- Validation: 205 selected replay/client/store/Gateway cases passed in 38.03s,
  including seven new actual TLS/client cases. Fatal Python lint, whitespace and
  the 13-plugin install-contract check passed. Source integration uses isolated
  first-provisioned disk state and a synthetic issuer, not production credentials.
  No installer/bootstrap/lifecycle change requires a new clean-room installation
  lane; installed-service and live fleet proof remain explicitly open and no
  production rollout or capacity is authorized by this source change.
- Documentation-impact review: updated the existing replay Gateway reference and
  plugin README for the optional API, typed outcomes, limits, original-intent
  recovery and 403 uncertainty. No CLI/help/config/installer/activation contract
  changes. This closes part of the existing PR/fleet vision and respects optional
  composition, shared native validation and existing transport/lifecycle ownership.
- Performance impact: one SDK invocation per explicit consumer operation, with its
  existing two HTTPS exchanges and current-token/signature work; zero added
  subprocesses, retry loops or persistent sessions. Responses are at most 24 KiB
  and pending returns at most 16 events. Unit assertions check those exact work
  counts/bounds. The real TLS suite measures source integration, not qualified
  native fleet p95/CPU; the eventual scheduled consumer owns representative
  frequency/cardinality measurements before deployed acceptance.
- Next: reviewed source publication, then the shared Dispatch consumer through its
  existing durable prepare/arm/finish fences and independently installable service
  lifecycle. Parent credential/provider, release, authorized installed proof,
  presentation/migration and fleet acceptance remain open.

### 2026-10-22 - Merge the finite Gateway caller and continue replay consumption

- #8059 merged as `6e327f495f283406529782c2df064316f2e47965` at
  `2026-10-23T02:48:00Z`, from published head
  `688976106d8dc81fd0b5740bce8b9a850f15cc6e`. Current-head Copilot review
  `5623315206` approved with no open issues. Its full body and complete empty
  thread page were read; every required check passed. Managed completion used
  only the documented additional-CODEOWNER review exception, not a CI or
  feedback bypass. #7188 remains open.
- Re-read the full current parent Plan/Validation after merge and close only
  this bounded shared source prerequisite. The 167-case combined selection,
  final one-case fresh challenge/token count and two-case HEAD/304 checks are
  source/synthetic TLS proof, not production-provider or fleet qualification.
- Next: add the PR-owned typed replay client over this caller and connect the
  shared Dispatch consumer through its existing durable prepare/arm/finish
  fences. Preserve original request timestamp/deadline, exact registration/event
  identity, replay after response loss, and ACK only after generation-fenced
  durable completion. No per-task waiter process or duplicate task registry.
- Managed post-merge reconciliation retained a backup and removed only the
  already-merged source commit. The worktree remains bound to this Active
  parent; no finalization, runtime update, production credential acquisition,
  remote activation or new capacity follows from source publication.

### 2026-10-22 - Prepare the finite service-caller transport

- Added Fleet's finite `ServiceCaller` SDK under the existing optional
  `service-gateway` extra, reusing its TLS, PoP and wire bounds. The embedding
  supplies the exact canonical Gateway origin, current async access-token
  callback, proof key and optional CA. There is no credential issuer, ambient
  login, endpoint discovery, retry scheduler or runtime activation.
- Each call owns a bounded HTTP session, obtains a fresh challenge/current
  token, and returns an immutable finite response. Policy withdrawal is an
  ordinary failed request; cancellation/transport loss cannot establish remote
  nonacceptance. A transmitted mutation with an unusable response reports
  uncertainty for original-ID reconciliation, not replacement or rollback.
- Exact real-TLS refusal tests caught aiohttp retrying a disconnected GET even
  on a fresh force-closed connection. The SDK now explicitly disables automatic
  retries, following the existing connector's idiom. It also validates the
  nonce before credential acquisition. The contained Windows caller, transport
  and policy selection passes 167 tests; the new caller cases include actual
  PoP, method/query binding, current credentials, policy withdrawal/recovery,
  malformed challenge, redirect/encoding/stream-size refusal, cancellation,
  bounded concurrency and one-attempt GET/POST/PUT/DELETE disconnects.
- Final focused checks also confirm one challenge/token callback per operation
  and bodyless HEAD/304 responses whose representation `Content-Length` exceeds
  the finite body cap (one plus two passing cases). Fatal lint, the 13-plugin
  install-contract gate and 13-adopter headless-launch guard pass.
- This bounded shared-module source contribution remains subject to current-head
  review and required CI. The PR-owned replay client, shared Dispatch consumer,
  installable lifecycle, production-provider credentials, release/authorized
  installed proof and actual fleet acceptance remain open. The parent is Active;
  no rollout or production authentication occurred.

### 2026-10-22 - Merge scoped observation and continue shared consumption

- #8028 merged as `e53d8d24e00fc6a4ba82003273398ab680b44347` at
  `2026-10-23T01:55:34Z`, from published head
  `6aa0aa4504577d9032f954caeb9808a40f127d25`. Current-head Copilot review
  `5623018366` approved with no open issues. The full review body and all thread
  pages were read; the event-size thread was resolved, the body-only stale-target
  finding was fixed, and every required check passed. Managed completion used
  only the documented additional-CODEOWNER review exception, not a CI or
  feedback bypass. The parent #7188 remains open.
- Re-read the entire current parent Plan and Validation after merge. Closed
  only the finite native replay-observation source item. Final validation
  passes 275 cases with ten existing platform skips, including real
  native/Fleet-CLI/TLS credentials through durable replay and restarted consumer
  ACK. Production credential validation, installation, lifecycle, client/matrix,
  shared Dispatch consumption and actual fleet acceptance remain open.
- Next: connect the shared consumer through the real service profile and
  existing Dispatch prepare/arm/finish fences. The current static Gateway client
  does not implement that service-caller transport; contribute the missing
  bounded shared seam in its owner rather than use index routes or a second
  authentication scheme. Preserve original request/deadline and exact
  registration/event identity across reconnect and completion.
- Managed post-merge pull-forward retained a backup and removed only the three
  redundant already-published commits. The worktree remains bound to this
  Active parent; no finalization, runtime update, production credential
  acquisition, remote activation or new capacity is authorized by the merge.

### 2026-10-22 - Connect replay groups to finite native observation

- Added a read-only eligibility refresh before each native read: an earlier
  read's timeout sweep or concurrent completion cannot leave a stale target
  consuming provider/credential work ahead of another live target. The original
  captured identity set remains the fence, including cancellation plus a new
  registration for the same PR. This adds a short reload, not a held I/O lock.
  The final focused selection passes 275 cases with ten existing platform skips.
- The first #8028 pass identified valid native snapshots larger than replay's
  delivery budget as a per-target failure, not an owner failure. A reproduced
  two-target regression initially failed at the size refusal. A distinct
  event-size exception now leaves that watch intact, continues deadline sweeps
  and later targets across repeated cycles, while authority, corrupt state and
  commit failures remain fatal. The corrected focused selection passes 273
  cases with ten existing platform skips; current-head review and CI remain required.
- Added trusted immutable account/repository/native-command bindings and one
  owner-invoked observation cycle, not another poller, service, forge client or
  fleet controller. The embedding lifecycle retains scheduling, ownership,
  credentials, expiry/disconnect checkpoints and drain. No registration or
  Gateway operation accepts provider argv or credential selectors.
- Reused complete-envelope disk transactions for target enumeration, application
  and timeout sweeps. Native reads occur outside disk custody. Exact pre-read
  registration IDs prevent an older result from reaching a concurrently added
  wait; reload preserves concurrent cancellation and pending event/ACK identity.
  Different account/repository scopes never share reads or fallback credentials.
- Contained validation passes 270 focused cases, with ten existing POSIX-only
  skips on Windows. This includes actual native subprocess/coalesced disk state,
  a restarted consumer's exact event/ACK, provider failure/deadline handling,
  revocation-as-disconnect, post-commit uncertainty and the complete synthetic
  native/Fleet-CLI/TLS credential exchange through durable replay observation.
  Ruff, all 13 install contracts and all 13 headless-launch adopter checks pass.
- Clean-room installer and live external-venue lanes are not exercised: this
  slice adds no installer, first-provision or service activation path, and no
  production credentials or deployment are authorized. The independently
  installable lifecycle, production provider verification, shared Dispatch
  consumer, release/authorized rollout and real fleet acceptance remain open.
  Review, required CI and a source merge are still pending for this slice.

### 2026-10-22 - Merge the typed native process adapter and retain observation integration

- #7984 merged as `5c7406f0750d727c6336684073047ba1dd963b3a` at
  `2026-10-23T00:46:00Z`, from published head
  `197a8bcb2a36b4f19b441b9eeb50bc959639e54a`. Current-head Copilot review
  `5622635624` approved with no open issues. The batch-boundary thread was
  resolved and every required check passed. Managed completion used only the
  documented additional-CODEOWNER review exception, not a CI or feedback bypass.
- Re-read the entire current parent Plan and Validation after merge. Closed
  only the bounded native-command credential-adapter source item. Preserved
  all client/matrix, installed adoption, lifecycle, provider and fleet gates.
  The final focused selection passes 264 cases, including the complete
  PR/native/Fleet-CLI/TLS credential exchange; ten existing POSIX controls
  skip on Windows. This is synthetic source proof, not deployment.
- Next: connect authorized replay observation groups to the native fetch
  adapter, preserving account-scoped coalescing, durable original deadlines,
  pending event/ACK identities and no ambient fallback. Then complete the
  shared Dispatch consumer and independently installable lifecycle through
  existing owners' public interfaces. Production provider verification,
  release/authorized installation and actual fleet acceptance remain open.
- No runtime update, production credential acquisition, remote activation,
  new capacity or worktree finalization follows from this source merge.

### 2026-10-22 - Connect typed credentials across the native command boundary

- Implemented an explicit typed `credential_provider` over the existing worker
  policy and a machine-only Fleet `credential` command. It retains exact
  source/account/forge selection, recipient verification and ACK before
  returning scoped material; the string-only index loader still refuses
  forge policies.
- Worktrees' snapshot-only `--credential-command` adapter captures a versioned
  material envelope at each native acquisition, with the canonical finite
  caller-owned process/stream budgets and no Fleet import, token argument,
  cache or ambient fallback. PR's existing native bridge forwards an explicit
  trusted command; no registration can supply it and no runtime is activated.
- Added real native snapshot-child and full PR/native/Fleet-CLI/TLS credential
  exchange tests. They exercise fresh material on every command, exact
  scope/expiry validation, loss before a later forge request, sealed delivery
  ACK and ordinary bridge compatibility. A full-chain run exposed the
  synthetic controller fixture polling above its own principal rate budget;
  pacing that fixture at 150 ms preserves real admission rather than waiving
  throttling.
- Initial native tests needed short parameter IDs for Windows' environment
  size limit and an explicit configuration anchor. Fleet's prepared test
  package was stale and was restored through its contained preparation path.
  An overly broad `test_cli` selector included unrelated native client
  construction and reached the 240-second aggregate limit; the exact
  credential/base-CLI module selection passes without relaxing deadlines.
- Final focused validation passes 264 cases: 99 Worktrees credential/configuration,
  63 Fleet typed/legacy worker and base CLI, and 102 PR native/transport cases.
  Ten existing POSIX process controls skip on Windows. The real complete
  process/TLS path is included, not just an injected credential callback.
  Python lint and the 13-plugin install contract pass.
- #7984 review identified an outer Windows batch-shim boundary before native
  helper validation. Credential-backed PR bridge reads now refuse `.cmd`/`.bat`
  in both direct command and explicit prefix forms. Normal unselected launches
  stay unchanged; both cases and uppercase suffixes have refusal/compatibility
  tests, while the complete native/Fleet/TLS exchange still passes.
- This closes no production or deployment gate. The credential account/action
  contract, actual provider verification, service observation/consumer/lifecycle,
  installed acceptance and real fleet acceptance remain Active. Final focused
  review and source merge for this adapter are still pending.

### 2026-10-22 - Merge the native request boundary and retain process integration

- #7954 merged as `3b870884d307bd86e4d6b027eb5b199ca13a3d13` at
  `2026-10-22T21:50:48Z`, from published head
  `310da9cb00237534c390c24ce29a3ad899c7fd5a`. Current-head Copilot review
  `5621280923` approved with no open issues. All four review threads were
  resolved, the full review-body default-port finding was fixed, and required
  CI passed. Managed completion used only the documented missing-additional-
  CODEOWNER review exception, not a CI or feedback bypass.
- Re-read the full current parent Plan and Validation after merge, preserving
  every retained client/matrix, lifecycle, installation and fleet gate. Closed
  only the additive native library source child. The parent credential and
  consumer-contract items remain open; provider labels and isolated TLS/CLI
  controls are not production downscoping or deployed acceptance.
- Next: connect the PR service's existing native-command process boundary to
  the existing typed credential worker without a second forge client, secret
  persistence or ambient fallback. Then complete the shared dispatch consumer
  and independently installable lifecycle. This merge authorizes no runtime
  update, production credential use, remote activation or new capacity.

### 2026-10-22 - Prepare native per-request observation credential consumption

- Added a read-only structural credential-source seam at the existing
  configured observation/fetch boundary. It accepts the typed exchange material
  without a Fleet import and checks exact provider/API/repository/observe/auth
  binding and expiry immediately before every native request. GitHub checks
  and reviews renew on each page; ADO preserves PAT versus bearer and renews
  on each HTTP request. Source or authorization loss stops later reads without
  ambient-account fallback. No mutation interface accepts the source.
- Kept normal string/command-backed/ambient CLI behavior. Extracted the existing
  GitHub review-page reader to keep its provider within the shrink-only module
  budget; this is not a parallel forge implementation.
- Contained focused/legacy validation passed: the earlier 518-case observation/
  provider packet, then 395 cases after the review-reader extraction and
  additional auth-loss controls. The latter includes 42 targeted credential
  cases, with an actual subprocess and isolated TLS continuation/stop controls.
  Fatal lint, headless launch and the 13-plugin install contract passed.
  These are synthetic-source/native-boundary proofs, not actual provider
  downscoping or a running Fleet deployment. Fresh-machine installation is
  unchanged; production credentials, remote activation and rollout are outside
  this source-only slice.
- Review of #7954 caught two concrete integration gaps: the exchange's canonical
  ADO PAT authentication discriminator is `basic`, not a new consumer-specific
  label, and Windows ARM64 must collect subprocess controls without the optional
  cryptography package. Corrected the consumer/fixtures/docs, added refusal of
  the noncanonical label, and moved TLS-only imports behind a per-fixture
  optional dependency check. All 44 focused cases passed, including a real
  child process that blocks cryptography imports while collecting the module.
- The next review identified inherited GitHub CLI `api_host` routing and its
  tenant/development-host endpoint rules. Pinned every selected-source request
  to its validated absolute HTTPS API endpoint, added tenancy routing, and
  refused the HTTP-only development host before acquisition. All 51 focused
  controls passed. A real CLI with an isolated non-forwarding proxy proved a
  relative positive control used the configured alternate destination while
  the scoped request used only its validated endpoint; no forge was contacted.
- The full third-round review body also identified explicit default HTTPS
  ports, despite reporting no open threads. Normalized API routing by parsed
  hostname, omitted `:443`, and retained nondefault API ports. Actual CLI
  controls additionally exposed that `--hostname` rejects a port-qualified
  hostname; the source now passes its canonical hostname separately from the
  absolute API authority. The resulting 83-case focused/ordinary snapshot
  selection passed, including real public/tenant `:443` proxy controls.
  With added Enterprise Server default/nondefault-port controls, the final
  selection passed all 85 cases (57 focused credential cases).
- The source child remains open pending normal review/merge. The native
  process-boundary credential loader/PR-service adapter is still unimplemented;
  do not mark the parent credential, consumer-contract or fleet acceptance
  items complete based on this additive library seam.

### 2026-10-22 - Merge the typed PR-worker exchange and retain native renewal

- #7926 merged as `cb969b1df3cd230d42fc69c07d83e929b4723c0e` at
  `2026-10-22T19:49:28Z`, from published head
  `08b5db0449f450c0ac79ffc7fd1b9c8ae5a6bd40`. Current-head Copilot review
  `5619946737` approved with no open issues; its one test-validity finding
  was corrected with fresh-client negative and positive controls. Required
  checks were green. Managed completion used only the documented
  missing-additional-CODEOWNER review exception, not a CI bypass.
- Re-read the full current parent Plan and Validation after merge, preserving
  all retained client/matrix and lifecycle/installation/fleet acceptance.
  Closed only the typed credential-exchange source item. The parent and the
  consumer-contract credential item remain Active/open.
- Next: carry verified typed material into the existing native PR driver's
  actual HTTP/CLI request boundary, renewing without ambient fallback in
  explicit service mode and preserving ordinary CLI behavior. Then close
  the typed service/dispatch consumer and independently installable lifecycle.
  This source merge proves neither production-provider permissions nor an
  installed/running deployment and authorizes no rollout.

### 2026-10-22 - Bind PR observation credentials in the existing exchange

- Added a distinct `pr-worker` credential role to the existing Gateway
  broker/controller/worker exchange. It has no index corpus or index-query
  authority. Exact provider/API-base/repository/observation/authentication
  scope participates in rule selection, mandatory external provider
  verification, signed request correlation and recipient-sealed material.
- The recipient checks that typed scope before ACK. The legacy string-only
  index token loader refuses it instead of erasing the target or confusing
  ADO bearer material with a PAT. No issuer, broker, supervisor or forge
  adapter was added; no runtime, credentials or placement were activated.
- Validation: 464 cases pass across the new PR-credential tests and existing
  Gateway worker/runtime/auth/fence/identity/process/manifest contracts.
  Real isolated TLS/PoP/controller/broker/JWE/ACK calls prove fresh acquisition,
  exact target and provider-result refusal, restart-policy revocation and
  rejection before ACK of a wrongly scoped but validly signed sealed reply.
  Fatal Python lint and all 13 install contracts pass.
- Review identified that a retained pre-restart client could refuse due to
  stale nonce/epoch rather than revocation. The corrected regression creates
  a fresh client with the same principal and keys after restart, proving both
  successful acquisition without revocation and refusal without provider use
  when revoked. All 47 PR-credential cases pass after this test-only correction.
- No installer/bootstrap path changed, so a new clean-room installation lane
  is not applicable. No live provider, production credentials or external
  deployment lane was exercised: this source-only SDK exchange has no
  activated PR consumer, and source publication grants none of those changes.
- This closes a source seam toward the PR vision's
  `explicit-backends-and-credential-authority` and
  `one-operator-with-separated-scopes` behaviors, following the existing
  optional-integration and explicit-failure patterns. It does not close
  the consumer-contract credential checkbox: native provider HTTP/CLI
  effect-boundary renewal and actual production-provider validation remain.
- The trace found that Worktrees captures GitHub credentials when building
  a fetcher and ADO headers once per multi-request observation. The next
  slice must reuse those native drivers while obtaining typed credentials
  at each actual authenticated request, preserving explicit native CLI
  behavior and refusing any service-side ambient fallback.

### 2026-10-22 - Merge the reviewed disconnect boundary and retain service follow-through

- #7915 merged as `d22e6e32f310a4a707cb6239fd89ea9d4f592ee2` at
  `2026-10-22T18:36:19Z`, from published head
  `359ec8692d92ef71d9ad2aaada1e0505a936a1c8`. Current-head Copilot review
  `5619049618` approved with no open issues; its one prior finding was corrected
  and resolved, and required checks were green. Managed completion used the
  documented missing-additional-CODEOWNER exception, not a CI bypass.
- The final 402-case Gateway/PR selection proves the real isolated serving
  path, known-disconnect lookup/commit/result checkpoints, independent lifetimes,
  timeout-before-cleanup ordering, retained disk custody and original-ID recovery.
  This is source/synthetic-identity proof, not production credential, container
  lifecycle, installed runtime or live fleet acceptance.
- Re-read the complete parent Plan and Validation after merge, preserving all
  retained #2400/#2700 intake and its bounded ownership. Closed only this source
  disconnect slice and its matching validation. The effort remains Active.
- Next: deliver the PR worker's real account/repository/action credential
  binding through existing provider/Gateway owners, then the typed shared
  service consumer and independently installable lifecycle. The static
  credential profile currently admits only index workers; PR must not
  impersonate that identity, invent a corpus, or add another issuer/controller.
  Source merge authorizes no runtime update, credential acquisition or placement.

### 2026-10-22 - Complete retained acceptance from #2700

- Retained the complete #2700 scope directly in the Plan and Validation,
  without declaring feature completion or transferring source/resource custody.
  Normal reviewed merge and current full readback precede administrative
  supersession; all substantive acceptance remains unfinished.

### 2026-10-22 - Complete retained acceptance from #2400

- Retained the full #2400 remainder directly in the Plan and Validation,
  preserving delivered partials, existing source/resource custodians and gates.
  Reviewed actual merge and current full readback precede administrative
  supersession; no capability, native trial or adoption is declared complete.

### 2026-10-22 - Treat service revocation as request disconnect

- The operator clarified that revocation has ordinary network-disconnect
  semantics, superseding a distributed revocation barrier. This closes the
  design choice without changing the separate Gateway/service topology or
  authorizing a listener, credential, placement or runtime rollout.
- The connector supplies one local, non-wire request lifetime, closed before
  cancellation/channel retirement and checked by the handler before response
  emission. The PR adapter passes that same signal into its existing complete
  replay transaction and retains shielded disk custody after disconnect.
- Lookup, commit and result checkpoints refuse already-known disconnected work.
  A write already started when withdrawal arrives may commit; response loss
  never proves absence, resets the original deadline or permits a second wait.
- The initial focused source run passes 106 cases; the complete affected
  Gateway-profile and PR replay selection passes 400 cases. Real TLS/PoP/channel
  tests withdraw policy and disconnect before lookup and inside the atomic
  writer. They prove publication proceeds while the write is held and original-ID
  reconciliation after reauthorization/reconnect. Review/current publication
  gates remain pending; no production acceptance is claimed.
- Review found that `wait_for()` joins a handler's cancellation cleanup before
  raising its timeout. The connector now closes request lifetime before
  cancelling and joining its separately owned response task. Deadline and
  external-cancellation regressions inspect lifetime from inside held cleanup
  and prove that the connector still joins it. The affected portfolio passes
  402 cases after this correction.

### 2026-10-22 - Merge the native client slice and continue the service contract

- #7850 merged as `a750ccc3b446f7dea85f297143a61b48b2a0bb4d` at
  `2026-10-22T11:14:01Z`. The final published head
  `79901927c324a4d6bbd7012168085ac6c19ead46` received current-head
  Copilot approval with no open issues; all six review threads were resolved
  and every required check was green. The managed merge used the documented
  Maintainer exception for the additional CODEOWNER vote, not a CI bypass.
- Current focused source contracts passed: 149 tests, with 10 POSIX-only
  process-lifetime cases skipped on Windows. The real Windows boundaries
  include all four commands through final Python, separate consumer reparsing,
  empty/quote/multiline/Unicode preservation and old-host refusal before effects.
  Fatal lint, module-size and install-contract checks passed.
- A PR-description edit temporarily removed its source marker. The attribution
  check caught the actual missing metadata; the description was repaired using
  the managed preparation command and its assigned public codename. Current
  required attribution passed before merge; no dummy commit or bypass was used.
- Re-read the complete Plan and Validation Plan after merge. Only the bounded
  native client slice is closed. Promotion, authorized installed proof, actual
  forge/credential parity and all fleet acceptance remain open; no running
  runtime, credential, daemon or fleet placement changed.
- Next: close the PR service's current policy/enrollment and effect-side
  admission fence through the actual serving boundary, reusing the fleet-owned
  contracts rather than treating the replay store's transaction lock or a
  caller-context SDK as deployment/effect authority. Credentials, the shared
  dispatch consumer, independently installable lifecycle and live acceptance
  retain their own subsequent gates. This effort remains Active.

### 2026-10-22 - Reuse central native PR operations without a second provider

- Added an explicitly selected `--worktrees-command` path for the PR plugin's
  existing status, create, merge and wait verbs. The default GitHub interface
  remains unchanged. Native reads reuse the validated snapshot contract and
  preserve ADO votes, approval events and human/policy gates independently.
- Native create calls central `pr-create --from-branch`, retaining target
  registration, publication checks, attribution, idempotent reuse and claimant
  journaling. Native merge calls `pr-merge --now`; role refusal and pending
  native auto-completion remain central decisions, not client-side overrides.
  Flags the central CLI cannot honor fail before mutation.
- Added bounded caller-owned command invocation, including a real Windows
  PowerShell argument-data boundary for multiline bodies, quotes and Unicode.
  Missing or incompatible commands and uncertain mutation results never retry
  through the GitHub adapter.
- Validation: focused CLI/watch contracts passed, including all four real
  front-door command boundaries and legacy GitHub regressions. The new behavior
  does not alter installation or first-provisioning paths, so a new clean-room
  scenario is not applicable. No synthetic configured, authorized live ADO
  target is available to this slice; real forge and fleet acceptance remain
  open rather than inferred from controlled protocol peers.
- This narrows `first-class-provider-neutral-pr-surface` under the PR vision
  and composes the existing independent-installation, attributable-command and
  fail-loud patterns. It adds no daemon, credential store, forge adapter or
  deployment authority. Source publication/review remains pending; the parent
  effort is Active.
- The first #7850 review found argument loss at the final Python call under
  Windows PowerShell 5.1, empty selectors selecting GitHub, an explicit squash
  request indistinguishable from the default, stale hints blocking an available
  same-cell peer, and batch shims unsupported by direct process creation.
  The correction requires PowerShell 7.3+ standard argument passing, rejects
  batch shims and empty selectors, records explicit method selection, and
  resolves the validated peer before considering any legacy hint. Native
  regressions now cross the final Python call and prove old-host refusal
  before target execution; no installed runtime or fleet capacity was changed.
- The follow-up review found that an empty pushed-head operand could select
  the central local-publication path, and option-like body/title text could
  be reparsed as flags. Native create now rejects blank heads before spawning
  and attaches all forwarded option values with `=`. Regressions distinguish
  arrival at Python from reparsing those values at the consumer boundary.

### 2026-10-22 - Bind admitted PR callers to durable replay operations

- #7832 merged as `ea2a6b8408f3271760d4c544faefb2815af38cac` after
  current-head Copilot approval, resolution of the deadline finding and all
  required CI checks. This is source delivery, not promotion or rollout.
- Added the optional PR-owned `WatchGateway` source adapter around the merged
  real connector SDK. Trusted configuration maps the admitted
  fleet/Gateway/slot/principal and a target alias to `ConsumerScope`; request
  bodies cannot nominate consumer/account/repository identity or callbacks.
  The versioned bounded operations cover capabilities, registration,
  inspection, pending reads, exact ACK and cancellation.
- Added explicit first provision and complete-envelope disk transactions in
  `ReplayStore`, using the existing process/thread-owned `OperationLock` for
  each load/use/atomic commit. Missing, corrupt or foreign-namespace state is
  not empty state. There is no cached core between transactions. This is
  local transaction exclusion, not active placement, shared-volume,
  enrollment-revocation or installation/cutover authority.
- Bounded disk offload keeps the actual connector loop responsive. Cancelled
  callers leave their transactions tracked; explicit drain stops admission
  and waits, or visibly refuses safe retirement on timeout.
- Focused synthetic validation exercises the real TLS/PoP/WebSocket Gateway
  and PR operations, subject isolation, forged labels, original-ID
  reconciliation, adapter/connector replacement, ACK loss/retry and caller
  withdrawal. Separate real-child checks exercise disk contention/replay.
  Existing replay/registry/native-delivery contracts stay selected.
- Reused the existing bounded plugin-test runner's explicit same-checkout Fleet
  dependency preparation. Fleet's linked standalone build resource requires
  retaining canonical `libs/versioned-runtime`, as the index preparation
  already does; neither dependency seasoning nor containment was bypassed.
- Current-base integration also exposed a real shared retained-input overlap:
  Fleet's declared package resource created a partial parent directory, so
  project retention skipped that parent's metadata. The canonical policy now
  retains complete projects before merging resources. Editable/noneditable
  overlap regressions and a fresh real PR/Fleet preparation cover this
  prerequisite; no local runtime was updated.
- Documentation impact: updated the plugin README, replay and Gateway
  references, architecture map and consumer-contract availability. This
  closes part of the PR vision's scoped durable-delivery behavior under the
  independent-installation/optional-composition patterns; it adds no new
  daemon, listener, installer, forge provider or deployment.
- Full contract checkboxes remain open: current enrollment/effect fencing,
  authorized forge observation, the shared Dispatch consumer, lifecycle/
  placement integration, release, authorized installation and actual fleet
  acceptance are not established by these synthetic serving tests.
- #7832 review found an integer deadline that was accepted exactly in the
  replay record but rounded by registry restoration, making the next complete
  envelope unreadable. Timestamp validation now rejects precision loss and
  overflow before persistence in the core, not only in the wire adapter.
  Reload and admitted-caller regressions retain another scope's existing
  receipt, and the actual TLS flow also refuses the input without a write.
  The focused replay/store/handler/transport/native contracts pass 229 cases.
- Re-read the parent Plan and Validation Plan after merge. Only the optional
  scoped source adapter item closes; installed/deployed credentials,
  enrollment/effect fencing, observation, shared consumer and full fleet
  acceptance remain open. The next source slice reuses the existing central
  ADO `pr-*` provider/contracts in `agent-pull-requests`, rather than adding
  another forge adapter or weakening worktree publication policy.

### 2026-10-22 - Carry authenticated callers into a bounded service handler

- #7810 merged as `94718b751eb6c0d41b9555c1baae905594283fb4` after
  exact-head approving review, the resolved expiry-isolation finding and all
  required checks. This confirms source delivery, not release or deployment.
- Traced the actual caller admission, owner-retained grant, Gateway request
  frame and fixed HTTP backend path. The missing subject handoff is now an
  explicit `caller-context-v1` admission/attachment negotiation, not an
  identity-header passthrough or a serialized offline permit.
- The optional in-process connector handler receives immutable
  fleet/Gateway/slot/principal and bounded-expiry context from the admitted
  channel. It is mutually exclusive with the existing HTTP backend and limits
  replies to 32 KiB using the existing chunk/ACK and cancellation path.
  Legacy frame shapes and proxy defaults remain unchanged.
- Real TLS/PoP/WebSocket tests cover caller-header/body/query spoofing,
  separate admitted subjects, owner mismatch, withdrawal cancellation,
  strict profile/context parsing, bounded replies and value-free handler
  failures. The existing Gateway service portfolio also passes on the
  supported dependency floor.
- #7810 review found that context expiry during transit could escape the
  per-request task and terminate the shared channel. Expiry now refuses
  inside that task before invoking the handler; malformed/wrong-owner
  context remains channel-fatal. Real concurrent GET/POST regressions retain
  another caller's valid in-flight response and subsequent channel use.
  The current-source Gateway service portfolio passes all 258 cases;
  mutations retain their existing conservative `outcome-unknown` semantics.
- Fresh test provisioning exposed the existing unseasoned `aiohttp>=3.14.5`
  floor. The Gateway's disabled-compression path supports the seasoned 3.14.4
  release; both declared extras now admit it without bypassing the seven-day
  policy. All 24 Fleet Python dependency declarations pass the live age
  checks and the fresh environment uses the policy-resolved graph.
- This is a source SDK seam, not PR-service activation or fleet acceptance.
  The PR embedding must still bind each admitted subject to configured
  account/repository permissions, persistent namespace/writer custody and
  scoped forge credentials, then deliver the real operations and dispatch
  consumer. All full-contract, installed and deployment items remain open.
  No service, external identity or machine runtime was changed.

### 2026-10-21 - Build the bounded PR-owned replay transaction core

- #7786 merged as `fea868b178df6fe700f7a87ebe771c80e2a75077` after
  exact-head approval, both resolved original review threads and green required
  CI. Final focused coverage is 245 passing tests: 58 replay, 103 existing
  registry/delivery and 84 native-observation cases; 10 existing Linux-only
  process cases skip on Windows. This confirms source merge, not promotion,
  rollout or serving authority.
- Implemented internal `WatchReplay` request reconciliation, account-separated
  observation groups, consumer-only bounded reads, exact ACK/cancel identities
  and compact completed receipts using the existing `WatchRegistry`. Original
  request timestamps/deadlines survive retries; expired/unknown outcomes report
  uncertainty. Active events are not evicted to admit another request.
- The embedding owner must provide authenticated scope, one active writer and
  atomic persistence of the complete envelope. This source slice does not
  publish a CLI capability, Gateway route, service role or coordinator consumer.
  Pull events cannot enter the native callback scheduler, and no remote callback
  command or URL is introduced.
- Focused isolated validation passes 138 tests across replay, registry and
  acknowledged-delivery suites, including real consumer-process restart before
  and after ACK, actual callback-scheduler separation, pre/post-replacement
  failures, retention/capacity/batch bounds and foreign/stale identities.
  Fatal Python lint and install-contract checks pass.
- The first #7786 review identified injected-clock deadline reconstruction and
  omitted rich fingerprint baselines at the transaction/restore boundary.
  Rebased restored monotonic deadlines on the supplied clock and persisted a
  validated pull baseline, with deterministic before/at-deadline checks and
  all rich transitions exercised across staging and restart. Native callback
  baselines remain on their existing serialization contract.
- The follow-up review identified quadratic scope reconstruction at the
  supported 1,024-repository capacity. Indexed active records once per restore;
  one-group and 1,024-group regressions bound scope-validation calls linearly
  across restore and a subsequent staged mutation.
- Documentation impact: updated the PR-plugin README and added the authoritative
  [internal replay contract](../../../plugins/agent-pull-requests/docs/watch-replay.md).
  This realizes part of the PR vision's durable delivery/scope behavior under
  the existing independent-installation and one-writer patterns; it changes
  neither standing intent nor native CLI configuration.
- Validation boundaries: these are domain, persistence and process checks.
  Fresh host provisioning is unchanged; the clean-room install lane is not
  applicable to this library. Authenticated serving, Gateway/container,
  credential renewal, dispatch generation fencing through that remote path and
  installed lifecycle remain unimplemented acceptance, not inferred from these
  tests. No shared runtime or deployment is changed or authorized.
- Next: connect the merged core to the actual scoped serving and consumer
  boundary. The five contract-delivery items remain unchecked;
  this groundwork alone does not satisfy any full-stack proof or close the
  effort. The no-owner-signoff correction landed in #7778.

### 2026-10-21 - Remove the retained-owner approval deadlock

- The operator rejects retained-owner agreements as a source-work prerequisite.
  Supersedes the agreement-first next-step wording in the earlier reconciliation
  below: #7763 remains a valid inventory/release record, not permission to
  suspend implementation until another campaign acknowledges a request.
- The delegation driver owns concrete contract decisions, the consumer
  implementation and narrowly scoped shared-interface contributions through
  normal reviewed PRs. Existing owners retain their campaigns and live
  resources; notify them, inspect overlapping work and resolve actual conflicts,
  but absence of a separate reply is not a source-work veto.
- Replaced the five signature-style gates with a contract delivery checklist.
  None is marked complete: actual identity, policy/effect fencing, credential,
  replay/ACK and persistent lifecycle behavior must be implemented and proved.
  Required review, checks, single-operator isolation and no competing external
  PR remain intact. Missing APIs are implementation work, not assumed support.
- Next source slice: specify and implement bounded PR-owned request
  reconciliation and consumer-scoped pending-event/ACK behavior using the
  existing durable watch core, without remote callback execution. Then add the
  necessary authenticated Gateway/credential seams through reviewed source
  changes rather than waiting for an approval rendezvous.
- No shared runtime, credential, placement, migration or deployment is changed
  or authorized by this correction. Installed acceptance and explicitly
  authorized rollout remain separate open obligations; unrelated machine
  repairs retain their existing owner.

### 2026-10-21 - Record release and reconcile the available Gateway contract

- #7752 merged the post-delegation effort reconciliation. The canonical effort
  remains Active; that documentation merge does not complete another runtime
  or fleet requirement.
- #7751 promoted the delegation in successful Main-owned run `39718096362`.
  Release content pinned at `e247cd4429d8dd760eafdc488633b8ec680a8c7b`
  matches all 27 changed production files from #7714 after only canonical
  installer-engine vendoring rewrites and the verified human-output digest.
  Dispatch `0.18.41-dev1` and PR-owner `0.3.36-dev1` are released, pending
  authorized rollout; no installed or running-state proof follows from this.
- Reconciled the [consumer contract](pr-service-contract.md) to the available
  opt-in generic service Gateway rather than describing all Gateway access as
  index-specific. Its authenticated bounded transport is reusable, but stripped
  caller identity/credentials do not establish PR backend consumer/account
  authority or a PR worker's forge credential binding.
- Sent retained-owner requests on [#7188](https://github.com/ThomasMichon/copilot-extensions/issues/7188#issuecomment-6332334124)
  and [#6443](https://github.com/ThomasMichon/copilot-extensions/issues/6443#issuecomment-6332334793)
  for the trusted backend context, actual serving/admission/delegation/effect
  boundary, PR-worker account/repository/action and provider-result validation,
  renewal/revocation, and persistent namespace/single-writer lifecycle.
  No subsequent agreement was present at this reconciliation. All five
  consumer-contract agreement items remain open; a posted request is not an
  owner receipt or a reservation of another campaign's files.
- Next source gate is that retained-owner agreement, including the declared
  offer and request/response bounds plus request reconciliation and bounded
  replay/ACK semantics. Do not wire unsupported routes, impersonate an index
  role, treat an isolated permit as backend authority, or start a second
  controller. Installed credentials/adoption/governed lifecycle, explicitly
  authorized rollout and actual fleet/Gateway acceptance remain unresolved.

### 2026-10-21 - Merge fenced delegation and retain the parent completion gate

- #7714 squash-merged as `bfcfc62d301d6a5b5822e580ce8a2ee762fd8f34`.
  The actual current-head Copilot review `5608971607` approved
  `30337791ef211e35d0adc1495a15175dc973fab2` with no open issues. All seven
  review threads are resolved, and required checks passed. Completion used the
  normal repository-authorized Maintainer flow, not a CI or feedback bypass.
- Exact-head hosted source acceptance `39716960139` passed the selected Linux
  job `119621264467` and Windows job `119621264235`; the seven unrelated roles
  remained intentionally unselected. Local final qualification passed all 125
  delegation cases and all 88 PR-owner acknowledged-delivery cases. These are
  source/contained proofs, not a deployed fleet or installed-account claim.
- Re-read this Plan and Validation Plan after merge. Marked the landed Phase 3
  source requirements and the directly supported validation items complete.
  Credential acceptance against real installed configuration remains open,
  alongside complete installed adoption and governed lifecycle acceptance.
- Pulled the same worktree forward after preserving its complete merged tip.
  The Active parent still owns release verification, explicitly authorized
  installed proof, the retained fleet/Gateway consumer agreement, first-class
  service expansion, and eventual archive/resource closure. No runtime,
  subscription or deployment was changed by merging source.
- Next source stretch: reconcile the PR-service consumer contract with its
  retained routing, credential, dispatch and placement owners before wiring a
  fleet service. Keep local installation-cell delegation inactive until the
  existing installed prerequisite clears; do not substitute rollout of
  unrelated machine components or an isolated admission API.

### 2026-10-21 - Isolate legacy ownership and bound notification descendants

- The next review confirms the first three fixes and identifies four further
  boundaries: legacy owner package isolation, callback reconstruction of the
  subscriber's route, caller-only watch-home overrides, and Windows callback
  descendants surviving wrapper timeout. These remain part of #7714, not a
  reason to bypass review.
- Legacy owner commands now launch the attributable interpreter with `-I`.
  A real installed-package test places a shadow package in both the caller's
  working directory and `PYTHONPATH` and proves the installed owner still runs.
  `AGENT_PULL_REQUESTS_HOME` selects process fallback before an owner probe.
- A legacy callback retains the subscriber's nonsecret Dispatch root, discards
  foreign resident-owner Dispatch overrides, and reconstructs ordinary routing
  there before authentication. Real independent coordinators prove that an
  owner routed to another coordinator cannot redirect completion or replay.
- Acknowledged PR-owner notifications now use the existing canonical
  owned-process runner without requiring the worktrees observation provider.
  The native Windows regression stalls an actual stable-entry callback and its
  descendant, pins their process handles, observes all exits after timeout
  before replay, and then proves the original event completes once.
- Focused Dispatch qualification completes 87 cases before one test-only
  incorrect helper invocation; after that fixture correction the affected
  foreign-route and native timeout cases both pass. The complete PR-owner
  acknowledged-delivery module passes 88 cases, including legacy best-effort
  compatibility. The final combined four-module Dispatch selection passes all
  125 cases. Fatal lint, installer, headless and module-size checks pass.
- The preceding repaired head passed both selected hosted platform jobs in run
  39714176699. These additional changes still owe their own current-head checks,
  hosted qualification and review. Installed native-account, fleet and release
  obligations remain open; no deployed runtime or shared service is changed.

### 2026-10-21 - Repair the first delegation review findings

- #7714's first review identified three concrete blockers: forge/config
  overrides were not selecting process fallback, stored callbacks could
  outlive their Python version slot, and concurrent initialization could race
  the new waiter-column migrations. All three are corrected in source.
- `GH_HOST` and `GH_CONFIG_DIR` now select the ordinary process waiter before
  probing either owner. Schema additions tolerate only duplicate-column races;
  unrelated operational failures remain errors.
- The installer publishes a stable callback entry beside its canonical
  resolver. A subscription stores the entry and a nonsecret encoded envelope;
  each delivery resolves the current runtime rather than retaining an obsolete
  interpreter or package file. Missing entries select process fallback before
  preparation. Same-cell peer governance and exact-generation ACK fencing remain
  mandatory, and callback resolution never provisions or uses PATH.
- The Windows callback is a bounded, captured child, not a daemon: the system
  PowerShell host and its runtime child remain in the owner's process tree.
  The runtime child uses `CreateNoWindow` with concurrent raw stream copies;
  event stdin, errors and exit status remain observable. Linux uses the system
  shell and `exec` through the same canonical resolver.
- Contained Windows source qualification passes 390 cases with five platform
  skips, including actual pending subscriptions followed by upgrade, removal of
  the old slot, transition delivery and replay for legacy mode and both cells.
  The native installer publication path copies the exact stable entries.
  Additional supervisor fixtures now supply the reservation read-back contract
  their mocked Bridge calls require; production refusal guards are unchanged.
- The remaining 25-module regression tail completes 605 passing cases and
  exposes four permission-fixture failures on Python 3.14. Those fixtures now
  inject denial at the actual stat/lstat/native-directory seam rather than
  assuming `Path.is_dir()` delegates to `Path.stat()`. All intended partial
  discovery, healthy-peer and no-write assertions remain intact. The focused
  discovery/installation-context group then passes 61 cases with two platform
  skips; no discovery implementation changes.
- Fatal lint, the thirteen installer contracts, headless-launch guard and
  contribution-scoped module-size check pass. The narrowly selected hosted
  Linux/Windows lane passed the original PR head; its corrected-head repeat,
  fresh review and merge remain pending. Complete installed native-account and
  fleet acceptance remain separate parent obligations. No deployed runtime,
  shared subscription or service registration is changed by this repair.

### 2026-10-21 - Qualify fenced callbacks and preserve the process alternative

- The reconciled source negotiates both capabilities before preparing a
  generation. Arbitrary commands, explicit credentials and unprovable client
  routing remain ordinary process waits. Uncertain subscription setup does not
  create a second wait owner.
- Same-cell callbacks retain only a nonsecret installation identity, re-enter
  the existing canonical peer boundary, validate the current Dispatch receipt,
  and reconstruct its routing rather than inheriting the PR daemon's client
  overrides. Foreign or deactivated receipts refuse without acknowledging.
- Real isolated owner/coordinator tests prove coalescing, TCP outage, owner and
  coordinator restart, stable persisted event replay, timeout and one durable
  wake. Both cell selections exercise real callback subprocesses and canonical
  peer resolution; this does not substitute for installed native-account proof.
- After a clean managed pull-forward, the selected current-source negotiation,
  fencing, coordinator, process and owner contracts pass 325 cases with 19
  explicit skips. The installed-contract guard, fatal Python lint, headless
  launch guard and contribution-scoped module-size check also pass.
- With explicit operator approval, repair stale test fixtures for the current
  reservation/invocation contract and bound their native process trees. Windows
  publication flows receive focused item budgets for their measured native
  work; global suite limits and assertions remain intact. The complete default
  periodic portfolio has not yet passed as a whole.
- Add a manual, narrowly selected Linux/Windows source-acceptance path to the
  existing installation-context workflow. Actual hosted execution, review,
  merge, release and authorized installed proof remain open. No shared service,
  deployment, Dispatch subscription or fleet route is activated.
- Preserve all prerequisite journals in this substantive integration. The
  fleet-consumer agreement, first-class service expansion and final closure
  remain part of this Active parent rather than being implied complete by
  local delegation.

### 2026-10-21 - Resume fenced dispatch delegation

- The operator directs this driver to focus solely on PR-Watch delegation;
  unrelated installation and connectivity repairs have separate owners.
- Pulled the prerequisite worktree forward while retaining its unpublished
  journal commits. The installed attributable PR-owner command now reports
  `0.3.18-dev1`; complete isolated installed-contract acceptance remains open.
- Preserved the saved delegation branch and reconciled its implementation onto
  current `dev`. Retained current coordinator capabilities and source-qualified
  versioning. Reused the already-landed PR-owner peer/state-root contract rather
  than replaying its obsolete precursor.
- No live coordinator, subscription, service activation or deployment changed.
  Current-source tests and the remaining integration/acceptance gates remain open.

### 2026-10-20 - Run the explicitly authorized local unified update

- The operator explicitly directs this session to run the local unified
  harness update and handle its errors, warnings and blockers. This permits
  the local canonical update rather than waiting exclusively on delivery to
  the previously retained updater; it does not authorize a competing updater,
  immutable-slot rewrite, feed-policy bypass or Dispatch/fleet activation.
- The single update completes after 777.7 seconds with exit 1. Payload refresh,
  anchor fast-forward and multiple runtime installations succeed. The
  PR-owner installer completes at `0.3.17-dev1`, and its real payload-local CLI
  imports and dispatches `--help` without starting a watcher. This is not the
  full isolated deployed-contract acceptance.
- Actual Bridge health now reports `0.9.67-dev1`, ready and not draining.
  #7058's SSH profile-port correction is therefore live. A subsequent framed
  read-only request exposes the separately tracked binary-stdin defect:
  the actual management shim exchanges a valid hello, then corrupts a request
  frame; the same SSH route and selected runtime return the expected structured
  refusal under a diagnostic direct-runtime invocation. That invocation is not
  used to deliver work. Add fresh evidence to the existing #7264 repair,
  preserving its retained #5694/#7119 consumer gates and source ownership.
- Index correctly refuses same-version content conflict. Read-only comparison
  establishes that only two generated helper bytecode files differ between
  self-stages; their embedded absolute staging paths contaminate whole-payload
  identity while all source files match. Preserve the completed slot and
  refusal. Notify the retained canonical-helper owner in #7234 under #5472;
  no marker, lock, bytecode or installed source is edited as a workaround.
- Machines initially times out on a construction lease. A concurrent builder
  subsequently completes the selected `0.2.28-dev1` slot, and the real CLI
  reports that version. Do not delete the persistent kernel-lock file or
  infer its owner from its timestamp. Final unified-update convergence is
  still owed rather than treating later completion as a successful first run.
- The registered Manager launcher fails dependency resolution and emits no
  stdout. The Terminal profile refresher calls that same launcher and attempts
  to parse its empty stdout as JSON; settings corruption is not demonstrated.
  An approved mirror reports a recent upload date for an old, digest-identical
  public dependency, conflicting with the retained seven-day filter during
  universal resolution. Keep the governed feed and age policy intact.
  Current bootstrap has no platform-local production repair path; the coherent
  source remedy requires provisioning and checking the selected platform's
  runtime before publishing its launcher, rather than a binstub rewrite.
- Three installers visibly fall back to inherited streams because they do not
  declare the native UTF-8 output contract. MCP and budget installation complete;
  that compatibility fallback is not an encoding failure or permission to claim
  unsupported framing. No output contract or error is suppressed.
- The update remains incomplete. No independent repair PR supersedes another
  contributor's open work, no shared service is manually restarted, and no
  policy or installation admission guard is weakened. Preserve the remaining
  source and governance decisions with the parent still Active.

### 2026-10-20 - Diagnose the retained-owner carrier failure without repair

- The operator requests read-only diagnosis of the carrier handshake failure.
  Ordinary SSH reachability and a fresh isolated carrier using the configured
  SSH profile both succeed. The serving daemon's read-only remote resolution
  still returns HTTP 503 `carrier_unavailable` with `IncompleteReadError`.
- Observe the actual daemon-created SSH command rather than attributing the
  error to generic packet corruption. It forces `-p 22`, overriding the
  profile's tunnel endpoint. Repeating that exact override produces exit 255,
  zero stdout bytes and a banner-exchange connection refusal; profile-based
  invocation receives a valid framed carrier hello.
- The old serving Bridge is `0.9.62-dev1`. Its remote-operation adapter passes
  topology user/port defaults into `SSHProfileSource`, overriding the
  configured profile. #7058 already corrects that adapter to use the alias
  alone. The released `0.9.65-dev1` remote-operation source at #7291 matches
  the corrected source blob exactly; no competing source fix is needed.
- Preserve the retained updater and existing session. The remaining boundary
  is governed rollout of the released correction, not changing SSH profiles,
  hardcoding a tunnel port, manually restarting the shared daemon or bypassing
  the attributable command surface. This diagnosis performs no runtime update,
  service restart, session replacement or delegation activation. The parent
  remains Active and installed-contract proof is still owed.

### 2026-10-20 - Confirm released adoption and bootstrap content

- The operator reports that promotion is unpaused and a release is available.
  Read the current trusted Main state rather than retaining the earlier pause
  as a permanent blocker: `paused` is false and `pause_reason` is null.
- Promotion #7291 actually merges at
  `9c7a61518540787498c77a284f640c36bbe7d8e4` at
  `2026-10-20T00:50:39Z`, publishing qualified Dev
  `af4ef48d7175d6e2a3aace81bc61017efd45e315`. That Dev snapshot contains
  the merged #7160 adoption and #7240 bootstrap fixes. The generated candidate
  `5fc89aa4106820482516279c95c21a6ff9fa08fd` and merged Main have the
  identical whole-tree identity `bf9c3992c2e55d7472561416d1378788aedf2685`.
- Confirm the release by content, not original-source ancestry against
  generated Main. All 233 selected PR-owner, lifecycle, peer-launch,
  singleton, bootstrap and runner-contract files retain their exact source
  blobs and modes. All 106 materialized PR-owner library files match their
  canonical Dev sources; both shipped installer engines match the canonical
  installer-engine library. Release version for `agent-pull-requests` is
  `0.3.16-dev1`.
- The release gate is cleared, not the deployment gate. Preserve the retained
  unified-updater owner's custody; do not start a parallel updater, replace a
  shared owner, enable Dispatch delegation or activate fleet routes from a
  release notification alone. No installation or running-state proof is
  claimed by this entry.
- Keep combined land-and-deploy items unchecked pending authorized rollout
  and isolated deployed-contract proof. Dispatch, fleet-owner agreement,
  service acceptance and final closure remain open; the parent stays Active.
- The operator subsequently authorizes rollout through the existing retained
  unified-updater owner, followed by isolated deployed-contract validation,
  explicitly excluding a parallel updater, Dispatch activation and fleet
  activation. Coordination readiness is confirmed, but the bounded request to
  that owner's exact retained worktree fails with HTTP 503
  `carrier_unavailable`: `carrier handshake failed: IncompleteReadError`.
  Delivery is not confirmed. Preserve owner custody and the existing session;
  do not retry in a loop, restart shared services, substitute an updater or
  infer installation from release availability. Rollout and installed proof
  remain blocked on restoring the retained-owner communication path.

### 2026-10-19 - Confirm the intentional release hold after green Dev CI

- Post-fix Dev CI `39253165776` succeeds on exact merged source
  `de8b5f4b81853c5b03517177d4769ebf9f7ef5ab`, with no failed or
  unfinished jobs. The bootstrap regression no longer leaves Dev red.
- The actual completion trigger `39255839947` identifies that source and
  dispatches promotion run `39255877612`. Although the run concludes
  successfully, only its admission gate executes; every qualification,
  publication and merge job skips. This is not a released candidate.
- The trusted Main gate explicitly reports that automatic validation and
  promotion are deferred because Main is paused. The unchanged Main
  `a325f5063e7cc0a2b3276be81be3e4959f8f4651` records operator-approved
  containment until pre-merge qualification makes it safe to consume under
  #6237; its last promoted Dev source remains
  `fc61ab3aa44ac2b235ea540601127ff91129b81c`.
- Preserve that administrative gate and the retained release/updater ownership.
  Do not unpause, dispatch a bypass qualification/promotion, install, update or
  activate delegation merely to complete this effort. No polling loop remains
  useful while the explicit hold is in force; all owned watches have finished
  and their results are captured.
- The current source slice is merged and green, not released or deployed.
  The parent remains Active, with release/authorized deployment awaiting the
  retained #6237 gate and all Dispatch/fleet/final-closure obligations intact.

### 2026-10-19 - Merge the proven bootstrap fix forward

- #7240 actually merges at `de8b5f4b81853c5b03517177d4769ebf9f7ef5ab`
  after exact head `0f8de077ba0c073e4c34ba32a6cd2f482a5a1e5b`
  receives genuine automated approval `5571272800`, required CI
  `39248507940` succeeds, and all fifteen selected native jobs pass in
  `39248519840`. The earlier same-head CI `39248249746` also succeeds;
  no failed run, hold, required check or review request is ignored.
- Actual Windows normal-upgrade job `118046294407` executes all 39 selected
  cases with no skips in 257.63 seconds. Windows PowerShell records two
  invocations, 300 native observer samples, zero visible windows, zero
  foreground transitions and four timeout participants exited. PowerShell 7
  records two invocations, 224 samples and the same zero-window/zero-focus/
  four-exit result. Both prove native Job membership, empty-Job termination
  and signaled original process handles, not merely creation flags.
- Reply with these measurements and resolve the one review thread only after
  its actual native execution passes. Re-read the full approval body and every
  thread page before the role-scoped managed merge; the overview's retained
  open-finding banner does not supersede the resolved, proven thread.
  No review or CI bypass is used.
- Re-read the parent after merge. The bounded source regression and required
  Windows proof are complete; release qualification must now run again on
  source containing this fix. Combined deployment prerequisites, dispatch
  integration, fleet-owner agreement, installed proof and final closure remain
  open. No install, update, rollout, shared-owner replacement or dispatch
  activation follows from this merge. The effort remains Active.

### 2026-10-19 - Add the required live Windows launch regression

- The bootstrap repair is published as #7240 at `d1ae5e7e`.
  Required CI `39243373833` succeeds, including the previously missing
  customization consumer, and native acceptance `39243565444` passes all
  fifteen selected jobs. These functional gates do not satisfy the separate
  live window/focus requirement for the new native child-launch path.
- Actual review `5570850298` identifies that missing proof as a Medium
  finding. Add the opt-in live Windows flow required by the existing launch
  pattern; do not treat mocked flags or output assertions as a substitute.
- For both Windows PowerShell and PowerShell 7, the flow launches the actual
  wrapper from a real windowless `pythonw.exe` parent, runs a real local
  console descendant across two complete invocations, and reuses the canonical
  Win32 observer. Additional sampling records every new visible window and
  every foreground transition, including baseline windows, without recording
  titles or user content. The observer must stay alive and sample both cycles.
- A separate forced timeout captures all four native participants, verifies
  each is in the caller's non-breakaway Job, terminates that Job with
  zero-active-process confirmation, and requires each captured native handle
  to signal exit. No daemon-survival or remote-routing behavior is introduced.
- This flow runs only in the already opt-in disposable Windows hosted-account
  normal-upgrade scenario. Local checks compile both fixture programs and
  explicitly skip the two live cases on the shared host; current-head native
  execution remains required before resolving the finding or merging.
  The parent remains Active with no install or rollout authorized.

### 2026-10-19 - Fix the post-merge native bootstrap composition regression

- Post-merge Dev CI `39232925741` blocks release of #7160. Its only failing
  root job is `customizing-copilot`: the actual whole-suite session-start
  contract sees the new admission wrapper as a possible context producer.
  Reproduce the same assertion through the contained consumer suite before
  editing; this is not a transient or permission to retry promotion.
- The wrapper now follows the existing `bootstrap-check.*` naming and native
  exit/finally JSON-finalizer contract. It emits one empty JSON value for
  cell, legacy and refused admission, suppresses only delegated stdout, and
  preserves stderr and failure status. Hook registrations and packaged-payload
  coverage follow the renamed internal entry. The generic scanner, trust
  markers and assertion remain unchanged.
- Add the existing whole-suite consumer to changed-plugin CI selection so
  future cross-plugin hook regressions cannot first appear after merge.
  Native wrapper controls cover both shells, cold/legacy/refused/unknown
  admission and a failing legacy delegate. They catch direct PowerShell
  `Console.Out` bypassing pipeline suppression; the wrapper now drains a
  contained same-host child's stdout without a console window, inheriting
  stderr and retaining the actual exit code.
- Preserve the post-merge journal tail while pulling forward: managed sync
  safely aborts a squash-history conflict; after verifying that only the
  single journal commit follows the merged PR head, replay that tail onto
  current `origin/dev`. No reset, stash, force-push or foreign work is used.
- Complete invocation coverage passes 78 cases with one explicit-tier cold-build
  skip in 209.49 seconds; the unchanged complete customization consumer passes
  515 cases in 78.20 seconds. Runner/partition/native-scope contracts pass
  250 cases with two explicit skips in 29.52 seconds. The actual CI foundation
  recipe passes all 15 packaged-hook/stamp cases in 144.56 seconds, retaining
  each native child's original 60-second deadline; a first local probe's extra
  30-second wrapper limit was not the consumer's CI contract. No production,
  test-source or CI deadline is increased.
- Existing fifteen-job native acceptance also selects the new wrapper/error
  controls and actual disposable-account governance checks. Current-head native
  proof, ordinary PR review/CI, merge and release remain owed. No install,
  update, rollout or dispatch activation is authorized; the parent remains
  Active.

### 2026-10-19 - Merge the reviewed installation-cell adoption source

- #7160 actually merges at `3c62c9442c5944640d2c13385d7ff00db614c1aa`
  after exact tested head `30df6e50c1410b2689ae1e6d375b6657815f8aa5`
  clears actual automated review `5569218963`, all three resolved review
  threads, required CI `39226193526` and native acceptance `39226209682`.
  The managed source-owner merge uses the resolved `pr-self-merge` flow;
  no CI bypass, review waiver or hold dismissal is used.
- All fifteen selected native jobs pass; only the two unselected diagnostics
  skip. The actual hosted PR-owner partitions execute all assigned cases:
  first partition 99/136/152 passes in 26.04/105.25/92.44-second groups;
  second partition 188/121 passes in 106.20/160.83-second groups.
  Their explicit skips remain visible and every group is below the unchanged
  300-second cap. The index suite successfully sets up and executes
  320/366/420/118 passing cases; no setup retry exception or test omission
  substitutes for required green CI.
- Re-read the parent plan after merge and close only the implemented and
  proven source-adoption/cutover items. Combined land-and-deploy, release,
  installed proof, dispatch delegation and the expanded fleet service remain
  unchecked. The accepted reader residual #7208 remains tracked, not
  cause-fixed.
- Next stretch is release qualification and coordination with the retained
  unified-updater owner, without installing, updating, replacing a shared
  owner or activating dispatch. Fleet Gateway/credential/placement agreement
  remains with its existing owners. The effort stays Active; this component
  merge is not whole-adoption completion.

### 2026-10-19 - Retain strict CI limits with smaller PR-owner test groups

- Final-runtime candidate `d494201e2` receives actual automated review
  `5568632217` with no open findings; all three review threads are resolved.
  Native acceptance `39219867964` succeeds on that exact head with all fifteen
  selected jobs, including cold and running-owner restart responses.
- Required CI `39219690896` nevertheless fails. Its PR-owner second partition
  reaches the strict 300-second process-group limit at 88% of the selected
  cases. Its separate index job fails before any test starts: the setup-uv
  action's remote latest-version manifest request times out after five seconds.
  The required aggregate correctly remains red; neither is bypassed.
- The PR owner's default file-group size is tightened to ten through the
  existing contained runner's per-plugin profile. Both job assignments and
  all test files remain complete and disjoint, explicit group limits still
  win, and unrelated plugins retain twenty-five-file defaults. Item, group,
  aggregate-plugin and CI-job deadlines are unchanged. Native filtered
  acceptance retains its existing grouping and budgets.
- Complete runner/partition/native-scope contracts pass 250 cases with two
  explicit skips in 30.23 seconds. The exact runner selection proves every
  assigned file survives subgrouping once and in order. This changes no
  qualification recipe, controller routing or test-selection filter.
- A new-head CI run must exercise the smaller Linux groups within the actual
  hosted deadline and run the index suite after successful setup. The
  diagnosed remote bootstrap timeout does not authorize skipping index tests,
  mutating its runtime or merging past a non-green gate. Review, exact-head
  native acceptance and actual merge remain owed.

### 2026-10-19 - Preserve the governed restart response contract

- Published the staged-origin repair at `97b995eac` on #7160.
  Native acceptance `39217236601` passes all fifteen selected jobs at that
  exact head, including the expanded native-account Linux/Windows legacy
  staging cases. Only the two unselected diagnostic jobs skip.
- Another completed actual review, submitted against the preceding
  `e944d348` head, identifies a separate Medium compatibility regression:
  governed `restart --json` returns success without the legacy JSON body.
- The existing guarded `ensure()` now reports whether incumbent discovery
  found an owner, only after ready/complete success. Governed restart emits
  the unchanged `restarted`/`was_running` JSON shape or the ordinary success
  message. Refusal and rollback still propagate without a success response.
- Response-shape and failure controls cover both incumbent states. Existing
  native incumbent coverage checks the actual `ensure()` result, and normal
  shipped-payload acceptance now starts cold through `restart --json`, then
  restarts its upgraded live owner while preserving pending subscriptions.
  All operations consume the original scenario budget; no deadline is
  increased. Both complete affected modules pass all 18 contained cases in
  814.79 seconds, including actual cold restart, full-payload live upgrade,
  warm restart and existing native incumbent/refusal coverage.
- Required CI, a subsequent actual review and fresh native acceptance remain
  binding for the final repaired head. No merge, deployment, shared-owner
  replacement or receipt investigation is performed.

### 2026-10-19 - Revalidate marketplace admission after legacy self-staging

- The actual completed review of #7160 identifies one additional Medium
  finding in its body despite the overview's no-open-issues banner: the
  staged child loses the original marketplace locator, so a valid nonempty
  per-marketplace legacy policy can refuse the normal installer.
- Both native mode helpers now recognize only the installer's canonical
  per-profile staging layout. They require an existing canonical installed
  origin and equal, link-free payload trees before asking the unchanged
  native installation-context validator to resolve that original source
  against current governance. Environment staging flags alone never waive
  admission, and origin replacement or disappearance refuses safely.
  The shared thirteen-plugin self-staging blocks remain unchanged.
- Initial contained coverage passes 33 selected cases in 136.02 seconds,
  including actual PowerShell staging for all five mutating installer
  actions and refused modified payloads, missing/forged origins, changed
  governance, lost marketplace declarations and ordinary payload hints.
  Final complete invocation coverage passes 68 cases with one explicit-tier
  cold-build skip in 203.40 seconds, including both scoped policy forms and
  native link refusal. An initially malformed unrelated-marketplace fixture
  is corrected to the canonical source-derived ID shape; validation is not
  relaxed. The partition/native-scope contracts pass all 187 cases in 17.57
  seconds with short runner-owned Windows paths; exact fixture cleanup
  removes its read-only Git objects afterward. Fatal lint, module-size and
  all thirteen install-contract gates pass.
  The existing native-account normal-upgrade scenario also selects these
  contracts on Linux and Windows without adding jobs or widening deadlines.
- This is a supported legacy-compatibility fix, not renewed reader/receipt
  investigation or a deployment. Current-head review, required CI and native
  acceptance remain owed; #7160 is not merged and the parent remains Active.

### 2026-10-19 - Preserve native acceptance while reconciling current-base CI

- Published bounded recovery at `e9e9f37ad` on the existing #7160, replied to
  and resolved the two proven admission findings, and updated the reviewable
  rationale. Native acceptance `39208728241` succeeds on that exact head:
  all 15 selected Linux/Windows/macOS jobs pass; only the two unselected
  diagnostic jobs skip. macOS remains refusal evidence, not positive operation.
- The scoped review wait reaches its genuine 600-second deadline without a
  new review event. Its conflict report is confirmed by Git's non-mutating
  merge calculation: upstream's preserved-controller compatibility branch and
  this PR's additional smoke partition overlap in one test assertion.
  Supported synchronization aborts safely with the old head retained; the
  documented owned-PR rebase then replays all four commits, preserving upstream
  controller/fixture changes and counting three extra partitions only for the
  modern matrix. The reconciled partition/native-scope contracts pass all
  187 cases in 20.23 seconds. No runtime bytes change in the rebase.
- The shared workflow reference is aligned to the existing four effective
  profiles and explicit self-merge versus consent verbs. It recognizes genuine
  current-head approval without a categorical owner-authorship assumption;
  documented clean-comment acceptance remains conditional on the target repo.
  Caller role, actual review, ownership, holds, conflict and required-CI vetoes
  remain binding. This documentation does not change another campaign's
  classifier, consent automation or repository configuration.
- Current-base publication still owes a subsequent actual review and new-head
  required CI/native acceptance. Historical green native jobs do not authorize
  merging a different head. No CI bypass, shared-owner replacement or rollout
  is performed; the effort remains Active.

### 2026-10-19 - Implement the selected bounded installer reconciliation

- The completed-target installer now retains one original 300-second activation
  envelope, including physical target binding. The first owner attempt and
  canonical reconciliation each retain an at-most-120-second operation limit.
  Only a timeout captured under the initial attempt's own held guard, after
  commit with an exact successor, admits one status/reconciliation attempt.
  Unscoped timeouts and identity, governance, rollback or other failures do not.
- The recovery path reports the original timeout, holds a fresh same-installation
  native guard, checks the exact transaction/installation/target/successor and
  coherent selection, and obtains authenticated native-bracketed writer health.
  Normal journal recovery additionally compares the complete just-verified
  document before rotating controller custody. Changed status evidence cannot
  rotate another transaction. No recovery branch begins a journal, spawns an
  owner or advances a route generation.
- Canonical reconciliation must finish and archive the same transaction, prove
  original predecessor exit, clear its breadcrumb and retain final exact-owner
  health within the original envelope. Both reconciliation archive-return paths
  now check the unchanged operation deadline. The whole native upgrade CLI uses
  the remaining original 900-second scenario budget rather than treating its
  earlier 300-second build-plus-activation proxy as the owner deadline; owner,
  native-exit, scenario, group and plugin limits are not increased.
- The initial focused run passes 36 cases, including actual completed-prefix,
  private IPC and native same-owner reconciliation at committed and complete
  phases, and unchanged-journal refusal for foreign comparison evidence and
  governance. Its one failure is a test cleanup attempting a prohibited
  terminal retire, corrected to its existing test-owned graceful stdin cleanup.
  Current full affected contracts, the post-status change refusal, actual
  normal full-payload upgrade, current-head CI/native acceptance and fresh
  review remain owed. Injected lost-result controls are not evidence that an
  actual production timeout has recovered. The effort remains Active.
- The corrected affected owner/runtime admission batch passes 154 cases with
  two explicit skips in 1,176.91 seconds. After adding the final complete-document
  fence, all 44 journal/fence cases pass in 322.60 seconds, including a real
  completed-prefix owner whose journal changes after authenticated status:
  recovery refuses before controller rotation and preserves the changed bytes.
  Fatal Python lint, the 961-line journal module cap and all 13 install contracts
  pass. The actual normal full-payload upgrade is now running separately; those
  unit/native-boundary controls do not substitute for that consumer.
- The actual normal full-payload cold install and live-owner upgrade now pass
  in 533.09 seconds through the shipped native installer and completed runtime.
  The scenario requires the original predecessor's native exit, complete
  same-successor journal/archive, coherent metadata/native route and unchanged
  pending subscription state; no timeout waiver or conditional success was
  added. This passing run does not by itself identify whether the initial
  attempt needed reconciliation. The injected interruption cases separately
  prove that recovery path. Publication, fresh review/current-head CI/native
  acceptance, merge, release and authorized rollout remain open.

### 2026-10-19 - Address actual admission review findings

- The operator subsequently selects `bounded-status-reconcile`: after a
  post-commit activation timeout, verify the exact committed owner and permit
  one bounded reconciliation of that same journal through the normal installer.
  The original timeout remains visible. Unknown ownership, a changed
  transaction, other errors or a failed reconciliation still fail; no second
  daemon or relaxed validation is allowed. This implementation is the next
  slice, not yet a completed defense or a waiver of current-head gates.
- Exact candidate `2f6805616` passes required CI `39191708109` and native
  acceptance `39191823054`: all 15 selected native jobs succeed, with only the
  two unselected default diagnostic jobs skipped. Copilot review `5566485611`
  nevertheless reports two concrete admission defects; those findings remain
  merge gates independently of the green runs.
- Reproduced the POSIX wrapper rejecting an absent optional marketplace map
  while accepting invalid scalar/null maps, and physical completed-module
  entry accepting missing current selection or a mismatched manifest.
  Corrected the shell's missing-path return code and extracted the existing
  owner selection check into one stdlib-only helper shared by payload dispatch
  and completed peer entry. Ordinary handlers require coherent selected
  metadata; journal-authorized reserved passive startup remains distinct.
- The focused real-shell/JSON-query, physical-module entry and actual reserved
  passive startup checks pass all ten selected cases in 48.51 seconds.
  The shell branch fixture substitutes only native status, not its JSON query;
  full native-account policy admission and the actual same-cell peer path
  remain separate current-head acceptance obligations. Canonical receipt
  validation and all lifecycle/CI deadlines are unchanged.
- Full local peer rejection and real native-policy preflight pass, but the
  normal live-upgrade composite reaches a separate post-commit timeout. Caller
  CLI admission was also incorrectly timed against the owner's 120-second
  cutover allowance: startup now consumes only the remaining existing
  900/1,140-second scenario budget, never a reset window. The actual driver now
  checks its unchanged deadline after terminal archival too; a real-child
  late-archive control failed before that correction. Shared entry selection
  uses its already-validated completion result rather than adding duplicate
  full validation. These corrections do not make the remaining upgrade
  timeout a successful operation; bounded status/reconciliation still needs
  implementation and real proof.

### 2026-10-18 - Resume canonical installation adoption; stop receipt investigation

- The operator explicitly selects `tracked-residual-with-defenses` for the
  unexplained Windows in-handle reader observation, superseding the earlier
  cause-investigation merge hold. #7208 records the residual, missing original
  metadata and distinction from the cross-API report #1423. Passing reruns are
  not a claimed fix. No further recording investigation is requested.
- Follow-up direction, verbatim: "The installation-receipt stuff can just be
  improved by aligned install to be consistent with all other plugins; I'd move
  on from trying to investigate the receipt thing". Keep the canonical shared
  installer, generated validators and strict ownership checks; do not add a
  PR-specific receipt workaround.
- The caller recovery defense is a fresh scoped `pr-status` read after an
  interrupted wait, followed by re-arming only an unmet condition with the
  original cursor/remaining deadline and no duplicate wait owner. Actual
  transitions and consent updates still need handling; auth, usage,
  cancellation and installation-identity failures are not spurious wakes.
  The owning Worktrees PR workflow now states this discipline.
- Reconciled the adopter with current `dev`, preserving the reviewed fleet
  scope/consumer-contract intake and upstream CI backend-selection policy.
  Adoption still requires canonical-copy/install-contract checks, current-head
  CI/native acceptance and actual review before merge. Release, authorized
  rollout, saved dispatch integration and fleet-owner agreement remain open.
- Canonical generation now confirms all 12 plugin and one standalone-payload
  installation-context adopters, every peer vendor and the 13-plugin install
  contract. No canonical reader or PR-specific validator change was needed.
- The selected native installer check exposed a separate fixture timeout:
  synthetic legacy-source tests copied and repeatedly fingerprinted the entire
  newly expanded cell-bootstrap payload. Retained the real complete canonical
  `versioned_runtime*` family while excluding unrelated cell inputs from that
  synthetic fixture; the full-payload cell acceptance remains separate and
  unchanged. A byte-for-byte family regression and both native PowerShell
  construction/reuse cases pass, together with a real pipe-inheriting-child
  timeout cleanup case: four passes and one explicit POSIX-layout skip in
  49.28 seconds. Native query, item and suite budgets are unchanged. Fixture
  subprocesses now use owned Job/process-group teardown rather than leaving
  descendants holding capture pipes after timeout.
- The complete selected installer wiring, native source-transaction and real
  completed-slot construction slice now passes 103 cases with one explicit
  Windows/POSIX-layout skip in 318.69 seconds. Rebased partition/admission
  contracts pass 186 cases. Their accompanying version-guard tests exposed Git
  treating a long SHA range/blob expression as a Windows path and silently
  reporting a failed diff as no changes. Explicit revision/path separators and
  a visible failed-diff result correct that guard; its complete 30-case suite
  and 52 changefile/accumulation consumer cases pass. No test or CI deadline
  was increased.

### 2026-10-18 - Concrete PR-service contract intake

- Reconciled the fleet-service worktree with newly merged #7099. Its isolated
  admission library validates existing observation/operation shapes; it is not
  a PR registration route, durable delivery transport or serving policy owner.
- Grounded the consumer proposal in the actual PR registry and callback
  implementation: pending registration/event identities already exist, but
  replace-by-subscriber-ID is not idempotent registration retry, local callback
  argv is not a remote transport, and callback return success is not a remote
  coordinator ACK protocol. The proposed shared consumer flow retains PR-owned
  durable state and dispatch's existing generation fences.
- Live owner-routing coordination has not established Gateway authority. An
  attempted coordination channel returned `carrier_unavailable` (HTTP 503,
  handshake `IncompleteReadError`); preserved that evidence without restart,
  replacement or repair. The contract proposal and owner-agreement checklist
  permit specific review while keeping those owners and the original reader
  blocker intact; no runtime or live deployment changes are claimed.

### 2026-10-18 - Fleet PR-service amendment reviewed and merged

- #7189 merged into `dev` at `34c49cf34ec34bcddfa607eb0acc95526a6ce0ec`
  after exact-head Copilot approval `5564926445`, no review threads and successful
  required CI `39165380566`. The standing single-operator fleet boundary and
  first-class PR-service implementation/validation plan have cleared review.
- Reconciled the planning worktree onto the merged intent and retained the
  Active parent and distinct fleet-service slice. Next resolve the retained
  fleet owners' supported role, routing, credential and remote delivery
  contracts. No runtime, service deployment or live authority changed.
- Read the current `fleet-manager/docs/gateway-runtime.md` at the merged
  source: its executable backend contract is index-specific, its enrolled roles
  do not include a PR-service consumer, and its credential broker is explicitly
  in-memory with restart uncertainty rather than a durable event queue.
  #7188 therefore depends on the retained #6236/#5789 owners agreeing PR service
  routing/enrollment and transport-appropriate durable delivery. Do not disguise
  PRs as index corpora or use the credential broker as the subscription registrar.
- The existing Windows receipt-read diagnosis remains open; its frozen repro
  candidate and operator-run capture procedure are preserved. The scope
  amendment neither clears that blocker nor completes installation-cell
  adoption, saved dispatch integration, release or authorized deployment.

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

### 2026-10-18 - Bound receipt diagnostics and preserve the smoke deadline

- The next required-CI run, `39114847088` at `1635df762`, passes the workflow trust guard and every other executed job but cancels PR-owner smoke at its unchanged eight-minute limit. The first contained group passes 369 cases with 56 intentional skips in 293.50 seconds; the second is still running when the job deadline expires. Use the existing scheduling map to run the suite's two original file groups as independent required partitions. Complete/disjoint file coverage, unchanged runner process groups and exact candidate-qualification inventory pass all 82 targeted tool contracts; no test selector, job deadline or containment limit is weakened.
- Expand the opt-in receipt diagnostic to a real active installed owner with concurrent completed-runtime revalidation, authenticated health requests and raw receipt readers. The initial bounded case passes in 26.17 seconds; the sustained 75-second consumer window passes in 83.40 seconds, still under the existing 180-second item bound. Neither supplies the original refusal's missing cause or clears the merge blocker.
- Test-only refusal tracing now captures the exact path, both Python stat records and Windows basic-information/file-ID snapshots around the open descriptor before cleanup. A real metadata-change control proves the diagnostic records a refusal and captures the changed native timestamp without modifying production validation. That control, the raw parallel-reader case and the instrumented passive crash boundary pass together in 52.64 seconds. Production reader behavior remains unchanged; #7160 stays draft pending a justified resolution, current-head native acceptance, CI and review.

### 2026-10-18 - Complete native acceptance; strict reader investigation retained

- Native acceptance run `39108195693` passes all 15 effective jobs at exact candidate `7db1ea808`: all ten fresh Linux/Windows account scenarios, four contained controller-death/concurrent-upgrade jobs and native macOS refusal. The default diagnostics are the two intentionally skipped jobs in this opt-in run; no selected acceptance job is skipped or waived.
- Required CI later rejects the acceptance workflow's dynamic runner expressions under the repository's existing trust policy. Split the Linux/Windows native and contained jobs onto literal hosted labels, using the repository's existing shared-step YAML-anchor pattern. Scenario matrices, commands, account admission, bounds and the 15-job effective inventory stay unchanged; the trust guard itself is not weakened. The real trusted-CI guard and all 157 trusted-routing/scope tests pass.
- The unexplained earlier in-handle Windows receipt read remains a merge blocker, and investigation continues rather than accepting an intermittent gap. A bounded synthetic native probe covers 2,000 atomic-replacement/read pairs, both normal and aged access timestamps, with real Windows basic-information snapshots under Python 3.14.7; it observes no stable-field change. This rules out that probe's simple access-update path, not the original failure. The separate upstream stat-versus-fstat compatibility mismatch in python/cpython#157671 does not by itself explain a change between two fstat calls on one open descriptor.

### 2026-10-18 - Native deadline preservation and POSIX vendor mode

- The corrected exact-head native run `39106966491` passes 14 of 15 effective jobs, including all ten fresh-account Linux/Windows scenarios, native macOS refusal and the Linux/Windows controller-death matrices. The remaining Windows concurrent-upgrade check measures an acknowledged subscription deadline drifting by 14.385 milliseconds; its existing ten-millisecond assertion correctly fails.
- Reproduced the defect deterministically with a coarse wall clock and independently advancing monotonic clock: every persisted snapshot recomputed the absolute deadline, producing a 14.625-millisecond shift without any registration change. The registry now captures that durable deadline once at registration and retains the original value on reload, while live expiry continues to use its monotonic deadline. The new regression and all 142 delivery/registry/quiescence/actual-concurrent-upgrade cases pass. Tightened the native integration assertion to exact deadline equality rather than increasing its tolerance.
- Required Linux CI also detects the new generated installation-context shell copy's Git executable mode differing from canonical. Windows byte synchronization cannot infer that index bit; matched the new copy's index mode to the canonical `100755` without modifying its content or relaxing the POSIX guard.
- Four additional instrumented passive-recovery attempts on the local Windows interpreter do not reproduce the original in-handle stable-read refusal. They provide no missing metadata evidence and do not clear the blocker. Native/current-head CI and review still must pass after these fixes; the draft remains unmerged and the parent effort remains Active.

### 2026-10-18 - First native hosted-account acceptance and fix-forward

- Published draft #7160 at candidate `0ca337ecae` through the managed flow after reconciling the newest immutable-source installer; that source's native transaction/materialization/normal-peer checks pass 77 cases with one explicit skip. Dispatched native acceptance run `39105260993` at the exact candidate: six of the 15 effective jobs pass, including macOS refusal, Linux peer roundtrip, both Linux lease-only refusals and both contained Linux owner-boundary jobs. Nine fail; all seven Windows jobs reject newly created private custody as belonging to another OS account, while Linux upgrade/isolation fixtures try a nonexistent extension-suffixed binstub.
- New Windows secret-bearing temporary files now assign the current token user explicitly alongside the protected DACL before publishing bytes; ordinary read/replacement still rejects foreign custody without changing it. Native assertions verify owner-setting flags and real privacy checks, plus a wrong-owner no-repair control. Corrected the POSIX fixture to call the actual extensionless shipped binstub instead of inventing a `.sh` name. The real full-payload peer/build/private-file group passes all 32 selected cases locally.
- Required CI also found the shared hook-roster assertion still requiring a direct bootstrap reconciler instead of the PR owner's admission wrapper, and three metadata adapter tests consulting the real POSIX account policy instead of their synthetic fixture policy. The roster now requires the actual attributable wrapper and both native wrapper files while retaining the shared reconciler requirements. Only the in-process metadata fixture substitutes its explicit profile observation; canonical policy/activation parsing remains real, and fresh-account acceptance still prohibits substitutions. Current-head CI and the complete external matrix must pass again before readiness.
- The original Windows in-handle stable-read refusal remains unresolved and is not cleared by these independent failures or passing lanes. The draft is not a merge verdict, release or authorized rollout, and parent delegation gates stay open.

### 2026-10-18 - Current-base integration and restored-owner withdrawal

- Consolidated the unpublished adopter and reconciled it with the current contribution branch. Kept the shared legacy daemon-spawn primitive, native human-output initializer, seasoned dependency installation and newly split guest-environment validators. Canonical installation-context, peer-launch and library copies remain generated from their owning sources.
- Real normal-install checks exposed the new dependency policy attributing packages to the immutable helper's snapshot while the builder installed a disposable copy. Construction now loads that copy's own helper and uses its directory as cwd. Its policy-retained inputs use the inherited temporary root rather than a nested version-slot temporary root, avoiding native Windows subprocess path overflow. The real package-build/completed-replay lane and all 25 installer-slice cases pass; the normal full-payload peer roundtrip and passive controller-death/parallel-read lanes pass separately in 149.05 seconds. The shared native-exit/custody/deadline lane passes 236 cases with eight explicit platform skips.
- Independent source review found normal stop comparing a restored predecessor against the rollback intent's pre-restoration route. Withdrawal now derives the restored generation/history using the same canonical restoration calculation. Two real rollover failures, before and after native route publication, complete rollback and then stop the exact restored original through normal invocation, preserving publication history. These regressions pass in 194.87 seconds.
- The complete withdrawal/interrupted-stop/restart suite passes all 14 cases in 701.22 seconds. The current-base shared peer, release-impact, runner and packaging pass accounted for 167 passes and two explicit skips, with one stale workflow-shape assertion failing on the newly authored opt-in input. Updated that owning contract to retain the default diagnostics and assert the separate five-scenario/two-platform native matrix, two-scenario/two-platform contained matrix and native macOS refusal; all 65 scope tests pass. No required CI lane or production deadline was expanded.
- Fresh hosted-account Linux/Windows acceptance and native macOS refusal remain unrun until candidate publication. The earlier in-handle stable-read refusal remains unresolved: neither the successful instrumented passive rerun nor the parallel-reader probe establishes its cause or an accepted flake disposition. No merge, release, rollout, parent completion or saved-delegation gate is waived.

### 2026-10-18 - Final same-cell PR-owner peer integration

- Registered the PR owner in the canonical caller and target-peer rosters, synchronized all nine packaged boundaries, and added the PR package to the generator. The boundary scrubs the PR override namespace and rebinds only the exact target receipt's state root. Existing changefile coverage now includes Logger and Machines as additional generated consumers; the shared release-impact map explicitly charges Worktrees and the PR owner too.
- The PR CLI now loads that boundary and its packaged validator from its completed runtime's validated immutable snapshot, not the replaceable management payload. A physical-prefix regression exposed import machinery writing `__pycache__` into the snapshot and breaking its completion digest. The in-process loader now compiles the already-validated owned source without emitting bytecode; its standalone prefix adds `-B` so subsequent validator imports are likewise read-only. Pre/post physical runtime validation and canonical helper authorization remain intact.
- Physical invocation/runtime/peer regressions pass all 51 selected cases, including same and advanced management receipts, refusal of a missing snapshot helper without management/PATH fallback, and execution of a reserved bootstrap that leaves the snapshot untouched. The shared packaging/environment/release-impact contracts pass 43 cases; the actual dispatch peer-boundary lane passes 72 cases with 10 explicit platform/role skips, including the PR caller across two source-qualified cells. Module size, headless launch, installer and canonical vendor guards pass.
- A separate normal full-payload roundtrip passes in 107.58 seconds: the actual PR interpreter constructs its immutable prefix and calls a test-owned same-cell Worktrees echo backend, preserving Unicode stdin/stdout, stderr and exit 7 while discarding inherited tokens and PR routing overrides. The reverse canonical boundary enters the actual PR module and reports the explicit installation-local absent-owner health result, without starting an owner or mutating selection/journal/state. Full completion validation still passes afterward. Earlier harness timeouts reached the echo backend in roughly seven seconds but awaited EOF through an extra wrapper's implicit Windows standard-handle inheritance; explicitly binding that wrapper's Python stdin/stdout/stderr, as the canonical boundary itself does, fixes the fixture without a production deadline or stdio change.
- Added that roundtrip as a fifth fresh-account scenario per platform, making ten Linux/Windows native-account jobs plus four contained owner-boundary jobs and one native macOS refusal job. All remain unrun externally until the candidate is published. Peer integration is still local and unpublished; current-head review, native evidence, the unresolved stable-read observation, release/authorized deployment and the saved dispatch integration remain parent gates.

### 2026-10-18 - Native-account refusal and macOS fail-closed acceptance lanes

- Extended the existing fresh hosted-account Linux/Windows jobs with separate reachable/unreachable lease-only cases. Each performs normal full-payload init before starting the actual unproven legacy owner in the selected cell's state root, then invokes the completed target's real installer activation. Assertions retain pending state, original native liveness, selected marker/manifest/route and terminal journal custody, with no shutdown or duplicate spawn. Legitimate controller/capability rotation of an existing metadata-only terminal journal is not confused with owner/selection mutation.
- Exercised the same normal-installer refusal path locally for both rendezvous cases in disposable Windows profiles, plus the unsupported-native-evidence unit contract: all three pass in 193.18 seconds. The two original small-prefix Windows refusal cases also pass. The full-payload local profiles are not actual native-account evidence; the new hosted-account jobs remain unrun.
- Added a separate native macOS completed-interpreter job for explicit startup/instance refusal, bounded absent-owner stop, and cleanup/original-exit refusal without health control, signaling, spawning or selected-state mutation. An ordinary unit test injects unavailable native primitives to exercise the higher-level no-effects contract on this host; it passes, but is not macOS evidence. Native macOS governed operation remains unsupported until shared birth/exit primitives exist. Its fail-closed lane and the named macOS identity/recovery follow-up under #6286 remain open pending the actual native job; no platform exemption or positive macOS cutover claim has been taken.
- The opt-in workflow now has eight fresh-account Linux/Windows scenario jobs, four contained owner-boundary jobs and one native macOS refusal job. Its default eight derived diagnostics remain unchanged. All parent publication, review, release, authorized rollout and saved-dispatch gates remain open; the unresolved stable-read observation recorded below is still not waived.

### 2026-10-18 - Actual controller death and concurrent installer upgrade

- A completed-slot controller now exits abruptly, after its real `bind_successor()` persists passive custody but before endpoint binding. A separately captured native reference proves that original controller exited before a new guard holder recovers. This reproduced an endpoint-custody gap: reconciliation left the exact reserved passive child stranded despite its private launch receipt. Recovery now revalidates that receipt and live authenticated instance before binding the missing endpoint, without discovery fallback or new retirement authority.
- The expanded process matrix passes all 12 boundaries: reserved launch, passive binding, endpoint binding, writer release, read-ready adoption, completed-slot marker CAS before manifest publication, native route publication before custody capture, captured route, a committed journal write before its return, returned commit, activation before retirement, and committed recovery with unavailable predecessor health. The unavailable-health case stays committed with both native processes accounted for, then converges after a second guarded retry. Each case preserves the pending event/registration and retry payload, coherent selection, native publication history, exact obsolete-process exit and archived terminal custody.
- Three missing/foreign launch-receipt cases refuse before lifecycle control, with no selection mutation or claimed exit. The 12-boundary matrix plus these three cases passes in 752.47 seconds under the unchanged 1,200-second group bound. The existing and new recovery contracts separately pass all 11 cases. These are owned Windows process tests in disposable profiles, not native-account or shipped-full-payload evidence.
- The actual completed-target installer activation also passes with a pre-existing committed owner while two admitted register/unregister handlers, an accepted poll result, a real slow acknowledged callback subprocess and three queued legacy callbacks overlap the upgrade. Drain keeps predecessor writer custody and successor passivity; late mutations refuse. After release, final adoption retains the accepted registration, cancellation, poll event identity, unrelated subscriber, pending retry and wall-clock deadline (under 10 milliseconds of conversion drift), without overlapping callback generations. Original native exit and coherent target routing are verified. This composite passes in 93.22 seconds; production callback/cutover deadlines remain 30/120 seconds.
- Two earlier concurrency harness attempts failed and are not counted as product passes: one started its 25-second test gates before slow passive construction, and one incorrectly required a persisted remaining-time value to stay constant as time elapsed. The fixture now admits blocked work after actual passive readiness and checks stable identities plus the measurable wall-clock deadline instead. It does not extend any production or fixture gate.
- The passive-bind crash stays in the ordinary smoke portfolio. Other crash boundaries and the concurrent composite are explicit T3 cases with separate Linux/Windows jobs in the existing opt-in acceptance workflow; default diagnostic jobs remain unchanged. Those external jobs remain unrun. Native account/platform coverage, final peer integration, publication/review, release, authorized deployment and the parent effort's remaining gates are still open.
- A final two-case explicitly admitted run passes the concurrent upgrade again but refuses the passive crash recovery at the canonical JSON reader's stable-read guard during final predecessor attestation (`JSON document changed while it was being read`). This is an unresolved acceptance failure, not a waived flake or a passing combined run. Added test-owned, metadata-only diagnostics around that reader's existing refusal without changing its validation, plus an explicit-tier 4,096-read/eight-thread immutable receipt probe. The instrumented passive case passes in 45.42 seconds and the concurrent-read probe passes in 7.16 seconds, but neither reproduces or explains the prior refusal. Preserve this open Windows native-read observation under #6286 until exact evidence supplies a fix or an explicit tracked disposition; these reruns do not independently clear publication.

### 2026-10-17 - Normal same-path payload refresh and live-owner upgrade

- Added an explicit-tier Windows check using a private released directory-marketplace payload. Normal native init creates the first cell, the generated command starts a real governed owner, then a same-path management version refresh goes through normal init without direct receipt stamping, activation edits, manual snapshots or runtime edits. Assertions retain the original native exit reference, historical completion/archive, pending event identity, coherent target selection and native route history.
- Its first run built both complete payloads and refreshed the receipt, but exhausted the existing 120-second lifecycle deadline at final successor verification after original retirement. This is a failed full-payload upgrade, not covered by the earlier tiny-incumbent fixture's passing result. Fixed its cleanup command to use the supported top-level stop.
- Journal readiness now validates the actual health RPC inside its own runtime/native bracket rather than nesting a fully validating client followed by another bracket around a cached response. Ordinary client health still validates fully; the journal-only transport callback conveys no authority. Native exit capture opens the non-signaling exact-process reference before one bracketed health exchange and closes it on refusal. Production deadlines, pre/post governance/runtime/native checks and original-exit requirements are unchanged.
- Focused driver, recovery, journal, runtime and control/deadline verification passed 108 cases and exposed two fixture defects: an endpoint token shorter than the existing custody contract, and an error-message assertion that did not allow the earlier native reference refusal. After fixing only those expectations, the four affected post-RPC mutation/stale-token cases pass. The actual normal full-payload upgrade then passes in 464.09 seconds, including its governed cleanup. Fatal lint, module size, headless launch, installer and diff contracts pass. This is contained Windows process evidence, not an isolated OS-account/native Linux or two-independent-shipped-cell claim; the complete matrix and all parent gates remain open.
- The base CLI, notification, drain and writer group passes 220 cases. The larger combined owner-boundary group exceeds the unchanged 1,200-second runner limit without an assertion failure; partial progress is not counted as a suite pass. Its remaining accounting is being split into smaller named groups under the same limits, not waived or retried as a presumed flake.
- The actual two-shipped-cell check passes in 681.01 seconds. Two full normal installs and generated starts in one disposable Windows profile produce distinct source-qualified cells, native owners and endpoints. Each sees only its pending registration, cannot cancel the other's registration, and refuses crossed context for health, stop, restart/recovery and init. Direct control at the other real endpoint refuses foreign transaction authority even with that endpoint's own token and exact target instance. State, route and journal stay unchanged; stopping the first proves the second remains the same live effectful writer. Both test-owned original processes exit through governed cleanup.
- Added opt-in fresh native-account jobs to the existing manual installation-context acceptance workflow, separate from its unchanged default diagnostics. Linux and Windows run normal full-payload upgrade and two-shipped-cell isolation in independently discarded hosted accounts without HOME/USERPROFILE substitution. The fixture rejects non-hosted runners, substituted profiles and pre-existing config/installation state. Workflow syntax and fatal lint pass; these native jobs have not yet run, so contained-profile results do not close that platform/account gate.
- The complete current contained Windows inventory is now accounted for: 597 passing cases and two platform skips across disjoint bounded selections, including explicitly admitted real construction. The focused runtime/journal/driver/recovery/deadline group accounts for 110 cases; base behavior accounts for 220; smaller owner-boundary groups pass 50, 81 and 40; normal full-payload upgrade and two-cell isolation pass one each; the final complement passes 94 with two skips. An explicit skip-report run identifies both as the POSIX-only stale-install-lock/reaper cases (`POSIX bash not available`), not disabled construction or an unexercised owner gate. The timed-out combined batch is superseded by those fully accounted smaller groups, not reported as passing. Actual controller-death and concurrent-upgrade evidence, native account/platform lanes, final peer integration, review, release and authorized deployment remain open.

### 2026-10-17 - Operator-selected lease-only ownership refusal

- The pre-cutover daemon's rendezvous records contain a PID, token and wall-clock start time; health does not attest installation, native process-start identity or completed-slot ownership. The singleton library explicitly labels its PID record diagnostic, not lock ownership. Native exit references prove exit, not permission to control that process.
- The operator selected `refuse-unprovable-owner`: retain fail-closed activation and revise the reviewed first-upgrade prerequisite instead of adding an exact-owner authorization/configuration surface. The revised plan requires a real reachable/unreachable lease holder to remain running with pending state unchanged, without installer shutdown, signaling, selection mutation, route publication or a second owner.
- Two real Windows process regressions pass for reachable and unreachable legacy rendezvous records. They run the actual legacy CLI/daemon with its OS singleton lease and pending acknowledged event, then execute installer activation in the actual completed target. The installer refuses without shutdown or spawn, and pending bytes, legacy endpoint and absent target selection/route stay unchanged. Test-owned cleanup alone requests graceful shutdown and waits on the original daemon's pre-captured native exit reference. The initial fixture mistakenly captured the Windows venv redirector PID; it now binds the actual original daemon's native identity announced by its test-owned process before installer control. This is contained process evidence, not an isolated-OS-account or native Linux claim.
- This decision does not relax any attributable governed-upgrade, concurrency/crash, shipped-cell, native-platform/account, review, release, deployment or parent close-out gate. No Plan or Validation checkbox is closed by this scope revision.

### 2026-10-16 - Native payload bootstrap and attributable command dispatch

- Added native governance admission before Python selection, legacy installer writes and session-start reconciliation. Missing/false policy preserves legacy first-touch bootstrap without Python. Governed requests, scoped overrides without source provenance, invalid receipts and maintenance/refusal states never fall back to the legacy root. The shared legacy bootstrap-check files remain unchanged; a narrow admission wrapper keeps them out of governed cells without creating another background reconciler.
- Generated version-2 required-context payload commands now dispatch only through this payload. Fresh policy-enabled cells with genuinely absent legacy state use canonical receipt stamping and generation-pinned activation; existing unactivated receipts and migration-required state demand an explicit decision. Explicit bootstrap refreshes only its already-active source's management version through canonical stamp/activation CAS, then uses the normal immutable snapshot/build/activation installer.
- Runtime dispatch requires coherent current/LKG markers, schema-3 manifest, canonical slot ownership, immutable snapshot and build completion. It never scans for a newest slot or repairs partial metadata. Older owned completed selection remains usable until installer-driven replacement. Caller arguments, outside-payload CWD, streams and native exit status are preserved.
- The real native first-touch check initially exposed a test-fixture build CWD longer than Windows' native 260-character limit (271 characters, versus 190 for the ordinary account profile). A short factory fixture keeps the native lane below that limit without changing production behavior. The next run exposed genuinely lost stdout in the hidden follow-on subprocess: explicit native standard handles now preserve stdout/stderr, rather than relying on implicit inheritance with `CREATE_NO_WINDOW`.
- Focused installer/bootstrap validation passes 62 tests with explicit T3 execution, including real normal init, canonical activation, real package construction, cold selection without an owner, generated command output and nonzero target status, native no-Python admission, source/generation refresh, missing/foreign receipt refusal, corrupt selection refusal and legacy-root isolation. This is contained Windows fixture evidence, not a native isolated-OS-account or two-shipped-cell claim. Full owner, lease-only transition, concurrency/crash matrix, platform/account lanes, peer-roster integration, review, release, authorized rollout, saved dispatch integration and parent close-out remain open.

### 2026-10-16 - Native target status and actual-control attestation follow-up

- Follow-up verification found that Windows `os.execv` does not preserve the executed target's nonzero status. The previous entry's replacement-based implementation is therefore superseded, not accepted as a complete activation contract. The installer now waits for the real completed target's own bounded journaled lifecycle through structured native subprocess arguments, with no extra construction job around that controller. Build helpers keep their existing deadline and job containment. Outer test containment and canonical daemon breakaway suppression remain unchanged.
- Actual target subprocess tests reject exit 7 even when stdout contains success-shaped activation JSON, and reject incomplete or malformed completion output. A subsequent combined run passed those three cases and normal cold installation but again exhausted the real upgrade's existing 120-second cutover under load; it is not classified as a waived flake.
- Nonterminal lifecycle calls now bracket their actual authenticated control acknowledgement with immutable runtime and native-instance validation, instead of fully attesting a separate preliminary health RPC and then sending an unbracketed control. The exact acknowledged post-transition snapshot confirms drain without another redundant RPC; older acknowledgement shapes still require a fresh health response. Terminal shutdown retains its separate preflight and pre-captured original-exit proof. Authority, receipt/native rechecks and the same caller deadline remain required; no proof cache or larger production budget was introduced.
- The final actual different-runtime normal-installer upgrade passes in 323.83 seconds, including real package construction, native predecessor retirement, durable successor survival after installer return, unchanged pending event identity and governed cleanup. All affected physical-invocation, writer, control/deadline, policy-off cleanup and installer-refusal cases then pass: 110 cases with one explicit-tier construction skip in 539.24 seconds. These include post-control native-change/deadline refusal, lifecycle-lock authority and same-response drain confirmation. Fatal lint, module-size, headless launch, thirteen installer contracts and staged/unstaged diff checks pass.
- Normal bootstrap/dispatcher work is still next. The shipped two-cell, real OS-account/native-platform, remaining crash/concurrency, complete-suite, exact-head review, release and authorized deployment gates remain open. No Plan or Validation checkbox is closed, and this remains the same unpublished adopter, not a separate groundwork PR.

### 2026-10-16 - Real different-runtime installer upgrade and durable activation

- Continued the same unpublished adopter with an actual different-version target, built through normal snapshot preparation and the shared engine rather than a preassembled target slot. The predecessor is a physically attested, archived governed owner; the current management receipt advances canonically while the historical runtime, native instance and original archive remain immutable. A future-retry acknowledged event proves registration, event and payload identity survive the handoff without contacting a real PR or callback target.
- The first combined fixture exceeded its newly authored 180-second test allowance before exposing a result. Timing showed approximately 44 seconds of native fixture setup, approximately 96 seconds of previously measured package construction, and the existing 120-second cutover plus graceful stop. The composite test now budgets those separate bounded operations explicitly; no production cutover/native-exit, Windows group or aggregate deadline changed. Diagnostic runs then exposed real cutover exhaustion, not an accepted flake. Profiling identified repeated complete canonical runtime validation nested within the same transition. The adapter no longer duplicates the proof already performed by physical invocation capture or authoritative control validation. Writer authority still runs under its lifecycle lock and is rechecked after quiescence before release; no authority cache, test-admission bypass or permissive validation flag was added.
- After that correction, installer activation committed successfully but its nested construction job killed the new owner on exit in contained tests. The normal installer now replaces its management entry with the actual completed target. Construction subprocesses remain bounded and job-owned, and the outer supervisor still contains the target and honors canonical breakaway suppression. The fix does not disable containment or add an alternate daemon launcher.
- The explicitly admitted real normal cold-install and different-runtime-upgrade cases pass together: two cases in 414.11 seconds. Upgrade assertions verify original native exit, live exact successor after installer completion, writer/read/effect readiness, coherent markers and manifest, preserved native generation/history/previous route, unchanged historical archive, and the same pending registration/event payload. Governed cleanup succeeds. The subsequent authority/physical-invocation/writer/policy-off-cleanup/installer-refusal group passes 99 cases with one explicit-tier construction skip in 471.66 seconds. Fifteen final deadline/control-routing cases pass, including direct proof that adoption refusal and post-drain release refusal occur under the writer lifecycle lock and preserve the required native lease. Earlier timeout and live-upgrade failures are not counted as passes. Fatal lint, module-size, headless-launch, thirteen installer contracts and diff checks pass.
- This remains contained Windows fixture evidence, not two shipped independent cells or a fresh OS-account/native Linux proof. Normal payload bootstrap/dispatcher integration, lease-only transition, remaining crash/concurrency and platform matrices, complete current-owner suites, exact-head review, promotion, authorized deployment and saved dispatch integration remain open. No Plan or Validation checkbox is closed.

### 2026-10-16 - Canonical snapshot preparation and normal cold installer

- Continued the same unpublished adopter after saving exact stop/restart custody. The owning cell installer can now prepare its immutable input rather than requiring a test to preassemble it. Development-tree input composes the canonical release materializer's existing per-consumer library, installer-engine and launcher-asset functions. Published standalone payloads must already be self-contained. No independent materializer, Index service coordinator or alternative deployment framework was copied.
- Snapshot preparation rechecks source content and receipt identity, derives a content-addressed name, claims a new directory exclusively, checks final bytes, and publishes canonical provenance last. Existing incomplete, changed or unattributed snapshots refuse without overwrite; an interrupted exposed candidate stays explicit repair custody. Only the invocation's unexposed staging tree is disposable. Installer `install`, `update` and `provision` retain explicit prepared input support and otherwise derive their version and snapshot from the management payload.
- The metadata/installer group passes 24 cases in 85.42 seconds, with two explicit-tier construction cases skipped rather than credited. Explicit admission then passed the prior real package-build case but exposed Windows access denial while renaming a just-copied snapshot tree. Snapshot publication now uses exclusive directory production and provenance-last visibility instead; it does not retry past a failed ownership check or replace an existing snapshot. The subsequent explicitly admitted normal prepare/build/cold-activation case passes in 95.95 seconds, starting no owner and touching no legacy runtime or global binstub.
- The combined explicitly admitted installer/snapshot/activation regression group passes all 73 cases in 548.19 seconds. Subsequent receipt/package-version refusal hardening and the complete snapshot group, including interrupted-copy refusal and real automatic cold activation, pass all seven cases in 135.67 seconds. Fatal lint, module-size, headless-launch and install-contract checks pass. Payload-launcher bootstrap, actual different-runtime live upgrade, the lease-only transition and full shipped-cell/native-platform acceptance remain open; no Plan or Validation checkbox is closed.

### 2026-10-16 - Exact governed stop and retained-generation restart

- The same unpublished adopter now routes explicit-context stop through private withdrawal custody referencing the exact archived terminal owner. It keeps the whole native cutover guard, captures the original native exit reference before authenticated control, and requires original exit plus actual writer availability before exact native route withdrawal. The owner revalidates governance after quiescence and before releasing the writer. No legacy shutdown, PID-only cleanup, synthetic successor, parallel owner or shared live service was used.
- Withdrawal preserves sticky publication history and a stopped-generation witness. Only a completed archived withdrawal permits later cold selection to reuse that route. The shared pre-adoption orchestrator now distinguishes an adapter-selected incumbent from a retained `previous` witness; it still drains a previous endpoint when the adapter actually selects it. Actual stop/restart advances generation without erasing history.
- Stop, restart, protected publication, interrupted shutdown, lost acknowledgement, replay, entry and recovery cases pass together: 25 cases in 1008.51 seconds, within the unchanged 1200-second group/1800-second aggregate bounds. An initial restart failure exposed the shared selection bug and was fixed. A newly authored lost-ACK fixture initially asserted immediate exit instead of waiting on its pre-captured native reference; a subsequent indentation error prevented collection. Both were corrected before the passing run. These failed attempts are not counted as passes.
- The complete canonical ZDD suite passes 633 cases with 28 platform skips in 46.48 seconds. The subsequent final withdrawal group passes all 12 cases in 648.52 seconds, including compare-before-replace custody and refusal of another installation's native guard before writing. Headless-launch, module-size, install-contract, fatal Python lint, canonical installation-context, vendor-consistency and diff checks pass. Existing complete private archives are durably reconfirmed rather than silently trusted after an uncertain write.
- Actual different-target installer upgrade, normal bootstrap and snapshot preparation, first lease-only transition, complete concurrency/crash and shipped two-cell matrices, native Linux/account evidence, review, promotion and authorized deployment remain open. No Plan or Validation checkbox is closed, and saved dispatch delegation remains gated.

### 2026-10-16 - Receipt-advance discovery and explicit installer integration

- Continued the same unpublished adopter. Entry and discovery now compose current custody with the established archived-terminal reader: a fresh management receipt can retain only an already-archived, same-cell terminal incumbent. Pending or unarchived historical custody still refuses unchanged. Three actual installed-subprocess cases prove retained endpoint/no replacement and both refusal shapes.
- First-publication recovery now retries only an exact reserved, effect-free read-ready successor whose conservative native history matches its durable intent. It keeps the original generation and routing-owned promotion epoch; foreign history remains pending and untouched. Five actual-process recovery cases pass, including protected-write interruption and foreign-history refusal. An earlier interrupted run emitted two results and did not complete; it is not counted as a pass.
- Added explicit PowerShell/Bash installer cell dispatch before legacy side effects, canonical metadata adapter actions and immutable completed-slot construction through the shared installer engine. Actual activation executes in the completed target, checks physical running identity and uses the existing whole-transaction owner guard before metadata locks. Cold activation selects target markers/manifest only; known-target replay retains its owner, and an unattributed real writer blocks cold selection. No shared live instance was stopped or replaced.
- Real package construction found an uncached build backend and then a PEP 440 normalization mismatch in the initial metadata-version comparison. Restored the missing backend through the package manager into a contained build cache and made the operative check use the loaded package version, matching physical runtime attestation. The newly authored T3 construction case initially inherited the fast 30-second default and exceeded it; it now explicitly uses the existing 180-second native-lifecycle test budget. Existing operative deadlines, the 1200-second Windows group bound and 1800-second aggregate bound are unchanged. This is not a clean-room/no-cache proof.
- The integrated explicit-tier entry/recovery/installer group passes all 82 cases in 499.39 seconds, including real signed-venv package construction, immutable completed replay, native installer entry and governance refusal. A subsequent contained fresh-cache package construction/replay case passes in 38.33 seconds without the explicit warm-cache override; it still is not a fresh-OS-account or full shipped-cell proof. Fatal Python lint, module-size, headless-launch, install-contract and canonical installation-context/vendor consistency checks pass. A test-fixture argument error in an earlier run was corrected; that red run is not represented as green.
- Normal bootstrap/snapshot preparation and installer upgrade integration, governed stop, first lease-only transition, the full crash/concurrency matrix, actual shipped independent cells, native Linux/macOS disposition, full current-owner suites, review, promotion and authorized deployment remain open. The saved dispatch integration remains blocked behind those gates. No Plan or Validation checkbox is closed by this unpublished slice.

### 2026-10-14 - Operative owner integration, still unpublished

- The same full adopter now wires the public shared cutover orchestrator to reserved actual-child custody, private authenticated TCP controls, canonical runtime selection, exact native exit and the owner's independent commit/archive gate. Cold on-demand startup and live rollover use the same held whole-transaction guard. The shared result, best-effort activation and cleared breadcrumb are not completion evidence.
- The explicit-context CLI captures its actual completed running slot before discovery. Its passive process publishes only the exact reserved private launch receipt; ordinary no-context CLI behavior remains unchanged. Pending reconciliation never spawns a replacement: it finishes a committed target forward or proves the known successor's exit before restoring selection and resuming a predecessor. An unresolved reservation and an actual writer without discovery both block cold selection.
- Native publication intent retains exact generation and existing promotion history. The first routing-owned epoch is captured through public guarded endpoint validation, not predicted or rewritten. Reserved successors cannot commit without captured native promotion history. Recovery can capture the exact intended first publication after a lost acknowledgement, while foreign history remains pending and untouched.
- Early native runs exposed an exit-wait API bound mismatch, redundant activation and deadline failures in compound fixtures and combined runs. Exit waits now honor the native 30-second maximum. Health attestation brackets the actual authenticated wire exchange and rechecks native birth afterward; native exit capture similarly validates around the real exchange instead of nesting a second complete health attestation. Passive publication retains its native/runtime/governance checks without repeating physical invocation capture at every nested helper. Receipt absence is only a cheap readiness hint before full custody validation. No test or operation deadline was raised, and no authority cache, permissive validation flag or admission bypass was added.
- The current combined operative/legacy CLI/deadline group passes all 50 contained Windows cases in 615.69 seconds with unchanged bounds, including real cold start, rollover, rollback cleanup, native reassertion, the actual module's private passive entry, no-spawn recovery, live-writer absence refusal and foreign-history refusal. The subsequent journal/custody regression group plus lost-publication recovery passes 65 cases in 1149.78 seconds, including refusal to recover a committed reserved successor after its captured native history is removed. An earlier focused 15-case run also passed. Earlier combined red runs remain recorded observations, not known flakes or waived gates; the remaining affected suites and full current acceptance matrix still need accounted results.
- The existing authenticated TCP lifecycle group passes 11 cases in 178.93 seconds; disabled-governance passive cleanup passes 16 in 595.41 seconds. A subsequent real native commit-forward case passes in 111.63 seconds with an explicit assertion that the shared recovery breadcrumb is cleared only after the owner's terminal proof and archive. Leaving that committed shared breadcrumb behind would block the next otherwise-valid upgrade.
- Installer provisioning/activation, governed stop and first lease-only transition, the complete crash/concurrency matrix, actual shipped independent cells, native Linux proof, current-head review, release promotion, authorized deployment and the saved dispatch integration remain open. No Plan or Validation item is closed by this unpublished integration.

### 2026-10-14 - Recoverable canonical cold-selection metadata

- Continuing the same unpublished full adopter. `WatchSelection` now composes the journal's whole native transaction guard with canonical completed-slot marker CAS and nested genesis/install metadata locks. The target manifest is an exact-byte, atomic durable nonsecret write; native routing remains exclusively routing-owned. No separate service coordinator or deployment framework is introduced.
- Precommit interruption at either marker or the manifest can recover to captured prior or target metadata. Rollback preserves exact original bytes rather than normalizing a prior marker, while committed cold metadata repairs only the known target forward. Unknown metadata and disabled governance refuse. The installer still owes actual incumbent-absence proof before selecting this path; this helper is not a discovery or native-writer probe.
- Twelve contained Windows cases pass against real canonical completed slots, including cold no-spawn/no-route/no-state assertions, every marker/manifest interruption in both recovery directions, committed repair, foreign metadata/governance refusal and a concurrent metadata replacement that must not be overwritten. An initial pair of test failures named `current` rather than the actual `current-version` file, so did not inject their intended fault; correcting that selector makes the interruption exercise the real canonical boundary. No product validation was relaxed.
- Production driver/CLI/installer wiring, serialized on-demand startup, lease-only upgrade, the complete live crash/concurrency matrix, actual shipped independent cells, native platform lanes, current-head review, release promotion, authorized deployment and the saved dispatch integration remain open. No Plan or Validation item is completed by this internal selection seam.

### 2026-10-14 - Attributable native rollback-route restoration

- Continuing the same unpublished full adopter, without a separate groundwork PR or operative entry-point/installer claim. The owner journal now captures restoration intent inside the public native routing CAS before publication. Restoring the predecessor retains ZDD's monotonically newer generation and sticky publication history, rather than overwriting the original raw snapshot. Original prior bytes remain immutable recovery evidence.
- Recovery can recognize only that exact attributable restored route, or retry the exact conservative-history intermediate write. Successor native exit precedes restoration intent and predecessor resumption. A matching owner reannouncement without intent, displaced CAS, changed generation/history/previous row, or changed prior manifest cannot authorize resumption or terminal completion. Terminal archive and subsequent transaction checks use the same route custody.
- Eleven native Windows routing/process regressions pass, including before/after-flip restoration, interruption before route persistence, conservative-history persistence, lost final-publication acknowledgement, archive/next-transaction custody and route drift. This is actual shared public routing integration, not synthetic generation equality; it is not a complete operative driver or shipped-cell acceptance claim.
- Added shared read-only original-exit observation for controller loss. Windows distinguishes native OpenProcess nonexistence from query errors or access denial and requests no termination rights. Linux composes a pidfd with strict bounded birth evidence; missing or malformed evidence never becomes tolerant-diagnostics absence. Unsupported platforms refuse. Captured graceful-exit references retain their existing contract. The changed canonical shared selection passes 57 tests with three native-Linux-only skips on Windows.
- Six actual owner recovery cases pass: a live original blocks observation, an already-exited original needs no lost handle, native writer reacquisition still refuses an occupied lease, unknown/denied/unsupported evidence cannot write exit custody, and policy-off cleanup can reconcile the exact already-gone private reserved child before ordinary binding without authorizing resumption or terminal completion.
- The first combined journal/custody/rollback selection exceeded the unchanged 1,200-second aggregate wall-clock bound after 64 completed items and has no suite-completion result. Validation was partitioned by those existing test modules rather than increasing budgets or bypassing admission: 40 ordinary journal, 24 terminal/launch custody, 14 final deadline-aware native restoration, and 16 policy-off cleanup cases pass. The complete changed shared ZDD suite passes 623 tests with 28 platform skips on Windows. Fatal lint, module-size, headless launch, thirteen installer contracts, canonical generation and vendor checks pass. Production wiring, shipped platform lanes and all parent completion gates remain open.

### 2026-10-14 - Private launch custody and explicit terminal history

- Continuing the same unpublished full adopter, without advertising peer/owner readiness or enabling the production entry point. A physically attested passive child can publish its exact pre-reserved native instance and authenticated endpoint into an exclusive, private, crash-durable launch receipt before controller binding. Recovery reads only that known transaction/cell receipt, rotates controller capability, and still requires authenticated exact passive health before binding or cleanup. Reusing the reserved UUID for another real child does not authorize cleanup of that different native instance.
- Journal, launch and archive files require current-account ownership and private permissions: POSIX `0600`, or a protected owner/SYSTEM-only Windows DACL. Temporary files are protected before writing capability bytes. Permission/link failures refuse, not repair or follow foreign custody. Endpoint tokens remain outside public health and routing evidence. Launch/history publication never replaces existing custody.
- A reserved but unbound child blocks both predecessor resumption and terminal rollback. Terminal phases also require the obsolete instance's recorded native exit. Explicit archival retains immutable completed custody before a next transaction can replace the live journal; pending, malformed, unarchived, foreign-root or future-generation records refuse. After a canonical management-receipt update, only already-archived terminal history in the same known cell can seed a fresh governed transaction. Pending recovery and controls retain strict pinned-generation checks. The next transaction preserves its exact surviving owner and private endpoint.
- Lifecycle preflight, capability lookup, control and confirmation now consume one caller budget. The existing coalescing client gains an optional monotonic whole-transport completion budget, without changing ordinary calls' server-deadline grace. All 43 shared coalescing tests pass, including real partial-TCP-response deadline proof; the existing deliberate early-serve failure test still emits its expected thread-exception warning.
- Current contained Windows proofs: 47 custody/privacy/budget/physical-invocation cases, 27 real TCP/control and policy-off cleanup cases, 110 ordinary journal/runtime/installation cases, then 37 final custody/privacy/budget cases after the additional exact-exit, predecessor-route and duplicate-native-child fences. Fatal Python lint, module-size, headless launch, thirteen installer contracts, canonical installation generation and vendor agreement pass. These are targeted internal-seam proofs, not a current full-suite or actual shipped-cell acceptance claim.
- One earlier focused run had 29 passes and a fixture-setup refusal, `invalid (activation-invalid)`, before spawning its cleanup child. The isolated case and subsequent larger selections pass, but the original refusal is not classified or silently waived. The disposable installed-slot fixture now preserves the canonical refusal cause without retrying, suppressing it or changing production validation. Retain this observation under #6286's outstanding native/full acceptance until resolved or explicitly transferred.
- Before operative integration, reconcile the shared native CAS rollback's new route generation with owner recovery custody; do not overwrite native publication history merely to restore byte-identical prior routing. Actual driver/CLI/installer selection, cold/on-demand startup, lease-only upgrade, complete interruption matrix, shipped independent cells, native platform lanes, current-head review, release promotion, authorized deployment, saved dispatch integration and parent closeout remain open. No Plan or Validation box is completed by this internal continuation.

### 2026-10-14 - Restricted policy-off passive cleanup

- Continuing the same unpublished full adopter. Canonical receipt/build identity reads are now distinct from normal lifecycle authority; ordinary mutation still requires authoritative enabled governance. A cleanup-only transaction holds the same native cutover guard and refuses malformed policy, maintenance, missing activation and changed identity.
- The controller reserves the exact random child identity before spawning. Authenticated cleanup can recover that child after a pre-bind crash, or stop an already-bound passive, only with the exact journal capability, live native controller custody, unchanged prior selection and actual no-writer/no-effects health. Close revalidates authority, and native exit is separately captured before control and recorded afterward.
- Missing/default-off and explicitly disabled policy have real native subprocess/TCP proofs for both bound and pre-bind cases. The contained cleanup/governance selection passes 21 tests. An initial run's four failures occurred only at the final post-exit assertion: a genuinely closed socket reports the existing `DaemonUnavailable` boundary rather than a remote refusal; the assertion was corrected without changing product behavior. Cleanup after writer adoption/publication/commit, stale capability/instance/birth, changed selection/receipt, missing native guard and unreserved discovery all refuse.
- Recorded cleanup preserves the pending transaction phase and prior selection, including drain/release intent. It does not fabricate active predecessor health, terminal rollback, promotion or effect resumption. This is internal groundwork within the full adopter, not operative main/installer or shipped two-cell acceptance. Durable endpoint custody, production wiring, full interruption/upgrade proof, native platform lanes, review/release/deployment and the saved dispatch integration remain open.
- The ordinary-boundary regression selection passed 160 tests and exposed one exit-reference sequencing defect: the new wrapper queried health twice before native capture. It now preserves exactly health-before/open/health-after and closes the reference on later refusal. The final exact-edit runtime/cleanup selection passes 35 tests, including changed ownership after native capture and refusal to advance a cleaned-up successor even when policy is re-enabled. Fatal lint, canonical installation/vendor checks, module-size, headless launch and all thirteen installer contracts pass. This is not a new full-suite or shipped-cell acceptance claim.

### 2026-10-14 - Physical invocation and authenticated lifecycle transport

- Continuing the same unpublished full adopter, without a new groundwork PR, peer-roster advertisement or production CLI/installer activation. Physical invocation checks require the actual completed venv/interpreter, loaded owner/shared-runtime library placement and package version. The Windows factory binds actual invocation roots and composes the canonical same-cell authentication prefix without registering or bypassing the peer roster. A current action refreshes management receipts in its known cell while preserving historical incumbent runtime evidence.
- Added an internal owner/client adapter over the existing authenticated loopback TCP protocol. Exact-instance, transaction capability, permitted phase and native controller custody authorize every writer transition. Coalescing uses the entire authority-bearing payload, not an untrusted caller key; tests independently vary each authority dimension. Refusals and unexpected owner failures produce explicit sanitized wire errors. Quiescence does not terminate the listener, and retirement acknowledgement is not captured native exit evidence. Final read-ready adoption stays separate from durable-commit-gated effect activation.
- The final contained Windows invocation/transport selection passes 26 tests. Disposable non-editable native venvs prove actual interpreter/module placement and reject base interpreters, linked slots, foreign loaded modules, missing completion, wrong package versions, root relocation and disabled governance. Real authenticated TCP/child-process tests prove effect-free adoption, commit-gated activation, wrong-capability and ordinary-shutdown refusal, and graceful rollback successor exit. Non-finite control payloads receive explicit refusals before coalescing. The rollback fixture correctly refuses terminal completion without a verified active predecessor; it does not fabricate that proof.
- One complete multi-step TCP transaction exceeded the original 90-second per-test budget under Windows I/O load after first passing; only that test now has a measured 180-second bound. Other new tests retain 90 seconds, and caller control deadlines remain finite. Fatal Python lint, module-size, headless launch and all thirteen installer-contract checks pass. The separate existing daemon/quiescence/native-writer regression selection passes 93 tests.
- Actual production entry-point selection/control, durable endpoint custody, default-off passive cleanup, cold/upgrade and crash recovery, installer/shipped two-cell integration, native Linux/macOS lanes, current-head review, release and authorized deployment remain open. No Plan or Validation item is completed by this internal slice; saved dispatch integration and parent closeout remain blocked on the full adopter.

### 2026-10-14 - Completed-runtime evidence and durable owner control journal

- Continuing the same unpublished adopter work, without enabling a production listener, installer or peer. Completed-slot evidence composes the canonical snapshot, ownership and build receipts with current installation governance, including a historical owned incumbent after canonical activation revalidation. Exact instance evidence binds that runtime, the native PID/birth token and a random instance ID; native exit references are captured before graceful control and grant no signaling authority.
- Added an independently durable owner journal binding current receipt generations, prior marker/manifest/route bytes, completed target, exact instances, controller identity and random transaction authorization. Recorded phases are control intent, not successful writer-transfer evidence. Commit requires coherent selection and an exact effect-free read-ready writer; durable commit precedes effects and permits only forward recovery. Rollback resumption requires captured exact successor exit and restored prior selection.
- Receiver validation requires current governance, exact instance/process/runtime, the permitted phase and transaction capability, plus native controller guard custody. Recovery rotates the capability under that guard before new control. A held caller guard is revalidated without nesting a borrow inside an orchestrator callback through the shared read-only `require_owned()` seam. All eleven affected ZDD consumers declare their patch release impact.
- The stable contained Windows owner suite passes 348 tests with two existing shell-availability skips. Real test-owned subprocesses prove receiver PID binding, native controller contention/refusal after release, capability fencing and captured graceful successor exit before rollback resumption. Dedicated tests refuse wrong completed-runtime/instance evidence, incoherent selection, governance changes, linked or oversized journals and ambiguous commit acknowledgement. These are internal boundary and IPC proofs, not actual shipped owner/cell or full interruption-matrix acceptance.
- Full validation exposed Windows' distinct `lstat` creation-time versus `fstat` change-time semantics. Bounded journal reads now compare each API's complete metadata with itself while retaining cross-API file identity, size and modification-time checks; regressions accept the stable API difference and reject actual metadata change. The measured Windows suite takes 850.50 seconds, so only this plugin's Windows aggregate budgets are raised to 1,200/1,800 seconds; individual deadlines, Linux and unrelated plugins remain unchanged, and explicit limits still win.
- Fatal Python lint, canonical installation-context generation, vendor agreement, headless launch, installer contracts and module-size checks pass. The real materialized ZDD vendor passes 152 guard/pre-adoption tests with five platform skips; two runner tests confirm the narrow budget and override precedence. Actual invocation-root validation, lifecycle transport, selection reconciliation, default-off passive cleanup, installer integration, native Linux/macOS lanes, shipped cells, review/release/authorized deployment and saved dispatch integration remain open.

### 2026-10-14 - Canonical owner binding continues within the adopter work

- The unpublished adopter now packages the canonical installation-context bootstrap and Python fragments through both generator rosters, with byte-identical generated copies. Its ZDD dependency resolves from the canonical shared library, not a plugin-private vendor. This does not enable cell invocation, advertise the PR owner as a peer or change ordinary legacy startup.
- The internal installation binding pins exact plugin/cell/payload identity, generations and roots. It refuses changed receipts and requires current authoritative default-off governance, active installation/namespace and same-environment activation. The full-operation transaction uses the canonical run-root cutover guard, with validation before and after native acquisition; the shared orchestrator must borrow it. Completed-runtime, exact-instance and durable transaction authorization remain separate obligations.
- The focused contained Windows contracts pass 29 tests. Canonical stamping and activation APIs establish the disposable fixtures; tests cover foreign identity, disabled/missing/malformed policy, receipt/root changes, governance reversal during acquisition, body failure, inherited-binding refusal and two independent cell roots. A real test-owned subprocess contends for the same native transaction lock, then acquires after release. These are binding/exclusion proofs, not actual shipped two-cell owner or crash-recovery acceptance.
- The complete contained Windows owner suite passes 277 tests with two existing shell-availability skips, including 34 receipt-binding contracts after maintenance and linked-receipt coverage was added. Fatal Python lint, canonical generation, headless launch, the installer contract and the 1,000-line module cap pass. No service was deployed, and the native Linux/shipped owner lanes remain outstanding.

### 2026-10-14 - Native exit proof merged; owner writer adapter continues

- #6720 merged into `dev` at `ebd31b613c44b2068335043b8c956aeb545a8838` after exact-head approval `5520975016` with no open findings, resolved documentation feedback and successful current-head CI `38594827798`. Its native Ubuntu canonical ZDD lane passed 620 tests with four stress-lane skips, including real graceful-exit, high-descriptor and inherited-parent-lock cases. The shared reference has no signaling capability and no production service opt-in. Receipt/governance, exact-instance authorization, owner recovery and deployment remain open.
- The owner adapter now has local, unpublished writer-handoff work. An inactive owner must acquire the real installation-scoped lease before final-state reload, remains read-ready without effects until separately authorized, and rechecks authorization after complete drain before releasing the writer. An incomplete drain retains the lease and original registry; rollback after transfer reacquires and reloads rather than resuming a stale snapshot. Failed state reads or incomplete close retain accountable native ownership. This is not yet the receipt/journal/control/installer integration and does not complete the adoption prerequisite.
- Sixteen initial contracts failed against the missing adapter. The coupled contained Windows watch-core selection passes 170 tests. Two fixture assertions initially sampled an actively polling snapshot: final-state comparison now occurs after predecessor quiescence and before resumption starts effects, preserving the actual handoff contract rather than treating a legitimate later baseline observation as corruption. Explicit state roots do not mutate the ambient legacy override or permit cross-root writes. Full-suite validation, native subprocess proofs, exact transaction authorization, coherent recovery and shipped-payload adoption remain gates.
- The complete contained Windows owner suite passes 243 tests with two existing shell-availability skips; fatal Python lint passes. A real test-owned subprocess keeps its lifecycle alive after releasing the writer while the successor acquires and reloads its final state, without touching shared services. Four inherited-process guards reject before parent object access, and four incomplete authority-result cases refuse native acquisition. This proves the local writer boundary, not receipt-proven rollback, native Linux adoption, installer activation or the complete governed cell transaction.

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
### 2026-10-10 - Notification prerequisite merged

- #6019 merged as `a936d3b39` after current-head review with zero open findings and successful required CI. Full contained notification coverage reached 158 passing tests.
- Fixed the directly blocking dispatch CI job budget under #6052 without skipping tests; the corrected CI completed successfully.
- Pulled forward onto the merged source. Beginning negotiated dispatch integration; the effort remains active through deployment and closure.

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
