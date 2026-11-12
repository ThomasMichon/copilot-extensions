# Durable PR-Watch Delegation

- **Slug:** `durable-pr-watch-delegation`
- **Repo:** copilot-extensions
- **Branch(es):** Separate proposal, notification-contract, and integration PRs against `dev`
- **Created:** 2026-10-09
- **Status:** Active
- **Vision:** `visions/plugins/agent-dispatch/README.md` / `hibernate-the-wait`; `visions/plugins/agent-worktrees/pull-requests/README.md` / `durable-shared-pr-transition-subscriptions`; `visions/plugins/agent-pull-requests/README.md`; `visions/machine-fleet/README.md` / `single-operator-fleet`
- **Author-publication intent:** `agent-pull-requests` vision /
  `complete-author-publication-and-metadata`,
  `delegated-source-with-preserved-custody` and
  `one-supported-workflow-in-consumer-guidance`
- **Umbrella issue:** #6007
- **Comment identity intent:** [Compact forge-comment provenance](../../../visions/harness-guidance/README.md#compact-forge-comment-provenance)
- **Comment identity source:** #11128 (compact metadata and retry-stable comment/write identity)
- **Sub-issues:** #6286 (PR-owner installation-cell adoption); #7188 (first-class PR service and fleet deployment); #11779 (first cache/event milestone planning)

## Goal

Deliver the first independently useful integrated PR-service milestone: a
locally queryable durable current-PR-state cache for explicitly configured
GitHub and Gitea repositories, plus subscribable events over one shared
observation owner. The [M1 contract](repository-cache-events-m1.md) captures
the selected Request, serial source exits and separate live qualification.
M1 precedes agent consumers, Dispatch emission, a pane, complete CLI parity
and production writer/merge cutover; it does not replace the broader Guiding
Intent or any original, refined or inherited Plan/Validation outcome.
This effort remains Active under its existing integration and domain owners.

The [M1 source evidence map](repository-cache-events-m1.md#source-evidence-map)
separates the standalone synthetic vertical from still-open release,
installed/running and authorized real-target qualification. Completing that
source slice does not complete M1 or this effort. The reviewed source stages
1–4 have landed; the combined Plan and Validation items below remain open
because they also require the separate live qualification.

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

The central service is caller-driven: agents ask it to connect to forge
backends and subscribe to PR sets as they create PRs; reviewer task emitters
also request subscriptions to the PR ranges they need to watch. A client-side
native/fleet switch alone does not deliver this service intake model.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Delegation driver | Proposal, implementation, validation, PR stewardship, deployment | This effort's source worktree and linked issue |
| PR-service plan steward | First-class plugin/service and fleet-consumer scope amendment, review and integration | Separate managed planning worktree; #7188 |
| Author-publication plan steward | Bounded author-publication proposal and vision amendment only | Public coordination issue #6007; existing runtime owners retained |
| PR-perspectives steward | Author/Reviewer domain amendment, bounded inert attention source and isolated qualification; no live activation | #6007 and the Author/Reviewer perspectives plan |
| Author-publication implementation driver | Finite A0-A5 authorship contribution, integration, validation and PR stewardship | Bounded implementation claim #10383; existing broader owners retained |
| M1 plan steward | Bounded repository-cache/event contract, independent review and source reconciliation only | Source creaky-lever; planning claim #11779 |

## Coordination

The [M1 cache/event contract](repository-cache-events-m1.md) under #11779
sequences the first integrated milestone within #6007, not a second campaign.
The plan steward owns documentation delivery only; the integration owner
retains the whole effort and subsequent runtime assignments. Existing
publication/completion work is neither taken over nor an M1 dependency.

The [compact comment-identity extension](comment-identity.md) composes the
existing AI-attribution profile and Worktrees source-identity owners with
current publication, intent/receipt and observation custody. Issue-comment
consumers may reuse the preparation contract without transferring issue
lifecycle ownership here. Ordinary comment origin never refreshes PR
ownership; existing source/resource, provider and credential claims remain.
Review the portable contract before implementation and resolve concrete
overlaps through normal reviewed contributions, not a new sign-off ceremony.

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

The complete #6007 keeper and #6286/#7188 original/refined/inherited outcomes
continue through the [direct per-origin Plan, Validation and Coordination](retained-pr-service-outcomes.md)
under these same delegation, PR-service and domain custodians. Preserve
#6286's own installed-adoption exit separately from #7188's broader fleet,
provider/client/image and latest registration-intent duties. Existing
implementation reservations, real source/resource holds and operational
authorization remain; no source claim, task or resource state is released
or changed by this tracker intake. #7778's supersession of signature-like
source checkpoints remains in force, and #6007 stays open.

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

The caller-driven intake slice below extends the same watcher/client
integration responsibility. Provider/inventory, credential, publication/claim
and reviewer task owners remain unchanged; this is not a new generic producer
framework, fleet controller or permission to activate a live backend.

The [author-publication amendment](author-publication.md) refines the existing
checkout-free/client-convergence lane. Its original steward owns the planning
amendment; the subsequently assigned implementation driver works under #10383.
Worktrees publication/claim, #7051 conformance and #7188 service custody remain
unchanged.

The [Author/Reviewer amendment](author-reviewer-perspectives.md) extends this
same canonical effort with role-aware attention, a bounded live conversation
journal, terminal tombstones, CLI/API convergence and three-driver acceptance.
The PR-perspectives steward may realize a reviewed, finite inert domain slice;
existing source reservations, provider, workspace, Dispatch and operational
custody remain unchanged. No production review, merge or deployment is granted.

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

The selected first milestone's verbatim public-safe Request and separately
labelled recommendations are in [repository-cache-events-m1.md#request](repository-cache-events-m1.md#request).

The 2026-11-03 direction expanding the service into Author and Reviewer
perspectives is captured in public-safe form in
[author-reviewer-perspectives.md#direction-and-scope](author-reviewer-perspectives.md#direction-and-scope).
Its proposed implementation and validation are agent-recommended, with
operational effects separately gated.

The additional author-publication request is captured in public-safe form in
[the bounded amendment](author-publication.md#request-and-authority).
Its planning-only authority does not change the earlier grants or holds below.

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

Artifact-construction scope selection, verbatim:

> extend-shared-artifacts

This selects the offered extension of the shared artifact path for PR-service
image inputs rather than a supplied-wheelhouse-only container test. It does
not authorize runtime rollout, public runtime-dependency fallback or publication
of third-party bytes. The
[construction proposal](artifact-construction.md) separates first-party release
artifacts from governed, target-specific offline image inputs.

Continuation selection after offline-input source delivery, verbatim:

> watcher-integration

Continue watcher/client integration while actual image validation remains
pending an approved builder, pinned base and runtime closure. This does not
waive image acceptance or authorize unrelated runtime repair.

Caller-driven service usage clarification, verbatim:

> So, I expect that as agents start making PRs, the central service will be asked to connect to different backends and subscribe to a set of PRs. In addition, reviewer task emitters will wind up using this service to request subscriptions to ranges of PR that need watching.

The [caller-driven subscription proposal](caller-driven-subscriptions.md)
contains the **agent-recommended** interpretation, design and validation:
author-side exact PR sets and reviewer-selected PR populations share the
central service rather than requiring static startup entries or independent
discovery pollers. The selection grammar and admission mechanics are design
details, not additional operator quotations or live deployment authority.

## Plan

### Driver-aware filter definitions and owned population demand

The accepted generic expansion is specified in
[filter subscriptions](filter-subscriptions.md), bounded claim #12159.
It composes the existing #11783 cache/observer/service rather than replacing
its implementation or authorizing production activation.

- [ ] Review and land the filter/subscription contract before implementing it.
- [ ] Deliver the linked expression, driver, demand/storage and actual
  service/client slices with their complete validation plan; source completion
  does not close the parent service/fleet or live-provider acceptance.

### First integrated repository cache and event milestone

- [ ] Deliver [M1's serial standalone discovery/projection/query/subscription stages](repository-cache-events-m1.md#serial-implementation-roster), then separately qualify authorized read-only live shadow; retain all broader outcomes.

### Author/Reviewer service perspectives

_Operator-required outcome; agent-recommended slices and validation._

The [CLI parity inventory](cli-parity-inventory.md) records the draft-to-ready
contract reviewed in #11641 and its finite native source delivery in #11643.
Windows, provider, installed and HTTP parity acceptance remain separate;
readiness does not grant merge consent or release a hold.
The [explicit native reviewer-request contract](native-reviewer-request.md)
cleared review in #11645; its finite native source merged in #11647.
Windows, provider, installed and HTTP qualification remain open;
request acceptance is not evidence that a fresh review exists.
The [native creation-cancellation contract](native-creation-cancellation.md)
cleared review in #11651; its finite native source adapter merged in #11653 and selects only the
existing unpublished-reservation owner. Remote closure, reason-comment
publication and abandoned-claim settlement remain separate; source qualification
is not installed support or complete `pr-abandon` parity.
The [explicit native thread-read contract](native-thread-read.md), reviewed in
#11655, reuses the existing checkout-free Worktrees read owner; finite native
source merged in #11657. It adds no transport or watcher. Native-platform,
installed/HTTP qualification, live providers and thread resolution stay open.
The [explicit native diff-read contract](native-diff-read.md), reviewed in
#11659, defines a bounded
checkout-free reader over the existing provider primitive while preserving
tracked `pr-diff`, honest ADO refusal and unknown completeness/revision
evidence. Finite receiver/frontend source merged in #11661; installed,
live-provider and workspace generation acceptance remain independent.
The [existing-owner HTTP report boundary](owner-report-read.md), reviewed in #11665,
adds a read-only thread/diff exchange through the existing authenticated
Gateway without treating raw reports as principal-bound generations.
The optional source embedding merged in #11668; large-report, native-command adopter,
installed and live-provider acceptance remain separate.
The [selected native report-owner contract](native-report-owner.md), reviewed in
#11681 under #11680, defines a finite GitHub.com-first adopter. Implementation
merged in #11686 without changing legacy native authentication or implying
host adoption. Large-report, native-platform, installed/live-provider and full
CLI/HTTP acceptance remain separate.
The [size-qualified response contract](bounded-report-response.md), reviewed in
#11694 under #11692,
reuses the existing 8 MiB channel while preserving current 32 KiB caller-aware
handler/report defaults. Its opt-in SDK prerequisite merged in #11699; the
parallel report profile and native capture/client budget composition merged
in #11702. Their finite source qualification is not explicit standalone
configuration, installed hosting or provider acceptance.
The [explicit standalone report-host contract](standalone-report-host.md), reviewed in #11705,
selects the existing reader/native adapter and actual SDK handler ceiling
through strict optional host configuration. The finite source implementation
and fresh isolated controls are recorded in its linked implementation journal;
they merged in #11712. This is not retained-host enrollment or rollout.
The [selected native thread-resolution contract](native-thread-resolution.md),
reviewed in #11714, defines an optional ADO-first, single-thread status change through the existing
owner. Its [source implementation and isolated controls](journal/2026/11.09%20Selected%20native%20resolution%20implementation.md)
pass and merged in #11728 after independent review and required CI.
Read routes stay read-only; installed/account/HTTP and three-provider
qualification remain open. It advances #7051 without replacing its lifecycle
owner or claiming an atomic candidate/thread write.
Its [selected reopening refinement](native-thread-resolution.md#selected-native-reopening-proposal)
cleared independent review in #11741. Its
[source implementation and controls](journal/2026/11.09%20Selected%20native%20reopening%20implementation.md)
compose the same owner for an exact resolved-to-active transition, merged in
#11748 after independent approval and all required CI. Close-only commands and
legacy status choices remain unchanged; installed support is not qualified.
The [native local source-reconciliation proposal](native-source-reconciliation.md)
under #11753 composes the existing Worktrees `pr-complete` owner with optional
exact-source admission and a capability-directed native client. Contract review
cleared in #11754; its [finite source qualification](journal/2026/11.09%20Native%20local%20source%20reconciliation.md)
passes and implementation merged in #11761 after current-head independent
approval and all actual required checks.
No remote source mutation, merge evidence, claim settlement or finalization is implied.

- [x] Complete [P0 - reviewed intent](author-reviewer-perspectives.md#plan-and-exit-evidence)
  (merged in #10380; implementation, provider and adoption gates remain).
- [ ] Complete P1 normalized candidate/conversation/cursor and inert attention
  foundation, then P2 existing-owner central intake, bounded journal and
  terminal tombstone recovery.
  The first finite source leaf is the
  [inert attention projection](../../../plugins/agent-pull-requests/docs/attention.md);
  merged in #10390. It does not close normalized acquisition, durable cursors
  or serving adoption.
  The
  [existing-owner conversation metadata journal](../../../plugins/agent-pull-requests/docs/conversation-journal.md)
  and opt-in event reader merged in #10401; full body acquisition, coordinated terminal
  retention, notifications and normal host adoption remain separate.
  Quiet-observation metadata expiry merged in #11574; it preserves the cursor
  anchor and does not compact body-bearing caches, outboxes or effect receipts.
  The [coordinated terminal-retention refinement](terminal-retention.md)
  was reviewed in #11576. It defines the same-owner fence, notification,
  recovery-only payload and scoped cache/materialization sequence;
  T1 terminal intent/admission/drain and minimal replay notification merged
  in #11605. T2a compact recovery-only operations and actual protected readback
  merged in #11614. T2b scoped cache/materialization retirement merged in
  #11623. The optional read-only T3 receiver source merged in #11625;
  strict standalone selection and fresh-only source provisioning merged
  in #11628. Finite explicit service-lifecycle source qualification merged
  in #11631. Complete retained-reader, mapping/retention composition,
  installed/provider and P1/P2 acceptance remain open.
  The [mapping-preservation refinement](terminal-retention.md#retained-acquisition-mapping-composition)
  cleared review in #11633. Explicitly adopted lossless slot transfer and
  scoped historical library readback merged in #11634. Separately negotiated
  terminal retained-comment serving merged in #11636. Explicit standalone
  host selection, fresh-only adoption and foreground compact-read
  qualification merged in #11639; installed, provider and complete T3
  acceptance remain open.
  Existing normalized ADO load/sync acquisition feeds the explicitly enabled
  journal after confirmed cache publication (#10405); native observation and
  standalone-host adoption remain distinct prerequisites.
  Exact native target-tip metadata merged in #10599. The bounded
  [existing-owner native workspace host](native-workspace-host-slice.md)
  contract merged in #10602 and its optional source adapter in #10609. It composes
  the existing finite exporter, cache and journal; thin watch metadata alone is not full
  workspace acquisition. Operational configuration, installed/provider/shadow
  qualification and the remaining P1/P2 acceptance remain separate gates.
  The [durable attention contract](durable-attention-slice.md), merged in #10633,
  separates explicit owner-layout expansion, actual observation/feedback
  integration and the optional authenticated reader; it is not implemented
  by the inert projection or conversation ACK.
  Its A1 explicit data-owner expansion merged in #10641 and finite atomic
  acquisition half of A2 in #10648. The
  [captured-feedback refinement](captured-attention-feedback.md), merged in
  #10664, defines the remaining pre-effect capture, confirmed retry readback
  and interest-specific echo integration. Its finite B1 storage expansion
  merged in #10665. The optional protected B2 source seam merged in #10677 captures original
  positions and reconciles confirmed comment/reply/edit readback without
  resends or consuming newer input. The first conservative B3 comment-echo
  leaf merged in #10743; exact original versions and one contiguous semantic
  position are required. Unsupported attribution and longer chains remain
  unavailable. The optional [retained reader](../../../plugins/agent-pull-requests/docs/attention-reader.md)
  source merged in #10906, including conservative captured-feedback availability
  and the negotiated CLI/client/Gateway path. Complete B3/A2,
  normal-host/provider/installed serving and the remaining parent acceptance
  stay open.
- [x] Review and land [B4 historical original-position readback](captured-attention-feedback.md#b4---historical-original-position-readback-proof)
  before changing the strict owner layout or executing behavior (#10970).
- [x] Deliver B4's finite existing-owner storage, protected proof and negotiated
  read-consumer leaves; qualify overlapping operations, original identity,
  capacity, restart, unavailable evidence and old/new compatibility without
  weakening B2/B3 or closing broader attention/provider/installed acceptance.
  The finite explicit v6/capture-v2 storage prerequisite merged in #10972.
  Protected original-readback proof commits merged in #10977; negotiated v6
  reads merged in #10979. This is the finite synthetic source exit, not native,
  live-provider, installed or full B3/A2 acceptance.
- [x] Review and land the [compact comment-identity contract](comment-identity.md)
  before adding preparation, persistence or publisher behavior (#11129).
- [ ] Deliver the contract's shared preparation, actual all-forge comment
  consumer inventory, existing-owner correlation and separately qualified
  adopter leaves. Keep AI-profile/privacy policy, minimal clutter, stable
  comment/per-write identity and native proof distinct; a helper alone is
  not full publication coverage.
  Shared preparation merged in #11146. The
  [publisher inventory](comment-publication-inventory.md) keeps legacy,
  explicit-composition and unsupported surfaces distinct; automatic consumer
  adoption, allocation/correlation and installed qualification remain open.
  The [prepared-intent binding refinement](comment-intent-binding.md) addresses
  the missing structured admission fact before new storage/publisher behavior.
  The reviewed [durable pre-approval selection refinement](comment-intent-binding.md#durable-pre-approval-selection-and-allocation)
  precedes automatic allocation and strict storage/publisher changes.
  Its finite owner data leaves below do not qualify producer adoption.
- [x] Deliver the finite optional prepared-intent source binding: explicit
  storage (#11207), native execution/original receipts (#11214), negotiated
  workspace submission (#11224) and host-bound local preparation before
  preview/approval (#11237). Caller-retained GUIDs and actual profile/source
  owners remain required; this is not automatic all-publisher adoption,
  allocation, acquisition/DB mapping or installed/provider qualification.
- [x] Deliver finite pre-approval owner retention (#11251) and fresh
  comment/reply allocation (#11253), committing exact final bytes before
  preview and separating pending selection from execution admission.
  Complete producer/all-forge/installed adoption beyond the finite leaves
  below remains open.
- [x] Deliver the finite optional receiver-owned pre-approval source path
  (#11299): negotiate actual receiver/storage support, commit final fresh
  comment/reply previews before returning them, recover the original key
  and preserve exact explicit admission/native receipts.
  Normal host enrollment, other publishers, acquisition/DB mapping, provider
  and installed/shadow acceptance remain open; selected edits have a separate
  finite qualification below.
- [x] Deliver the finite optional selected-edit allocation library (#11374),
  binding actual protected generation/source and native-author evidence to
  same-owner original receipts. Stable logical identity, fresh write identity,
  selected legacy edits and original-key recovery are source-qualified;
  normal receiver enrollment and all-publisher/installed acceptance remain open.
- [x] Qualify the separately selected
  [owned-edit receiver extension](comment-intent-binding.md#optional-owned-edit-receiver-extension)
  through the existing CLI/native/Fleet/Gateway consumers, preserving baseline
  fresh allocation, explicit-ID/v1/v2 and receiver rollback semantics (#11381;
  250 coupled source cases). Normal host enrollment, other publishers and
  provider/installed adoption remain separate.
- [x] Review the [trusted producer-enrollment contract](comment-producer-enrollment.md)
  before changing normal-host behavior (#11393).
- [x] Deliver the finite optional in-process source/profile resolver through
  the actual Worktrees record and attribution owners (#11397). Resolve before
  pure retained composition; this is not normal-host enrollment.
- [x] Deliver separately selected protected credential scope and encrypted
  delivery through the existing Gateway/controller/worker owners (#11412).
  Native permission verification, pinned transport and host adoption remain
  separate; observation contracts retain their meanings.
- [x] Deliver the finite optional pinned native ADO request factory (#11428),
  preserving one actual material generation, mandatory owner/policy checks,
  final candidate/ownership/captured-target guards and durable unknown outcomes.
  This source prerequisite does not supply the actual credential verifier,
  normal-host route or installed/live-provider qualification.
- [x] Deliver the finite optional request-local workspace factory (#11452)
  after reviewed authority/lifetime design (#11450). Resolve once across
  actions and feedback without credential acquisition for preparation or
  storage-only recovery. Native verifier/source and host enrollment remain
  separate; legacy per-context embedding is retained.
- [x] Deliver the finite native effective-permission evaluator (#11462) through the
  existing pinned ADO transport: resolve actual Git namespace/action semantics,
  require repository and exact native target-branch read/review permission
  before the attempt fence and at dispatch, and preserve unknown attempts on
  later denial. Actual credential-source grant/audience verification, current
  custodian admission and strict normal-host enrollment remain separate.
- [x] Supply the finite [preparation-only receiver](comment-producer-enrollment.md#preparation-without-writer-enrollment)
  through the existing allocation and trusted source/profile owners (#11471).
  Retain exact selection contracts and original previews without a writer;
  submission remains independently unavailable.
- [x] Deliver the [canonical helper and native source-discovery prerequisites](comment-producer-enrollment.md#canonical-helper-delivery-and-native-source-discovery)
  before expanding strict standalone configuration. Direct wheel/sdist/release
  delivery and actual executing exact-root discovery are separate from
  fixture imports or installed enrollment. Native discovery merged in #11488
  and canonical helper packaging in #11500; this closes only the finite source
  prerequisites, not normal-host or installed enrollment.
- [x] Supply explicitly selected standalone preparation-only hosting using
  those actual source/profile dependencies. Qualify the executing provider before
  advertising support; no preview verifies protected-source grants or
  closes writer enrollment (#11507; finite source qualification only).
- [ ] Deliver the actual protected write-owner prerequisite through its
  existing owners, then bind one explicitly selected authenticated host route.
  Injected factories and observation credentials do not satisfy these exits;
  default watch-only and native read-only hosting stay unchanged.
  The [provider-first writer expansion](comment-producer-enrollment.md#provider-first-protected-writer-expansion)
  cleared review in #11410 before credential/provider changes. The finite
  delivery leaf above does not close native rotation/unknown-effect or
  normal-host qualification.
  The [current-authority and request-local material design](comment-producer-enrollment.md#current-authority-and-request-local-material)
  cleared review in #11450. It separates actual source/native/custodian
  verification and binds one factory per execution before strict host
  configuration expands.
- [x] Review the finite [acquired comment observation refinement](comment-observation-correlation.md)
  before joining original prepared receipts to acquired native objects.
  Keep the independently unavailable protected credential source unsupported
  (#11511; broader provider/installed qualification remains open).
- [x] Deliver its finite existing-owner read-only library projection (#11514;
  212 coupled source cases). Preserve original native receipts, unknown
  outcomes and acquired observation intervals without new IO or storage.
- [x] Separately qualify negotiated workspace/CLI/client/Gateway source consumers
  (#11517; 210 coupled and final 82 focused consumer/Gateway cases, overlapping).
  Preserve original
  receipt/unknown states and observation age; a current content match is not
  original-version proof, persisted DB mapping or full C3/provider acceptance.
- [x] Review the [persisted acquisition mapping refinement](comment-observation-correlation.md#persisted-acquisition-mapping-refinement)
  before changing strict owner or serving schemas. Reuse retained intent and
  receipt identity; add only bounded acquired evidence under the same owner
  (#11538; finite storage and consumer delivery below).
- [x] Deliver its finite explicitly adopted data-owner/library transaction and
  scoped retained-read leaves (#11542; final affected packet: 100 source cases).
  Preserve original confirmation, unknown outcomes and B3/B4 proof.
- [x] Deliver actual explicitly selected workspace load/sync acquisition and
  negotiated retained serving for that persisted mapping (#11556; final 56
  actual-boundary source controls),
  preserving original confirmation, unknown outcomes and B3/B4 proof; keep
  all-publisher, provider and installed acceptance separate.
- [x] Select retained mappings through the existing standalone foreground
  host's trusted per-route configuration and fresh-only storage adoption.
  Preserve default-off hosting, read-only native scope and retained inspection
  after optional preparation-provider withdrawal; this is source hosting,
  not protected publication or production enrollment (#11561).
- [x] Review the [tracked comment/review consumer refinement](tracked-comment-consumers.md)
  before changing legacy commands (#11525).
- [x] Qualify one explicit tracked comment/review source binding
  through the existing workspace selection/receipt owner. Preserve distinct
  ADO text/vote outcomes and keep unsupported native surfaces explicit
  (#11530; 33 tracked and 59 Worktrees source cases). Normal/installed host
  enrollment, protected grants/writers and all-publisher acceptance remain open.
- [ ] Complete P3 HTTP-backed author/reviewer CLI and Dispatch consumer
  integration, preserving exact legacy semantics and source/resource custody.
- [ ] Complete P4 ADO, GitHub and Gitea driver-specific acceptance.
- [ ] Complete P5 isolated container/service and separately authorized read-only
  shadow qualification; no shadow-triggered mutations.
- [ ] Complete P6 optional policy-gated automation, thin presentation and staged
  legacy retirement only after compatibility and consumer-dependency proof.

### Governed watch-owner cutover prerequisite

_Agent-recommended design and validation, explicitly selected for review._

- [x] Review and merge the [watch-owner lifecycle plan](watch-owner-cutover.md) before implementing the service transaction (confirmed by #6291).
- [x] Land the canonical pre-adoption drain, full-transaction borrowed authority and custody-preserving rollback prerequisite before the owner adapter (confirmed by #6396).
- [x] Land inactive watch-state construction, final read-ready reload and bounded complete quiescence as internal groundwork, without enabling a production service or installation-cell adapter (confirmed by #6714).
- [x] Land non-signaling native process-exit references captured before graceful control, without granting receipt, installation or retirement authority (confirmed by #6720).
- [x] Adopt canonical graceful-cutover primitives with installer-driven activation, passive health, exact-instance promotion and retirement, and crash-consistent marker/manifest/route recovery (source landed in #7160).
- [x] Define and enforce one installation-scoped watch-state writer across predecessor drain and successor subscription/delivery adoption; preserve event and registration identities without overlapping live-generation delivery (source landed in #7160).
- [x] Prove rollback, commit-forward, governance refusal, crash recovery, callback drain, and refusal of lease-only owners without verifiable same-cell ownership on Windows and Linux before landing the cutover prerequisite (exact-head native acceptance `39226209682`).
- [x] Land and deploy the governed lifecycle prerequisite as part of PR-owner adoption before enabling installation-cell dispatch delegation. Source/release credit remains #7160/#7291. Authorized unified rollout now selects PR-owner `0.3.249-dev1`; four installed-profile cases and exact-source native acceptance `42461516789` pass. macOS retains its tested fail-closed disposition with positive support tracked in #11545. Live delegation activation remains separately authorized.

### Installation-cell prerequisite

_Agent-recommended prerequisite, explicitly selected by the operator._

- [x] Review and merge the installation-cell plan amendment before adoption implementation (confirmed by #6146; the lifecycle amendment above remains a separate review gate).
- [x] Adopt the canonical installation-context payload, installer/runtime-resolution and state-root contract in `agent-pull-requests`, preserving supported legacy installs without silent cell-to-legacy fallback (source landed in #7160).
- [x] Include the PR owner in the generated same-cell peer-launch contract and declare release impact for every generated consumer (source landed in #7160).
- [x] Prove first installation, upgrade, invalid/foreign receipt refusal, state isolation, actual two-cell PR-owner invocation, and pending watch-subscription recovery using the shipped payload rather than a fabricated sibling contract (exact-head native acceptance `39226209682`).
- [x] Land and deploy the adoption prerequisite before enabling installation-cell dispatch delegation. Source/release credit remains #7160/#7291. Release #11489, authorized unified rollout, installed-profile lifecycle/isolation/refusal and native-account acceptance `42461516789` clear this finite prerequisite. This does not enable live dispatch delegation or complete the broader service/fleet effort.

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
- [x] Land optional replay-consumer scheduling through the coordinator's existing
  waiter-maintenance cadence, active-owner checks and drain/shutdown boundary.
  Keep native recovery independent of remote IO and retain original identities
  after interrupted cycles. This source seam does not configure a production
  caller, attest its namespace, activate a backend or close service/fleet proof
  (source confirmed by #8124).
- [x] Land the existing-core serving/observation lifecycle seam: adopt persisted
  replay state under native lifetime exclusion, own one connector and observer,
  withdraw later admission/effects on disconnect, and join actual serving and
  native/disk work before releasing the writer. Failed drain retains custody.
  Source confirmed by #8157. This prerequisite is not standalone packaging,
  fleet activation, cross-host fencing or slow-target fairness acceptance.
- [x] Land the independent foreground host distribution, normal wheel/sdist,
  explicit credential/configuration references, separate provisioning, passive
  readiness and existing `container.service` role inputs (source confirmed by
  #8196). Actual image construction, deployment lifecycle, live providers and
  representative performance remain open; the source recipe is not an installed role.
- [x] Land bounded concurrent native observation with completion-order replay
  application, ongoing deadline sweeps and actual-read drain custody. Preserve
  exact authorized groups and original registration/event identities; this
  source leaf does not close representative production performance or fleet proof
  (source confirmed by #8209).
- [x] Bind the standalone replay volume to its exact fleet/Gateway/slot and
  namespace on every transaction, preserving original identities and refusing
  unbound/foreign formats without implicit migration or downgrade. This source
  safeguard does not close image construction, cross-host fencing or update/
  rollback acceptance (source confirmed by #8221).
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
- [x] Land standalone first-party PR/Fleet construction and verification through
  the existing shared artifact path, preserving plugin compatibility and
  canonical source/resource identity (confirmed by #8293). This source leaf
  does not admit a runtime dependency closure or establish image acceptance.
- [x] Land canonical retained hashed dependency compile/export through the
  existing installer owner, preserving resolver seasoning, input isolation and
  ordinary installation behavior (confirmed by #8327). This source prerequisite
  constructs candidate locks, not approved target profiles or acquired closures.
- [ ] Deliver the [shared artifact construction](artifact-construction.md)
  proposal through review, standalone first-party artifact support, admitted
  runtime closure and actual offline image acceptance. Preserve public
  first-party-only release bundles and canonical dependency authority.
- [ ] Implement centralized, authorization-scoped observation and durable
  registration with transport-appropriate replay/ACK delivery; do not execute a
  remote subscriber's local callback argv in the service container.
- [ ] Integrate scoped, renewable forge credentials and typed client/dispatch
  access through the fleet-owned Gateway contracts, without hosting any other
  operator's state or credentials.
- [ ] Prove standalone/container lifecycle, restart and upgrade continuity,
  account/fleet isolation, observation sharing, disconnection recovery and the
  real client/Gateway/service path before treating fleet deployment as complete.

### PR-service offline image inputs

_Delegation driver source slice; agent-recommended implementation under the
reviewed artifact-construction plan. The planning worktree retains its broader
fleet-service binding._

- [x] Land the repository-only offline-context consumer and exact retained-wheel
  and fresh offline-install controls through normal review and Linux/Windows
  source validation (confirmed by #8885). This does not close profile/feed/base approval, actual
  image construction, container lifecycle or fleet acceptance above.

### PR-service watcher/client integration

_Delegation driver source validation slice; agent-recommended under the
existing serving/consumer contract, not a second watcher or evaluator._

- [x] Qualify actual watch-service observation through the real Gateway and
  Dispatch replay/queue consumers: shared targets, unchanged nonevents,
  original identities across restart and one wake per event after lost ACK.
  Source confirmed by #8927. This finite proof does not close live provider credentials, the
  independent evaluator route, image or installed/fleet acceptance.
- [x] Review the [scoped replay selection amendment](pr-service-contract.md#scoped-replay-selection-amendment-agent-recommended-proposed)
  before implementing a parallel opt-in v2 binding contract. Preserve strict v1
  wire/record compatibility, original identities and default native operation
  (confirmed by #8938; implementation and qualification remain below).
- [x] Deliver the parallel v2 provider/client binding seam without changing v1
  (#8945). Exact-head native source qualification passed 340 cases on each of
  Linux and Windows; scoped Dispatch intent and explicit programmatic
  coordinator selection followed in #8957.
- [x] Implement and qualify current service/consumer binding at every replay
  operation, then wire explicit optional coordinator composition through its
  existing lifetime, active-route and credential owners. No configuration label
  grants admission or deployment authority (source confirmed by #8957).
- [x] Exercise the amendment's mixed-version/rollback, remapped-target,
  malformed binding, restart, credential-loss and ambiguous-response controls
  through the real Gateway and durable queue/store. Keep live provider, image,
  independent evaluator and installed/fleet acceptance separate (#8945, #8957).
- [x] Qualify the actual watch-service and Dispatch consumer with the native
  credential-backed observation path and typed credential exchange in one
  isolated integration. Preserve renewal/refusal, original intent and one wake
  after recovery; synthetic issuers/forge responses do not close live provider,
  installed, image or fleet acceptance (GitHub source proof in #8975).
- [x] Qualify the corresponding native ADO configured-observation reader through
  the same actual watcher, typed credential exchange and scoped Dispatch path.
  Preserve provider-native state and credential refusal without crediting
  GitHub-only coverage as ADO parity or taking over broader conformance
  (bearer-auth source proof in #8980; basic authentication and live forge
  transport remain outside this finite evidence).

### Caller-driven backend and PR-set intake

_Operator-required usage; agent-recommended design, sequencing and validation._

- [x] Review the [caller-driven subscription amendment](caller-driven-subscriptions.md)
  and corresponding PR/reviewer vision changes before implementation (#9033).
- [ ] Add caller-driven connections to admitted forge backends and durable
  exact-PR-set registrations through the existing central service owners;
  preserve current credentials, identities and legacy watch contracts.
- [ ] Add bounded provider-backed discovery and continuing membership for
  reviewer PR selections, reusing native provider/inventory and watch cores
  rather than a second forge adapter or polling service.
- [ ] Integrate confirmed author-side PR creation/tracking without repeating
  publication after subscription failure or losing its existing PR claim.
- [ ] Integrate reviewer emitters with shared discovery and replay while
  retaining eligibility, one-task-per-target deduplication and reviewer lineage.
- [ ] Qualify real client/service and consumer paths, initial membership versus
  changes, overlapping selections, partial discovery, disconnect/restart,
  lost results and scoped cancellation at the proposal's named evidence tiers.
  Keep provider, image and installed/fleet acceptance separately open.

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

- [x] Confirm promotion of the changed source to the release branch by content, then deploy through the unified update surface. Earlier #7291/#7751 and #7714 credit remains intact. Release #11489 is content-verified; the authorized unified updater completes successfully with selected/running Dispatch `0.18.306-dev1` and selected PR-owner `0.3.249-dev1`. All 410 Dispatch production Python files match that release, and installed-profile/native-account PR-owner acceptance passes. Live consumer/delegation activation is not implied by runtime installation or this checkbox.
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

### Author publication and metadata convergence

The [bounded amendment](author-publication.md) captures the public-safe request,
credited baseline, unchanged owners and agent-recommended implementation.
It extends the following checkout-free lane, not the fleet deployment scope.

- [x] Complete [A0: contract and target policy](author-publication.md#a0---contract-and-target-policy) at the source-contract layer (#10402).
- [x] Complete [A1: policy-qualified foreign creation](author-publication.md#a1---policy-qualified-foreign-creation), retaining caller claims and delegated publication checks (#10402, reachable-workspace adapter).
- [x] Complete [A2: verified author metadata](author-publication.md#a2---verified-author-metadata), including exact descriptions, reviewers and bidirectional work-item links (#10402 source acceptance).
- [x] Complete [A3: delegated source publication](author-publication.md#a3---delegated-source-publication) through the existing source-control owner (#10402 source acceptance).
- [ ] Complete [A4: one documented front door](author-publication.md#a4---one-documented-front-door) across generic guidance and consumer adapters.
- [ ] Complete [A5: qualification and adoption](author-publication.md#a5---qualification-and-adoption), with source, release and authorized installed acceptance distinguished.

The operator selected source-PR completion with remaining adoption tracked
separately. Historical #10412 was subsumed into the
[author-publication cohort under #6007](https://github.com/ThomasMichon/copilot-extensions/issues/6007#issuecomment-6668447425),
preserving its per-item owners and remaining A4-A5 consumer/live/installed and
remote-only transport acceptance, not marking them complete. #11092 expands
the portable worker author-policy source contract; its source tests do not
close these rows or adopt the broader parent campaign.

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

### Direct retained keeper and per-origin acceptance

- [ ] Complete every unfinished #6007/#6286/#7188
  [direct per-origin Plan row](retained-pr-service-outcomes.md#plan),
  preserving complete original/refined/inherited scope, distinct finite exits,
  actual checked source/release credit, semantic choices, domain-specific
  bounds and unchanged source/resource ownership.

## Validation Plan

- [x] Deliver the finite [native local source-reconciliation controls](native-source-reconciliation.md#compatibility-delivery-and-qualification)
  after reviewed contract #11754. All five finite source-control rows pass
  (229 coupled Worktrees and 123 frontend cases, five existing platform skips);
  exact committed actor-free qualification passes 4,699 cases. Implementation
  #11761 merged after current-head independent approval and every actual
  required check. Legacy source behavior and broader
  installed/provider/HTTP/resource/effort acceptance remain independent.

- [ ] Prove [M1's synthetic foreground/client/persistence/subscriber vertical and separate released/installed/running qualification](repository-cache-events-m1.md#validation-and-acceptance); neither fixtures nor a healthy empty host close M1 or this effort.

- [x] Qualify the finite source [selected native reopening refinement](native-thread-resolution.md#reopening-source-controls-after-review)
  after independent contract review (#11741): exact resolved-to-active transition,
  native action authority, same candidate/thread, status-only write, original
  consumed-operation outcomes, strict peers and unchanged close-only behavior.
  All five linked source controls pass (414 Worktrees and 193 frontend controls,
  five existing platform skips), merged in #11748 after current-head independent
  approval and all actual required checks. No installed/provider/HTTP or native
  Windows parity is claimed.

- [x] Qualify the finite [selected native thread-resolution contract](native-thread-resolution.md#expansion-and-qualification) (#11728)
  after reviewed intent: exact ADO target/principal/candidate/thread, status-only
  mutation, consumed-key recovery, unknown outcomes and old/new runtime
  composition. Source controls do not establish atomic CAS, other-provider
  support, installed/HTTP adoption or the full lifecycle acceptance in #7051.
  All five linked source controls now pass (336 Worktrees and 150 dedicated
  frontend cases, five existing platform skips); independent current-head
  approval, resolved findings and all actual required CI preceded merge.
  The actor-free packet passes 4,699. This credits only the finite source leaf.

- [x] Qualify finite explicit standalone report hosting (#11712): 168 full
  standalone and 68 isolated affected plugin cases cover actual foreground
  TLS/native delivery above 1 MiB, strict/default/prior/rollback selection,
  request-only native identity and original writer custody through expiry,
  withdrawal and delayed native/SDK delivery cleanup. The actor-free packet
  passes 4,668; strict service typing covers Linux/Windows analysis.
  All six finite host-contract source controls are resolved. Installed/live
  providers, native Windows, retained hosts/containers, full CLI/HTTP/Dispatch,
  activation and rollout acceptance remain open.

- [x] Qualify the finite opt-in v2 report/native-budget composition (#11702):
  259 focused cases cover actual native-child, SDK/Gateway TLS and source CLI
  delivery above 1 MiB under an 8 MiB selection, encoded/capture/client limits,
  mixed peers, lifetime/backpressure and joined cleanup. Ordinary native
  1 MiB defaults and v1 raw-JSON compatibility/32 KiB encoded limits remain.
  The actor-free packet passes 4,665; all six finite
  [response-contract source obligations](bounded-report-response.md#serial-delivery-and-validation)
  are resolved. Explicit standalone configuration, installed/live-provider,
  native Windows and broader CLI/HTTP/Dispatch acceptance remain open.

- [x] Qualify the finite opt-in caller-aware SDK response prerequisite (#11699):
  152 focused handler/caller/local cases cover actual larger TLS delivery,
  bounded construction/emission, legacy/local refusal, ACK backpressure,
  expiry, disconnect, withdrawal and joined cleanup. The actor-free unit
  packet passes 4,665. The report-v2/native-budget composition and combined
  response-contract source validation subsequently merged in #11702 above;
  installed/live-provider, native Windows and full CLI/HTTP acceptance remain open.

- [x] Qualify the finite optional selected GitHub.com report owner (#11686):
  actual configuration/strict account policy, explicit verified CLI-store
  identity, pinned provider reads, one request budget and original cleanup
  custody. The final target/committed-policy packet passes 97 cases and the
  actor-free packet passes 4,665; earlier overlapping report/Gateway/TLS and
  native-adapter packets pass 88 and 22. Linux fixtures are not native Windows,
  installed hosting, live-provider, large-report or full CLI/HTTP acceptance.

- [x] Qualify the finite optional existing-owner report boundary (#11668):
  configured report scope before resolution/acquisition, full native report and
  original exit preservation, epoch-safe negotiation, current owner/deadline
  checks through worker and shielded task handoffs, and actual Gateway drain.
  Final focused report qualification passes 62 cases; the earlier coupled
  CLI/watch packet passes 210 with five Windows-only skips. Broader native-command
  adoption, larger-report transport, installed/live-provider and full
  HTTP/CLI acceptance remain open.

- [x] Qualify the finite [explicit native diff-read source boundary](native-diff-read.md#compatibility-and-qualification) (#11661)
  after contract review: 149 native cases with five Windows-only skips and
  362 actual owner/read/provider/tracked cases. Real subprocess provider
  methods retain target/endpoint selection, tracked compatibility and honest
  unsupported/error/completeness evidence. Installed, live-provider, HTTP and
  candidate-bound workspace acceptance remain separate.

- [x] Qualify finite explicit-target native thread reading (#11657): 148 native
  parser/routing cases with five Windows-only skips and 43 actual-owner
  read/observation cases. Preserve exact reports, incomplete/null completeness,
  candidate changes, valid failures and pre-dispatch option-prefix refusal.
  Real subprocess old/new callers exercise actual GitHub/Gitea/ADO builders
  with synthetic IO. Native-platform, installed/live-provider, HTTP and
  broader CLI parity remain open.

- [x] Qualify finite native unpublished-creation cancellation (#11653):
  exact full worktree/creation selection, original revision/reason recovery,
  actual receiver declaration and one existing-owner dispatch. The final
  cancellation/routing packet passes 149 cases with five platform skips;
  actual creation/status/cancellation composition and old-caller subprocess
  pass 82. Preserve unknown committed/lost outcomes, owner evidence,
  frontend recovery inputs and source/sibling/claim/finalization custody.
  Windows, installed/provider/HTTP and remote abandonment/comment/claim
  integration remain open; this is not complete legacy parity.

- [x] Qualify finite explicit-target native reviewer requests (#11647).
  Preserve tracked nudge selection, transport and no-op behavior; use the
  actual target/provider and distinguish API acceptance from a fresh verdict.
  The owner/provider/legacy packet passes 398 cases; the final shared
  request/author/readiness/worker-policy packet passes 180, overlapping the
  former packet and adding malformed reserved-worker marker controls.
  The native packet passes 116 with five Windows-only skips and verified
  cleanup. Source qualification is not native Windows, live-provider,
  installed or HTTP parity.

- [x] Qualify finite explicit-target native draft readiness (#11643), preserving
  tracked ready and legacy-hold behavior. The final owner packet passes 54 cases;
  the native frontend passes 84 with five Windows-only skips and verified
  cleanup. Actual delivered-policy/projectless refusal, target/provider
  validation, versioned outcomes, single draft-only mutation and uncertain
  result no-replay controls are covered. Windows/live-provider/installed/HTTP
  parity and the broader CLI matrix remain open.

- [x] Qualify finite standalone terminal mapping selection, missing/incompatible
  core and prior-constructor behavior, strict parser/rollback, fresh-only
  interrupted adoption and validation-only retained serving (#11639).
  The existing foreground Gateway/CLI/native-exporter fixture seeds synthetic
  original history before its writer, drains before owner-side compaction and
  populated-cache retirement, then reads exact compact metadata after restart
  without another export or replay-store write. The final standalone packet
  passes 132 cases; strict typing passes Linux/Windows analysis. Installed
  hosts, providers, full retention and P1-P6 acceptance remain open.

- [x] Qualify finite optional terminal mapping serving through actual adopted
  storage, Native/Gateway/CLI negotiation, all four owner layouts, populated
  cache/buffer retirement and fresh file-reader/service restart (#11636).
  Preserve baseline v1 contracts, missing/withdrawn support, strict rollback,
  unsupported historical versus null evidence and malformed-value refusal.
  The final focused suite passes 39 cases; earlier overlapping packets pass
  173 and 108. Standalone selection and installed/provider/full T3 acceptance
  remain open.

- [x] Qualify finite mapping data/library controls: original prepared/native
  joins, restart, later writes, unknown/partial/divergent evidence, copied
  targets, explicit/capacity-refused adoption, old strict readers, receipt
  invalidation, uncertain replacement, cache-before-journal failure and
  normalization/digest reuse (#11542; final 100 affected source cases).
  Actual load/sync source controls follow below.
- [x] Qualify finite [persisted acquisition mapping source controls](comment-observation-correlation.md#provider-first-delivery-and-validation):
  same-owner native joins, real-file restart/contention, monotone evidence,
  capacity and uncertain replacement, no duplicate IO or false feedback,
  strict old/new/rollback peers, additive discovery and unchanged default
  hosting (#11542/#11556; final 56 consumer controls). Normal installed
  enrollment and live provider acceptance remain with P3-P5, not this source leaf.
- [x] Qualify that standalone mapping selection through strict passive parsing,
  fresh-only adoption, unsupported existing volumes, route isolation and the
  real foreground/Gateway/native-exporter boundary with a synthetic prior
  receipt. Separate retained current-content evidence from native-send,
  original-version, feedback/handled and installed/provider acceptance
  (#11561; final 105 standalone controls).
- [x] Qualify the explicit tracked comment/review consumer through its actual
  resolution and workspace CLI/tool/client boundary: exact scoped selection,
  final preview and separate submission, original-key restart/lost-result
  recovery, partial comment/vote outcomes, bodyless actions, and unsupported
  or rolled-back receivers without legacy mutation fallback (#11530;
  33 tracked native/Gateway and 59 legacy/real-configuration/lazy-dispatch
  source cases). These do not qualify live providers or installed hosts.
- [x] Qualify the finite acquired comment observation library through actual
  preparation, original native attempt/receipt and acquired generations:
  exact scoped native IDs/actor/content, restart, later edits/deletion,
  copied tags, partial/unknown evidence and zero mutation/acquisition/send
  (#11514; 212 coupled source cases, including real owner-lock contention).
- [x] Qualify old/current/rolled-back workspace source peers separately (#11517),
  including actual adopted-storage rechecks at dispatch for direct and stale
  native/Gateway clients. Neither cached matches nor today's object digest
  establish original-version feedback; provider/installed acceptance remains open.
- [x] Qualify preparation-only CLI/client/Gateway allocation and original
  recovery with the actual preparer, zero protected acquisitions/sends,
  independent submission refusal, unchanged writer/default behavior and
  current/prior receiver rollback (#11471; 107 coupled source cases).
- [x] Qualify the [canonical helper delivery and native discovery controls](comment-producer-enrollment.md#canonical-helper-delivery-and-native-source-discovery):
  outside-checkout wheel consumption, sibling-free sdist rebuild, canonical
  bytes/import isolation and actual exact-root current/prior parser/discovery
  refusal, with no credential or forge effects (#11488/#11500; bounded source
  and artifact controls, not installed enrollment).
- [x] Verify actual executing source/profile packaging and the strict
  current/prior standalone-host configuration/process seam before
  preparation-only hosting is advertised. Preserve the separately open
  credential-source, protected-writer and installed/provider acceptance
  (#11507; 88 standalone and 119 coupled source cases).

- [x] Qualify native effective-permission evaluation (#11462; 216 coupled
  source cases) with contained namespace/
  action/result parsing, nested/case/Unicode target refs, repository/branch
  denial, incomplete responses, one pinned material generation and executor
  denial before/after the durable fence. Preserve legacy embedding,
  candidate/owned-target guards and unknown-effect no-resend semantics; source
  controls do not establish live native grants or installed host adoption.

- [x] Qualify the finite request-local workspace factory (#11452): 151 coupled
  lifetime/workspace/selection/owned-edit/attention cases pass with verified
  cleanup. Multiple action and feedback contexts share one resolution;
  preparation and storage-only recovery acquire none. Concurrent requests,
  failure, rotation/withdrawal and unknown reconciliation retain their
  separate execution/no-resend semantics. This is not actual native grant
  verification, normal-host or installed/provider qualification.

- [x] Qualify optional selected-edit CLI/native/Fleet/Gateway source consumers:
  actual shared preparation and native receipts, prepared and genuine legacy
  edits, lost previews, cache-independent original-key recovery, confirmed
  retry, missing/malformed/refreshed-away support, current receiver rechecks
  and withdrawal/restoration without state downgrade (#11381; 250 coupled
  cases, including 17 focused consumer cases). Preserve prior fresh clients,
  explicit-ID/v1/v2/default behavior and separately open installed/provider
  and all-publisher acceptance.

- [x] Qualify selected owned/legacy edit library allocation, repeated protected
  native edit chains, original receipts, exact scope/source/native baseline,
  conflicting/unknown history refusal, duplicate targets and lost-result/
  restart recovery (#11374; 150 coupled cases, including 20 focused edit
  cases). Existing receiver refusal stays unchanged; this is synthetic source
  qualification, not automatic consumer or provider/installed adoption.

- [ ] Resolve the [comment-identity validation obligations](comment-identity.md#validation-obligations)
  through the real owning preparation, publication, receipt and acquisition
  boundaries, with explicit surface/provider coverage and evidence tiers.
  Preserve every existing attention, native, installed and shadow gate.

- [ ] Qualify the [normal producer-enrollment controls](comment-producer-enrollment.md#serial-delivery-and-qualification)
  through actual source/profile and protected native owner boundaries, not
  supplied callbacks alone. Cover source/policy mismatch, observe-only and
  changed credential bindings, old/withdrawn support, original-key recovery,
  explicit exact admission and read-only/default refusal. Source controls
  cannot substitute for installed/provider or operational authorization.

- [x] Qualify the finite actual source/profile resolver with real managed
  records and profile files, unique canonical root selection, aliased roots,
  reply/edit profiles, pure in-lock composition, original preview recovery and
  missing/prior owner refusal (#11397). The coupled packet passes 62 cases;
  Worktrees origin passes 31; shared attribution/profile passes 120 with four
  existing skips. Cleanup is verified. Normal writer/host/provider adoption
  remains outside this finite source exit.

- [x] Qualify the finite separate credential-delivery path (#11412): exact
  selected scope, current/prior controller and Gateway support, withdrawal,
  observation preservation, mismatched/expired material and sealed native
  principal refusal before ACK. The selected canonical standalone packet
  passes 158 cases with verified cleanup; 4,316 unrelated cases are
  deselected. Provider verification remains synthetic, Fleet typing remains
  pending under #9807, and native dispatch/rotation/unknown effects,
  installed support and host adoption are not qualified by this packet.

- [x] Qualify the finite pinned native request source (#11428): contained TLS,
  immutable material, selected routes, current-policy refusal, bounded/lost
  responses and original unknown-attempt recovery without resend. The final
  coupled executor/feedback packet passes 134 cases; the selected native TLS
  file passes 29, and the earlier overlapping transport/outcome packet passes
  34. Shared-context restoration and final captured-target drift regressions
  fail before their corrections; cleanup is verified. Actual native permission
  verification, normal-host/provider/installed adoption and representative
  cold/warm performance remain open, not inferred from source timing.

- [x] Qualify the finite pre-approval owner data path through actual shared
  preparation, original-key restart/lost-result recovery, contention,
  immutable input, explicit exact admission/native receipts, strict reader
  lifecycle and capacity: 187 coupled cases (#11253).
  Bodyless votes remain bodyless; automatic edits refuse rather than infer
  native identity. This is not producer/wire, provider or installed acceptance.

- [x] Qualify the optional pre-approval CLI/native/Fleet/Gateway source path
  through the existing owner and protected synthetic execution (#11299).
  Cover full-frame precommit limits, full source/target revisions, invalid
  reply targets, normalized original input, original-key restart/lost-commit
  and cache-independent recovery, exact admission/receipts and v1/v2/default/
  rollback compatibility. The protected static-selection packet passes 146
  cases; the final client/selection packet passes 77. These overlap and are
  not live-provider, installed or all-publisher acceptance.

- [x] Qualify the finite local preparation -> exact preview -> explicit
  native/Gateway submission -> durable reopen and confirmed retry source
  path for comments, replies and owned edits (#11237; 133 coupled cases).
  Retained-write profile changes, unsupported receiver/binding and caller
  policy/identity rebinding refuse. Synthetic native responses are not live
  provider, installed or all-publisher acceptance.

- [x] Qualify the finite B4 source contract through existing-owner storage,
  actual protected synthetic readback and negotiated CLI/client/Gateway/service
  consumers: 232 coupled cases, including exact proof, overlapping operations,
  capacity, restart/reopen and strict old/new refusal (#10979). Native,
  provider, installed and shadow evidence remain at their parent gates.

- [ ] Resolve the Author/Reviewer amendment's full
  [validation matrix](author-reviewer-perspectives.md#validation-matrix):
  normalized provider facts, event/cursor semantics, fair role-aware attention,
  bounded retention/tombstone/reopen recovery, shared IO, actual CLI/HTTP parity,
  protected effects and separate container/native/provider/live evidence.
  Existing parent, workspace and conformance acceptance remains open.

- [ ] Complete the author-publication amendment's
  [validation matrix](author-publication.md#validation-agent-recommended):
  caller/target isolation, delegated evidence and source races, exact metadata,
  partial-effect recovery, source-publication policy, observation continuity,
  actual consumer routing and separate native/live/installed evidence tiers.
  Preserve every existing acceptance row and current owner.
  Source controls merged in #10402; remaining live/installed and consumer
  qualification is retained by #10412, not claimed complete.

- [ ] Resolve the caller-driven backend, exact-set and reviewer-selection
  [validation contract](caller-driven-subscriptions.md#validation-agent-recommended)
  through actual author and reviewer consumers. A static configured-target
  watch or per-coordinator backend switch is not equivalent delivery.

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
- [x] Platform: bounded callback launch and shutdown/restart behavior pass on Windows and Linux, using existing contained test tooling (prior source acceptance `39716960139`; current PR-owner installed-profile and exact-release native-account acceptance `42461516789`). macOS has native fail-closed evidence with positive governed support tracked in #11545, not a positive lifecycle claim.
- [x] Source gates: touched-plugin tests, fatal Python lint, module-size, headless-launch, install-contract, and required CI pass; add a changefile for each runtime plugin changed (#7714; current unit-only required policy, not a claim that the whole periodic portfolio passed).
- [ ] Live deployment: exercise an existing, authorized PR target with isolated subscriber/coordinator state when available; otherwise record the exact unavailable lane and a named follow-up before closure.
- [x] Documentation: review the final diff's impact on plugin behavior, failure/retry semantics, fallback, configuration, CLI help, and source-of-truth docs (#7714).
- [ ] Fleet service: resolve every acceptance item in
  [the fleet plan](fleet-pr-service.md#validation-plan-agent-recommended),
  including the single-operator ownership boundary, without substituting mocks
  or a healthy container for the requested deployed path.
- [ ] Artifact construction: resolve the
  [construction validation](artifact-construction.md#validation) at each named
  evidence tier; a built wheel, synthetic lock or public CI run cannot certify
  governed-feed admission or fleet deployment.

- [ ] Complete the matching
  [direct per-origin Validation rows](retained-pr-service-outcomes.md#validation-plan)
  against their full Plan scope and existing linked contract matrices.
  Keep all original cell, provider/native-ADO, credential, service/client,
  image/profile/feed/base, lifecycle/storage/cross-host, installed/fleet and
  performance venues distinct. Source/native/release credit is not an
  installed proof; no universal signature or native-every-host gate returns.
- [ ] Read back every
  [continuing per-origin Coordination row](retained-pr-service-outcomes.md#continuing-coordination),
  retaining original holders, exact actual conditions and #7778's
  nonblocking-consultation rule before separately supported tracker disposition.

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

- [2026-11-09 - First integrated cache/event milestone selected](journal/2026/11.09%20Repository%20cache%20events%20milestone.md)
- [2026-11-09 - Selected native thread-resolution source implementation](journal/2026/11.09%20Selected%20native%20resolution%20implementation.md)
- [2026-11-09 - Selected native reopening source implementation](journal/2026/11.09%20Selected%20native%20reopening%20implementation.md)
- [2026-11-09 - Selected native thread-resolution proposal](journal/2026/11.09%20Selected%20native%20resolution%20proposal.md)
- [2026-11-09 - Standalone report-host contract reviewed](journal/2026/11.09%20Standalone%20report%20host%20contract.md)
- [2026-11-09 - Standalone report-host source implementation](journal/2026/11.09%20Standalone%20report%20host%20implementation.md)
- [2026-11-09 - Opt-in report v2 source qualification](journal/2026/11.09%20Bounded%20report%20v2%20source.md)
- [2026-11-09 - Bounded SDK response owner qualified for source review](journal/2026/11.09%20Bounded%20SDK%20response%20owner.md)
- [2026-11-09 - Size-qualified response contract reviewed; SDK owner first](journal/2026/11.09%20Size-qualified%20response%20contract.md)
- [2026-11-09 - Native report-owner source merged; parent acceptance retained](journal/2026/11.09%20Native%20report%20owner%20source.md)

### 2026-11-09 - Native report boundary corrections

- An empty committed batch is not policy-path absence until the existing
  exact-candidate tree probe confirms it. Missing refs, trees or blobs,
  non-symbolic remote HEAD and unavailable declared policy branches refuse.
  Actual Git corruption/absence fixtures and existing source controls pass
  together in the 97-case target/committed-policy packet.
- Strict anchor-registry acquisition refuses before checkout-free selection;
  a later successful account read cannot erase an earlier anchor failure.
  Registry-derived policy and one-time read-failure controls pass within the
  54-case target-policy suite. Ordinary anchor resolution remains fail-soft.
- Strict selection treats enabled drop-in target loss, unusable targets and
  identity mismatch as unavailable policy, not successful omission. Configured
  plugin activation failures refuse; confirmed disable remains supported.
  The 52-case target-policy suite passes with isolated registry/activation fixtures.
- Confirming an absent policy descendant also validates its first existing
  ancestor. A dangling policy-directory link remains unavailable rather than
  silently removing policy. Three directory-link controls join the leaf-link
  cases across global, repository and marketplace sources; 453 Worktrees
  cases pass in isolation.
- Strict presence acquisition uses raw stat outcomes rather than a fail-soft
  existence predicate. Looping and dangling policy links refuse before account
  selection across global, repository and marketplace sources; confirmed
  optional absence remains supported. POSIX link fixtures pass within 450
  Worktrees cases, and 22 actual native-adapter cases pass in isolation.
- Any unavailable selection checkpoint or capture stays unavailable across
  provider normalization, including transient failures whose next selection
  succeeds. Only actual provider access errors become native error reports.
  The expanded report/Gateway/TLS suite passes 88 isolated cases, including
  capture and cleanup failures inside both native provider builders.
- Strict acquisition also rejects duplicate keys in explicit/global YAML,
  operator drop-ins and managed pointers without changing default loaders or
  sharing retained registry state between strict and ordinary scans. Delayed
  selection cleanup stays sticky across provider error normalization, preventing
  an unavailable checkpoint from becoming HTTP 200. Isolated qualification
  passes 444 Worktrees and 86 report/Gateway/TLS cases.
- Strict committed reads decode actual bytes as UTF-8 on both batch and
  ordered paths, rather than treating a literal replacement character as
  decoding loss. Invalid bytes refuse; valid U+FFFD remains valid policy text.
  The focused Worktrees suite passes 440 isolated cases with both controls.
- Checkout-free selection uses only the target owner's account map when no
  exact repository entry exists, never a sibling repository's account. Strict
  committed text validation applies equally to batch and ordered recovery
  paths. These refinements pass 438 Worktrees cases and 18 actual native-adapter
  cases in isolation; prior 84-case report/Gateway/TLS controls remain unchanged.
- Preserve a separate lightweight final check after the original Gateway task
  completes: deadline, permission and current binding still apply at the last
  event-loop handoff without starting a subprocess. Keep both prepared-response
  and task-completion regressions, including owner replacement.
- Strict committed policy reads refuse unresolved established head/branch/blob
  acquisition and lossy decoding before credentials. Confirmed absence permits
  disk policy, while bounded ordered reads may recover the same committed
  source. Fresh Git fixtures prove a divergent checkout cannot hide a committed
  token helper after acquisition failure.
- The final publication/configuration refinements pass 434 focused Worktrees
  cases and 84 report/Gateway/TLS cases in isolated fixtures.
- Publication-time selection can perform process work and delayed cleanup.
  Keep both asynchronous publication rechecks in shielded, tracked workers so
  unrelated Gateway requests and drain timers remain responsive while the
  admitted request retains cleanup custody.
- Require strict repository policy parsing and refuse failed configuration
  contributions before account acquisition. Preserve ordinary fail-soft
  configuration behavior outside this optional selection path.
- Absolute GraphQL API routes require explicitly nested variables with native
  string/integer types. Provider access failures remain supported native error
  reports, distinct from unavailable transport and cleanup.
- Isolated qualification passed 724 Worktrees cases, including the existing
  authentication suite against the extracted runner, and 80 report/Gateway/TLS
  cases. Current-head CI and genuine approval remain required before merge.

### 2026-11-09 - Native report-owner source qualification pending review

- Implemented the reviewed #11681 contract under #11680 as an optional
  GitHub.com-only source embedding. Reuse actual Worktrees configuration,
  related-target and strict configured-account selection, the selected
  CLI-store/authenticated-login observer and existing report normalization.
  Selection workers and credential/provider children share one request budget;
  absolute API routes avoid CLI auxiliary discovery and ambient routing.
- Isolated Linux qualification passed 360 Worktrees cases and 73 report,
  Gateway/client and TLS transport cases. The final adapter-specific suite
  passes 15 cases, including exact configured store spelling for equivalent
  scopes, policy replacement before publication, oversized captures and
  original Gateway custody during delayed cleanup with and without disconnect.
  These are new fixture resources, not live GitHub credential/provider proof.
- The actor-free required packet passed 4,665 cases against the initial source
  implementation revision; the later account/publication refinements have their
  focused native qualification. Retain the required current-head CI and genuine
  review gates before merge. No installed loader, HTTP override fields, new
  protocol generation, default native behavior or operational enrollment changed.
- Native Windows execution, other providers, large-report transport, retained
  hosting and the broader Plan/Validation remain separate. The implementation
  and its finite source issue are not complete before normal review and merge;
  afterward return to the whole parent rather than finalizing this worktree.

### 2026-11-09 - Native report-owner contract merged; implementation next

- #11681 merged as `c3a404d2e61b6e50909fc13856a41a9bb4699d1e`
  at 02:22 Pacific after genuine current-head Approval `5824491264`,
  no inline findings and all three required CI gates green. Read the full
  review body and native findings before managed completion; only the admitted
  additional CODEOWNER exception applied.
- Normal source reconciliation preserved its backup and the attribution-only
  commit; merged contribution trailers are verified. No parent or source
  worktree was finalized. #11680 remains open for its implementation and tests.
- Returned to the canonical Plan/Validation; its wider acceptance remains open.
  Next is the reviewed finite
  GitHub.com native owner adapter with new isolated fixtures, not actual
  credential acquisition, installed-host configuration, rollout or source
  completion of the broader program.

### 2026-11-09 - Native report-owner source trace and proposal

- Claimed #11680 after checking open report-related work. Traced actual
  Worktrees target resolution, account selection and provider execution:
  legacy ambient authentication, relative GitHub thread routing and independent
  child timeouts prevent treating matching callback labels as service admission.
- Proposed one optional GitHub.com-first owner adapter through the existing
  report embedding. Strict configured-account/store selection, authenticated
  login matching, endpoint-pinned provider calls and the original request
  budget precede body reads; native legacy behavior stays unchanged.
- This is a review-first source proposal, not runtime implementation,
  credential acquisition, installed support or parent completion. Other
  providers, large reports and the retained operational gates remain open.

### 2026-11-09 - Existing-owner report source merged; full parent return

- #11668 merged as `a510df7bfcdb855caa3c121cf66ad49148febf4b`
  after genuine current-head Approval `5824357118`, no remaining findings
  and all three required CI gates green. Normal managed completion used
  only the admitted additional CODEOWNER exception, not a review/check waiver.
- Corrected four runtime findings: stale discovery failure invalidation,
  both asynchronous publication gaps, and endpoint/provider remapping before
  acquisition. The final focused report suite passes 62 cases with verified
  cleanup; initial CLI/watch qualification passes 210 with five Windows-only
  skips. A final documentation-only correction states current guarantees
  directly rather than embedding review history in contract prose.
- Normal source reconciliation preserved a pre-completion backup and aligned
  to the confirmed merge. Contribution trailers remain in the merge commit.
  The finite #11664 source issue is complete; #6007 and its parent gates remain.
- Re-read the complete root Plan/Validation after merge. Credit only the
  finite optional report source; native-command account
  binding, large reports, all-publisher/protected-writer, Dispatch, installed,
  provider, container/shadow and retained-origin acceptance remain open.
- Next source action: trace actual Worktrees report target/account/endpoint
  resolution and credential admission, then derive the smallest review-first
  native report-owner adopter. Callback labels alone are not authenticated
  native selection, and this continuation grants no operational enrollment.

### 2026-11-09 - Existing-owner report source qualification

- Implemented the reviewed #11665 read-only boundary under #11664:
  fixed admitted owner/scope resolution, live checkpoints, finite synchronous
  collection, optional existing Gateway routes, SDK client and strict source
  host-injected facade. Whole native reports and original owner exits remain
  distinct from principal-bound review generations.
- Contained Linux qualification passed 210 tests; five Windows-only native
  CLI cases skipped. Actual TLS/SDK requests and policy/channel/cancellation
  controls retain the original Gateway task through delayed collector cleanup.
  Real Worktrees builders/providers use only synthetic GitHub/Gitea/ADO IO;
  ADO unified diff remains unsupported. Full encoded exchanges above 32 KiB
  refuse instead of truncating or persisting report bodies.
- Fatal lint, install-contract, headless-launch, module-size and docs-consistency guards
  passed. The whole enforced typecheck stopped on an unprepared unrelated
  checker environment, not a reported source diagnostic. No installed/native
  binding, live provider, host activation or parent acceptance is claimed.
  Drive the implementation through normal review/CI/merge, then reconcile the
  complete root Plan/Validation and continue the remaining source parity work.

- [2026-11-09 - HTTP report contract merged; implementation next](journal/2026/11.09%20HTTP%20report%20contract%20merged.md)
- [2026-11-09 - Native diff-read source merged; full parent return](journal/2026/11.09%20Native%20diff-read%20source%20merged.md)

### 2026-11-09 - Native diff-read source qualification

- Implemented the contract reviewed in #11659 through the existing one-shot
  read/configuration and provider owners. The additive native-only frontend
  dispatches once, validates the exact target/report and retains complete
  valid evidence independently of command outcome. No transport, watcher,
  diff archive, mutation or claimant is introduced.
- GitHub diff acquisition now honors its configured endpoint through the
  existing provider host helper; ordinary ambient/default behavior is
  retained when no endpoint is configured. Gitea uses its existing endpoint,
  and ADO remains explicitly unsupported for unified diffs.
- The native packet passes 149 cases with five existing Windows-only skips;
  final actual-owner/read/provider/tracked qualification passes 362.
  Real subprocess fixtures preserve UTF-8/line endings, provider refusal,
  neutral CWD, mixed-version handling and tracked reconciliation/output.
  An initial fixture expected the wrong Gitea API path and omitted the
  generic tracked JSON version; actual boundaries corrected those
  expectations without changing tracked behavior.
- A module-size guard caught the enlarged dispatcher. Keeping on-demand
  provider/account selection in the existing read-context module restores
  the ceiling; the final owner packet also covers existing thread readers.
- Validate provider return fields inside the failure-report boundary, so
  absent or malformed results remain valid failed reports. Reject unknown
  on-demand report verbs before selecting a provider or acquiring credentials.
  Six focused regressions cover those failure classes.
- Require positive support at report dispatch, bound UTF-8 sizing before
  encoding oversized text, and expose only the exception class for an
  unexpected provider failure. Native result errors remain intact; exception
  text cannot become a second path for accidental secret disclosure.
- Actual provider CLI text capture normalized CRLF and bare CR before report
  construction. Add an optional strict UTF-8 byte-capture mode through the
  existing process owner, selected only by explicit diff acquisition; tracked
  calls retain their original normalization. Real subprocess controls prove
  both modes and invalid-UTF-8 refusal, rather than mocking the capture seam.
- Reject malformed configured GitHub endpoints before `gh` and bind supplied
  tokens to all public/Enterprise aliases, preserving absent-configuration
  defaults. Actual synthetic CLI processes prove the selected host/token
  overrides unrelated ambient values. The Gitea diff helper stays under its
  existing curl owner while preserving the module ceiling.
- GitHub vendor-download HTTP 504s blocked disposable fixture preparation.
  Qualification recovered with the same UV version from its canonical
  package feed, independently SHA-256 pinned and older than seven days,
  selected only in generated disposable configuration. Resource limits,
  offline test execution and verified cleanup remain; no tracked bootstrap
  or installed runtime changed. This does not qualify the GitHub bootstrap.
- Fatal lint, install/headless/module guards and documentation consistency
  pass. Both changed plugins remain typing-Pending under #9807; native
  platform, installed/provider, HTTP, workspace and full parent acceptance
  remain open.
- Required CI identified stale exact-source admission for the changed
  project router before running units. Bind the actual router/test bytes
  and add two actor-free exact-command controls, preserving all packet
  guards and budgets. The complete admitted packet passes 4,665 cases
  with verified cleanup; no failed test or source gate is bypassed.

### 2026-11-08 - Native diff-read contract merged; full parent return

- #11659 merged as `02ae0268e0d1b2518d204c9d67090b2709e422f4` at
  23:50 PT after genuine current-head Copilot Approval `5822914588` on
  `83cea490c3099a738e25109b8a6663c760fc2439`, all three findings resolved
  and every required check green. Normal managed completion used only the
  documented additional-CODEOWNER exception; review and CI remained required.
- Normal `pr-complete` retained a backup and removed three redundant local
  commits, aligning source to the confirmed merged base. Contribution
  trailers remain in the merge commit. The parent issue remains open.
- Re-read the complete root Plan and Validation Plan after merge. Credit
  only reviewed intent: receiver/frontend realization and every native,
  provider, installed, HTTP, workspace and retained-parent exit remain open.
- Next source action: implement the additive explicit-target diff read
  through the existing one-shot read and provider owners, then qualify
  actual parser/process and provider behavior. No live activation is granted.

### 2026-11-08 - Native diff-read boundary proposed

- The existing tracked diff operation reconciles a worktree before reading.
  Its provider result does not carry candidate identity or completeness;
  Azure DevOps explicitly cannot supply a unified patch. The workspace
  generation/source reader is a different evidence and custody boundary.
- Proposed one additive checkout-free owner and native frontend using the
  existing provider primitive and process budget. Preserve legacy tracked
  behavior, provider errors, unknown evidence and exact valid reports.
  The new target-qualified path must honor configured GitHub endpoints;
  the existing provider method currently ignores that argument.
- This is review-first intent, not runtime support or provider/installed
  acceptance. Complete CLI/HTTP, workspace, service and retained-origin
  parent outcomes remain open.

### 2026-11-08 - Native thread-read source merged; entire parent return

- #11657 merged as `5a0aeafc01d28f3459f75a88f3462d10fe74b81d` at
  23:22 PT after genuine current-head Copilot Approval `5822662199` on
  `ff228eba468415eecff6567159b0ffe0e50875ad`, the one finding fixed/resolved
  and all three required checks green. Normal managed completion used only
  the documented additional-CODEOWNER exception. No review/CI or live-effect
  gate was waived.
- Normal `pr-complete` retained a backup and dropped three redundant local
  commits, aligning the source to the confirmed merged base. Contribution
  trailers are present in the merge commit.
- Re-read the entire root Plan and Validation Plan after merge and credited
  only the finite native thread-read leaf. Complete retention/attention,
  all-publisher/protected-writer, CLI/HTTP/Dispatch, provider, installed/fleet
  and retained-origin acceptance remain open.
- Next bounded source action: inspect existing explicit native diff/evidence
  read owners, parsers and consumers, then derive the smallest missing
  read-only convergence boundary for review if a gap is confirmed. Do not
  add a second forge transport or treat the thread adapter as full parity.

### 2026-11-08 - Native thread-read source qualification

- Reject option-prefixed repository values in the new native parser before
  either launch path. The shared legacy slug parser accepts them; passing
  one as a positional Worktrees argument could reinterpret it as a host,
  token or config override before later result-identity validation. Direct
  and managed parser regressions retain the pre-dispatch boundary.
- Implemented the contract reviewed in #11655 with one bounded invocation of
  the existing Worktrees report owner. The new native-only parser validates
  required target/runtime selection before direct or managed launch.
- Preserve exact valid reports, including additive/nested fields, incomplete
  or unknown completeness, candidate changes and report errors. Keep invocation
  failures separate on stderr without rewriting owner evidence, inferring an
  empty discussion list, retrying or adding another forge route.
- Isolated thread/routing qualification passes 148 cases with five existing
  Windows-only skips. Actual checkout-free owner/observation qualification
  passes 43, including old and new process callers, synthetic GitHub/Gitea/ADO
  provider report construction, partial ADO reads and changed candidates.
  Cleanup is verified. Windows/macOS, installed/live-provider, HTTP and
  complete parent acceptance remain open.
- Documentation lint has no errors. Both the root effort and PR-plugin
  overview size warnings remain tracked documentation-maintenance work under
  this parent; preserve canonical contracts when extracting linked material.

### 2026-11-08 - Native thread-read contract merged; full parent return

- #11655 merged as `d075b3f3b990b8ca0339948c2f3e05e961ee9dde` at
  22:59 PT after genuine current-head Copilot Approval `5822530375` on
  `27b25b90cf8a2aa353a865df46f1c1ba68696bdf`, no open findings and green
  required checks. Normal managed completion used only the documented
  additional-CODEOWNER exception; no review/CI or live-effect gate was waived.
- Re-read the entire root Plan and Validation Plan after merge. The contract
  clears only the finite design gate: all implementation/qualification rows
  and complete parent retention, attention, publication, CLI/HTTP/Dispatch,
  provider, installed and fleet acceptance remain open.
- Next is the thin native-only thread-read adapter and focused isolated
  owner/parser/process qualification under the reviewed boundary. No new
  transport, watcher, persisted conversation body or thread mutation is added.

### 2026-11-08 - Native thread-read boundary proposed

- Inspected the existing explicit Worktrees parser, neutral-CWD entry,
  target/configuration resolution, report builder, ADO discussion reader and
  dedicated native CLI consumers. Worktrees already supplies the read owner;
  the dedicated client lacks the thin adapter.
- Proposed the existing version-1 baseline without a new capability RPC or
  provider transport. Preserve incomplete/null completeness, changed-candidate
  evidence, nested provider fields and valid error reports; a successful read
  is not writeback or merge authority.
- This is a review-first contract only. Isolated parser/process/provider
  qualification and implementation remain open, alongside the full parent
  Plan and Validation Plan. No runtime, host or consumer activation changed.
- Documentation lint has no errors; its root-effort size warning remains
  open documentation-maintenance work under this parent. Split historical
  journal material without losing canonical Plan/Validation/approval custody.

### 2026-11-08 - Native cancellation merged; entire parent return

- #11653 merged as `0887674795dc64228f973f90798412842d4c536c` at
  22:48 PT after genuine current-head Copilot Approval `5822410308` on
  `3d8b97d923777f3c32e4f9d41c74c83507071cbc`, no open findings and all three
  required checks green. Normal managed completion used only the documented
  missing-CODEOWNER exception. No review/CI or live-effect gate was waived.
- Reconciled the source through normal `pr-complete`: seven redundant local
  commits dropped with the owner's backup retained, source aligned to the
  merged base, required contribution trailers present.
- Read the entire canonical Plan and Validation Plan after merge, not only
  the cancellation sub-contract. Credited only the finite source leaf above.
  Retention/attention, all-publisher/protected-writer, full CLI/HTTP/Dispatch,
  three-provider, installed/container/shadow and retained-origin gates stay open.
- Next bounded source action: inspect existing explicit native thread-read
  parser/result and consumer contracts, then propose the smallest missing
  read-only CLI convergence boundary for review. Remote close/reason-comment
  work remains separate; no new forge transport, ledger or live effect is
  authorized by this selection.

### 2026-11-08 - Native cancellation source qualification

- Current-head Balanced review had no open issues but requested human
  assessment of qualification history versus current guarantees. Replaced the
  contract's iterative test-count narrative with a current-evidence table and
  explicit remaining qualification; retained the complete correction history
  here. No runtime guarantee or validation result changed.
- Full review then identified missing blank-selector admission on the
  governed peer path. Reused the existing parser validator before peer
  selection/discovery; three managed-peer controls reproduce that boundary.
  Final affected isolated suite: 149 passed, five platform skips.
- The next bounded re-review added two distinct findings, repeated at five
  locations: missing-stderr fallback and original-target recovery evidence.
  Centralized text-only stderr handling and added frontend-owned original
  selectors/reason without altering receiver evidence. The final affected
  isolated subset passed 146 tests with five platform skips.
- A subsequent Comment review could not run its full agentic suite. Addressed
  missing-stdout diagnostics and redundant error fallback; retained malformed
  owner fields under the reviewed payload-preservation contract. Documented
  and tested full-receipt success output as the existing native convention,
  rather than silently changing it to a lossy summary. The final affected
  isolated subset passed 143 tests with five platform skips.
- Current-head review on #11653 found that plain owner-error output hid the
  frontend's unknown-outcome recovery guidance. Retained the owner error and
  printed the distinct diagnostic; the affected isolated cancellation/routing
  subset passed 139 tests with five platform skips, including two plain-output
  controls. A fresh review is required for the correction.
- Implemented the contract reviewed in #11651 as a thin native-only
  `cancel-creation` frontend. The same selected receiver supplies read-only
  support/attempt discovery and one existing cancellation-owner dispatch.
  Original worktree, creation, revision and reason remain selected; no forge
  transport, cancellation algorithm, resource ledger or remote close was added.
- Bounded isolated native routing/process controls passed 167 tests with five
  existing platform skips. Actual creation/status/cancellation composition,
  receipt recovery, source/sibling/claim custody and an old caller subprocess
  passed 82 tests. A committed cancellation with a lost response remains
  `unknown`; no fallback or automatic replay occurs.
- Two fixture corrections preserved the real owners: select a file rather
  than the fixture's existing worktrees directory, and supply the fixture's
  admitted project context. The legacy output control uses the real JSON
  output boundary. Bootstrap HTTP 504 recovery changed only a disposable
  vendor-download URL while retaining the pinned hash and isolation.
- Source implementation is awaiting its own review. Windows execution,
  installed/provider/HTTP qualification, remote abandonment/comment/claim
  integration and the complete parent acceptance remain open.

### 2026-11-08 - Merge the native cancellation contract and return to implementation

- #11651 merged as `50b618a194ef8a5e11e6effc30ed4d4b21184dc1`
  after genuine current-head Copilot approval with no findings and all three
  required checks green. Normal source completion retained the existing
  parent and source claims; the contract does not implement its frontend.
- Re-read the complete parent Plan and Validation Plan after merge. Continue
  the reviewed native-only local-cancellation adapter, actual selected receiver
  discovery and isolated owner/process qualification. Remote abandonment,
  all-publisher comment identity, HTTP, provider, installed and operational
  acceptance remain open.

### 2026-11-08 - Merge the abandonment prerequisite and select local cancellation

- #11649 merged as `d84413cc0d34c2c4300a7778f8bfc8351a19af93` after
  current-head Copilot approval with no open findings and every required
  CI check green. Both review findings were corrected and resolved; the
  final 183-case isolated packet passes with verified cleanup.
- Confirmed merged attribution and reconciled the owned source through normal
  PR completion without finalizing or releasing parent/source custody.
  Re-read the complete parent Plan and Validation Plan; the broader native,
  HTTP, provider, installed and operational gates remain open.
- Select the smallest independent continuation: review a native
  unpublished-creation cancellation frontend over the existing v1 owner and
  read-only status declaration. It preserves exact original revision/reason
  recovery and does not expose remote close or silently bypass unfinished
  reason-comment identity and partial-effect contracts.
- The new document is a proposal, not implementation or native/platform proof.
  No live provider, host activation, deployment or resource effect occurred.

### 2026-11-08 - Preserve abandonment custody after combined provider failures

- Remaining CLI inventory inspection distinguishes unpublished-attempt
  cancellation from remote closure, its best-effort reason comment and claim
  settlement. Neither terminal observation nor `--confirm` supplies operator
  authority. Native successor integration remains open.
- Found a directly coupled legacy defect: a GitHub close failure with an
  appended comment-failure warning was accepted as success because the owner
  searched for the warning text anywhere in the result. Recognize only the
  existing warning-only prefixes; preserve close errors, open state and claims.
  CLI syntax, provider result shape and successful-close semantics are unchanged.
- The actual GitHub provider transport fixture now covers both failures and
  confirms no abandoned history or claim release. Existing warning-only forms,
  cancellation and provider conformance pass: 183 isolated cases with cleanup
  verified. The initial packet found a test-owned empty-timestamp assertion and
  a Git-only finalization fixture missing its existing no-mux binding; corrected
  both without weakening the shared-host command guard.
- Fatal lint, install-contract and headless-launch checks pass. This finite
  prerequisite is not native/HTTP parity, prepared reason-comment adoption,
  installed acceptance or permission for live closure. Continue the inventory
  and both complete parent gates after reviewed merge.

### 2026-11-08 - Merge native reviewer requests and resume the remaining CLI inventory

- #11647 merged as `20cbf5c3b075162415d7f7e1e5fc129afe90df64` after
  genuine current-head Copilot approval, both findings fixed and resolved,
  and all required checks green. The required PR gate was explicitly
  awaited; no review or CI waiver was used. Merged provenance is verified.
- The final shared author-policy correction rejects reserved worker
  bindings with missing or mismatched root markers before ambient loading,
  including capability reporting. Review-history prose stays in this
  Journal; the qualification document describes the current invariant.
- Re-read both entire parent Plan/Validation sections after the actual merge.
  Their 82 public and 11 companion unfinished rows remain open. Complete
  CLI/HTTP, all-publisher, retention, Dispatch, provider, installed, shadow
  and presentation acceptance are not implied by this finite source leaf.
- Next: inspect the remaining abandonment/close CLI owner, operator-confirmed
  intent and reason-comment behavior before selecting a compatible source
  contract. Preserve publication identity and existing resource custody;
  no live closure, credential work, host activation or rollout is authorized.

### 2026-11-08 - Qualify the bounded explicit reviewer-request source candidate

- Implement the reviewed optional target-qualified operation through the
  existing Worktrees/GitHub provider and conditional native client. The
  tracked nudge transport and no-op behavior remain unchanged; explicit
  requests preflight active non-draft identity and preserve accepted,
  rejected and unknown outcomes without retry or fallback.
- The isolated owner/provider/legacy packet passes 398 cases; the final
  request-owner subset passes 33, overlapping the packet and adding four
  explicit target/policy refusals. The native packet passes 116 with five
  Windows-only skips and real-process forwarding. Cleanup is verified.
- The first broad provider packet exposed four old creation fixtures that
  forbade even local authority resolution. They now allow the existing
  reservation owner and assert zero forge creates when auto-open is off;
  the failure control injects the actual create API instead of preventing
  reservation. No production reservation or publication guard was weakened.
  Two initial native assertions were corrected to expect argparse's actual
  strict `SystemExit(2)` rather than an ordinary return code.
- Source review, required CI, native Windows, live-provider, installed and
  HTTP acceptance remain open. No host activation, credentials, rollout,
  protected writer or parent completion is claimed.
- Review found that an explicit reserved worker binding without its
  `repo_name` marker could reach ambient policy. The shared author loader now
  rejects that malformed shape before config resolution, for discovery and
  every dependent author operation. Ordinary full project configurations
  retain their supported path. Eight focused regressions and the coupled
  request/author/readiness/worker packet pass 180 cases with verified cleanup.

### 2026-11-08 - Merge the native reviewer-request contract and return to parent gates

- #11645 merged as `92797be51c5006d9a59aedb2f99e02bdd32c6f29` after
  current-head Copilot approval with no findings and all required CI green.
  The merged commit retains its required provenance trailers.
- Re-read both entire parent Plan/Validation sections after the actual merge.
  The 82 public and 11 companion unfinished rows remain open; the proposal
  does not deliver a command, activate a host or qualify provider parity.
- Next: implement the bounded GitHub/provider and conditional native-client
  path under the reviewed contract, then exercise actual owner composition
  and mixed-version refusal without altering tracked nudge behavior.

### 2026-11-08 - Specify explicit native reviewer requests before implementation

- The existing CLI inventory's nudge gap is distinct from tracked Worktrees
  selection. Its provider result records API acceptance, not a fresh verdict;
  a generic subprocess failure cannot prove a request was not accepted.
- The [finite proposed contract](native-reviewer-request.md) preserves tracked
  syntax and no-op behavior, selects the existing target/provider owner and
  distinguishes accepted requests, known no-ops, refusals and uncertainty.
  Actual parser/provider, native-client and mixed-version validation remain
  open pending review-first implementation.
- No runtime, live request, installed adoption, legacy retirement or parent
  acceptance row is completed by this source-only proposal.

### 2026-11-08 - Merge native draft readiness and resume the CLI inventory

- #11643 merged as `15601b6c7e5daa174dcfeb020fa2b7e50b6d9c3d` after
  current-head Copilot approval with no findings, resolved threads and all
  required CI green. The PR gate was awaited explicitly; two initially
  reported green guards did not substitute for it.
- The reviewed corrections preserve pre-dispatch refusals and diagnostics,
  require provider evidence before dispatch, retain a versioned uncertain
  result with its known provider, and cover the actual projectless dispatcher.
  Final isolated source qualification is 54 owner and 84 native cases with
  five Windows-only skips; source reconciliation retired only the five
  redundant merged commits through the managed path.
- Read both complete parent Plan/Validation sections after merge: 82 root and
  11 companion rows remain unfinished. No provider, installed, HTTP parity,
  full retention, all-publisher or parent completion is inferred.
- Next: inspect the remaining native reviewer-request/nudge gap through its
  existing Worktrees/provider owner and unsupported/no-op semantics, then
  select the smallest compatible source refinement before changing behavior.
  Protected credential work and host activation remain outside this slice.

### 2026-11-08 - Qualify explicit native draft readiness source

- The candidate implements the reviewed optional `draft-ready` operation through
  the target's existing provider/transport owner. Explicit Worktrees and native
  author parsers preserve the old tracked invocation, draft-only effects,
  active/identity readback and failed-versus-unknown outcomes without replay.
- The final isolated source selections pass 54 Worktrees and 84 native frontend
  cases, with five Windows-only skips and verified cleanup. Initial fixture
  failures were missing required repository fields and the expected worker-file
  argv order; corrected fixtures retain the production semantics.
- Review found two pre-dispatch outcome errors in the native client. Capability
  launch/timeout failures now remain not-attempted; valid claimant refusals
  preserve their null provider and actionable diagnostic. Actual frontend/
  Worktrees refusal and read-only probe controls pass; attempted outcomes
  still require the selected provider and lost write results stay unknown.
- The next review identified missing pre-dispatch provider validation and
  unversioned synthesized failures. Malformed declarations now refuse before
  writes; synthesized results retain the versioned envelope and known provider.
  The final native packet and both actual owner-result controls pass.
- The projectless dispatcher now emits the same not-attempted result before
  the handler runs. An actual delivered-policy capability probe followed by
  unregistered-CWD refusal, resolution exceptions and strict syntax controls
  pass without weakening the claimant/project gate.
- Fatal lint and the 13-plugin installation contract pass. The typing inventory
  keeps both changed plugins pending; the broader enforced preflight stopped at
  an unprepared unrelated `lib-config-migrate` checker environment after its
  preceding scopes passed. No plugin strict-typing or full-preflight pass is
  claimed. Live/provider, Windows, installed, HTTP parity and parent acceptance
  remain open; current-head review and required CI still gate this candidate.

### 2026-11-08 - Merge the finite draft-ready contract and return to parent gates

- #11641 merged as `7aa41656fc5fe0e4055defd8c4333ba89e2a119f` after
  current-head Copilot approval, no findings and all three required checks.
  The earlier approval addressed the pre-attribution head and was not used
  as current-head authorization; an ordinary re-request obtained a fresh pass.
- Read both complete parent Plan/Validation sections after the actual merge.
  Their 82 public and 11 companion unfinished rows remain open. No runtime
  readiness, provider, installed, HTTP parity or parent completion is claimed.
- Next: implement the optional target-qualified provider/native author path
  and its focused source controls under the now-reviewed contract. Existing
  tracked ready/legacy-hold behavior and all effect/rollout gates stay intact.

### 2026-11-08 - Specify draft-to-ready native convergence before implementation

- The existing CLI inventory's ready row remains open: the dedicated native
  client has no readiness verb and the Azure DevOps provider explicitly refuses
  un-draft. Tracked ready selection cannot stand in for an explicit foreign PR.
- The [bounded contract](cli-parity-inventory.md#draft-to-ready-native-convergence)
  preserves all tracked syntax, adds optional target-qualified author support,
  separates draft state from holds/consent and requires honest readback outcomes.
  Its parser/provider, native-client and mixed-version validation remain open.
- This is review-first source planning under #6007, not provider implementation,
  installed adoption, a live write or closure of any parent acceptance row.

### 2026-11-08 - Select the standalone terminal mapping reader

- #11639 merged as `5cc6cd6d87d539866149e1d162c503f7e6a40767` after
  current-head Copilot approval with no findings and all required CI green.
  The first managed merge correctly refused the not-yet-reported PR gate;
  the ordinary watcher observed success before the next managed attempt.
- `workspaces.terminal_mapping_reads` is explicit, default-off and requires
  terminal reads. The host checks the executing optional declaration, adopts
  only under fresh-root lifecycle exclusion and never repairs a retained
  volume. Prepared-intent storage preserves original metadata without
  selecting preparation, native mutation or feedback owners.
- The final standalone packet passes 132 cases with confirmed cleanup;
  strict typing passes all 16 service targets for Linux and Windows analysis,
  and fatal lint, module size, documentation and install guards pass. Earlier
  failed fixture attempts identified live-epoch/generation seeding and the
  cleared cache's current value; corrected fixtures use the existing owners
  rather than weakening production admission.
- Re-read the complete current root Plan/Validation and the companion's
  acceptance after this merge. The root still has 82 unfinished rows; the
  companion has 11. No phase, provider, installed/shadow, all-publisher,
  CLI/Dispatch, fleet or whole-retention gate closes from this finite leaf.
- Next: return to the existing publication/CLI/consumer inventories and select
  one finite source convergence gap. Protected credential-source admission
  stays unavailable; no host activation, retained-volume transition or rollout
  is authorized by this source milestone.

### 2026-11-08 - Land terminal intent, retirement and a read-only receiver

The [terminal-retention journal](journal/2026/11.08%20terminal-retention.md)
records T1/T2 source merges #11605/#11614/#11623, their reviewed race/replay
corrections and still-open T3 and complete parent acceptance.

### 2026-11-08 - Review coordinated terminal retention before realization

The [terminal-retention record](journal/2026/11.08%20terminal-retention.md)
records the finite plan merge and the next T1 storage/admission/notification
source slice. P1/P2 and runtime retention acceptance remain open.

### 2026-11-08 - Expire journal metadata during quiet observations

- The existing P1/P2 lane now removes the aged metadata suffix on admitted
  unchanged observations while retaining the last cursor anchor. Expiry does
  not allocate events, acknowledge a cursor or promote feedback. Terminal
  summaries and other replay-owner state retain their existing custody.
- The focused journal, workspace-projection and CLI/HTTP integration packet
  passes 53 cases under the bounded local runner. Both disposable-container
  attempts failed during the pinned `uv` download with HTTP 504, before
  collection, and verified cleanup; this is not container qualification.
  Fatal lint, documentation consistency and install-contract gates pass.
- Acquisition-owner inspection confirms that the current relay returns text,
  not authoritative acquisition metadata. Keep protected review unavailable;
  internal Azure CLI credential methods are not a supported relay boundary.
  The existing enrollment contract retains that dependency without probing
  credentials or enabling another source.
- Source #11574 merged as `2eeb74efebd9446b53b97d8c5cae331b04133bb2`
after current-head Copilot approval `5817530673` with no open findings and
latest CI `42471468624` / source checks `42471468097` passed. Managed
completion preserved the verified attribution-only bookkeeping commit and
its pre-complete backup; the source worktree remains owned.
Coordinated body/cache/outbox
  retention, all-publisher/provider/installed qualification, full
  CLI/Dispatch and shadow/presentation outcomes remain open. No activation,
  deployment or existing resource release is implied.

### 2026-11-08 - Select acquired mappings in the existing standalone host

- #11561 merged as `139f39067c6b18159366550b807f8ed0f85524eb`.
  The current-head Comment verdict was assessed under the owner-authored
  review policy, with actionable findings fixed and explained findings
  resolved. Required current-head checks passed; no further approval
  round was required after that accepted condition.
- Final standalone qualification passes 105 cases; strict service analysis
  passes all 14 files on Linux and Windows targets. The actual foreground
  fixture qualifies a present preparer before later withdrawal and restart,
  using a synthetic original receipt seeded before its writer starts.
- Re-read the parent Plan/Validation and close only the two finite host
  leaves above. The next protected-host prerequisite is still actual
  supported acquisition-source grant/audience and current-custodian
  verification, not observation credentials or injected callbacks.
  All-publisher, provider/installed, full retention/CLI/Dispatch and
  shadow/pane acceptance remain open. No live host was activated or updated.

### 2026-11-08 - Correct canonical legacy install-root negotiation

- Implement the narrow #11565 source correction: capture only an absolute,
  normalized canonical legacy home install root as intrinsic routing.
  Namespaced receipt validation and every other caller override remain
  unchanged; this is not a new credential-context capability.
- The canonical contained CLI/context selection passes all 70 cases without
  skips, including default-root negotiation and foreign, relative and
  default-plus-token refusal. An actual source-bound Python `-I` process
  confirms those routing decisions without mocks or service effects.
  Ruff `F,E9`, install-contract and documentation-consistency checks pass.
- The equivalent-root spelling control also passes: retain the original
  environment value after proving normalized equivalence, because the
  credential guard compares that original value exactly. The expanded
  focused selection passes all 71 cases without skips.
- These are unit/simple-process results, not full deployed-consumer evidence.
  Preserve the still-open installed consumer, configured-credential,
  live-service/fleet and retained-resource gates. No additional hosted run,
  shared-service mutation or local adoption occurred in this correction.

### 2026-11-08 - Merge the finite prerequisite record and continue deployed-consumer validation

- #11555 merged at `6fb38bc450431e806fdc4b525d5f49dde5c77001`.
  Exact-head Copilot review `5817296462` approves the documentation-only
  prerequisite record with no open issues; required CI `42465432427` passes.
  No runtime, dependency or CI behavior changed, and no additional native run
  or local rollout was performed.
- The complete parent remains Active. Current main Plan/Validation has 84
  unchecked items; the per-origin annex retains 73 unchecked checks. The
  finite installed-cell/lifecycle exit is qualified, not the broader
  configured-credential, live-consumer, service/fleet or resource disposition.
- Next, exercise the installed Dispatch/PR-owner consumer contract in isolated
  state against an existing authorized PR target. Preserve shared coordinators,
  subscriptions and peers; distinguish real provider/credential observations
  from synthetic controls and do not enable production delegation.
- The next-path preflight finds #11565: the generated native Dispatch command
  assigns its canonical default install root, but legacy context capture gives
  negotiation no intrinsic environment. The actual deployed `0.18.306-dev1`
  guard accepts a clean caller and rejects that assignment alone, before any
  owner capability probe. Current source has the same logic.
- Correct only canonical absolute default-root routing, retaining credential,
  nondefault-root and relative-root fallback and all namespaced ownership
  checks. The existing source integration fixture's synthetic runtime markers
  and source-path injection are not deployed acceptance; validate complete
  installed bits and real intrinsic routing after the source correction.

### 2026-11-08 - Clear the finite installed-owner prerequisite with exact-release native evidence

- The single authorized native run `42461516789` completes successfully at
  `c310f4b4c2d09b8cf352d4ab29477904a8f2582b`, whose tree is identical to
  release #11489 and the locally tested PR-owner `0.3.249-dev1` payload.
  All 15 selected jobs pass; 68 unrelated workflow entries are skipped.
- Retained logs account for 116 passing tests. Linux's seven skips are five
  unavailable Windows PowerShell wrapper cases and two Windows window/focus
  controls, all of which pass on Windows. Core native-account upgrade,
  two shipped cells, peer roundtrip and both lease-only refusals pass on both
  OSes with native home/profile equality and fresh-state checks.
- Controller-death boundaries pass 17 cases per OS and concurrent upgrade
  passes one per OS. Original case, cleanup, containment and job budgets
  remain unchanged. Together with four passing installed-profile cases and
  independent local exact-exit readbacks, this closes the finite #6286
  installation/lifecycle prerequisite, not the whole #6007/#7188 objective.
- Native macOS startup refuses unsupported owner birth evidence before
  control, signal, spawn or state publication; absent-owner stop is bounded.
  Positive governed macOS identity, cleanup and recovery remain explicitly
  tracked in #11545. This is the declared fail-closed disposition, not a
  positive macOS operation or an unverified-signaling exemption.
- Authorized runtime readback confirms Dispatch `0.18.306-dev1` selected and
  running, with all 410 production Python files matching the same release.
  PR-owner selection remains `0.3.249-dev1`. Next, exercise the deployed
  consumer contract in isolated state while preserving credentials and every
  shared-service, fleet, resource and live-activation authority boundary.

### 2026-11-08 - Qualify installed lease-only refusal before native-account acceptance

- Both installed `0.3.249-dev1` normal-payload lease-only variants pass:
  reachable and unreachable endpoint cases, zero skips, 266.25 seconds,
  within unchanged 600-second case and 1200-second containment bounds.
  The original owner receives no installer control, no successor starts,
  and selected metadata, routes and pending state remain unchanged.
- Each test then gracefully stops only its exact original owner. Independent
  non-signaling native readback confirms both originals exited.
- Together with the original upgrade/restart scenario and two-cell isolation,
  all four selected installed-profile cases pass. These are not fresh native
  OS-account or shared-consumer results; both installed prerequisite gates
  remain open for complete acceptance.
- The existing native-account workflow can exercise Linux/Windows owner
  scenarios and boundary cases plus macOS fail-closed behavior on disposable
  hosted runners. Its existing release branch still names the exact qualified
  candidate whose tree matches the locally installed release. No current
  native run covers that release, and the earlier focused hosted grant has
  completed. A new hosted dispatch requires separate operator authorization;
  no new dispatch, deployment, source publication or resource release occurred.

### 2026-11-08 - Qualify two independently installed PR-owner cells

- The installed `0.3.249-dev1` payload's original two-normal-cell scenario
  passes: one test, zero skips, 683.00 seconds, within its unchanged
  1140-second test and 1200-second containment bounds.
- Both cells use normal payload installation and invocation. State,
  registration identities and endpoints remain separate. Foreign-context
  reads, stop/restart/install requests and cross-cell owner-control requests
  refuse without changing journals, routes, subscriptions or the other owner.
  Stopping the first cell leaves the second healthy.
- Governed cleanup passes; independent non-signaling native readback confirms
  both exact owners exited. This is installed-payload isolated-profile
  evidence, not native-account, shared-service, fleet or delegation activation.
- The installed normal-payload lease-only refusal variants are qualified
  above. Native-account acceptance remains distinct; the complete parent
  remains active.

### 2026-11-08 - Verify the released validation improvement before installed adoption

- Normal promotion `42441823658` passed its frozen-candidate, source-unit,
  Windows Resume, artifact, attestation, publication and exact-candidate merge
  gates. Release #11489 merged at
  `d4d48c0ea6bef6c283b221a05311058cd9956749`. All 64 production Python files
  changed by #11472 match the admitted source byte-for-byte in that release.
- The release stamps PR-owner `0.3.249-dev1`, Bridge `0.9.364-dev1` and
  Worktrees `1.25.26-dev1`. The separate #11153 acquisition migration repair
  remains present alongside the later schema migration.
- The authorized local unified update completed successfully through its
  existing serialized execution owner. Current payload/selection readback
  confirms the versions above; Bridge and Worktrees also report those running
  versions. All 16 changed PR-owner production/test files match release bytes.
- The installed payload's original normal lifecycle scenario passes: one test,
  zero skips, 707.63 seconds including governed cleanup. First owner startup
  completes in 108.04 seconds. Live upgrade, same-version restart, pending
  subscriptions, immutable history/completion and obsolete-process exits pass
  within the original scenario, owner, cleanup and containment limits.
  Independent non-signaling readback confirms both final transaction processes
  exited. #11231 is closed with that finite installed-profile evidence.
- The first fixture failed before owner startup because a 272-character build
  working directory exceeds native Windows process-launch support. A
  launch-only control reproduces that refusal; the accepted fresh fixture
  uses 243 characters without changing the shipped test or payload.
- The separate two-normal-cell contract is qualified above. This is not
  native-account, shared-service or delegation activation proof. #6007 and the
  broader installed, provider, fleet and retained-resource outcomes remain open.

### 2026-11-08 - Repair Source workflow context and resume release qualification

- The normal Dev push exposed a workflow compilation failure before execution:
  the installed Manager job used `runner.temp` in job-level environment,
  where that context is unavailable. #11484 moves the unchanged acceptance
  directory into an early runner step and retains the native gate, receipt
  requirements, permissions and budgets. All 37 focused workflow controls,
  actual PowerShell environment handoff, fatal lint and required CI pass.
- #11484 merged at `5e0fe365335305d51c87b3b3b5b668a83db9251f`
  after exact-head Copilot approval `5816295430` with no open issues and
  required CI `42441608996` passed. This is Source repair, not independently
  governed Main controller activation or a new native qualification claim.
- The normal promotion run `42441823658` admits source
  `57952d35f1d98800bf8b4980d29a3d7007dbd741`, including #11472. Its
  frozen-candidate, unit, Windows Resume and artifact validation must finish
  before release content and authorized local adoption can be qualified.
  The parent installed, service, provider and retained-resource outcomes remain
  open; no operational hold or custody boundary is released.

### 2026-11-08 - Merge the qualified fresh-validation source improvement

- #11472 merged at `57952d35f1d98800bf8b4980d29a3d7007dbd741`
  after final-head Copilot approval `5816236964` with no open issues and
  required CI `42440720357` passed. The two changed canonical unit-source
  admissions and two runtime-admission generated SDK copies are synchronized;
  the unit inventory, coverage targets and budget are unchanged.
- The integrated original normal-payload lifecycle passes locally within its
  original limits, alongside the focused driver, native entry, guarded-journal
  and real TCP controls recorded below. This closes the source correction
  slice, not #11231's installed acceptance or any broader parent outcome.
- #11153's separate Bridge migration prerequisite has also merged. Next,
  confirm both fixes in a promoted release before the already-authorized
  same-machine unified update and isolated installed-owner qualification.
  Preserve the prior partial-update evidence, existing sessions and pending
  state; no force, live delegation activation, fleet effect or retained-resource
  release is implied. #6007 and its installed/service/provider/resource rows
  remain open.

- [2026-11-07 - Preparation-only producer sequencing](journal/2026/11.07%20preparation-only.md)

### 2026-11-07 - Deliver native effective review-permission evaluation

- #11462 actually merged as `ee2fc4f817681598a999b091b12806f033e536cd`
  after corrected-head Copilot approval `5816104058` with no open issues and
  latest required CI `42437653463` / `128601798356` passed. Managed completion
  reconciled only the merged source and retained its backup.
- The existing pinned provider resolves actual Git namespace/action semantics
  and separately evaluates read and review permission for the repository and
  exact native PR target branch. Both complete positive results are required
  for each action bit, without an administrator shortcut, write probe,
  credential reacquisition or undocumented combined-mask assumption.
- The first review exposed rejection of native translator-backed Git
  definitions. The regression failed before correction; both boolean native
  forms now preserve the documented logical token. Primary contained TLS
  exercises the translator-backed form. Final native parser/TLS tests pass
  71 cases, with 145 executor/feedback/request-local lifetime cases; bounded
  cleanup is verified. Ruff, docs, module size, installation contracts and
  source pre-commit pass. Pending runtime type admission is not qualified.
- Pre-fence denial leaves an unattempted action; later denial preserves its
  original unknown attempt. Existing action/candidate/owned-target policy,
  legacy context embedding and normal-host read-only defaults remain intact.
  These finite source controls are not live permission or installed proof.
- Re-read the complete current Plan/Validation and close only these finite
  evaluator leaves. Continue the separate authoritative credential-source
  grant/audience and current-custodian verifier before strict host enrollment.
  All-forge publishers, acquisition/DB mapping, full attention, provider/
  retention/CLI/Dispatch/installed/shadow acceptance and #11128 remain open.
  No host/backend activation, deployment, production effects or resource
  release occurred.

### 2026-11-08 - Qualify the integrated source consumer before publication

- Integrated #11231 with the current source dependencies without dropping
  either journal history. Fresh static lint, generated-copy, module-size,
  installation-contract and diff checks pass.
- Fifteen focused driver, native entry, guarded-journal and real TCP controls
  pass in 345.77 seconds, including cold start, rollover, durable-commit
  admission, rollback and exact-instance retirement. The canonical runner
  completed preparation and cleanup in 402.50 seconds overall.
- The integrated original
  `test_normal_payload_init_refreshes_receipt_and_upgrades_live_owner` passes
  locally in 660.44 seconds, with 681.66 seconds overall including preparation
  and cleanup. Its startup, upgrade, restart, state-preservation and cleanup
  assertions ran with the existing 120-second cutover, 900-second test and
  150-second cleanup limits. No native-account override was enabled.
- This qualifies the composed source consumer, not the previously installed
  owner or a general latency guarantee. The earlier hosted pass remains scoped
  to its recorded source; its completed approval was not reused. Review, CI,
  promotion, authorized installed adoption and all parent/resource gates remain.

### 2026-11-08 - Pass the original focused hosted Windows lifecycle case

- [Focused Windows run 42434040443](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42434040443)
  qualified source `834856ee875cc9f7b2ed62bd59401d162327c365` under its protected
  controller. The request-bound artifact identifies exactly the original
  `test_normal_payload_init_refreshes_receipt_and_upgrades_live_owner`: passed
  in 229.621 seconds, with no skipped or incomplete selection. Its original
  startup, upgrade, restart, state-preservation and governed-cleanup assertions
  completed without enlarging their clocks.
- This is Windows Server 2025 source evidence with the reported outer test
  runtime Python 3.12.10. It does not replace the failed local installed-profile
  evidence or qualify every runtime/platform. The approved hosted loop's
  stopping condition is satisfied; retain its source/request/run evidence and
  append-only transport history rather than launching another attempt.
- Complete dependent source regressions and normal review/CI/promotion before
  treating the improvement as released. Local installed-owner acceptance,
  authorized rollout and the larger parent/resource gates remain open.

### 2026-11-08 - Qualify the paired-read candidate through the focused Windows lane

- #11231 retains bounded concurrent fresh target/predecessor reads only after
  the private journal matches its guarded current document. Both reads finish
  before the action, including on failure. Existing nested SDK validation
  scopes remain serial to preserve their shared changed-file observations.
  Thirteen focused boundary cases pass across the original and residual
  selections; no cached authority or expanded deadline was introduced.
- The original local lifecycle remains unqualified. Phase output now preserves
  its first failure before cleanup can obscure it; the latest run exceeded
  120 seconds during committed-owner health at first startup, then completed
  governed cleanup. Earlier successful startup runs do not establish reliable
  whole-lifecycle acceptance.
- The operator approved a progress-based source fix/retest loop through the
  existing owner-only `windows-qualification.yml`, selecting the original
  normal-payload lifecycle case and stopping when it passes. Preserve the
  original test/cutover/cleanup clocks, committed source identity, request/run
  evidence and the lane's protected-controller and append-only transport rules.
  Unchanged or unobserved attempts are not progress.
- Hosted source evidence does not replace local installed-owner acceptance.
  Source review, CI, promotion, authorized rollout and all parent delegation,
  provider and retained-resource gates remain outstanding.

### 2026-11-07 - Pass first startup and isolate the remaining upgrade race

- #11231 now composes freshly validated journal target/instance evidence within
  each action instead of reconstructing it again. Actual health/control
  exchanges keep fresh completion and native-birth checks on both sides;
  selection publication checks before and after the write under its metadata
  locks. No proof cache or cutover deadline change was introduced.
- Nine focused journal/selection/archive controls pass. The original Windows
  scenario then passed first startup: binding took about 36 seconds and native
  publication about 68 seconds. The whole lifecycle still failed during upgrade,
  after predecessor drain, on a changing JSON read; route rollback subsequently
  exhausted its remaining deadline. This is progress, not lifecycle acceptance.
- The canonical JSON reader now names the file in changing-read diagnostics
  without altering its refusal or handoff semantics; all sixteen focused JSON
  and lock-handoff controls pass. A narrowed normal-init/startup/upgrade
  diagnostic retains named phases, the original 120-second cutover and governed
  cleanup to identify the actual race rather than repeat a broad packet blindly.
- Source work remains useful WIP. Required review, CI, promotion, authorized
  installation and installed-owner acceptance remain outstanding, as do
  Bridge #11153 and the parent delegation/provider/fleet/resource gates.

### 2026-11-07 - Retain the unresolved native startup deadline separately from hashing work

- #11231's bounded path-walker improvements preserve full snapshot traversal,
  exact digests, native identity and metadata fences, reparse rejection and
  joined descriptor cleanup. Ten focused controls and strict Linux/Windows
  typing pass. These source results do not establish lifecycle acceptance.
- The original normal-upgrade scenario still fails at first startup, before
  upgrade, within its unchanged 120-second cutover budget. The successor
  bound after about 75 seconds; rollback and exact-instance observations
  remain explicit failure evidence, not successful activation.
- Retain the tested source work without publishing a complete-fix claim.
  Further direction must distinguish reducing validation work within the
  existing budget from deliberately changing the bounded latency contract.
  No deadline, verification rule, installed runtime, delegation activation
  or retained-resource disposition has been changed by that distinction.

### 2026-11-07 - Diagnose the actual installed first-owner startup failure

- The released `0.3.227-dev1` payload's own normal-upgrade test was exercised
  in an isolated Windows profile with canonical repository process containment.
  Initial installation completed, but first owner startup exhausted its
  original 120-second cutover budget before the upgrade step. Normal cleanup
  also timed out during reconciliation; recorded test processes have exited.
  This is failed installed-profile evidence, not native-account acceptance.
- A read-only profile of running invocation capture took 14.811 seconds;
  its two snapshot-content traversals consumed 11.238 seconds. This identifies
  repeated completion validation as measurable cost, not the complete cause
  of the transaction failure or a controlled speedup comparison.
- #11231 owns bounded diagnosis and source correction. Preserve fresh
  governance, completed-slot mutation/link/race refusals, one-writer custody,
  pending subscriptions and exact process-exit evidence within existing
  budgets. Do not retry the unchanged broad test, patch deployed copies or
  treat larger limits as a fix. Bridge #11153 and all parent adoption,
  delegation, provider and fleet requirements remain separate and open.

### 2026-11-07 - Refine current authority and request-local writer design

- #11450 actually merged as `fe70e2d4b898d3a835d8f452d9c51d72146738b4`
  after current-head Copilot approval `5815965694` with no open issues and
  latest required CI `42434797023` / `128591352150` passed. Managed completion
  reconciled the source and retained its backup.
- Trace the actual Fleet verifier, raw Azure relay source, standalone host,
  workspace executor and pinned native factory. The raw relay's action/host
  policy is not Fleet's mandatory material verifier, and local process
  withdrawal is not native admission. Document source-grant, native effective
  permission and current custodian checks as distinct prerequisites.
- Choose lazy, per-execution factory resolution at the existing workspace
  boundary. Reuse one actual material generation across action contexts and
  feedback; leave the legacy embedding separate. Storage-only recovery must
  not acquire credentials, and unknown actions retain read-only reconciliation
  without resend. Strict host configuration remains unchanged.
- This is review-first design, not an implemented verifier or host route.
  Documentation consistency and whitespace checks pass; implementation,
  contained lifetime controls, native source adoption and both parent effort
  acceptance gates remain open.

### 2026-11-07 - Qualify the finite request-local workspace factory source

- #11452 actually merged as `6813f6e026a876b73304e8be06d954c35a9a69f6`
  after current-head Copilot approval `5815992311` with no open issues and
  latest required CI `42435506760` / `128594078737` passed. Managed completion
  retained its source backup; #11128 stays open.
- Implement the reviewed optional in-process resolver at `WorkspaceOperations`.
  It resolves lazily once per submission execution, retaining the selected
  factory across action and feedback contexts. Failed resolution, material
  rotation or writer withdrawal cannot reacquire or switch to legacy
  authentication. Existing per-context embeddings and read-only hosts remain.
- The coupled lifetime/workspace/selection/owned-edit/attention packet passes
  151 cases with verified cleanup. It includes actual pre-approval selection
  and original receipts without acquisition, multiple action/feedback
  contexts, concurrent request isolation, failed resolution, rotation,
  withdrawal and unknown-action reconciliation without resend.
  The first concurrency fixture incorrectly raced the cache's nonblocking
  custody lock; it was corrected to overlap at the actual resolver boundary,
  not by adding storage retries or weakening the lock.
- Fatal Python lint, module size, documentation and installation-contract
  checks pass. This finite source seam does not implement native source-grant
  verification, normal-host enrollment, installed/provider acceptance or
  representative native latency. Broader typing remains pending under #9807.
  Both parent Plan/Validation gates remain open.
- Re-read both complete parent Plans/Validation after merge and close only
  this finite lifetime leaf. Continue the actual native permission/source
  verifier under the existing credential/provider owners before strict
  normal-host enrollment; no host activation or deployment occurred.

### 2026-11-07 - Deliver finite pinned native requests without host enrollment

- #11428 actually merged as `9e2c2596b7825abbdf00c11915f775972262ed81`
  after current-head Copilot review `5815884949` reported no open issues and
  its requested performance-impact assessment was supplied. The latest
  required CI `42432992385`, including gate `128587410909`, passed;
  earlier green runs or author replies were not substituted. The normal
  managed Maintainer completion verified the actual merge and retained its
  source backup during squash reconciliation.
- The optional native factory retains one private material generation through
  identity, candidate, policy and selected requests. Review corrections compose
  the executor's existing exact-candidate/ownership and independent captured
  target checks on every fresher dispatch snapshot, restoring the original
  policy callback after each action and every refusal/early exit.
- Four shared-context regressions and two final-target regressions failed before
  correction. The coupled executor/feedback packet passes 134 cases; the
  selected TLS file passes 29. Earlier overlapping transport/outcome and
  source/runner packets pass 34 and 17 respectively; 11 preparation/cache
  cases pass after sharing the retained-build selector. All bounded jobs
  verified cleanup. Dependency-age inference and the 1,000-line runner cap
  were corrected without weakening either gate; the complete age audit
  reports 579 declarations and zero violations.
- The PR records blocking request/resolver work, evidence invalidation and
  reproducible contained lanes. Whole-suite timing is not per-action native
  latency or a cold/warm tail qualification. Broad typing remains pending
  under #9807. Default observation/read-only paths are unchanged.
- Re-read the complete Plan/Validation and close only these finite source
  leaves. Continue review-first actual native permission/current-owner policy
  and operation-scoped normal-host binding; supplied callbacks are not that
  enrollment. All-forge publication, acquisition/DB mapping, full attention,
  CLI/Dispatch, provider, installed/shadow and presentation gates remain open.
  This merge does not activate a host, enroll credentials, deploy or release
  broader source/resource custody.

### 2026-11-07 - Deliver separate protected credential scope without host activation

- #11412 actually merged as `fe117fd8609ffa22bc5e787be3fbdf39903c9a12`
  after corrected-head Copilot approval with no findings and latest CI
  `42426371350`, including required gate `128567515092`, passed.
  The original approval did not waive a missing standalone Fleet changefile
  or become approval of the corrected head. After a normal bounded wait,
  a requested current-head pass supplied the actual approval. Managed
  completion retained its backup and reconciled only the merged commits.
- Separate `ReviewScope`, explicit version-2 configuration, current controller
  support and exact sealed material checks extend the existing credential
  owners. Prior observe-only contracts are unchanged. The mandatory external
  verifier still owns actual native permissions; the SDK adds no verifier,
  native request transport, CLI adapter, host writer or live enrollment.
- The selected contained standalone packet passes 158 cases with cleanup
  verified. The plugin-only devcontainer runner could not address the
  standalone target and supplied no coverage. Ruff, docs, installation,
  changefile, pre-commit and six-module size checks pass. Fleet's broad
  project probe remains pending under #9807, not a strict pass.
- Re-read the canonical Plan/Validation and close only the finite delivery
  leaves. Continue the actual pinned native ADO transport and current-owner
  policy before explicit host binding. Both parents and #11128 remain open;
  no deployment, production effect or resource release follows from this merge.

### 2026-11-07 - Separate protected credential support before native host adoption

- #11410 actually merged as `15704f93b3d8aac8b9feb9be22775a1f2e7b173b`
  after exact-head Copilot approval with no findings and latest CI
  `42425153368` passed. Managed completion retained its backup and reconciled
  only the merged source commits.
- Re-read the affected canonical Plan and Validation. Contract review is the
  only completed exit here. Continue the separate typed credential delivery
  through the existing Gateway/controller/worker owners, then the native
  pinned transport and explicit host consumer. Observe-only contracts remain
  unchanged; a synthetic verifier is not actual native permission evidence.
- Both parents and #11128 stay open. No credential enrollment, host activation,
  production review, deployment or resource release follows from this merge.

### 2026-11-07 - Resolve actual producer source and profile before composition

- #11397 actually merged as `fa1d15da3d496760750c02fb5086ba2f0472a38d`
  after exact-head Copilot approval, the resolved canonical-root finding and
  latest CI `42423725267`, including required gate `128558581914`, passed.
  Managed completion retained its backup and reconciled only the two merged
  source commits.
- The optional in-process resolver uses the actual managed source record and
  effective attribution profile before returning a pure, scope-bound shared
  composer. Original-key selection recovery precedes re-resolution. Neither
  the service's own checkout nor caller-supplied source/policy labels become
  producer identity or write authority.
- Both duplicate-root and aliased-root regressions failed before correction.
  Canonical exact-root matching now refuses ambiguity and retains registered
  aliases. Final contained packets pass 62 coupled, 31 Worktrees and 120
  attribution/profile cases with four existing skips and verified cleanup.
  AI-attribution's eight strict targets pass Linux/win32 analysis; pending
  Worktrees/PR type admission is not a strict-project result.
- Re-read the complete current Plan/Validation and credit only this finite
  prerequisite. Continue the protected native credential/transport owner
  before normal host binding: current typed forge credentials are explicitly
  observe-only and cannot be relabeled as review authority. All-publisher,
  mapping, full provider/CLI/Dispatch/retention, installed and shadow/pane
  acceptance remains open. No credential, host, deployment or resource effect
  follows from this source merge.

### 2026-11-07 - Review actual producer dependencies without enabling a writer

- #11393 actually merged as `fd66cc49d339442664eea11bd1ffc6af17f26351`
  after exact-head Copilot approval with no open issues or threads and latest
  CI `42421735633`, including required gate `128551570834`, passed.
  Managed completion retained its backup and reconciled only the merged commit.
- The source trace distinguishes injected preparation/write factories from
  normal hosting: the standalone configuration supplies only an observe-bound
  reader, while protected native review consumes an existing private transport.
  The reviewed contract preserves that read-only default and requires actual
  producer source/profile and protected writer prerequisites before enrollment.
- Re-read the complete current parent. Credit only contract review; continue
  the finite actual source/profile resolver, then the protected native owner
  and explicit host binding. All-publisher, mapping, provider/installed,
  attention, CLI/Dispatch, retention and shadow acceptance remain open.
  No host, credential, service or resource was activated or released.

### 2026-11-07 - Qualify optional selected-edit consumers without host activation

- #11381 actually merged as `362d7b691d64434136a522eff1a948283374f156`
  after exact-head Copilot approval with no open issues and latest CI
  `42416679608`, including required PR gate `128537341926`, passed.
  Managed completion retained the source backup and reconciled only the
  actually merged commit.
- The default-off trusted-host opt-in adds one separate edit capability while
  preserving the exact fresh-selection declaration. Native/Fleet clients
  refuse missing/malformed support before dispatch; the actual receiver
  independently rechecks current opt-in and protected source. Retained
  original-key previews, explicit exact submission and native receipts
  remain separate from permission to write.
- The bounded seven-file packet passes 250 cases with cleanup verified.
  Seventeen new consumer cases cover actual native/Gateway CLI preparation,
  original receipts, genuine legacy and prepared edits, lost committed
  previews, unavailable cache, confirmed retries, old fresh capabilities and
  support withdrawal/restoration without rewriting retained state.
  Fatal lint, module, documentation and installation contracts pass.
  Pending plugin typing under #9807 is not a strict-project pass.
- Re-read the complete current parent and credit only the optional source
  consumer leaf. Next, resolve actual normal-host producer enrollment through
  its reviewed source/authority owners; do not activate the read-only host
  from a callback or capability label. Other publishers, acquisition/DB
  mapping, full attention/CLI/Dispatch/provider, installed/shadow and retained
  parent outcomes remain open. No deployment, live feedback, migration or
  resource release occurred.

### 2026-11-07 - Review compatible selected-edit consumers

- #11375 actually merged as `a34b256ad1479c705071a1829b34c0b0d6b67f6a`
  after exact-head Copilot approval with no open issues and latest CI
  `42415921281`, including its required PR gate, passed. Managed completion
  retained the source backup and reconciled only the merged proposal.
- The reviewed default-off receiver extension retains the exact fresh
  allocation capability and adds a separately selected edit declaration
  through the existing operation. Current receiver/source/receipt authority,
  original-key recovery and explicit approval/submission remain separate.
- Continue finite CLI/native/Fleet/Gateway source qualification. This proposal
  does not enroll normal hosts or close full publication, provider/installed,
  attention, CLI/Dispatch, shadow or parent acceptance. No rollout occurred.

### 2026-11-07 - Bind selected edits through original native custody

- #11374 actually merged as `a7aafaadeedf78c1c5f13cbd2b7bf29b54d6b858`
  after exact-head Copilot approval with no open issues and latest CI
  `42415394987`, including required PR gate `128533318728`, passed.
  Managed completion retained the source backup and reconciled the one
  actually merged commit.
- The optional library reuses the actual protected source validator,
  current native-author/baseline checks, same replay transaction and original
  receipt owner. It preserves one logical identity across intentional writes,
  permits only explicitly selected genuine legacy edits, and refuses copied/
  unbound metadata, conflicting histories, potentially matching unknown
  effects and duplicate selected native targets.
- The bounded four-file packet passes 150 cases with cleanup verified;
  20 focused edit cases include actual shared composition, owner files,
  protected synthetic native effects and original receipt projection.
  Fatal lint, module, documentation and installation contracts pass.
  Pending plugin typing under #9807 is not a strict-project pass.
- Re-read the canonical parent and credit only this finite library leaf.
  Continue the separately reviewed optional receiver extension, preserving
  baseline fresh allocation and missing/rolled-back support refusal.
  Both the parent and #11128 remain open; no normal host enrollment,
  all-publisher/provider/installed adoption, live effect or rollout is inferred.

### 2026-11-07 - Review selected owned-edit binding before consumer activation

- #11370 actually merged as `d6b69d7dfe` after exact-head Copilot approval
  with no open issues and latest CI `42414652814`, including its required PR
  gate, passed. Managed completion retained the source backup and reconciled
  only the merged contract commit.
- Re-read the complete current parent. The refinement keeps protected
  generation/source validation and original native receipts under their
  existing owners, with selected legacy edits distinguished from backfill.
  The next finite library slice does not change `owned_edits: false` in the
  existing receiver, activate a consumer or close the full comment-identity,
  provider/installed, attention, CLI/Dispatch or shadow acceptance.
- Both the parent and #11128 remain open. No rollout, live feedback, migration
  or source/resource custody change is authorized by the reviewed contract.

### 2026-11-07 - Commit receiver-owned previews before approval

- #11299 actually merged as `0b9cf2df611ba9d254fc3e3c6762b21e8a40b4d2`
  after exact-head Copilot approval, the addressed review thread and latest
  current-head CI `42364749821`, including its required PR gate, passed.
  Managed completion retained the source backup and reconciled the existing
  worktree; the parent remains Active.
- The optional actual receiver/native/Fleet/CLI path negotiates explicit
  selection adoption and trusted composition. Actual policy/profile/origin
  resolution precedes the existing replay transaction; pure composition and
  complete preview/submission frame validation precede retention. The first
  committed v2 preview is recovered through the same original selection
  without a second body, allocation, decoration, cache lookup or admission.
- Review corrected oversized retained previews, partial target-revision
  checks, missing/deleted reply targets and inconsistent original endpoint
  normalization. The receiver reuses the executor's pure static selection
  checks; actual protected submission remains a separate explicit operation.
  Lost post-dispatch results preserve possible selection-commit uncertainty,
  not a claim of native effects or permission for another key.
- The bounded protected-selection packet passes 146 cases; the final
  client/selection packet passes 77 with cleanup verified. The earlier
  Gateway/owner boundary packet passes 141. These overlapping source controls
  include actual CLI/Gateway/owner files and protected synthetic native
  outcomes, not live provider or installed proof. Fatal lint, module,
  documentation and install-contract gates pass. Pending plugin typing
  under #9807 is not a strict-project pass.
- Re-read the complete current Plan/Validation and credited only this finite
  leaf. Next, qualify actual scoped/native identity binding before automatic
  owned/legacy edit allocation, retaining normal host enrollment, all other
  publisher, acquisition/mapping, full attention/CLI/Dispatch/provider and
  installed/shadow gates. No rollout, live review effect, migration or
  resource release follows from this source merge.

### 2026-11-06 - Allocate fresh identities inside original-selection custody

- #11253 actually merged as `0bd34706744af9755aa75db2f6cca0d162f7ad77`
  after exact-head Copilot approval with no open issues and the latest
  current-head CI and required PR gate green. Managed source reconciliation
  retained its backup; the parent remains Active.
- #11251 actually merged as `911bfa164602526aecc7347016554a83cb59a8ca`
  after exact-head Copilot approval with no open issues and the latest
  current-head CI and required PR gate green. Managed source reconciliation
  preserved its backup and retained the active parent/source custody.
- The finite source leaf adds allocation for new comments/replies
  through that same original-selection transaction. The trusted host resolves
  policy/profile/source before entry and supplies only bounded synchronous
  pure composition under the lock. Original-key recovery avoids allocation
  or redecoration after lost results, restart, contention and admission.
- The coupled owner/selection/capture/executor source packet passes 187 tests
  with bounded cleanup verified. Actual shared preparation exercises distinct
  reply GUIDs, in-lock composition, first-commit races, lost-result recovery,
  changed input/profile/source, exact admission/native receipts, bodyless votes,
  unsupported edits and invalid preparation without partial persistence.
- This is not a public producer capability or complete pre-approval pipeline.
  Bodyless votes remain bodyless; automatic owned/legacy edits refuse pending
  their actual protected native identity binding. The explicit caller-ID path,
  exact approval/admission/native receipt boundary and old strict formats
  remain unchanged. Producer wiring, all-publisher adoption, mapping,
  provider/installed/shadow and full parent acceptance remain open.

### 2026-11-06 - Retain pre-approval data without execution admission

- The reviewed #11246 refinement now has a finite storage prerequisite:
  `ReviewSelectionStore` composes over the existing ReplayStore transaction,
  file and review capacity. Explicit lifecycle adoption fences old strict
  readers. A bounded original-input digest and stage accompany the single
  final v2 intent; no second body archive or allocation ledger is introduced.
- Exact original-key recovery retains the first committed prepared bytes
  across restart, lost results and changed profile/origin candidates. Existing
  nonblocking lock contention refuses; callers reconcile that original key
  after contention clears. Changed source/action/target/draft selection refuses.
  Preparation cannot inspect an execution operation, start an attempt, retain
  a native receipt/projection or bind feedback capture. Exact final admission
  changes only the stage before existing execution/receipt semantics apply.
- The bounded four-file owner/selection/capture/executor source packet passes
  180 tests with cleanup verified. Two test assumptions were corrected:
  alternate source metadata must use the actual shared preparer's matching
  body, and the existing operation lock refuses contention rather than queues
  both concurrent callers. Neither required changing the existing lock.
- This is data-only source support, not automatic GUID allocation, producer
  capability advertisement, actual approval evidence or installed behavior.
  Next, compose allocation and retained selection through the existing owner
  before preview, preserving exact approval/native receipt boundaries.
  Other publishers, mapping, provider/installed/shadow and full parent
  Plan/Validation acceptance remain open. No operational effects are granted.

### 2026-11-06 - Review durable selection before automatic identity allocation

- The finite prepared-source completion journal #11244 actually merged after
  exact-head Copilot approval with no open issues and the latest required CI
  green. Its Plan/Validation credit remains limited to the delivered optional
  source chain; the parent stays Active and the source worktree reconciled.
- The next bounded proposal extends the existing intent-binding refinement
  with durable pre-approval selection and allocation under the same storage
  owner. It separates retained preview data from execution admission and
  original native receipts, with lost-result/restart recovery by original
  operation identity, bounded retention and explicit old/new compatibility.
- Review this contract before changing strict storage or publisher behavior.
  It adds no runtime path, persisted format, GUID allocator, policy authority,
  production migration or operational permission. Other publishers, mapping,
  full attention/CLI/provider and installed/shadow acceptance remain open.

### 2026-11-06 - Compose the local producer before preview and explicit submission

- #11237 actually merged as `bb2f7a49c40e44aba3aa65941b5a47085cbec546`
  after a passing owner-authored Copilot `COMMENTED` verdict with no
  Medium/High findings and the latest current-head CI and required PR gate
  green. Its one Low finding, the missing PR-description documentation-impact
  assessment and links, was corrected and live-audited before merge.
  The existing source worktree reconciled through managed completion.
- An optional trusted host binding composes actual profile/source preparation
  through the existing local CLI/tool before preview/approval. It returns an
  exact v2 intent without admission or native effects, retains caller-selected
  identities and bodyless votes, and leaves submission/default v1 unchanged.
  No new Gateway route, storage layout, owner loader or GUID allocator exists.
- The final coupled source packet passes 133 cases with bounded cleanup
  verified. The managed publication rebase preserved every validated producer
  and workspace test owner unchanged; native/provider/installed adoption is
  not inferred from that source proof.
- Re-read the parent Plan/Validation and credited only this finite leaf.
  Next, resolve durable pre-approval identity allocation/retention through the
  existing selected-operation owner before enabling automatic allocation;
  preparation without a retained original cannot establish cross-call
  uniqueness. Other issue/PR/review/inline publishers, acquisition/mapping,
  full attention/CLI/provider and installed/shadow acceptance remain open.
  This source milestone authorizes no rollout or operational effects.

### 2026-11-06 - Negotiate prepared review submission through existing workspaces

- #11224 actually merged as `083c198ce7` after exact-head Copilot approval
  with no open issues and the latest current-head CI and required PR gate
  green. The existing source worktree reconciled through managed completion.
- The optional existing CLI/tool/client/Gateway path negotiates prepared v2
  submissions and scoped original receipts. Current receiver/storage/native
  gates remain authoritative; cached support cannot authorize execution.
  Prior/default bindings remain v1, with no silent fallback or storage adoption.
- The final workspace, source-checkpoint and native-custody packet passes
  125 cases with bounded cleanup verified. The corrected credential-denial
  fixture targets the actual typed producer and proves no credential read
  started; no SDK or runtime authority was changed.
- Re-read the full parent Plan and Validation Plan. Continue an optional local
  producer before final preview and approval, retaining caller-selected IDs
  and existing profile/source owners. Automatic allocation, all-publisher
  coverage, native/provider/installed adoption and full parent acceptance
  remain open. No deployment or operational effect was introduced.

### 2026-11-06 - Correlate explicitly selected native prepared effects

- #11214 actually merged as `7ef86b52f3a091e3bda3c8e308e64f6833e62f37`
  after exact-head Copilot approval with no open issues and the latest
  current-head CI and required checks green. The source worktree reconciled.
- The explicitly selected source executor retains v2 identity through the same
  native context, unknown fence and original receipts. The read-only scoped
  receipt projection preserves unknown/partial states and does not infer
  identity from legacy tags or bodyless votes, perform fresh native IO or
  advance feedback/echo/ACK/Dispatch.
- The actual executor/feedback/owner packet passes 176 cases and the existing
  workspace/service/echo packet passes 106, with bounded cleanup verified.
  A confirmed original receipt remains distinct from current readback:
  independent edits leave feedback unavailable rather than marking it handled.
- Re-read the parent Plan/Validation and continue optional actual receiver/
  client/tool/CLI composition through existing capability and Gateway owners.
  Default v1, no implicit adoption/fallback and all remaining publication,
  allocation, provider and installed gates remain; no rollout occurred.

### 2026-11-06 - Retain structured prepared identity before native source adoption

- #11207 actually merged as `4bb05db59c8f836d90bf494afab403d811c6d9b2`
  after exact-head Copilot approval with no open issues and the latest required
  CI green. An earlier managed merge correctly refused an expected check from
  the newer run; it was not replayed until that exact run completed successfully.
  The source worktree reconciled through its normal completion path.
- The explicit orthogonal storage extension retains structured original
  comment/write/source identity and body binding under the current owner.
  Old strict readers refuse the extension; existing layouts and legacy
  operations/unknown receipts retain their meanings. No sender or serving
  capability was enabled by the data-only source change.
- Review corrected literal-boundary parity with the shared preparer. The final
  storage/capture/conversation packet passes 102 cases; the legacy native/CLI
  packet passes 106, both with bounded cleanup verified. The standalone
  validator passes strict Linux/win32 checking; the overall plugin remains
  pending type admission, not a strict-project pass.
- Re-read the parent Plan/Validation and continue the explicitly selected
  existing native executor and original receipt projection. Preserve default
  v1, no implicit adoption/fallback, actual native actor/version checks and
  separate feedback/echo, receiver, provider and installed gates.

### 2026-11-06 - Review original prepared-intent binding before its data extension

- #11203 actually merged as `93d33164b35f5ce7f34928ecad959c3e1330c7f5`
  after exact-head Copilot approval with no open issues and the latest required
  CI green. The source worktree reconciled through its normal completion path.
- The refinement names the missing structured admission fact separately from
  opaque-body preservation. It retains actual preparation before approval,
  immutable original GUIDs/bytes/source, strict owner/receiver compatibility
  and original native receipts without upgrading tags into actor/version,
  handled or feedback-echo proof.
- Re-read the parent Plan/Validation and begin the finite existing-owner data
  admission leaf. Executing producer/receiver composition, native correlation,
  other surfaces/providers and installed qualification remain open; review
  of the contract does not implement or activate them.

### 2026-11-06 - Qualify explicit prepared-body consumption before admission changes

- #11197 actually merged as `106bebee5ce46eb290ffb4cc28c8821c5ab77564`
  after exact-head Copilot approval with no open issues and the latest required
  CI green. The source worktree reconciled through its normal completion path.
- All 42 affected workspace CLI/tool cases pass with bounded cleanup verified.
  The three new cases compose the actual source/profile preparers with native
  create, reply and owned-edit serialization, real-file retained intent and
  confirmed retry after owner restart without another send.
- Review corrected the invalid sibling-plugin development dependency and
  parent/reply identity reuse. The actual canonical dependency graph and finite
  release materializer now pass with isolated source imports and a YAML-parser
  development dependency only. No runtime or request schema changed.
- Re-read the parent Plan/Validation and publication inventory. Explicit opaque
  body consumption is qualified; automatic preparation, structured original
  identity admission/correlation, other surfaces/providers and installed
  adoption remain open. Review the narrow existing-owner binding refinement
  before changing strict persisted or receiver shapes. No deployment occurred.

### 2026-11-06 - Land compact comment preparation and continue actual consumers

- #11146 actually merged as `1f92edb9aba7a9f1499dbc36efa7c3981e6abba0`
  after current-head Copilot approval and the latest required checks passed.
  The source worktree reconciled through its normal completion path.
- The optional AI-profile/body helper composes a public-safe source snapshot,
  supplied comment/write GUIDs and one bounded hidden marker before approval.
  It preserves retry bytes and refuses foreign/ambiguous/filler metadata;
  it does not allocate identities, persist intent, publish or prove native effects.
- Its final focused source packet passes 119 cases with four existing profile
  skips and verified cleanup. Strict typing passes all seven targets under
  Linux and win32 settings. The actual origin/body CLI composition case also
  passes separately; native Windows and installed/provider adoption are not
  claimed.
- Re-read the parent Plan and Validation Plan; the effort and #11128 remain
  open. Continue actual publication-owner inventory and contained opaque-body
  consumer qualification, retaining allocation, correlation and separately
  authorized adoption as unfinished work.

### 2026-11-07 - Preserve the partial authorized Windows update

- The operator separately authorized a normal unified update on one Windows
  target followed by isolated installed PR-owner validation, preserving
  updater serialization, configuration, credentials, sessions and subscriptions.
  Other machines, fleet deployment, UAC, forced takeover, delegation activation
  and retained-resource cleanup remain outside that grant.
- Durable worker launch was refused by the caller's Windows Job. The supported
  supervised mode then reused the same pending request and completed through
  the canonical serialized executor, without weakening containment. The full
  update returned exit 6; this is partial installation, not successful rollout.
- PR-owner `0.3.227-dev1` is selected. Its actual installed version, root help
  and watch help commands all pass. Those entry checks do not certify complete
  installed-cell or deployed-delegation acceptance.
- Bridge selected `0.9.348-dev1` but its successor failed startup on the missing
  acquisition Worktrees writer trigger; the healthy `0.9.304.dev1` incumbent
  remains undrained. Existing #11144 / contributor PR #11153 owns that repair.
  Supplied actual upgrade evidence and the precise contract-registry CI
  refusal to the author rather than opening a competing patch.
- MCP rolled back because four attached sessions were busy; unconfirmed
  retirement remains retained. Do not force those sessions or retire their
  runtimes. Continue after the existing Bridge fix clears review, CI and
  release, then reconcile the ordinary update and remaining installed proof.
  Parent requirements and all retained-resource holds remain open.

### 2026-11-07 - Confirm normal release of the qualified PR-owner repair

- Normal promotion `42314791785` completed successfully, including candidate
  freezing, eligible source coverage, first-party construction and attestation,
  real Windows Resume startup, exact source-unit reuse, publication readback
  and non-administrative candidate merge.
- Published Main `2f10aac90cc7c1ee8e6a644eb3d626678a6adc8f` contains all four
  PR-owner implementation files from #10953 byte-for-byte. The compact Efforts
  template from #11167 is unchanged apart from its normal release-version
  stamp and remains 4,049 bytes. #11160 closed on the earlier actual hosted
  candidate-freezing proof; full promotion now supplies distinct release proof.
- The parent digest still has eighty-three open Plan/Validation items.
  Release does not establish installed adoption, configured credentials,
  deployed delegation, image/provider/fleet operation or retained-resource
  settlement. No local runtime, profile, policy or service was changed.
- Continue with the remaining source and installed-adoption obligations under
  their existing owners and effect bounds. The completed disposable hosted
  qualification grant does not authorize another run or a local rollout.

### 2026-11-06 - Land the bounded projection repair and retain release proof

- #11167 merged as `3f29614f05a93fdd979a9e71b2735aab297aa768` after
  exact-head Copilot approval with no findings and required CI `42312927520`
  passed. The source worktree reconciled its merged PR and pulled forward
  through the normal managed path.
- Current parent digest and touched-section readback confirm all eighty-three
  open Plan/Validation items remain; no parent requirement changed or closed.
  The native/Dispatch journal outcomes now landed with this meaningful source
  contribution, rather than a journal-only PR.
- Keep #11160 open until normal hosted candidate composition confirms its
  blocker cleared. Post-merge CI `42313550089` is in progress; release and
  any separately authorized installed proof remain pending. No retry, runtime
  rollout, resource release or custody override follows from this merge.

### 2026-11-06 - Repair the actual release-candidate projection blocker

- The repair in #10953 is still pending release. Promotion run `42309816584`
  failed its candidate-freezing job on #11160: the Efforts completion template
  was 4,167 bytes, above the unchanged 4,096-byte declaration limit. Overlap
  warnings were not the blocker.
- Compact wording retains the same effort completion, post-merge digest,
  continued-driving, authority, hold and agenda rules at 4,049 bytes. The
  existing shipped-template and enabled-stack regressions reproduced two
  failures before the edit; all twenty-one tests pass afterward.
- The actual promotion projection-sync helper also passes on a fresh isolated
  candidate containing the real Efforts payload. It writes the selector and
  reviewed fallback, and a second invocation makes no changes. Its owned
  fixture was removed after successful validation. This is consumer-contract
  proof, not a full hosted promotion, runtime rollout or operational acceptance.
- Continue the bounded #11160 contribution through ordinary review, required
  CI and an actual merge, then observe normal release execution. Preserve all
  parent and retained-resource gates; do not force the pipeline or reuse a
  completed qualification grant for a new released-source run.

### 2026-11-06 - Qualify the original released Dispatch preparation path

- #9277's original fresh-release command,
  `python tools/run-plugin-tests.py --prepare-only agent-dispatch`, passed on
  Windows with CPython 3.12.10 and UV 0.12.0 at released Main
  `9007b0ef321d2be24088cd42279564005d39289e`. A separate fresh test environment
  also passed with UV 0.13.7. Both real runner invocations returned exit zero
  and confirmed preparation, preserving source constraints and normal
  dependency admission. No shared resolver or runtime was installed or updated.
- The issue is closed on this direct Dispatch evidence after #10861, not on
  the sibling PR-owner/Fleet preparation or #10953's native acceptance.
  Preparation alone runs no Dispatch pytest suite and does not qualify
  credentials, deployed delegation, image/provider/fleet operation or the
  parent effort. All retained resource and custody holds remain unchanged.
- Continue with the remaining installed-adoption and retained-resource
  obligations at their existing authority boundaries. Do not repeat the
  completed preparation or native qualification to substitute for those gates.

### 2026-11-06 - Complete the bounded native watch-owner repair

- #10953 merged as `ea954b226a096c4bea0ebbd1b1ebcdd64c049c81` after
  exact-head Copilot approval, resolution of both timeout-diagnostic findings
  and required CI, including the metadata-triggered run `42305029563`.
  This resolves #10927's four diagnosed failure classes, not the parent.
- Final-head hosted run `42303184240` at
  `5cd79cbbd7bee1d15be4e47d09caf96cfc8c83d1` passed all fifteen selected
  jobs with actual test and cleanup results read for each. Windows/Linux
  normal upgrade, shipped cells, peer directions, lease-only refusal,
  controller-death recovery and concurrent upgrades passed; macOS's
  unsupported governed-identity refusal passed. Linux's seven existing
  platform-specific legacy skips do not skip native upgrade or a selected job.
- Both earlier progress-loop candidates also passed their fifteen selected
  jobs. Each of the three exact temporary tags was removed after a fresh
  matching-SHA check and absence readback. The final timeout correction
  preserves Python 3.10-compatible evidence and the original exception;
  source controls are not a claim of native Python 3.10 execution.
- Re-read the parent Plan, Validation Plan and direct retained outcomes after
  merge. They remain open, including #9277's distinct released-tree Dispatch
  preparation reproduction, configured credentials, running-consumer,
  image/provider/fleet and retained-resource duties. Release and authorized
  installed rollout remain separate from this candidate's disposable hosted
  qualification. No shared runtime, profile, policy or service was changed.
- Continue by reconciling #9277's exact release-only preparation path against
  current source and existing fixes; do not infer Dispatch success from the
  passing PR-owner/Fleet cohort or repeat the completed qualification.

### 2026-11-06 - Land the compact comment-origin producer before its consumer

- #11137 actually merged as `a914f7815b7915c66b7cd8f0f660cc60ee932fe5`
  after exact-head Copilot approval with no open issues and the latest required
  CI green. The source worktree reconciled through its normal completion path.
- The optional Worktrees producer returns a distinct compact comment-origin
  contract from its actual managed record, defaults to public-safe source
  data and preserves raw/anonymous/codename policy. It does not compose or
  post a comment, mint GUIDs or become a PR-ownership refresh.
- The exact published two-file source packet passes 37 cases with bounded
  cleanup verified. Fatal lint, module/docs/install contracts pass; the
  changed module's typecheck project remains pending, not strict evidence.
- Re-read the current canonical Plan and Validation Plan; the parent and
  #11128 remain open. Continue configured body/GUID preparation, then actual
  publisher inventory, durable correlation and separately authorized adopter
  qualification. No deployment, live feedback or custody transfer occurred.

### 2026-11-06 - Review compact forge-comment provenance before implementation

- #11129 actually merged as `5687588a648f5ac54bcd7acf6e5857fdebe4ada4`
  after exact-head Copilot approval with no open issues and all required CI
  green. The source worktree reconciled through its normal completion path.
- The shared vision and #11128 contract preserve configured AI attribution,
  compact source provenance, stable comment/per-write identity and the
  existing native proof/publication owners. Ordinary comment origin cannot
  become a PR-ownership refresh, and metadata does not get a filler comment.
- Re-read the current canonical Plan and Validation Plan; the parent remains
  Active. Begin the finite source-owner origin producer, then shared body
  preparation, actual publisher inventory/integration and correlation. A pure
  producer/helper does not close #11128 or all-forge/adopter acceptance.
  No runtime deployment, live-comment rewrite or custody transfer occurred.

### 2026-11-06 - Complete the finite historical-readback source leaves

- #10979 actually merged as `efd2f76812f8e84d2056f9b4d1bfda89c7fa7105`
  after exact-head Copilot approval with no open issues and the latest required
  CI green. The source worktree reconciled through its normal completion path.
- Together with reviewed plan #10970, storage #10972 and protected proof #10977,
  the optional attention-read/v2 consumer qualifies per-operation original
  readback under explicitly adopted v6 storage. Current handled/echo progress
  remains independent; v1/v5 defaults and ordinary host behavior are unchanged.
- The review's epoch-filter issue was fixed before merge: confirmed actions
  without original readback remain unavailable across reopen, while certified
  history permits genuine new-epoch work and independent consumers. The final
  bounded coupled packet passes 232 cases with cleanup verified.
- Fatal lint, module/docs/install contracts and the admitted strict service
  Linux/win32 targets pass. These are isolated synthetic/source controls, not
  native Windows, live provider, installed hosting or shadow acceptance.
- Re-read the current full Plan and Validation Plan and mark only B4's finite
  source exit complete. The parent stays Active. Full B3/A2 still requires
  unsupported reply/edit/vote version attribution and longer echo-chain
  qualification; broader retention, provider, CLI/Dispatch, installed and
  shadow/presentation outcomes remain open under their existing owners.
  No deployment, live feedback, migration or resource release occurred.

### 2026-11-06 - Retain actual protected original-readback proof

- #10977 actually merged as `add3727398a5425701b48e106596e6a6ecf32925`
  after exact-head Copilot approval, no open findings and all required CI green.
  The source worktree reconciled through its normal completion path.
- The existing protected owner now commits each v6 capture's own body-free
  original-readback proof after confirmed native outcomes and fresh pinned
  readback. Older and same-frontier operations cannot borrow another proof,
  regress current handled provenance or reset age. Original B3 echo coverage
  remains distinct, and v5 behavior is preserved.
- The coupled packet passes 218 cases; the subsequently added same-frontier
  regression passes separately. Both bounded executions verified cleanup.
  Fatal lint, module/docs/install contracts and the admitted strict service
  Linux/win32 targets pass; native, installed and live-provider proof is not
  inferred.
- Re-read the full current Plan and Validation Plan; the parent remains Active.
  Continue the separately negotiated v6 reader. Full B3/A2 and broader
  provider/retention/CLI/Dispatch/installed/shadow acceptance remain open.
  No deployment, live feedback or resource reassignment occurred.

### 2026-11-06 - Land historical readback storage without activating consumers

- #10972 actually merged as `8971538874535035c9dafd5a1616760415772ede`
  after exact-head Copilot approval, no open findings and all required CI green.
  The source worktree reconciled through the normal completion path.
- Explicit v5-to-v6 adoption retains original captures with null readback and
  separately reserved bounded proof capacity. Complete-envelope writers and
  repeated earlier adoptions preserve the expanded layout; prior disk/wire
  contracts remain unchanged without implicit migration or downgrade.
- The final bounded coupled packet passes 207 cases with cleanup verified;
  fatal lint, touched-module, documentation and install-contract gates pass.
  The admitted strict service consumer passes Linux/win32 targets, not native
  Windows execution or installed/provider qualification.
- Re-read the full current Plan and Validation Plan; the parent remains Active.
  Continue actual protected proof commits, then negotiated v6 reads. The
  storage prerequisite does not close B4, B3/A2, broader service acceptance or
  production shadow/pane qualification. No deployment or live effects occurred.

### 2026-11-06 - Review historical original-position readback before implementation

- #10970 actually merged as `5f27d56d2d8c532cbbffc79691b8f082cc23cc7c`
  after exact-head Copilot approval, no open findings and all required CI green.
  The source worktree reconciled through its normal completion path.
- B4 preserves immutable captures and native receipts under the existing owner,
  separates historical protected readback from the current handled frontier
  and B3 echo, and requires explicit storage expansion before protected and
  negotiated consumer integration. No runtime behavior was claimed by the plan.
- Re-read the current full Plan and Validation Plan; the parent remains Active.
  Continue the first bounded storage leaf, then protected proof and the actual
  reader. Complete B3/A2, provider/retention/CLI/Dispatch, installed serving and
  shadow acceptance remain open; no deployment or production effects occurred.

### 2026-11-06 - Land the optional retained attention consumer source

- #10906 actually merged as `98e8f311db0efdf9a19a2e9fc27e317ad6c03329`
  after current-head Copilot approval with no open review issues and the latest
  required CI passed. The source worktree is reconciled; no running host was
  migrated, enrolled, updated or deployed.
- The optional v5 reader uses the current acquisition, selector/policy,
  protected-feedback, replay-envelope and authenticated Gateway owners.
  Actual strict CLI/client/handler/store fixtures preserve scoped interests,
  meaningful age, original native outcomes and snapshot-fenced paging without
  read-side handled writes, watch ACK, Dispatch emission or a new ledger.
- All five findings were addressed: capture-specific readback provenance,
  distinct refused/unavailable/invalid-result CLI exits, unknown/partial native
  feedback across reopen and explicit in-flight read supersession during
  capability refresh. The final coupled packet passes 295 cases with bounded
  cleanup verified. Fatal lint, module/docs/install contracts and admitted
  strict service Linux/Windows targets pass; native Windows, installed and
  live-provider acceptance are not inferred.
- The current layout retains only current handled provenance. Historical
  per-operation readback, unsupported reply/edit/vote echo versions and longer
  chains remain explicitly unavailable under the existing captured-feedback
  acceptance. Complete B3/A2, broad A3 serving, provider/retention/CLI/Dispatch
  and installed/shadow outcomes stay open.
- Post-merge pull-forward initially replayed the squash and aborted on a
  conflict, despite canonical aggregate landing proof. All changed source/test
  content matched the actual merge; the journal contained only an additional
  concurrent upstream entry. Retained backup and verified zero-unpublished
  range allowed lossless reconciliation. The recurrence is recorded on #4130's
  existing PRC-5404-R4 / PRC-4699-R2 scope, not silently dismissed or assigned a
  new owner.
- Re-read the complete current Plan and Validation Plan. Keep the parent Active
  and preserve every independent source/resource, provider and rollout gate.

### 2026-11-06 - Retain pinned hosted failures and improve peer diagnostics

- A subsequent, explicitly authorized progress-based repair/retest loop passed
  all fifteen selected watch-owner jobs in run `42296155145` at
  `fbdd0a9c0b0b71dd9549001e903388c54a465610`, including real fresh-account
  Linux/Windows upgrades, shipped-cell and peer isolation, lease-only refusal,
  controller recovery, concurrent upgrades and macOS refusal. Every selected
  runner completed its owned cleanup; the exact temporary tag was removed
  after a matching-SHA check and absence readback. This is hosted qualification,
  not shared-host rollout, the separate Dispatch reproduction or parent completion.
- Fresh review then found the new timeout wrapper changed the exception type
  before startup/upgrade diagnostic handlers could catch it. The helper now
  emits captured streams to stderr and rethrows the original timeout; both cutover callers combine
  captured streams with journal, lifecycle, native-birth and original-exit
  evidence. This does not depend on Python 3.11 exception notes: the plugin
  retains Python 3.10 support. Eleven focused controls pass, including both
  actual diagnostic caller phases with exception notes unavailable.
  Current-head CI, re-review and hosted qualification of this
  test-helper correction remain pending under the same bounded repair loop.
- The separately authorized `watch_owner` run `42279632485` exercised
  #10953's exact candidate `10be300359f85323f012c94fd1124ed50ac70a06`.
  All fifteen disposable jobs reached pytest: five passed and ten failed.
  macOS's structured refusal, both controller-death cases and both concurrent
  upgrades passed. This is not complete installed acceptance.
- Eight failures stopped in parser construction because the optional attention
  client imported unavailable `fleet_manager`. Dev already contains #10961's
  deferred-import repair; the source branch incorporates it through the normal
  managed sync, without duplicating that patch or changing the approved run.
  The two peer-roundtrip failures reached the reverse CLI call but returned no
  JSON; their stderr was not exposed, so the same cause is not yet established.
- Peer assertions now expose stderr before decoding an empty JSON response.
  Payload-command timeouts also retain captured stdout/stderr without changing
  subprocess, containment or deadline policy. A contained Windows check stopped
  earlier at a snapshot-directory stability refusal. A diagnostic attempt passed
  that boundary but hit the normal installer's unchanged 300-second timeout;
  neither attempt reached peer launch or qualifies the shipped-account path.
  Temporary instrumentation was removed; do not blindly repeat that setup.
- Rebased source validation passed thirteen refusal, concurrency, diagnostic
  and native controller-admission controls with one macOS-only skip; four
  timeout-output, command-shape and original-deadline controls also passed.
  Fatal lint on all ten affected Python files, the thirteen-plugin installation
  contract and whitespace checks passed. These remain source controls, not
  the missing shipped-account or reverse-peer result.
- The hosted result and provider logs are retained. The temporary tag was
  deleted only after a fresh exact-SHA check, with absence confirmed afterward.
  That one-run approval is consumed. Keep #10953 draft while source validation
  and current-head review/CI proceed; a new hosted pin/run needs a new explicit
  approval. #10927, #9277 and all installed, operational, retained-resource and
  parent completion obligations remain open. No runtime was rolled out.

### 2026-11-06 - Repair bounded native acceptance prerequisites

- #10927's Linux/Windows baseline failure came from the concurrency fixture's
  snapshot hook dropping the production poller's `fetch_revision` keyword.
  Forwarding the keyword preserves revision fencing and the original held-work,
  notification, writer-transfer and timeout assertions. The complete isolated
  race now passes.
- Shared ZDD now exposes Darwin birth tokens, but the governed owner still
  supports Windows/Linux decimal tokens. Unsupported formats now refuse at
  controller/instance capture, before journal or selection publication, rather
  than persisting evidence its own schema rejects. The native macOS test retains
  this governed refusal; it no longer assumes shared Darwin exit APIs are absent.
- Isolated source controls passed: three process tests with one macOS-only skip,
  plus five read-only failure-evidence controls. Touched Python lint and the
  thirteen-plugin installation contract passed. No native OS-account, installed
  or fleet test was run locally.
- The Windows `serve` controller was also admitted as a resident, unlike the
  passing `restart` path. Its disposable Windows admission creates an inherited
  kill-on-close Job. Admission now belongs only to the actual passive and legacy
  foreground owners. Five focused routing/native controls pass: the corrected
  child survives caller exit, while restoring old controller admission kills
  that exact child. Test containment, leases and native checks are unchanged.
- Actual shipped-account acceptance remains open. An earlier narrowed
  disposable direct-start experiment failed before publishing child custody,
  not at the hosted boundary, and is not shipped or counted as acceptance.
  The shipped-cell failure now records bounded read-only native-birth comparison
  and original-exit observations separately, retaining unknown/refused evidence
  without control, retry or cleanup bypass.
- Fresh hosted qualification needs a new exact-source approval; both earlier
  one-run grants are consumed. Keep #10927, #9277 and every original installed,
  operational, resource and parent completion obligation open.

### 2026-11-06 - Confirm the exact candidate checker budget

- Normal promotion `42174892203` produced release #10919 at Main
  `b17ff8924cd88e2249441f99614ae78e8a5b26d1`; its checker source exactly
  matches #10909. That producing run's admitted candidate typecheck passed
  in 127 seconds under the unchanged six-minute limit, and the exact-candidate
  merge succeeded. #10895 is closed on that evidence, not on a run's creation
  timestamp, source-only timing or a manual retry.
- This finite source/release result does not qualify every future workload,
  authorize rollout or close the installed failures in #10927, the original
  Dispatch reproduction in #9277, or the parent effort.

### 2026-11-06 - Retain the corrected hosted installed-contract result

- One separately authorized `watch_owner` run, `42178491630`, exercised released
  `4a3d3e28799c17f7fe16b2af3dfbf34571060e88`. All 15 selected disposable jobs
  reached pytest: 11 passed and four failed. The earlier preparation blocker
  is cleared for this PR-owner/Fleet cohort, not the original Dispatch
  reproduction in #9277 or the complete installed contract.
- Linux normal upgrade, shipped cells, peer roundtrip, both lease-only refusal
  cases and controller-death recovery passed. Windows normal upgrade, peer
  roundtrip, both lease-only refusal cases and controller-death recovery passed.
- #10927 retains the failed Linux/Windows initial-baseline concurrency waits,
  unhandled macOS controller-identity refusal, and Windows first-cell health
  native-identity failure. The logs do not establish their root causes.
  Keep the existing identity checks, timeout bounds and cleanup custody;
  passing setup or eleven cells is not complete installed acceptance.
- The result and provider logs are retained. The exact temporary qualification
  tag was deleted only after a fresh matching-SHA check; readback confirmed
  absence. The one-run approval is consumed, with no automatic repeat or
  local/fleet/shared-service effects. Continue the bounded source repair,
  retaining all parent Plan, Validation, Coordination and resource holds.

### 2026-11-06 - Reduce checker repetition and retain installed acceptance

- #10909 merged at `20618b0826b4d321e3e7d46ce073772c488e1802`,
  after current-head Copilot approval with no findings and green required CI
  `42173423874`. Ordinary MyPy dependency caches are shared without dropping
  projects, matrix cells, source verification or cache-disabled scopes.
  The source CI admitted typecheck passed in 116 seconds; exact-candidate
  timing remains open under #10895.
- All 40 admitted projects passed their declared matrices across two bounded
  Windows-host runs, alongside 25 focused controls and actual-MyPy cache
  invalidation coverage. This is not a single-run six-minute Windows pass or
  an installed-contract result.
- Normal promotion `42167730047` completed successfully. Released Main
  `4a3d3e28799c17f7fe16b2af3dfbf34571060e88` contains the exact #10861
  test-preparation helper. The earlier one-run hosted approval was consumed;
  a corrected pinned qualification repeat still needs separate authorization.
- The complete current Plan, Validation and Coordination were read back.
  No operational, credential, service/fleet or resource row closes from this
  tooling optimization. Keep #6007, #9277 and the retained installed exit open;
  no runtime rollout or owner transfer follows.

### 2026-11-06 - Land the released-checkout test-preparation repair

- #10861 merged at `0c269795980dfd898dd0f1a04142ae4e63f7b767`, after
  current-head Copilot approval, resolved feedback and green latest CI
  `42162316695`. The source/resolver packet passed 59 focused contracts;
  its real-metadata and materialized-input limits remain recorded below.
- The merged parent's complete Plan, Validation and Coordination were read
  back. No installation, credential, service/fleet or resource row becomes
  complete from this tooling repair. The #6286 retained installed exit and
  broader #6007/#7188 outcomes remain open.
- Source tracking observed the merge. Pull-forward initially auto-aborted
  while replaying the squash; read-only native landing proof and a verified
  recovery branch permitted a zero-unpublished-commit reconciliation without
  losing work. The larger source worktree is retained.
- #9277 remains open for corrected released-account preparation proof.
  Release promotion and a separately authorized qualification repeat are the
  next stretch. No hosted rerun, local update, shared-service activation,
  fleet rollout or unrelated resource disposition follows from the merge.

### 2026-11-06 - Keep metadata qualification valid on released checkouts

- The real-metadata fixture now explicitly retains the required top-level
  canonical library closure as well as each consumer's embedded closure.
  Canonical materialization may legitimately do nothing when its input is
  already released; that no-op is accepted without dropping canonical data.
- Both PR/Fleet and Dispatch/PR/Fleet are exercised from checkout-form and
  already-materialized inputs. The corrected focused packet passes 59
  contained Windows contracts with clean sandbox removal, unchanged bounds,
  fatal lint and diff checks. Production composition code is unchanged by
  this fixture correction; native installed acceptance remains open.

### 2026-11-06 - Refuse conditional source replacement

- Dependency requirement predicates and source-mapping selectors have
  different effects. Canonical composition retains requirement predicates,
  but refuses `marker`, `extra` or `group` selectors in the original and
  selected canonical source graphs before emitting overrides. This prevents
  a conditional local/registry choice becoming an unconditional local URL.
- Six filesystem-only controls cover the three selectors in both graphs and
  verify refusal before resolver-input creation. The real metadata and
  requirement-marker cases remain passing. The focused contained Windows
  packet now passes 57 contracts with unchanged bounds, clean sandbox
  removal, fatal lint, scoped module-size checks and diff checks.
- This remains a source-only prerequisite. The failed hosted run is not
  replaced with these fixtures; released-account, installed and parent
  service/fleet acceptance remain separately open.

### 2026-11-06 - Preserve real metadata activation in test-source constraints

- Shared library metadata already contains platform-marked dependencies and
  optional extras. Canonical source composition now follows the runner's
  selected entry extras and their local dependencies, retaining target marker
  predicates and version bounds without imposing inactive optional bounds.
  Extra names and comparisons use normalized identities; UV still evaluates
  marker truth for its selected target.
- The regression uses the canonical materializer with the actual PR/Fleet and
  Dispatch/PR/Fleet project and library metadata. Separate actual offline UV
  cases preserve false platform predicates and reject incompatible active
  extra or core bounds. No root-interpreter dependency was added.
- Fifty-one focused contained Windows contracts pass with the original
  30-second item and 300-second group bounds, clean sandbox removal, fatal
  Python lint, scoped module-size checks and diff checks. This corrects the
  source-preparation prerequisite; the failed fresh-account run remains
  failure evidence, not installed acceptance or rollout.

### 2026-11-06 - Retain the released-account setup failure

- The single native acceptance run
  [42151348602](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42151348602)
  used release `32c045f081ba8d9c4b5759dfb3c9bfa21ec68d55` and selected
  only the existing 15 PR-owner jobs, with their original deadlines.
  Every selected job failed during environment preparation, before pytest
  acceptance. No installed-contract or deployment success is credited.
- Linux and macOS report conflicting `agent-procutil` URLs from the PR and
  Fleet payloads' separate vendored libraries. Windows reports the failed
  UV preparation command without subordinate resolver stderr. This extends
  the existing released-checkout defect #9277; its source repair is now the
  next prerequisite, not another installation-cell implementation.
- The temporary qualification tag was removed after preserving the terminal
  result and checking its exact commit. No workflow rerun, local update,
  shared-service control or fleet rollout occurred. The retained #6286
  installed exit and all broader parent/resource gates remain open.
- The bounded #9277 source repair keeps canonical library selection in the
  runner's existing cache helper and preserves version constraints separately
  from UV source overrides. Both selected canonical bytes and their mapping
  invalidate stale cached environments; unsupported identity or requirement
  forms refuse rather than guessing.
- Fifty focused Windows tests pass, including actual offline UV resolution
  for the two-consumer PR/Fleet and three-consumer Dispatch/PR/Fleet layouts,
  plus incompatible-version refusal. Fatal Python lint, the unchanged module
  cap and diff checks pass. These are source/resolver proofs, not successful
  fresh-account installation, release promotion or installed adoption.

### 2026-11-05 - Land the qualified Manager source-acceptance leaf

- #10671 merged at `6a6bbb0c306ca93d65e388ba97a2ff81533d9018`, after
  current-head Copilot approval, resolved documentation feedback and green
  required CI. The finite source-scheduling/fixture issue #10632 is closed.
  The earlier four-construction/native evidence and its limits remain below.
- The source workspace was pulled forward without losing work; its complete
  published head remains on a verified recovery branch. No source PR remains
  open for this leaf.
- The actual parent installation-cell, governed cutover, Phase 4 and
  Validation rows were read after merge and remain open. The next stretch
  returns to #6286's retained isolated installed-contract acceptance;
  source qualification alone does not close it or the broader #6007/#7188
  outcomes. Live updates, credentials, service activation and deployment
  still require their separately applicable authorization.
- Existing source/resource owners and cleanup holds are unchanged. The
  delegation worktree and parent effort are retained, not declared complete.

### 2026-11-05 - Qualify all four real Manager source constructions

- The source contract passed at `4e73aecca7fe744129e5322f489686222b6f05e8`
  under its approved shared 1,800-second ceiling. Release, qualified-source,
  source-generation and repair commands all completed; the final pytest
  assertion passed without skips. The actual native sandbox was removed,
  with no unsettled-process diagnostic.
- This preserves real canonical source acquisition/preparation, immutable
  repair and healthy reuse, retained predecessor bytes, dirty-checkout
  refusal, global command bytes and default-root configuration/SQLite
  bytes, file identity and schema continuity across both interpreters.
- Twenty-five clock/cleanup controls and fatal Python lint passed. The
  existing source cleanup expectation now accounts for all four construction
  commands plus the final assertion. Other native contracts and production
  deadlines remain unchanged.
- Draft #10671 can now proceed through publication, current-head review and
  required CI. This is local Windows native/fresh-profile qualification,
  not Linux source-construction, live-account, serving, image or deployed
  acceptance. The existing hosted native-launch lane invokes a different
  runtime contract and is not used as a proxy for this source lane.
- The complete parent Plan/Validation and existing owners remain active;
  no rollout, resource-disposal hold or broader completion gate is cleared.

### 2026-11-05 - Retain the source timeout and approve its finite test ceiling

- After #10772, the real release, qualified-source and source constructions
  completed successfully. Repair then exhausted the remaining 211 seconds
  of the shared 1,200-second ceiling; final assertions did not run.
  This credits the caller-relative correction, not full source acceptance.
- The same four separately contained constructions and final assertions now
  share an explicitly approved 1,800-second test-only ceiling. Every command
  receives only the remaining allowance. Production deadlines, test-item
  limits, source/data witnesses, cleanup and all other contracts are unchanged.
- The stages use one evolving installation and retained checkpoint state;
  they are not parallelized or replaced with cached success. Draft #10671
  and the complete parent Plan/Validation remain open pending real validation.
  No further budget increase, live update or resource disposal is authorized.

### 2026-11-05 - Remove the caller-relative bootstrap prerequisite

- The finite caller-directory correction merged in #10772 at
  `f23139f2a9ccfb1821948ac882a5898f1199c5e6`, after current-head Copilot
  approval and required CI. Its source workspace finalized through native
  landing checks. It does not replace #10559's whole-bootstrap transaction
  or retained worker/source-lifetime scope.
- Thirty-four selected Manager checks exercised real request/child source
  resolution, unchanged configuration bytes, bounded receipts, automatic
  holds and legacy Manager-to-engine custody. Construction alone was
  substituted in the small regression; full source construction is not
  credited by that result.
- Draft #10671 now consumes the merged correction on current Dev. Its four
  separately contained source constructions and final assertions still need
  qualification under the shared 1,200-second ceiling, unchanged production
  deadlines, test-item limits and original witnesses.
- The complete current Plan and Validation Plan were read after the merge.
  Remaining installed, provider, service/client, image and fleet acceptance
  stays open; neither this prerequisite nor source-workspace finalization
  completes the effort or grants rollout.

### 2026-11-05 - Land conservative original-version comment echo coverage

- #10743 actually merged as `eb6d7492c79664eb71b50b8a53618d28d2bd818f`
  after exact-head Copilot approval with all four review findings resolved
  and both current-head CI runs green, including their required PR gates.
  Managed completion reconciled the existing source worktree.
- The final coupled packet passes 317 cases; the unchanged native
  response/exporter packet passes 42. A final 41-case feedback rerun verifies
  the deterministic deadline fixture. Source lint, documentation/install
  contracts, module limits and admitted strict service Linux/Windows targets
  pass. These are source/synthetic results, not native Windows, live provider
  or installed proof.
- Re-read the complete parent Plan and Validation Plan. Preserve the Active
  parent and remaining B3/A2 acceptance: unsupported response versions and
  longer event chains stay unavailable rather than becoming queue progress.
  The next consumer boundary must retain this distinction when qualifying
  optional attention serving; A3, full provider/CLI/Dispatch/retention and
  installed/shadow acceptance remain open. No operational authority changed.

### 2026-11-05 - Qualify the first conservative comment-echo leaf

- The optional native outcome now supplies original confirmed response data
  ephemerally. The feedback-enabled caller stores only normalized version
  fingerprints in its existing body-free receipt identities. Old providers
  and recovered lost responses cannot manufacture missing original versions;
  old callers retain their existing behavior.
- The same workspace cache/acquisition-position binding supplies current
  evidence. Exact distinct comment threads may cover only one contiguous
  semantic position for their original captured interests after complete
  protected owner readback and transactional journal/attention rechecks.
  Other interests still see the event; independent input and pending age
  remain intact. Immutable captures, native receipts and newer handled
  progress are preserved.
- Synthetic controls exercise original-response versions, current/prior
  peer combinations, all-confirmed retry without resends, protected full
  metadata/policy readback, mixed input, unavailable acquisition, gaps,
  intervening round trips, restart, reopen and transaction races.
  Real cache and replay-lock contention likewise preserve the original result
  and reconcile optional coverage on retry without resending.
  Original handled persistence precedes optional echo work, which uses only
  half the remaining request budget. A workspace-deadline regression proves
  optional readback timeout preserves the structured result and no-resend retry.
  The executor is capped by the original enclosing absolute deadline, including
  delayed entry after setup; its regression reaches the near-deadline boundary.
  The coupled PR packet passes 317 cases and the native response/exporter
  packet passes 42, both with bounded cleanup verified. Fatal Ruff,
  documentation consistency, the install contract and admitted strict service
  Linux/Windows targets pass; these are not native Windows or live-provider
  results.
- Reply/edit version sequences, exact immutable vote attribution, longer
  event chains and complete B3/A2 acceptance remain open. Negotiated A3
  serving, broader provider/consumer/retention and installed/shadow acceptance
  are not supplied by this leaf. No default host, strict wire, provisioner,
  live provider, retained service or rollout changes.

### 2026-11-05 - Land protected original-position reconciliation

- #10677 actually merged as `fc68de920ebce4ad874c0969d931d895879d5d5c`
  after exact-head Copilot approval with both findings resolved and all
  required CI green, including the final PR gate. The managed source
  worktree is reconciled; both parent plans remain Active.
- The final 288-case bounded packet qualifies protected synthetic capture,
  actual acquisition-to-position binding, original receipt recovery and
  no-resend readback/handled progress. It does not qualify live providers,
  installed serving, native Windows or echo coverage.
- Re-read the canonical Plan/Validation and continue the first B3 evidence
  leaf: original protected response versions and exact interest-specific
  comment echo classification, preserving other interests and mixed or
  unproven intermediate input. Missing vote attribution remains unavailable.
  B3's full exit, A2, A3 and all broader acceptance remain unresolved; no
  runtime installation, provider effect or production adoption is authorized.

### 2026-11-05 - Realize protected original-position feedback

- The optional trusted submit embedding now captures positively admitted
  interests and exact complete/fresh generation/journal meaning before the
  first native attempt. Missing capture after an attempt cannot be backfilled;
  capacity and newer unavailable evidence refuse before sending.
- Capture additionally requires the exact position returned by that
  generation's successful acquisition, not a cursor inferred from equal
  current contents. The existing workspace bounds this metadata by its cache's
  generation limit; missing metadata requires reacquisition for new effects,
  while a durable original capture still reconciles after restart. Old
  pre-reopen and same-epoch round-trip generations cannot borrow a new cursor.
- The same protected owner guards the actual target tip and performs fresh
  readback on all-confirmed retries without another send. Exact confirmed
  comment/reply/edit objects can record only their captured position and
  interests; later independent input remains pending, age gaps stay
  conservative, newer handled proof is not regressed and reopen epochs fence
  old progress. Native action disposition and tracking are separate.
  Target motion or missing capture does not block read-only original receipt
  attribution, and cannot authorize remaining writes.
- Protected synthetic cases cover restart, unknown-response reconciliation,
  original attempt/receipt custody, target-only motion, concurrent input,
  tracking-write failure before/after replacement, unavailable acquisition,
  retained gaps, interest binding and request-side override rejection.
  Current votes or equal VoteUpdate counts cannot identify an immutable vote
  event and remain explicitly tracking-unavailable. Exact vote attribution,
  B3 echo coverage, A3 serving and all broader acceptance remain open.
- The final bounded feedback/executor/capture/acquisition/workspace/
  conversation/review-owner/replay packet passes 288 cases, with container
  cleanup verified. Fatal Ruff, documentation consistency and the 13-plugin
  install contract pass. This is protected synthetic source qualification,
  not native Windows, live provider, installed or serving acceptance.
- No default host, serving descriptor, strict wire, provisioner or lifecycle
  adoption changes. No real provider write, retained service or rollout is
  authorized by this source contribution.

### 2026-11-05 - Land capture storage; continue protected integration

- #10665 actually merged as `ca22dc3698229c5af9c5ec2709e87f456fc599a9`
  after exact-head Copilot approval with no findings and all required CI green,
  including the PR gate. The managed source worktree is reconciled; the
  174-case bounded packet remains source qualification, not installed adoption.
- Re-read the canonical Plan/Validation and preserve the Active parent.
  Continue B2's trusted pre-effect capture, actual-target protected checks,
  fresh readback on confirmed retries and captured-position handled progress
  through the current owners. B3 echo coverage and A3 serving remain later
  exits. No live effect, migration or deployment is authorized.

### 2026-11-05 - Review capture contract; realize its finite data-owner expansion

- #10664 actually merged as `1fcb7efce751cecbea9955e498ee690af6c196d0`
  after exact-head Copilot approval with no findings and required green CI.
  The existing source worktree is reconciled. The canonical Plan/Validation
  remains Active; the next finite leaf is B1, not full A2 feedback.
- B1 implements explicit v4-to-v5 adoption, bounded body-free capture values
  and complete-envelope validation under ReplayStore. Retained capture
  references must match their original immutable review intents; these checks
  are data consistency, not selection, freshness, actual-target or effect proof.
  Default provisioning and serving contracts remain unchanged.
- The focused capture/attention/acquisition/conversation/review-owner/replay
  packet passes 174 cases with bounded cleanup verified. It covers retained
  confirmed and unknown review state, replay writes, restart, non-downgrading
  prior adoption, uncertain replacement, lifecycle withdrawal, capacity and
  corrupt/foreign capture refusal. Existing strict service closure passes
  Linux and Windows targets; install contract and documentation checks pass.
- No capture admission, protected sender integration, handled-progress writer
  or interest-specific echo coverage is implemented by this data leaf.
  Continue B2/B3 and A3 through their actual owners; no live migration,
  provider write, retained service or rollout is authorized.

### 2026-11-05 - Land atomic acquisition; refine the remaining feedback contract

- #10648 actually merged as `01c0e53da149d27bec299cfc991af3433314642a`
  after exact-head Copilot Comment with no open findings and successful
  required CI, including the PR gate. The managed source worktree is
  reconciled to that merge; this is source delivery, not installed adoption.
- Review corrections preserve pending age after unknown selection, mark
  newer missing-evidence acquisitions unavailable, fence stale/overlapping
  input with the retained acquisition watermark and reconcile identical
  terminal retries after a lost commit response. The final focused
  acquisition/workspace/attention-store packet passes 67 cases with bounded
  cleanup verified. The earlier 133-case coupled and 64-case standalone
  packets remain separately scoped evidence, not additive case counts.
- Re-read the canonical Plan/Validation and workspace companion after merge.
  A2 still needs original pre-effect capture and actual protected feedback;
  A3 serving, coordinated retention, provider/CLI/Dispatch and installed
  acceptance remain open. No production backend or default host changed.
- The [captured-feedback proposal](captured-attention-feedback.md) separates
  existing-owner v5 data expansion from protected execution and
  interest-specific echo qualification. It preserves strict existing
  protocols and original receipts, requires readback on all-confirmed retries
  and refuses capture over newer unavailable attention. Review the refinement
  before implementing it; no provider write or rollout is authorized.

### 2026-11-04 - Compose the finite attention acquisition half

- The existing workspace acquisition path now accepts trusted, generation-bound
  current interest/policy inputs and atomically records attention with its
  actual journal observation after confirmed cache publication. Default
  embeddings remain unchanged; neither serving nor reading adopts v4.
- Preserve meaningful age on semantic nonevents and restart, retain the first
  unhandled event's age, and begin a new age only after a handled position or a
  fresh epoch. Omitted interests, missing draft and incomplete path/policy
  evidence remain unavailable rather than becoming silent exclusions.
- The final acquisition/workspace/native-read/protected-review packet passes
  133 cases; the earlier 46-case atomic acquisition/adoption/journal packet
  overlaps it. The 64 standalone service cases pass with bounded cleanup
  verified. Existing strict service analysis passes Python 3.10 Linux and
  Windows targets, not new plugin-wide strict admission or native Windows
  execution. Install-contract, fatal lint and documentation checks pass.
- The first review identified an age reset when unknown or omitted selection
  recovers. Only a confirmed `False -> True` admission now starts a new
  selection age; outage recovery preserves it. The corrected 52-case
  acquisition/adoption/journal packet passes, including restart controls.
- This is only A2's acquisition half. A1's provenance references cannot recover
  an original pre-effect captured position once concurrent journal input has
  replaced it. Protected captured-feedback and interest-specific self-echo
  integration must resolve that durable capture boundary before claiming A2;
  no global echo suppression or newest-cursor baseline is acceptable. A3
  negotiated serving and the full parent acceptance remain open.

### 2026-11-04 - Land A1 and continue actual attention integration

- #10641 merged as `04659cf43c535038b5bdccfeb5b473ac55d13c6f`
  after exact-head Copilot approval with no findings and successful actual
  CI. Managed completion reconciled the existing source worktree.
- The affected source packet passed 83 adoption/journal/projection, 123 coupled
  replay/review/workspace and 64 standalone cases. The final 47-case
  adoption/journal rerun passed after clarifying v4 serving refusal; it is a
  repeated subset, not additional unique coverage. All bounded cleanup passed.
- Re-read the canonical gate and admitted contract after merge. Continue A2
  actual acquisition and protected-feedback integration, then A3 negotiated
  reading. Self-echo handling must be interest-specific: a confirmed comment
  remains real new conversation input for other participants. Do not suppress
  the global journal event to hide one participant's own echo.
- No serving volume, credential, policy or production process changed. A1 is
  not a running queue or completion of the full parent effort.

### 2026-11-04 - Expand the existing attention data owner after contract review

- #10633 merged as `ededd50e1ac49abac458a2ba3f0f5d4c1a494dd0`.
  The owner-authored Comment verdict contained one Low documentation-impact
  finding, addressed in the PR description and resolved before merge. No
  Medium/High finding remained; refreshed actual CI passed. No dummy source
  commit or extra approval round was needed for the description-only repair.
- Re-read the canonical gate and admitted attention contract. A1 now expands
  the existing v3 complete-envelope owner explicitly to v4, preserving replay,
  journal and confirmed/unknown review operations under its existing lock and
  atomic transaction. Bounded attention data references do not grant feedback
  authority; this slice adds no ingest or handled-write operation.
- Default provisioning and v2/v3 consumers remain unchanged. Internal
  conversation transactions preserve v4, while its existing v3 serving
  descriptor refuses the expanded layout instead of misreporting support.
  The source data foundation is not a serving-volume migration or running queue.
- Focused adoption/journal/projection controls and coupled replay/review/
  workspace consumers pass with verified cleanup, as does the standalone
  default/composed-host packet. A2 actual acquisition/confirmed-feedback
  integration, A3 negotiated service/CLI reading and all parent retention,
  provider, shared-intake, Dispatch and operational gates remain open.

### 2026-11-04 - Continue from composed-host qualification to durable attention

- #10631 merged as `d60196aa408aff2c1e8dc821636dcb912296b5a2`
  after exact-head Copilot approval with no findings and successful required
  CI. Its 64-case standalone packet and strict service analysis retain their
  stated source-only bounds. Managed completion reconciled the same worktree.
- Re-read the parent Plan/Validation and workspace companion after merge.
  The next bounded proposal expands the existing storage owner for durable
  interest age and confirmed handled progress, then composes actual acquisition,
  protected feedback and an optional authenticated read-only consumer.
- The [attention contract](durable-attention-slice.md) requires review before
  a strict v4 adoption or runtime change. Journal hashes, watch ACK, current
  votes and configuration labels cannot manufacture selection, feedback or
  effect authority. Concurrent input and unclassified self-echoes cannot be
  swallowed by a handled update.
- Global body-bearing retention, shared selection/IO, driver qualification,
  full CLI/Dispatch, installed and shadow/pane acceptance remain open. No
  production state, credential, service or action policy changed.

### 2026-11-05 - Preserve bounded source qualification and its runtime dependency

- The test-only source acceptance correction is tracked in #10632. Its four
  real constructions and final assertions now use separate containment with
  one approved 1,200-second aggregate deadline; every command receives only
  the remaining time. Production deadlines, assertion item limits and all
  other native contracts remain unchanged.
- Source fixtures now use the canonical source materializer before identity
  stamping. The legacy bootstrap copy retained a nested editable declaration,
  which the noneditable constructor correctly projected and then refused as
  a different assembly. Canonical materialization preserves the complete
  assembly through retention without relaxing identity validation.
- Sixteen focused scheduling, exhaustion, failure, retention and unchanged
  contract-budget controls pass. Fatal Python lint and the installation
  contract guard pass. Real upstream Git acquisition, source preparation and
  retained identity also pass without installing a runtime.
- The full native run passed release and source-qualified construction, then
  refused acquisition of the caller-relative configured repository after the
  explicit bootstrap changed its worker directory. Source acquisition,
  repair and final continuity assertions are not claimed qualified.
- The existing bootstrap/source-anchor owner in #10559 received the exact
  non-update bootstrap reproduction. Preserve that runtime ownership rather
  than opening a competing repair. The source-test patch remains draft until
  the dependency and full native qualification clear; the parent Plan and
  installed-adoption exit remain open. No live update or resource disposition
  is authorized by this source work.

### 2026-11-04 - Qualify the composed native workspace foreground path

- #10609 merged as `9e1c3e20622e41669c1baa1ede9028182129575c`
  after fresh exact-head Copilot approval with no findings and successful
  required CI. The base movement conflicted only in a generated producer
  digest; canonical regeneration preserved the contract revision, and the
  first four source commits were content-identical after rebase.
- Re-read the parent Plan/Validation and workspace companion after merge.
  The remaining composed-host qualification now extends the existing actual
  foreground-process fixture rather than supplying a different service handler.
  Explicit configuration/provisioning creates the real native workspace owner;
  CLI parsing and its supplied client cross real Gateway TLS/PoP to that host,
  which launches the finite synthetic exporter process.
- The fixture covers full metadata materialization, same-head dialogue, an
  unchanged semantic poll, capability discovery once per selected reader,
  read-only event cursors, connection reattachment and retained-volume restart.
  Restarted reads preserve the generation/cursor and frozen replay event without
  another native export. Existing default watch-only operation remains covered.
- All 64 standalone cases pass with bounded cleanup verified. The admitted
  Python 3.10 strict service analysis passes Linux and Windows; this is not
  Windows native execution. Synthetic export output and a forwarded credential
  reference do not prove actual provider/credential exchange, executable client
  binding discovery, installed rollout, a container image or live shadow.
- Continue with the reviewed Author/Reviewer attention/handled and coordinated
  retention gaps through the existing owner. Shared selections/IO, three-driver
  acceptance, complete HTTP CLI parity, Dispatch and operational qualification
  remain open. No live service, backend, credential or effect authority changed.

### 2026-11-04 - Realize the opt-in native read-only host slice

- The native-host contract merged in #10602 before implementation. Its finite
  source adapter retains the exact exporter's capabilities and native profile,
  actual canonical scope/principal checks, partial sections, shared deadline,
  complete cleanup custody, existing generation CAS and journal transaction.
- Standalone configuration binds only existing admitted caller/target routes
  and their typed observation credential owner. Default watch-only hosting is
  unchanged. Fresh explicit provisioning selects the existing v3/cache layout;
  serving validates retained state under the writer and never migrates it.
- The affected source packet passed 21 native-adapter/exporter, 143
  workspace/journal/replay/lifecycle and 63 standalone cases, with bounded
  cleanup verified. Strict service typing passes Linux and Windows analysis.
  Actual process boundaries and the default installed TLS/PoP fixture retain
  synthetic provider inputs; they do not qualify a live native/provider host.
- Review of #10609 identified nested credential-scope and POSIX cleanup-retry
  gaps. The repair stays at the canonical owners: exporter capability-negotiated
  enclosing-scope credential execution, and complete retained process/stream
  retries with the original caller's settlement callback. Actual credential
  timeout controls pin all descendants and require exit before the native
  operation returns; no writer release is inferred from a killed exporter.
- After that repair, 166 native/workspace/journal/lifecycle, 118 canonical
  process-owner (five platform skips), 76 exporter/credential and 63 standalone
  cases pass. Bounded cleanup is verified; strict Python 3.10 service analysis
  passes Linux and Windows. This is not a Windows native execution or deployed
  provider qualification.
- The next review found an interruption window after a retry's confirmed reap.
  Retry now reuses the retained `Popen.returncode` rather than signalling an
  already-reaped PID. Its actual-process regression passes; the canonical
  packet is now 119 passed/five platform skips. Required unit CI separately
  refused the two changed, previously admitted source digests. Refresh only
  those source entries; the exact unit nodes, effect restrictions and execution
  budget remain unchanged.
- The repaired head received exact-head Copilot approval with no open issues;
  its eligible-unit CI job also passed. The remaining required gate exposed
  stale generated caller-lifetime archives. Regenerate the canonical source
  archive and its installation-context/installer copies with the existing
  synchronizers, retaining all consumer changefiles and fresh-head review/CI.
  No installer policy, activation or production authority changes.
- These controls close a source prerequisite, not the full P1/P2/P3 gate.
  Shared selection/IO, durable attention/handled semantics, coordinated
  retention, three-provider qualification, installed adoption, container and
  separately authorized shadow acceptance remain open. No production service,
  credential, backend mutation or Dispatch authority changes.

### 2026-11-04 - Recover publication and continue existing-host integration

- #10599 merged as `24ce8a9ba2b3fc0d12b206592e63adc65e54b391`
  after exact-head Copilot approval with no findings and required green CI.
  The rebased candidate passed 123 native-candidate/ADO/CLI cases and 17
  decoder/actual-subprocess cases, with cleanup verified.
- The original managed creation attempt succeeded without local Git,
  credential or ruleset changes. The selected account had push permission and
  no effective branch rule prohibited its PR head. GitHub subsequently reported
  a resolved Git Operations incident consistent with the earlier remote internal
  errors; it does not establish their request-specific internal root cause.
- Re-read the parent Plan/Validation and workspace companion after merge.
  Native metadata alone does not close workspace/principal acquisition or the
  standalone host. The next reviewed proposal composes the existing finite
  exporter with the existing service/cache/journal and authenticated route;
  it does not fabricate a protected-owner callback from configuration.
- #10408 and #10410 remain open; their broader failing packets are not claimed
  green. P1/P2, providers, installed adoption, retention, parity and live shadow
  remain open. No production service or authority changed.

### 2026-11-04 - Continue native candidate evidence after normalized acquisition

- #10405 merged as `9cbe45b8f3e83e95022994c811f756170a7409f9` after
  current-head Copilot approval with no findings and required green checks.
  Managed completion reconciled the source worktree.
- Re-read the complete parent Plan/Validation and workspace companion. The
  standalone observer still lacks journal intake; the existing native snapshot
  exports only a base branch, not the exact target-tip candidate required by
  the journal. Continue that bounded metadata prerequisite without another read.
- The new candidate/native ADO/CLI packet passed 123 isolated cases. The
  broader provider packet exposed 14 unrelated publication-fixture failures
  tracked in #10408; the native-watch packet exposed three synthetic
  lost-child/cleanup assertion failures tracked in #10410. Those packets are
  not reported as passing; no publication or cleanup contract was weakened.
- Installed/provider/shadow and whole P1/P2 acceptance remain open. No live
  backend, credential or service authority moved.

### 2026-11-04 - Retain failed Manager source acceptance

- The acceptance-sandbox retention correction merged in #10590 at
  `8b26d2c729d023b97c1f3b0c8329178b8c7b4015`, after exact-head approval,
  green required checks and no open review threads. Eight focused wrapper
  controls passed. This changes test ownership and cleanup, not Manager
  construction, installation policy or the existing acceptance budgets.
- The documented Manager source contract at that source did not reach its
  assertions within the unchanged 300-second construction group. Two-build
  diagnostics also exhausted that group after a successful first constructor;
  they do not replace the four-build acceptance matrix or establish a pass.
  Their contained sandboxes settled and were removed normally.
- Fixture-only tracing reached the actual installed preparation worker. In
  one instrumented run, the first constructor completed in 166.943 seconds;
  its payload copy took 6.932 seconds and its inventory walk took 52.628
  seconds. Samples show artifact reads and ancestor/path checks, not a proven
  deadlock. Profiling overhead and separate source/build work preclude treating
  these measurements as an uninstrumented benchmark or a complete root cause.
- Preserve #10126's preparation ownership and inventory/path checks, #7028's
  source-generation and packaging ownership, and #7305's separate installed
  ordinary-update/headed-CLI acceptance. No deadline was enlarged, build
  omitted, live runtime changed or delegation activated. The original #6286
  installed-adoption exit and the parent Plan/Validation remain open.

### 2026-11-04 - Reconcile retained planning records

- #10572 merged after exact-head automated approval, green required checks
  and no open review threads. The complete historical #7193 review entry is
  preserved in its linked journal document; the later accepted #7208
  disposition remains authoritative.
- The five retained adoption/bootstrap journal entries from October 19-20
  already appear verbatim in the canonical effort. Their release and partial
  update outcomes do not replace the remaining installed-contract acceptance.
- Continue the finite original #6286 acceptance through its direct retained
  outcome rows, independently of the broader #7188 service work. The parent
  remains Active; existing implementation and operational owners are unchanged.
  No new diagnostic, installation, rollout or delegation activation follows
  from this documentation merge.

### 2026-11-04 - Qualify normalized workspace-to-journal acquisition

- The opt-in ADO workspace owner now projects confirmed load/sync acquisition
  into the existing journal transaction, without another fetcher, watcher,
  database or caller-supplied ingest route. Disabled embeddings are unchanged.
- The focused isolated acquisition/journal/workspace packet passed 79 cases.
  It covers body-free projection, unchanged polls, same-head dialogue, target
  movement, fractional interval overlap, partial relationship/statistic
  preservation, restart, terminal/reopen, cache conflicts and journal failure
  after confirmed cache publication. Cleanup was verified.
- Capture intervals widen outward to integer seconds; an overlap remains
  explicitly stale, not fabricated ordering. ADO current votes do not prove a
  candidate-bound verdict; iterations do not prove review-round or commit totals.
- This is source qualification, not an installed host, autonomous acquisition,
  three-driver proof or live shadow. Whole P1/P2 and the parent acceptance remain
  open. Next: compose journal evidence with read-only attention comparison while
  keeping missing eligibility/handled state explicit.

### 2026-11-04 - Merge the journal/event source leaf

- #10401 merged as `978fc45be52e1da899dace26544280a9397cae23`
  after current-head Copilot approval and green required CI. The final fix
  refuses null/scalar persisted journal state rather than treating corruption
  as first provisioning and resetting cursor lineage.
- The initial affected packet passed 136 container cases; the final focused
  journal/CLI/HTTP/replay-store packet passed 87 after cumulative-statistics
  and persistent-state corrections. Actual TLS/channel reads use synthetic
  identities and an explicitly supplied test host, with zero forge writes.
- Re-read the parent Plan/Validation. Whole P1/P2 and installed/provider/shadow
  acceptance remain open. Next: connect existing normalized acquisition to
  the journal while preserving source intervals, CAS, partial/unknown evidence
  and explicit adoption. No live service or review authority changed.

### 2026-11-04 - Qualify the opt-in conversation cursor source

- The bounded metadata journal and optional workspace event reader passed
  136 container cases across journal/storage, actual CLI/SDK/TLS/Gateway,
  current workspace consumers and legacy replay transactions.
- Same-head input, duplicate/stale observations, cursor gaps, restart,
  uncertain replacement and policy withdrawal are covered with synthetic
  identities. Journal terminal metadata preserves unknown effects and caches;
  it does not claim coordinated global compaction or live shadow evidence.
- The source requires explicit layout/serving adoption and normal review.
  New-provider normalization, terminal notification/retention, installed
  consumers, actual provider qualification and the full parent gate remain open.

### 2026-11-04 - Land the inert attention source leaf

- #10390 merged as `24e0744f8b1f756b96df70f16ed33bbbeea4df99`
  after current-head Copilot approval and successful required CI. The review
  correction checks feedback consistency before terminal/draft projections.
- The final focused container packet passed 36 attention cases; the earlier
  combined packet passed 39 attention/watch-contract cases. Neither is
  service, provider, installed or shadow acceptance.
- Re-read the parent Plan/Validation. P1/P2 remain open. Next: qualify an
  actual shared-owner journal/cursor and authenticated consumer path, preserving
  replay, review receipts, explicit layout adoption and legacy CLI semantics.
- No production backend, service, credential or review authority changed.

### 2026-11-03 - Land Author/Reviewer intent and qualify the existing baseline

- #10380 merged as `fd6ddccd35e6448f2b6de8fd6ff6ca442283a795` after
  current-head Copilot approval with no findings and required green checks.
  Re-read the canonical Plan/Validation and preserved unfinished parent scope.
- An isolated, network-disconnected test-container baseline passed 79 cases
  with five explicit platform skips across the existing native CLI, workspace
  integration and Gateway transport packets. The bounded supervisor confirmed
  cleanup. This qualifies those existing synthetic/source paths, not a new
  service perspective, three live providers, image delivery or live shadow.
- Added the initial [CLI parity inventory](cli-parity-inventory.md), explicitly
  distinguishing existing four-verb native composition, default GitHub paths,
  unsupported flags and still-open full HTTP/consumer migration.
- Next admitted runtime slice remains the inert normalized attention
  foundation. No installed backend, service, credential or reviewer changed.

### 2026-11-03 - Propose Author/Reviewer service perspectives

- Extend the existing service and caller-driven subscription campaign rather
  than introducing another daemon, forge implementation or execution queue.
- Capture a bounded live-conversation model, explicit terminal tombstones,
  role-aware attention/fairness, CLI/HTTP parity and three-driver acceptance.
  Gitea's public PR/review/comment models are conceptual references, not a
  copied database or newly selected runtime dependency.
- Preserve every prior Plan/Validation row and existing operational hold.
  Reviewed intent precedes finite source realization; no live reviewer,
  backend enrollment, credential, service or deployment changed.

### 2026-11-04 - Merge finite ADO author source and retain adoption

- #10402 merged after current-head Copilot approval, resolved findings and
  required green CI. A0-A3 source acceptance and generic frontend guidance are
  recorded in the [bounded amendment](author-publication.md#implementation-journal).
- The explicit source-only completion disposition transfers the remaining
  A4-A5 live/installed, consumer migration and remote-only evidence duties to
  assigned #10412. Azure CLI/Entra remains supported without a PAT prerequisite.
- No runtime rollout or consumer-helper retirement occurred. #6007 stays
  Active; #7051 conformance and #7188 service ownership are unchanged.

### 2026-11-03 - Propose bounded author-publication convergence

- Extended the existing checkout-free/client-convergence lane rather than
  opening another PR campaign or watcher. The public-safe request and
  agent-recommended plan are in [author-publication.md](author-publication.md).
- Credited existing foreign already-pushed creation, label repair, native
  observation, replies, requeues and durable subscriptions. The remaining
  proposal covers delegated publication-check qualification, metadata-only
  repair, adopted-workspace source publication and consistent consumer routing.
- Reviewed intent is the only requested delivery in this amendment. Existing
  runtime reservations, publication/claim authority, #7051 conformance and
  #7188 service ownership remain unchanged; implementation and rollout are
  not authorized or claimed complete.

### 2026-10-31 - Accept the caller-driven registration proposal

- #9033 merged as `9a6adc0dc3ad6704830729ecdec246feb507ea47` from
  head `0ac5d27621695193b2d402947319559c55517593`. Current-head Copilot
  review `5725904092` approved without open findings, required CI
  `41261657361` passed, and the documentation-impact thread was resolved.
- The rebase retained the newer per-origin keeper and incremental review
  contracts alongside the caller-driven intake proposal. The original
  source replay preserved its author and message; a separate revision-date
  commit records the integrated intent without changing that provenance.
- Re-read the current Plan and Validation boundary after the verified merge
  and pull-forward. Close only proposal review. Backend connections and
  exact-set intake are the next implementation slice under #7188; selections,
  author/reviewer integration, live provider, image, installed/fleet and
  independent-evaluator acceptance remain open. No runtime, credential,
  service or deployment was activated, and #6007 remains Active.

### 2026-10-28 - Direct retained keeper, adoption and fleet outcomes

The complete #6007/#6286/#7188 originals, refinements and inherited acceptance
are retained through the named Plan/Validation/Coordination ledger. The
bounded read-only study checked forty-one actual current texts and complete
nested eligibility under stable actor/roster guards, with current receiver,
four directly needed contract sub-documents and selected intent read at one
immutable source pin. No product code, test, credential or runtime was operated.

All fifty-four existing checked source/documented-qualification baselines
remain credited without duplicate implementation. #6286's remaining installed
prerequisite is not expanded into all #7188 fleet work. Original domain bounds,
single-operator authority, actual source/resource holders and the specific
latest registration-intent receipt gap remain. The older source-owner
signature rendezvous is not reinstated.

Normal review, actual merge and full current receiving readback precede any
separately supported administrative source consolidation. This does not mark
native/installed/provider/image/fleet outcomes complete, release a claim,
appoint an owner or authorize deployment. #6007 and this effort remain open.

### 2026-10-25 - Clarify caller-driven central registration

- Captured the operator's service-usage clarification verbatim and re-read it
  against the source. The intended consumers are PR-creating agents and
  reviewer task emitters requesting PR sets/ranges from the central service,
  not merely coordinators choosing a native/fleet access mode.
- Source tracing found fixed startup scope/observation mappings in the current
  host and Gateway, confirmed-PR publication and claim boundaries in the
  existing authoring path, and repository/filter-based reviewer discovery.
  Native provider inventory and continuous-watch foundations are reusable;
  their existence does not establish caller-driven connection or selection
  support.
- Propose reviewed vision and contract extensions before implementation.
  Preserve all delivered exact-PR/credential proofs, existing owners and
  remaining live provider, image, installed/fleet and evaluator acceptance.

### 2026-10-25 - Merge native ADO watcher credential recovery

- #8980 merged as `cf82e5f8e6e5f6d639655c0152857b4984908e10` from
  approved head `1268534a72bbbee9c54b4624b5c41a61768f5ef3`.
  Copilot review `5643208853` approved without findings; complete review and
  review/issue comments were read. Required CI `40193320812` passed.
- Exact-head native run `40193388396` passed all four selected jobs:
  Dispatch passed 237 cases on Windows and 236 on Linux with one explicit
  platform skip; PR service/client passed 296 plus 44 standalone cases on each
  platform. Totals were 577 Windows passes and 576 Linux passes with one skip.
  The new case constructs the actual native ADO configured snapshot and bearer
  request headers after real typed credential acquisition, through the same
  WatchService and scoped Dispatch queue. Only forge HTTP responses and the
  issuer/provider are synthetic. No legacy `az` or ambient-PAT fallback is used.
- The focused ADO case passed locally, followed by both provider cases
  together. Fatal lint, install-contract, documentation consistency, changed-
  module and whitespace gates passed. No production code, credential/native
  deadline, required unit portfolio or manual job budget changed.
- Re-read the parent Plan/Validation after merge. Managed completion retained
  its backup and reconciled only the already-merged commit after refreshing
  the recorded PR head. This closes only the ADO bearer source leaf; basic
  authentication, broader conformance, live provider results, normal backend
  activation, images and installed/fleet acceptance remain distinct and open.
  No next activation, rollout or runtime owner change follows from this proof.
### 2026-10-25 - Merge native credential-backed watcher integration

- #8975 merged as `5773f281ac4881bb98ca44dfe74e87ed55fa171f` from
  approved head `19eeb2cc1b9d8c83d6045d91c4f06f23b2f0bd0d`.
  Copilot review `5643127045` approved without findings; complete reviews and
  review/issue comments were read. Required CI `40191259030` passed.
- Exact-head native run `40191323874` passed all four selected jobs:
  Dispatch passed 236 cases on Windows and 235 on Linux with one explicit
  platform skip; PR service/client passed 296 plus 44 standalone cases on each
  platform. Totals were 576 Windows passes and 575 Linux passes with one skip.
  The joined control executes actual native GitHub snapshot construction and
  typed credential acquisition through the watcher and durable queue. Only
  issuer/provider and forge responses are synthetic; no live provider
  permission, image or deployment is certified.
- Local validation passed the joined case with 15 existing transport cases,
  all four refactored credential controls and a final focused run including
  captured-log and SQLite-sidecar secrecy checks. The integration fixture uses
  current time, explicit test-issuer renewal and completed native observation
  before consuming recovery; production/native-command deadlines are unchanged.
- Re-read the parent Plan/Validation after merge, refreshed the recorded merged
  head and used managed completion to retain its backup and reconcile only the
  already-merged commit. Continue the finite ADO native-reader counterpart
  above. Independent evaluator, live provider, image and installed/fleet
  acceptance and their existing owners remain open. No runtime was activated.

### 2026-10-25 - Merge scoped durable intent and coordinator composition

- #8957 merged as `2b89f0cbf3e50b5997a481287ac109d06bc920b6` from
  approved head `0b23f098058ff3c5ee8ef13010c66e5038023ee9`.
  Copilot review `5642864682` approved without findings; full reviews and
  review/issue comments were read. Required CI `40187435521` passed.
- Exact-head native run `40187527865` passed 235 selected cases on Windows
  and 234 on Linux with one explicit platform skip. Scoped durable intent,
  v1 compatibility, original-ID replay, real watcher nonevents/restart/lost-ACK
  recovery and the trusted programmatic front door passed. Default CLI startup
  still selects no remote backend.
- The initial hosted Windows run exhausted the 30-second item watchdog in
  the multi-phase watcher integration. Bounded local phase instrumentation
  found distributed observation/durable/restart/recovery cost, with complete
  v1/v2 calls taking 20.90/23.46 seconds. Only that Windows integration item
  now has a 120-second watchdog; internal and production deadlines and all
  functional assertions are unchanged. The final local transport packet passed
  15 cases before publication; temporary instrumentation was removed.
- Re-read the parent Plan/Validation after merge. Refreshing the recorded
  merged PR head let managed completion retain its backup and reconcile only
  the two already-merged commits. Continue the finite native credential-backed
  watcher/consumer integration above, not a new polling or credential owner.
  Independent evaluator, production providers, image and installed/fleet
  acceptance remain open; no runtime or deployment was activated.

### 2026-10-25 - Merge scoped replay provider/client support

- #8945 merged as `913a1a970ba982a3ebfdd0510ec4e8ae0beef478` from
  approved head `dfedfefc2f74aff628d31a41b0311b7179b7e1f0`.
  Copilot review `5642688345` approved without findings; all reviews and
  review/issue comments were read. Required checks passed.
- Exact-head native run `40183559810` passed 296 selected service/client cases
  and 44 standalone cases on each of Linux and Windows, 340 per platform.
  The real TLS/PoP path covers v1 compatibility, v2 expected-binding refusal,
  post-discovery remapping, provider rollback without downgrade, disk restart
  and original-ID recovery across policy/channel/cancellation loss. Synthetic
  issuer/provider fixtures are not production credential or fleet proof.
- The local expanded packet passed 295 cases before the final persisted-
  namespace control; its final 38-case binding rerun and all 75 workflow-scope
  controls passed. Fatal lint, install-contract, documentation consistency,
  changed-module and whitespace gates passed. Only the existing manual
  native lane expands; required CI gains no process matrix.
- Re-read the parent Plan/Validation and current journal after merge. Managed
  completion retained a backup and reconciled only the merged source commit.
  Continue the scoped Dispatch durable-intent variant and explicit optional
  coordinator composition. No service, credentials, runtime update, image or
  deployment was enabled. Parent, independent evaluator, live provider,
  installed and fleet acceptance remain open.

### 2026-10-25 - Merge the scoped replay selection contract

- #8938 merged as `edde802ec4819310f6e4e19cae015b4a387dbd4b` from
  approved head `1daa5bab124dfd5bac51b52009c1128ade4ed53b`.
  Copilot review `5642584036` approved without findings; full reviews and
  review/issue comments were read. Required checks passed, as did local
  documentation consistency and whitespace validation.
- Re-read the current parent intent, Coordination, relevant Plan/Validation
  and journal after merge. Close only the proposal-review item. The parallel
  opt-in v2 contract retains v1 and requires expected binding comparison at
  the real operation boundary, not a capability-only or configuration-label
  authority check.
- Continue provider-first implementation and real transport compatibility
  qualification, then scoped durable intent and explicit optional coordinator
  composition. No runtime, credentials, image or deployment was enabled by
  this documentation-only merge. The parent remains Active.

### 2026-10-25 - Merge actual watcher/client source qualification

- #8927 merged as `e1c153dd7c37d64c9ab7ca489951f03a1bead149` from
  approved head `f81fdff9d5658d53045abc6ebdcf2fc39bebc105`.
  Copilot review `5642404042` approved with no blocking issues; all reviews,
  review comments and issue comments were read. The one initial contention
  finding is fixed and its thread is resolved. Required checks passed.
- Exact-head native acceptance `40179487266` passed 202 tests on Windows
  and 201 tests with one explicit skip on Linux. The final transport file
  passed all ten cases locally. Nonevent and empty-pending assertions require
  successful replay cycles; blocked transport is not credited as no-wake
  evidence. Original IDs remain bound through all retry/recovery paths.
- Re-read the current parent intent, Coordination, Plan, Validation and latest
  journal after merge. Close only this finite source-validation leaf. Managed
  sync safely aborted an individual-commit replay conflict after the squash;
  managed completion then verified the merged head and dropped only its two
  upstreamed commits, retaining a backup and this continuing worktree.
- Continue the explicit watcher/client selection contract. The real
  coordinator front door still does not configure the optional replay
  factory, and current v1 capabilities do not attest its namespace. Do not
  turn a configuration label into scope authority or silently enable a
  backend. Propose and review any missing backwards-compatible binding seam
  before production selection. Image, provider, independent evaluator,
  installed and fleet gates remain open.

### 2026-10-25 - Merge offline PR image inputs and retain real image acceptance

- #8885 merged as `f541c831e939e31ba0ef419c1ae9fb24ceca71fe` from
  approved head `9e112793999ab860309c69d07e12c5ad83d3e8c7`.
  Copilot review `5641852096` approved without findings; complete reviews and
  review/issue comments were read. Required CI `40169117492` passed.
  Exact-head native acceptance `40169147365` passed all 180 selected core
  cases and 44 standalone cases on each of Linux and Windows.
- Re-read the current parent intent, coordination, Plan, Validation and latest
  journal after merge. Close only the finite offline-input source slice.
  Managed pull-forward retained the reviewed tip and advanced this continuing
  worktree to the confirmed merge without discarding unpublished work.
- The helper and native synthetic installation do not approve a real runtime
  profile, base or feed, build an image, or prove a deployed watcher. Continue
  real offline image construction and packaged process acceptance through the
  existing shared owners; all provider, credential, writer transfer,
  update/rollback, installed and fleet gates remain open. No SSH work resumes.

### 2026-10-25 - Exercise the actual watcher-to-Dispatch consumer chain

- Following the operator's `watcher-integration` selection, compose the
  existing `WatchService`, connector, native observation child and both disk
  owners with the real TLS/PoP Gateway and finite Dispatch replay consumer.
  Two waits for the same authorized PR share a native observation target.
  Repeated identical snapshots produce no wake; service/queue restart retains
  original request, deadline and registration identities. A subsequent
  matching state change completes one wake per task, including recovery after
  a committed ACK response is lost.
- The native child supplies synthetic provider snapshots, not real forge or
  credential-provider qualification. The independent periodic evaluator,
  actual image construction, installed adoption, live providers and fleet
  gates remain open. No production polling, retry, deadline or activation
  behavior changes.
- The focused integrated case passes. Its surrounding replay, real-transport
  and coordinator-maintenance packet passes 72 tests under existing runner
  bounds. Initial test assumptions were corrected to distinguish a prepared
  waiter from an active one and a completed observation cycle from transient
  store contention. Read-only capability negotiation may retry before any
  waiter exists; no uncertain mutation is retried with a new identity.
  Hosted current-head Windows/Linux qualification remains required.

### 2026-10-24 - Resume PR-service construction on the shared profile owner

- Resume the PR service and watcher system. Separate SSH repair and native
  desktop validation are not prerequisites for this service slice.
- Close #8367 with operator confirmation as superseded by #8568 and #8586.
  Preserve its reviewed commits and carry forward the unique #8327 effort
  record rather than overwrite the newer release-profile representation or
  installation-owned policy composition. No runtime-profile consumer is
  enabled by this disposition.
- #8327 merged as `18b688bb57d2c7fb33ea793bfae8ff3065facff6` from
  approved head `1b1310c79ed783dbf10eb4890e186d3ddacaaa44`; required CI
  `40039650223` and exact-head native acceptance `40039790134` passed.
  The latter exercised all 55 selected tests on Linux and Windows. Local
  targeted validation passed 234 cases and workflow scope passed 59.
  This closes only retained hashed compile/export, not approved profile,
  exact acquisition, offline image or installed/fleet acceptance.
- Continue target-specific runtime closure and offline image construction
  through the existing artifact and dependency owners. The parent remains
  Active; provider, lifecycle, credentials, client, installed and fleet gates
  remain open. No SSH update or unrelated deployment follows.
- Implement the bounded PR offline-context consumer over canonical retained
  exact-profile wheel inspection. Its context contains only the existing
  recipe, complete wheels and exact version/hash lock; it grants no approval
  and performs no acquisition or image build. All 44 standalone tests pass,
  including 19 context controls and actual fresh offline installation with
  synthetic packages followed by tampered-wheel refusal. An existing real
  restart/ACK test now retries only the exact pre-entry store-busy response,
  preserving original IDs and failing immediately on uncertainty. Local
  Docker connection is unavailable, so image acceptance remains open.

### 2026-10-23 - Retain canonical dependency compile inputs before runtime admission

- Added the bounded lock-export seam to the existing installer owner, not a
  PR-service resolver. Compile admits its own native settings projection,
  applies the unchanged seven-day/stronger-package policy, retains complete
  project/recursive requirements/configuration, and rechecks effective resolver
  equivalence before execution. Existing install defaults and test seams remain
  compatible; canonical helpers are synchronized to every shipped consumer.
- Candidate exports force hashed requirements, ignore existing output pins and
  keep explicit output paths in the original working directory. Effective
  configuration cannot redirect implicit output, omit packages or emit index
  URLs. Unsupported compile shapes and live editable graphs refuse. This does
  not approve a profile, acquire its exact closure, qualify a governed feed or
  construct an offline context/image.
- The contained targeted Windows selection passes 234 tests. Real compile
  cases establish a positive recent-version control, seasoned transitive hashes,
  refusal of missing upload timestamps, existing-output independence and
  original-input mutation isolation, including absolute-dot aliases. Review
  exposed directory dependency exports that could reference cleaned-up snapshots
  and positional file aliases that could reopen mutable originals. Four new
  cases failed before correction; compile now refuses directory requirement
  edges/local artifact mappings while preserving metadata-only project inputs,
  and maps every positional input through the canonical retained graph.
  A further real mutation case reproduced the same alias escape inside recursive
  includes; their requirement/constraint targets are now rewritten by canonical
  identity, with six option/continuation controls. Positive exports are checked
  after snapshot cleanup. The separate native selection also passes
  all eight existing transitive/isolated-build age cases. All 59 workflow-scope
  tests pass after correcting stale artifact-lane and replay-store expectations.
  Fatal lint, all thirteen installer contracts and both canonical sync checks
  pass. Current-head review, required CI and the new explicitly selected native
  Linux/Windows compile lane remain publication gates.
- A subsequent review identified double lexical remapping when a POSIX root
  anchor leaves an original file path as a suffix inside its retained target.
  Offline composition reproduced the corruption; canonical includes now bypass
  later lexical remapping, while non-include lines retain their existing
  transformation. Cross-platform unit and native root-working-directory
  controls cover this boundary; exact-head Linux acceptance remains required.
- The parent remains Active. Approved platform-specific runtime closure,
  separately selected native Worktrees artifact, image/container acceptance,
  provider/storage/update qualification, release/authorized rollout, actual
  fleet and retained client/Dispatch closure remain open.

### 2026-10-23 - Merge first-party artifact construction and continue runtime admission

- #8293 merged as `f2fe13adcd9d8a191184dd2669b5cc9fd66ae1fe` at
  `2026-10-23T21:29:20Z`, from final head
  `6d9589baef8326450128c295da37f8188dcfcbd0`. Copilot approved that
  exact head with no open findings. Both review threads are resolved; complete
  review bodies, issue comments and thread pagination were read. Required CI
  `40026088499`, workflow lockdown, attribution and identifier checks passed.
  Managed merge used only the authorized additional CODEOWNER-vote exception,
  not a CI or blocking-feedback bypass. Closing references were empty.
- Review exposed two real production export defects missed by ordinary direct
  builds: nested scratch ancestry made Fleet's helper selection ambiguous,
  then admission metadata shadowed the disposable source handle after a reuse
  miss. Isolate attribute-bound source data outside the original ancestry and
  retain its distinct handle through validation. Keep Fleet's helper safety
  check unchanged. Pure controls cover build, reuse, reuse miss, failed build
  and failed admission, including source lifetime and complete cleanup.
- Final exact-head native acceptance `40026242589` passed Linux and Windows:
  real MCP/SSH and PR/Fleet builds, source verification, fresh offline
  first-party standalone installation outside a checkout, and actual nested
  export through the reuse-miss admission branch. Only catalog absence is
  simulated; Git, construction, source checks, admission identity and cleanup
  execute normally. This is packaging/source proof, not governed runtime
  dependency acquisition, approved Worktrees selection or image/fleet proof.
  Local follow-up packets passed 23 export/reuse and 19 canonical-attribute
  cases; the final pure selection passes 20. Its source roster, effect observer
  and budgets remain unchanged. A local focused observer attempt lacked
  coverage tooling and is not a pass; the hosted complete unit packet passed.
- Re-read the entire current parent after merge. Close only the standalone
  first-party source delivery item; runtime closure, offline context/image,
  provider, lifecycle, authorized installed rollout, fleet and every directly
  retained client/reviewer/migration/idle-record/nonevent gate remain open.
  The parent and #7188 remain Active; no rollout or finalization is authorized.
- Preserve the reviewed head. Managed continuation safely aborted a squash
  replay conflict; the complete published range and merged patch have the
  identical stable patch ID. Exclude only that already-merged range, then
  complete the managed pull-forward onto current `dev` without discarding
  unpublished work. Next compose target-specific runtime closure and offline
  context through canonical retained-input/profile authority, not a PR-only
  resolver or public runtime fallback.

### 2026-10-23 - Implement standalone first-party artifact construction

- Added explicit PR-service and Fleet component identities to the shared locked
  builder, candidate inventory, bundle verifier and publication naming. Existing
  plugin schema-3 manifests and release names remain unchanged; standalone
  schema-4 manifests bind the selected source and declared build resources.
  Fleet's helper/setup admission and injected wheel members are checked against
  trusted producer code and canonical resource bytes, not executed by verification.
- Local contained controls passed: 207 existing builder cases, all 191 promotion
  cases across bounded packets, and 36 standalone/pure-unit cases. Earlier
  oversized packets timed out and are not passes. Synthetic Git tests needed
  command-local Windows long-path support; no machine Git setting changed.
  Lint, install-contract and documentation guards passed. The fifteen pure
  controls join the exact admitted unit manifest without weakening its observer
  or wall budget.
- Added a separately selected Linux/Windows real-build and offline standalone
  install lane. Initial run `40019289742` passed both platforms on
  `e021b3b9dffbcc9b51bb0da6dc99cf3360da93d1`, building the existing MCP/SSH
  plugin closures and PR/Fleet standalone wheels, then installing the latter
  offline outside a checkout. It proves packaging only, not a governed
  runtime dependency closure, an approved native Worktrees selection, a built
  image or a working service/fleet deployment. Managed-host tests do not use the
  public-runner toolchain exception.
- Required hosted unit execution passed on that head. Its independent workflow
  guard rejected a dynamic runner matrix; the lane now names Linux and Windows
  runners explicitly, preserving that guard. Final-head review, CI and a fresh
  native run remain pending. No delivery or parent acceptance checkbox closes;
  release/authorized installed rollout and every retained parent gate remain open.

### 2026-10-23 - Shared artifact construction proposal merged

- #8275 merged as `79f6f114c4b86234b8ca885e1b5ea442b665f522` at
  `2026-10-23T19:08:36Z`, from head
  `39a2888ada6ab3ff299bb474fc5709341bda404e`. Copilot `5633200885`
  approved that exact head with no findings; full reviews and paginated review/
  issue comments contain no unresolved feedback. Required CI `40008173497`,
  workflow lockdown `40008173546`, attribution and identifier checks passed.
  Managed merge used only the authorized additional CODEOWNER-vote exception.
- Re-read the complete current parent goal, Coordination, Plan and Validation
  after merge. Only the construction proposal's review item closes. Begin
  standalone first-party artifact support through the shared builder/verifier;
  exact runtime closure, real offline image and every provider, lifecycle,
  installed, fleet and retained client/dispatch gate remain open.
- Managed completion confirmed the published change was merged and advanced
  this continuing worktree onto its reviewed base. No artifact implementation,
  release availability, runtime update or deployment is claimed.

### 2026-10-23 - Select the shared PR-service artifact construction path

- Operator selected `extend-shared-artifacts` rather than a supplied-wheelhouse
  container test. The linked construction proposal retains separate first-party
  publication and governed target-runtime closure boundaries; it does not extend
  the public build-toolchain exception to runtime dependencies.
- Source inspection found the shared locked wheel builder and signed candidate
  pipeline currently select plugins and vendored libraries, not standalone
  PR/Fleet distributions. Canonical input retention owns install-time source,
  configuration and resolver admission. A new PR-only resolver or a renamed
  standalone-as-plugin record would lose those contracts.
- Propose reviewable standalone artifact support, exact-profile/feed admission
  and dedicated offline-context/image proof under the existing #7188 scope.
  Implementation follows review of this proposal. Existing native wheel and
  installed-host evidence does not count as image construction or governed-feed
  acceptance. No runtime, credentials, capacity or deployment was changed.

### 2026-10-23 - Merge durable service storage identity and retain lifecycle work

- #8221 merged as `24b7c667dbc23cbe08924f70a4c2bbc3a2c60361` at
  `2026-10-23T13:14:07Z`, from final head
  `282c180342bb46f4edd1af843840f7afd2860e2e`. Copilot `5628695933`
  approved that exact head with no open issues. The sole earlier Low finding
  requested a description-only graceful-cutover statement; the updated body
  names the existing rollout owner, unchanged drain/retained-writer boundary
  and explicit volume migration/refusal policy. Its thread is resolved.
- Required CI `39957878327` and `39958755938`, attribution/lockdown and
  exact-head native acceptance `39957995159` passed. Both Linux and Windows
  run the core/store lifecycle and independent installed process. Local
  affected acceptance passed 246 and rebuilt standalone acceptance passed 25;
  fatal lint, diff, install contract and documentation consistency passed.
  Full review bodies, thread/comment pagination and issue comments were read.
  Managed self-merge used only the authorized additional CODEOWNER-vote
  exception, not a failed-check or feedback bypass.
- Re-read the full parent goal, Coordination, Plan and Validation after merge.
  Close only this storage-binding source leaf. The consumer-contract, image,
  service update/migration/rollback, cross-host fencing, real provider,
  authorized installed/fleet proof and every directly retained client,
  presentation, reviewer, idle-record and nonevent clause remain open.
  #7188 remains open; no source merge authorizes rollout or finalization.
- Preserve the reviewed head; managed `pr-complete` proved squash equivalence
  and advanced the continuing worktree past only its two merged commits.
  Next inspect the existing standalone artifact/container acceptance tooling
  for real image construction and isolated persistent-volume proof. Reuse
  existing artifact/role/lifecycle owners; do not add a second installer or
  controller, infer activation from a role projection, or reopen the accepted
  reader residual.

### 2026-10-23 - Bind the retained service volume before broader container lifecycle

- Trace Fleet's artifact preflight, authenticated retained staging and public
  client construction/activation contracts. Byte admission is not activation;
  the current constructor supports only its single-wheel client closure.
  The Komodo role adapter projects typed references, not a container executor.
  Do not stretch those APIs into PR installation permission or introduce a
  second controller/installer.
- Close a concrete PR-owned source gap first: the disk adapter previously
  checked only the namespace. Add an optional typed fleet/Gateway/slot binding
  to the complete atomic envelope, checked under existing transaction custody.
  The standalone host always selects it; the Gateway adapter refuses a
  differing identity. Keep unbound native/core bytes and remote protocol
  unchanged. Bound/unbound and unknown formats refuse without mutation or
  automatic migration, including older readers encountering the bound shape.
- The initial corruption test exposed a null nested replay being passed as the
  core's explicit empty-provisioning input. Require a dictionary before core
  restoration and retain null/list/scalar regressions. This is not accepted
  partial-state recovery. Original-ID restart, uncertain commit and exact ACK
  remain unchanged.
- Final local affected store/replay/service/Gateway/observer selection passes
  246 cases; rebuilt independent installed-host acceptance passes 25. Fatal
  lint, diff, thirteen-plugin install contract and documentation consistency
  checks pass. Current-head hosted Linux/Windows acceptance and required CI/
  review remain pending.
- This serves the PR vision's separated scopes and reliable single-owner
  state, using existing disk/lease/foreground lifecycle primitives. Documentation
  impact covers host configuration/storage, corruption/refusal, compatibility,
  upgrade/downgrade and testing. No new CLI flag, remote route, credential,
  supervisor, publication selector or fleet action is introduced.
- Fresh-state source tests and the installed process exercise provisioning,
  changed-identity refusal and same-volume restart/replay/ACK with synthetic
  Gateway/provider inputs. No Docker or authorized live fleet venue is available
  here; actual image build, migration, writer movement/fencing, candidate
  activation/rollback, provider/installed rollout and the full parent gates
  remain open. Source tests are not a substitute for those tiers.

### 2026-10-23 - Merge bounded observation fairness and retain deployment gates

- #8209 merged as `bf32acce7ec792b6e5fb9dcc0308a68746035b30` at
  `2026-10-23T12:22:45Z`, from head
  `981b86f86bd93fb9e9f5946240922ef2da168968`. Copilot review `5628091662`
  approved that exact head with no open issues. The earlier sweep-write
  finding is fixed and its thread is resolved; full review, thread/comment
  pagination and issue comments were read. Required CI `39951794405` and
  `39952129058`, attribution/lockdown and exact-head native Linux/Windows
  acceptance `39952160900` passed. The native jobs include the core lifecycle
  and independent installed foreground process, using synthetic issuer/provider
  inputs. Local affected selection passed 206 cases; refreshed standalone
  acceptance passed 25. No CI/feedback bypass was used; managed self-merge
  used only the authorized additional CODEOWNER-vote exception.
- Re-read the parent goal, Coordination, full Plan and Validation Plan after
  merge. Close only the bounded-observation source leaf. All directly retained
  client/reviewer/migration/idle-record/nonevent clauses and broader provider,
  installed adoption, image/container/lifecycle and fleet acceptance remain
  open. Closing references were empty; #7188 remains open. No production
  credential, deployment, runtime update or finalization follows from this merge.
- Preserve a backup of the reviewed head. Managed `pr-complete` confirmed the
  squash-merged content, dropped only its two redundant local commits and
  fast-forwarded this source worktree to the merged `dev` tip.
- Next: inspect the remaining service contract's artifact/container and
  deployment-lifecycle checklist, then close the next bounded source gap under
  the existing placement/lifecycle owners. Do not reopen the accepted reader
  residual or treat an unavailable authorized fleet lane as source completion.

### 2026-10-23 - Bound native observation IO without adding another owner

- Replace serial native reads with one four-read rolling window inside the
  existing finite observer. Only the cycle thread performs replay transactions;
  completed reads apply without waiting for earlier slow targets. Captured
  registration identities, account/repository bindings and native time/output
  limits remain unchanged. No per-subscriber waiter or credential source is added.
- Continue authority checks and deadline sweeps while reads are pending. On
  disconnect, invalid state or uncertain commit, stop admission, withdraw queued
  work and join actual reads before releasing the cycle lock. The service's
  existing shielded task/drain keeps its writer lease until every read settles;
  partial fast-target progress does not satisfy whole-service readiness.
- Local focused observation/lifecycle/real TLS transport selection passed 70
  cases, including real native child completion order, rolling refill past a
  blocked target, four-read maximum, distinct shared-target deliveries and
  failed-drain writer exclusion and saturated-window deadline sweeps.
  The independent installed standalone selection passed 25 cases after source
  preparation; all 13 install contracts, fatal lint and doc consistency passed.
  The first test attempt hit a test-side nonblocking
  read/sweep contention; capture the durable event inside the single writer's
  completed-result boundary instead of weakening store custody or adding retry.
- Documentation impact: update the observer, service and test guides for the
  rolling bound, timeout behavior and retained drain. This closes an existing
  central-observation vision/plan requirement under the same independent-service
  and graceful-cutover patterns; intended scope and placement authority do not
  change. Image construction, fresh container, update/rollback, live providers,
  installed activation and representative fleet performance remain open.
- Publish #8209 at `54c1dd8aec`; required CI `39949621748` and native
  Linux/Windows acceptance `39949759241` passed. Copilot review `5627918808`
  identified repeated unchanged sweep writes. Keep authority/deadline polling,
  but skip sweep persistence when the complete durable state is unchanged,
  excluding only its relative monotonic timeout projection. Actual timeout
  and retention changes retain normal pre-replace/uncertain-commit behavior.
  The expanded 206-case local replay/core/store/Gateway/observer/service
  selection passes, including zero idle-poll writes while all slots are
  blocked and one durable timeout write. Current-head re-review and native
  acceptance remain required before merging this correction.

### 2026-10-23 - Merge standalone foreground packaging and continue observation fairness

- #8196 merged as `1de7102e0c77d83d5baf43f0f1618fdb6fc58066` at
  `2026-10-23T11:06:09Z`, from head
  `fd9be575715fa9c37fb210337119b2a45618eae6`. Copilot review `5627331624`
  approved that exact head with no open issues. Read the complete review and
  all thread/comment pages; the earlier missing-version finding is fixed and
  resolved. Required CI and attribution/lockdown checks passed. Managed merge
  used only the documented additional CODEOWNER exception, not a CI or
  blocking-feedback bypass. Closing references were empty; #7188 stays open.
- Required source CI `39942689605` passed the full 919-node admitted Linux unit
  packet with coverage, plus guards and the aggregate gate. The changed native
  inventory/recipe seam has two explicit effect-free cases and exact reviewed
  source-byte admission; no node, effect boundary or budget was weakened.
  Exact-head native acceptance `39942822074` passed the selected Linux and
  Windows jobs, including the installed standalone process and core lifecycle.
- Local owning checks passed 25 standalone, 16 Fleet credential/worker,
  10 runner/candidate and 58 workflow-scope cases, fatal lint, all 13 install
  contracts and the Python declaration-age audit. An attempted full local
  Windows unit packet was rejected for a native-loader effect and a skipped
  node; it is not recorded as passing. The accepted whole packet is Linux CI;
  Windows service proof is the separate actual native lane. Secure local npm
  metadata remained unavailable; neither TLS nor CI was bypassed.
- Re-read the current parent goal, Coordination, full Plan and Validation Plan.
  Close only the foreground packaging source leaf. Broad artifact/fleet,
  provider, installed adoption, release/authorized rollout and every retained
  direct client/presentation/reviewer/migration/idle-record/nonevent clause
  remain open. No production credentials, placement, runtime update or
  finalization follows from this source merge.
- The managed pull-forward aborted a squash-history conflict without changing
  the branch. A backup was preserved and only the provider-confirmed merged
  range was excluded before retrying; completion then pulled the same source worktree onto
  current `dev`, with no unmerged work discarded.
- Next: close the remaining source-level slow-target observation gap with
  bounded authorized-target IO, preserving the single existing observer,
  writer/drain custody, original identities and native credential owner.
  Do not add a per-subscriber waiter, parallel controller or production
  activation. Image/fresh-container and actual deployment gates stay separate.

### 2026-10-23 - Prepare the independent foreground service package

- Add `services/pr`, a normal standalone distribution over the existing
  `WatchService`, with explicit trusted host configuration, separate first
  provisioning, foreground serve and passive bounded-age readiness. Native
  commands remain independent. The host requires an explicit scoped credential
  command; no ambient account fallback, remote callback argv, new issuer,
  deployment controller or per-session supervisor is introduced.
- Factor Fleet's bounded credential-reference reader without changing typed
  worker renewal, and expose an external P-256 key/current-token callback for
  standalone service connectors. Local launch/stop bounds local work; actual
  Gateway and credential-worker admission remain separate. Neither a deadline,
  token label, local lock nor readiness snapshot is a placement permit.
- Add an offline wheel-only non-root container recipe and a reference-only
  example of the existing `container.service` role. Register the distribution,
  exact-source test preparation, native candidate recipe and existing manual
  Linux/Windows service selector. These are source artifacts, not a constructed
  image, installed fleet role or activation result.
- Contained validation passed 25 standalone cases, 10 focused runner/candidate
  cases and 16 Fleet credential/worker cases. The installed-process test uses
  real TLS/PoP Gateway transport, synthetic provider snapshots, an actual native
  child, disk state, duplicate-writer refusal, reconnect, restart and exact ACK.
  Windows uses the public embedding stop event; Linux CLI SIGTERM remains for
  the exact-head hosted lane. A real Windows passive-reader rename conflict
  now skips a status publication without extending old freshness or stopping
  the writer.
- Store contention can cause a native result to be reread before its commit
  succeeds. The process test retries only an explicitly busy pending read,
  never an ambiguous registration/ACK. It verifies terminal read stability,
  not a one-read performance claim under contention. Slow-target fairness and
  representative workload budgets remain open.
- Keep the parent Active and all broad artifact/fleet and direct client,
  presentation, reviewer, migration, idle-record and nonevent gates unchecked.
  Real image construction, fresh-container lifecycle, safe update/rollback,
  interrupted cutover, owned uninstall, live providers, authorized placement,
  release and installed adoption still require their actual proofs.

### 2026-10-23 - Merge the core service lifecycle and continue standalone delivery

- #8157 merged as `e92711cc1e303d12c9a81983bd03194ac8c41f41` at
  `2026-10-23T09:08:14Z`, from published head
  `1701fd87e8600e4371933eaf9856b4d1fd45f547`. Current-head Copilot
  review `5626157491` approved with no open issues. Read its full body and
  every thread; the only earlier thread is resolved and outdated. All required
  checks passed. Managed completion used only the documented additional
  CODEOWNER exception, not a CI or feedback bypass. #7188 remains open.
- Exact-head hosted acceptance `39928959332` passed both selected Linux and
  Windows jobs. Final contained service/Gateway/observer validation passed
  112 cases, fatal lint and all 13 install contracts. The full workflow-scope
  audit passed 58 checks. Real isolated TLS/PoP, native child, disk, lease,
  reconnect, restart and ACK tests are source integration, not production
  provider, installed artifact or fleet qualification.
- Review and hosted execution exposed startup-cancellation, queued-close
  budget, pre-lock admission withdrawal and stop-during-adoption gaps. The
  final source reserves startup ownership before scheduling, withdraws before
  waiting for the close mutex, observes stop concurrently with adoption and
  counts owned cleanup against one shutdown budget. A timed-out drain retains
  actual unfinished work and native writer custody; no cancellation is treated
  as rollback or permission for a replacement owner.
- Read this entire parent after merge and close only the core lifecycle source
  leaf. All direct client, presentation, migration, reviewer, idle-record and
  nonevent acceptance remains, alongside installed adoption, credential/provider,
  release/authorized rollout and the complete fleet plan.
- Next: deliver the independently installable PR artifact and declared container
  role through existing standalone-service, credential and fleet lifecycle
  contracts. Reuse this core rather than another registrar, provider, supervisor
  or fleet controller. Missing shared source seams use normal reviewed changes,
  not an owner-ACK rendezvous; credentials, actual placement and activation keep
  their separate authorization boundaries.
- The parent stays Active. No runtime update, production credential acquisition,
  activation, new capacity or worktree finalization follows from this source merge.

### 2026-10-23 - Merge shared scheduling and continue independent service delivery

- #8124 merged as `c9ed75549c790eeb36d2f9eb0e9fb1bc6c117953` at
  `2026-10-23T07:10:16Z`, from published head
  `73d254a7b3d698b1bffe19b62563841cd19f70ec`. Exact-head Copilot review
  `5624985262` approved with no open issues; the complete thread page was
  empty. Required checks passed. Managed completion used only the documented
  additional CODEOWNER exception, not a CI or feedback bypass. #7188 remains open.
- Hosted acceptance `39915084850` passed both selected Linux and Windows
  jobs at that exact head. The preceding Windows run exhausted its 30-second
  item watchdog during cold SQLite schema initialization, before waiter
  preparation. Only the real 40-waiter cardinality case now uses the existing
  120-second native Windows convention; assertions, other items and group/job
  bounds are unchanged. The final local maintenance suite passes all 15 cases;
  fatal lint and all 13 install contracts pass.
- Read this entire parent after the actual merge. Close only the optional
  coordinator-scheduling source leaf, preserving every direct client,
  presentation, migration, reviewer, idle-record and nonevent acceptance
  clause, current custody, installation, provider and fleet gate.
- Next: deliver the independently installable PR serving/observation artifact
  through the existing standalone-service and fleet role/lifecycle contracts.
  Reuse the finite PR observer, replay store and Gateway handler rather than
  introducing a second forge stack, registrar, controller or per-task poller.
  Concrete shared-source gaps remain implementation work under normal review;
  credentials, placement and live activation retain their authorization gates.
- The parent stays Active. This is source integration, not released/installed
  service acceptance, production provider proof or fleet qualification. No
  runtime update, credential provisioning, service activation, new capacity
  or worktree finalization follows from this merge.

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

### 2026-10-18 - Consumer proposal review record

- Preserve the [dated consumer-proposal review record](journal/2026/10.18%20Consumer%20proposal%20review.md)
  without reinstating its historical diagnostic or operational permissions.
  The current Plan and accepted #7208 disposition remain authoritative.

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
